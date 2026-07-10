"""Tests for topo_rdf_geojson.load_topo() against tests/topoobjects.ttl."""
import json

from conftest import RDF_OUTPUT_DIR, TESTS_DIR
from topo_rdf_geojson import load_topo

TTL_FILE = TESTS_DIR / "topoobjects.ttl"


def test_load_topo_resolves_edge_to_linestring():
    geoms = load_topo(str(TTL_FILE))

    edge = geoms["eg2:l535242"]
    assert edge["type"] == "LineString"
    assert len(edge["coordinates"]) == 2


def test_load_topo_resolves_parcel_polygon_by_chaining_edges():
    geoms = load_topo(str(TTL_FILE))

    parcel = geoms["eg2:8446454"]
    assert parcel["type"] == "Polygon"

    ring = parcel["coordinates"][0]
    assert ring[0] == ring[-1]          # closed ring
    assert len(ring) == 7               # 6 boundary vertices + closing point

    RDF_OUTPUT_DIR.joinpath("parcel-8446454.geojson").write_text(
        json.dumps({"type": "Feature", "id": "8446454", "geometry": parcel}, indent=2)
    )


def test_load_topo_indexes_by_both_uri_and_qname():
    geoms = load_topo(str(TTL_FILE))

    uri_key = "http://csdm-example-surveys/DP-572532/8446454"
    assert geoms[uri_key] is geoms["eg2:8446454"]