#!/usr/bin/env python3

"""Mixed 2D/3D dataset partitioning (not yet wired into `validate_topology`).

A dataset may legitimately mix 3D topology with 2D content -- a cadastral
parcel outline, say -- that should be excluded from 3D conformance checks
rather than rejected outright (see `model.point_dimensionality`). This module
walks a topology dataset's reference graph (points -> curves -> surfaces ->
solids) to split it into a 3D-only view and a 2D-only view.

Solids never appear in the 2D view: nothing in today's data model represents
a "2D solid", so a solid tainted by 2D content is simply excluded from the 3D
view, not relocated anywhere. A future rule-reuse pass over the 2D view (not
yet implemented) is expected to run point/curve/surface-level topology rules
against it; there is currently no solid-level analogue to run.

Tainting is deliberately checked at whole-object granularity -- a surface
with one 2D ring member is entirely excluded, not partially validated -- so a
downstream rule is never handed a partially-resolved ring or face.

This module is self-contained and currently has no caller: `validate_structure`
and `validate_topology` (validator.py) are unchanged, and still classify a
dataset as uniformly 2D or 3D via `points_are_all_two_dimensional`.
"""

from __future__ import annotations

from .model import (
    Curve,
    Issue,
    MIXED_DIMENSION_CURVE_CODE,
    Point,
    PointDimensionality,
    Solid,
    Surface,
    TopologyData,
    err,
    point_dimensionality,
    solid_face_ids,
)


def classify_points(points: list[Point]) -> dict[str, PointDimensionality]:
    """Classify every point in *points* by coordinate dimensionality.

    Args:
        points: Point records to classify.

    Returns:
        A dimensionality classification keyed by point id. Points without a
        string id are skipped -- structural validation reports that
        separately. Last-one-wins on a duplicate id, matching
        `model.build_indexes`.
    """
    classes: dict[str, PointDimensionality] = {}

    for point in points:
        point_id = point.get("id") if isinstance(point, dict) else None
        if not isinstance(point_id, str):
            continue

        classes[point_id] = point_dimensionality(point)

    return classes


def _mixed_dimension_curve_issue(curve_id: str) -> Issue:
    """Build a MIXED_DIMENSION_CURVE issue for a curve spanning 2D and 3D points."""
    return err(
        MIXED_DIMENSION_CURVE_CODE,
        f"Curve {curve_id} references a mix of 2D and 3D points",
        object_id=curve_id,
    )


def find_2d_tainted_curves(
    curves: list[Curve],
    point_classes: dict[str, PointDimensionality],
) -> tuple[set[str], list[Issue]]:
    """Return the ids of curves touching a 2D point, and any mixed-dimension defects.

    A curve is 2D-tainted when at least one of its vertices resolves to a 2D
    point. A curve whose vertices resolve to *both* 2D and 3D points is a
    genuine defect -- unlike a curve consistently built from 2D points, which
    is legitimate 2D content -- so it is both tainted (excluded from the 3D
    view) and reported via `MIXED_DIMENSION_CURVE_CODE`. Vertices that don't
    resolve to a known point at all (missing, or classified "invalid") are
    ignored here; a missing point is `TR-11`'s concern, and a structurally
    invalid one is `INVALID_COORDINATES`'s, not this pass's.

    Args:
        curves: Curve records to classify.
        point_classes: Dimensionality classification from `classify_points`.

    Returns:
        A tuple of (tainted curve ids, mixed-dimension-curve issues).
    """
    tainted: set[str] = set()
    issues: list[Issue] = []

    for curve in curves:
        curve_id = curve.get("id") if isinstance(curve, dict) else None
        if not isinstance(curve_id, str):
            continue

        vertex_dims = {
            point_classes[vertex_id]
            for vertex_id in curve.get("vertices", [])
            if isinstance(vertex_id, str) and vertex_id in point_classes
        }

        has_2d = "2d" in vertex_dims
        has_3d = "3d" in vertex_dims

        if not has_2d:
            continue

        tainted.add(curve_id)
        if has_3d:
            issues.append(_mixed_dimension_curve_issue(curve_id))

    return tainted, issues


def find_2d_tainted_surfaces(
    surfaces: list[Surface],
    tainted_curve_ids: set[str],
) -> set[str]:
    """Return the ids of surfaces with at least one 2D-tainted ring member.

    Tainting applies to the whole surface, not the individual ring: a surface
    with one 2D-tainted ring member is excluded from the 3D view in full,
    never left as a partially-validated ring.

    Args:
        surfaces: Surface records to classify.
        tainted_curve_ids: Curve ids already identified as 2D-tainted.

    Returns:
        Ids of the 2D-tainted surfaces.
    """
    tainted: set[str] = set()

    for surface in surfaces:
        surface_id = surface.get("id") if isinstance(surface, dict) else None
        if not isinstance(surface_id, str):
            continue

        for ring in surface.get("rings", []):
            members = ring.get("members", []) if isinstance(ring, dict) else []
            if any(member.get("ref") in tainted_curve_ids for member in members):
                tainted.add(surface_id)
                break

    return tainted


def find_2d_tainted_solids(
    solids: list[Solid],
    tainted_surface_ids: set[str],
) -> set[str]:
    """Return the ids of solids owning at least one 2D-tainted face.

    Face ownership is resolved via `model.solid_face_ids`, the same helper
    TR-06/TR-10/TR-18 use, so this can never disagree with those rules about
    which faces belong to a solid.

    Args:
        solids: Solid records to classify.
        tainted_surface_ids: Surface ids already identified as 2D-tainted.

    Returns:
        Ids of the 2D-tainted solids.
    """
    tainted: set[str] = set()

    for solid in solids:
        solid_id = solid.get("id") if isinstance(solid, dict) else None
        if not isinstance(solid_id, str):
            continue

        if any(face_id in tainted_surface_ids for face_id in solid_face_ids(solid)):
            tainted.add(solid_id)

    return tainted


def partition_topology(data: TopologyData) -> tuple[TopologyData, TopologyData, list[Issue]]:
    """Split *data* into its 3D-only and 2D-only views.

    Args:
        data: Internal topology data, assumed structurally valid (this pass
            does not itself validate structure -- see `validator.py`).

    Returns:
        A tuple of (topology_3d, topology_2d, issues):

        - `topology_3d` is *data* with every 2D point and every 2D-tainted
          curve/surface/solid removed, so it is exactly what today's
          conformance-class rules should see. `observation_curves` and
          `surface_shell_face_refs` entries referencing a removed
          curve/surface are filtered out too, defensively.
        - `topology_2d` holds the 2D points plus every 2D-tainted curve and
          surface. It never contains solids -- see the module docstring.
        - `issues` currently holds only `MIXED_DIMENSION_CURVE` findings,
          discovered as a byproduct of tainting curves.

        Neither return value is consumed anywhere yet.
    """
    points = data.get("points", [])
    curves = data.get("curves", [])
    surfaces = data.get("surfaces", [])
    solids = data.get("solids", [])

    point_classes = classify_points(points)
    tainted_curve_ids, issues = find_2d_tainted_curves(curves, point_classes)
    tainted_surface_ids = find_2d_tainted_surfaces(surfaces, tainted_curve_ids)
    tainted_solid_ids = find_2d_tainted_solids(solids, tainted_surface_ids)

    points_2d_ids = {
        point_id
        for point_id, dimensionality in point_classes.items()
        if dimensionality == "2d"
    }

    topology_3d: TopologyData = {
        "points": [p for p in points if p.get("id") not in points_2d_ids],
        "curves": [c for c in curves if c.get("id") not in tainted_curve_ids],
        "surfaces": [s for s in surfaces if s.get("id") not in tainted_surface_ids],
        "solids": [s for s in solids if s.get("id") not in tainted_solid_ids],
    }

    if "observation_curves" in data:
        topology_3d["observation_curves"] = [
            observation_curve
            for observation_curve in data["observation_curves"]
            if observation_curve.get("ref") not in tainted_curve_ids
        ]

    if "surface_shell_face_refs" in data:
        topology_3d["surface_shell_face_refs"] = [
            face_ref
            for face_ref in data["surface_shell_face_refs"]
            if face_ref.get("ref") not in tainted_surface_ids
        ]

    topology_2d: TopologyData = {
        "points": [p for p in points if p.get("id") in points_2d_ids],
        "curves": [c for c in curves if c.get("id") in tainted_curve_ids],
        "surfaces": [s for s in surfaces if s.get("id") in tainted_surface_ids],
        "solids": [],
    }

    return topology_3d, topology_2d, issues
