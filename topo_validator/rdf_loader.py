#!/usr/bin/env python3

"""
Build the validator's internal `TopologyData` model directly from an RDF
graph (Turtle or JSON-LD, geojson-topo vocabulary) instead of Topo Feature /
3D CSDM JSON.

This is the schema-agnostic-by-reference path: points/curves/surfaces/solids
can be defined once in RDF and referenced by id from any number of datasets,
the same way `topo_rdf_geojson` resolves referenced topology into GeoJSON
geometry -- except here the graph is walked *structurally* (ids + orientation)
rather than resolved to coordinates, since topology-consistency rules need the
id graph itself (e.g. TR-11 point-fabric consistency, TR-13 duplicate curves,
TR-05 shared-edge orientation), not resolved geometry.

Supported RDF shapes
---------------------
topo:Edge (relatedFeatures = ordered point URIs) -> Curve
topo:Face (directedReferences = directed Ring URIs,
           each Ring's own directedReferences = directed Edge URIs) -> Surface
topo:Shell (directedReferences = directed Face URIs) -> one
           entry in a Solid's "shells"
topo:Solid (shells = directed Shell URIs) -> Solid
Points with direct geojson:geometry / dct:spatial coordinates -> Point

Limitations
-----------
Domain properties that live outside the core topology vocabulary (a solid's
volume/theme/parcel_type/parent_id/levels, observation-curve exemptions) are
not part of `topo_rdf_geojson`'s RDF walk either, so they aren't available
here -- solids built from RDF get default values (volume 0.0, theme
"default", parcel_type "primary", no parent/servient/host, no levels). Rules
that only depend on the topology *graph* (points, curves, surfaces, shell/
solid structure) are fully supported; rules that depend on those domain
properties need them supplied separately (e.g. merged in from CSDM JSON via
`topo_validator.merge.merge_topology`).
`geojson: Polygon`-style surfaces (undirected rings of edges, direction
inferred from adjacency) are not handled -- only the `topo: Face`/`topo:Ring`
vocabulary, since orientation there is explicit and lossless.
"""

from __future__ import annotations

from rdflib import RDF, Graph, Literal, URIRef

from topo_rdf_common import DCT, GEOJSON, TOPO, RdfTopologyWalker
from topo_rdf_common import feature_uris as _feature_uris
from topo_rdf_common import load_graph as _load_graph
from topo_rdf_common import qname_fn as _qname_fn

from .model import Curve, Point, Ring, RingMember, Shell, Solid, Surface, TopologyData

_TOPO_EDGE = TOPO.Edge
_TOPO_FACE = TOPO.Face
_TOPO_SOLID = TOPO.Solid
_GJ_LINESTRING = GEOJSON.LineString


class _RdfTopologyBuilder(RdfTopologyWalker):
    """Walks a geojson-topo RDF graph to build the validator's internal
    structural model (ids "+" orientation), rather than resolved geometry."""

    def __init__(self, g: Graph) -> None:
        super().__init__(g)
        self._qname = _qname_fn(g)

    def _id_of(self, uri: URIRef) -> str:
        """Prefer a qname (prefix:local) for readability in issue object_ids,
        falling back to the full URI when no namespace prefix matches."""
        uri_str = str(uri)
        return self._qname(uri_str) or uri_str

    def _point(self, uri: URIRef) -> Point | None:
        for prop in (GEOJSON.geometry, DCT.spatial):
            geom_node = self.g.value(uri, prop)
            if geom_node is None:
                continue
            coords_node = self.g.value(geom_node, GEOJSON.coordinates)
            if coords_node is None:
                continue
            items = self._items(coords_node)
            if items and all(isinstance(v, Literal) for v in items):
                point: Point = {
                    "id": self._id_of(uri),
                    "coordinates": [float(str(v)) for v in items],
                }
                return point
        return None

    def _curve(self, uri: URIRef, topo_node) -> Curve | None:
        rf_node = self._related_features(topo_node)
        vertices = [self._id_of(URIRef(str(ref))) for ref in self._items(rf_node)]
        if len(vertices) < 2:
            return None
        curve: Curve = {"id": self._id_of(uri), "vertices": vertices}
        return curve

    def _ring_members(self, dr_node) -> list[RingMember]:
        return [{"ref": self._id_of(ref), "orientation": orient}
                for orient, ref in self._directed_refs(dr_node)]

    def _surface(self, uri: URIRef, topo_node) -> Surface | None:
        dr_node = self.g.value(topo_node, TOPO.directedReferences)
        rings: list[Ring] = []
        for _face_orient, ring_uri in self._directed_refs(dr_node):
            ring_topo = self.g.value(ring_uri, GEOJSON.topology)
            if ring_topo is None:
                continue
            ring_dr = self.g.value(ring_topo, TOPO.directedReferences)
            ring_members = self._ring_members(ring_dr)
            if ring_members:
                rings.append({"type": "outer", "members": ring_members})
        if not rings:
            return None
        surface: Surface = {"id": self._id_of(uri), "rings": rings}
        return surface

    def _shell_faces(self, topo_node) -> tuple[list[str], dict[str, str]]:
        dr_node = self.g.value(topo_node, TOPO.directedReferences)
        faces: list[str] = []
        orientations: dict[str, str] = {}
        for orient, face_uri in self._directed_refs(dr_node):
            face_id = self._id_of(face_uri)
            faces.append(face_id)
            orientations[face_id] = orient
        return faces, orientations

    def _solid(self, uri: URIRef, topo_node) -> Solid | None:
        shells_node = self.g.value(topo_node, TOPO.shells)
        shells: list[Shell] = []
        all_faces: list[str] = []
        all_orientations: dict[str, str] = {}
        for index, (_orient, shell_uri) in enumerate(self._directed_refs(shells_node)):
            shell_topo = self.g.value(shell_uri, GEOJSON.topology)
            if shell_topo is None:
                continue
            faces, orientations = self._shell_faces(shell_topo)
            shell_type = "outer" if index == 0 else "inner"
            shells.append({"type": shell_type, "faces": faces, "face_orientations": orientations})
            all_faces.extend(faces)
            all_orientations.update(orientations)
        if not shells:
            return None
        return {
            "id": self._id_of(uri),
            "shells": shells,
            "faces": all_faces,
            "face_orientations": all_orientations,
            "volume": 0.0,
            "theme": "default",
            "parcel_type": "primary",
            "parent_id": None,
            "servient_id": None,
            "host_id": None,
            "levels": [],
        }

    def build(self) -> TopologyData:
        points: dict[str, Point] = {}
        curves: dict[str, Curve] = {}
        surfaces: dict[str, Surface] = {}
        solids: dict[str, Solid] = {}

        for uri in _feature_uris(self.g, include_collections=False):
            pt = self._point(uri)
            if pt is not None:
                points[pt["id"]] = pt
                continue

            topo_node = self.g.value(uri, GEOJSON.topology)
            if topo_node is None:
                continue
            topo_type = self.g.value(topo_node, RDF.type)

            if topo_type in (_TOPO_EDGE, _GJ_LINESTRING):
                curve = self._curve(uri, topo_node)
                if curve is not None:
                    curves[curve["id"]] = curve
            elif topo_type == _TOPO_FACE:
                surface = self._surface(uri, topo_node)
                if surface is not None:
                    surfaces[surface["id"]] = surface
            elif topo_type == _TOPO_SOLID:
                solid = self._solid(uri, topo_node)
                if solid is not None:
                    solids[solid["id"]] = solid

        return {
            "points": list(points.values()),
            "curves": list(curves.values()),
            "surfaces": list(surfaces.values()),
            "solids": list(solids.values()),
            "observation_curves": [],
            "reference_surfaces": [],
        }


def from_rdf_graph(source, *, format: str | None = None) -> TopologyData:
    """
    Build the validator's internal `TopologyData` model from an RDF graph
    (Turtle or JSON-LD, geojson-topo vocabulary).

    Unlike `topo_rdf_geojson.load_topo()` (which resolves topology all the
    way to GeoJSON coordinates), this walks the graph structurally -- curve
    vertex ids, ring/surface membership, and shell/solid structure are kept
    intact, since topology-consistency rules operate on that id graph rather
    than on resolved geometry.

    Parameters
    ----------
    source : str, file-like, or rdflib.Graph
        Path, URL, RDF text, file-like object, or a pre-parsed rdflib.Graph.
        Both Turtle and JSON-LD are accepted (see `topo_rdf_common.load_graph`
        for format resolution).
    format : str, optional
        Explicit rdflib parser format (e.g. "turtle", "json-ld"). Auto-detected
        when omitted.

    Returns
    -------
    TopologyData
        `{"points": [...], "curves": [...], "surfaces": [...],
          "solids": [...], "observation_curves": [], "reference_surfaces": []}`.
        See the module docstring's "Limitations" section for what isn't
        populated.
    """
    g = _load_graph(source, format=format)
    return _RdfTopologyBuilder(g).build()
