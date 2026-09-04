#!/usr/bin/env python3

"""Conformance checks for CC-04 shell topology.

This module validates shell-level topology rules for a `TopologyData` instance,
including closed solids, shell closure, and dangling face detection.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from ..geometry import (
    face_polygons,
    polygon_area_vector,
    shell_polygons,
    solid_shells,
)

from ..model import (
    Curve,
    Issue,
    Point,
    Shell,
    Solid,
    Surface,
    Tolerances,
    TopologyData,
    build_indexes,
    err,
    solid_face_ids,
    solid_owned_face_ids,
)

CONFORMANCE_CLASS_ID = "CC-04"
CONFORMANCE_CLASS_NAME = "Shell topology"
RULE_IDS = ["TR-06", "TR-18", "TR-27"]

OPEN_SOLID_SHELL_CODE = "OPEN_SOLID_SHELL"
MAX_OPEN_CURVES_IN_ISSUE = 10
DANGLING_FACE_CODE = "DANGLING_FACE"

SHELL_NOT_CLOSED_CODE = "SHELL_NOT_CLOSED"

# TR-27 closure bound.  The residual of the summed oriented face areas is
# coordinate round-off for a genuinely closed shell, so it grows with the
# geometry and the bound is relative to the shell's own surface area.  Closed
# shells across the reference fixtures residual at or below 1.3e-10 of their
# area; an open one runs to 0.45 of it.  The absolute floor keeps a tiny shell
# from being held to a bound below double precision.
SHELL_CLOSURE_RELATIVE_TOLERANCE = 1e-6
SHELL_CLOSURE_ABSOLUTE_TOLERANCE = 1e-9

MAX_UNMATCHED_EDGES_IN_ISSUE = 6

# Decimal places used to key a vertex when matching directed edges.  Micrometre
# resolution: fine enough that distinct survey vertices never collide, coarse
# enough that two faces meeting at a shared vertex agree on its key.
EDGE_MATCH_PRECISION = 6


# ---------------------------------------------------------------------------
# TR-06  Closed solid
# ---------------------------------------------------------------------------


def _surface_curve_references(surface: Surface) -> list[str]:
    """Return curve ids referenced by all rings in a surface."""
    curve_references: list[str] = []

    for ring in surface.get("rings", []):
        for member in ring.get("members", []):
            curve_references.append(member["ref"])

    return curve_references


def _count_solid_shell_curve_references(
    solid: Solid,
    surfaces: dict[str, Surface],
) -> dict[str, int]:
    """Count how many times each curve is referenced by a solid's faces.

    Faces come from ``solid_face_ids``, so a solid carrying only structured
    ``shells`` is counted rather than skipped.  Reading ``solid["faces"]``
    directly returned nothing for such a solid, leaving the count empty and the
    rule passing vacuously — a silent false negative on exactly the condition
    it exists to detect.
    """
    curve_reference_counts: dict[str, int] = defaultdict(int)

    for face_id in solid_face_ids(solid):
        surface = surfaces.get(face_id)
        if surface is None:
            continue

        for curve_id in _surface_curve_references(surface):
            curve_reference_counts[curve_id] += 1

    return curve_reference_counts


def _open_curve_ids(curve_reference_counts: dict[str, int]) -> list[str]:
    """Return curve ids whose reference count does not satisfy closed-shell rules."""
    return [
        curve_id
        for curve_id, reference_count in curve_reference_counts.items()
        if reference_count != 2
    ]


def _open_solid_shell_issue(solid_id: str, open_curves: list[str]) -> Issue:
    """Create a TR-06 issue for a solid shell that is not closed."""
    return err(
        OPEN_SOLID_SHELL_CODE,
        f"Solid {solid_id} shell is not closed: "
        f"{len(open_curves)} curve(s) do not appear exactly twice",
        object_id=solid_id,
        extra={
            "open_curve_count": len(open_curves),
            "open_curves": open_curves[:MAX_OPEN_CURVES_IN_ISSUE],
        },
    )


def validate_closed_solid(
    data: TopologyData,
) -> list[Issue]:
    """
    TR-06: the shell of a solid must be a closed 2-manifold.
    In a closed shell every curve is used by the solid's faces exactly
    twice (once in each direction). A count other than 2 means the shell
    has a gap or a hole.
    """
    issues: list[Issue] = []
    surfaces = build_indexes(data)["surfaces"]

    for solid in data.get("solids", []):
        solid_id = solid["id"]
        curve_reference_counts = _count_solid_shell_curve_references(solid, surfaces)
        open_curves = _open_curve_ids(curve_reference_counts)

        if open_curves:
            issues.append(_open_solid_shell_issue(solid_id, open_curves))

    return issues


# ---------------------------------------------------------------------------
# TR-27  Shell closure
# ---------------------------------------------------------------------------


VertexKey = tuple[float, ...]
DirectedEdge = tuple[VertexKey, VertexKey]


def _shell_area_residual(
    polygons: list[list[list[float]]],
) -> tuple[float, float]:
    """Return ``(residual, total_area)`` for a set of oriented polygons.

    The residual is the magnitude of the summed oriented area vectors, which is
    zero for a closed, consistently wound surface.  The total area is the sum
    of the individual magnitudes, and is what the tolerance scales against.
    """
    sum_x = sum_y = sum_z = 0.0
    total_area = 0.0

    for poly in polygons:
        if len(poly) < 3:
            continue

        area_x, area_y, area_z = polygon_area_vector(poly)
        sum_x += area_x
        sum_y += area_y
        sum_z += area_z
        total_area += math.sqrt(
            area_x * area_x + area_y * area_y + area_z * area_z
        ) / 2.0

    residual = math.sqrt(
        sum_x * sum_x + sum_y * sum_y + sum_z * sum_z
    ) / 2.0
    return residual, total_area


def _vertex_key(vertex: list[float]) -> VertexKey:
    """Return a rounded, hashable key for a vertex."""
    return tuple(round(value, EDGE_MATCH_PRECISION) for value in vertex[:3])


def _unmatched_directed_edges(
    shell: Shell,
    surfaces: dict[str, Surface],
    curves: dict[str, Curve],
    points: dict[str, Point],
) -> list[tuple[str, VertexKey, VertexKey]]:
    """Return directed boundary edges of *shell* that have no reverse twin.

    On a closed, consistently wound surface every directed edge is walked once
    forward by one face and once backward by its neighbour, so the unmatched
    edges are exactly the boundary of the defect.  Each entry is
    ``(face_id, start, end)``, which is what lets the issue name the faces to
    correct rather than only the solid.
    """
    counts: Counter[DirectedEdge] = Counter()
    owners: dict[DirectedEdge, list[str]] = defaultdict(list)

    face_orientations = shell.get("face_orientations", {})
    for face_id in shell.get("faces", []):
        surface = surfaces.get(face_id)
        if surface is None:
            continue

        for poly in face_polygons(
            surface, curves, points, face_orientations.get(face_id, "+")
        ):
            vertex_count = len(poly)
            for index in range(vertex_count):
                edge = (
                    _vertex_key(poly[index]),
                    _vertex_key(poly[(index + 1) % vertex_count]),
                )
                counts[edge] += 1
                owners[edge].append(face_id)

    unmatched: list[tuple[str, VertexKey, VertexKey]] = []
    for (start, end), count in counts.items():
        if counts.get((end, start), 0) == count:
            continue
        for face_id in owners[(start, end)]:
            unmatched.append((face_id, start, end))

    return unmatched


def _shell_not_closed_issue(
    solid_id: str,
    shell_index: int,
    shell: Shell,
    residual: float,
    total_area: float,
    tolerance: float,
    unmatched: list[tuple[str, VertexKey, VertexKey]],
) -> Issue:
    """Create a TR-27 issue for a shell that is not a closed, oriented surface."""
    offending_faces = sorted({face_id for face_id, _start, _end in unmatched})
    shell_type = shell.get("type", "outer")

    return err(
        SHELL_NOT_CLOSED_CODE,
        f"Solid {solid_id} {shell_type} shell is not a closed, consistently "
        f"oriented surface: oriented face areas leave a residual of "
        f"{residual:.3f} m² against a total area of {total_area:.3f} m² "
        f"(tolerance {tolerance:.3g} m²). {len(unmatched)} directed edge(s) "
        f"have no reverse twin, across {len(offending_faces)} face(s). Most "
        f"likely a reversed or internal face in the source model.",
        object_id=solid_id,
        extra={
            "shell_index": shell_index,
            "shell_type": shell_type,
            "area_defect": residual,
            "total_area": total_area,
            "tolerance": tolerance,
            "unmatched_edge_count": len(unmatched),
            "offending_face_ids": offending_faces,
            "sample_unmatched_edges": [
                {"face": face_id, "start": list(start), "end": list(end)}
                for face_id, start, end in unmatched[
                    :MAX_UNMATCHED_EDGES_IN_ISSUE
                ]
            ],
        },
    )


def validate_shell_closure(data: TopologyData) -> list[Issue]:
    """
    TR-27: every shell must be a closed, consistently oriented surface.

    A shell bounds a volume only if its faces form a watertight surface whose
    normals all point the same way out of it.  The test is the divergence
    theorem applied to a constant field: the oriented area vectors of a closed
    surface sum to zero, so any non-zero residual is the area of the hole — or
    of the region a reversed face is covering twice.

    Why this is not already covered
    -------------------------------
    TR-06 (:func:`validate_closed_solid`, this class) requires each curve to be
    referenced by a solid's faces exactly twice, but counts references without
    regard to direction, so two faces walking a shared edge the *same* way
    satisfy it.  TR-25 tests only the *sign* of the volume integral, and TR-26
    only its magnitude — both presume closure, so an open shell reaches them as
    a strange number rather than as a closure error.  Between them a solid with
    two coplanar faces of opposing normals passes every existing rule while
    enclosing nothing well-defined.

    That gap is how a reversed face in a source model reaches the topology:
    SketchUp reports a group as "Solid" from edge counts alone and does not
    require consistent face orientation, so the defect survives the modelling
    checks.

    Mirrors TR-27 in the test suite's independent validator; the two engines
    must agree, so the tolerance is defined the same way in both.
    """
    indexes = build_indexes(data)
    surfaces = indexes["surfaces"]
    curves = indexes["curves"]
    points = indexes["points"]

    issues: list[Issue] = []

    for solid in data.get("solids", []):
        solid_id = solid["id"]

        for shell_index, shell in enumerate(solid_shells(solid)):
            polygons = shell_polygons(shell, surfaces, curves, points)
            if not polygons:
                continue  # no resolvable geometry — TR-06 / TR-17 / TR-18 cover it

            residual, total_area = _shell_area_residual(polygons)
            tolerance = max(
                SHELL_CLOSURE_ABSOLUTE_TOLERANCE,
                SHELL_CLOSURE_RELATIVE_TOLERANCE * total_area,
            )
            if residual <= tolerance:
                continue

            issues.append(
                _shell_not_closed_issue(
                    solid_id,
                    shell_index,
                    shell,
                    residual,
                    total_area,
                    tolerance,
                    _unmatched_directed_edges(shell, surfaces, curves, points),
                )
            )

    return issues


# ---------------------------------------------------------------------------
# TR-18  No dangling faces
# ---------------------------------------------------------------------------


def _referenced_face_ids(data: TopologyData) -> set[str]:
    """Return all face ids referenced by solids in the topology dataset.

    Delegates to ``solid_owned_face_ids`` so this rule and TR-10 resolve
    ownership identically.  Building the set from ``solid["faces"]`` directly
    ignored a solid's structured ``shells``, so a face owned only through a
    shell was reported as dangling when it is properly owned.
    """
    return solid_owned_face_ids(data)


def _surface_shell_face_ids(data: TopologyData) -> set[str]:
    """Return face ids that belong to a CSDM shell.

    Surface-only shells (shells not referenced by any solid, e.g. ground
    surfaces) still legitimately own faces. Those faces are exempt from the
    dangling check because they belong to a modelled surface, not to an
    orphaned face.
    """
    return {
        entry["ref"]
        for entry in data.get("surface_shell_face_refs", [])
    }


def _dangling_face_issue(surface_id: str) -> Issue:
    """Create a TR-18 issue for a surface that is not owned by any solid."""
    return err(
        DANGLING_FACE_CODE,
        f"Surface {surface_id} is not referenced by any solid",
        object_id=surface_id,
    )


def validate_no_dangling_faces(
    data: TopologyData,
) -> list[Issue]:
    """
    TR-18: every surface (face) must be referenced by at least one solid
    shell or belong to a surface-only shell.

    A face that no solid owns and that no shell references cannot form part
    of any closed shell and is topologically orphaned. Faces recorded in
    ``data['surface_shell_face_refs']`` are exempt because they belong to a
    surface-only shell (e.g. a ground surface).
    """
    referenced_faces = _referenced_face_ids(data)
    exempt_faces = _surface_shell_face_ids(data)
    issues: list[Issue] = []

    for surface in data.get("surfaces", []):
        surface_id = surface["id"]
        if surface_id in referenced_faces or surface_id in exempt_faces:
            continue
        issues.append(_dangling_face_issue(surface_id))

    return issues


def validate(data: TopologyData, tolerances: Tolerances | None = None) -> list[Issue]:
    """Validate CC-04 shell topology rules.

    Args:
        data: Topology data to validate.
        tolerances: Optional tolerance overrides. Present for interface
            consistency; this validator does not currently use them.

    Returns:
        A list of validation issues found in `data`.
    """
    issues: list[Issue] = []
    issues.extend(validate_closed_solid(data))
    # Closure before the CC-05 volume rules run: those integrate over the
    # shell, and neither the sign nor the magnitude means anything if the
    # surface has a hole in it.
    issues.extend(validate_shell_closure(data))
    issues.extend(validate_no_dangling_faces(data))
    return issues
