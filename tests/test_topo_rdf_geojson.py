"""Tests for topo_rdf_geojson.load_topo() against tests/topoobjects.ttl."""
import json

import pytest
from conftest import RDF_OUTPUT_DIR, TESTS_DIR
from topo_rdf_geojson import load_topo, load_topo_components

TTL_FILE = TESTS_DIR / "topoobjects.ttl"

# Real-world TTL mixes topo:Edge/topo:relatedFeatures with plain
# geojson:LineString/geojson:relatedFeatures for the same kind of edge
# feature (see _sources/examples/referenced-objects.ttl in the topo-feature
# register) — both predicates must resolve.
GEOJSON_RELATED_FEATURES_TTL = """
@prefix geojson: <https://purl.org/geojson/vocab#> .

<http://www.example.com/features/LineP1P2> a geojson:Feature ;
    geojson:topology [ a geojson:LineString ;
            geojson:relatedFeatures ( <http://www.example.com/features/P1> <http://www.example.com/features/P2> ) ] .

<http://www.example.com/features/P1> a geojson:Feature ;
    geojson:geometry [ a geojson:Point ; geojson:coordinates ( 10 10 ) ] .

<http://www.example.com/features/P2> a geojson:Feature ;
    geojson:geometry [ a geojson:Point ; geojson:coordinates ( 20 20 ) ] .
"""


@pytest.fixture
def geojson_related_features_ttl(tmp_path):
    path = tmp_path / "geojson-related-features.ttl"
    path.write_text(GEOJSON_RELATED_FEATURES_TTL)
    return path


# geojson:Polygon (topo:relatedFeatures = a ring of edge URIs) gives no
# per-edge orientation hint at all, unlike topo:Ring/topo:Face
# (topo:directedReferences carries an explicit topo:orientation "+"/"-").
# ex:e1 is deliberately stored "backwards" (P2 -> P1, not P1 -> P2) relative
# to how e2/e3 connect, to exercise _TopoResolver._chain_edges's direction
# auto-detection.
GJ_POLYGON_BACKWARDS_FIRST_EDGE_TTL = """
@prefix geojson: <https://purl.org/geojson/vocab#> .
@prefix topo: <https://purl.org/geojson/topo#> .
@prefix ex: <http://example.com/> .

ex:triangle a geojson:Feature ;
    geojson:topology [ a geojson:Polygon ;
            topo:relatedFeatures ( ( ex:e1 ex:e2 ex:e3 ) ) ] .

ex:e1 a geojson:Feature ;
    geojson:topology [ a topo:Edge ; topo:relatedFeatures ( ex:P2 ex:P1 ) ] .

ex:e2 a geojson:Feature ;
    geojson:topology [ a topo:Edge ; topo:relatedFeatures ( ex:P2 ex:P3 ) ] .

ex:e3 a geojson:Feature ;
    geojson:topology [ a topo:Edge ; topo:relatedFeatures ( ex:P3 ex:P1 ) ] .

ex:P1 a geojson:Feature ; geojson:geometry [ a geojson:Point ; geojson:coordinates ( 10 10 ) ] .
ex:P2 a geojson:Feature ; geojson:geometry [ a geojson:Point ; geojson:coordinates ( 20 20 ) ] .
ex:P3 a geojson:Feature ; geojson:geometry [ a geojson:Point ; geojson:coordinates ( 13 17 ) ] .
"""


@pytest.fixture
def gj_polygon_backwards_first_edge_ttl(tmp_path):
    path = tmp_path / "gj-polygon-backwards-first-edge.ttl"
    path.write_text(GJ_POLYGON_BACKWARDS_FIRST_EDGE_TTL)
    return path


def test_gj_polygon_ring_closes_correctly_when_first_edge_is_backwards(gj_polygon_backwards_first_edge_ttl):
    """A geojson:Polygon ring's first edge stored in the "wrong" direction
    must still chain into a valid, non-degenerate closed ring — checking
    adjacency only against the tail of the chain built so far (rather than
    both ends) previously corrupted the ring in exactly this case."""
    geoms = load_topo(str(gj_polygon_backwards_first_edge_ttl))

    triangle = geoms["http://example.com/triangle"]
    assert triangle["type"] == "Polygon"

    ring = triangle["coordinates"][0]
    assert ring[0] == ring[-1]                     # closed
    assert len(ring) == 4                           # 3 distinct vertices + closing point
    assert set(map(tuple, ring[:-1])) == {(10.0, 10.0), (20.0, 20.0), (13.0, 17.0)}


def test_load_topo_resolves_edge_using_geojson_related_features_predicate(geojson_related_features_ttl):
    """A geojson:LineString topology node that declares its endpoints via
    geojson:relatedFeatures (rather than topo:relatedFeatures) must still
    resolve — both predicates are used interchangeably in real data."""
    geoms = load_topo(str(geojson_related_features_ttl))

    line = geoms["http://www.example.com/features/LineP1P2"]
    assert line == {"type": "LineString", "coordinates": [[10.0, 10.0], [20.0, 20.0]]}


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


def test_load_topo_components_decomposes_face_to_its_edges_and_points():
    """The parcel (a Face, one ring of 6 edges) should decompose down to its
    6 constituent edges and 6 constituent points, even though load_topo()
    itself only ever exposes the flattened Polygon."""
    components = load_topo_components(str(TTL_FILE))

    comps = components["eg2:8446454"]
    assert len(comps["edges"]) == 6
    assert len(comps["points"]) == 6
    assert all(g["type"] == "LineString" for g in comps["edges"].values())
    assert all(g["type"] == "Point" for g in comps["points"].values())