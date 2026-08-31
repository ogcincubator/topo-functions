#!/usr/bin/env python3

"""Merge multiple `TopologyData` sources into one, by id.

Used to combine topology built inline from CSDM JSON (`loader.from_csdm_json`)
with topology resolved by reference from an external RDF graph
(`rdf_loader.from_rdf_graph`) -- both need to be visible to a single
`validate_topology()` call for cross-referencing rules (e.g. a curve's
vertices, or a surface's referenced curves) to resolve correctly regardless
of which source actually defined the referenced object.
"""

from __future__ import annotations

from .model import (
    Curve,
    ObservationCurve,
    Point,
    ReferenceSurface,
    Solid,
    Surface,
    TopologyData,
)


def merge_topology(*topologies: TopologyData) -> TopologyData:
    """Union multiple `TopologyData` dicts by object id.

    Later arguments take precedence when the same id appears in more than one
    source (e.g. an inline CSDM override of an RDF-supplied default).
    Observation-curve and reference-surface exemptions are deduplicated by
    (ref, source) instead, since they have no id of their own.
    """
    points: dict[str, Point] = {}
    curves: dict[str, Curve] = {}
    surfaces: dict[str, Surface] = {}
    solids: dict[str, Solid] = {}
    observation_curves: list[ObservationCurve] = []
    seen_observation_refs: set[tuple[str, str]] = set()
    reference_surfaces: list[ReferenceSurface] = []
    seen_reference_surface_refs: set[tuple[str, str]] = set()

    for topology in topologies:
        for point in topology.get("points", []):
            points[point["id"]] = point
        for curve in topology.get("curves", []):
            curves[curve["id"]] = curve
        for surface in topology.get("surfaces", []):
            surfaces[surface["id"]] = surface
        for solid in topology.get("solids", []):
            solids[solid["id"]] = solid
        for observation_curve in topology.get("observation_curves", None) or []:
            key = (observation_curve["ref"], observation_curve["source"])
            if key not in seen_observation_refs:
                seen_observation_refs.add(key)
                observation_curves.append(observation_curve)
        for reference_surface in topology.get("reference_surfaces", None) or []:
            key = (reference_surface["ref"], reference_surface["source"])
            if key not in seen_reference_surface_refs:
                seen_reference_surface_refs.add(key)
                reference_surfaces.append(reference_surface)

    return {
        "points": list(points.values()),
        "curves": list(curves.values()),
        "surfaces": list(surfaces.values()),
        "solids": list(solids.values()),
        "observation_curves": observation_curves,
        "reference_surfaces": reference_surfaces,
    }
