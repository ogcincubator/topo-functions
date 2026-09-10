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

# TopoValidatorPlugin is re-exported here for library/programmatic use, but
# that does NOT make it discoverable via a bare `modules: [topo_validator]`
# entry in a consuming register's bblocks-config.yaml: the postprocessor's
# plugin harness (_plugin_harness.py:_validator_classes) only accepts classes
# whose __module__ equals the declared module's own name, which excludes
# classes merely imported into __init__.py's namespace -- the class keeps the
# __module__ of where it's actually defined (topo_validator.plugin). A
# consuming register must declare `modules: [topo_validator.plugin]` instead.
# See https://ogcincubator.github.io/bblocks-docs/create/validation#validator-plugins
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
