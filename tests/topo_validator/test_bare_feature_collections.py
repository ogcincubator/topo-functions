"""Tests for `_iter_features` (and its dependents) handling bare Features
directly under a collection key, not just FeatureCollection wrappers.

Per the `topo-feature-multi-collection` schema, each of `points`/`edges`/
`rings`/`faces`/`shells`/`solids`/`parcels` is an array whose items are
`oneOf` a bare Feature directly, or a FeatureCollection wrapper holding a
nested `features` array. `_iter_features` previously only implemented the
wrapper shape -- a document using bare Features (as real `topo-feature`
examples do, e.g. `topo-solid/examples/solid-with-context.json`) silently
produced zero points/edges/rings/faces/shells/solids, while still being
correctly recognized as topology data by the validator plugin (which checks
only for the presence of the collection keys) -- surfacing as a plugin
report of "Validation passed (points: 0, edges: 0, ...)" for a
perfectly valid, non-empty document.
"""

from __future__ import annotations

import json
import types
from typing import Any

from topo_validator.loader import (
    _build_parcel_surfaces,
    _iter_features,
    count_features,
    from_csdm_json,
)
from topo_validator.plugin import TopoValidatorPlugin


def test_iter_features_yields_bare_features_directly():
    data = {
        "solids": [
            {"id": "s1", "type": "Feature", "topology": {"type": "Solid"}},
            {"id": "s2", "type": "Feature", "topology": {"type": "Solid"}},
        ]
    }

    assert [f["id"] for f in _iter_features(data, "solids")] == ["s1", "s2"]


def test_iter_features_still_handles_feature_collection_wrappers():
    data = {
        "solids": [
            {
                "type": "FeatureCollection",
                "featureType": "Solid",
                "features": [
                    {"id": "s1", "type": "Feature", "topology": {"type": "Solid"}},
                    {"id": "s2", "type": "Feature", "topology": {"type": "Solid"}},
                ],
            }
        ]
    }

    assert [f["id"] for f in _iter_features(data, "solids")] == ["s1", "s2"]


def test_iter_features_handles_a_mix_of_bare_and_wrapped_entries():
    """A document may mix styles across collections, or even within one --
    both must be found."""
    data = {
        "solids": [
            {"id": "s1", "type": "Feature"},
            {
                "type": "FeatureCollection",
                "features": [{"id": "s2", "type": "Feature"}],
            },
            {"id": "s3", "type": "Feature"},
        ]
    }

    assert [f["id"] for f in _iter_features(data, "solids")] == ["s1", "s2", "s3"]


def test_count_features_counts_bare_feature_collections():
    """The exact symptom reported: the validator plugin's pass-report calls
    `count_features`, which must reflect real content, not zero, for a
    document using bare Features."""
    data: dict[str, Any] = {
        "points": [{"id": "p1", "type": "Feature"}, {"id": "p2", "type": "Feature"}],
        "edges": [{"id": "e1", "type": "Feature"}],
        "rings": [{"id": "r1", "type": "Feature"}],
        "faces": [{"id": "f1", "type": "Feature"}],
        "shells": [{"id": "sh1", "type": "Feature"}],
        "solids": [{"id": "sol1", "type": "Feature"}],
    }

    assert count_features(data) == {
        "points": 2,
        "edges": 1,
        "rings": 1,
        "faces": 1,
        "shells": 1,
        "solids": 1,
    }


# ---------------------------------------------------------------------------
# End-to-end: from_csdm_json actually resolves a bare-Feature document into
# real internal records, not just counts.
# ---------------------------------------------------------------------------

_BARE_MINIMAL_TOPOLOGY = {
    "points": [
        {"id": "p1", "type": "Feature", "geometry": {"type": "Point", "coordinates": [0.0, 0.0, 0.0]}, "properties": {}},
        {"id": "p2", "type": "Feature", "geometry": {"type": "Point", "coordinates": [1.0, 0.0, 0.0]}, "properties": {}},
    ],
    "edges": [
        {"id": "e1", "type": "Feature", "geometry": None, "topology": {"type": "Edge", "references": ["p1", "p2"]}, "properties": {}},
    ],
    "rings": [
        {"id": "r1", "type": "Feature", "geometry": None, "topology": {"type": "Ring", "directed_references": [{"ref": "e1", "orientation": "+"}]}, "properties": {}},
    ],
    "faces": [
        {"id": "f1", "type": "Feature", "geometry": None, "topology": {"type": "Face", "directed_references": [{"ref": "r1", "orientation": "+"}]}, "properties": {}},
    ],
    "shells": [
        {"id": "sh1", "type": "Feature", "geometry": None, "topology": {"type": "Shell", "directed_references": [{"ref": "f1", "orientation": "+"}]}, "properties": {}},
    ],
    "solids": [
        {"id": "sol1", "type": "Feature", "geometry": None, "topology": {"type": "Solid", "directed_references": [{"ref": "sh1", "orientation": "+"}]}, "properties": {}},
    ],
}


def test_from_csdm_json_resolves_a_bare_feature_document():
    """Not just counted -- actually resolved into real internal points,
    curves, surfaces and solids, the same way a wrapped equivalent would be.
    Geometric validity of the resulting topology is not the point here
    (`validate_topology` is exercised elsewhere); this is purely about
    whether the loader finds and links the content at all."""
    topology = from_csdm_json(_BARE_MINIMAL_TOPOLOGY)

    assert [p["id"] for p in topology["points"]] == ["p1", "p2"]
    assert [c["id"] for c in topology["curves"]] == ["e1"]
    assert len(topology["surfaces"]) == 1
    assert len(topology["solids"]) == 1
    assert topology["solids"][0]["id"] == "sol1"


# ---------------------------------------------------------------------------
# _build_parcel_surfaces: same bare/wrapped duality, plus the feature_type
# propagation caveat documented on the function.
# ---------------------------------------------------------------------------

_TRIANGLE_POINTS = {
    "p1": {"id": "p1", "coordinates": [0.0, 0.0]},
    "p2": {"id": "p2", "coordinates": [1.0, 0.0]},
    "p3": {"id": "p3", "coordinates": [0.0, 1.0]},
}
_TRIANGLE_CURVES = {
    "e1": {"id": "e1", "vertices": ["p1", "p2"]},
    "e2": {"id": "e2", "vertices": ["p2", "p3"]},
    "e3": {"id": "e3", "vertices": ["p3", "p1"]},
}


def _triangle_parcel_feature(feature_id: str) -> dict[str, Any]:
    return {
        "id": feature_id,
        "type": "Feature",
        "geometry": None,
        "topology": {"type": "Polygon", "references": [["e1", "e2", "e3"]]},
        "properties": {},
    }


def test_build_parcel_surfaces_resolves_a_bare_parcel_feature():
    data = {"parcels": [_triangle_parcel_feature("parcel1")]}

    surfaces = _build_parcel_surfaces(data, _TRIANGLE_CURVES, _TRIANGLE_POINTS)

    assert len(surfaces) == 1
    assert surfaces[0]["id"] == "parcel1"
    assert len(surfaces[0]["rings"]) == 1


def test_build_parcel_surfaces_bare_feature_has_no_feature_type():
    """Documented limitation: a bare parcel Feature has no enclosing
    FeatureCollection to carry `featureType`, so the resulting surface has
    none either -- TR-28/TR-29 (which key off `feature_type ==
    "PrimaryParcel"`) won't activate for it, rather than guessing a type."""
    data = {"parcels": [_triangle_parcel_feature("parcel1")]}

    surfaces = _build_parcel_surfaces(data, _TRIANGLE_CURVES, _TRIANGLE_POINTS)

    assert "feature_type" not in surfaces[0]


def test_build_parcel_surfaces_wrapped_feature_still_carries_feature_type():
    """Control case: the existing FeatureCollection-wrapped shape is
    unaffected -- `feature_type` is still propagated from the wrapper's own
    `featureType`."""
    data = {
        "parcels": [
            {
                "type": "FeatureCollection",
                "featureType": "PrimaryParcel",
                "features": [_triangle_parcel_feature("parcel1")],
            }
        ]
    }

    surfaces = _build_parcel_surfaces(data, _TRIANGLE_CURVES, _TRIANGLE_POINTS)

    assert surfaces[0]["feature_type"] == "PrimaryParcel"


# ---------------------------------------------------------------------------
# Plugin-level regression test for the reported symptom: a validator-plugin
# report of "Validation passed (points: 0, edges: 0, ...)" for a document
# whose collections are non-empty but bare-Feature-shaped.
# ---------------------------------------------------------------------------


def test_plugin_finds_and_validates_a_bare_feature_document(tmp_path):
    """`_BARE_MINIMAL_TOPOLOGY` is structurally too small to form a valid
    solid (a two-point ring, zero volume), so this correctly reports real
    topology errors -- which is itself the proof the fix works: before the
    fix, the plugin found nothing at all and reported a silent
    "Validation passed (points: 0, edges: 0, ...)" for this same document.
    Here it must instead report genuine, content-derived findings that name
    the real object ids from the fixture."""
    input_path = tmp_path / "bare-feature-topology.json"
    input_path.write_text(json.dumps(_BARE_MINIMAL_TOPOLOGY), encoding="utf-8")

    context = types.SimpleNamespace(validation_resources=[])
    meta = types.SimpleNamespace(input_path=str(input_path), context=context)

    entries = TopoValidatorPlugin().validate(meta)

    assert entries is not None
    assert entries != []
    assert not any("Validation passed (points: 0" in e["message"] for e in entries)
    object_ids = {e["payload"].get("object_id") for e in entries}
    assert object_ids & {"f1", "sol1"}
