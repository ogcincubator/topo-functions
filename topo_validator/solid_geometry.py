#!/usr/bin/env python3

"""Exact solid geometry — face planes, point containment, boundary crossings.

Bounding boxes answer "could these solids overlap?", never "do they?".  A
U-shaped or L-shaped parcel wrapping a neighbour has a bounding box that
encloses the neighbour entirely while sharing no volume with it, so an AABB
test reports overlap where none exists.  The predicates here provide the exact
answer used by TR-08 (mutual overlap, CC-06) and TR-09 (parent containment,
CC-07).

Faces are treated as planar polygons with an arbitrary number of rings and
tested with the even-odd rule.  That handles concave faces and faces with holes
without triangulating them, which matters here because fan triangulation is
invalid for concave rings — precisely the wrapping faces this module exists to
judge.  Under the even-odd rule a point inside both an outer ring and a hole
ring crosses two boundaries and is correctly reported as outside, so
containment needs no ring labels and is unaffected by a mislabelled one.

``loader.from_csdm_json`` does label rings ("outer" for the first ring of a
face, "inner" for the rest).  Those labels matter only where an outer/hole
distinction is arithmetically required — the signed-volume integral behind
TR-25/TR-26/TR-27, via ``geometry.face_polygons`` — not here.

Mirrors the exact-geometry half of the test suite's independent validator
(src/tests/unit/wa_csdm_topology_rules/validator.py).  The duplication between
that module and this package is deliberate: the two engines reimplement the
rules separately so they can be cross-checked, and sharing the code would let
one bug agree with itself on both sides.  The two must nevertheless agree on
results, so changes here belong there too.
"""

from __future__ import annotations

from typing import TypedDict

from .geometry import (
    euclidean_dist,
    face_ring_pairs,
    polygon_plane,
    solid_shells,
    split_face_rings,
    vec_dot,
    vec_len,
    vec_sub,
)
from .model import (
    Curve,
    Point,
    Solid,
    Surface,
    TOLERANCE_GEOMETRY,
)

BoundingBox = tuple[float, float, float, float, float, float]
Edge = tuple[list[float], list[float]]

# Decimal places used to deduplicate boundary edges shared by two faces.
EDGE_DEDUP_PRECISION = 6

# Samples per axis when probing a shared bounding box for common interior
# volume.  4 gives 64 probes at the cell centres of a 4x4x4 lattice, which
# never land on the box faces themselves.
OVERLAP_SAMPLE_STEPS: int = 4

# Ray directions for the even-odd containment test.  Deliberately irrational
# and non-axis-aligned so a ray is unlikely to graze a vertex or run along an
# edge; several are provided so a degenerate cast can be retried rather than
# guessed at.  Fixed (not random) to keep results reproducible across runs.
RAY_DIRECTIONS: tuple[list[float], ...] = (
    [0.5773502691896258, 0.5773502691896258, 0.5773502691896258],
    [0.7071067811865476, -0.5000000000000000, 0.5000000000000000],
    [-0.3015113445777636, 0.9045340337332909, 0.3015113445777636],
    [0.2672612419124244, 0.5345224838248488, -0.8017837257372732],
    [-0.8571428571428571, -0.2857142857142857, 0.4285714285714285],
)


class FaceGeometry(TypedDict):
    """One face resolved to the form the exact predicates consume."""

    normal: list[float]
    origin: list[float]
    axis: int
    rings_3d: list[list[list[float]]]
    rings_2d: list[list[tuple[float, float]]]


# ---------------------------------------------------------------------------
# Face resolution
# ---------------------------------------------------------------------------


def project_2d(point: list[float], axis: int) -> tuple[float, float]:
    """
    Drop the *axis* component of *point*, giving 2D coordinates for in-plane
    tests.  Dropping the axis most nearly parallel to the face normal keeps the
    projected polygon non-degenerate.
    """
    if axis == 0:
        return point[1], point[2]
    if axis == 1:
        return point[0], point[2]
    return point[0], point[1]


def face_geometry(
    surface: Surface,
    curves: dict[str, Curve],
    points: dict[str, Point],
) -> FaceGeometry | None:
    """
    Build the geometric description of one face used by the exact tests.

    Returns the face plane (``normal``, ``origin``), the projection axis, and
    every ring in both 3D and projected 2D form; or None when the face has no
    usable geometry.

    The plane is taken from the outer ring — the ring not labelled a hole, or
    the largest-extent ring when labels are absent or contradict the geometry
    (see :func:`geometry.split_face_rings`).  The choice is not load-bearing
    for containment: a hole ring is coplanar with its outer ring and merely
    anti-parallel, and every consumer of ``normal``/``origin`` is invariant to
    the normal's sign and to which point in the plane is the origin.
    """
    pairs = face_ring_pairs(surface, curves, points)
    if not pairs:
        return None

    rings_3d = [coords for _ring, coords in pairs]

    outer_index, _hole_indices = split_face_rings(pairs)
    plane = polygon_plane(pairs[outer_index][1])
    if plane is None:
        return None
    normal, origin = plane

    axis = max(range(3), key=lambda k: abs(normal[k]))

    return {
        "normal": normal,
        "origin": origin,
        "axis": axis,
        "rings_3d": rings_3d,
        "rings_2d": [[project_2d(c, axis) for c in ring] for ring in rings_3d],
    }


def solid_face_geometries(
    solid: Solid,
    surfaces: dict[str, Surface],
    curves: dict[str, Curve],
    points: dict[str, Point],
) -> list[FaceGeometry]:
    """
    Return the face geometries bounding *solid*, across every shell.

    Inner (void) shells are included: for the even-odd containment test their
    faces are part of the boundary, and counting them is what makes a point
    inside a cavity correctly report as outside the solid.
    """
    geometries: list[FaceGeometry] = []

    for shell in solid_shells(solid):
        for face_id in shell.get("faces", []):
            surface = surfaces.get(face_id)
            if surface is None:
                continue

            geometry = face_geometry(surface, curves, points)
            if geometry is not None:
                geometries.append(geometry)

    return geometries


def face_edges(faces: list[FaceGeometry]) -> list[Edge]:
    """
    Return the deduplicated boundary segments of a set of face geometries.

    Each ring contributes its closing segment as well, so the returned list is
    the full wireframe of the solid.  Segments are deduplicated on rounded
    coordinates because every interior edge is shared by two faces.
    """
    seen: set[tuple[tuple[float, ...], tuple[float, ...]]] = set()
    edges: list[Edge] = []

    for face in faces:
        for ring in face["rings_3d"]:
            ring_length = len(ring)
            for index in range(ring_length):
                start = ring[index]
                end = ring[(index + 1) % ring_length]
                key_start = tuple(
                    round(value, EDGE_DEDUP_PRECISION) for value in start[:3]
                )
                key_end = tuple(
                    round(value, EDGE_DEDUP_PRECISION) for value in end[:3]
                )
                key = (
                    (key_start, key_end)
                    if key_start <= key_end
                    else (key_end, key_start)
                )
                if key in seen:
                    continue

                seen.add(key)
                edges.append((start, end))

    return edges


# ---------------------------------------------------------------------------
# Point classification
# ---------------------------------------------------------------------------


def point_to_segment_dist(
    p: list[float],
    a: list[float],
    b: list[float],
) -> float:
    """Return the shortest distance from *p* to segment *a*-*b*."""
    ab = vec_sub(b, a)
    ab_sq = vec_dot(ab, ab)
    if ab_sq <= 0.0:
        return euclidean_dist(p, a)

    t = vec_dot(vec_sub(p, a), ab) / ab_sq
    t = max(0.0, min(1.0, t))
    closest = [a[k] + t * ab[k] for k in range(3)]
    return euclidean_dist(p, closest)


def point_on_face_boundary(
    point: list[float],
    face: FaceGeometry,
    tol: float,
) -> bool:
    """True when *point* lies within *tol* of any ring edge of *face*."""
    for ring in face["rings_3d"]:
        ring_length = len(ring)
        for index in range(ring_length):
            distance = point_to_segment_dist(
                point, ring[index], ring[(index + 1) % ring_length]
            )
            if distance <= tol:
                return True

    return False


def point_in_face(
    point: list[float],
    face: FaceGeometry,
    tol: float,
) -> bool | None:
    """
    Test whether a point known to lie in the face plane falls inside the face.

    Returns True when strictly inside, False when strictly outside, and None
    when it lies on a ring boundary (within *tol*) — an ambiguous position the
    callers resolve rather than guess at.

    Containment uses the even-odd rule across *all* rings, so concave faces and
    faces with holes are handled without triangulation.
    """
    if point_on_face_boundary(point, face, tol):
        return None

    px, py = project_2d(point, face["axis"])

    inside = False
    for ring in face["rings_2d"]:
        ring_length = len(ring)
        for index in range(ring_length):
            x1, y1 = ring[index]
            x2, y2 = ring[(index + 1) % ring_length]
            if (y1 > py) != (y2 > py):
                x_at = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
                if px < x_at:
                    inside = not inside

    return inside


def point_in_solid(
    point: list[float],
    faces: list[FaceGeometry],
    tol: float,
) -> bool | None:
    """
    Even-odd point-in-polyhedron test against a set of bounding faces.

    Returns True when *point* is inside the solid, False when outside, and
    None when the answer is indeterminate — either the point lies on the
    boundary, or every candidate ray grazed an edge or vertex.  Callers treat
    None as "no evidence" rather than as a pass or a fail.

    A ray is cast from *point* and its crossings of the face interiors are
    counted; an odd count means inside.  Any cast whose hit lands on a ring
    boundary is discarded and the next direction tried, since a graze would
    otherwise be miscounted as either zero or one crossing.
    """
    for face in faces:
        # On the face plane and within its rings ⇒ on the boundary.
        if abs(vec_dot(face["normal"], vec_sub(point, face["origin"]))) <= tol:
            if point_in_face(point, face, tol) is not False:
                return None

    for direction in RAY_DIRECTIONS:
        crossings = 0
        degenerate = False

        for face in faces:
            denom = vec_dot(face["normal"], direction)
            if abs(denom) <= tol:
                # Ray parallel to this face: it cannot cross the interior, but
                # if it also lies in the plane the count is unreliable.
                offset = vec_dot(face["normal"], vec_sub(point, face["origin"]))
                if abs(offset) <= tol:
                    degenerate = True
                    break
                continue

            t = vec_dot(face["normal"], vec_sub(face["origin"], point)) / denom
            if t <= tol:
                continue  # behind the origin, or at it

            hit = [point[k] + t * direction[k] for k in range(3)]
            verdict = point_in_face(hit, face, tol)
            if verdict is None:
                degenerate = True  # grazed a ring boundary — retry
                break
            if verdict:
                crossings += 1

        if not degenerate:
            return crossings % 2 == 1

    return None


# ---------------------------------------------------------------------------
# Boundary probing
# ---------------------------------------------------------------------------


def edge_subsegment_midpoints(
    p1: list[float],
    p2: list[float],
    faces: list[FaceGeometry],
    tol: float,
) -> list[list[float]]:
    """
    Split segment *p1*-*p2* wherever it meets any face of *faces*, and return
    the midpoint of each resulting sub-segment.

    Each midpoint lies wholly on one side of the other solid's boundary, so
    testing it answers "does this stretch of edge run through the other solid?"
    without depending on the geometry of the crossing itself.

    Crossings are collected boundary-inclusively — a hit on a ring edge still
    splits the segment — because the point of the split is to bracket the
    stretches between contacts, not to classify the contacts.
    """
    d = vec_sub(p2, p1)
    seg_len = vec_len(d)
    if seg_len <= tol:
        return []

    params: list[float] = []
    for face in faces:
        denom = vec_dot(face["normal"], d)
        # |denom| / |d| is the sine of the angle between segment and plane;
        # near zero means the segment runs parallel and cannot split here.
        if abs(denom) / seg_len <= tol:
            continue

        t = vec_dot(face["normal"], vec_sub(face["origin"], p1)) / denom
        if not 0.0 < t < 1.0:
            continue

        hit = [p1[k] + t * d[k] for k in range(3)]
        if point_in_face(hit, face, tol) is not False:
            params.append(t)

    bounds = [0.0] + sorted(params) + [1.0]
    midpoints: list[list[float]] = []
    for lo, hi in zip(bounds, bounds[1:]):
        if (hi - lo) * seg_len <= tol:
            continue  # sub-segment shorter than tolerance

        mid = (lo + hi) / 2.0
        midpoints.append([p1[k] + mid * d[k] for k in range(3)])

    return midpoints


def boundary_probe_points(
    faces: list[FaceGeometry],
    edges: list[Edge],
    faces_other: list[FaceGeometry],
    tol: float,
) -> list[list[float]]:
    """
    Return probe points on the boundary of one solid, positioned to reveal how
    it sits relative to another.

    Ring vertices are included, together with the sub-segment midpoints
    produced by splitting every edge against the other solid's faces.
    """
    probes: list[list[float]] = []

    for face in faces:
        for ring in face["rings_3d"]:
            probes.extend(ring)

    for p1, p2 in edges:
        probes.extend(edge_subsegment_midpoints(p1, p2, faces_other, tol))

    return probes


def boundary_enters_solid(
    faces: list[FaceGeometry],
    edges: list[Edge],
    faces_other: list[FaceGeometry],
    tol: float,
) -> bool:
    """
    True when part of one solid's boundary runs through the *interior* of
    another.

    A boundary point strictly inside the other solid is positive proof of
    shared volume: the other solid is locally solid there, and this solid's
    boundary passes through it, so their interiors must meet.

    Testing the interior position of boundary probes — rather than classifying
    the crossings themselves — is what makes this robust for survey data.  Two
    parcels meeting at a shared but independently-modelled wall have faces that
    are coincident only to within millimetres; a test that asks "does this edge
    cross that face?" answers yes for such a pair, because the two planes are
    not exactly parallel.  Asking instead "does this stretch of edge lie inside
    the neighbour?" answers no, which is correct — the edge runs along the
    shared boundary, not through the neighbour.
    """
    probes = boundary_probe_points(faces, edges, faces_other, tol)
    return any(point_in_solid(p, faces_other, tol) is True for p in probes)


def boundary_escapes_solid(
    faces: list[FaceGeometry],
    edges: list[Edge],
    faces_other: list[FaceGeometry],
    tol: float,
) -> bool:
    """
    True when part of one solid's boundary lies strictly *outside* another.

    The containment counterpart of :func:`boundary_enters_solid`: a boundary
    point strictly outside the other solid proves this solid is not contained
    within it.  Points on the shared boundary are indeterminate and ignored, so
    a child sharing faces with its parent is not misreported.
    """
    probes = boundary_probe_points(faces, edges, faces_other, tol)
    return any(point_in_solid(p, faces_other, tol) is False for p in probes)


def first_decisive_containment(
    faces_probe: list[FaceGeometry],
    faces_target: list[FaceGeometry],
    tol: float,
) -> bool | None:
    """
    Decide whether the solid bounded by *faces_probe* sits inside the solid
    bounded by *faces_target*, given that their boundaries do not cross.

    With no boundary crossing, every vertex of the probe solid is on the same
    side of the target, so a single decisive vertex settles it.  Vertices are
    tried in turn because a vertex shared with the target's boundary yields
    None; the first definite answer is returned, or None if none is reached.
    """
    for face in faces_probe:
        for ring in face["rings_3d"]:
            for vertex in ring:
                verdict = point_in_solid(vertex, faces_target, tol)
                if verdict is not None:
                    return verdict

    return None


# ---------------------------------------------------------------------------
# Overlap
# ---------------------------------------------------------------------------


def faces_bbox(faces: list[FaceGeometry]) -> BoundingBox | None:
    """Return (xmin, ymin, zmin, xmax, ymax, zmax) over all face rings."""
    coords = [c for face in faces for ring in face["rings_3d"] for c in ring]
    if not coords:
        return None

    return (
        min(c[0] for c in coords),
        min(c[1] for c in coords),
        min(c[2] for c in coords),
        max(c[0] for c in coords),
        max(c[1] for c in coords),
        max(c[2] for c in coords),
    )


def bbox_intersection(
    b1: tuple[float, ...],
    b2: tuple[float, ...],
    tol: float,
) -> tuple[list[float], list[float]] | None:
    """
    Return ``(lo, hi)`` of the box shared by *b1* and *b2*, or None when they
    do not overlap in every axis by more than *tol*.

    A shared box thinner than *tol* in any axis is face-to-face contact, not
    shared volume, so it is reported as no intersection.
    """
    lo = [max(b1[k], b2[k]) for k in range(3)]
    hi = [min(b1[k + 3], b2[k + 3]) for k in range(3)]
    if any(hi[k] - lo[k] <= tol for k in range(3)):
        return None

    return lo, hi


def shared_interior_point(
    faces_a: list[FaceGeometry],
    faces_b: list[FaceGeometry],
    box: tuple[list[float], list[float]],
    tol: float,
) -> bool:
    """
    True when a probe point is found strictly inside both solids.

    Probes are placed at the cell centres of a lattice spanning the solids'
    shared bounding box — the only region where common volume can exist.  A
    point strictly interior to both solids is positive proof of shared volume.
    Indeterminate probes (on a boundary, or undecidable by every ray) are
    skipped rather than counted either way.
    """
    lo, hi = box
    steps = OVERLAP_SAMPLE_STEPS

    for i in range(steps):
        for j in range(steps):
            for k in range(steps):
                probe = [
                    lo[0] + (i + 0.5) / steps * (hi[0] - lo[0]),
                    lo[1] + (j + 0.5) / steps * (hi[1] - lo[1]),
                    lo[2] + (k + 0.5) / steps * (hi[2] - lo[2]),
                ]
                if point_in_solid(probe, faces_a, tol) is not True:
                    continue
                if point_in_solid(probe, faces_b, tol) is True:
                    return True

    return False


def solids_properly_overlap(
    faces_a: list[FaceGeometry],
    faces_b: list[FaceGeometry],
    tol: float = TOLERANCE_GEOMETRY,
) -> bool:
    """
    Narrow-phase overlap test between two solids.  True only for solids that
    genuinely share volume.

    Three independent conditions are checked, any one of which proves an
    overlap:

    1. **Interpenetration** — part of one solid's boundary lies strictly inside
       the other.
    2. **Nesting** — one solid lies wholly within the other.
    3. **Shared interior** — a probe point is found strictly inside both.

    Every condition takes the same form: it fires only on a point demonstrably
    interior to a solid, never on the geometry of a contact.  That is what
    keeps millimetre-level coincident faces — two parcels modelled either side
    of the same wall — from reading as interpenetration.

    Condition 3 is not redundant.  Two axis-aligned solids can share volume
    while every edge intersection lands exactly on a face *boundary*: two unit
    cubes offset along X but spanning identical Y and Z ranges share volume,
    yet no edge stretch lies strictly inside the other cube and neither is
    nested.  Only an interior probe detects that arrangement.

    Solids that merely touch — sharing a face, an edge or a vertex — satisfy
    none of the three and are correctly reported as non-overlapping.  That is
    what separates a genuine violation from the wrapping arrangement a
    bounding-box test cannot tell apart.

    Limitations
    -----------
    All three conditions are sound: when one fires, the solids really do share
    volume.  None is complete, because each rests on a finite set of probes, so
    absence of a detection is not a proof of disjointness.  An overlap region
    that contains no lattice cell centre, encloses no boundary probe and leaves
    neither solid nested may go unreported; such a region is thinner than a
    sixteenth of the shared bounding box in some axis, which for parcel
    geometry is contact rather than encroachment.  Deciding those cases exactly
    needs a polyhedral boolean (e.g. OpenCASCADE ``BRepAlgoAPI_Common``), which
    would add a compiled dependency to this otherwise pure-Python package.
    """
    if not faces_a or not faces_b:
        return False

    edges_a = face_edges(faces_a)
    edges_b = face_edges(faces_b)

    # 1. One boundary running through the other solid's interior.
    if boundary_enters_solid(faces_a, edges_a, faces_b, tol):
        return True
    if boundary_enters_solid(faces_b, edges_b, faces_a, tol):
        return True

    # 2. One solid wholly inside the other.
    if first_decisive_containment(faces_a, faces_b, tol):
        return True
    if first_decisive_containment(faces_b, faces_a, tol):
        return True

    # 3. Common interior volume within the shared bounding box.
    bbox_a = faces_bbox(faces_a)
    bbox_b = faces_bbox(faces_b)
    if bbox_a and bbox_b:
        box = bbox_intersection(bbox_a, bbox_b, tol)
        if box and shared_interior_point(faces_a, faces_b, box, tol):
            return True

    return False
