"""Tests for topo_validator.transform's bblocks transform entry points.

Exercises the report-generation contract (topo2geojson.run_transform's
convention, applied to validation reports) directly against
topo_validator.transform, rather than against a copy of the transform script
that lives in the sibling topo-feature repo (which this repo doesn't check
out) -- that script is expected to become a thin wrapper around this module.
"""

import json
import types
from pathlib import Path

from topo_validator.transform import run_transform

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
EXAMPLE_PATH = FIXTURES_DIR / "tr01-duplicate-point-fail.json"


def test_run_transform_html_output_is_a_standalone_report():
    transform_metadata = types.SimpleNamespace(metadata={"output_format": "html"})

    report = run_transform(EXAMPLE_PATH.read_text(encoding="utf-8"), transform_metadata)

    assert report.startswith("<!doctype html>")
    assert "<h1>Topology Validation Report</h1>" in report
    assert "<h2>Rule results</h2>" in report


def test_run_transform_falls_back_to_module_globals_for_html_output():
    import topo_validator.transform as transform_module

    transform_module.transform_metadata = types.SimpleNamespace(
        metadata={"output_format": "html", "fail_on_error": False}
    )
    transform_module.input_data = EXAMPLE_PATH.read_text(encoding="utf-8")

    try:
        output_data = transform_module.run_transform()
    finally:
        del transform_module.transform_metadata
        del transform_module.input_data

    assert output_data.startswith("<!doctype html>")
    assert "Topology Validation Report" in output_data
    assert '"valid"' not in output_data


def test_run_transform_json_output_reports_the_expected_duplicate_point_error():
    transform_metadata = types.SimpleNamespace(metadata={"output_format": "json"})

    report = run_transform(EXAMPLE_PATH.read_text(encoding="utf-8"), transform_metadata)
    parsed = json.loads(report)

    assert parsed["valid"] is False
    codes = {issue["code"] for issue in parsed["issues"]}
    assert "DUPLICATE_POINT_PROXIMITY" in codes


def test_run_transform_fail_on_error_raises_for_a_failing_fixture():
    transform_metadata = types.SimpleNamespace(
        metadata={"output_format": "json", "fail_on_error": True}
    )

    try:
        run_transform(EXAMPLE_PATH.read_text(encoding="utf-8"), transform_metadata)
    except ValueError as exc:
        assert "error" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError for a failing fixture with fail_on_error=True")
