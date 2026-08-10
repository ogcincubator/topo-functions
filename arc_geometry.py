#!/usr/bin/env python3

"""
Bridge between topo-arc *topology* descriptions and arc_densify.

The topo-arc building block describes curved geometry by *reference* to point
features rather than by storing the curve's vertices:

    Arc            references = [start, point-on-arc, end]
    ArcWithCenter  references = [start, end, centre]    + orientation
    ArcByChord     references = [start, end]            + radius + orientation
    CircleByCenter references = [centre]                + radius

arc_densify.densify_arc() works from an explicit (start, end, centre,
direction) description, so this module supplies the missing geometry —
circumcentre for a 3-point Arc, the centre implied by a chord + radius for
ArcByChord, and a full sweep for CircleByCenter — then hands off to
densify_arc()/max_chord_angle() to produce chord vertices within a maximum
sagitta tolerance.

The result is a GeoJSON geometry dict (LineString for arcs, Polygon for a
circle) that a caller (see topo2geojson) can drop straight onto a feature.

CubicSpline is intentionally out of scope: arc_densify only models circular
arcs, so spline topology is left for the caller to approximate.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

from arc_densify import (
    ArcDirection,
    densify_arc,
    max_chord_angle,
    point_angle,
)

# Topology `type` values (lower-cased) this module can densify.
ARC_TOPOLOGY_TYPES = frozenset(
    {"arc", "arcwithcenter", "arcbychord", "circlebycenter"}
)

# A generous radius tolerance for the internally-derived centres: the centres
# are computed to lie exactly on the supplied points, so the only differences
# are floating-point rounding. densify_arc()'s default 5mm tolerance assumes
# metre survey coordinates and would spuriously reject small demo coordinates,
# so we relax it relative to the arc's own radius.
def _radius_tolerance(radius: float) -> float:
    return max(1e-6, radius * 1e-6)


Coord = Sequence[float]


def _xy(coord: Coord) -> tuple[float, float]:
    """Return the (x, y) pair of a coordinate, ignoring any Z component."""
    return (float(coord[0]), float(coord[1]))


def circumcentre(a: Coord, b: Coord, c: Coord) -> tuple[float, float]:
    """
    Return the centre of the circle passing through three points.

    Raises ValueError if the points are collinear (no finite centre).
    """
    ax, ay = _xy(a)
    bx, by = _xy(b)
    cx, cy = _xy(c)

    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if math.isclose(d, 0.0, abs_tol=1e-12):
        raise ValueError(
            "Arc points are collinear; no circle passes through them."
        )

    a_sq = ax * ax + ay * ay
    b_sq = bx * bx + by * by
    c_sq = cx * cx + cy * cy

    ux = (a_sq * (by - cy) + b_sq * (cy - ay) + c_sq * (ay - by)) / d
    uy = (a_sq * (cx - bx) + b_sq * (ax - cx) + c_sq * (bx - ax)) / d
    return (ux, uy)


def three_point_orientation(start: Coord, mid: Coord, end: Coord) -> ArcDirection:
    """
    Return the sweep direction of the arc start -> mid -> end.

    Uses the sign of the cross product of (mid - start) and (end - start):
    a counter-clockwise turn means the arc sweeps counter-clockwise.
    """
    sx, sy = _xy(start)
    mx, my = _xy(mid)
    ex, ey = _xy(end)
    cross = (mx - sx) * (ey - sy) - (my - sy) * (ex - sx)
    return "ccw" if cross > 0 else "cw"


def chord_centre(
    start: Coord,
    end: Coord,
    radius: float,
    orientation: ArcDirection,
) -> tuple[float, float]:
    """
    Return the centre of the (minor) circular arc joining two chord endpoints
    with the given radius, on the side implied by `orientation`.

    The centre lies on the perpendicular bisector of the chord, a distance
    sqrt(radius^2 - halfchord^2) from the chord midpoint. Of the two candidate
    centres (one either side of the chord) the one whose *minor* arc is swept
    in the requested direction is chosen.
    """
    sx, sy = _xy(start)
    ex, ey = _xy(end)

    mx, my = (sx + ex) / 2.0, (sy + ey) / 2.0
    chord_len = math.hypot(ex - sx, ey - sy)
    if chord_len == 0.0:
        raise ValueError("ArcByChord start and end points coincide.")

    half = chord_len / 2.0
    if radius < half - 1e-9:
        raise ValueError(
            f"ArcByChord radius {radius} is too small for chord length "
            f"{chord_len} (needs radius >= {half})."
        )

    offset = math.sqrt(max(0.0, radius * radius - half * half))
    # Unit vector along the chord, then its left-hand normal.
    ux, uy = (ex - sx) / chord_len, (ey - sy) / chord_len
    nx, ny = -uy, ux

    candidates = [
        (mx + offset * nx, my + offset * ny),
        (mx - offset * nx, my - offset * ny),
    ]

    for centre in candidates:
        ccw_sweep = (point_angle(end, centre) - point_angle(start, centre)) % (
            2.0 * math.pi
        )
        # For a ccw minor arc the ccw sweep is <= pi; for a cw minor arc the
        # ccw sweep is >= pi (its complement, the cw sweep, is the minor one).
        if orientation == "ccw" and ccw_sweep <= math.pi:
            return centre
        if orientation == "cw" and ccw_sweep >= math.pi:
            return centre

    # Degenerate (semicircle) fallback: either centre gives the same arc.
    return candidates[0]


def densify_full_circle(
    centre: Coord,
    radius: float,
    max_offset: float,
) -> list[tuple[float, float]]:
    """
    Return a closed ring of vertices approximating a full circle within
    max_offset sagitta tolerance.
    """
    cx, cy = _xy(centre)
    theta_max = max_chord_angle(radius, max_offset)
    segments = max(3, math.ceil((2.0 * math.pi) / theta_max))

    points = [
        (
            cx + radius * math.cos(2.0 * math.pi * i / segments),
            cy + radius * math.sin(2.0 * math.pi * i / segments),
        )
        for i in range(segments)
    ]
    points.append(points[0])  # close the ring
    return points


def arc_topology_to_geometry(
    topo: dict[str, Any],
    coords: list[Coord],
    max_offset: float,
) -> dict[str, Any] | None:
    """
    Convert a resolved arc/circle topology to a GeoJSON geometry dict.

    Parameters
    ----------
    topo:
        The feature's inline `topology` block. Must carry `type` and, for
        ArcByChord/CircleByCenter, `radius`, and for ArcWithCenter/ArcByChord,
        `orientation`.
    coords:
        The topology's `references` already resolved to coordinates, in the
        same order as the references list.
    max_offset:
        Maximum permitted chord-to-arc offset (sagitta), in the coordinate
        units.

    Returns
    -------
    dict | None
        A GeoJSON geometry dict (LineString for arcs, Polygon for a circle),
        or None if the topology type is not a densifiable arc/circle or the
        references could not be resolved to enough points.
    """
    topo_type = (topo.get("type") or "").lower()
    if topo_type not in ARC_TOPOLOGY_TYPES:
        return None

    if topo_type == "arc":
        if len(coords) < 3:
            return None
        start, mid, end = coords[0], coords[1], coords[2]
        centre = circumcentre(start, mid, end)
        orientation = three_point_orientation(start, mid, end)
        points = densify_arc(
            start=_xy(start),
            end=_xy(end),
            centre=centre,
            max_offset=max_offset,
            direction=orientation,
            radius_tolerance=_radius_tolerance(math.hypot(
                centre[0] - start[0], centre[1] - start[1])),
        )
        return _linestring(points)

    if topo_type == "arcwithcenter":
        if len(coords) < 3:
            return None
        start, end, centre = coords[0], coords[1], coords[2]
        orientation = (topo.get("orientation") or "ccw").lower()
        radius = math.hypot(centre[0] - start[0], centre[1] - start[1])
        points = densify_arc(
            start=_xy(start),
            end=_xy(end),
            centre=_xy(centre),
            max_offset=max_offset,
            direction=orientation,
            radius_tolerance=_radius_tolerance(radius),
        )
        return _linestring(points)

    if topo_type == "arcbychord":
        if len(coords) < 2:
            return None
        radius = topo.get("radius")
        if radius is None:
            return None
        orientation = (topo.get("orientation") or "ccw").lower()
        start, end = coords[0], coords[1]
        centre = chord_centre(start, end, float(radius), orientation)
        points = densify_arc(
            start=_xy(start),
            end=_xy(end),
            centre=centre,
            max_offset=max_offset,
            direction=orientation,
            radius_tolerance=_radius_tolerance(float(radius)),
        )
        return _linestring(points)

    # topo_type == "circlebycenter"
    if not coords:
        return None
    radius = topo.get("radius")
    if radius is None:
        return None
    ring = densify_full_circle(coords[0], float(radius), max_offset)
    return {
        "type": "Polygon",
        "coordinates": [[[x, y] for x, y in ring]],
    }


def _linestring(points: list[tuple[float, float]]) -> dict[str, Any]:
    return {
        "type": "LineString",
        "coordinates": [[x, y] for x, y in points],
    }


if __name__ == "__main__":
    # Small self-check using the topo-arc referenced points.
    P1, P2, P3, PC = (10, 10), (20, 20), (13, 17), (10, 20)

    demo = [
        ("Arc", {"type": "Arc", "references": ["P1", "P3", "P2"]}, [P1, P3, P2]),
        ("ArcWithCenter",
         {"type": "ArcWithCenter", "orientation": "ccw"}, [P1, P2, PC]),
        ("ArcByChord",
         {"type": "ArcByChord", "radius": 105.438, "orientation": "cw"}, [P1, P2]),
        ("CircleByCenter", {"type": "CircleByCenter", "radius": 10}, [PC]),
    ]
    for name, topo, coords in demo:
        geom = arc_topology_to_geometry(topo, coords, max_offset=0.02)
        n = len(geom["coordinates"][0]) if geom["type"] == "Polygon" \
            else len(geom["coordinates"])
        print(f"{name}: {geom['type']} with {n} vertices")
