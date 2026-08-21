"""
topo_rdf_common.py
===================
Shared RDF-graph plumbing used by both `topo_rdf_geojson` (resolves topology
to GeoJSON geometry) and `topo_validator.rdf_loader` (resolves topology to the
validator's internal structural model). Factored out so the two don't
duplicate the same RDF-list/directed-reference walking logic.

Namespace constants
--------------------
`GEOJSON`, `TOPO`, `DCT` — the geojson-topo vocabulary namespaces shared by
every module that walks this RDF shape.

Graph loading
-------------
`load_graph(source, format=None)` accepts a file path, URL, string of RDF
text, file-like object, or an already-parsed `rdflib.Graph`, and returns a
parsed `Graph`. Both Turtle and JSON-LD are supported (rdflib's built-in
`json-ld` plugin — no extra dependency needed). Format is resolved, in order:

1. an explicit `format` argument,
2. `rdflib.util.guess_format()` on a path/URL's file extension,
3. content sniffing for string/file-like input with no filename to go on:
   the first non-whitespace character is `{` or `[` for JSON-LD, else Turtle,
4. on parse failure, retry with the other format as a last resort.

RDF-list walking
-----------------
`RdfTopologyWalker` is a small base class wrapping the low-level primitives
needed to walk the geojson-topo vocabulary's RDF lists and directed
references: `_items`, `_is_list_head`, `_related_features`, `_ref_to_uri`,
`_directed_refs`. Subclass it (passing the parsed `Graph` to `__init__`) to
get id/orientation-level access to the graph without re-deriving these from
scratch — this is what both `topo_rdf_geojson._TopoResolver` (which further
resolves ids to coordinates) and `topo_validator.rdf_loader` (which stops at
ids + orientation, for the validator's structural model) build on.
"""

from __future__ import annotations

from typing import Any

import rdflib.util
from rdflib import RDF, BNode, Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection

# ---------------------------------------------------------------------------
# Namespace constants
# ---------------------------------------------------------------------------

GEOJSON = Namespace("https://purl.org/geojson/vocab#")
TOPO = Namespace("https://purl.org/geojson/topo#")
DCT = Namespace("http://purl.org/dc/terms/")

# geojson-vocab geometry type URIs -> GeoJSON type strings (direct geometry)
GEOJSON_TYPE_MAP = {
    GEOJSON.Point:           "Point",
    GEOJSON.LineString:      "LineString",
    GEOJSON.Polygon:         "Polygon",
    GEOJSON.MultiPoint:      "MultiPoint",
    GEOJSON.MultiLineString: "MultiLineString",
    GEOJSON.MultiPolygon:    "MultiPolygon",
}


# ---------------------------------------------------------------------------
# Graph loading (Turtle or JSON-LD)
# ---------------------------------------------------------------------------

def _sniff_content_format(text: str) -> str:
    """Guess 'json-ld' vs 'turtle' from the first non-whitespace character of
    raw RDF text (no filename/extension to go on)."""
    stripped = text.lstrip()
    if stripped[:1] in ("{", "["):
        return "json-ld"
    return "turtle"


def _looks_like_inline_rdf(s: str) -> bool:
    """True if *s* looks like RDF text itself rather than a file path/URL —
    a multi-line string, or one starting with a Turtle/JSON-LD syntax marker.
    rdflib's `Graph.parse()` needs inline content passed via its `data=`
    keyword (not the positional `source=` used for paths/URLs), so this
    distinction has to be made before calling it."""
    if "\n" in s:
        return True
    stripped = s.lstrip()
    return stripped[:1] in ("{", "[", "@", "<")


def load_graph(source: Any, *, format: str | None = None) -> Graph:
    """
    Parse *source* (a path, URL, string of RDF text, file-like object, or an
    already-parsed `Graph`) into an `rdflib.Graph`. Accepts Turtle or JSON-LD;
    see the module docstring for the format-resolution order.
    """
    if isinstance(source, Graph):
        return source

    inline_text: str | None = None

    if hasattr(source, "read"):
        pos = source.tell() if hasattr(source, "tell") else None
        raw = source.read()
        if pos is not None:
            try:
                source.seek(pos)
            except Exception:
                pass
        inline_text = raw if isinstance(raw, str) else raw.decode("utf-8")
        if format is None:
            name = getattr(source, "name", None)
            format = rdflib.util.guess_format(name) if isinstance(name, str) else None
        if format is None:
            format = _sniff_content_format(inline_text)
    elif isinstance(source, str) and _looks_like_inline_rdf(source):
        inline_text = source
        if format is None:
            format = _sniff_content_format(source)
    elif format is None and isinstance(source, str):
        format = rdflib.util.guess_format(source)

    g = Graph()
    if inline_text is not None:
        try:
            g.parse(data=inline_text, format=format or "turtle")
        except Exception:
            if format is not None:
                raise
            g = Graph()
            g.parse(data=inline_text, format="json-ld")
        return g

    # Path or URL.
    try:
        g.parse(source, format=format or "turtle")
    except Exception:
        if format is not None:
            raise
        g = Graph()
        g.parse(source, format="json-ld")
    return g


# ---------------------------------------------------------------------------
# RDF-list / directed-reference walking
# ---------------------------------------------------------------------------

class RdfTopologyWalker:
    """Base class providing id/orientation-level access to an RDF graph
    encoded with the geojson-topo vocabulary. Subclasses add whatever
    resolution they need on top (geometry coordinates, or a validator's
    structural model)."""

    def __init__(self, g: Graph) -> None:
        self.g = g

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

    def _related_features(self, topo_node):
        """Return the topo:relatedFeatures / geojson:relatedFeatures RDF list
        node for a topology node. Data in the wild uses either predicate
        interchangeably (e.g. topo:Edge nodes commonly carry topo:
        relatedFeatures, while geojson:LineString nodes carry geojson:
        relatedFeatures), so both are tried."""
        return (self.g.value(topo_node, TOPO.relatedFeatures)
                or self.g.value(topo_node, GEOJSON.relatedFeatures))

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


# ---------------------------------------------------------------------------
# Feature discovery / qname resolution
# ---------------------------------------------------------------------------

def feature_uris(g: Graph, include_collections: bool = False) -> set:
    """Return every geojson:Feature (and, if requested, FeatureCollection)
    subject URI in *g*."""
    uris = set()
    for s in g.subjects(RDF.type, GEOJSON.Feature):
        if isinstance(s, URIRef):
            uris.add(s)
    if include_collections:
        for s in g.subjects(RDF.type, GEOJSON.FeatureCollection):
            if isinstance(s, URIRef):
                uris.add(s)
    return uris


def qname_fn(g: Graph):
    """Return a uri_str -> qname_or_None function using longest-prefix-first
    namespace matching."""
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

    return _qname
