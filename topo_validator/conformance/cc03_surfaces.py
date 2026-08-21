#!/usr/bin/env python3

"""Conformance checks for CC-03 surface topology.

This module validates surface-level topology rules for a `TopologyData` instance,
including closed rings, shared edges, self-intersection, duplicates, curve
consistency, and connected interior requirements.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..geometry import (
    curve_end_id,
    curve_start_id,
    point_coordinates,
    segments_intersect_3d,
)

from ..model import (
    Curve,
    Issue,
    Point,
    Ring,
    RingMember,
    Surface,
    Tolerances,
    TopologyData,
    build_indexes,
    err
)

CONFORMANCE_CLASS_ID = "CC-03"
CONFORMANCE_CLASS_NAME = "Surface topology"
RULE_IDS = ["TR-04", "TR-05", "TR-15", "TR-16", "TR-17", "TR-23"]

SURFACE_RING_NOT_CLOSED_CODE = "SURFACE_RING_NOT_CLOSED"
SHARED_EDGE_SAME_ORIENTATION_CODE = "SHARED_EDGE_SAME_ORIENTATION"
SURFACE_SELF_INTERSECTION_CODE = "SURFACE_SELF_INTERSECTION"
DUPLICATE_SURFACE_CODE = "DUPLICATE_SURFACE"
UNKNOWN_CURVE_REFERENCE_CODE = "UNKNOWN_CURVE_REFERENCE"
SURFACE_RING_REPEATED_VERTEX_CODE = "SURFACE_RING_REPEATED_VERTEX"
SurfaceCurveRefKey = frozenset[str]
Segment = tuple[str, str]
CurveUsage = tuple[str, str]
CurveUsageIndex = dict[str, list[CurveUsage]]


# ---------------------------------------------------------------------------
# TR-04  Surface closed rings
# ---------------------------------------------------------------------------


def _surface_ring_not_closed_issue(
    surface_id: str,
    ring_index: int,
    member_index: int,
    next_member_index: int,
    end_vertex_id: str,
    next_start_vertex_id: str,
) -> Issue:
    """Create a TR-04 issue for adjacent ring members that do not connect."""
    return err(
        SURFACE_RING_NOT_CLOSED_CODE,
        f"Surface {surface_id} ring {ring_index}: member {member_index} "
        f"ends at {end_vertex_id!r} but member {next_member_index} "
        f"starts at {next_start_vertex_id!r}",
        object_id=surface_id,
        extra={
            "ring_index": ring_index,
            "member_index": member_index,
            "end_point": end_vertex_id,
            "next_start": next_start_vertex_id,
        },
    )


def _ring_member_endpoints_match(
    current_member: RingMember,
    next_member: RingMember,
    curves: dict[str, Curve],
) -> tuple[bool, str | None, str | None]:
    """Return whether two adjacent directed ring members connect end-to-start."""
    current_curve = curves.get(current_member["ref"])
    next_curve = curves.get(next_member["ref"])

    if current_curve is None or next_curve is None:
        return True, None, None

    end_vertex_id = curve_end_id(current_curve, current_member["orientation"])
    next_start_vertex_id = curve_start_id(next_curve, next_member["orientation"])

    return (
        end_vertex_id == next_start_vertex_id,
        end_vertex_id,
        next_start_vertex_id,
    )


def validate_surface_closed_rings(
    data: TopologyData,
) -> list[Issue]:
    """
    TR-04: every ring in a surface must form a closed chain.
    The end-point of each member must equal the start-point of the next,
    and the ring must close back to its first start-point.
    """
    issues: list[Issue] = []
    curves = build_indexes(data)["curves"]

    for surface in data.get("surfaces", []):
        surface_id = surface["id"]

        for ring_index, ring in enumerate(surface.get("rings", [])):
            members = ring.get("members", [])
            if not members:
                continue

            member_count = len(members)
            for member_index, current_member in enumerate(members):
                next_member_index = (member_index + 1) % member_count
                next_member = members[next_member_index]

                endpoints_match, end_vertex_id, next_start_vertex_id = (
                    _ring_member_endpoints_match(
                        current_member,
                        next_member,
                        curves,
                    )
                )

                if endpoints_match:
                    continue

                if end_vertex_id is None or next_start_vertex_id is None:
                    continue

                issues.append(
                    _surface_ring_not_closed_issue(
                        surface_id,
                        ring_index,
                        member_index,
                        next_member_index,
                        end_vertex_id,
                        next_start_vertex_id,
                    )
                )
                break

    return issues


# ---------------------------------------------------------------------------
# TR-05  Shared surface edges consistency
# ---------------------------------------------------------------------------


def _curve_usage_by_surface(data: TopologyData) -> CurveUsageIndex:
    """Return curve usage entries grouped by curve id."""
    usage_by_curve_id: CurveUsageIndex = defaultdict(list)

    for surface in data.get("surfaces", []):
        surface_id = surface["id"]

        for ring in surface.get("rings", []):
            for member in ring.get("members", []):
                curve_id = member["ref"]
                orientation = member["orientation"]
                usage_by_curve_id[curve_id].append((surface_id, orientation))

    return usage_by_curve_id


def _has_same_orientation(usages: list[CurveUsage]) -> bool:
    """Return True when all usages have the same orientation."""
    orientations = {orientation for _, orientation in usages}
    return len(orientations) == 1


def _usage_extra(usages: list[CurveUsage]) -> dict[str, list[dict[str, str]]]:
    """Return machine-readable usage details for an issue."""
    return {
        "usages": [
            {"surface": surface_id, "orientation": orientation}
            for surface_id, orientation in usages
        ]
    }


def _shared_edge_same_orientation_issue(
    curve_id: str,
    usages: list[CurveUsage],
) -> Issue:
    """Create a TR-05 issue for a shared curve used with one orientation only."""
    first_orientation = usages[0][1]

    return err(
        SHARED_EDGE_SAME_ORIENTATION_CODE,
        f"Curve {curve_id} appears in {len(usages)} surfaces all "
        f"with orientation '{first_orientation}'; adjacent surfaces "
        f"must use opposite orientations",
        object_id=curve_id,
        extra=_usage_extra(usages),
    )


def validate_shared_surface_edges(
    data: TopologyData,
) -> list[Issue]:
    """
    TR-05: when a curve is used in two surfaces, it must appear with
    opposite orientations (one "+" and one "–").

    A curve appearing with the same orientation in two faces indicates
    that the outward-normal convention is broken on the shared edge.
    """
    issues: list[Issue] = []
    usage_by_curve_id = _curve_usage_by_surface(data)

    for curve_id, usages in usage_by_curve_id.items():
        if len(usages) < 2:
            continue

        if _has_same_orientation(usages):
            issues.append(_shared_edge_same_orientation_issue(curve_id, usages))

    return issues

# ---------------------------------------------------------------------------
# TR-15  No surface self-intersection
# ---------------------------------------------------------------------------


def _ring_segments(
    ring: Ring,
    curves: dict[str, Curve],
) -> list[Segment]:
    """Return all consecutive vertex-id segments referenced by a surface ring."""
    segments: list[Segment] = []

    for member in ring.get("members", []):
        curve = curves.get(member["ref"])
        if not isinstance(curve, dict):
            continue

        vertices = curve.get("vertices")
        if not isinstance(vertices, list):
            continue

        segments.extend(
            (vertices[index], vertices[index + 1])
            for index in range(len(vertices) - 1)
        )

    return segments


def _segments_are_adjacent_or_closing_pair(
    first_index: int,
    first_segment: Segment,
    second_index: int,
    second_segment: Segment,
    segment_count: int,
) -> bool:
    """Return True when two ring segments should not be tested for crossing."""
    if first_index == 0 and second_index == segment_count - 1:
        return True

    first_start, first_end = first_segment
    second_start, second_end = second_segment
    return (
        first_start in (second_start, second_end)
        or first_end in (second_start, second_end)
    )


def _segment_endpoint_coordinates(
    segment: Segment,
    points: dict[str, Point],
) -> tuple[list[Any], list[Any]] | None:
    """Return segment endpoint coordinates, or None when either endpoint is missing."""
    start_coordinates = point_coordinates(points, segment[0])
    end_coordinates = point_coordinates(points, segment[1])

    if start_coordinates is None or end_coordinates is None:
        return None

    return start_coordinates, end_coordinates


def _find_ring_self_intersection(
    segments: list[Segment],
    points: dict[str, Point],
) -> tuple[int, int] | None:
    """Return the first pair of non-adjacent ring segments that cross."""
    segment_count = len(segments)

    for first_index, first_segment in enumerate(segments):
        first_coordinates = _segment_endpoint_coordinates(first_segment, points)
        if first_coordinates is None:
            continue

        first_start_coordinates, first_end_coordinates = first_coordinates

        for second_index in range(first_index + 2, segment_count):
            second_segment = segments[second_index]

            if _segments_are_adjacent_or_closing_pair(
                first_index,
                first_segment,
                second_index,
                second_segment,
                segment_count,
            ):
                continue

            second_coordinates = _segment_endpoint_coordinates(second_segment, points)
            if second_coordinates is None:
                continue

            second_start_coordinates, second_end_coordinates = second_coordinates

            if segments_intersect_3d(
                first_start_coordinates,
                first_end_coordinates,
                second_start_coordinates,
                second_end_coordinates,
            ):
                return first_index, second_index

    return None


def _surface_self_intersection_issue(
    surface_id: str,
    ring_index: int,
    first_segment_index: int,
    second_segment_index: int,
) -> Issue:
    """Create a TR-15 issue for a self-intersecting surface ring."""
    return err(
        SURFACE_SELF_INTERSECTION_CODE,
        f"Surface {surface_id} ring {ring_index} "
        f"self-intersects at segments {first_segment_index} and {second_segment_index}",
        object_id=surface_id,
        extra={
            "ring_index": ring_index,
            "seg_a": first_segment_index,
            "seg_b": second_segment_index,
        },
    )


def validate_no_surface_self_intersection(
    data: TopologyData,
) -> list[Issue]:
    """
    TR-15: the edges within a surface ring must not cross each other.
    Non-adjacent segments of the same ring are checked for a proper 3D
    interior crossing.  A self-intersecting ring defines an invalid
    (bowtie) polygon.  Segments that are coplanar but share no endpoints
    and cross in their interiors are flagged.
    """
    issues: list[Issue] = []
    indexes = build_indexes(data)
    curves = indexes["curves"]
    points = indexes["points"]

    for surface in data.get("surfaces", []):
        surface_id = surface["id"]

        for ring_index, ring in enumerate(surface.get("rings", [])):
            segments = _ring_segments(ring, curves)
            intersection = _find_ring_self_intersection(segments, points)

            if intersection is None:
                continue

            first_segment_index, second_segment_index = intersection
            issues.append(
                _surface_self_intersection_issue(
                    surface_id,
                    ring_index,
                    first_segment_index,
                    second_segment_index,
                )
            )

    return issues


# ---------------------------------------------------------------------------
# TR-16  No duplicate surfaces
# ---------------------------------------------------------------------------


def _surface_curve_ref_key(surface: Surface) -> SurfaceCurveRefKey:
    """Return the orientation-independent set of curve ids used by a surface."""
    return frozenset(
        member["ref"]
        for ring in surface.get("rings", [])
        for member in ring.get("members", [])
    )


def _duplicate_surface_issue(
    surface_id: str,
    duplicate_of_surface_id: str,
) -> Issue:
    """Create a TR-16 issue for a surface that duplicates an earlier surface."""
    return err(
        DUPLICATE_SURFACE_CODE,
        f"Surface {surface_id} is a duplicate of surface {duplicate_of_surface_id!r}",
        object_id=surface_id,
        extra={"duplicate_of": duplicate_of_surface_id},
    )


def validate_no_duplicate_surfaces(
    data: TopologyData,
) -> list[Issue]:
    """
    TR-16: no two surfaces may reference the same set of curves.

    Two surfaces are considered duplicates when the *frozenset* of all curve
    ids referenced in their rings is identical, regardless of ring order,
    member order, or the orientation ("+" / "-") of each member.
    Reversed winding and/or reversed per-edge orientation do not make two
    faces distinct.
    """
    issues: list[Issue] = []
    surface_id_by_curve_refs: dict[SurfaceCurveRefKey, str] = {}

    for surface in data.get("surfaces", []):
        surface_id = surface["id"]
        curve_ref_key = _surface_curve_ref_key(surface)
        duplicate_of_surface_id = surface_id_by_curve_refs.get(curve_ref_key)

        if duplicate_of_surface_id is not None:
            issues.append(
                _duplicate_surface_issue(surface_id, duplicate_of_surface_id)
            )
        else:
            surface_id_by_curve_refs[curve_ref_key] = surface_id

    return issues


# ---------------------------------------------------------------------------
# TR-17  Surface-curve consistency
# ---------------------------------------------------------------------------


def _known_curve_ids(data: TopologyData) -> set[str]:
    """Return all curve ids present in the topology dataset."""
    return {curve["id"] for curve in data.get("curves", [])}


def _unknown_curve_reference_issue(
    surface_id: str,
    ring_index: int,
    curve_id: str,
) -> Issue:
    """Create a TR-17 issue for a surface ring member referencing an unknown curve."""
    return err(
        UNKNOWN_CURVE_REFERENCE_CODE,
        f"Surface {surface_id} ring {ring_index} references unknown curve {curve_id!r}",
        object_id=surface_id,
        extra={"curve_id": curve_id, "ring_index": ring_index},
    )


def validate_surface_curve_consistency(
    data: TopologyData,
) -> list[Issue]:
    """
    TR-17: every curve id referenced in a surface ring must exist in the
    curves' collection.

    A broken reference between the surface layer and the curve layer
    indicates an incomplete or corrupt topology dataset.
    """
    issues: list[Issue] = []
    known_curves = _known_curve_ids(data)

    for surface in data.get("surfaces", []):
        surface_id = surface["id"]

        for ring_index, ring in enumerate(surface.get("rings", [])):
            for member in ring.get("members", []):
                curve_id = member.get("ref", "")
                if curve_id not in known_curves:
                    issues.append(
                        _unknown_curve_reference_issue(
                            surface_id,
                            ring_index,
                            curve_id,
                        )
                    )

    return issues


# ---------------------------------------------------------------------------
# TR-23  Connected interior
# ---------------------------------------------------------------------------


def _surface_ring_repeated_vertex_issue(
    surface_id: str,
    ring_index: int,
    vertex_id: str,
    first_position: int,
    repeated_position: int,
) -> Issue:
    """Build a TR-23 issue for a repeated directed start vertex in one ring."""
    return err(
        SURFACE_RING_REPEATED_VERTEX_CODE,
        f"Surface {surface_id} ring {ring_index}: vertex "
        f"{vertex_id!r} is the start-point of members "
        f"{first_position} and {repeated_position}; the ring visits "
        f"this vertex twice, creating a pinch point that "
        f"disconnects the surface interior",
        object_id=surface_id,
        extra={
            "ring_index": ring_index,
            "vertex_id": vertex_id,
            "first_position": first_position,
            "repeated_position": repeated_position,
        },
    )


def _validate_ring_connected_interior(
    surface_id: str,
    ring_index: int,
    ring: Ring,
    curves: dict[str, Curve],
) -> list[Issue]:
    """Return TR-23 issues for repeated directed start vertices in one ring."""
    issues: list[Issue] = []
    first_position_by_vertex: dict[str, int] = {}
    members = ring.get("members", [])

    for member_index, member in enumerate(members):
        curve = curves.get(member["ref"])
        if curve is None:
            continue

        start_vertex_id = curve_start_id(curve, member["orientation"])
        first_position = first_position_by_vertex.get(start_vertex_id)

        if first_position is None:
            first_position_by_vertex[start_vertex_id] = member_index
            continue

        issues.append(
            _surface_ring_repeated_vertex_issue(
                surface_id,
                ring_index,
                start_vertex_id,
                first_position,
                member_index,
            )
        )

    return issues


def validate_surface_connected_interior(
    data: TopologyData,
) -> list[Issue]:
    """
    TR-23: within each surface ring, no vertex may appear as the directed
    start-point of more than one member.
    A vertex that is the start-point of two or more members in the same ring
    means the ring visits that vertex twice, forming a pinch point.  At a
    pinch point the ring's interior splits into two (or more) disconnected
    pieces — the surface no longer has a simply-connected interior.
    Example (figure-8 ring):
        e01+ e12+ e20+ e03+ e34+ e40+
    Start-points: p0  p1  p2  p0  p3  p4
    p0 appears at positions 0 and 3 → SURFACE_RING_REPEATED_VERTEX on sf.
    Distinct from:
      TR-04 (SurfaceClosedRing) – checks consecutive connectivity,
                                      not vertex uniqueness within a ring.
      TR-15 (NoSurfaceSelfIntersection) – detects segment crossings in
                                      interiors; does not detect rings
                                      that touch at a shared vertex.
      TR-22 (CurveOrientation) – detects a curve repeated in a ring;
                                      different curves can share a vertex,
                                      which this rule catches instead.
    """
    issues: list[Issue] = []
    curves = build_indexes(data)["curves"]

    for surface in data.get("surfaces", []):
        surface_id = surface["id"]
        rings = surface.get("rings", [])

        for ring_index, ring in enumerate(rings):
            issues.extend(
                _validate_ring_connected_interior(
                    surface_id,
                    ring_index,
                    ring,
                    curves,
                )
            )

    return issues


def validate(data: TopologyData, tolerances: Tolerances | None = None) -> list[Issue]:
    """Validate CC-03 surface topology rules.

    Args:
        data: Topology data to validate.
        tolerances: Optional tolerance overrides. Present for interface
            consistency; this validator does not currently use them.

    Returns:
        A list of validation issues found in `data`.
    """
    issues: list[Issue] = []
    issues.extend(validate_surface_closed_rings(data))
    issues.extend(validate_shared_surface_edges(data))
    issues.extend(validate_no_surface_self_intersection(data))
    issues.extend(validate_no_duplicate_surfaces(data))
    issues.extend(validate_surface_curve_consistency(data))
    issues.extend(validate_surface_connected_interior(data))
    return issues
