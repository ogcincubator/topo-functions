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

    def _sub(a: list[float], b: list[float]) -> list[float]:
        """Return vector a minus vector b."""
        return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]

    def _cross(a: list[float], b: list[float]) -> list[float]:
        """Return the 3D cross-product of two vectors."""
        return [
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        ]

    def _dot(a: list[float], b: list[float]) -> float:
        """Return the 3D dot product of two vectors."""
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

    d1 = _sub(p2, p1)  # direction of segment 1
    d2 = _sub(p4, p3)  # direction of segment 2
    r = _sub(p3, p1)  # vector from p1 to p3

    n = _cross(d1, d2)  # d1 × d2
    n_sq = _dot(n, n)

    if n_sq < tol * tol:
        # Segments are parallel (or degenerate) — no proper interior crossing
        return False

    # Coplanarity guard: perpendicular distance² between the infinite lines
    # = (r · n)² / n_sq must be below tol²
    rn = _dot(r, n)
    if (rn * rn) / n_sq > tol * tol:
        return False  # Skew lines — no intersection

    # Parametric parameters along each segment
    t = _dot(_cross(r, d2), n) / n_sq
    s = _dot(_cross(r, d1), n) / n_sq

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
            v1 = poly[i]
            v2 = poly[i + 1]
            cx = v1[1] * v2[2] - v1[2] * v2[1]
            cy = v1[2] * v2[0] - v1[0] * v2[2]
            cz = v1[0] * v2[1] - v1[1] * v2[0]
            total += v0[0] * cx + v0[1] * cy + v0[2] * cz
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