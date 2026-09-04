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
    Shell,
    Solid,
    Surface,
    TOLERANCE_FACE_NORMAL,
    TOLERANCE_GEOMETRY,
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


def vec_len(a: list[float]) -> float:
    """Return the Euclidean length of a vector."""
    return math.sqrt(vec_dot(a, a))


def segments_intersect_3d(
    p1: list[float],
    p2: list[float],
    p3: list[float],
    p4: list[float],
    tol: float = TOLERANCE_GEOMETRY,
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

    A proper interior intersection requires *t* and *s* to be strictly inside
    ``(0, 1)`` by at least ``tol`` of arc length.

    Shared endpoints
    ----------------
    Segments that share an endpoint meet at a vertex.  That is the normal
    condition for consecutive segments of a ring and for edges of adjacent
    faces of the same solid, and is not a crossing.  Such pairs are rejected
    up front: relying on the ``0 < t < 1`` bounds alone is not safe, because
    a shared endpoint yields *t* and *s* of ``1 - 2.2e-16`` rather than
    exactly 1 once the coordinates have been through projection arithmetic,
    which slips through a strict comparison and reports a false crossing.

    The interior bounds are therefore expressed as a distance (``tol`` metres)
    converted to parameter space per segment, rather than as a bare ``0 < t``.

    Mirrors the same helper in the test suite's independent validator
    (src/tests/unit/wa_csdm_topology_rules/validator.py); the two engines must
    agree on what counts as a crossing, so the guards are defined the same way
    in both.  TR-02, TR-14, TR-15, and TR-24 all rest on this predicate.
    """
    # Shared endpoint — segments meet at a vertex, which is not a crossing.
    for a in (p1, p2):
        for b in (p3, p4):
            if euclidean_dist(a, b) <= tol:
                return False

    d1 = vec_sub(p2, p1)  # direction of segment 1
    d2 = vec_sub(p4, p3)  # direction of segment 2
    r = vec_sub(p3, p1)  # vector from p1 to p3

    len1 = vec_len(d1)
    len2 = vec_len(d2)
    if len1 <= tol or len2 <= tol:
        return False  # degenerate (zero-length) segment

    n = vec_cross(d1, d2)  # d1 × d2
    n_sq = vec_dot(n, n)

    # Parallel guard on the *angle*, so it is independent of segment length:
    # |d1 × d2| / (|d1|·|d2|) is sin(angle) between the directions.
    if math.sqrt(n_sq) / (len1 * len2) <= tol:
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

    # Convert the linear tolerance into parameter space for each segment so
    # the interior test means "at least tol metres clear of either endpoint".
    eps1 = tol / len1
    eps2 = tol / len2

    return eps1 < t < 1.0 - eps1 and eps2 < s < 1.0 - eps2


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


def polygon_area_vector(
    poly: list[list[float]],
) -> tuple[float, float, float]:
    """Return the Newell area vector of *poly*, whose magnitude is 2·area.

    The direction follows the polygon's winding, so summing this across an
    oriented surface measures how far that surface is from closing — the basis
    of the CC-04 shell-closure check.
    """
    nx = ny = nz = 0.0
    n = len(poly)
    for i in range(n):
        current = poly[i]
        nxt = poly[(i + 1) % n]
        nx += (current[1] - nxt[1]) * (current[2] + nxt[2])
        ny += (current[2] - nxt[2]) * (current[0] + nxt[0])
        nz += (current[0] - nxt[0]) * (current[1] + nxt[1])
    return nx, ny, nz


def polygon_normal(coords: list[list[float]]) -> list[float] | None:
    """
    Return the Newell normal of a planar polygon, or None when degenerate.

    Not normalised: only its direction is used to compare two rings' windings.
    """
    if len(coords) < 3:
        return None
    nx, ny, nz = polygon_area_vector(coords)
    if nx * nx + ny * ny + nz * nz == 0.0:
        return None
    return [nx, ny, nz]


def polygon_plane(
    coords: list[list[float]],
) -> tuple[list[float], list[float]] | None:
    """
    Return ``(unit_normal, origin)`` for the plane of a polygon ring.

    Uses Newell's method, which sums the cross products of every consecutive
    edge pair rather than relying on any single vertex triple.  That keeps the
    normal stable for the slightly non-planar rings that survey exports
    routinely contain, and for rings whose first three vertices happen to be
    collinear.

    Returns None when the ring is degenerate (all vertices collinear, or fewer
    than three vertices), in which case it bounds no area and cannot be crossed.

    Distinct from :func:`polygon_normal`, which returns the un-normalised
    vector used only to compare two rings' windings.  The exact solid-geometry
    predicates need a unit normal and a point on the plane.
    """
    if len(coords) < 3:
        return None

    nx, ny, nz = polygon_area_vector(coords)

    normal = [nx, ny, nz]
    length = vec_len(normal)
    if length <= TOLERANCE_FACE_NORMAL:
        return None

    return [nx / length, ny / length, nz / length], coords[0]


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


def solid_shells(solid: Solid) -> list[Shell]:
    """Return a solid's structured shells, falling back to its flat face list.

    Fixtures predating structured shells carry ``faces`` / ``face_orientations``
    directly on the solid; those are treated as a single outer shell so every
    shell-level rule sees the same shape of input.
    """
    fallback_shell: Shell = {
        "type": "outer",
        "faces": solid.get("faces", []),
        "face_orientations": solid.get("face_orientations", {}),
    }
    return solid.get("shells") or [fallback_shell]


def shell_polygons(
    shell: Shell,
    surfaces: dict[str, Surface],
    curves: dict[str, Curve],
    points: dict[str, Point],
) -> list[list[list[float]]]:
    """
    Return oriented polygons for every resolvable face of *shell*.

    A face with holes contributes ``flux(outer) − flux(hole)``; ``face_polygons``
    emits each hole ring wound against its outer ring to produce that
    subtraction, so results do not depend on the exporter having counter-wound
    its holes.
    """
    polygons: list[list[list[float]]] = []
    face_orientations = shell.get("face_orientations", {})

    for face_id in shell.get("faces", []):
        surface = surfaces.get(face_id)
        if surface is None:
            continue

        polygons += face_polygons(
            surface, curves, points, face_orientations.get(face_id, "+")
        )

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

    Local origin
    ------------
    Vertices are shifted to the polygon set's own centroid before integrating.
    For a closed surface that is a no-op — signed volume is
    translation-invariant — but on projected survey coordinates it matters: a
    parcel 6.5e6 m from the projection origin makes the raw integrand ~1e19 for
    a ~1e2 answer, discarding most of the available precision.  And when the
    surface is *not* closed, the result stops being translation-invariant, so a
    distant origin multiplies the closure defect into a wildly wrong figure
    instead of a merely wrong one.
    """
    vertices = [
        vertex for poly in polygons if len(poly) >= 3 for vertex in poly
    ]
    if not vertices:
        return 0.0

    count = len(vertices)
    origin = [
        sum(vertex[axis] for vertex in vertices) / count for axis in range(3)
    ]

    total = 0.0
    for poly in polygons:
        n = len(poly)
        if n < 3:
            continue
        v0 = vec_sub(poly[0], origin)
        for i in range(1, n - 1):
            total += vec_dot(
                v0, vec_cross(vec_sub(poly[i], origin), vec_sub(poly[i + 1], origin))
            )
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