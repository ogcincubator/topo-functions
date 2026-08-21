#!/usr/bin/env python3

"""
OGC Building Blocks *validator plugin* (duck-typed), per
https://ogcincubator.github.io/bblocks-docs/create/validation#validator-plugins

A validator plugin class needs `mime_types` and/or `file_extensions` class
attributes plus a `validate(self, meta)` method returning a list of
`{"message": str, "is_error": bool, "payload": {...}}` entries (or `None`/`[]`
for no findings). It is registered by a *consuming* register's
`bblocks-config.yaml`, not by this repo:

    plugins:
      validators:
        - pip: git+https://github.com/ogcincubator/topo-functions.git
          modules:
            - topo_validator

This is a different mechanism from the `type: python` bblocks *transform*
convention (see `topo_validator.transform`, and `topo2geojson.run_transform`
for the transformer-side equivalent) -- a validator plugin is applied per
matching test resource across a whole register, receives a file path (not raw
input_data), and returns pass/fail findings rather than a report document.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .loader import from_csdm_json, load_json
from .merge import merge_topology
from .model import Issue
from .rdf_loader import from_rdf_graph
from .validator import validate_topology

_CSDM_COLLECTION_KEYS = ("points", "edges", "rings", "faces", "shells", "solids")
_RDF_SUFFIXES = (".ttl", ".turtle", ".jsonld", ".json-ld")


def _looks_like_csdm_topology(data: Any) -> bool:
    """True if *data* has at least one CSDM topology collection key.

    A validator plugin's mime/extension match fires on every JSON test
    resource in a register, most of which have nothing to do with topology
    -- this lets `validate()` return `None` (no findings) for those instead
    of raising or reporting spurious errors."""
    return isinstance(data, dict) and any(key in data for key in _CSDM_COLLECTION_KEYS)


def _rdf_validation_resource_paths(meta: Any) -> list[str]:
    """Return paths of any TTL/JSON-LD resources declared with
    `role: validation` in the bblock's `bblock.json` (exposed via
    `meta.context.validation_resources`), for resolving externally-
    referenced topology objects (points/curves/etc. defined outside the
    file being validated)."""
    context = getattr(meta, "context", None)
    resources = getattr(context, "validation_resources", None) if context is not None else None
    if not resources:
        return []

    paths: list[str] = []
    for resource in resources:
        path = resource.get("path") if isinstance(resource, dict) else getattr(resource, "path", None)
        if isinstance(path, str) and path.lower().endswith(_RDF_SUFFIXES):
            paths.append(path)
    return paths


def _issue_entry(issue: Issue) -> dict[str, Any]:
    return {
        "message": f"{issue['code']}: {issue['message']}",
        "is_error": issue.get("severity", "error") == "error",
        "payload": {
            "code": issue["code"],
            "object_id": issue.get("object_id"),
            "path": issue.get("path"),
            "extra": issue.get("extra") or {},
        },
    }


class TopoValidatorPlugin:
    """OGC Building Blocks validator plugin for Topo Feature / 3D CSDM
    topology consistency."""

    mime_types = ["application/json", "application/geo+json"]
    file_extensions = [".json", ".geojson"]

    def validate(self, meta: Any) -> list[dict[str, Any]] | None:
        data = load_json(Path(meta.input_path))
        if not _looks_like_csdm_topology(data):
            return None

        topology = from_csdm_json(data)

        rdf_paths = _rdf_validation_resource_paths(meta)
        if rdf_paths:
            rdf_topologies = [from_rdf_graph(path) for path in rdf_paths]
            topology = merge_topology(*rdf_topologies, topology)

        issues = validate_topology(topology)
        return [_issue_entry(issue) for issue in issues] or None
