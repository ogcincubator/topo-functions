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
THREE_D_COORDINATE_LENGTH = 3


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
                "3D topology conformance checks were skipped. 2D validation "
                "is not yet implemented.",
                path="points",
            )
        )

    issues.extend(_validate_points_structure(
        data["points"],
        minimum_coordinate_length=TWO_D_COORDINATE_LENGTH if two_dimensional else THREE_D_COORDINATE_LENGTH,
    ))
    issues.extend(_validate_curves_structure(data["curves"]))
    issues.extend(_validate_surfaces_structure(data["surfaces"]))
    issues.extend(_validate_solids_structure(data["solids"]))
    issues.extend(_validate_observation_curves_structure(data))
    issues.extend(_validate_reference_surfaces_structure(data))

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
    *,
    minimum_coordinate_length: int = THREE_D_COORDINATE_LENGTH,
) -> list[Issue]:
    """Validate point coordinate structure.

    Args:
        points: Point records to validate.
        minimum_coordinate_length: Minimum coordinate list length to accept.
            Callers pass 2 for an all-2D dataset (see
            `points_are_all_two_dimensional`), so 2D points aren't flagged as
            structurally invalid merely for lacking a z value.
    """
    issues: list[Issue] = []

    for index, point in enumerate(points):
        point_path = f"points[{index}]"
        coordinates_path = f"{point_path}.coordinates"
        coordinates = point.get("coordinates")
        object_id = point.get("id")

        if not isinstance(coordinates, list) or len(coordinates) < minimum_coordinate_length:
            issues.append(
                err(
                    "INVALID_COORDINATES",
                    f"{coordinates_path} must be a list with at least "
                    f"{minimum_coordinate_length} numbers",
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


def _validate_reference_surfaces_structure(data: Mapping[str, Any]) -> list[Issue]:
    """Validate optional reference surface exemption records."""
    reference_surfaces = data.get("reference_surfaces", [])

    if not isinstance(reference_surfaces, list):
        return [
            err(
                "INVALID_REFERENCE_SURFACES",
                "reference_surfaces must be a list when present",
                path="reference_surfaces",
                extra={"actual_type": type(reference_surfaces).__name__},
            )
        ]

    issues: list[Issue] = []
    for index, reference_surface in enumerate(reference_surfaces):
        path = f"reference_surfaces[{index}]"

        if not isinstance(reference_surface, dict):
            issues.append(
                err(
                    "INVALID_REFERENCE_SURFACE",
                    f"{path} must be an object",
                    path=path,
                    extra={"actual_type": type(reference_surface).__name__},
                )
            )
            continue

        ref = reference_surface.get("ref")
        if not isinstance(ref, str):
            issues.append(
                err(
                    "INVALID_REFERENCE_SURFACE_REF",
                    f"{path}.ref must be a string",
                    path=f"{path}.ref",
                    extra={"actual_type": type(ref).__name__},
                )
            )

        source = reference_surface.get("source")
        if not isinstance(source, str):
            issues.append(
                err(
                    "INVALID_REFERENCE_SURFACE_SOURCE",
                    f"{path}.source must be a string",
                    path=f"{path}.source",
                    extra={"actual_type": type(source).__name__},
                )
            )

    return issues


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


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

    if points_are_all_two_dimensional(data.get("points")):
        # Conformance-class rules assume 3D coordinates throughout (volume,
        # thickness, 3D segment intersection, ...); running them against an
        # all-2D dataset would either crash or produce meaningless results.
        # validate_structure() already recorded the NO_3D_TOPOLOGY warning.
        if progress is not None:
            progress("Skipping topology conformance checks: no 3D topology found (2D data)")
        return issues

    from .conformance import CONFORMANCE_CLASSES

    topology = cast(TopologyData, cast(object, data))
    selected = set(conformance_classes or [])

    for cc in CONFORMANCE_CLASSES:
        if selected and cc.CONFORMANCE_CLASS_ID not in selected:
            continue

        class_label = (
            f"{cc.CONFORMANCE_CLASS_ID} "
            f"{getattr(cc, 'CONFORMANCE_CLASS_NAME', cc.__name__)}"
        )

        if progress is not None:
            progress(f"Running {class_label}")

        class_issues = cc.validate(topology, tolerances=t)
        issues.extend(class_issues)

        if progress is not None:
            progress(f"Completed {class_label} ({len(class_issues)} issue(s))")

    return issues
