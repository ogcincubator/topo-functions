#!/usr/bin/env python3

"""Conformance checks for CC-05 solid topology.

This module validates solid-level topology rules for a `TopologyData` instance,
including positive volume, minimum thickness, self-intersection, and shell
orientation.
"""

from __future__ import annotations

from ..geometry import (
    point_coordinates,
    segments_intersect_3d,
    signed_volume_of_polygons,
    solid_bbox,
    ring_coords,
)

from ..model import (
    Curve,
    Issue,
    Point,
    Shell,
    ShellType,
    Solid,
    Surface,
    Tolerances,
    TopologyData,
    build_indexes,
    err,
)

CONFORMANCE_CLASS_ID = "CC-05"
CONFORMANCE_CLASS_NAME = "Solid topology"
RULE_IDS = ["TR-07", "TR-19", "TR-24", "TR-25"]

ZERO_OR_NEGATIVE_VOLUME_CODE = "ZERO_OR_NEGATIVE_VOLUME"
SOLID_BELOW_MINIMUM_THICKNESS = "SOLID_BELOW_MINIMUM_THICKNESS"
OUTER_SHELL_TYPE: ShellType = "outer"
OUTER_SHELL_REVERSED_CODE = "SHELL_ORIENTATION_REVERSED"
INNER_SHELL_REVERSED_CODE = "INNER_SHELL_ORIENTATION_REVERSED"

SolidSegment = tuple[int, list[float], list[float]]
SolidIntersection = tuple[SolidSegment, SolidSegment]


# ---------------------------------------------------------------------------
# TR-07  Positive volume
# ---------------------------------------------------------------------------


def _non_positive_volume_issue(solid_id: str, volume: float) -> Issue:
    """Create a TR-07 issue for a solid with non-positive declared volume."""
    return err(
        ZERO_OR_NEGATIVE_VOLUME_CODE,
        f"Solid {solid_id} has non-positive volume {volume}",
        object_id=solid_id,
        extra={"volume": volume},
    )


def validate_positive_volume(
    data: TopologyData,
    min_positive_volume: float = Tolerances.volume,
) -> list[Issue]:
    """TR-07: every solid must declare a strictly positive volume."""
    issues: list[Issue] = []
    solids = data.get("solids", [])

    for solid in solids:
        solid_id = solid["id"]
        volume = solid.get("volume", 0.0)

        if volume <= min_positive_volume:
            issues.append(_non_positive_volume_issue(solid_id, volume))

    return issues


# ---------------------------------------------------------------------------
# TR-19  Minimum solid thickness
# ---------------------------------------------------------------------------


def validate_minimum_solid_thickness(
    data: TopologyData,
    min_thickness: float = Tolerances.thickness,
) -> list[Issue]:
    """
    TR-19: the bounding box of each solid must have a minimum extent of
    *min_thickness* in every axis direction.

    Solids that are effectively flat in one or more directions (slivers) are
    numerically unstable and must be flagged.
    """
    issues: list[Issue] = []
    indexes = build_indexes(data)
    surfaces = indexes["surfaces"]
    curves = indexes["curves"]
    points = indexes["points"]

    for solid in data.get("solids", []):
        solid_id = solid["id"]
        bbox = solid_bbox(solid, surfaces, curves, points)

        if bbox is None:
            continue

        dimensions = _solid_bbox_dimensions(bbox)
        thin_axes = _thin_axes(dimensions, min_thickness)

        if thin_axes:
            issues.append(
                _minimum_solid_thickness_issue(
                    solid_id,
                    thin_axes,
                    dimensions,
                    min_thickness,
                )
            )

    return issues


def _solid_bbox_dimensions(
    bbox: tuple[float, float, float, float, float, float],
) -> dict[str, float]:
    """Return x/y/z extents from a solid bounding box."""
    xmin, ymin, zmin, xmax, ymax, zmax = bbox

    return {
        "x": xmax - xmin,
        "y": ymax - ymin,
        "z": zmax - zmin,
    }


def _thin_axes(
    dimensions: dict[str, float],
    min_thickness: float,
) -> list[str]:
    """Return axis names whose dimension is below the minimum thickness."""
    return [
        axis
        for axis, dimension in dimensions.items()
        if dimension < min_thickness
    ]


def _minimum_solid_thickness_issue(
    solid_id: str,
    thin_axes: list[str],
    dimensions: dict[str, float],
    min_thickness: float,
) -> Issue:
    """Build a TR-19 issue for a solid with insufficient AABB thickness."""
    return err(
        SOLID_BELOW_MINIMUM_THICKNESS,
        f"Solid {solid_id} has thickness below {min_thickness:.3e} "
        f"in axis/axes: {thin_axes}",
        object_id=solid_id,
        extra={
            "thin_axes": thin_axes,
            "dimensions": dimensions,
            "min_thickness": min_thickness,
        },
    )


# ---------------------------------------------------------------------------
# TR-24  No solid self-intersection
# ---------------------------------------------------------------------------


def _solid_segments(
    solid: Solid,
    surfaces: dict,
    curves: dict,
    points: dict,
) -> list[SolidSegment]:
    """Collect all curve segments from a solid, tagged with their face index."""
    segments: list[SolidSegment] = []

    for face_index, face_id in enumerate(solid.get("faces", [])):
        surface = surfaces.get(face_id)
        if not isinstance(surface, dict):
            continue

        for ring in surface.get("rings", []):
            for member in ring.get("members", []):
                curve = curves.get(member["ref"])
                if not isinstance(curve, dict):
                    continue

                vertices = curve.get("vertices", [])
                if not isinstance(vertices, list):
                    continue

                for vertex_index in range(len(vertices) - 1):
                    start_coords = point_coordinates(points, vertices[vertex_index])
                    end_coords = point_coordinates(points, vertices[vertex_index + 1])

                    if start_coords is None or end_coords is None:
                        continue

                    segments.append((face_index, start_coords, end_coords))

    return segments


def _find_solid_self_intersection(
    segments: list[SolidSegment],
) -> SolidIntersection | None:
    """Return the first pair of cross-face segments that properly intersect."""
    for first_index, first_segment in enumerate(segments):
        face_index_a, start_a, end_a = first_segment

        for second_segment in segments[first_index + 1:]:
            face_index_b, start_b, end_b = second_segment

            if face_index_a == face_index_b:
                continue

            if segments_intersect_3d(start_a, end_a, start_b, end_b):
                return first_segment, second_segment

    return None


def _solid_self_intersection_issue(
    solid_id: str,
    intersection: SolidIntersection,
) -> Issue:
    """Build a SOLID_SELF_INTERSECTION issue for a detected segment crossing."""
    first_segment, second_segment = intersection
    face_index_a, start_a, end_a = first_segment
    face_index_b, start_b, end_b = second_segment

    return err(
        "SOLID_SELF_INTERSECTION",
        f"Solid {solid_id}: segment on face index {face_index_a} "
        f"({start_a}→{end_a}) crosses segment on face index {face_index_b} "
        f"({start_b}→{end_b})",
        object_id=solid_id,
        extra={"face_index_a": face_index_a, "face_index_b": face_index_b},
    )


def validate_no_solid_self_intersection(
    data: TopologyData,
) -> list[Issue]:
    """
    TR-24: no two faces of the same solid may have curve segments that
    properly cross each other in 3D space.

    For each solid, every curve segment from every face is collected together
    with its face index in "solid[" faces"]". Segment pairs from different
    faces are tested for proper 3D intersection. At most one error per solid
    is reported.
    """
    issues: list[Issue] = []
    indexes = build_indexes(data)
    surfaces = indexes["surfaces"]
    curves = indexes["curves"]
    points = indexes["points"]

    for solid in data.get("solids", []):
        solid_id = solid["id"]
        segments = _solid_segments(solid, surfaces, curves, points)
        intersection = _find_solid_self_intersection(segments)

        if intersection is None:
            continue

        issues.append(_solid_self_intersection_issue(solid_id, intersection))

    return issues


# ---------------------------------------------------------------------------
# TR-25  Shell orientation
# ---------------------------------------------------------------------------


def _solid_shells(solid: Solid) -> list[Shell]:
    """Return structured shells, falling back to legacy flat solid faces."""
    fallback_shell: Shell = {
        "type": OUTER_SHELL_TYPE,
        "faces": solid.get("faces", []),
        "face_orientations": solid.get("face_orientations", {}),
    }
    return solid.get("shells") or [fallback_shell]


def _shell_polygons(
    shell: Shell,
    surfaces: dict[str, Surface],
    curves: dict[str, Curve],
    points: dict[str, Point],
) -> list[list[list[float]]]:
    """Build oriented polygon coordinate lists for all resolvable shell faces."""
    polygons: list[list[list[float]]] = []
    face_orientations = shell.get("face_orientations", {})

    for face_id in shell.get("faces", []):
        surface = surfaces.get(face_id)
        if surface is None:
            continue

        face_orientation = face_orientations.get(face_id, "+")
        for ring in surface.get("rings", []):
            coords = ring_coords(ring, curves, points)
            if not coords:
                continue

            polygons.append(
                list(reversed(coords)) if face_orientation == "-" else coords
            )

    return polygons


def _shell_orientation_issue(
    solid_id: str,
    shell: Shell,
    signed_volume: float,
    tol: float,
) -> Issue | None:
    """Return a shell-orientation issue when the shell volume sign is invalid."""
    is_outer_shell = shell.get("type", OUTER_SHELL_TYPE) == OUTER_SHELL_TYPE

    if is_outer_shell and signed_volume < -tol:
        return err(
            OUTER_SHELL_REVERSED_CODE,
            f"Solid {solid_id} outer shell has reversed orientation "
            f"(signed volume = {signed_volume:.6g}; expected positive)",
            object_id=solid_id,
            extra={"signed_volume": signed_volume},
        )

    if not is_outer_shell and signed_volume > tol:
        return err(
            INNER_SHELL_REVERSED_CODE,
            f"Solid {solid_id} inner shell has reversed orientation "
            f"(signed volume = {signed_volume:.6g}; expected negative for a void)",
            object_id=solid_id,
            extra={"signed_volume": signed_volume},
        )

    return None


def validate_shell_orientation(
    data: TopologyData,
    tol: float = Tolerances.volume,
) -> list[Issue]:
    """
    TR-25: every shell of every solid must have the correct normal orientation.

    Outer shells must have positive signed volume. Inner shells representing
    voids or cavities must have negative signed volume when evaluated in
    isolation.
    """
    errors: list[Issue] = []
    indexes = build_indexes(data)
    surfaces = indexes["surfaces"]
    curves = indexes["curves"]
    points = indexes["points"]

    for solid in data.get("solids", []):
        solid_id = solid["id"]

        for shell in _solid_shells(solid):
            polygons = _shell_polygons(shell, surfaces, curves, points)
            if not polygons:
                continue

            signed_volume = signed_volume_of_polygons(polygons)
            issue = _shell_orientation_issue(
                solid_id,
                shell,
                signed_volume,
                tol,
            )

            if issue is not None:
                errors.append(issue)

    return errors


def validate(data: TopologyData, tolerances: Tolerances | None = None) -> list[Issue]:
    """Validate CC-05 solid topology rules.

    Args:
        data: Topology data to validate.
        tolerances: Optional tolerance overrides. If omitted, default tolerances
            are used.

    Returns:
        A list of validation issues found in `data`.
    """
    t = tolerances or Tolerances()

    issues: list[Issue] = []
    issues.extend(validate_positive_volume(data, min_positive_volume=t.volume))
    issues.extend(validate_minimum_solid_thickness(data, min_thickness=t.thickness))
    issues.extend(validate_no_solid_self_intersection(data))
    issues.extend(validate_shell_orientation(data))
    return issues
