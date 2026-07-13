"""
Tests for topo2geojson.process().

cube-with-void.json is fully self-contained: its points/edges/rings/faces are
all inline JSON, so it needs no TTL. parcel1.json only carries a bare
topology reference list (edge ids with no coordinates anywhere in the file),
so it can only be resolved by loading topoobjects.ttl.
"""
import json
import types

import pytest
from conftest import JSON_OUTPUT_DIR, TESTS_DIR
from topo2geojson import load_ttl_geoms, process, run_transform

CUBE_FILE = TESTS_DIR / "cube-with-void.json"
PARCEL_FILE = TESTS_DIR / "parcel1.json"
TTL_FILE = TESTS_DIR / "topoobjects.ttl"


def _persist(name: str, geojson_str: str) -> None:
    JSON_OUTPUT_DIR.joinpath(name).write_text(geojson_str)


def test_cube_with_void_faces_are_self_contained_no_ttl_needed():
    with CUBE_FILE.open() as fh:
        output = process(fh, mode="faces", number=None)

    _persist("cube-with-void-faces.geojson", output)

    data = json.loads(output)
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == 12  # 12 boundary faces on the cube-with-void
    for feature in data["features"]:
        assert feature["geometry"]["type"] == "MultiPolygon"


def test_cube_with_void_points_and_edges():
    with CUBE_FILE.open() as fh:
        output = process(fh, mode="points,edges", number=None)

    _persist("cube-with-void-points-edges.geojson", output)

    data = json.loads(output)
    counts = {}
    for feature in data["features"]:
        t = feature["geometry"]["type"]
        counts[t] = counts.get(t, 0) + 1
    assert counts == {"Point": 16, "LineString": 24}


def test_parcel1_without_ttl_cannot_be_resolved():
    """parcel1.json's topology only references edge ids; with no TTL loaded
    there are no coordinates anywhere to resolve them against."""
    with PARCEL_FILE.open() as fh:
        with pytest.raises(ValueError, match="No point geometries found"):
            process(fh, mode="faces", number=None)


def test_parcel1_resolved_via_ttl():
    ttl_geoms, ttl_coords = load_ttl_geoms([str(TTL_FILE)])

    with PARCEL_FILE.open() as fh:
        output = process(fh, mode="faces", number=None,
                          ttl_geoms=ttl_geoms, ttl_coords=ttl_coords)

    _persist("parcel1-resolved.geojson", output)

    data = json.loads(output)
    assert data["type"] == "Feature"
    assert data["geometry"]["type"] == "Polygon"

    ring = data["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1]      # closed ring
    assert len(ring) == 7           # 6 boundary vertices + closing point


def test_reprojects_non_wgs84_input_via_pyproj():
    """Neither fixture declares a GeoJSON `crs` object, so this exercises the
    pyproj-based reprojection path (replacing geopandas) with a synthetic
    input. Expected lon/lat is cube-with-void.json's own paired value for
    this same point: "geometry" is the WGS84 form of "place"'s EPSG:7850
    easting/northing (404685.707, 6471518.197)."""
    feature = {
        "type": "Feature",
        "id": "reproj-check",
        "crs": {"type": "name", "properties": {"name": "EPSG:7850"}},
        "geometry": {"type": "Point", "coordinates": [404685.707, 6471518.197, 16.0]},
        "properties": {},
    }

    output = process(json.dumps(feature), mode="points", number=None)
    _persist("reprojected-point.geojson", output)

    data = json.loads(output)
    lon, lat, elev = data["geometry"]["coordinates"]
    assert lon == pytest.approx(115.99215095371282, abs=1e-9)
    assert lat == pytest.approx(-31.88815772870778, abs=1e-9)
    assert elev == 16.0     # z is untouched by the 2D horizontal transform


def test_run_transform_callable_directly_by_a_host():
    """OGC Building Blocks-style hosts can call run_transform(input_data,
    transform_metadata) directly instead of exec'ing the whole module with
    those names bound as globals."""
    transform_metadata = types.SimpleNamespace(metadata={
        "mode": "faces",
        "ttl": str(TTL_FILE),
    })

    with PARCEL_FILE.open() as fh:
        input_data = fh.read()

    output = run_transform(input_data, transform_metadata)
    _persist("parcel1-run-transform.geojson", output)

    data = json.loads(output)
    assert data["type"] == "Feature"
    assert data["geometry"]["type"] == "Polygon"


def test_run_transform_falls_back_to_module_globals():
    """A host that binds input_data/transform_metadata as module attributes
    (or execs the module with them as globals) can call run_transform() with
    no arguments."""
    import topo2geojson

    topo2geojson.transform_metadata = types.SimpleNamespace(metadata={
        "mode": "faces",
        "ttl": str(TTL_FILE),
    })
    with PARCEL_FILE.open() as fh:
        topo2geojson.input_data = fh.read()

    try:
        output = topo2geojson.run_transform()
    finally:
        del topo2geojson.transform_metadata
        del topo2geojson.input_data

    data = json.loads(output)
    assert data["type"] == "Feature"
    assert data["geometry"]["type"] == "Polygon"


def test_run_transform_requires_input_data_and_transform_metadata():
    with pytest.raises(RuntimeError, match="requires input_data and transform_metadata"):
        run_transform()
