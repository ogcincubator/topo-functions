#!/usr/bin/env python3

"""Command-line interface for the topology boundary-block validator."""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

from .loader import from_csdm_json, load_json
from .merge import merge_topology
from .model import errors_only
from .rdf_loader import from_rdf_graph
from .report import to_html_report, to_json_report, to_text_report
from .validator import validate_topology

INPUT_ERROR_EXIT_CODE = 2


def _default_report_path(fixture_path: Path, report_format: str) -> Path:
    """Return the default report output path for a fixture and format."""
    suffix = "html" if report_format == "html" else report_format
    return fixture_path.resolve().parent / "reports" / (
        f"{fixture_path.stem}-validation-report.{suffix}"
    )


def _report_content(issues, report_format: str, source_name: str | None = None) -> str:
    """Return report content in the requested format."""
    if report_format == "json":
        return to_json_report(issues)
    if report_format == "html":
        return to_html_report(issues, source_name=source_name)
    return to_text_report(issues)


def _build_parser() -> argparse.ArgumentParser:
    """Build the validator CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Validate Topo Feature / 3D CSDM topology fixtures."
    )
    parser.add_argument("fixture", help="Path to a JSON fixture")
    parser.add_argument("--format", choices=["text", "json", "html"], default="text")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Path to save the report. If omitted, reports are printed to stdout, "
            "except HTML reports, which are saved under tests/reports by default."
        ),
    )
    parser.add_argument(
        "--raw-internal",
        action="store_true",
        help="Input is already in internal points/curves/surfaces/solids format",
    )
    parser.add_argument(
        "--ttl",
        action="append",
        default=[],
        metavar="RDF_FILE",
        help=(
            "TTL or JSON-LD file providing topology for objects referenced but "
            "not defined inline (repeatable, supports glob). Merged with the "
            "fixture's own topology by id; see topo_validator.rdf_loader."
        ),
    )
    return parser


def _load_and_validate_fixture(fixture_path: Path, raw_internal: bool, ttl_files: list[str] | None = None):
    """Load a fixture, adapt it when required, and run topology validation."""
    raw_fixture = load_json(fixture_path)
    topology_data = raw_fixture if raw_internal else from_csdm_json(raw_fixture)

    if ttl_files:
        rdf_topologies = [from_rdf_graph(path) for pattern in ttl_files
                           for path in (sorted(glob.glob(pattern)) or [pattern])]
        topology_data = merge_topology(*rdf_topologies, topology_data)

    return validate_topology(topology_data)


def _resolve_output_path(
    fixture_path: Path,
    report_format: str,
    requested_output_path: Path | None,
) -> Path | None:
    """Return the explicit or default report output path if one should be used."""
    if requested_output_path is not None:
        return requested_output_path

    if report_format == "html":
        return _default_report_path(fixture_path, report_format)

    return None


def _write_or_print_report(content: str, output_path: Path | None) -> None:
    """Write report content to disk when an output path exists; otherwise print it."""
    if output_path is None:
        print(content)
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    print(f"Report written to {output_path}")


def main(argv: list[str] | None = None) -> int:
    """Run the validator CLI.

    Args:
        argv: Optional command-line arguments. Uses `sys.argv` when omitted.

    Returns:
        Process exit code. Returns 0 when no errors are found, 1 for validation
        errors, and 2 for CLI/input failures.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    fixture_path = Path(args.fixture)
    report_format = args.format

    try:
        issues = _load_and_validate_fixture(
            fixture_path=fixture_path,
            raw_internal=args.raw_internal,
            ttl_files=args.ttl,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return INPUT_ERROR_EXIT_CODE

    report_content = _report_content(
        issues,
        report_format,
        source_name=fixture_path.name,
    )
    output_path = _resolve_output_path(
        fixture_path=fixture_path,
        report_format=report_format,
        requested_output_path=args.output,
    )
    _write_or_print_report(report_content, output_path)

    has_validation_errors = bool(errors_only(issues))
    return 1 if has_validation_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
