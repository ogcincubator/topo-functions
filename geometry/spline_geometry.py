#!/usr/bin/env python3

"""
Interpolate topo-arc CubicSpline topology into a densified polyline.

Where arc_densify models circular arcs, this module handles the CubicSpline
topology type, delegating the actual interpolation to the `splines` package
(https://pypi.org/project/splines/, MIT licensed):

    CubicSpline                references = [p0, p1, ..., pN]
        -> a natural cubic spline through the control points

    CubicSpline (with tangents) references = [p0, ..., pN]
                               startTangentVector / endTangentVector
        -> a cubic spline through the control points, clamped so its start and
           end tangents match the supplied direction vectors

The continuous curve is sampled with adaptive subdivision so that the maximum
offset (sagitta) between the output chords and the true spline does not exceed
`max_offset` — the same tolerance-driven contract arc_densify provides for
arcs.

The exact input start/end coordinates are re-used as the polyline endpoints so
adjoining topology shares identical vertices.
"""

from __future__ import annotations

import math
from typing import Sequence

from splines import Natural

Coord = Sequence[float]
Point = tuple[float, ...]

# Guard rail: cap the adaptive-subdivision recursion so a pathological spline
# (e.g. a near-cusp) cannot subdivide without bound.
_MAX_SUBDIVISION_DEPTH = 20


def _coerce(coord: Coord, dims: int) -> Point:
    """
    Return `coord` as a tuple of exactly `dims` floats, truncating extra
    components and padding missing ones with 0.0.
    """
    values = [float(c) for c in coord[:dims]]
    values.extend(0.0 for _ in range(dims - len(values)))
    return tuple(values)


def _output_dims(vertices: list[Coord]) -> int:
    """Return 3 if any vertex carries a Z component, else 2."""
    return 3 if any(len(v) >= 3 for v in vertices) else 2


def tangent_from_references(coords: list[Coord]) -> Point | None:
    """
    Return the tangent direction implied by a tangent-vector's two point
    references: the vector from the first to the second point. The result keeps
    the higher dimensionality of the two points (so a Z component is carried
    through when present).

    Returns None if fewer than two points, or the two points coincide (no
    usable direction).
    """
    if len(coords) < 2:
        return None
    dims = max(len(coords[0]), len(coords[1]))
    a = _coerce(coords[0], dims)
    b = _coerce(coords[1], dims)
    delta = tuple(bi - ai for ai, bi in zip(a, b))
    if all(math.isclose(c, 0.0, abs_tol=1e-15) for c in delta):
        return None
    return delta


def _point_to_segment_distance(p: Point, a: Point, b: Point) -> float:
    """Perpendicular distance from point p to the segment a-b, in N dimensions."""
    ab = [bi - ai for ai, bi in zip(a, b)]
    ap = [pi - ai for pi, ai in zip(p, a)]
    length2 = sum(c * c for c in ab)
    if length2 == 0.0:
        return math.sqrt(sum(c * c for c in ap))
    t = max(0.0, min(1.0, sum(ap_i * ab_i for ap_i, ab_i in zip(ap, ab)) / length2))
    proj = [ai + t * ab_i for ai, ab_i in zip(a, ab)]
    return math.sqrt(sum((pi - pr) ** 2 for pi, pr in zip(p, proj)))


def _flatten(spline, t0: float, t1: float, max_offset: float,
             out: list[Point], depth: int = 0) -> None:
    """
    Adaptively subdivide the spline over [t0, t1], appending vertices to `out`.

    Works in the spline's full (3-D) coordinate space so that deviations in Z
    are measured too. `out` must already end with the curve point at t0. The
    segment is split at its midpoint until the mid curve point lies within
    `max_offset` of the chord joining the segment endpoints, then the endpoint
    at t1 is appended.
    """
    p0 = out[-1]
    p1 = _coerce(spline.evaluate(t1), 3)
    t_mid = 0.5 * (t0 + t1)
    p_mid = _coerce(spline.evaluate(t_mid), 3)

    if depth >= _MAX_SUBDIVISION_DEPTH or \
            _point_to_segment_distance(p_mid, p0, p1) <= max_offset:
        out.append(p1)
        return

    _flatten(spline, t0, t_mid, max_offset, out, depth + 1)
    _flatten(spline, t_mid, t1, max_offset, out, depth + 1)


# Centripetal parameterization (Catmull-Rom alpha). Uniform parameterization
# (alpha=0) of an interpolating cubic spline through unevenly-spaced points
# overshoots badly near closely-spaced points — producing sharp kinks/cusps.
# Centripetal (alpha=0.5) is the standard choice that avoids cusps and
# self-intersections (Yuksel et al. 2011).
DEFAULT_ALPHA = 0.5


def densify_spline(
    vertices: list[Coord],
    max_offset: float,
    start_tangent: Coord | None = None,
    end_tangent: Coord | None = None,
    alpha: float | None = DEFAULT_ALPHA,
) -> list[Point]:
    """
    Interpolate a cubic spline through `vertices` and return its densified
    chord vertices within `max_offset` sagitta tolerance.

    Parameters
    ----------
    vertices:
        Ordered control points the spline passes through (>= 2).
    max_offset:
        Maximum permitted offset between the output chords and the true spline,
        in the coordinate units.
    start_tangent, end_tangent:
        Optional tangent direction vectors at the first and last vertex. When
        both are supplied the spline is clamped to those directions; otherwise
        natural (zero second-derivative) end conditions are used.
    alpha:
        Parameterization exponent (0 = uniform, 0.5 = centripetal, 1 = chordal).
        Defaults to centripetal, which prevents the overshoot/kink artefacts a
        uniform parameterization produces through unevenly-spaced points.

    Returns
    -------
    list[tuple[float, ...]]
        Densified polyline vertices, including the exact supplied start and end
        coordinates. Each vertex is 2-D unless any input vertex carries a Z
        component, in which case all vertices are 3-D (Z interpolated along the
        spline).
    """
    if len(vertices) < 2:
        raise ValueError("A spline needs at least two control points.")
    if max_offset <= 0:
        raise ValueError("max_offset must be greater than zero.")

    out_dims = _output_dims(vertices)

    # Feed the interpolator 3-D vertices regardless of input dimensionality:
    # the `splines` package deprecates 2-D vectors, and a padded Z=0 leaves X/Y
    # unchanged (a natural spline solves each coordinate independently) while
    # letting genuine Z values be interpolated.
    vertices_3d = [list(_coerce(v, 3)) for v in vertices]

    if start_tangent is not None and end_tangent is not None:
        endconditions: object = [list(_coerce(start_tangent, 3)),
                                 list(_coerce(end_tangent, 3))]
    else:
        endconditions = "natural"

    # alpha needs >= 2 distinct points; with exactly 2 it degenerates to a line,
    # so uniform is fine there.
    spline = Natural(vertices_3d, endconditions=endconditions, alpha=alpha)
    grid = spline.grid

    # Flatten in 3-D, then project each vertex down to the output dimensionality.
    raw: list[Point] = [_coerce(spline.evaluate(grid[0]), 3)]
    for i in range(len(grid) - 1):
        _flatten(spline, grid[i], grid[i + 1], max_offset, raw)

    points = [_coerce(p, out_dims) for p in raw]

    # Re-use the supplied endpoints exactly so adjoining topology shares them.
    points[0] = _coerce(vertices[0], out_dims)
    points[-1] = _coerce(vertices[-1], out_dims)
    return points


if __name__ == "__main__":
    # Self-check using the topo-arc spline control points.
    plain = [(10, 10), (14, 12.5), (12.5, 15), (15.5, 16), (18, 15.5),
             (16.5, 18.5), (20, 20)]
    pts = densify_spline(plain, max_offset=0.02)
    print(f"plain spline: {len(pts)} vertices "
          f"(from {len(plain)} control points)")

    tangent = [(10, 10), (14, 12.5), (12.5, 15), (15.5, 16), (20, 20)]
    pts = densify_spline(tangent, max_offset=0.02,
                         start_tangent=(1, 0), end_tangent=(0, 1))
    print(f"clamped spline: {len(pts)} vertices "
          f"(from {len(tangent)} control points)")
