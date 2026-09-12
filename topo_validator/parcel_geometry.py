#!/usr/bin/env python3

"""Exact geometry for TR-28/TR-29's declared solid-to-parcel containment check.

TR-28 (`DeclaredParcelContainment`) and TR-29 (`DeclaredEasementBurden`) test
whether a 3D solid's footprint lies within a declared 2D Primary Parcel,
treating the parcel as an unlimited vertical prism (the resolution for how a
2D parcel bounds a 3D volume -- see the "for-discussion/parent-parcel-
relationships" proposal this rule pair implements). That is a different
predicate from anything in `solid_geometry.py`: every predicate there
resolves a face's own plane/normal to get a projection axis, which a parcel
surface has none of -- its points are natively 2D (see
`model.point_dimensionality`), not a 3D plane the exact predicates could
derive an axis from. This module works directly in the parcel's own (x, y)
plane instead.

Mirrors `solid_geometry.py`'s stated limitation: these predicates are sound
(a positive result is real) but incomplete (checking only the solid's own
boundary vertices, not every point along its edges), which for parcel-scale
convex-ish footprints is the same trade the rest of this package makes
elsewhere for exact geometry without a compiled dependency.
"""

from __future__ import annotations

from .model import Curve, Point, Solid, Surface, TOLERANCE_GEOMETRY, solid_face_ids

Point2D = tuple[float, float]


def solid_footprint_2d(
    solid: Solid,
    surfaces: dict[str, Surface],
    curves: dict[str, Curve],
    points: dict[str, Point],
) -> list[Point2D]:
    """Return the (x, y) projection of every vertex bounding *solid*.

    Not a bounding box or convex hull: every distinct boundary vertex is
    projected (dropping z), so a concave solid's footprint is tested
    point-by-point rather than approximated by its extremes.

    Args:
        solid: The 3D solid to project, from the 3D topology view.
        surfaces: Surface index from the same (3D) topology view.
        curves: Curve index from the same (3D) topology view.
        points: Point index from the same (3D) topology view.

    Returns:
        One (x, y) pair per distinct vertex id bounding the solid.
    """
    footprint: list[Point2D] = []
    seen: set[str] = set()

    for face_id in solid_face_ids(solid):
        surface = surfaces.get(face_id)
        if not isinstance(surface, dict):
            continue

        for ring in surface.get("rings", []):
            for member in ring.get("members", []):
                curve = curves.get(member["ref"])
                if not isinstance(curve, dict):
                    continue

                for vertex_id in curve.get("vertices", []):
                    if vertex_id in seen:
                        continue
                    seen.add(vertex_id)

                    point = points.get(vertex_id)
                    if not isinstance(point, dict):
                        continue

                    coordinates = point.get("coordinates")
                    if not isinstance(coordinates, list) or len(coordinates) < 2:
                        continue

                    footprint.append((coordinates[0], coordinates[1]))

    return footprint


def parcel_ring_2d(
    surface: Surface,
    curves: dict[str, Curve],
    points: dict[str, Point],
) -> list[Point2D] | None:
    """Return the (x, y) boundary of a 2D parcel surface's outer ring.

    A parcel surface's own points are natively 2D, so this reads coordinates
    directly rather than projecting -- there is no z to drop and no face
    plane to derive a projection axis from.

    Args:
        surface: A parcel-derived surface, from the 2D topology view.
        curves: Curve index from the same (2D) topology view.
        points: Point index from the same (2D) topology view.

    Returns:
        The outer ring's (x, y) vertex sequence (closing duplicate omitted),
        or None when it has no usable geometry.
    """
    rings = surface.get("rings", [])
    if not rings:
        return None

    outer_ring = rings[0]
    coords: list[Point2D] = []

    for member in outer_ring.get("members", []):
        curve = curves.get(member["ref"])
        if not isinstance(curve, dict):
            continue

        vertices = curve.get("vertices", [])
        ordered = (
            vertices[:-1]
            if member["orientation"] == "+"
            else list(reversed(vertices))[:-1]
        )

        for vertex_id in ordered:
            point = points.get(vertex_id)
            if not isinstance(point, dict):
                continue

            coordinates = point.get("coordinates")
            if not isinstance(coordinates, list) or len(coordinates) < 2:
                continue

            coords.append((coordinates[0], coordinates[1]))

    return coords or None


def _point_to_2d_segment_distance(
    px: float,
    py: float,
    ax: float,
    ay: float,
    bx: float,
    by: float,
) -> float:
    """Return the shortest distance from (px, py) to segment (ax, ay)-(bx, by)."""
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy

    if length_sq <= 0.0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5

    t = ((px - ax) * dx + (py - ay) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    closest_x, closest_y = ax + t * dx, ay + t * dy
    return ((px - closest_x) ** 2 + (py - closest_y) ** 2) ** 0.5


def point_in_2d_ring(
    x: float,
    y: float,
    ring: list[Point2D],
    tol: float = TOLERANCE_GEOMETRY,
) -> bool:
    """Even-odd point-in-polygon test for a single 2D ring.

    A point within *tol* of any ring edge is treated as contained (not an
    ambiguous third state): this test feeds a boundary-inclusive containment
    check, not an exact/on-boundary/outside classification like
    `solid_geometry.point_in_face`'s.

    Args:
        x: Query point's x coordinate.
        y: Query point's y coordinate.
        ring: Ordered (x, y) ring vertices (no closing duplicate needed).
        tol: Boundary tolerance.

    Returns:
        True when (x, y) is inside the ring or within *tol* of an edge.
    """
    vertex_count = len(ring)

    for index in range(vertex_count):
        x1, y1 = ring[index]
        x2, y2 = ring[(index + 1) % vertex_count]
        if _point_to_2d_segment_distance(x, y, x1, y1, x2, y2) <= tol:
            return True

    inside = False
    for index in range(vertex_count):
        x1, y1 = ring[index]
        x2, y2 = ring[(index + 1) % vertex_count]
        if (y1 > y) != (y2 > y):
            x_at = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < x_at:
                inside = not inside

    return inside
