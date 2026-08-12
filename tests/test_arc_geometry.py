"""
Tests for arc densification: the arc_geometry bridge module and its
integration into topo2geojson.process(densify=True).

The referenced points below match the topo-arc register's
referenced-objects.ttl (P1=(10,10), P2=(20,20), P3=(13,17), PC=(10,20)),
so the self-contained fixtures here exercise the same geometry as the
building block's own examples.
"""
import json
import math

import pytest
from conftest import JSON_OUTPUT_DIR, TESTS_DIR

from arc_geometry import (
    arc_topology_to_geometry,
    chord_centre,
    circumcentre,
    densify_full_circle,
    three_point_orientation,
)
from topo2geojson import process

# Referenced points shared by the topo-arc examples.
P1 = (10.0, 10.0)
P2 = (20.0, 20.0)
P3 = (13.0, 17.0)
PC = (10.0, 20.0)

# Path to the topo-arc building block in the sibling topo-feature register,
# used by the tests that run over the real example files.
TOPO_FEATURE = TESTS_DIR.parent.parent / "topo-feature"
TOPO_ARC_EXAMPLES = TOPO_FEATURE / "_sources" / "features" / "topo-arc" / "examples"
REFERENCED_OBJECTS_TTL = TOPO_FEATURE / "_sources" / "examples" / "referenced-objects.ttl"


def _persist(name: str, geojson_str: str) -> None:
    JSON_OUTPUT_DIR.joinpath(name).write_text(geojson_str)


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _point_to_polyline_distance(p, coords):
    """Minimum perpendicular distance from point p to a polyline `coords`."""
    best = math.inf
    for a, b in zip(coords, coords[1:]):
        ax, ay = a
        bx, by = b
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        if length2 == 0:
            t = 0.0
        else:
            t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / length2))
        best = min(best, math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy)))
    return best


# ---------------------------------------------------------------------------
# arc_geometry unit tests
# ---------------------------------------------------------------------------

def test_circumcentre_is_equidistant_from_all_three_points():
    centre = circumcentre(P1, P3, P2)
    r1, r2, r3 = _dist(centre, P1), _dist(centre, P2), _dist(centre, P3)
    assert math.isclose(r1, r2, abs_tol=1e-9)
    assert math.isclose(r1, r3, abs_tol=1e-9)


def test_circumcentre_rejects_collinear_points():
    with pytest.raises(ValueError, match="collinear"):
        circumcentre((0, 0), (1, 1), (2, 2))


def test_three_point_orientation_matches_turn_direction():
    # P1 -> P3 -> P2 sweeps clockwise about the circumcentre (P3 sits left of
    # the P1->P2 chord, so the arc through it turns clockwise).
    assert three_point_orientation(P1, P3, P2) == "cw"
    # Mirror the mid point to the other side of the chord -> counter-clockwise.
    assert three_point_orientation(P1, (17, 13), P2) == "ccw"


def test_chord_centre_lies_at_radius_from_both_endpoints():
    radius = 105.438
    centre = chord_centre(P1, P2, radius, "cw")
    assert math.isclose(_dist(centre, P1), radius, abs_tol=1e-6)
    assert math.isclose(_dist(centre, P2), radius, abs_tol=1e-6)


def test_chord_centre_opposite_orientations_are_on_opposite_sides():
    cw = chord_centre(P1, P2, 105.438, "cw")
    ccw = chord_centre(P1, P2, 105.438, "ccw")
    mid = ((P1[0] + P2[0]) / 2, (P1[1] + P2[1]) / 2)
    # The two centres sit on opposite sides of the chord midpoint.
    assert (cw[0] - mid[0]) * (ccw[0] - mid[0]) < 0


def test_chord_centre_rejects_radius_smaller_than_half_chord():
    with pytest.raises(ValueError, match="too small"):
        chord_centre(P1, P2, 1.0, "cw")


def test_densify_full_circle_vertices_are_on_the_circle_and_closed():
    ring = densify_full_circle(PC, 10.0, max_offset=0.02)
    assert ring[0] == ring[-1]  # closed
    assert len(ring) >= 4
    for x, y in ring:
        assert math.isclose(math.hypot(x - PC[0], y - PC[1]), 10.0, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# arc_topology_to_geometry: one case per topology type
# ---------------------------------------------------------------------------

def test_arc_three_point_passes_through_mid_within_tolerance():
    geom = arc_topology_to_geometry({"type": "Arc"}, [P1, P3, P2], max_offset=0.02)
    assert geom["type"] == "LineString"
    coords = geom["coordinates"]
    # Exact endpoints preserved for topology sharing.
    assert coords[0] == list(P1)
    assert coords[-1] == list(P2)
    # The defining mid point lies on the true arc, so within the sagitta.
    assert _point_to_polyline_distance(P3, coords) <= 0.02 + 1e-9


def test_arc_with_center_endpoints_preserved_and_curve_bulges():
    geom = arc_topology_to_geometry(
        {"type": "ArcWithCenter", "orientation": "ccw"}, [P1, P2, PC], max_offset=0.02
    )
    coords = geom["coordinates"]
    assert coords[0] == list(P1)
    assert coords[-1] == list(P2)
    # Every vertex is at the arc radius from the centre.
    radius = _dist(PC, P1)
    for xy in coords:
        assert math.isclose(_dist(PC, xy), radius, abs_tol=1e-6)


def test_arc_by_chord_vertices_lie_on_the_radius_circle():
    radius = 105.438
    geom = arc_topology_to_geometry(
        {"type": "ArcByChord", "radius": radius, "orientation": "cw"},
        [P1, P2], max_offset=0.02,
    )
    coords = geom["coordinates"]
    centre = chord_centre(P1, P2, radius, "cw")
    for xy in coords:
        assert math.isclose(_dist(centre, xy), radius, abs_tol=1e-4)


def test_circle_by_center_produces_closed_polygon():
    geom = arc_topology_to_geometry(
        {"type": "CircleByCenter", "radius": 10}, [PC], max_offset=0.02
    )
    assert geom["type"] == "Polygon"
    ring = geom["coordinates"][0]
    assert ring[0] == ring[-1]


def test_smaller_max_offset_produces_more_vertices():
    coarse = arc_topology_to_geometry(
        {"type": "ArcWithCenter", "orientation": "ccw"}, [P1, P2, PC], max_offset=0.1
    )
    fine = arc_topology_to_geometry(
        {"type": "ArcWithCenter", "orientation": "ccw"}, [P1, P2, PC], max_offset=0.001
    )
    assert len(fine["coordinates"]) > len(coarse["coordinates"])


def test_non_arc_topology_returns_none():
    assert arc_topology_to_geometry(
        {"type": "CubicSpline"}, [P1, P2, P3], max_offset=0.02
    ) is None


# ---------------------------------------------------------------------------
# Integration through topo2geojson.process(densify=True)
# ---------------------------------------------------------------------------

def _points_collection(*ids_and_coords):
    return [
        {"type": "Feature", "id": pid, "geometry": {"type": "Point", "coordinates": list(c)}}
        for pid, c in ids_and_coords
    ]


def _feature_collection(arc_feature):
    return {
        "type": "FeatureCollection",
        "features": _points_collection(
            ("P1", P1), ("P2", P2), ("P3", P3), ("PC", PC)
        ) + [arc_feature],
    }


def test_process_densifies_arc_with_center_inline_points():
    data = _feature_collection({
        "type": "Feature",
        "id": "arc1",
        "geometry": None,
        "topology": {"type": "ArcWithCenter", "references": ["P1", "P2", "PC"],
                     "orientation": "ccw"},
    })
    output = process(json.dumps(data), mode="edges,faces", densify=True, max_offset=0.02)
    _persist("arc-with-center-densified.geojson", output)
    result = json.loads(output)
    arc = [f for f in result["features"] if f.get("id") == "arc1"][0]
    assert arc["geometry"]["type"] == "LineString"
    assert len(arc["geometry"]["coordinates"]) > 2  # densified, not a bare chord


def test_process_densify_off_does_not_produce_curved_arc():
    """Without densify the arc is not turned into a densified curve."""
    data = _feature_collection({
        "type": "Feature",
        "id": "arc1",
        "geometry": None,
        "topology": {"type": "ArcWithCenter", "references": ["P1", "P2", "PC"],
                     "orientation": "ccw"},
    })
    output = process(json.dumps(data), mode="edges,faces", densify=False)
    result = json.loads(output)
    arcs = [f for f in result.get("features", []) if f.get("id") == "arc1"]
    # Default path yields at most a straight chord (<= 3 pts), never a dense arc.
    if arcs:
        assert len(arcs[0]["geometry"]["coordinates"]) <= 3


def test_process_densifies_circle_to_polygon():
    data = _feature_collection({
        "type": "Feature",
        "id": "circle1",
        "geometry": None,
        "topology": {"type": "CircleByCenter", "references": ["PC"], "radius": 10},
    })
    output = process(json.dumps(data), mode="edges,faces", densify=True, max_offset=0.02)
    _persist("circle-densified.geojson", output)
    result = json.loads(output)
    circle = [f for f in result["features"] if f.get("id") == "circle1"][0]
    assert circle["geometry"]["type"] == "Polygon"
    ring = circle["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1]


def test_arc_by_chord_radius_from_feature_property():
    """ArcByChord radius may live on the feature (property) rather than on the
    topology block itself (per the topo-arc schema)."""
    data = _feature_collection({
        "type": "Feature",
        "id": "chord1",
        "geometry": None,
        "topology": {"type": "ArcByChord", "references": ["P1", "P2"],
                     "orientation": "cw"},
        "properties": {"radius": 105.438},  # radius only on the feature property
    })
    output = process(json.dumps(data), mode="edges,faces", densify=True, max_offset=0.02)
    result = json.loads(output)
    chord = [f for f in result["features"] if f.get("id") == "chord1"][0]
    assert chord["geometry"]["type"] == "LineString"
    assert len(chord["geometry"]["coordinates"]) >= 2


def test_arc_topology_to_geometry_uses_feature_radius_fallback():
    # No radius on the topology block -> falls back to feature_radius argument.
    geom = arc_topology_to_geometry(
        {"type": "ArcByChord", "orientation": "cw"}, [P1, P2],
        max_offset=0.02, feature_radius=105.438,
    )
    assert geom is not None and geom["type"] == "LineString"
    # Circle likewise.
    geom = arc_topology_to_geometry(
        {"type": "CircleByCenter"}, [PC], max_offset=0.02, feature_radius=10,
    )
    assert geom is not None and geom["type"] == "Polygon"


def test_arc_by_chord_without_any_radius_returns_none():
    assert arc_topology_to_geometry(
        {"type": "ArcByChord", "orientation": "cw"}, [P1, P2], max_offset=0.02,
    ) is None


def test_arc_with_center_tolerates_survey_radius_noise():
    """Real ArcWithCenter survey data rarely has start/end exactly equidistant
    from the supplied centre (mimicking the extended-example arc 152, whose
    radii were 248.0445 vs 248.0452). Such sub-mm noise must not be rejected."""
    centre = (0.0, 0.0)
    start = (248.0445, 0.0)                                  # radius 248.0445
    ang = math.radians(15.0)
    end = (248.0452 * math.cos(ang), 248.0452 * math.sin(ang))  # radius 248.0452
    geom = arc_topology_to_geometry(
        {"type": "ArcWithCenter", "orientation": "ccw"}, [start, end, centre],
        max_offset=0.02,
    )
    assert geom is not None
    assert geom["type"] == "LineString"
    assert len(geom["coordinates"]) > 2  # actually densified


def test_arc_with_center_still_rejects_gross_radius_mismatch():
    """A point grossly off the circle (wrong reference) is still rejected."""
    centre = (0.0, 0.0)
    start = (248.0, 0.0)
    end = (300.0, 0.0)  # ~52 m off — clearly not the same circle
    with pytest.raises(ValueError, match="same circle"):
        arc_topology_to_geometry(
            {"type": "ArcWithCenter", "orientation": "ccw"}, [start, end, centre],
            max_offset=0.02,
        )


# ---------------------------------------------------------------------------
# Run over the real topo-arc example files (skipped if the register isn't
# checked out alongside this repo).
# ---------------------------------------------------------------------------

REAL_EXAMPLES_AVAILABLE = TOPO_ARC_EXAMPLES.is_dir() and REFERENCED_OBJECTS_TTL.is_file()

EXPECTED_GEOMETRY = {
    "arc_by_center": "LineString",
    "arc": "LineString",
    "arc_chord": "LineString",
    "circle": "Polygon",
    "spline": "LineString",
    "spline_with_tangents": "LineString",
}


@pytest.mark.skipif(not REAL_EXAMPLES_AVAILABLE,
                    reason="topo-feature register not checked out alongside topo-functions")
@pytest.mark.parametrize("name,expected_type", sorted(EXPECTED_GEOMETRY.items()))
def test_densify_over_real_topo_arc_examples(name, expected_type):
    from topo2geojson import load_ttl_geoms

    ttl_geoms, ttl_coords, ttl_components = load_ttl_geoms([str(REFERENCED_OBJECTS_TTL)])
    with open(TOPO_ARC_EXAMPLES / f"{name}.json") as fh:
        output = process(fh, mode="edges,faces", ttl_geoms=ttl_geoms,
                         ttl_coords=ttl_coords, ttl_components=ttl_components,
                         densify=True, max_offset=0.02)
    _persist(f"{name}-densified.geojson", output)
    result = json.loads(output)
    geom = result.get("geometry") or result["features"][0]["geometry"]
    assert geom["type"] == expected_type
