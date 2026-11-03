"""
The GeoJSON the tests persist under tests/output is tracked in Git, and
.gitattributes stores text with LF. Writing it in text mode on Windows would
turn every newline into CRLF and show all of it as modified, so each writer
must force LF. These tests redirect each writer to a temporary folder.
"""
import pytest

import test_arc_geometry
import test_spline_geometry
import test_topo2geojson
import test_topo_rdf_geojson


@pytest.mark.parametrize(
    "module",
    [test_arc_geometry, test_spline_geometry, test_topo2geojson],
    ids=lambda m: m.__name__,
)
def test_persist_writes_lf(module, tmp_path, monkeypatch):
    monkeypatch.setattr(module, "JSON_OUTPUT_DIR", tmp_path)

    module._persist("lines.geojson", '{\n  "type": "FeatureCollection"\n}\n')

    data = (tmp_path / "lines.geojson").read_bytes()
    assert b"\n" in data
    assert b"\r" not in data


def test_parcel_output_written_with_lf(tmp_path, monkeypatch):
    monkeypatch.setattr(test_topo_rdf_geojson, "RDF_OUTPUT_DIR", tmp_path)

    test_topo_rdf_geojson.test_load_topo_resolves_parcel_polygon_by_chaining_edges()

    data = (tmp_path / "parcel-8446454.geojson").read_bytes()
    assert b"\n" in data
    assert b"\r" not in data
