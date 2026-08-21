#!/usr/bin/env python3

"""Conformance checks for CC-01 point topology.

This module validates point-level topology rules for a `TopologyData` instance,
including uniqueness of points and point-fabric consistency.
"""

from __future__ import annotations

from itertools import combinations

from ..model import (
    err,
    Issue,
    Tolerances,
    TopologyData,
)
from ..geometry import (
    euclidean_dist,
)

CONFORMANCE_CLASS_ID = "CC-01"
CONFORMANCE_CLASS_NAME = "Point topology"
RULE_IDS = ["TR-01", "TR-11"]

DUPLICATE_POINT_PROXIMITY_CODE = "DUPLICATE_POINT_PROXIMITY"
UNKNOWN_POINT_REFERENCE_CODE = "UNKNOWN_POINT_REFERENCE"


# ---------------------------------------------------------------------------
# TR-01  Unique cadastral points
# ---------------------------------------------------------------------------


def _duplicate_point_issue(
    point_id: str,
    other_point_id: str,
    tolerance: float,
    distance: float,
) -> Issue:
    """Create a TR-01 issue for two points closer than the configured tolerance."""
    return err(
        DUPLICATE_POINT_PROXIMITY_CODE,
        f"Points {point_id} and {other_point_id} are within "
        f"tolerance {tolerance} (distance={distance:.3e})",
        object_id=point_id,
        extra={"other_id": other_point_id, "distance": distance},
    )


def validate_unique_points(
    data: TopologyData,
    tol: float = Tolerances.point,
) -> list[Issue]:
    """TR-01: no two points may lie within *tol* of each other."""
    issues: list[Issue] = []
    points = data.get("points", [])

    for point, other_point in combinations(points, 2):
        point_id = point["id"]
        other_point_id = other_point["id"]
        point_coordinates = point["coordinates"]
        other_point_coordinates = other_point["coordinates"]

        distance = euclidean_dist(point_coordinates, other_point_coordinates)
        if distance < tol:
            issues.append(
                _duplicate_point_issue(
                    point_id,
                    other_point_id,
                    tol,
                    distance,
                )
            )

    return issues


# ---------------------------------------------------------------------------
# TR-11  Point fabric consistency
# ---------------------------------------------------------------------------


def _known_point_ids(data: TopologyData) -> set[str]:
    """Return all cadastral point ids present in the topology dataset."""
    return {point["id"] for point in data.get("points", [])}


def _unknown_point_reference_issue(curve_id: str, vertex_id: str) -> Issue:
    """Create an issue for a curve vertex that references an unknown point."""
    return err(
        UNKNOWN_POINT_REFERENCE_CODE,
        f"Curve {curve_id} references unknown point {vertex_id!r}",
        object_id=curve_id,
        extra={"vertex_id": vertex_id},
    )


def validate_point_fabric_consistency(
    data: TopologyData,
) -> list[Issue]:
    """
    TR-11: every vertex id referenced in a curve must exist as a cadastral point.

    A curve referencing an unknown point id means the geometry fabric has a
    broken link between the curve layer and the point layer.
    """
    issues: list[Issue] = []
    known_points = _known_point_ids(data)

    for curve in data.get("curves", []):
        curve_id = curve["id"]

        for vertex_id in curve.get("vertices", []):
            if vertex_id not in known_points:
                issues.append(_unknown_point_reference_issue(curve_id, vertex_id))

    return issues


def validate(data: TopologyData, tolerances: Tolerances | None = None) -> list[Issue]:
    """Validate CC-01 point topology rules.

    Args:
        data: Topology data to validate.
        tolerances: Optional tolerance overrides. If omitted, default tolerances
            are used.

    Returns:
        A list of validation issues found in `data`.
    """
    t = tolerances or Tolerances()

    issues: list[Issue] = []
    issues.extend(validate_unique_points(data, tol=t.point))
    issues.extend(validate_point_fabric_consistency(data))
    return issues
