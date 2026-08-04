"""
Tests for topo2geojson.process().

cube-with-void.json is fully self-contained: its points/edges/rings/faces are
all inline JSON, so it needs no TTL. parcel1.json only carries a bare
topology reference list (edge ids with no coordinates anywhere in the file),
so it can only be resolved by loading topoobjects.ttl.
"""
import json
import os
import types

import pytest
from conftest import JSON_OUTPUT_DIR, TESTS_DIR
from topo2geojson import (
    _chain_edges,
    _expand_ttl_glob,
    _merge_namespaces,
    _NamespaceResolvingMap,
    _normalize_namespace_input,
    _prefixes_from_jsonld_context,
    load_ttl_geoms,
    process,
    run_transform,
)

CUBE_FILE = TESTS_DIR / "cube-with-void.json"
PARCEL_FILE = TESTS_DIR / "parcel1.json"
TTL_FILE = TESTS_DIR / "topoobjects.ttl"

# TTL fixture used by the namespace-resolution tests below: two distinct
# features share the local name "Thing" under two different namespaces, so
# resolving by declared namespace (rather than by accidental local-name
# uniqueness) is the only way to land on the intended one.
NAMESPACE_TTL = """
@prefix geojson: <https://purl.org/geojson/vocab#> .
@prefix a: <http://a/> .
@prefix b: <http://b/> .

a:Thing a geojson:Feature ;
    geojson:geometry [ a geojson:Point ;
        geojson:coordinates ( 1.0 2.0 ) ] .

b:Thing a geojson:Feature ;
    geojson:geometry [ a geojson:Point ;
        geojson:coordinates ( 3.0 4.0 ) ] .
"""


@pytest.fixture
def namespace_ttl(tmp_path):
    path = tmp_path / "namespace.ttl"
    path.write_text(NAMESPACE_TTL)
    return path


# TTL fixture reproducing a real-world layout (topo-feature register's
# referenced-objects.ttl): edge features typed geojson:LineString whose
# endpoints are declared via geojson:relatedFeatures (not topo:Edge /
# topo:relatedFeatures), referenced from a JSON Polygon ring by bare local
# name ("LineP1P2" etc., matching the TTL's URI tail).
GEOJSON_EDGES_TTL = """
@prefix geojson: <https://purl.org/geojson/vocab#> .

<http://www.example.com/features/LineP1P2> a geojson:Feature ;
    geojson:topology [ a geojson:LineString ;
            geojson:relatedFeatures ( <http://www.example.com/features/P1> <http://www.example.com/features/P2> ) ] .

<http://www.example.com/features/LineP2P3> a geojson:Feature ;
    geojson:topology [ a geojson:LineString ;
            geojson:relatedFeatures ( <http://www.example.com/features/P2> <http://www.example.com/features/P3> ) ] .

<http://www.example.com/features/LineP3P1> a geojson:Feature ;
    geojson:topology [ a geojson:LineString ;
            geojson:relatedFeatures ( <http://www.example.com/features/P3> <http://www.example.com/features/P1> ) ] .

<http://www.example.com/features/P1> a geojson:Feature ;
    geojson:geometry [ a geojson:Point ; geojson:coordinates ( 10 10 ) ] .

<http://www.example.com/features/P2> a geojson:Feature ;
    geojson:geometry [ a geojson:Point ; geojson:coordinates ( 20 20 ) ] .

<http://www.example.com/features/P3> a geojson:Feature ;
    geojson:geometry [ a geojson:Point ; geojson:coordinates ( 13 17 ) ] .
"""


@pytest.fixture
def geojson_edges_ttl(tmp_path):
    path = tmp_path / "geojson-edges.ttl"
    path.write_text(GEOJSON_EDGES_TTL)
    return path


def _point_feature(ref: str, context=None) -> dict:
    feature = {
        "type": "Feature",
        "id": "check",
        "geometry": None,
        "topology": {"type": "Point", "references": [ref]},
        "properties": {},
    }
    if context is not None:
        feature["@context"] = context
    return feature


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
    ttl_geoms, ttl_coords, ttl_components = load_ttl_geoms([str(TTL_FILE)])

    with PARCEL_FILE.open() as fh:
        output = process(fh, mode="faces", number=None,
                          ttl_geoms=ttl_geoms, ttl_coords=ttl_coords,
                          ttl_components=ttl_components)

    _persist("parcel1-resolved.geojson", output)

    data = json.loads(output)
    assert data["type"] == "Feature"
    assert data["geometry"]["type"] == "Polygon"

    ring = data["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1]      # closed ring
    assert len(ring) == 7           # 6 boundary vertices + closing point


def test_parcel1_edges_mode_decomposes_the_polygon_via_ttl_components():
    """parcel1.json only ever resolves as a single Face/Polygon (there's no
    top-level edges/points collection of its own), so -m edges must
    decompose that polygon down to its constituent edges rather than
    yielding nothing."""
    ttl_geoms, ttl_coords, ttl_components = load_ttl_geoms([str(TTL_FILE)])

    with PARCEL_FILE.open() as fh:
        output = process(fh, mode="edges", number=None,
                          ttl_geoms=ttl_geoms, ttl_coords=ttl_coords,
                          ttl_components=ttl_components)

    _persist("parcel1-edges.geojson", output)

    data = json.loads(output)
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == 6
    for feature in data["features"]:
        assert feature["geometry"]["type"] == "LineString"


def test_parcel1_points_mode_decomposes_the_polygon_via_ttl_components():
    ttl_geoms, ttl_coords, ttl_components = load_ttl_geoms([str(TTL_FILE)])

    with PARCEL_FILE.open() as fh:
        output = process(fh, mode="points", number=None,
                          ttl_geoms=ttl_geoms, ttl_coords=ttl_coords,
                          ttl_components=ttl_components)

    _persist("parcel1-points.geojson", output)

    data = json.loads(output)
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == 6
    for feature in data["features"]:
        assert feature["geometry"]["type"] == "Point"


def test_cube_with_void_shells_and_solids_resolve_to_multipolygon():
    """shells/solids weren't previously processed at all: solids carry their
    directed refs under a "shells" key (not "directed_references"), and the
    type mapping used the invalid GeoJSON type "Solid"."""
    with CUBE_FILE.open() as fh:
        shells_output = process(fh, mode="shells", number=None)
    with CUBE_FILE.open() as fh:
        solids_output = process(fh, mode="solids", number=None)

    _persist("cube-with-void-shells.geojson", shells_output)
    _persist("cube-with-void-solids.geojson", solids_output)

    shells_data = json.loads(shells_output)
    solids_data = json.loads(solids_output)

    shell_features = shells_data["features"] if shells_data["type"] == "FeatureCollection" else [shells_data]
    solid_features = solids_data["features"] if solids_data["type"] == "FeatureCollection" else [solids_data]

    assert shell_features
    assert solid_features
    for feature in shell_features + solid_features:
        assert feature["geometry"]["type"] == "MultiPolygon"


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


# ---------------------------------------------------------------------------
# ttl path resolution (a transform's "ttl" metadata is commonly written
# relative to the building block register root, not wherever the transform
# host's own process cwd happens to be when it invokes the transform)
# ---------------------------------------------------------------------------

def test_expand_ttl_glob_falls_back_to_base_dirs_when_cwd_relative_finds_nothing(tmp_path, monkeypatch):
    register_dir = tmp_path / "register"
    (register_dir / "_sources" / "examples").mkdir(parents=True)
    ttl_path = register_dir / "_sources" / "examples" / "model.ttl"
    ttl_path.write_text("")

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    # Not found relative to cwd...
    assert _expand_ttl_glob("_sources/examples/model.ttl", []) == []
    # ...but found once the register root is offered as a fallback base dir.
    matches = _expand_ttl_glob("_sources/examples/model.ttl", [str(register_dir)])
    assert [os.path.normpath(m) for m in matches] == [str(ttl_path)]


def test_run_transform_resolves_relative_ttl_via_context_working_dir(tmp_path, monkeypatch):
    """A transform host that reports context.working_dir (per the bblocks
    transform-context docs) should have its relative "ttl" metadata path
    resolve against that, even though the host's actual process cwd (here,
    deliberately, an unrelated directory) doesn't contain it — this is what
    broke a topo2geojson transform step wired into a real building block's
    transforms.yaml even though the identical TTL/JSON pair worked fine from
    the CLI (where the user naturally runs from the right directory)."""
    register_dir = tmp_path / "register"
    (register_dir / "_sources" / "examples").mkdir(parents=True)
    ttl_path = register_dir / "_sources" / "examples" / "model.ttl"
    ttl_path.write_text(TTL_FILE.read_text())

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    transform_metadata = types.SimpleNamespace(
        metadata={"mode": "faces", "ttl": ["_sources/examples/model.ttl"]},
        context=types.SimpleNamespace(working_dir=str(register_dir)),
    )

    with PARCEL_FILE.open() as fh:
        output = run_transform(fh.read(), transform_metadata)

    data = json.loads(output)
    assert data["type"] == "Feature"
    assert data["geometry"]["type"] == "Polygon"


# ---------------------------------------------------------------------------
# Namespace/prefix resolution
# ---------------------------------------------------------------------------

def test_normalize_namespace_input_accepts_dict_list_and_bare_uri():
    assert _normalize_namespace_input({"a": "http://a/"}) == {"a": "http://a/"}
    assert _normalize_namespace_input(["a=http://a/", "http://b/"]) == {
        "a": "http://a/", "http://b/": "http://b/",
    }
    assert _normalize_namespace_input(None) == {}
    assert _normalize_namespace_input([]) == {}


def test_prefixes_from_jsonld_context_filters_non_namespace_terms():
    context = [
        "https://example.org/remote-context.jsonld",  # remote URL, skipped (not dereferenced)
        {
            "exns": "http://a/",                        # namespace prefix (trailing "/")
            "name": "http://xmlns.com/foaf/0.1/name",    # full predicate IRI, not a prefix
            "@vocab": "http://ignored/",                 # JSON-LD keyword, skipped
        },
    ]
    assert _prefixes_from_jsonld_context(context) == {"exns": "http://a/"}


def test_merge_namespaces_precedence_context_then_examples_then_fallback():
    """Preference order: the input JSON's own @context, then examples.yaml
    prefixes (via the bblocks transform context), then metadata globals /
    CLI args as the last-resort fallback — a source only fills in prefixes
    a higher-priority source didn't already declare."""
    data = {"@context": {"p": "http://from-context/"}}
    transform_metadata = types.SimpleNamespace(
        metadata={"namespaces": {"p": "http://from-metadata/", "q": "http://from-metadata-only/"}},
        context=types.SimpleNamespace(example={"prefixes": {"p": "http://from-examples/"}}, snippet=None),
    )
    merged = _merge_namespaces(data, transform_metadata,
                                cli_namespaces=["p=http://from-cli/", "r=http://from-cli-only/"])

    assert merged["p"] == "http://from-context/"          # JSON @context wins
    assert merged["q"] == "http://from-metadata-only/"     # metadata fills in what context lacks
    assert merged["r"] == "http://from-cli-only/"          # CLI is the last-resort fallback


def test_namespace_resolving_map_tries_prefix_and_local_name_candidates():
    data = {"http://a/Thing": "A", "http://b/Thing": "B"}
    wrapped = _NamespaceResolvingMap(data, {"exns": "http://a/"})

    assert wrapped.get("http://a/Thing") == "A"            # exact match, no namespace lookup needed
    assert wrapped.get("exns:Thing") == "A"                 # declared prefix + local name
    assert wrapped.get("missing", "default") == "default"   # no candidate matches


def test_polygon_ring_of_geojson_linestring_edges_resolves_via_bare_local_name(geojson_edges_ttl):
    """A JSON Polygon ring referencing edges by their bare local name
    ("LineP1P2" etc.) must chain correctly even though the TTL declares
    those edges as geojson:LineString/geojson:relatedFeatures rather than
    topo:Edge/topo:relatedFeatures."""
    ttl_geoms, ttl_coords, ttl_components = load_ttl_geoms([str(geojson_edges_ttl)])

    feature = {
        "type": "Feature",
        "id": "triangle",
        "geometry": None,
        "topology": {"type": "Polygon", "references": [["LineP1P2", "LineP2P3", "LineP3P1"]]},
        "properties": {},
    }
    output = process(json.dumps(feature), mode="faces", number=None,
                      ttl_geoms=ttl_geoms, ttl_coords=ttl_coords, ttl_components=ttl_components)

    _persist("geojson-edges-triangle.geojson", output)
    data = json.loads(output)
    assert data["type"] == "Feature"
    ring = data["geometry"]["coordinates"][0]
    assert ring == [[10.0, 10.0], [20.0, 20.0], [13.0, 17.0], [10.0, 10.0]]


def test_namespace_resolution_reconciles_unrecognized_prefix(namespace_ttl):
    """A JSON ref like "exns:Thing" whose prefix isn't declared anywhere in
    the TTL is reconciled against the TTL's true URI (http://a/Thing) via an
    explicitly declared namespace — not by accidentally colliding with
    "b:Thing"'s local name (both "a:Thing" and "b:Thing" share the local
    name "Thing")."""
    ttl_geoms, ttl_coords, ttl_components = load_ttl_geoms([str(namespace_ttl)])

    output = process(json.dumps(_point_feature("exns:Thing")), mode="points", number=None,
                      ttl_geoms=ttl_geoms, ttl_coords=ttl_coords, ttl_components=ttl_components,
                      namespaces={"exns": "http://a/"})

    _persist("namespace-unrecognized-prefix.geojson", output)
    data = json.loads(output)
    assert data["geometry"]["coordinates"] == [1.0, 2.0]   # a:Thing, not b:Thing


def test_namespace_resolution_prefers_jsonld_context_over_metadata_fallback(namespace_ttl):
    """The input JSON's own @context takes precedence over a metadata-globals
    namespace declaration for the same prefix."""
    feature = _point_feature("exns:Thing", context={"exns": "http://b/"})
    transform_metadata = types.SimpleNamespace(metadata={
        "mode": "points",
        "ttl": str(namespace_ttl),
        "namespaces": {"exns": "http://a/"},
    })

    output = run_transform(json.dumps(feature), transform_metadata)
    _persist("namespace-jsonld-context-precedence.geojson", output)

    data = json.loads(output)
    assert data["geometry"]["coordinates"] == [3.0, 4.0]   # b:Thing, from the JSON's own @context


def test_namespace_resolution_via_examples_yaml_prefixes_in_transform_context(namespace_ttl):
    """examples.yaml prefixes, exposed by a bblocks transform host via
    transform_metadata.context.example["prefixes"], resolve refs when the
    input JSON has no @context of its own."""
    transform_metadata = types.SimpleNamespace(
        metadata={"mode": "points", "ttl": str(namespace_ttl)},
        context=types.SimpleNamespace(
            example={"prefixes": {"exns": "http://a/"}},
            snippet=None,
        ),
    )

    output = run_transform(json.dumps(_point_feature("exns:Thing")), transform_metadata)
    _persist("namespace-examples-yaml-prefixes.geojson", output)

    data = json.loads(output)
    assert data["geometry"]["coordinates"] == [1.0, 2.0]   # a:Thing


# ---------------------------------------------------------------------------
# Topology shapes found in the topo-* building blocks' real examples that
# previously crashed process() outright instead of resolving (or gracefully
# failing to resolve)
# ---------------------------------------------------------------------------

def test_multilinestring_topology_resolves_to_multilinestring_geometry():
    """topology.type == "MultiLineString" (references = a list of point-id
    lists, one per line) previously fell through to the generic flat-point-
    list fallback and crashed with `unhashable type: 'list'` (e.g.
    topo-line's multilinestring.json)."""
    feature = {
        "type": "Feature",
        "id": "MultiLineP1P2P3",
        "geometry": None,
        "topology": {
            "type": "MultiLineString",
            "references": [["P1", "P2"], ["P2", "P3"]],
        },
        "properties": None,
    }
    points = [
        {"type": "Feature", "id": "P1", "geometry": {"type": "Point", "coordinates": [10.0, 10.0]}},
        {"type": "Feature", "id": "P2", "geometry": {"type": "Point", "coordinates": [20.0, 20.0]}},
        {"type": "Feature", "id": "P3", "geometry": {"type": "Point", "coordinates": [13.0, 17.0]}},
    ]
    data = {"type": "FeatureCollection", "features": [feature], "points": points}

    output = process(json.dumps(data), mode="rings", number=None)
    _persist("multilinestring.geojson", output)

    parsed = json.loads(output)
    assert parsed["features"][0]["geometry"] == {
        "type": "MultiLineString",
        "coordinates": [[[10.0, 10.0], [20.0, 20.0]], [[20.0, 20.0], [13.0, 17.0]]],
    }


def test_solid_directed_reference_missing_ref_key_is_skipped_not_a_crash():
    """A Solid's "shells" entry that inlines a whole {"type": "Shell", ...}
    object (rather than a flat {"ref": ..., "orientation": ...} pointer —
    seen in topo-shell's shell-with-context.json) previously crashed with
    `KeyError: 'ref'` in the top-level solids/shells collection loop; it
    should be skipped with a warning instead."""
    data = {
        "type": "FeatureCollection",
        "features": [],
        "solids": [
            {
                "id": "solid-1",
                "type": "Feature",
                "topology": {
                    "type": "Solid",
                    "shells": [
                        {"type": "Shell", "directed_references": [{"ref": "e1", "orientation": "+"}]},
                    ],
                },
            }
        ],
        "points": [{"type": "Feature", "id": "p1", "geometry": {"type": "Point", "coordinates": [1.0, 1.0]}}],
    }

    output = process(json.dumps(data), mode="solids", number=None)
    parsed = json.loads(output)
    # No crash, and the bad node contributed nothing to the geometry.
    assert parsed["features"][0]["geometry"] == {"type": "MultiPolygon", "coordinates": [[]]}


def test_decompose_polygon_topology_skips_non_linestring_refs_without_crashing():
    """A Solid resolved by chaining two TTL Shells (each a MultiPolygon, not
    a flat point list) previously crashed `_decompose_polygon_topology` with
    `unhashable type: 'list'` when asked for -m points/-m edges, since it
    assumed every referenced geometry was edge-like (a flat point list)."""
    ttl_geoms, ttl_coords, ttl_components = load_ttl_geoms([str(TTL_FILE)])

    feature = {
        "type": "Feature",
        "id": "solid-from-shells",
        "geometry": None,
        "topology": {
            "type": "Solid",
            "directed_references": [
                {"ref": "eg2:44396823", "orientation": "+"},  # a Point, not a Shell — just needs *some* resolvable multi-part ref
            ],
        },
        "properties": None,
    }
    # Force geom_type_str to a decomposition-triggering type by resolving
    # through a ref whose TTL geometry is a Polygon (the parcel) rather than
    # a flat edge, mirroring the real Shell/Solid case.
    feature["topology"]["directed_references"] = [{"ref": "eg2:8446454", "orientation": "+"}]

    output = process(json.dumps(feature), mode="points,edges", number=None,
                      ttl_geoms=ttl_geoms, ttl_coords=ttl_coords, ttl_components=ttl_components)
    # Must not raise; the nested Polygon ref simply can't be decomposed by
    # this fallback and is skipped.
    json.loads(output)


def test_custom_object_polygon_ring_of_edges_is_not_over_nested():
    """A named collection processed via -k/--objects (e.g. "parcels:Polygon")
    whose Feature topology is type "Polygon" with `references` = rings of
    edge IDs (matching real CSDM extended_example.json "parcels" data) must
    chain those edges into a flat ring, the same as an individual top-level
    Feature with the same topology shape does via _resolve_inline_topology.
    Previously the collection-processing loop used the naive _resolve_refs
    recursive substitution instead, which left each edge's own two-point
    LineString nested as a sub-list inside the ring rather than flattened
    into it — one array level too deep."""
    data = {
        "type": "FeatureCollection",
        "features": [],
        "points": [
            {"type": "Feature", "id": "P1", "geometry": {"type": "Point", "coordinates": [10.0, 10.0]}},
            {"type": "Feature", "id": "P2", "geometry": {"type": "Point", "coordinates": [20.0, 20.0]}},
            {"type": "Feature", "id": "P3", "geometry": {"type": "Point", "coordinates": [13.0, 17.0]}},
        ],
        "edges": [
            {"type": "Feature", "id": "e1", "geometry": None,
             "topology": {"type": "Edge", "references": ["P1", "P2"]}},
            {"type": "Feature", "id": "e2", "geometry": None,
             "topology": {"type": "Edge", "references": ["P2", "P3"]}},
            {"type": "Feature", "id": "e3", "geometry": None,
             "topology": {"type": "Edge", "references": ["P3", "P1"]}},
        ],
        "parcels": [
            {
                "type": "Feature",
                "id": "triangle",
                "geometry": None,
                "topology": {"type": "Polygon", "references": [["e1", "e2", "e3"]]},
                "properties": {},
            }
        ],
    }

    output = process(json.dumps(data), mode="parcels", objects="parcels:Polygon", number=None)
    parsed = json.loads(output)
    feature = parsed["features"][0] if parsed.get("type") == "FeatureCollection" else parsed

    assert feature["geometry"]["type"] == "Polygon"
    ring = feature["geometry"]["coordinates"][0]
    assert all(isinstance(pt, list) and len(pt) == 2 and all(isinstance(c, float) for c in pt)
               for pt in ring), f"ring is over-nested: {ring!r}"
    assert ring[0] == ring[-1]   # closed
    assert len(ring) == 4        # 3 distinct vertices + closing point


# ---------------------------------------------------------------------------
# Edge orientation ("direction") auto-detection for Polygon `references`
# ---------------------------------------------------------------------------
# A Polygon's `references` (as opposed to `directed_references`, which
# carries explicit "+"/"-" orientation per edge) gives no orientation hint
# at all — each edge's direction has to be inferred purely from which
# endpoint touches its neighbour.

P1, P2, P3 = [10.0, 10.0], [20.0, 20.0], [13.0, 17.0]


@pytest.mark.parametrize("edge_segs", [
    [[P1, P2], [P2, P3], [P3, P1]],   # all forward
    [[P2, P1], [P3, P2], [P1, P3]],   # all reversed
    [[P1, P2], [P3, P2], [P1, P3]],   # mixed
    [[P2, P1], [P2, P3], [P3, P1]],   # first edge stored backwards relative
                                       # to the other two's traversal order —
                                       # only matching against the *tail* of
                                       # the growing chain misses this and
                                       # falls into the non-adjacent fallback
])
def test_chain_edges_flips_any_misoriented_edge_regardless_of_position(edge_segs):
    ring = _chain_edges(edge_segs)
    assert ring[0] == ring[-1]                       # closed
    assert len(ring) == 4                              # 3 vertices + closing point
    assert set(map(tuple, ring[:-1])) == {tuple(P1), tuple(P2), tuple(P3)}


def test_polygon_references_ring_resolves_correctly_when_first_edge_is_backwards():
    """End-to-end: a Polygon feature whose first ring-edge is stored in the
    "wrong" direction (its own start/end don't align with the following
    edge) must still produce a valid, non-self-intersecting closed ring —
    not the degenerate/duplicated-vertex ring the old tail-only adjacency
    check produced."""
    feature = {
        "type": "Feature",
        "id": "triangle",
        "geometry": None,
        "topology": {
            "type": "Polygon",
            "references": [["e1", "e2", "e3"]],
        },
        "properties": None,
    }
    points = [
        {"type": "Feature", "id": "P1", "geometry": {"type": "Point", "coordinates": P1}},
        {"type": "Feature", "id": "P2", "geometry": {"type": "Point", "coordinates": P2}},
        {"type": "Feature", "id": "P3", "geometry": {"type": "Point", "coordinates": P3}},
    ]
    edges = [
        # e1 stored backwards: natural traversal is P1->P2, but this is P2->P1
        {"type": "Feature", "id": "e1", "geometry": None,
         "topology": {"type": "Edge", "references": ["P2", "P1"]}},
        {"type": "Feature", "id": "e2", "geometry": None,
         "topology": {"type": "Edge", "references": ["P2", "P3"]}},
        {"type": "Feature", "id": "e3", "geometry": None,
         "topology": {"type": "Edge", "references": ["P3", "P1"]}},
    ]
    data = {"type": "FeatureCollection", "features": [feature], "points": points, "edges": edges}

    output = process(json.dumps(data), mode="faces", number=None)
    parsed = json.loads(output)
    feature = parsed["features"][0] if parsed.get("type") == "FeatureCollection" else parsed

    assert feature["geometry"]["type"] == "Polygon"
    ring = feature["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1]
    assert len(ring) == 4
    assert set(map(tuple, ring[:-1])) == {tuple(P1), tuple(P2), tuple(P3)}
