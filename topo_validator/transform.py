#!/usr/bin/env python3

"""
OGC Building Blocks *transform* entry points (the `type: python` convention
`topo2geojson.py` also uses -- see `topo2geojson.run_transform` /
`Topo2GeoJsonTransform`), for producing a standalone topology-validation
*report* (JSON/HTML/text) as build output.

This is deliberately separate from `topo_validator.plugin.TopoValidatorPlugin`
(the duck-typed validator-plugin convention that gates a register's own
test-resource validation, per
https://ogcincubator.github.io/bblocks-docs/create/validation#validator-plugins)
-- a validation *report* artifact and a pass/fail *gate* are genuinely
different consumers, and `topo-feature`'s existing
`_sources/features/topology-validator/transforms/validate_topology.py`
already relies on the report-generation shape this module provides (today it
reimplements this logic locally against a sys.path hack into a checked-out
copy of `topo-validator`; this module is the reusable, tested version of that
same logic).

`transform_metadata.metadata` keys:

- `"output_format"` -- `"json"` (default), `"html"`, or `"text"`
- `"fail_on_error"` -- raise instead of just returning a report when
  validation finds errors (default `False`)
- `"ttl"` -- a TTL/JSON-LD path, glob, or list of either, resolved the same
  way `topo2geojson`'s `"ttl"` metadata is (see `topo2geojson._expand_ttl_glob`),
  providing topology for objects referenced but not defined inline (see
  `topo_validator.rdf_loader`)
- `"conformance_classes"` -- optional list of conformance class ids to run
  (e.g. `["CC-01", "CC-02"]`); all registered classes run when omitted

Usage (`transforms.yaml`):

    transforms:
      - id: Validate-Topology
        type: python
        metadata:
          dependencies:
            pip: [git+https://github.com/ogcincubator/topo-functions.git]
          output_format: json
        code: |
          from topo_validator.transform import run_transform
          output_data = run_transform()

Call `run_transform()` to get the report string to bind to `output_data`.
Both arguments are optional -- if omitted, they're picked up from
`input_data`/`transform_metadata` globals, matching `topo2geojson.run_transform`'s
host-integration convention exactly.
"""

from __future__ import annotations

import json
import logging

from topo2geojson import _expand_ttl_glob

from .loader import from_csdm_json
from .merge import merge_topology
from .model import errors_only
from .rdf_loader import from_rdf_graph
from .report import to_html_report, to_json_report, to_text_report
from .validator import validate_topology

logger = logging.getLogger("topo_validator.transform")


def _expand_rdf_sources(value, base_dirs: list) -> list[str]:
    """Expand a ttl/rdf glob pattern (or list of them), reusing
    topo2geojson's ttl-resolution semantics (cwd-relative first, falling back
    to context.working_dir/bblock_files_path)."""
    patterns = value if isinstance(value, list) else [value]
    expanded: list[str] = []
    for pattern in patterns:
        expanded.extend(_expand_ttl_glob(pattern, base_dirs) or [pattern])
    return expanded


def _resolve_topology(data: dict, metadata: dict, transform_metadata=None) -> dict:
    """Build TopologyData from the input document, merging in any
    externally-referenced RDF topology declared via the "ttl" metadata."""
    topology = from_csdm_json(data)

    rdf_value = metadata.get("ttl") or metadata.get("rdf")
    if not rdf_value:
        return topology

    context = getattr(transform_metadata, "context", None)
    base_dirs = [
        getattr(context, "working_dir", None),
        getattr(context, "bblock_files_path", None),
    ] if context is not None else []

    sources = _expand_rdf_sources(rdf_value, base_dirs)
    rdf_topologies = [from_rdf_graph(path) for path in sources]
    return merge_topology(*rdf_topologies, topology)


def _format_report(issues, output_format: str, source_name: str | None) -> str:
    if output_format == "html":
        return to_html_report(issues, source_name=source_name)
    if output_format == "text":
        return to_text_report(issues)
    return to_json_report(issues)


def _source_name_from_metadata(transform_metadata) -> str | None:
    context = getattr(transform_metadata, "context", None)
    snippet = getattr(context, "snippet", None) if context is not None else None
    if isinstance(snippet, dict):
        ref = snippet.get("ref")
        if isinstance(ref, str):
            return ref
    return None


def run_transform(input_data=None, transform_metadata=None) -> str:
    """
    Entry point for OGC Building Blocks-style transform hosts (see
    `topo2geojson.run_transform` for the identical host-integration
    convention this mirrors).

    Returns a validation report string (JSON by default; see
    `"output_format"` in the module docstring) suitable for binding to
    `output_data`.
    """
    if input_data is None:
        logger.debug("seeking input_data in globals")
        input_data = globals().get("input_data")
    if transform_metadata is None:
        transform_metadata = globals().get("transform_metadata")
    if input_data is None or transform_metadata is None:
        raise RuntimeError(
            "run_transform() requires input_data and transform_metadata, "
            "either as arguments or as globals bound by the host."
        )

    metadata = getattr(transform_metadata, "metadata", None) or {}
    output_format = str(metadata.get("output_format", "json"))
    fail_on_error = bool(metadata.get("fail_on_error", False))
    conformance_classes = metadata.get("conformance_classes")

    data = json.loads(input_data) if isinstance(input_data, str) else json.load(input_data)
    topology = _resolve_topology(data, metadata, transform_metadata)
    issues = validate_topology(topology, conformance_classes=conformance_classes)

    if fail_on_error:
        errors = errors_only(issues)
        if errors:
            raise ValueError(f"Topology validation failed with {len(errors)} error(s).")

    logger.info("running in transformer mode (%s)", output_format)
    return _format_report(issues, output_format, _source_name_from_metadata(transform_metadata))


# Guard on `transform_metadata`'s presence so that a plain `import
# topo_validator.transform` (e.g. from tests, or from a host calling
# run_transform() itself) stays side-effect free.
if "transform_metadata" in globals():
    output_data = run_transform()


# ---------------------------------------------------------------------------
# bblocks plugin interface (type: python transforms, not validator plugins --
# see topo_validator.plugin for the duck-typed validator-plugin convention)
# ---------------------------------------------------------------------------
# A bblocks plugin host discovers transform_types/default_inputs/
# default_outputs on the class and calls transform(metadata), where metadata
# exposes `.input_data` and the same `.metadata` dict run_transform() reads.

class TopoValidatorTransform:
    transform_types = ["topo-validate"]
    default_inputs = ["application/json"]
    default_outputs = ["application/json"]

    def transform(self, metadata):
        return run_transform(metadata.input_data, metadata)


class _HtmlOutputMetadata:
    """Wraps a transform_metadata object, forcing metadata["output_format"]
    to "html" regardless of what the host actually configured -- so
    TopoValidatorHtmlTransform can share run_transform() instead of
    duplicating it."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.metadata = {**(getattr(inner, "metadata", None) or {}), "output_format": "html"}

    def __getattr__(self, name):
        return getattr(self._inner, name)


class TopoValidatorHtmlTransform:
    transform_types = ["topo-validate-html"]
    default_inputs = ["application/json"]
    default_outputs = ["text/html"]

    def transform(self, metadata):
        return run_transform(metadata.input_data, _HtmlOutputMetadata(metadata))
