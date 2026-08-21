"""Tests for topo_validator.rdf_loader.from_rdf_graph().

Builds a small tetrahedron topology directly in Turtle (geojson-topo
vocabulary: topo:Edge / topo:Ring / topo:Face / topo:Shell / topo:Solid) and
checks that from_rdf_graph() reproduces the expected structural TopologyData
-- and that a JSON-LD serialization of the exact same graph produces an
identical result, proving the loader is format-agnostic.
"""

import rdflib
import pytest

from topo_validator.merge import merge_topology
from topo_validator.model import errors_only
from topo_validator.rdf_loader import from_rdf_graph
from topo_validator.validator import validate_topology

TETRAHEDRON_TTL = """
@prefix geojson: <https://purl.org/geojson/vocab#> .
@prefix topo: <https://purl.org/geojson/topo#> .
@prefix ex: <http://example.com/tet/> .

ex:p0 a geojson:Feature ; geojson:geometry [ a geojson:Point ; geojson:coordinates ( 0.0 0.0 0.0 ) ] .
ex:p1 a geojson:Feature ; geojson:geometry [ a geojson:Point ; geojson:coordinates ( 1.0 0.0 0.0 ) ] .
ex:p2 a geojson:Feature ; geojson:geometry [ a geojson:Point ; geojson:coordinates ( 0.0 1.0 0.0 ) ] .
ex:p3 a geojson:Feature ; geojson:geometry [ a geojson:Point ; geojson:coordinates ( 0.0 0.0 1.0 ) ] .

ex:e01 a geojson:Feature ; geojson:topology [ a topo:Edge ; topo:relatedFeatures ( ex:p0 ex:p1 ) ] .
ex:e02 a geojson:Feature ; geojson:topology [ a topo:Edge ; topo:relatedFeatures ( ex:p0 ex:p2 ) ] .
ex:e03 a geojson:Feature ; geojson:topology [ a topo:Edge ; topo:relatedFeatures ( ex:p0 ex:p3 ) ] .
ex:e12 a geojson:Feature ; geojson:topology [ a topo:Edge ; topo:relatedFeatures ( ex:p1 ex:p2 ) ] .
ex:e13 a geojson:Feature ; geojson:topology [ a topo:Edge ; topo:relatedFeatures ( ex:p1 ex:p3 ) ] .
ex:e23 a geojson:Feature ; geojson:topology [ a topo:Edge ; topo:relatedFeatures ( ex:p2 ex:p3 ) ] .

ex:R012 a geojson:Feature ; geojson:topology [ a topo:Ring ; topo:directedReferences (
    [ topo:ref "ex:e01" ; topo:orientation "+" ]
    [ topo:ref "ex:e12" ; topo:orientation "+" ]
    [ topo:ref "ex:e02" ; topo:orientation "-" ]
) ] .
ex:R013 a geojson:Feature ; geojson:topology [ a topo:Ring ; topo:directedReferences (
    [ topo:ref "ex:e01" ; topo:orientation "+" ]
    [ topo:ref "ex:e13" ; topo:orientation "+" ]
    [ topo:ref "ex:e03" ; topo:orientation "-" ]
) ] .
ex:R023 a geojson:Feature ; geojson:topology [ a topo:Ring ; topo:directedReferences (
    [ topo:ref "ex:e02" ; topo:orientation "+" ]
    [ topo:ref "ex:e23" ; topo:orientation "+" ]
    [ topo:ref "ex:e03" ; topo:orientation "-" ]
) ] .
ex:R123 a geojson:Feature ; geojson:topology [ a topo:Ring ; topo:directedReferences (
    [ topo:ref "ex:e12" ; topo:orientation "+" ]
    [ topo:ref "ex:e23" ; topo:orientation "+" ]
    [ topo:ref "ex:e13" ; topo:orientation "-" ]
) ] .

ex:F012 a geojson:Feature ; geojson:topology [ a topo:Face ; topo:directedReferences (
    [ topo:ref "ex:R012" ; topo:orientation "+" ] ) ] .
ex:F013 a geojson:Feature ; geojson:topology [ a topo:Face ; topo:directedReferences (
    [ topo:ref "ex:R013" ; topo:orientation "+" ] ) ] .
ex:F023 a geojson:Feature ; geojson:topology [ a topo:Face ; topo:directedReferences (
    [ topo:ref "ex:R023" ; topo:orientation "+" ] ) ] .
ex:F123 a geojson:Feature ; geojson:topology [ a topo:Face ; topo:directedReferences (
    [ topo:ref "ex:R123" ; topo:orientation "+" ] ) ] .

ex:S a geojson:Feature ; geojson:topology [ a topo:Shell ; topo:directedReferences (
    [ topo:ref "ex:F012" ; topo:orientation "+" ]
    [ topo:ref "ex:F013" ; topo:orientation "+" ]
    [ topo:ref "ex:F023" ; topo:orientation "+" ]
    [ topo:ref "ex:F123" ; topo:orientation "+" ]
) ] .

ex:Tet a geojson:Feature ; geojson:topology [ a topo:Solid ; topo:shells (
    [ topo:ref "ex:S" ; topo:orientation "+" ]
) ] .
"""


@pytest.fixture
def tetrahedron_ttl(tmp_path):
    path = tmp_path / "tetrahedron.ttl"
    path.write_text(TETRAHEDRON_TTL, encoding="utf-8")
    return path


def _sorted_ids(records):
    return sorted(r["id"] for r in records)


def test_from_rdf_graph_builds_expected_structural_counts(tetrahedron_ttl):
    topo = from_rdf_graph(str(tetrahedron_ttl))

    assert len(topo["points"]) == 4
    assert len(topo["curves"]) == 6
    assert len(topo["solids"]) == 1

    solid = topo["solids"][0]
    assert solid["id"] == "ex:Tet"
    assert len(solid["shells"]) == 1
    assert solid["shells"][0]["type"] == "outer"
    assert sorted(solid["faces"]) == ["ex:F012", "ex:F013", "ex:F023", "ex:F123"]


def test_from_rdf_graph_curve_vertices_resolve_to_point_ids(tetrahedron_ttl):
    topo = from_rdf_graph(str(tetrahedron_ttl))
    curves_by_id = {c["id"]: c for c in topo["curves"]}

    assert curves_by_id["ex:e01"]["vertices"] == ["ex:p0", "ex:p1"]
    assert curves_by_id["ex:e23"]["vertices"] == ["ex:p2", "ex:p3"]


def test_from_rdf_graph_produces_no_reference_or_structural_issues(tetrahedron_ttl):
    """Every point/curve/ring/face/shell reference in the fixture resolves to
    something that actually exists, so no *_REFERENCE / structural issue
    codes should appear. (The fixture's edge orientations weren't chosen to
    be geometrically consistent -- SHARED_EDGE_SAME_ORIENTATION and the
    RDF-loader's known volume-property gap (ZERO_OR_NEGATIVE_VOLUME, see the
    module docstring's "Limitations" section) are expected and out of scope
    for this check, which is about reference integrity, not full rule
    conformance.)"""
    topo = from_rdf_graph(str(tetrahedron_ttl))
    issues = validate_topology(topo)

    reference_or_structural = [
        i for i in issues
        if "REFERENCE" in i["code"] or i["code"].startswith(("INVALID_", "MISSING_"))
    ]
    assert reference_or_structural == []


def test_from_rdf_graph_matches_between_turtle_and_jsonld(tetrahedron_ttl):
    """The same graph, serialized as JSON-LD (with its own @context, as any
    real JSON-LD topology document would carry) instead of Turtle, must
    produce an identical structural TopologyData -- proving the loader is
    format-agnostic (Turtle or equivalent JSON-LD), not just Turtle-shaped."""
    g = rdflib.Graph()
    g.parse(str(tetrahedron_ttl), format="turtle")
    context = {prefix: str(ns) for prefix, ns in g.namespaces() if prefix}
    jsonld_text = g.serialize(format="json-ld", context=context)

    ttl_topo = from_rdf_graph(str(tetrahedron_ttl))
    jsonld_topo = from_rdf_graph(jsonld_text)

    assert _sorted_ids(ttl_topo["points"]) == _sorted_ids(jsonld_topo["points"])
    assert _sorted_ids(ttl_topo["curves"]) == _sorted_ids(jsonld_topo["curves"])
    assert _sorted_ids(ttl_topo["solids"]) == _sorted_ids(jsonld_topo["solids"])

    ttl_curves = {c["id"]: c["vertices"] for c in ttl_topo["curves"]}
    jsonld_curves = {c["id"]: c["vertices"] for c in jsonld_topo["curves"]}
    assert ttl_curves == jsonld_curves


def test_merge_topology_combines_rdf_referenced_objects_with_inline_overrides(tetrahedron_ttl):
    """A curve/point defined inline (e.g. from CSDM JSON) with the same id as
    an RDF-referenced one should win the merge -- merge_topology's documented
    last-writer-wins contract."""
    rdf_topo = from_rdf_graph(str(tetrahedron_ttl))
    inline_override = {
        "points": [{"id": "ex:p0", "coordinates": [9.0, 9.0, 9.0]}],
        "curves": [],
        "surfaces": [],
        "solids": [],
    }

    merged = merge_topology(rdf_topo, inline_override)

    assert len(merged["points"]) == 4  # still 4 distinct point ids
    overridden = next(p for p in merged["points"] if p["id"] == "ex:p0")
    assert overridden["coordinates"] == [9.0, 9.0, 9.0]
