"""
topo_geojson.py
===============
Reads an RDF Turtle topology model (geojson-topo vocabulary) and returns a
dict of GeoJSON geometry objects for every feature, indexed by both full URI
string and qname (prefix:local).

Supported topology types
------------------------
Point  — direct geojson:geometry / dct:spatial coordinates
Edge / geojson:LineString
         — topo:relatedFeatures  → LineString
Ring   — topo:directedReferences of Edges → closed LinearRing (as Polygon)
Face   — topo:directedReferences of Rings → Polygon
Shell  — topo:directedReferences of Faces → MultiPolygon
Solid  — topo:shells of Shells            → MultiPolygon (union of all faces)
geojson:Polygon
         — topo:relatedFeatures = list-of-rings, each ring a list of edge URIs
           (direction auto-detected by adjacency)

Multiple geojson:topology triples on the same feature are merged:
  all LineString  → MultiLineString
  all Polygon     → MultiPolygon
  mixed           → GeometryCollection

Direct geojson:geometry always takes priority over topology.

Orientation
-----------
Directed references (topo:directedReferences / topo:shells) carry
topo:orientation "+" or "-".  A "-" orientation reverses the coordinate
sequence of the referenced geometry so that edges chain correctly.

Usage
-----
    from topo_geojson import load_topo

    geometries = load_topo("path/to/model.ttl")

    # index by full URI
    geom = geometries["http://csdm-example-surveys/DP-572532/8446454"]

    # index by qname when a matching prefix is declared
    geom = geometries["eg2:8446454"]

    print(geom)
    # {"type": "Polygon", "coordinates": [[[lon, lat], ...]]}

Dependencies: rdflib (pip install rdflib)
"""

from __future__ import annotations

from typing import Any

import rdflib
from rdflib import RDF, BNode, Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection

# ---------------------------------------------------------------------------
# Namespace constants
# ---------------------------------------------------------------------------

GEOJSON = Namespace("https://purl.org/geojson/vocab#")
TOPO = Namespace("https://purl.org/geojson/topo#")
DCT = Namespace("http://purl.org/dc/terms/")

_TOPO_EDGE = TOPO.Edge
_TOPO_RING = TOPO.Ring
_TOPO_FACE = TOPO.Face
_TOPO_SHELL = TOPO.Shell
_TOPO_SOLID = TOPO.Solid

# geojson-vocab geometry type URIs used as topo type identifiers
_GJ_LINESTRING = GEOJSON.LineString
_GJ_POLYGON    = GEOJSON.Polygon

# Map geojson type URIs → GeoJSON type strings (for direct geometry parsing)
_GEOJSON_TYPE_MAP = {
    GEOJSON.Point:           "Point",
    GEOJSON.LineString:      "LineString",
    GEOJSON.Polygon:         "Polygon",
    GEOJSON.MultiPoint:      "MultiPoint",
    GEOJSON.MultiLineString: "MultiLineString",
    GEOJSON.MultiPolygon:    "MultiPolygon",
}

# ---------------------------------------------------------------------------
# Internal resolver
# ---------------------------------------------------------------------------

class _TopoResolver:
    """Stateful resolver that walks the topology graph and caches results."""

    def __init__(self, g: Graph) -> None:
        self.g = g
        self._cache: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # RDF helpers
    # ------------------------------------------------------------------

    def _items(self, list_node) -> list:
        """Return Python list from an RDF list node, or [] if absent/nil."""
        if list_node in (None, RDF.nil):
            return []
        try:
            return list(Collection(self.g, list_node))
        except Exception:
            return []

    def _is_list_head(self, node) -> bool:
        """True if *node* is a BNode that heads an RDF list (has rdf:first)."""
        return isinstance(node, BNode) and self.g.value(node, RDF.first) is not None

    def _ref_to_uri(self, ref_str: str) -> URIRef:
        """
        Convert a topo:ref string literal to a URIRef.

        topo:ref values are plain string literals carrying a prefixed name,
        e.g. "uuid:abc123".  We resolve the prefix against the graph's
        namespace bindings; on failure we treat the whole string as a bare URI.
        """
        if ":" in ref_str:
            prefix, local = ref_str.split(":", 1)
            for p, ns in self.g.namespaces():
                if p == prefix:
                    return URIRef(str(ns) + local)
        return URIRef(ref_str)

    def _directed_refs(self, list_node) -> list[tuple[str, URIRef]]:
        """
        Parse an RDF list of directed-reference blank nodes.
        Each item: topo:orientation "+" | "-",  topo:ref "<prefixed-name>".
        Returns [(orientation, resolved_uri), ...]
        """
        result = []
        for item in self._items(list_node):
            orientation = str(self.g.value(item, TOPO.orientation) or "+")
            ref_val = self.g.value(item, TOPO.ref)
            if ref_val is not None:
                result.append((orientation, self._ref_to_uri(str(ref_val))))
        return result

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------

    def _point_coords(self, feature_uri: URIRef) -> list[float] | None:
        """
        Return [x, y] or [x, y, z] for a known-Point feature.
        Tries geojson:geometry first, then dct:spatial as fallback.
        Returns None if the geometry node's coordinates aren't all literals
        (i.e. the feature isn't actually a point).
        """
        for prop in (GEOJSON.geometry, DCT.spatial):
            geom_node = self.g.value(feature_uri, prop)
            if geom_node is None:
                continue
            coords_node = self.g.value(geom_node, GEOJSON.coordinates)
            if coords_node is None:
                continue
            items = self._items(coords_node)
            if items and all(isinstance(v, Literal) for v in items):
                return [float(str(v)) for v in items]
        return None

    def _parse_coords(self, coords_node) -> list:
        """
        Recursively parse a geojson:coordinates node into a nested Python list.

        Handles:
          (lon lat)           → [lon, lat]          (Point)
          (lon lat alt)       → [lon, lat, alt]      (Point 3D)
          ((lon lat)(lon lat)) → [[…],[…]]           (LineString)
          (((lon lat)…)(…))   → [[[…],…],[…]]        (Polygon)
        """
        items = self._items(coords_node)
        if not items:
            return []
        first = items[0]
        if isinstance(first, Literal):
            return [float(str(v)) for v in items]
        # Items are BNodes (nested list heads)
        result = []
        for item in items:
            if self._is_list_head(item):
                result.append(self._parse_coords(item))
            elif isinstance(item, Literal):
                result.append(float(str(item)))
            # Skip URIRefs — not coordinates
        return result

    def _direct_geometry(self, feature_uri: URIRef) -> dict | None:
        """
        Return a GeoJSON geometry dict from geojson:geometry (or dct:spatial),
        if the feature carries explicit coordinates.  Handles all geometry
        types including nested-list coordinates (LineString, Polygon, …).
        """
        for prop in (GEOJSON.geometry, DCT.spatial):
            geom_node = self.g.value(feature_uri, prop)
            if geom_node is None:
                continue
            coords_node = self.g.value(geom_node, GEOJSON.coordinates)
            if coords_node is None:
                continue
            coords = self._parse_coords(coords_node)
            if not coords:
                continue
            geom_type_uri = self.g.value(geom_node, RDF.type)
            type_str = _GEOJSON_TYPE_MAP.get(geom_type_uri)
            if type_str is None:
                # Infer from coordinate structure
                if isinstance(coords[0], (int, float)):
                    type_str = "Point"
                elif isinstance(coords[0], list):
                    type_str = "LineString" if isinstance(coords[0][0], (int, float)) else "Polygon"
            if type_str:
                return {"type": type_str, "coordinates": coords}
        return None

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def geometry(self, feature_uri: URIRef) -> dict | None:
        """Return a GeoJSON geometry dict for *feature_uri*, or None."""
        uri_str = str(feature_uri)
        if uri_str in self._cache:
            return self._cache[uri_str]
        self._cache[uri_str] = None          # sentinel to break cycles
        geom = self._resolve(feature_uri)
        self._cache[uri_str] = geom
        return geom

    def _resolve(self, feature_uri: URIRef) -> dict | None:
        # 1. Direct geometry (explicit coordinates win over topology)
        geom = self._direct_geometry(feature_uri)
        if geom is not None:
            return geom

        # 2. Collect all topology blank nodes (a feature may have several)
        topo_nodes = list(self.g.objects(feature_uri, GEOJSON.topology))
        if not topo_nodes:
            return None

        geoms = [g for tn in topo_nodes
                 if (g := self._resolve_topo(tn)) is not None]
        if not geoms:
            return None
        if len(geoms) == 1:
            return geoms[0]

        # Merge multiple geometries
        types = {g["type"] for g in geoms}
        if types == {"LineString"}:
            return {"type": "MultiLineString",
                    "coordinates": [g["coordinates"] for g in geoms]}
        if types == {"Polygon"}:
            return {"type": "MultiPolygon",
                    "coordinates": [g["coordinates"] for g in geoms]}
        return {"type": "GeometryCollection", "geometries": geoms}

    # ------------------------------------------------------------------
    # Topology dispatcher
    # ------------------------------------------------------------------

    def _resolve_topo(self, topo_node) -> dict | None:
        topo_type = self.g.value(topo_node, RDF.type)
        dispatch = {
            _TOPO_EDGE:       self._resolve_edge,
            _TOPO_RING:       self._resolve_ring,
            _TOPO_FACE:       self._resolve_face,
            _TOPO_SHELL:      self._resolve_shell,
            _TOPO_SOLID:      self._resolve_solid,
            _GJ_LINESTRING:   self._resolve_edge,          # geojson:LineString alias
            _GJ_POLYGON:      self._resolve_gj_polygon,    # geojson:Polygon with edge lists
        }
        handler = dispatch.get(topo_type)
        return handler(topo_node) if handler else None

    # ------------------------------------------------------------------
    # Edge / geojson:LineString  →  LineString
    # ------------------------------------------------------------------

    def _edge_pt_coords(self, edge_uri: URIRef, orientation: str) -> list[list[float]]:
        """Ordered point-coordinate list for a directed edge."""
        topo_node = self.g.value(edge_uri, GEOJSON.topology)
        if topo_node is None:
            return []
        rf_node = self.g.value(topo_node, TOPO.relatedFeatures)
        coords = [pt for ref in self._items(rf_node)
                  if (pt := self._point_coords(URIRef(str(ref)))) is not None]
        return list(reversed(coords)) if orientation == "-" else coords

    def _resolve_edge(self, topo_node) -> dict | None:
        rf_node = self.g.value(topo_node, TOPO.relatedFeatures)
        coords = [pt for ref in self._items(rf_node)
                  if (pt := self._point_coords(URIRef(str(ref)))) is not None]
        return {"type": "LineString", "coordinates": coords} if len(coords) >= 2 else None

    # ------------------------------------------------------------------
    # geojson:Polygon with topo:relatedFeatures = list-of-edge-lists
    # ------------------------------------------------------------------

    def _raw_edge_coords(self, edge_uri: URIRef) -> list[list[float]]:
        """Return undirected [pt1, pt2] coords for an edge feature."""
        topo_node = self.g.value(edge_uri, GEOJSON.topology)
        if topo_node is None:
            return []
        rf = self.g.value(topo_node, TOPO.relatedFeatures)
        return [pt for ref in self._items(rf)
                if (pt := self._point_coords(URIRef(str(ref)))) is not None]

    def _chain_edges(self, edge_uri_nodes: list) -> list[list[float]]:
        """
        Chain an ordered list of edge URIs into a closed linear ring.
        Direction of each edge is auto-detected by adjacency with the
        previous edge's endpoint.
        """
        chain: list[list[float]] = []
        for node in edge_uri_nodes:
            seg = self._raw_edge_coords(URIRef(str(node)))
            if not seg:
                continue
            if not chain:
                chain.extend(seg)
            else:
                last = chain[-1]
                if seg[0] == last:
                    chain.extend(seg[1:])
                elif seg[-1] == last:
                    chain.extend(list(reversed(seg))[1:])
                else:
                    # Non-adjacent — append as-is (shouldn't happen in valid data)
                    chain.extend(seg[1:])
        if chain and chain[0] != chain[-1]:
            chain.append(chain[0])
        return chain

    def _resolve_gj_polygon(self, topo_node) -> dict | None:
        """
        Handle geojson:Polygon topology:
          topo:relatedFeatures ( ( edge1 edge2 … ) ( edge_a … ) … )
        Outer list = rings; each inner list = ordered edge URIs.
        """
        rf_node = self.g.value(topo_node, TOPO.relatedFeatures)
        ring_nodes = self._items(rf_node)
        rings = []
        for ring_node in ring_nodes:
            # Each ring_node should be a BNode heading a list of edge URIs
            edge_nodes = self._items(ring_node) if self._is_list_head(ring_node) else [ring_node]
            ring_coords = self._chain_edges(edge_nodes)
            if ring_coords:
                rings.append(ring_coords)
        return {"type": "Polygon", "coordinates": rings} if rings else None

    # ------------------------------------------------------------------
    # Ring  →  closed coordinate list  (topo:Ring with directed edges)
    # ------------------------------------------------------------------

    def _ring_coords(self, ring_uri: URIRef, orientation: str = "+") -> list[list[float]]:
        """Build a closed linear-ring from directed edges."""
        topo_node = self.g.value(ring_uri, GEOJSON.topology)
        if topo_node is None:
            return []
        dr_node = self.g.value(topo_node, TOPO.directedReferences)
        chain: list[list[float]] = []
        for orient, edge_uri in self._directed_refs(dr_node):
            seg = self._edge_pt_coords(edge_uri, orient)
            if not seg:
                continue
            chain.extend(seg if not chain else seg[1:])
        if not chain:
            return []
        if chain[0] != chain[-1]:
            chain.append(chain[0])
        return list(reversed(chain)) if orientation == "-" else chain

    def _resolve_ring(self, topo_node) -> dict | None:
        """topo:Ring as a single-ring Polygon."""
        dr_node = self.g.value(topo_node, TOPO.directedReferences)
        chain: list[list[float]] = []
        for orient, edge_uri in self._directed_refs(dr_node):
            seg = self._edge_pt_coords(edge_uri, orient)
            chain.extend(seg if not chain else seg[1:])
        if not chain:
            return None
        if chain[0] != chain[-1]:
            chain.append(chain[0])
        return {"type": "Polygon", "coordinates": [chain]}

    # ------------------------------------------------------------------
    # Face  →  Polygon
    # ------------------------------------------------------------------

    def _face_rings(self, face_uri: URIRef, orientation: str = "+") -> list[list[list[float]]]:
        topo_node = self.g.value(face_uri, GEOJSON.topology)
        if topo_node is None:
            return []
        dr_node = self.g.value(topo_node, TOPO.directedReferences)
        rings = []
        for orient, ring_uri in self._directed_refs(dr_node):
            combined = "+" if orient == orientation else "-"
            rc = self._ring_coords(ring_uri, combined)
            if rc:
                rings.append(rc)
        return rings

    def _resolve_face(self, topo_node) -> dict | None:
        dr_node = self.g.value(topo_node, TOPO.directedReferences)
        rings = []
        for orient, ring_uri in self._directed_refs(dr_node):
            rc = self._ring_coords(ring_uri, orient)
            if rc:
                rings.append(rc)
        return {"type": "Polygon", "coordinates": rings} if rings else None

    # ------------------------------------------------------------------
    # Shell  →  MultiPolygon
    # ------------------------------------------------------------------

    def _shell_polygons(
        self, shell_uri: URIRef, orientation: str = "+"
    ) -> list[list[list[list[float]]]]:
        topo_node = self.g.value(shell_uri, GEOJSON.topology)
        if topo_node is None:
            return []
        dr_node = self.g.value(topo_node, TOPO.directedReferences)
        polys = []
        for orient, face_uri in self._directed_refs(dr_node):
            combined = "+" if orient == orientation else "-"
            rings = self._face_rings(face_uri, combined)
            if rings:
                polys.append(rings)
        return polys

    def _resolve_shell(self, topo_node) -> dict | None:
        dr_node = self.g.value(topo_node, TOPO.directedReferences)
        polys = []
        for orient, face_uri in self._directed_refs(dr_node):
            rings = self._face_rings(face_uri, orient)
            if rings:
                polys.append(rings)
        return {"type": "MultiPolygon", "coordinates": polys} if polys else None

    # ------------------------------------------------------------------
    # Solid  ->  MultiPolygon (all faces from all shells)
    # ------------------------------------------------------------------

    def _resolve_solid(self, topo_node) -> dict | None:
        shells_node = self.g.value(topo_node, TOPO.shells)
        all_polys = []
        for orient, shell_uri in self._directed_refs(shells_node):
            all_polys.extend(self._shell_polygons(shell_uri, orient))
        return {"type": "MultiPolygon", "coordinates": all_polys} if all_polys else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_topo(source, *, include_collections=False):
    """
    Parse a Turtle topology file and return GeoJSON geometries for every
    feature, indexed by both full URI string and qname.

    Parameters
    ----------
    source : str or rdflib.Graph
        File path, URL, or a pre-parsed rdflib.Graph.
    include_collections : bool, default False
        If True, also attempt to resolve geojson:FeatureCollection nodes.

    Returns
    -------
    dict[str, GeoJSON geometry dict]
        Keys are full URI strings *and* qname strings (prefix:local).
        Both keys point to the same geometry object.
        Features whose geometry cannot be resolved are omitted.

    Examples
    --------
    >>> from topo_geojson import load_topo
    >>> geoms = load_topo("survey.ttl")
    >>> geoms["eg2:8446454"]
    {'type': 'Polygon', 'coordinates': [[[174.7508, -36.93141], ...]]}
    """
    if isinstance(source, Graph):
        g = source
    else:
        g = Graph()
        g.parse(source, format="turtle")

    resolver = _TopoResolver(g)

    feature_uris = set()
    for s in g.subjects(RDF.type, GEOJSON.Feature):
        if isinstance(s, URIRef):
            feature_uris.add(s)
    if include_collections:
        for s in g.subjects(RDF.type, GEOJSON.FeatureCollection):
            if isinstance(s, URIRef):
                feature_uris.add(s)

    # Build longest-prefix-first list for qname generation
    ns_by_uri = sorted(
        [(str(ns), str(prefix)) for prefix, ns in g.namespaces()],
        key=lambda t: len(t[0]),
        reverse=True,
    )

    def _qname(uri_str):
        for ns_uri, prefix in ns_by_uri:
            if uri_str.startswith(ns_uri):
                local = uri_str[len(ns_uri):]
                if local:
                    return f"{prefix}:{local}" if prefix else local
        return None

    result = {}
    for uri in feature_uris:
        geom = resolver.geometry(uri)
        if geom is None:
            continue
        uri_str = str(uri)
        result[uri_str] = geom
        qn = _qname(uri_str)
        if qn and qn != uri_str:
            result[qn] = geom

    return result


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------

def _cli():
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        description="Resolve RDF topo-feature topology to GeoJSON geometries."
    )
    parser.add_argument("source", help="Path or URL to a Turtle (.ttl) file")
    parser.add_argument("--key", metavar="URI_OR_QNAME",
                        help="Return geometry for a single feature key")
    parser.add_argument("--keys-only", action="store_true",
                        help="Print only the index keys")
    parser.add_argument("--indent", type=int, default=2,
                        help="JSON indent level (default 2, 0 = compact)")
    args = parser.parse_args()

    geoms = load_topo(args.source)

    if args.key:
        obj = geoms.get(args.key)
        if obj is None:
            print(f"Key not found: {args.key}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(obj, indent=args.indent or None))
    elif args.keys_only:
        for k in sorted(set(geoms.keys())):
            print(k)
    else:
        seen_ids = set()
        output = {}
        for k, v in geoms.items():
            vid = id(v)
            if vid not in seen_ids:
                seen_ids.add(vid)
                output[k] = v
        print(json.dumps(output, indent=args.indent or None))


if __name__ == "__main__":
    _cli()
