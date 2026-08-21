#!/usr/bin/env python3

"""Conformance checks for CC-02 curve topology.

This module validates curve-level topology rules for a `TopologyData` instance,
including self-intersection, dangling curves, minimum length, duplicates,
intersection-at-nodes constraints, and orientation.
"""

from __future__ import annotations

from ..geometry import (
    euclidean_dist,
    segments_intersect_3d,
)
from ..model import (
    Curve,
    Issue,
    Point,
    Tolerances,
    TopologyData,
    build_indexes,
    err,
)

CONFORMANCE_CLASS_ID = "CC-02"
CONFORMANCE_CLASS_NAME = "Curve topology"
RULE_IDS = ["TR-02", "TR-03", "TR-12", "TR-13", "TR-14", "TR-22"]

CURVE_SELF_INTERSECTION_CODE = "CURVE_SELF_INTERSECTION"
SegmentIntersection = tuple[int, int]
DANGLING_CURVE_CODE = "DANGLING_CURVE"
CURVE_BELOW_MINIMUM_LENGTH_CODE = "CURVE_BELOW_MINIMUM_LENGTH"
DUPLICATE_CURVE_CODE = "DUPLICATE_CURVE"
CurveGeometryKey = tuple[str, ...]
Segment = tuple[str, str]
CURVE_REPEATED_IN_RING_CODE = "CURVE_REPEATED_IN_RING"

# ---------------------------------------------------------------------------
# TR-02  Curve no self-intersection
# ---------------------------------------------------------------------------


def _find_curve_self_intersection(
    coordinates: list[list[float]],
) -> SegmentIntersection | None:
    """Return the first pair of non-adjacent curve segments that intersect."""
    for first_segment_start in range(len(coordinates) - 1):
        for second_segment_start in range(first_segment_start + 2, len(coordinates) - 1):
            if segments_intersect_3d(
                coordinates[first_segment_start],
                coordinates[first_segment_start + 1],
                coordinates[second_segment_start],
                coordinates[second_segment_start + 1],
            ):
                return first_segment_start, second_segment_start

    return None


def _curve_self_intersection_issue(
    curve_id: str,
    first_segment_start: int,
    second_segment_start: int,
) -> Issue:
    """Create a TR-02 issue for a curve self-intersection."""
    return err(
        CURVE_SELF_INTERSECTION_CODE,
        f"Curve {curve_id} self-intersects between "
        f"segments {first_segment_start}–{first_segment_start + 1} and "
        f"{second_segment_start}–{second_segment_start + 1}",
        object_id=curve_id,
        extra={
            "seg_a": [first_segment_start, first_segment_start + 1],
            "seg_b": [second_segment_start, second_segment_start + 1],
        },
    )


def validate_curve_no_self_intersection(
    data: TopologyData,
) -> list[Issue]:
    """
    TR-02: curves must not self-intersect except at their endpoints.

    Only curves with four or more vertices (three or more segments) can
    self-intersect. Non-adjacent segment pairs are checked for a proper
    3D interior crossing using "segments_intersect_3d".
    """
    issues: list[Issue] = []
    points = build_indexes(data)["points"]

    for curve in data.get("curves", []):
        curve_id = curve["id"]
        vertex_ids = curve["vertices"]

        if len(vertex_ids) < 4:
            continue

        coordinates = [
            points[vertex_id]["coordinates"]
            for vertex_id in vertex_ids
            if vertex_id in points
        ]
        intersection = _find_curve_self_intersection(coordinates)

        if intersection is None:
            continue

        first_segment_start, second_segment_start = intersection
        issues.append(
            _curve_self_intersection_issue(
                curve_id,
                first_segment_start,
                second_segment_start,
            )
        )

    return issues


# ---------------------------------------------------------------------------
# TR-03  No dangling curves
# ---------------------------------------------------------------------------


def _referenced_curve_ids(data: TopologyData) -> set[str]:
    """Return curve ids referenced by any surface ring member."""
    return {
        member["ref"]
        for surface in data.get("surfaces", [])
        for ring in surface.get("rings", [])
        for member in ring.get("members", [])
    }


def _observation_curve_ids(data: TopologyData) -> set[str]:
    """Return curve ids that are exempt because they belong to observations."""
    return {
        observation_curve["ref"]
        for observation_curve in data.get("observation_curves", [])
    }


def _dangling_curve_issue(curve_id: str) -> Issue:
    """Create a TR-03 issue for an unreferenced, non-exempt curve."""
    return err(
        DANGLING_CURVE_CODE,
        f"Curve {curve_id} is not referenced by any face",
        object_id=curve_id,
    )


def validate_no_dangling_curves(
    data: TopologyData,
) -> list[Issue]:
    """TR-03: every curve must be referenced by at least one face ring.

    Curves referenced exclusively by "vectorObservations" or
    "observedVectors" are exempt; they are recorded in the optional
    "data['observation_curves']" list and skipped by this check.
    """
    issues: list[Issue] = []
    referenced_curve_ids = _referenced_curve_ids(data)
    exempt_curve_ids = _observation_curve_ids(data)

    for curve in data.get("curves", []):
        curve_id = curve["id"]
        if curve_id not in referenced_curve_ids and curve_id not in exempt_curve_ids:
            issues.append(_dangling_curve_issue(curve_id))

    return issues


# ---------------------------------------------------------------------------
# TR-12  Minimum curve length
# ---------------------------------------------------------------------------


def _curve_length(curve: Curve, points: dict[str, Point]) -> float:
    """Return the total arc length of a curve using known point references."""
    vertex_ids = curve.get("vertices", [])
    length = 0.0

    for start_vertex_id, end_vertex_id in zip(vertex_ids, vertex_ids[1:]):
        start_point = points.get(start_vertex_id)
        end_point = points.get(end_vertex_id)

        if not isinstance(start_point, dict) or not isinstance(end_point, dict):
            continue

        length += euclidean_dist(
            start_point["coordinates"],
            end_point["coordinates"],
        )

    return length


def _minimum_curve_length_issue(
    curve_id: str,
    length: float,
    min_length: float,
) -> Issue:
    """Create a TR-12 issue for a curve shorter than the configured minimum."""
    return err(
        CURVE_BELOW_MINIMUM_LENGTH_CODE,
        f"Curve {curve_id} has length {length:.3e} "
        f"< minimum {min_length:.3e}",
        object_id=curve_id,
        extra={"length": length, "min_length": min_length},
    )


def validate_minimum_curve_length(
    data: TopologyData,
    min_length: float = Tolerances.length,
) -> list[Issue]:
    """
    TR-12: the total arc length of each curve must exceed *min_length*.

    Curves that are shorter than the snapping tolerance produce degenerate
    topology and must be flagged as potential slivers or duplicates.
    """
    issues: list[Issue] = []
    indexes = build_indexes(data)
    points = indexes["points"]

    for curve in data.get("curves", []):
        curve_id = curve["id"]
        length = _curve_length(curve, points)

        if length < min_length:
            issues.append(
                _minimum_curve_length_issue(
                    curve_id,
                    length,
                    min_length,
                )
            )

    return issues


# ---------------------------------------------------------------------------
# TR-13  No duplicate curves
# ---------------------------------------------------------------------------


def _curve_geometry_key(vertices: list[str]) -> CurveGeometryKey:
    """Return a direction-independent key for a curve vertex sequence."""
    vertex_sequence = tuple(vertices)
    reversed_sequence = vertex_sequence[::-1]
    return min(vertex_sequence, reversed_sequence)


def _duplicate_curve_issue(
    curve_id: str,
    duplicate_of_curve_id: str,
) -> Issue:
    """Create a TR-13 issue for a curve duplicating another curve's geometry."""
    return err(
        DUPLICATE_CURVE_CODE,
        f"Curve {curve_id} is a duplicate of curve {duplicate_of_curve_id!r}",
        object_id=curve_id,
        extra={"duplicate_of": duplicate_of_curve_id},
    )


def validate_no_duplicate_curves(
    data: TopologyData,
) -> list[Issue]:
    """
    TR-13: no two curves may connect the same pair (or sequence) of vertices.

    Two curves are considered duplicates when their vertex lists are identical
    or are exact reverses of each other (same geometry, opposite traversal).
    """
    issues: list[Issue] = []
    first_curve_id_by_geometry: dict[CurveGeometryKey, str] = {}

    for curve in data.get("curves", []):
        curve_id = curve["id"]
        geometry_key = _curve_geometry_key(curve.get("vertices", []))
        duplicate_of_curve_id = first_curve_id_by_geometry.get(geometry_key)

        if duplicate_of_curve_id is not None:
            issues.append(_duplicate_curve_issue(curve_id, duplicate_of_curve_id))
        else:
            first_curve_id_by_geometry[geometry_key] = curve_id

    return issues


# ---------------------------------------------------------------------------
# TR-14  Curve intersection at nodes only
# ---------------------------------------------------------------------------


def _curve_segments(vertex_ids: list[str]) -> list[Segment]:
    """Return consecutive vertex pairs for a curve."""
    return [
        (vertex_ids[index], vertex_ids[index + 1])
        for index in range(len(vertex_ids) - 1)
    ]


def _segment_coordinates(
    segment: Segment,
    points: dict[str, Point],
) -> tuple[list[float], list[float]] | None:
    """Return endpoint coordinates for a segment, or None if unavailable."""
    start_point = points.get(segment[0])
    end_point = points.get(segment[1])

    if not isinstance(start_point, dict) or not isinstance(end_point, dict):
        return None

    start_coordinates = start_point.get("coordinates")
    end_coordinates = end_point.get("coordinates")

    if not isinstance(start_coordinates, list):
        return None

    if not isinstance(end_coordinates, list):
        return None

    return start_coordinates, end_coordinates


def _segments_share_vertex(
    first_segment: Segment,
    second_segment: Segment,
) -> bool:
    """Return True when two segments share an endpoint vertex."""
    return bool(set(first_segment) & set(second_segment))


def _curves_have_interior_intersection(
    first_segments: list[Segment],
    second_segments: list[Segment],
    points: dict[str, Point],
) -> bool:
    """Return True when two curves cross away from shared endpoint nodes."""
    for first_segment in first_segments:
        first_coordinates = _segment_coordinates(first_segment, points)
        if first_coordinates is None:
            continue

        first_start_coordinates, first_end_coordinates = first_coordinates

        for second_segment in second_segments:
            # Segments sharing a vertex are allowed to meet there.
            if _segments_share_vertex(first_segment, second_segment):
                continue

            second_coordinates = _segment_coordinates(second_segment, points)
            if second_coordinates is None:
                continue

            second_start_coordinates, second_end_coordinates = second_coordinates

            if segments_intersect_3d(
                first_start_coordinates,
                first_end_coordinates,
                second_start_coordinates,
                second_end_coordinates,
            ):
                return True

    return False


def _curve_intersection_not_at_node_issue(
    first_curve_id: str,
    second_curve_id: str,
) -> Issue:
    """Build a TR-14 issue for two curves crossing away from a shared node."""
    return err(
        "CURVE_INTERSECTION_NOT_AT_NODE",
        f"Curves {first_curve_id} and {second_curve_id} cross "
        f"at a non-node interior point",
        object_id=first_curve_id,
        extra={"other_id": second_curve_id},
    )


def validate_curve_intersection_at_nodes_only(
    data: TopologyData,
) -> list[Issue]:
    """
    TR-14: two distinct curves may only meet at shared cadastral point nodes.
    A crossing between the *interiors* of two curve segments (not at a shared
    vertex endpoint) is flagged. Skew segments at different elevations are
    correctly identified as non-intersecting by the 3D coplanarity test.
    """
    issues: list[Issue] = []
    points = build_indexes(data)["points"]
    curves = data.get("curves", [])

    for first_index, first_curve in enumerate(curves):
        first_curve_id = first_curve["id"]
        first_segments = _curve_segments(first_curve.get("vertices", []))

        for second_curve in curves[first_index + 1:]:
            second_curve_id = second_curve["id"]
            second_segments = _curve_segments(second_curve.get("vertices", []))

            if not _curves_have_interior_intersection(
                first_segments,
                second_segments,
                points,
            ):
                continue

            issues.append(
                _curve_intersection_not_at_node_issue(
                    first_curve_id,
                    second_curve_id,
                )
            )

    return issues

# ---------------------------------------------------------------------------
# TR-22  No Repeated Curves in Ring
# ---------------------------------------------------------------------------


def _curve_repeated_in_ring_issue(
    surface_id: str,
    ring_index: int,
    curve_id: str,
    first_member_index: int,
    repeated_member_index: int,
) -> Issue:
    """Create a TR-22 issue for a curve repeated within one surface ring."""
    return err(
        CURVE_REPEATED_IN_RING_CODE,
        f"Surface {surface_id} ring {ring_index}: curve {curve_id!r} "
        f"appears at positions {first_member_index} and {repeated_member_index}; "
        f"a curve must not be referenced more than once in the same ring",
        object_id=surface_id,
        extra={
            "ring_index": ring_index,
            "curve_id": curve_id,
            "first_position": first_member_index,
            "repeated_position": repeated_member_index,
        },
    )


def validate_no_repeated_curves_in_rings(
    data: TopologyData,
) -> list[Issue]:
    """
    TR-22: within a single surface ring, a curve must not be referenced more
    than once.
    A curve appearing twice (or more) in the same ring means the ring visits
    the same boundary segment more than once.  This creates a degenerate
    self-touching boundary regardless of the orientations used, and cannot
    form a valid simply-connected ring.
    This rule is distinct from:
      TR-04 (SurfaceClosedRing) – checks that consecutive members connect
                                  end-to-start; does not check uniqueness.
      TR-05 (SharedSurfaceEdges) – checks orientation consistency between
                                  different surfaces; does not check
                                  repetition within a single ring.
    """
    issues: list[Issue] = []

    for surface in data.get("surfaces", []):
        surface_id = surface["id"]

        for ring_index, ring in enumerate(surface.get("rings", [])):
            first_member_index_by_curve_id: dict[str, int] = {}

            for member_index, member in enumerate(ring.get("members", [])):
                curve_id = member["ref"]
                first_member_index = first_member_index_by_curve_id.get(curve_id)

                if first_member_index is None:
                    first_member_index_by_curve_id[curve_id] = member_index
                    continue

                issues.append(
                    _curve_repeated_in_ring_issue(
                        surface_id,
                        ring_index,
                        curve_id,
                        first_member_index,
                        member_index,
                    )
                )

    return issues


def validate(data: TopologyData, tolerances: Tolerances | None = None) -> list[Issue]:
    """Validate CC-02 curve topology rules.

    Args:
        data: Topology data to validate.
        tolerances: Optional tolerance overrides. If omitted, default tolerances
            are used.

    Returns:
        A list of validation issues found in `data`.
    """
    t = tolerances or Tolerances()

    issues: list[Issue] = []
    issues.extend(validate_curve_no_self_intersection(data))
    issues.extend(validate_no_dangling_curves(data))
    issues.extend(validate_minimum_curve_length(data, min_length=t.length))
    issues.extend(validate_no_duplicate_curves(data))
    issues.extend(validate_curve_intersection_at_nodes_only(data))
    issues.extend(validate_no_repeated_curves_in_rings(data))
    return issues
