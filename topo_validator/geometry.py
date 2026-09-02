#!/usr/bin/env python3

"""Geometry helpers."""

from __future__ import annotations

import math
from typing import Any

from .model import (
    Coordinate3D,
    Curve,
    Orientation,
    Point,
    Ring,
    Solid,
    Surface,
)

def euclidean_dist(a: Coordinate3D, b: Coordinate3D) -> float:
    """Return the Euclidean distance between two 3D coordinates."""
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def vec_sub(a: list[float], b: list[float]) -> list[float]:
    """Return vector a minus vector b."""
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


# The test suite's standalone validator (src/tests/unit/wa_csdm_topology_rules/
# validator.py) carries its own copy of this helper on purpose: it reimplements
# the topology rules independently so the two can be cross-checked, and sharing
# a vector primitive would let one bug agree with itself on both sides.
# noinspection DuplicatedCode
def vec_cross(a: list[float], b: list[float]) -> list[float]:
    """Return the 3D cross-product of two vectors."""
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]


def vec_dot(a: list[float], b: list[float]) -> float:
    """Return the 3D dot product of two vectors."""
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def segments_intersect_3d(
    p1: list[float],
    p2: list[float],
    p3: list[float],
    p4: list[float],
    tol: float = 1e-9,
) -> bool:
    """
    Return True when segment p1-p2 and segment p3-p4 properly intersect
    in 3D space (i.e. cross in their interiors, not just touch at ends).

    Algorithm
    ---------
    Two segments can only intersect if they are coplanar (not skew).  The
    perpendicular distance between the two infinite lines containing the
    segments is used as a coplanarity guard:

      dist = |r · n| / |n| where r = p3 − p1, n = d1 × d2

    If dist > tol, the lines are skew and cannot intersect.  When the lines
    are coplanar the parametric parameters *t* (on p1-p2) and *s* (on
    p3-p4) are computed from:

      t = (r × d2) · n / |n|²
      s = (r × d1) · n / |n|²

    A proper interior intersection requires 0 < t < 1 and 0 < s < 1.
    """

    d1 = vec_sub(p2, p1)  # direction of segment 1
    d2 = vec_sub(p4, p3)  # direction of segment 2
    r = vec_sub(p3, p1)  # vector from p1 to p3

    n = vec_cross(d1, d2)  # d1 × d2
    n_sq = vec_dot(n, n)

    if n_sq < tol * tol:
        # Segments are parallel (or degenerate) — no proper interior crossing
        return False

    # Coplanarity guard: perpendicular distance² between the infinite lines
    # = (r · n)² / n_sq must be below tol²
    rn = vec_dot(r, n)
    if (rn * rn) / n_sq > tol * tol:
        return False  # Skew lines — no intersection

    # Parametric parameters along each segment
    t = vec_dot(vec_cross(r, d2), n) / n_sq
    s = vec_dot(vec_cross(r, d1), n) / n_sq

    return 0.0 < t < 1.0 and 0.0 < s < 1.0


def curve_end_id(curve: Curve, orientation: Orientation) -> str:
    """Return the end point id for a curve in the given orientation."""
    verts = curve["vertices"]
    return verts[-1] if orientation == "+" else verts[0]

def curve_start_id(curve: Curve, orientation: Orientation) -> str:
    """Return the start point id for a curve in the given orientation."""
    verts = curve["vertices"]
    return verts[0] if orientation == "+" else verts[-1]

def point_coordinates(
    points: dict[str, Point],
    point_id: Any,
) -> list[Any] | None:
    """Return validated point coordinates, or None when unavailable."""
    point = points.get(point_id)

    if not isinstance(point, dict):
        return None

    coordinates = point.get("coordinates")
    if not isinstance(coordinates, list):
        return None

    if len(coordinates) < 3:
        return None

    if not all(isinstance(value, int | float) for value in coordinates):
        return None

    return coordinates


def solid_bbox(
    solid: Solid,
    surfaces: dict[str, Surface],
    curves: dict[str, Curve],
    points: dict[str, Point],
) -> tuple[float, float, float, float, float, float] | None:
    """Return (xmin, ymin, zmin, xmax, ymax, zmax) or None if no geometry."""
    coords: list[Coordinate3D] = []
    for face_id in solid.get("faces", []):
        sf = surfaces.get(face_id)
        if not isinstance(sf, dict):
            continue
        for ring in sf.get("rings", []):
            for member in ring.get("members", []):
                cv = curves.get(member["ref"])
                if not isinstance(cv, dict):
                    continue
                for vid in cv.get("vertices", []):
                    pt = points.get(vid)
                    if not isinstance(pt, dict):
                        continue
                    coordinates = pt.get("coordinates")
                    if not isinstance(coordinates, list):
                        continue

                    coords.append(coordinates)
    if not coords:
        return None
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    zs = [c[2] for c in coords]
    return min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)


def ring_coords(
    ring: Ring,
    curves: dict[str, Curve],
    points: dict[str, Point],
) -> list[list[float]] | None:
    """
    Return the ordered [x, y, z] vertex sequence for *ring*, excluding the
    closing duplicate vertex so the polygon has no repeated first/last point.

    Orientation "+" → traverse vertices forward (verts[:-1]).
    Orientation "-" → traverse vertices backward (reversed(verts)[:-1]).

    Returns None if any referenced curve or point is missing.
    """
    coords: list[list[float]] = []

    for member in ring.get("members", []):
        curve = curves.get(member["ref"])
        if curve is None:
            continue

        vertices = curve.get("vertices", [])
        ordered = (
            vertices[:-1]
            if member["orientation"] == "+"
            else list(reversed(vertices))[:-1]
        )

        for point_id in ordered:
            point = points.get(point_id)
            if point is None:
                continue

            coordinates = point.get("coordinates")
            if len(coordinates) < 3:
                continue

            coords.append(coordinates)

    return coords


HOLE_RING_TYPES: frozenset[str] = frozenset({"inner", "hole"})


def ring_is_hole(ring: Ring) -> bool:
    """True when *ring* is labelled as an interior (hole) boundary."""
    return str(ring.get("type", "outer")).lower() in HOLE_RING_TYPES


def ring_extent(coords: list[list[float]]) -> float:
    """Return the summed x/y/z span of a ring — an inexpensive size proxy."""
    return sum(
        max(c[k] for c in coords) - min(c[k] for c in coords) for k in range(3)
    )


def face_ring_pairs(
    surface: Surface,
    curves: dict[str, Curve],
    points: dict[str, Point],
) -> list[tuple[Ring, list[list[float]]]]:
    """
    Resolve every ring of *surface* to a ``(ring, coords)`` pair.

    Rings whose curves or points are missing, and rings with fewer than three
    vertices (which bound no area), are dropped — so indices do NOT line up
    with ``surface["rings"]``.
    """
    pairs: list[tuple[Ring, list[list[float]]]] = []
    for ring in surface.get("rings", []):
        coords = ring_coords(ring, curves, points)
        if coords and len(coords) >= 3:
            pairs.append((ring, coords))
    return pairs


def split_face_rings(
    pairs: list[tuple[Ring, list[list[float]]]],
) -> tuple[int, list[int]]:
    """
    Return ``(outer_index, hole_indices)`` into *pairs*.

    Rings labelled "inner"/"hole" are excluded from the outer candidates, so an
    explicit label decides — including when two rings span the same bounding
    box, where an extent alone cannot choose.  Extent then picks among the
    remaining candidates and vetoes the labels when a ring called a hole is
    *strictly* larger than the best labelled outer ring, since that means the
    labels are wrong for this face.  Faces with no hole labels fall through to
    a pure extent.
    """
    order = list(range(len(pairs)))
    extents = [ring_extent(coords) for _, coords in pairs]

    def extent_at(index: int) -> float:
        """Extent of the ring at *index* — the sort key for the max() calls."""
        return extents[index]

    candidates = [i for i in order if not ring_is_hole(pairs[i][0])] or order
    outer_index = max(candidates, key=extent_at)
    if extents[outer_index] < max(extents):
        outer_index = max(order, key=extent_at)
    return outer_index, [i for i in order if i != outer_index]


def polygon_normal(coords: list[list[float]]) -> list[float] | None:
    """
    Return the Newell normal of a planar polygon, or None when degenerate.

    Not normalised: only its direction is used to compare two rings' windings.
    """
    if len(coords) < 3:
        return None
    nx = ny = nz = 0.0
    for i, current in enumerate(coords):
        nxt = coords[(i + 1) % len(coords)]
        nx += (current[1] - nxt[1]) * (current[2] + nxt[2])
        ny += (current[2] - nxt[2]) * (current[0] + nxt[0])
        nz += (current[0] - nxt[0]) * (current[1] + nxt[1])
    if nx * nx + ny * ny + nz * nz == 0.0:
        return None
    return [nx, ny, nz]


def face_polygons(
    surface: Surface,
    curves: dict[str, Curve],
    points: dict[str, Point],
    orientation: Orientation = "+",
) -> list[list[list[float]]]:
    """
    Return the polygons of one face for signed-volume integration.

    A face bounded by an outer ring and one or more holes encloses
    ``region(outer) − region(hole)``, so its flux must be
    ``flux(outer) − flux(hole)``.  Fan triangulation cannot express a hole, so
    the subtraction is produced by emitting every hole ring wound *opposite* to
    the outer ring — decided from the rings' own normals rather than assumed
    from the source data, so the result does not depend on the exporter's
    winding convention.

    ``orientation`` is the face's orientation within its shell; "-" reverses
    the whole face, outer ring, and holes together.
    """
    pairs = face_ring_pairs(surface, curves, points)
    if not pairs:
        return []

    outer_index, hole_indices = split_face_rings(pairs)
    outer_normal = polygon_normal(pairs[outer_index][1])

    polygons: list[list[list[float]]] = [pairs[outer_index][1]]
    for index in hole_indices:
        coords = pairs[index][1]
        hole_normal = polygon_normal(coords)
        co_wound = (
            outer_normal is not None
            and hole_normal is not None
            and sum(hole_normal[k] * outer_normal[k] for k in range(3)) > 0.0
        )
        # A degenerate (collinear) hole has no normal and passes through
        # unchanged; it contributes zero flux under fan triangulation anyway.
        polygons.append(list(reversed(coords)) if co_wound else coords)

    if orientation == "-":
        polygons = [list(reversed(poly)) for poly in polygons]
    return polygons


def signed_volume_of_polygons(polygons: list[list[list[float]]]) -> float:
    """
    Compute the signed volume of a closed polyhedron from its face polygons.

    Uses the divergence theorem:

      V = (1/6) · Σ_faces Σ_triangles v0 · (v1 × v2)

    Where each face is fan-triangulated from its first vertex v0.

    A **positive** result means face normals point outward (right-hand rule,
    correct for an outer shell).  A **negative** result means the winding is
    reversed — all normals point inward.
    """
    total = 0.0
    for poly in polygons:
        n = len(poly)
        if n < 3:
            continue
        v0 = poly[0]
        for i in range(1, n - 1):
            total += vec_dot(v0, vec_cross(poly[i], poly[i + 1]))
    return total / 6.0


def bbox_strictly_overlaps(
    b1: tuple[float, ...],
    b2: tuple[float, ...],
) -> bool:
    """True when the two AABBs have a non-zero volume intersection."""
    return (
        b1[0] < b2[3]
        and b1[3] > b2[0]
        and b1[1] < b2[4]
        and b1[4] > b2[1]
        and b1[2] < b2[5]
        and b1[5] > b2[2]
    )


def bbox_contains(
    outer: tuple[float, ...],
    inner: tuple[float, ...],
) -> bool:
    """True when *outer* fully encloses *inner* (inclusive boundaries)."""
    return (
        outer[0] <= inner[0]
        and outer[3] >= inner[3]
        and outer[1] <= inner[1]
        and outer[4] >= inner[4]
        and outer[2] <= inner[2]
        and outer[5] >= inner[5]
    )