"""
Tests for cubic-spline densification: the spline_geometry bridge module
(built on the `splines` package) and its integration into
topo2geojson.process(densify=True).

Control points below match the topo-arc spline examples (P1, Px1..Px5, P2 in
referenced-objects.ttl).
"""
import json
import math

import pytest
from conftest import JSON_OUTPUT_DIR

from spline_geometry import (
    densify_spline,
    tangent_from_references,
)
from topo2geojson import process

# topo-arc spline control points.
P1 = (10.0, 10.0)
Px1 = (14.0, 12.5)
Px2 = (12.5, 15.0)
Px3 = (15.5, 16.0)
Px4 = (18.0, 15.5)
Px5 = (16.5, 18.5)
P2 = (20.0, 20.0)

PLAIN_SPLINE = [P1, Px1, Px2, Px3, Px4, Px5, P2]
TANGENT_SPLINE = [P1, Px1, Px2, Px3, P2]


def _persist(name: str, geojson_str: str) -> None:
    JSON_OUTPUT_DIR.joinpath(name).write_text(geojson_str)


def _unit(v):
    n = math.hypot(v[0], v[1])
    return (v[0] / n, v[1] / n)


# ---------------------------------------------------------------------------
# spline_geometry unit tests
# ---------------------------------------------------------------------------

def test_densify_spline_preserves_endpoints_and_densifies():
    pts = densify_spline(PLAIN_SPLINE, max_offset=0.02)
    assert pts[0] == P1
    assert pts[-1] == P2
    # A curved spline yields many more vertices than the control points.
    assert len(pts) > len(PLAIN_SPLINE)


def test_densify_spline_passes_through_control_points():
    """A natural cubic spline interpolates (passes through) every control
    point, so each control point lies within max_offset of the polyline."""
    max_offset = 0.02
    pts = densify_spline(PLAIN_SPLINE, max_offset=max_offset)
    for cp in PLAIN_SPLINE:
        best = min(
            _point_to_segment_distance(cp, a, b)
            for a, b in zip(pts, pts[1:])
        )
        assert best <= max_offset + 1e-9


def _point_to_segment_distance(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / length2))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def test_smaller_max_offset_produces_more_vertices():
    coarse = densify_spline(PLAIN_SPLINE, max_offset=0.2)
    fine = densify_spline(PLAIN_SPLINE, max_offset=0.001)
    assert len(fine) > len(coarse)


def test_clamped_tangents_change_the_curve_vs_natural():
    natural = densify_spline(TANGENT_SPLINE, max_offset=0.02)
    clamped = densify_spline(TANGENT_SPLINE, max_offset=0.02,
                             start_tangent=(1, 0), end_tangent=(0, 1))
    # Both still hit the endpoints.
    assert clamped[0] == P1 and clamped[-1] == P2
    # The first step of the clamped curve heads along +x (its clamped start
    # tangent), unlike the natural spline's start direction.
    clamped_dir = _unit((clamped[1][0] - clamped[0][0], clamped[1][1] - clamped[0][1]))
    natural_dir = _unit((natural[1][0] - natural[0][0], natural[1][1] - natural[0][1]))
    assert clamped_dir[0] > 0.9  # nearly +x
    assert clamped_dir != natural_dir


def test_tangent_from_references():
    # PVS=(9,10) -> P1=(10,10) gives direction (+1, 0).
    assert tangent_from_references([(9, 10), (10, 10)]) == (1.0, 0.0)
    # Fewer than two points, or coincident points, give no direction.
    assert tangent_from_references([(1, 1)]) is None
    assert tangent_from_references([(5, 5), (5, 5)]) is None


def test_densify_spline_requires_two_points():
    with pytest.raises(ValueError, match="at least two"):
        densify_spline([P1], max_offset=0.02)


def test_densify_spline_2d_input_stays_2d_without_deprecation_warning():
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        pts = densify_spline(PLAIN_SPLINE, max_offset=0.02)
    assert all(len(p) == 2 for p in pts)


def test_densify_spline_carries_z_through_for_3d_input():
    verts = [(10, 10, 0), (14, 12.5, 2), (12.5, 15, 4), (20, 20, 6)]
    pts = densify_spline(verts, max_offset=0.02)
    # All vertices are 3-D, endpoints (including Z) preserved exactly.
    assert all(len(p) == 3 for p in pts)
    assert pts[0] == (10.0, 10.0, 0.0)
    assert pts[-1] == (20.0, 20.0, 6.0)
    # Z is genuinely interpolated between the endpoint values, not left at 0.
    interior_z = [p[2] for p in pts[1:-1]]
    assert any(z not in (0.0, 6.0) for z in interior_z)
    assert all(-0.5 <= z <= 6.5 for z in interior_z)


def test_densify_spline_3d_xy_matches_2d_projection():
    """Padding Z should not disturb X/Y: a natural spline solves each
    coordinate independently, so the X/Y of a 3-D spline equals the 2-D one."""
    verts2d = [P1, Px1, Px2, Px3, P2]
    verts3d = [(x, y, 5.0) for x, y in verts2d]  # constant Z
    pts2d = densify_spline(verts2d, max_offset=0.02)
    pts3d = densify_spline(verts3d, max_offset=0.02)
    assert len(pts2d) == len(pts3d)
    for (x2, y2), (x3, y3, _z) in zip(pts2d, pts3d):
        assert math.isclose(x2, x3, abs_tol=1e-9)
        assert math.isclose(y2, y3, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# Integration through topo2geojson.process(densify=True)
# ---------------------------------------------------------------------------

def _points_collection(*ids_and_coords):
    return [
        {"type": "Feature", "id": pid,
         "geometry": {"type": "Point", "coordinates": list(c)}}
        for pid, c in ids_and_coords
    ]


def test_process_densifies_cubic_spline():
    ids = [("P1", P1), ("Px1", Px1), ("Px2", Px2), ("Px3", Px3),
           ("Px4", Px4), ("Px5", Px5), ("P2", P2)]
    data = {
        "type": "FeatureCollection",
        "features": _points_collection(*ids) + [{
            "type": "Feature", "id": "spline1", "geometry": None,
            "topology": {"type": "CubicSpline",
                         "references": [i for i, _ in ids]},
        }],
    }
    output = process(json.dumps(data), mode="edges", densify=True, max_offset=0.02)
    _persist("spline-densified.geojson", output)
    result = json.loads(output)
    spline = [f for f in result["features"] if f.get("id") == "spline1"][0]
    assert spline["geometry"]["type"] == "LineString"
    # Interpolated to many vertices, not just the 7 control points.
    assert len(spline["geometry"]["coordinates"]) > 7


def test_process_densifies_cubic_spline_with_tangents():
    ids = [("P1", P1), ("Px1", Px1), ("Px2", Px2), ("Px3", Px3), ("P2", P2),
           ("PVS", (9.0, 10.0)), ("PVE", (20.0, 21.0))]
    data = {
        "type": "FeatureCollection",
        "features": _points_collection(*ids) + [{
            "type": "Feature", "id": "spline2", "geometry": None,
            "topology": {
                "type": "CubicSpline",
                "references": ["P1", "Px1", "Px2", "Px3", "P2"],
                "startTangentVector": {"references": ["PVS", "P1"]},
                "endTangentVector": {"references": ["P2", "PVE"]},
            },
        }],
    }
    output = process(json.dumps(data), mode="edges", densify=True, max_offset=0.02)
    _persist("spline-with-tangents-densified.geojson", output)
    result = json.loads(output)
    spline = [f for f in result["features"] if f.get("id") == "spline2"][0]
    coords = spline["geometry"]["coordinates"]
    assert coords[0] == list(P1)
    assert coords[-1] == list(P2)
    assert len(coords) > 5


def test_process_fits_cubic_spline_even_when_densify_off():
    """A CubicSpline is always fitted as a curve, regardless of the densify
    flag — unlike arcs, which chord unless densification is requested."""
    ids = [("P1", P1), ("Px1", Px1), ("Px2", Px2), ("Px3", Px3),
           ("Px4", Px4), ("Px5", Px5), ("P2", P2)]
    data = {
        "type": "FeatureCollection",
        "features": _points_collection(*ids) + [{
            "type": "Feature", "id": "spline1", "geometry": None,
            "topology": {"type": "CubicSpline",
                         "references": [i for i, _ in ids]},
        }],
    }
    output = process(json.dumps(data), mode="edges", densify=False)
    result = json.loads(output)
    spline = [f for f in result["features"] if f.get("id") == "spline1"][0]
    assert spline["geometry"]["type"] == "LineString"
    # Fitted curve has far more vertices than the 7 control points.
    assert len(spline["geometry"]["coordinates"]) > 7


def test_process_fits_cubic_spline_with_tangents_when_densify_off():
    ids = [("P1", P1), ("Px1", Px1), ("Px2", Px2), ("Px3", Px3), ("P2", P2),
           ("PVS", (9.0, 10.0)), ("PVE", (20.0, 21.0))]
    data = {
        "type": "FeatureCollection",
        "features": _points_collection(*ids) + [{
            "type": "Feature", "id": "spline2", "geometry": None,
            "topology": {
                "type": "CubicSpline",
                "references": ["P1", "Px1", "Px2", "Px3", "P2"],
                "startTangentVector": {"references": ["PVS", "P1"]},
                "endTangentVector": {"references": ["P2", "PVE"]},
            },
        }],
    }
    # With densify off but a clamped spline, the fitted curve differs from the
    # unclamped (natural) fit — confirming tangents are applied unconditionally.
    clamped = json.loads(process(json.dumps(data), mode="edges", densify=False))
    clamped_coords = [f for f in clamped["features"]
                      if f.get("id") == "spline2"][0]["geometry"]["coordinates"]

    data["features"][-1]["topology"].pop("startTangentVector")
    data["features"][-1]["topology"].pop("endTangentVector")
    natural = json.loads(process(json.dumps(data), mode="edges", densify=False))
    natural_coords = [f for f in natural["features"]
                      if f.get("id") == "spline2"][0]["geometry"]["coordinates"]

    assert clamped_coords[0] == list(P1)
    assert clamped_coords[-1] == list(P2)
    assert len(clamped_coords) > 5
    assert clamped_coords != natural_coords
