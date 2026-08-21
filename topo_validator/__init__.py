#!/usr/bin/env python3

"""Production topology boundary-block validator package."""

from .loader import (
    from_csdm_json,
    load_json,
)

from .merge import merge_topology

from .model import (
    Curve,
    Issue,
    ObservationCurve,
    Point,
    Ring,
    RingMember,
    Shell,
    Solid,
    Surface,
    Tolerances,
    TopologyData,
    errors_only,
    has_error,
)

# TopoValidatorPlugin is imported and re-exported at module top level so a
# consuming register's bblocks-config.yaml can discover it via a bare
# `modules: [topo_validator]` entry -- see
# https://ogcincubator.github.io/bblocks-docs/create/validation#validator-plugins
from .plugin import TopoValidatorPlugin

from .rdf_loader import from_rdf_graph

from .validator import (
    validate_topology,
)

__all__ = [
    "Curve",
    "Issue",
    "ObservationCurve",
    "Point",
    "Ring",
    "RingMember",
    "Shell",
    "Solid",
    "Surface",
    "Tolerances",
    "TopoValidatorPlugin",
    "TopologyData",
    "from_csdm_json",
    "from_rdf_graph",
    "load_json",
    "merge_topology",
    "errors_only",
    "has_error",
    "validate_topology",
]
