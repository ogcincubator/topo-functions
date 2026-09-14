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
    Relationship,
    Solid,
    Surface,
    TOLERANCE_GEOMETRY,
    Tolerances,
    TopologyData,
    build_indexes,
    err,
    warn,
)
from ..parcel_geometry import parcel_ring_2d, point_in_2d_ring, solid_footprint_2d
from ..solid_geometry import (
    boundary_escapes_solid,
    face_edges,
    first_decisive_containment,
    solid_face_geometries,
)

CONFORMANCE_CLASS_ID = "CC-07"
CONFORMANCE_CLASS_NAME = "Containment and host topology"
# TR-28/TR-29 are declared here (not run via this module's own `validate()`)
# because they need both the 3D and 2D topology views at once -- see
# `validate_declared_parcel_containment`/`validate_declared_easement_burden`.
RULE_IDS = ["TR-09", "TR-20", "TR-21", "TR-28", "TR-29"]

SECONDARY_PARCEL_TYPES = {"easement", "secondary"}
THEMATIC_PARCEL_TYPE = "thematic"
THEMATIC_SOLID_MISSING_HOST_CODE = "THEMATIC_SOLID_MISSING_HOST"
UNKNOWN_HOST_REFERENCE_CODE = "UNKNOWN_HOST_REFERENCE"

# TR-28 / TR-29: declared parcel-relationship containment
PARCEL_LIKE_TYPES = {"primary", "secondary"}
PRIMARY_PARCEL_FEATURE_TYPE = "PrimaryParcel"
CONTAINING_PRIMARY_PARCEL_ROLE = "containingPrimaryParcel"
BURDENED_BY_SECONDARY_PARCEL_ROLE = "burdenedBySecondaryParcel"

MISSING_PARCEL_CONTAINMENT_RELATIONSHIP_CODE = "MISSING_PARCEL_CONTAINMENT_RELATIONSHIP"
MULTIPLE_PARCEL_CONTAINMENT_RELATIONSHIPS_CODE = "MULTIPLE_PARCEL_CONTAINMENT_RELATIONSHIPS"
SOLID_NOT_WITHIN_DECLARED_PARCEL_CODE = "SOLID_NOT_WITHIN_DECLARED_PARCEL"

MISSING_EASEMENT_BURDEN_RELATIONSHIP_CODE = "MISSING_EASEMENT_BURDEN_RELATIONSHIP"
MULTIPLE_EASEMENT_BURDEN_RELATIONSHIPS_CODE = "MULTIPLE_EASEMENT_BURDEN_RELATIONSHIPS"
SOLID_NOT_WITHIN_BURDENED_PARCEL_CODE = "SOLID_NOT_WITHIN_BURDENED_PARCEL"

UNKNOWN_PARCEL_REFERENCE_CODE = "UNKNOWN_PARCEL_REFERENCE"
PARCEL_TYPE_MISMATCH_CODE = "PARCEL_TYPE_MISMATCH"
UNKNOWN_BURDENED_PARCEL_REFERENCE_CODE = "UNKNOWN_BURDENED_PARCEL_REFERENCE"
BURDENED_PARCEL_TYPE_MISMATCH_CODE = "BURDENED_PARCEL_TYPE_MISMATCH"


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
    reason: str,
    child_bbox: tuple[float, ...] | None,
    parent_bbox: tuple[float, ...] | None,
) -> Issue:
    """Build a TR-09 issue for a child solid that escapes its parent."""
    solid_id = solid["id"]

    return err(
        "CHILD_NOT_CONTAINED_IN_PARENT",
        f"Solid {solid_id} is not fully contained within "
        f"parent {parent_id!r} ({reason})",
        object_id=solid_id,
        extra={
            "parent_id": parent_id,
            "reason": reason,
            "child_bbox": child_bbox,
            "parent_bbox": parent_bbox,
        },
    )


def _exact_containment_failure_reason(
    solid: Solid,
    parent: Solid,
    surfaces: dict[str, Surface],
    curves: dict[str, Curve],
    points: dict[str, Point],
) -> str | None:
    """Return why *solid* is not contained in *parent*, or None when it is.

    Reached only once the bounding boxes agree that containment is possible, so
    this is the test that decides it.
    """
    child_faces = solid_face_geometries(solid, surfaces, curves, points)
    parent_faces = solid_face_geometries(parent, surfaces, curves, points)

    if not child_faces or not parent_faces:
        return None  # no usable geometry — other rules report that

    child_edges = face_edges(child_faces)

    if boundary_escapes_solid(
        child_faces, child_edges, parent_faces, TOLERANCE_GEOMETRY
    ):
        return "part of the child boundary lies outside the parent"

    decisive = first_decisive_containment(
        child_faces, parent_faces, TOLERANCE_GEOMETRY
    )
    if decisive is False:
        return "child lies outside the parent"

    return None


def _containment_failure_reason(
    solid: Solid,
    parent: Solid,
    child_bbox: tuple[float, ...] | None,
    parent_bbox: tuple[float, ...] | None,
    surfaces: dict[str, Surface],
    curves: dict[str, Curve],
    points: dict[str, Point],
) -> str | None:
    """Return why *solid* is not contained in *parent*, or None when it is."""
    if child_bbox and parent_bbox and not bbox_contains(parent_bbox, child_bbox):
        # Conclusive: part of the child is outside the parent's own extent.
        return "child extends beyond the parent bounding box"

    return _exact_containment_failure_reason(
        solid, parent, surfaces, curves, points
    )


def validate_parent_containment(
    data: TopologyData,
) -> list[Issue]:
    """
    TR-09: a child parcel solid must lie entirely within its parent solid.

    Containment is tested exactly, not by bounding box.  An AABB test is only
    a necessary condition and fails in the dangerous direction: a child that
    protrudes through a concave parent — out of the notch of an L-shaped
    parent, say — still sits inside the parent's bounding box and would pass
    unnoticed.  A false negative on containment is worse than a false positive,
    because it silently certifies an invalid subdivision.

    A child fails containment when either holds:

    1. Part of its boundary lies strictly outside the parent — a vertex or a
       stretch of edge that has escaped, whether by protruding through a face
       or by sitting outside a concave parent entirely.
    2. No part of its boundary is outside, but the child is not on the inside
       either: it is a wholly separate solid.  One decisive vertex settles this.

    The bounding box is still used first, as a cheap conclusive reject: a child
    whose box escapes the parent's box cannot possibly be contained.

    Solids that share boundary faces with their parent — a unit whose wall is
    also the building envelope — are handled correctly: points on the shared
    boundary are indeterminate rather than outside, so a decisive point is
    sought instead of guessing.
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

        child_bbox = solid_bbox(solid, surfaces, curves, points)
        parent_bbox = solid_bbox(parent, surfaces, curves, points)

        reason = _containment_failure_reason(
            solid,
            parent,
            child_bbox,
            parent_bbox,
            surfaces,
            curves,
            points,
        )
        if reason is not None:
            issues.append(
                _child_not_contained_issue(
                    solid,
                    parent_id,
                    reason,
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


# ---------------------------------------------------------------------------
# TR-28 / TR-29  Declared parcel-relationship containment
# ---------------------------------------------------------------------------
#
# Implements the "for-discussion/parent-parcel-relationships" proposal: a
# solid declares (on its own `topology.relationships`, never the reverse) a
# `rel: "topology"` relationship to the Primary Parcel that physically
# contains it (TR-28, `role: "containingPrimaryParcel"`) or that it burdens
# as an easement/secondary interest (TR-29, `role:
# "burdenedBySecondaryParcel"`). The declared relationship is checked for
# cardinality and referential integrity, then cross-checked geometrically
# against the parcel treated as an unlimited vertical prism.
#
# Both rules are conditionally scoped, unlike every other rule in this
# package: they only activate for a dataset that actually declares at least
# one PrimaryParcel surface (via a `parcels` collection -- see
# `loader._build_parcel_surfaces`), and only for solids whose `parcel_type`
# marks them as representing cadastral parcel/spatial-unit geometry, not
# every solid. A dataset with no parcel content at all -- the common case for
# plain geometry fixtures -- has nothing for this rule to check and produces
# no findings from it, rather than every solid being flagged for lacking a
# relationship it was never meant to declare.
#
# Deliberately not run via this module's own `validate()`: every other rule
# here sees one `TopologyData` view. These need both the 3D view (the solid
# being checked) and the 2D view (the parcel surface it references) at once,
# since a parcel surface's points are natively 2D and therefore always land
# in the 2D view (see `dimensionality.partition_topology`) -- so
# `validator.validate_topology` calls these two directly, passing both views.


def _relationships_by_role(solid: Solid, role: str) -> list[Relationship]:
    """Return a solid's declared relationships matching *role*."""
    return [
        relationship
        for relationship in solid.get("relationships") or []
        if relationship.get("role") == role
    ]


def _declared_relationship(
    solid: Solid,
    role: str,
    missing_code: str,
    multiple_code: str,
) -> tuple[Relationship | None, list[Issue]]:
    """Return a solid's single relationship for *role*, and any cardinality issues.

    Missing is a warning: many fixtures exercise 3D topology with no
    cadastral parcel context at all, and this rule checks consistency when a
    relationship *should* exist, rather than mandating every solid declare
    one. Declaring more than one is a genuine authoring contradiction, not
    an absence, so it is an error.
    """
    solid_id = solid["id"]
    matches = _relationships_by_role(solid, role)

    if not matches:
        return None, [
            warn(
                missing_code,
                f"Solid {solid_id} has no declared {role!r} relationship",
                object_id=solid_id,
            )
        ]

    if len(matches) > 1:
        return None, [
            err(
                multiple_code,
                f"Solid {solid_id} declares {len(matches)} {role!r} "
                f"relationships; exactly one is required",
                object_id=solid_id,
                extra={"role": role, "count": len(matches)},
            )
        ]

    return matches[0], []


def _parcel_surfaces_by_id(
    topology_2d: TopologyData, expected_feature_type: str
) -> dict[str, Surface]:
    """Return the 2D view's surfaces of *expected_feature_type*, keyed by id."""
    return {
        surface["id"]: surface
        for surface in topology_2d.get("surfaces", [])
        if surface.get("feature_type") == expected_feature_type
    }


def _resolve_and_check_parcel_relationship(
    solid: Solid,
    relationship: Relationship,
    parcel_surfaces: dict[str, Surface],
    topology_3d_indexes: dict,
    topology_2d_indexes: dict,
    unknown_code: str,
    type_mismatch_code: str,
    not_within_code: str,
) -> Issue | None:
    """Resolve a declared relationship's target and check geometric containment.

    Returns an issue for an unknown target, a target-type mismatch, or a
    footprint that escapes the parcel; None when the relationship checks out
    or there isn't enough geometry to test (other rules report that).
    """
    solid_id = solid["id"]
    href = relationship["href"]

    parcel = parcel_surfaces.get(href)
    if parcel is None:
        return err(
            unknown_code,
            f"Solid {solid_id} references unknown parcel {href!r}",
            object_id=solid_id,
            extra={"href": href},
        )

    declared_type = relationship["targetFeatureType"]
    actual_type = parcel.get("feature_type")
    if declared_type != actual_type:
        return err(
            type_mismatch_code,
            f"Solid {solid_id}'s declared relationship to {href!r} expects "
            f"targetFeatureType {declared_type!r}, but that feature's actual "
            f"type is {actual_type!r}",
            object_id=solid_id,
            extra={"href": href, "declared_type": declared_type, "actual_type": actual_type},
        )

    footprint = solid_footprint_2d(
        solid,
        topology_3d_indexes["surfaces"],
        topology_3d_indexes["curves"],
        topology_3d_indexes["points"],
    )
    ring = parcel_ring_2d(
        parcel,
        topology_2d_indexes["curves"],
        topology_2d_indexes["points"],
    )

    if not footprint or ring is None:
        return None

    if any(not point_in_2d_ring(x, y, ring) for x, y in footprint):
        return err(
            not_within_code,
            f"Solid {solid_id} is not fully within its declared parcel {href!r} "
            f"(treated as an unlimited vertical prism)",
            object_id=solid_id,
            extra={"href": href},
        )

    return None


def _validate_declared_parcel_relationship(
    topology_3d: TopologyData,
    topology_2d: TopologyData,
    parcel_like_types: set[str],
    target_feature_type: str,
    role: str,
    missing_code: str,
    multiple_code: str,
    unknown_code: str,
    type_mismatch_code: str,
    not_within_code: str,
) -> list[Issue]:
    """Shared implementation for TR-28 and TR-29.

    Args:
        topology_3d: The 3D topology view -- solids are checked from here.
        topology_2d: The 2D topology view -- declared parcel targets resolve
            against its surfaces, since a parcel surface's points are
            natively 2D and always land here.
        parcel_like_types: `parcel_type` values this rule applies to.
        target_feature_type: the 2D `feature_type` a declared relationship's
            target must resolve against (`PRIMARY_PARCEL_FEATURE_TYPE` for
            both TR-28 and TR-29 today). Kept as an explicit parameter,
            rather than assumed inside `_parcel_surfaces_by_id`, so this
            shared engine can be reused by a future rule that resolves
            declared relationships against a different target feature type,
            without duplicating the cardinality/lookup/geometric-containment
            logic below.
        role: The declared relationship role this rule requires exactly one
            of.
        missing_code: Issue code for a missing declaration.
        multiple_code: Issue code for more than one declaration.
        unknown_code: Issue code for a declared `href` that resolves to no
            known surface of `target_feature_type`.
        type_mismatch_code: Issue code for a declared `targetFeatureType`
            that doesn't match the resolved target's actual type.
        not_within_code: Issue code for a footprint outside the target.

    `unknown_code`/`type_mismatch_code` are passed in rather than shared
    module constants because a solid with `parcel_type == "secondary"` is
    legitimately checked by both TR-28 and TR-29 (`PARCEL_LIKE_TYPES` and
    `SECONDARY_PARCEL_TYPES` both include "secondary") -- a code shared
    between the two rules would make both rows in a report's rule-results
    table show FAIL whenever only one rule's check actually fired.
    """
    parcel_surfaces = _parcel_surfaces_by_id(topology_2d, target_feature_type)
    if not parcel_surfaces:
        return []

    topology_3d_indexes = build_indexes(topology_3d)
    topology_2d_indexes = build_indexes(topology_2d)

    issues: list[Issue] = []

    for solid in topology_3d.get("solids", []):
        if solid.get("parcel_type") not in parcel_like_types:
            continue

        relationship, cardinality_issues = _declared_relationship(
            solid, role, missing_code, multiple_code
        )
        issues.extend(cardinality_issues)

        if relationship is None:
            continue

        issue = _resolve_and_check_parcel_relationship(
            solid,
            relationship,
            parcel_surfaces,
            topology_3d_indexes,
            topology_2d_indexes,
            unknown_code,
            type_mismatch_code,
            not_within_code,
        )
        if issue is not None:
            issues.append(issue)

    return issues


def validate_declared_parcel_containment(
    topology_3d: TopologyData,
    topology_2d: TopologyData,
) -> list[Issue]:
    """
    TR-28: a cadastral parcel/spatial-unit solid must declare exactly one
    `containingPrimaryParcel` relationship to the Primary Parcel that
    physically contains it, and its footprint must lie within that parcel
    (treated as an unlimited vertical prism).

    Applies to solids whose `parcel_type` is "primary" or "secondary", and
    only when the dataset declares at least one PrimaryParcel surface -- see
    the module-level note above for why this rule is conditionally scoped.
    """
    return _validate_declared_parcel_relationship(
        topology_3d,
        topology_2d,
        PARCEL_LIKE_TYPES,
        PRIMARY_PARCEL_FEATURE_TYPE,
        CONTAINING_PRIMARY_PARCEL_ROLE,
        MISSING_PARCEL_CONTAINMENT_RELATIONSHIP_CODE,
        MULTIPLE_PARCEL_CONTAINMENT_RELATIONSHIPS_CODE,
        UNKNOWN_PARCEL_REFERENCE_CODE,
        PARCEL_TYPE_MISMATCH_CODE,
        SOLID_NOT_WITHIN_DECLARED_PARCEL_CODE,
    )


def validate_declared_easement_burden(
    topology_3d: TopologyData,
    topology_2d: TopologyData,
) -> list[Issue]:
    """
    TR-29: a secondary/easement solid must declare exactly one
    `burdenedBySecondaryParcel` relationship to the Primary Parcel it
    burdens, and its footprint must lie within that parcel (treated as an
    unlimited vertical prism).

    Applies to solids whose `parcel_type` is "easement" or "secondary" (the
    same `SECONDARY_PARCEL_TYPES` set TR-20 uses), and only when the dataset
    declares at least one PrimaryParcel surface. Fully additive to TR-20's
    existing `burdened_id`/`servient_id` (solid-to-solid) check.
    """
    return _validate_declared_parcel_relationship(
        topology_3d,
        topology_2d,
        SECONDARY_PARCEL_TYPES,
        PRIMARY_PARCEL_FEATURE_TYPE,
        BURDENED_BY_SECONDARY_PARCEL_ROLE,
        MISSING_EASEMENT_BURDEN_RELATIONSHIP_CODE,
        MULTIPLE_EASEMENT_BURDEN_RELATIONSHIPS_CODE,
        UNKNOWN_BURDENED_PARCEL_REFERENCE_CODE,
        BURDENED_PARCEL_TYPE_MISMATCH_CODE,
        SOLID_NOT_WITHIN_BURDENED_PARCEL_CODE,
    )


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
