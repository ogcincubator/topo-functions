#!/usr/bin/env python3

"""Conformance checks for CC-07 containment and host topology.

This module validates containment and host relationship rules for a
`TopologyData` instance, including parent containment, easement containment,
and thematic host relationships.
"""

from __future__ import annotations

from ..geometry import (
    solid_bbox,
    bbox_contains,
)
from ..model import (
    Curve,
    Issue,
    Point,
    Solid,
    Surface,
    Tolerances,
    TopologyData,
    build_indexes,
    err,
)

CONFORMANCE_CLASS_ID = "CC-07"
CONFORMANCE_CLASS_NAME = "Containment and host topology"
RULE_IDS = ["TR-09", "TR-20", "TR-21"]

SECONDARY_PARCEL_TYPES = {"easement", "secondary"}
THEMATIC_PARCEL_TYPE = "thematic"
THEMATIC_SOLID_MISSING_HOST_CODE = "THEMATIC_SOLID_MISSING_HOST"
UNKNOWN_HOST_REFERENCE_CODE = "UNKNOWN_HOST_REFERENCE"


# ---------------------------------------------------------------------------
# TR-09  Parent-child containment
# ---------------------------------------------------------------------------
# TODO(#56): Restrict parent containment checks to child solids whose parent
# parcel resolves to a PrimaryParcel-compatible solid.
def _unknown_parent_reference_issue(solid: Solid, parent_id: str) -> Issue:
    """Build a TR-09 issue for a parent_id that does not resolve to a solid."""
    solid_id = solid["id"]

    return err(
        "UNKNOWN_PARENT_REFERENCE",
        f"Solid {solid_id} references unknown parent {parent_id!r}",
        object_id=solid_id,
        extra={"parent_id": parent_id},
    )


def _child_not_contained_issue(
    solid: Solid,
    parent_id: str,
    child_bbox: tuple[float, float, float, float, float, float],
    parent_bbox: tuple[float, float, float, float, float, float],
) -> Issue:
    """Build a TR-09 issue for a child solid outside its parent's bounding box."""
    solid_id = solid["id"]

    return err(
        "CHILD_NOT_CONTAINED_IN_PARENT",
        f"Solid {solid_id} is not fully contained within parent {parent_id!r}",
        object_id=solid_id,
        extra={
            "parent_id": parent_id,
            "child_bbox": child_bbox,
            "parent_bbox": parent_bbox,
        },
    )


def _solid_is_contained_in_parent(
    solid: Solid,
    parent: Solid,
    surfaces: dict[str, Surface],
    curves: dict[str, Curve],
    points: dict[str, Point],
) -> tuple[
    bool,
    tuple[float, float, float, float, float, float] | None,
    tuple[float, float, float, float, float, float] | None,
]:
    """Return whether a child solid's bounding box is contained by its parent."""
    child_bbox = solid_bbox(solid, surfaces, curves, points)
    parent_bbox = solid_bbox(parent, surfaces, curves, points)

    if child_bbox is None or parent_bbox is None:
        return True, child_bbox, parent_bbox

    return bbox_contains(parent_bbox, child_bbox), child_bbox, parent_bbox


def validate_parent_containment(
    data: TopologyData,
) -> list[Issue]:
    """
    TR-09: child parcel bounding box must be fully contained within its
    parent parcel bounding box.
    """
    issues: list[Issue] = []
    topology_indexes = build_indexes(data)
    surfaces = topology_indexes["surfaces"]
    curves = topology_indexes["curves"]
    points = topology_indexes["points"]
    solids_by_id = topology_indexes["solids"]
    solids = data.get("solids", [])

    for solid in solids:
        parent_id = solid.get("parent_id")
        if not parent_id:
            continue

        parent = solids_by_id.get(parent_id)
        if parent is None:
            issues.append(_unknown_parent_reference_issue(solid, parent_id))
            continue

        is_contained, child_bbox, parent_bbox = _solid_is_contained_in_parent(
            solid,
            parent,
            surfaces,
            curves,
            points,
        )
        if not is_contained and child_bbox is not None and parent_bbox is not None:
            issues.append(
                _child_not_contained_issue(
                    solid,
                    parent_id,
                    child_bbox,
                    parent_bbox,
                )
            )

    return issues

# ---------------------------------------------------------------------------
# TR-20  Easement containment
# ---------------------------------------------------------------------------


def _secondary_parcel_burdened_id(solid: Solid) -> str | None:
    """Return the burdened parcel id for a secondary/easement solid."""
    return solid.get("burdened_id") or solid.get("servient_id")


def _missing_burdened_issue(solid: Solid) -> Issue:
    """Build a TR-20 issue for a secondary/easement solid without a burdened id."""
    return err(
        "EASEMENT_MISSING_BURDENED",
        f"Secondary/easement solid {solid['id']} has no burdened_id",
        object_id=solid["id"],
    )


def _unknown_burdened_reference_issue(
    solid: Solid,
    burdened_id: str,
) -> Issue:
    """Build a TR-20 issue for an unknown burdened parcel reference."""
    return err(
        "UNKNOWN_BURDENED_REFERENCE",
        f"Secondary/easement solid {solid['id']} references unknown "
        f"burdened parcel {burdened_id!r}",
        object_id=solid["id"],
        extra={"burdened_id": burdened_id},
    )


def _secondary_not_contained_issue(
    solid: Solid,
    burdened_id: str,
    secondary_bbox: tuple[float, ...],
    burdened_bbox: tuple[float, ...],
) -> Issue:
    """Build a TR-20 issue for a secondary/easement solid outside its burdened parcel."""
    return err(
        "EASEMENT_NOT_CONTAINED_IN_BURDENED",
        f"Secondary/easement solid {solid['id']} is not fully contained "
        f"within its burdened parcel {burdened_id!r}",
        object_id=solid["id"],
        extra={
            "burdened_id": burdened_id,
            "easement_bbox": secondary_bbox,
            "servient_bbox": burdened_bbox,
        },
    )


def _secondary_solid_is_contained_in_burdened(
    secondary: Solid,
    burdened: Solid,
    surfaces: dict[str, Surface],
    curves: dict[str, Curve],
    points: dict[str, Point],
) -> tuple[bool, tuple[float, ...] | None, tuple[float, ...] | None]:
    """Return whether a secondary/easement solid bbox is contained by its burdened solid."""
    secondary_bbox = solid_bbox(secondary, surfaces, curves, points)
    burdened_bbox = solid_bbox(burdened, surfaces, curves, points)

    if secondary_bbox is None or burdened_bbox is None:
        return True, secondary_bbox, burdened_bbox

    return (
        bbox_contains(burdened_bbox, secondary_bbox),
        secondary_bbox,
        burdened_bbox,
    )


def validate_easement_containment(
    data: TopologyData,
) -> list[Issue]:
    """
    TR-20: every secondary/easement solid must be fully contained within its
    declared burdened parcel solid.

    A solid is checked when "parcel_type" is "easement" or "secondary". It must
    carry a "burdened_id" or legacy "servient_id" that references a known solid,
    and its bounding box must lie entirely within the burdened parcel's bounding box.
    """
    issues: list[Issue] = []
    topology_indexes = build_indexes(data)
    surfaces = topology_indexes["surfaces"]
    curves = topology_indexes["curves"]
    points = topology_indexes["points"]
    solids_by_id = topology_indexes["solids"]

    for solid in data.get("solids", []):
        if solid.get("parcel_type") not in SECONDARY_PARCEL_TYPES:
            continue

        burdened_id = _secondary_parcel_burdened_id(solid)
        if not burdened_id:
            issues.append(_missing_burdened_issue(solid))
            continue

        burdened = solids_by_id.get(burdened_id)
        if burdened is None:
            issues.append(_unknown_burdened_reference_issue(solid, burdened_id))
            continue

        is_contained, secondary_bbox, burdened_bbox = (
            _secondary_solid_is_contained_in_burdened(
                solid,
                burdened,
                surfaces,
                curves,
                points,
            )
        )
        if not is_contained and secondary_bbox is not None and burdened_bbox is not None:
            issues.append(
                _secondary_not_contained_issue(
                    solid,
                    burdened_id,
                    secondary_bbox,
                    burdened_bbox,
                )
            )

    return issues


# ---------------------------------------------------------------------------
# TR-21  Thematic host relationship
# ---------------------------------------------------------------------------


def _is_thematic_solid(solid: Solid) -> bool:
    """Return True when a solid is subject to TR-21 thematic host validation."""
    return solid.get("parcel_type") == THEMATIC_PARCEL_TYPE


def _solid_ids(data: TopologyData) -> set[str]:
    """Return all known solid ids in the topology dataset."""
    return {solid["id"] for solid in data.get("solids", [])}


def _missing_thematic_host_issue(solid_id: str) -> Issue:
    """Create a TR-21 issue for a thematic solid without a host id."""
    return err(
        THEMATIC_SOLID_MISSING_HOST_CODE,
        f"Thematic solid {solid_id} has no host_id",
        object_id=solid_id,
    )


def _unknown_thematic_host_issue(solid_id: str, host_id: str) -> Issue:
    """Create a TR-21 issue for a thematic solid referencing an unknown host."""
    return err(
        UNKNOWN_HOST_REFERENCE_CODE,
        f"Thematic solid {solid_id} references unknown host {host_id!r}",
        object_id=solid_id,
        extra={"host_id": host_id},
    )


def validate_thematic_host_relationship(
    data: TopologyData,
) -> list[Issue]:
    """
    TR-21: every thematic solid must reference a valid host parcel solid.

    A solid is thematic when "parcel_type" == "thematic". It must carry
    a "host_id" that resolves to a known solid in the same dataset.
    """
    issues: list[Issue] = []
    known_solid_ids = _solid_ids(data)

    for solid in data.get("solids", []):
        if not _is_thematic_solid(solid):
            continue

        solid_id = solid["id"]
        host_id = solid.get("host_id")

        if not host_id:
            issues.append(_missing_thematic_host_issue(solid_id))
            continue

        if host_id not in known_solid_ids:
            issues.append(_unknown_thematic_host_issue(solid_id, host_id))

    return issues


def validate(data: TopologyData, tolerances: Tolerances | None = None) -> list[Issue]:
    """Validate CC-07 containment and host topology rules.

    Args:
        data: Topology data to validate.
        tolerances: Optional tolerance overrides. Present for interface
            consistency; this validator does not currently use them.

    Returns:
        A list of validation issues found in `data`.
    """
    issues: list[Issue] = []
    issues.extend(validate_parent_containment(data))
    issues.extend(validate_easement_containment(data))
    issues.extend(validate_thematic_host_relationship(data))
    return issues
