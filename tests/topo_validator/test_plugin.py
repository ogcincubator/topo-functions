"""Tests for topo_validator.plugin.TopoValidatorPlugin, the duck-typed OGC
Building Blocks validator plugin (mime_types/file_extensions + validate(meta)),
per https://ogcincubator.github.io/bblocks-docs/create/validation#validator-plugins
"""

import json
import types
from pathlib import Path

from topo_validator.plugin import TopoValidatorPlugin

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
FAILING_FIXTURE = FIXTURES_DIR / "tr01-duplicate-point-fail.json"


def _meta(input_path: Path, validation_resources=None):
    context = types.SimpleNamespace(
        bblock_id="ogc.test.topology-validator",
        bblock_name="Topology Validator",
        register_base_url=None,
        validation_resources=validation_resources or [],
        bblock_metadata={},
    )
    return types.SimpleNamespace(
        input_path=str(input_path),
        mime_type="application/json",
        display_filename=input_path.name,
        schema_ref=None,
        context=context,
    )


def test_plugin_declares_required_duck_typed_attributes():
    plugin = TopoValidatorPlugin()
    assert plugin.mime_types
    assert plugin.file_extensions
    assert callable(plugin.validate)


def test_validate_returns_error_entries_for_a_failing_fixture():
    plugin = TopoValidatorPlugin()
    entries = plugin.validate(_meta(FAILING_FIXTURE))

    assert entries is not None
    assert any(e["is_error"] for e in entries)
    assert any("DUPLICATE_POINT_PROXIMITY" in e["message"] for e in entries)
    assert all({"message", "is_error"} <= e.keys() for e in entries)
    assert all(isinstance(e["payload"], dict) for e in entries)


def test_validate_returns_none_for_non_topology_json(tmp_path):
    unrelated = tmp_path / "unrelated.json"
    unrelated.write_text(json.dumps({"hello": "world"}), encoding="utf-8")

    plugin = TopoValidatorPlugin()
    assert plugin.validate(_meta(unrelated)) is None


def test_validate_merges_rdf_validation_resource(tmp_path):
    """A bblock declaring its companion TTL as a role: validation resource
    (exposed via meta.context.validation_resources) should have its
    RDF-referenced points/curves merged in before validation runs."""
    ttl_path = tmp_path / "referenced.ttl"
    ttl_path.write_text(
        """
        @prefix geojson: <https://purl.org/geojson/vocab#> .
        @prefix ex: <http://example.com/plugin-test/> .
        ex:extra a geojson:Feature ;
            geojson:geometry [ a geojson:Point ; geojson:coordinates ( 1.0 2.0 3.0 ) ] .
        """,
        encoding="utf-8",
    )

    plugin = TopoValidatorPlugin()
    entries = plugin.validate(_meta(
        FAILING_FIXTURE,
        validation_resources=[{"path": str(ttl_path), "role": "validation"}],
    ))

    # Merging an unrelated RDF point shouldn't remove the fixture's own
    # duplicate-point finding -- proves the merge is additive, not a replace.
    assert entries is not None
    assert any("DUPLICATE_POINT_PROXIMITY" in e["message"] for e in entries)
