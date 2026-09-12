#!/usr/bin/env python3

"""Validate WA 3D CSDM topology boundary-block rules."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, cast

from .model import (
    Issue,
    TOLERANCE_LENGTH,
    TOLERANCE_POINT,
    TOLERANCE_THICKNESS,
    TOLERANCE_VOLUME,
    Tolerances,
    TopologyData,
    err,
    errors_only,
    warn,
)

REQUIRED_COLLECTIONS = ("points", "curves", "surfaces", "solids")
ORIENTATIONS = {"+", "-"}
OBSERVATION_CURVE_SOURCES = {"observedVectors", "vectorObservations"}
RELATIONSHIP_ID_FIELDS = ("parent_id", "servient_id", "burdened_id", "host_id")
SHELL_TYPES = {"outer", "inner"}

TWO_D_COORDINATE_LENGTH = 2


# ---------------------------------------------------------------------------
# Structure validation
# ---------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _invalid_collection_type_issue(items: Any, collection_name: str) -> Issue:
    return err(
        "INVALID_COLLECTION_TYPE",
        f"Collection {collection_name!r} must be a list",
        path=collection_name,
        extra={"actual_type": type(items).__name__},
    )


def _invalid_object_type_issue(item: Any, path: str) -> Issue:
    return err(
        "INVALID_OBJECT_TYPE",
        f"{path} must be an object",
        path=path,
        extra={"actual_type": type(item).__name__},
    )


def _missing_id_issue(path: str) -> Issue:
    return err(
        "MISSING_ID",
        f"{path} is missing required id",
        path=f"{path}.id",
    )


def _invalid_id_type_issue(object_id: Any, id_path: str) -> Issue:
    return err(
        "INVALID_ID_TYPE",
        f"{id_path} must be a string",
        path=id_path,
        extra={"actual_type": type(object_id).__name__},
    )


def _duplicate_id_issue(
    object_id: str,
    collection_name: str,
    path: str,
    first_index: int,
    duplicate_index: int,
) -> Issue:
    return err(
        "DUPLICATE_ID",
        f"Duplicate id {object_id!r} in {collection_name}",
        object_id=object_id,
        path=f"{path}.id",
        extra={
            "collection": collection_name,
            "first_index": first_index,
            "duplicate_index": duplicate_index,
        },
    )


def _validate_unique_id_item(
    item: Any,
    collection_name: str,
    index: int,
    seen_ids_by_index: dict[str, int],
) -> list[Issue]:
    """Validate one collection item has a unique string id."""
    path = f"{collection_name}[{index}]"
    id_path = f"{path}.id"

    if not isinstance(item, dict):
        return [_invalid_object_type_issue(item, path)]

    object_id = item.get("id")
    if object_id is None:
        return [_missing_id_issue(path)]

    if not isinstance(object_id, str):
        return [_invalid_id_type_issue(object_id, id_path)]

    if object_id in seen_ids_by_index:
        return [
            _duplicate_id_issue(
                object_id,
                collection_name,
                path,
                seen_ids_by_index[object_id],
                index,
            )
        ]

    seen_ids_by_index[object_id] = index
    return []


def _validate_unique_ids(
    items: Any,
    collection_name: str,
) -> list[Issue]:
    """Validate object shape and unique string ids for one collection."""
    if not isinstance(items, list):
        return [_invalid_collection_type_issue(items, collection_name)]

    issues: list[Issue] = []
    seen_ids_by_index: dict[str, int] = {}

    for index, item in enumerate(items):
        issues.extend(
            _validate_unique_id_item(
                item,
                collection_name,
                index,
                seen_ids_by_index,
            )
        )

    return issues


def validate_structure(data: Mapping[str, Any]) -> list[Issue]:
    """Validate the internal topology dict shape before topology rules run.

    Args:
        data: Internal topology dictionary.

    Returns:
        Structural validation issues. If any error is returned, topology rule
        validators should not run because indexes and references may be unsafe.
    """
    issues = _validate_required_collections(data)

    if errors_only(issues):
        return issues

    two_dimensional = points_are_all_two_dimensional(data["points"])
    if two_dimensional:
        issues.append(
            warn(
                "NO_3D_TOPOLOGY",
                "All points have 2D coordinates; no 3D topology found. "
                "3D-specific conformance checks (shell/solid/volume rules) do "
                "not apply; the 2D-applicable point/curve/surface rules were "
                "run instead.",
                path="points",
            )
        )

    issues.extend(_validate_points_structure(data["points"]))
    issues.extend(_validate_curves_structure(data["curves"]))
    issues.extend(_validate_surfaces_structure(data["surfaces"]))
    issues.extend(_validate_solids_structure(data["solids"]))
    issues.extend(_validate_observation_curves_structure(data))
    issues.extend(_validate_surface_shell_face_refs_structure(data))

    return issues


def _validate_required_collections(data: Mapping[str, Any]) -> list[Issue]:
    """Validate required top-level collections and their object ids."""
    issues: list[Issue] = []

    for collection_name in REQUIRED_COLLECTIONS:
        if collection_name not in data:
            issues.append(
                err(
                    "MISSING_COLLECTION",
                    f"Missing required collection {collection_name!r}",
                    path=collection_name,
                )
            )
            continue

        issues.extend(_validate_unique_ids(data[collection_name], collection_name))

    return issues


def _is_two_dimensional_point(coordinates: Any) -> bool:
    """True when *coordinates* is exactly a [x, y] pair of numbers (no z)."""
    return (
        isinstance(coordinates, list)
        and len(coordinates) == TWO_D_COORDINATE_LENGTH
        and all(_is_number(v) for v in coordinates)
    )


def points_are_all_two_dimensional(points: Any) -> bool:
    """True when *points* is a non-empty list and every point has valid 2D
    (not 3D) coordinates -- i.e. the dataset as a whole is 2D, not merely one
    malformed point short of 3D. A mixed or otherwise-invalid points
    collection returns False and is left to the normal 3D-required
    structural checks."""
    return bool(points) and isinstance(points, list) and all(
        isinstance(p, dict) and _is_two_dimensional_point(p.get("coordinates"))
        for p in points
    )


def _validate_points_structure(
    points: list[dict[str, Any]],
) -> list[Issue]:
    """Validate point coordinate structure.

    Each point is checked independently against a floor of
    `TWO_D_COORDINATE_LENGTH`: a 2D point ([x, y]) and a 3D point
    ([x, y, z, ...]) are both structurally valid regardless of what the rest
    of the dataset looks like, so a dataset mixing 2D content (e.g. a
    cadastral parcel outline) with 3D topology does not have every 2D point
    rejected merely because the rest of the dataset is 3D. Only a shorter
    (0- or 1-value) coordinates list is a structural error.

    Args:
        points: Point records to validate.
    """
    issues: list[Issue] = []

    for index, point in enumerate(points):
        point_path = f"points[{index}]"
        coordinates_path = f"{point_path}.coordinates"
        coordinates = point.get("coordinates")
        object_id = point.get("id")

        if not isinstance(coordinates, list) or len(coordinates) < TWO_D_COORDINATE_LENGTH:
            issues.append(
                err(
                    "INVALID_COORDINATES",
                    f"{coordinates_path} must be a list with at least "
                    f"{TWO_D_COORDINATE_LENGTH} numbers",
                    object_id=object_id,
                    path=coordinates_path,
                )
            )
            continue

        for coord_index, value in enumerate(coordinates[:3]):
            coordinate_value_path = f"{coordinates_path}[{coord_index}]"
            if not _is_number(value):
                issues.append(
                    err(
                        "INVALID_COORDINATE_VALUE",
                        f"{coordinate_value_path} must be numeric",
                        object_id=object_id,
                        path=coordinate_value_path,
                        extra={"actual_type": type(value).__name__},
                    )
                )

    return issues


def _validate_curves_structure(curves: list[dict[str, Any]]) -> list[Issue]:
    """Validate curve vertex reference structure."""
    issues: list[Issue] = []

    for index, curve in enumerate(curves):
        curve_path = f"curves[{index}]"
        vertices_path = f"{curve_path}.vertices"
        vertices = curve.get("vertices")
        object_id = curve.get("id")

        if not isinstance(vertices, list):
            issues.append(
                err(
                    "INVALID_VERTICES",
                    f"{vertices_path} must be a list of point ids",
                    object_id=object_id,
                    path=vertices_path,
                )
            )
            continue

        for vertex_index, vertex_id in enumerate(vertices):
            vertex_path = f"{vertices_path}[{vertex_index}]"
            if not isinstance(vertex_id, str):
                issues.append(
                    err(
                        "INVALID_VERTEX_ID",
                        f"{vertex_path} must be a string",
                        object_id=object_id,
                        path=vertex_path,
                        extra={"actual_type": type(vertex_id).__name__},
                    )
                )

    return issues


def _validate_surfaces_structure(surfaces: list[dict[str, Any]]) -> list[Issue]:
    """Validate surface ring structure."""
    issues: list[Issue] = []

    for surface_index, surface in enumerate(surfaces):
        surface_path = f"surfaces[{surface_index}]"
        rings_path = f"{surface_path}.rings"
        rings = surface.get("rings")
        object_id = surface.get("id")

        if not isinstance(rings, list):
            issues.append(
                err(
                    "INVALID_RINGS",
                    f"{rings_path} must be a list",
                    object_id=object_id,
                    path=rings_path,
                )
            )
            continue

        for ring_index, ring in enumerate(rings):
            ring_path = f"{rings_path}[{ring_index}]"
            issues.extend(_validate_surface_ring_structure(ring, ring_path, object_id))

    return issues


def _validate_surface_ring_structure(
    ring: Any,
    ring_path: str,
    object_id: str | None,
) -> list[Issue]:
    """Validate one surface ring object."""
    issues: list[Issue] = []

    if not isinstance(ring, dict):
        return [
            err(
                "INVALID_RING",
                f"{ring_path} must be an object",
                object_id=object_id,
                path=ring_path,
                extra={"actual_type": type(ring).__name__},
            )
        ]

    members_path = f"{ring_path}.members"
    members = ring.get("members")
    if not isinstance(members, list):
        return [
            err(
                "INVALID_RING_MEMBERS",
                f"{members_path} must be a list",
                object_id=object_id,
                path=members_path,
            )
        ]

    for member_index, member in enumerate(members):
        member_path = f"{members_path}[{member_index}]"
        issues.extend(
            _validate_surface_ring_member_structure(member, member_path, object_id)
        )

    return issues


def _validate_surface_ring_member_structure(
    member: Any,
    member_path: str,
    object_id: str | None,
) -> list[Issue]:
    """Validate one directed surface ring member."""
    issues: list[Issue] = []

    if not isinstance(member, dict):
        return [
            err(
                "INVALID_RING_MEMBER",
                f"{member_path} must be an object",
                object_id=object_id,
                path=member_path,
                extra={"actual_type": type(member).__name__},
            )
        ]

    ref = member.get("ref")
    if not isinstance(ref, str):
        issues.append(
            err(
                "INVALID_RING_MEMBER_REF",
                f"{member_path}.ref must be a string",
                object_id=object_id,
                path=f"{member_path}.ref",
                extra={"actual_type": type(ref).__name__},
            )
        )

    orientation = member.get("orientation")
    if orientation not in ORIENTATIONS:
        issues.append(
            err(
                "INVALID_ORIENTATION",
                f"{member_path}.orientation must be '+' or '-'",
                object_id=object_id,
                path=f"{member_path}.orientation",
                extra={"actual_value": orientation},
            )
        )

    return issues


def _validate_solids_structure(solids: list[dict[str, Any]]) -> list[Issue]:
    """Validate solid topology and metadata structure."""
    issues: list[Issue] = []

    for solid_index, solid in enumerate(solids):
        solid_path = f"solids[{solid_index}]"
        object_id = solid.get("id")

        issues.extend(_validate_solid_faces_structure(solid, solid_path, object_id))
        issues.extend(_validate_solid_volume_structure(solid, solid_path, object_id))
        issues.extend(_validate_solid_levels_structure(solid, solid_path, object_id))
        issues.extend(_validate_solid_relationship_ids(solid, solid_path, object_id))
        issues.extend(_validate_solid_shells_structure(solid, solid_path, object_id))

    return issues


def _validate_solid_faces_structure(
    solid: dict[str, Any],
    solid_path: str,
    object_id: str | None,
) -> list[Issue]:
    """Validate a solid's face id list."""
    issues: list[Issue] = []
    faces_path = f"{solid_path}.faces"
    faces = solid.get("faces")

    if not isinstance(faces, list):
        return [
            err(
                "INVALID_FACES",
                f"{faces_path} must be a list of surface ids",
                object_id=object_id,
                path=faces_path,
            )
        ]

    for face_index, face_id in enumerate(faces):
        face_path = f"{faces_path}[{face_index}]"
        if not isinstance(face_id, str):
            issues.append(
                err(
                    "INVALID_FACE_ID",
                    f"{face_path} must be a string",
                    object_id=object_id,
                    path=face_path,
                    extra={"actual_type": type(face_id).__name__},
                )
            )

    return issues


def _validate_solid_volume_structure(
    solid: dict[str, Any],
    solid_path: str,
    object_id: str | None,
) -> list[Issue]:
    """Validate a solid's declared volume value."""
    volume = solid.get("volume", 0.0)

    if _is_number(volume):
        return []

    return [
        err(
            "INVALID_VOLUME",
            f"{solid_path}.volume must be numeric",
            object_id=object_id,
            path=f"{solid_path}.volume",
            extra={"actual_type": type(volume).__name__},
        )
    ]


def _validate_solid_levels_structure(
    solid: dict[str, Any],
    solid_path: str,
    object_id: str | None,
) -> list[Issue]:
    """Validate a solid's level identifiers."""
    levels = solid.get("levels", [])

    if isinstance(levels, list) and all(isinstance(level, str) for level in levels):
        return []

    return [
        err(
            "INVALID_LEVELS",
            f"{solid_path}.levels must be a list of strings",
            object_id=object_id,
            path=f"{solid_path}.levels",
        )
    ]


def _validate_solid_relationship_ids(
    solid: dict[str, Any],
    solid_path: str,
    object_id: str | None,
) -> list[Issue]:
    """Validate optional solid relationship id fields."""
    issues: list[Issue] = []

    for field_name in RELATIONSHIP_ID_FIELDS:
        value = solid.get(field_name)
        if value is not None and not isinstance(value, str):
            issues.append(
                err(
                    "INVALID_RELATIONSHIP_ID",
                    f"{solid_path}.{field_name} must be a string or null",
                    object_id=object_id,
                    path=f"{solid_path}.{field_name}",
                    extra={"actual_type": type(value).__name__},
                )
            )

    return issues


def _validate_solid_shells_structure(
    solid: dict[str, Any],
    solid_path: str,
    object_id: str | None,
) -> list[Issue]:
    """Validate optional structured shell records for one solid."""
    issues: list[Issue] = []
    shells = solid.get("shells", [])

    if shells is None:
        shells = []
    elif not isinstance(shells, list):
        return [
            err(
                "INVALID_SHELLS",
                f"{solid_path}.shells must be a list when present",
                object_id=object_id,
                path=f"{solid_path}.shells",
            )
        ]

    for shell_index, shell in enumerate(shells):
        shell_path = f"{solid_path}.shells[{shell_index}]"
        issues.extend(_validate_solid_shell_structure(shell, shell_path, object_id))

    return issues


def _validate_solid_shell_structure(
    shell: Any,
    shell_path: str,
    object_id: str | None,
) -> list[Issue]:
    """Validate one structured solid shell record."""
    issues: list[Issue] = []

    if not isinstance(shell, dict):
        return [
            err(
                "INVALID_SHELL",
                f"{shell_path} must be an object",
                object_id=object_id,
                path=shell_path,
                extra={"actual_type": type(shell).__name__},
            )
        ]

    shell_type = shell.get("type", "outer")
    if shell_type not in SHELL_TYPES:
        issues.append(
            err(
                "INVALID_SHELL_TYPE",
                f"{shell_path}.type must be 'outer' or 'inner'",
                object_id=object_id,
                path=f"{shell_path}.type",
                extra={"actual_value": shell_type},
            )
        )

    shell_faces = shell.get("faces")
    if not isinstance(shell_faces, list) or not all(
        isinstance(face_id, str) for face_id in shell_faces
    ):
        issues.append(
            err(
                "INVALID_SHELL_FACES",
                f"{shell_path}.faces must be a list of surface ids",
                object_id=object_id,
                path=f"{shell_path}.faces",
            )
        )

    face_orientations = shell.get("face_orientations", {})
    if not isinstance(face_orientations, dict):
        issues.append(
            err(
                "INVALID_FACE_ORIENTATIONS",
                f"{shell_path}.face_orientations must be an object",
                object_id=object_id,
                path=f"{shell_path}.face_orientations",
            )
        )
        return issues

    for face_id, orientation in face_orientations.items():
        if not isinstance(face_id, str) or orientation not in ORIENTATIONS:
            issues.append(
                err(
                    "INVALID_FACE_ORIENTATION",
                    f"{shell_path}.face_orientations entries must map "
                    f"surface ids to '+' or '-'",
                    object_id=object_id,
                    path=f"{shell_path}.face_orientations",
                    extra={"face_id": face_id, "orientation": orientation},
                )
            )

    return issues


def _validate_observation_curves_structure(data: Mapping[str, Any]) -> list[Issue]:
    """Validate optional observation curve exemption records."""
    observation_curves = data.get("observation_curves", [])

    if not isinstance(observation_curves, list):
        return [
            err(
                "INVALID_OBSERVATION_CURVES",
                "observation_curves must be a list when present",
                path="observation_curves",
                extra={"actual_type": type(observation_curves).__name__},
            )
        ]

    issues: list[Issue] = []
    for index, observation_curve in enumerate(observation_curves):
        path = f"observation_curves[{index}]"

        if not isinstance(observation_curve, dict):
            issues.append(
                err(
                    "INVALID_OBSERVATION_CURVE",
                    f"{path} must be an object",
                    path=path,
                    extra={"actual_type": type(observation_curve).__name__},
                )
            )
            continue

        ref = observation_curve.get("ref")
        if not isinstance(ref, str):
            issues.append(
                err(
                    "INVALID_OBSERVATION_CURVE_REF",
                    f"{path}.ref must be a string",
                    path=f"{path}.ref",
                    extra={"actual_type": type(ref).__name__},
                )
            )

        source = observation_curve.get("source")
        if source not in OBSERVATION_CURVE_SOURCES:
            issues.append(
                err(
                    "INVALID_OBSERVATION_CURVE_SOURCE",
                    f"{path}.source must be 'observedVectors' or 'vectorObservations'",
                    path=f"{path}.source",
                    extra={"actual_value": source},
                )
            )

    return issues


def _validate_surface_shell_face_refs_structure(
    data: Mapping[str, Any],
) -> list[Issue]:
    """Validate optional surface shell face reference records."""
    face_refs = data.get("surface_shell_face_refs", [])

    if not isinstance(face_refs, list):
        return [
            err(
                "INVALID_SURFACE_SHELL_FACE_REFS",
                "surface_shell_face_refs must be a list when present",
                path="surface_shell_face_refs",
                extra={"actual_type": type(face_refs).__name__},
            )
        ]

    issues: list[Issue] = []
    for index, face_ref in enumerate(face_refs):
        path = f"surface_shell_face_refs[{index}]"

        if not isinstance(face_ref, dict):
            issues.append(
                err(
                    "INVALID_SURFACE_SHELL_FACE_REF",
                    f"{path} must be an object",
                    path=path,
                    extra={"actual_type": type(face_ref).__name__},
                )
            )
            continue

        ref = face_ref.get("ref")
        if not isinstance(ref, str):
            issues.append(
                err(
                    "INVALID_SURFACE_SHELL_FACE_REF_REF",
                    f"{path}.ref must be a string",
                    path=f"{path}.ref",
                    extra={"actual_type": type(ref).__name__},
                )
            )

        shell_id = face_ref.get("shell_id")
        if not isinstance(shell_id, str):
            issues.append(
                err(
                    "INVALID_SURFACE_SHELL_FACE_REF_SHELL_ID",
                    f"{path}.shell_id must be a string",
                    path=f"{path}.shell_id",
                    extra={"actual_type": type(shell_id).__name__},
                )
            )

    return issues


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


EXCLUDED_FROM_3D_VALIDATION_CODE = "EXCLUDED_FROM_3D_VALIDATION"


def _excluded_from_3d_validation_issue(category: str, excluded_ids: list[str]) -> Issue:
    """Build a summarized, non-silent notice for one excluded-from-3D category.

    One issue per category (not per id) mirrors the existing single-line
    NO_3D_TOPOLOGY warning rather than reproducing a wall of near-identical
    per-id lines; full traceability lives in `extra.excluded_ids`.
    """
    return warn(
        EXCLUDED_FROM_3D_VALIDATION_CODE,
        f"{len(excluded_ids)} {category} excluded from 3D topology validation "
        f"because they are 2D; 2D-specific validation is not yet implemented "
        f"for {category}.",
        extra={
            "category": category,
            "count": len(excluded_ids),
            "excluded_ids": excluded_ids,
        },
    )


def _dimensionality_exclusion_issues(excluded_solid_ids: set[str]) -> list[Issue]:
    """Build a summarized, non-silent notice for solids excluded from 3D validation.

    Points, curves, and surfaces routed into the 2D view are validated by
    `_run_2d_applicable_rules` instead of being blanket-excluded. Solids have
    no 2D analogue in any rule -- TR-06/07/18/19/24/25/26/27 are inherently
    volumetric -- so they are the only category this notice still covers.

    Args:
        excluded_solid_ids: Ids of solids removed from the 3D view because
            they own at least one 2D-tainted face. Solids never appear in
            `topology_2d` itself -- see `dimensionality`'s module docstring.
    """
    if not excluded_solid_ids:
        return []

    return [_excluded_from_3d_validation_issue("solids", sorted(excluded_solid_ids))]


def _tag_as_2d(issues: list[Issue]) -> list[Issue]:
    """Return *issues* with `extra.dimensionality` set to "2d".

    Lets a report distinguish a 2D-view finding under a TR-xx code from an
    identical-looking 3D-view finding under the same code.
    """
    for issue in issues:
        issue["extra"] = {**issue.get("extra", {}), "dimensionality": "2d"}
    return issues


def _pad_2d_points_to_3d(topology_2d: TopologyData) -> TopologyData:
    """Return a shallow copy of *topology_2d* with every point padded to (x, y, 0.0).

    The shared 3D segment-intersection helper (`geometry.segments_intersect_3d`
    and its `vec_sub`/`vec_cross`/`vec_dot` primitives) hard-indexes a third
    coordinate. Every point in `topology_2d` is 2D by construction (see
    `dimensionality.partition_topology`), so padding every point onto a common
    z=0 plane is exact, not an approximation: a curve or ring living entirely
    at z=0 self-intersects (or doesn't) identically to its unpadded 2D
    projection. `curves` and `surfaces` reference points by id and are
    returned unchanged.

    Only used internally for the three rules that reach the 3D helper
    (`TR-02`/`TR-14`/`TR-15`); their issue messages and `extra` fields
    reference object ids and segment indices, never raw coordinates, so the
    padding never surfaces in reported output.
    """
    padded_points = [
        {**point, "coordinates": [point["coordinates"][0], point["coordinates"][1], 0.0]}
        for point in topology_2d.get("points", [])
    ]
    return {**topology_2d, "points": padded_points}


def _run_2d_applicable_rules(
    topology_2d: TopologyData,
    tolerances: Tolerances,
) -> list[Issue]:
    """Run the 2D-applicable subset of point/curve/surface rules against the
    2D-only view produced by `dimensionality.partition_topology`.

    Most rules here are purely referential, or use coordinate math that
    degrades correctly for consistently-2D input -- `topology_2d` never mixes
    2D and 3D points, so the pairwise-distance checks (TR-01, TR-12) never
    hit the length-mismatch case that would make reusing them unsafe.

    TR-02 (CurveNoSelfIntersection), TR-14 (CurveIntersectionAtNodesOnly), and
    TR-15 (NoSurfaceSelfIntersection) all call the shared 3D
    segment-intersection helper, which hard-indexes a third coordinate; these
    three are run against a z=0-padded copy of `topology_2d` (see
    `_pad_2d_points_to_3d`) rather than `topology_2d` itself.

    Every returned issue is tagged `extra.dimensionality = "2d"`.
    """
    from .conformance.cc01_points import (
        validate_point_fabric_consistency,
        validate_unique_points,
    )
    from .conformance.cc02_curves import (
        validate_curve_intersection_at_nodes_only,
        validate_curve_no_self_intersection,
        validate_curve_orientation,
        validate_minimum_curve_length,
        validate_no_dangling_curves,
        validate_no_duplicate_curves,
    )
    from .conformance.cc03_surfaces import (
        validate_no_duplicate_surfaces,
        validate_no_surface_self_intersection,
        validate_shared_surface_edges,
        validate_surface_closed_rings,
        validate_surface_connected_interior,
        validate_surface_curve_consistency,
    )

    issues: list[Issue] = []
    issues.extend(validate_unique_points(topology_2d, tol=tolerances.point))
    issues.extend(validate_point_fabric_consistency(topology_2d))
    issues.extend(validate_no_dangling_curves(topology_2d))
    issues.extend(
        validate_minimum_curve_length(topology_2d, min_length=tolerances.length)
    )
    issues.extend(validate_no_duplicate_curves(topology_2d))
    issues.extend(validate_curve_orientation(topology_2d))
    issues.extend(validate_surface_closed_rings(topology_2d))
    issues.extend(validate_shared_surface_edges(topology_2d))
    issues.extend(validate_no_duplicate_surfaces(topology_2d))
    issues.extend(validate_surface_curve_consistency(topology_2d))
    issues.extend(validate_surface_connected_interior(topology_2d))

    padded_topology_2d = _pad_2d_points_to_3d(topology_2d)
    issues.extend(validate_curve_no_self_intersection(padded_topology_2d))
    issues.extend(validate_curve_intersection_at_nodes_only(padded_topology_2d))
    issues.extend(validate_no_surface_self_intersection(padded_topology_2d))

    return _tag_as_2d(issues)


def validate_topology(
    data: Mapping[str, Any],
    tol: dict[str, float] | Tolerances | None = None,
    conformance_classes: list[str] | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[Issue]:
    """Run all topology validation rules.

    Args:
        data: Internal topology dict with points, curves, surfaces, and solids.
        tol: Optional tolerance override. Maybe a Tolerances instance, a dict
            with point/volume/length/thickness keys, or None for defaults.
        conformance_classes: Optional list of conformance class ids to run. When
            omitted or None, all registered conformance classes are run. Example
            values include "CC-01", "CC-02", "CC-03" to "CC-07".
        progress: Optional callback for validation progress messages.

    Returns:
        Combined list of structural and topology validation issues.
    """
    if tol is None:
        t = Tolerances()
    elif isinstance(tol, dict):
        t = Tolerances(
            point=tol.get("point", TOLERANCE_POINT),
            volume=tol.get("volume", TOLERANCE_VOLUME),
            length=tol.get("length", TOLERANCE_LENGTH),
            thickness=tol.get("thickness", TOLERANCE_THICKNESS),
        )
    else:
        t = tol

    issues: list[Issue] = []

    if progress is not None:
        progress("Running Structure validation")

    structure_issues = validate_structure(data)
    issues.extend(structure_issues)

    if progress is not None:
        progress(
            "Completed Structure validation "
            f"({len(structure_issues)} issue(s))"
        )

    if errors_only(issues):
        if progress is not None:
            progress("Skipping topology conformance checks because structure errors were found")
        return issues

    # A pure-2D dataset is not special-cased here: `partition_topology` routes
    # every point (and everything built from them) into `topology_2d`, so
    # `topology_3d` simply ends up empty and the CC-01..07 loop below no-ops
    # over it harmlessly. This is what makes a pure-2D dataset get the same
    # real 2D-applicable rule coverage as the 2D remainder of a mixed
    # dataset, instead of only ever producing the NO_3D_TOPOLOGY warning from
    # validate_structure() above.
    from .conformance import CONFORMANCE_CLASSES
    from .dimensionality import partition_topology

    topology = cast(TopologyData, cast(object, data))

    if progress is not None:
        progress("Running mixed 2D/3D dimensionality partitioning")

    topology_3d, topology_2d, dimensionality_issues = partition_topology(topology)
    issues.extend(dimensionality_issues)

    excluded_solid_ids = {
        solid["id"] for solid in topology.get("solids", [])
    } - {solid["id"] for solid in topology_3d.get("solids", [])}
    exclusion_issues = _dimensionality_exclusion_issues(excluded_solid_ids)
    issues.extend(exclusion_issues)

    two_dimensional_rule_issues = _run_2d_applicable_rules(topology_2d, t)
    issues.extend(two_dimensional_rule_issues)

    if progress is not None:
        progress(
            "Completed dimensionality partitioning and 2D-applicable rules "
            f"({len(dimensionality_issues) + len(exclusion_issues) + len(two_dimensional_rule_issues)} issue(s))"
        )

    selected = set(conformance_classes or [])

    for cc in CONFORMANCE_CLASSES:
        if selected and cc.CONFORMANCE_CLASS_ID not in selected:
            continue

        class_label = f"{cc.CONFORMANCE_CLASS_ID} {cc.CONFORMANCE_CLASS_NAME}"

        if progress is not None:
            progress(f"Running {class_label}")

        class_issues = cc.validate(topology_3d, tolerances=t)
        issues.extend(class_issues)

        if progress is not None:
            progress(f"Completed {class_label} ({len(class_issues)} issue(s))")

    return issues
