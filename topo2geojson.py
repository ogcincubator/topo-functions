"""
topo2geojson.py
================
Converts topo-feature JSON (points/edges/rings/faces/shells/solids, or
inline `topology` blocks on individual features) to GeoJSON.

Some input features carry a full inline topology (see cube-with-void.json);
others only carry bare references to features that live in a companion RDF
Turtle model (see parcel1.json + topoobjects.ttl). Those external references
are resolved via topo_rdf_geojson.load_topo().

Usage example:
    python topo2geojson.py -i parcel1.json -t topoobjects.ttl -o test.json
"""

import json
import glob as glob_module
import logging
import os
from typing import Generator, List

from pyproj import Transformer

from arc_geometry import ARC_TOPOLOGY_TYPES, arc_topology_to_geometry
from topo_rdf_geojson import load_topo, load_topo_components

# Default maximum chord-to-arc offset (sagitta) used when densifying arc/circle
# topology, in the coordinate units of the input. Overridable per-transform via
# the "max_offset" metadata key or the CLI --max-offset flag.
DEFAULT_MAX_OFFSET = 0.02


# Module logger. Diagnostic/status output goes through this instead of print()
# so it can be gated by the CLI's -v/--verbose flag (see _cli()). Without any
# handler configured (e.g. when invoked from a transform host that hasn't set
# up logging), Python's last-resort handler still emits WARNING and above to
# stderr, so warnings keep surfacing while info/debug stay quiet.
logger = logging.getLogger("topo2geojson")


# ---------------------------------------------------------------------------
# Shared helpers (from topo2geojson-orig.py)
# ---------------------------------------------------------------------------

def walk_features(data: list) -> Generator[dict, None, None]:
    for item in data:
        if isinstance(item, List):
            yield from walk_features(item)
            continue
        elif isinstance(item, dict):
            match item.get("type"):
                case "Feature":
                    yield item
                case "FeatureCollection":
                    yield from walk_features(item.get("features", []))
                case _:
                    raise ValueError(f"Unexpected GeoJSON type: {item.get('type')!r}")
        else:
            yield item


def extract_feature_coordinates(data: list) -> dict[str, object]:
    return {
        feature["id"]: feature["geometry"]["coordinates"]
        for feature in walk_features(data)
    }


# ---------------------------------------------------------------------------
# TTL helpers
# ---------------------------------------------------------------------------

def _local_name(uri_or_qname: str) -> str:
    """Return the local fragment/path-tail/suffix of a URI or qname."""
    key = str(uri_or_qname)
    if key.startswith("http://") or key.startswith("https://"):
        if "#" in key:
            return key.rsplit("#", 1)[1]
        return key.rsplit("/", 1)[-1]
    if ":" in key:
        return key.split(":", 1)[1]
    return key


def load_ttl_geoms(ttl_files: list[str]):
    """
    Parse TTL files with topo_geojson.load_topo() and return three dicts:
      geoms:      local_name / full_key / qname → {"type": ..., "coordinates": ...}
      coords:     local_name / full_key / qname → coordinates only (for geomsmap fallback)
      components: local_name / full_key / qname → {"edges": {id: geom}, "points": {id: geom}},
                  the terminal Edge/Point features that compose that feature's
                  topology however many Ring/Face/Shell/Solid levels deep
                  (used to decompose a higher-order feature for -m edges/-m points)
    """
    geoms = {}
    coords = {}
    components = {}
    for path in ttl_files:
        logger.info("Loading TTL: %s", path)
        resolved = load_topo(path)
        for key, geom in resolved.items():
            geoms[key] = geom
            local = _local_name(key)
            if local not in geoms:
                geoms[local] = geom
            if "coordinates" in geom:
                coords[key] = geom["coordinates"]
                if local not in coords:
                    coords[local] = geom["coordinates"]
        resolved_components = load_topo_components(path)
        for key, comps in resolved_components.items():
            components[key] = comps
            local = _local_name(key)
            if local not in components:
                components[local] = comps
    unique = len({id(v) for v in geoms.values()})
    logger.info("  -> %d unique TTL geometries indexed", unique)
    return geoms, coords, components


# ---------------------------------------------------------------------------
# Namespace/prefix resolution
# ---------------------------------------------------------------------------
# Bare JSON references (e.g. "LineP1P2") and refs carrying a prefix unknown
# to the TTL graph (e.g. "eg:LineP1P2") are reconciled against TTL URIs
# (e.g. "http://somens/LineP1P2") by trying prefix + local-name concatenation
# against an optional set of namespaces gathered, in order of preference,
# from:
#   1. the JSON-LD @context declared in the input JSON itself
#   2. examples.yaml prefixes, exposed to bblocks transforms via
#      transform_metadata.context.example/.snippet["prefixes"]
#      (https://ogcincubator.github.io/bblocks-docs/create/examples#prefixes,
#      https://ogcincubator.github.io/bblocks-docs/create/transforms#transform-context)
#   3. metadata globals (transform_metadata.metadata["namespaces"/"prefixes"])
#      or command-line -ns/--namespace arguments (fallback, in that order)

def _prefixes_from_jsonld_context(context) -> dict[str, str]:
    """Extract {prefix: namespace_uri} mappings from a JSON-LD @context value
    (a dict, or a list mixing dicts with remote-context URL strings, which
    are skipped since they aren't dereferenced here)."""
    prefixes: dict[str, str] = {}
    if not context:
        return prefixes
    for entry in context if isinstance(context, list) else [context]:
        if not isinstance(entry, dict):
            continue
        for term, value in entry.items():
            if term.startswith("@"):
                continue
            uri = value.get("@id") if isinstance(value, dict) else value
            if isinstance(uri, str) and (uri.endswith("/") or uri.endswith("#")):
                prefixes[term] = uri
    return prefixes


def _prefixes_from_transform_context(transform_metadata) -> dict[str, str]:
    """Extract example-level examples.yaml prefixes exposed by a bblocks
    transform host via transform_metadata.context.example/.snippet."""
    prefixes: dict[str, str] = {}
    context = getattr(transform_metadata, "context", None)
    if context is None:
        return prefixes
    for attr in ("example", "snippet"):
        obj = context.get(attr) if isinstance(context, dict) else getattr(context, attr, None)
        if obj is None:
            continue
        raw = obj.get("prefixes") if isinstance(obj, dict) else getattr(obj, "prefixes", None)
        if isinstance(raw, dict):
            for k, v in raw.items():
                prefixes.setdefault(k, v)
    return prefixes


def _normalize_namespace_input(raw) -> dict[str, str]:
    """Normalize a namespaces/prefixes value from CLI args or metadata into a
    {prefix: namespace_uri} dict. Accepts a dict already, or a list/tuple of
    "prefix=uri" / bare-uri strings (a bare URI is keyed by itself, so it's
    still tried for local-name concatenation even without an explicit CURIE
    prefix)."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    prefixes: dict[str, str] = {}
    for item in raw if isinstance(raw, (list, tuple)) else [raw]:
        if not isinstance(item, str):
            continue
        if "=" in item:
            prefix, uri = item.split("=", 1)
            prefixes[prefix.strip()] = uri.strip()
        else:
            prefixes[item.strip()] = item.strip()
    return prefixes


def _merge_namespaces(data: dict, transform_metadata=None, cli_namespaces=None) -> dict[str, str]:
    """Merge namespace/prefix declarations from every supported source. The
    first source to declare a given prefix wins:
      1. the input JSON's own @context
      2. examples.yaml prefixes (via the bblocks transform context)
      3. metadata globals / command-line arguments (fallback)
    """
    merged: dict[str, str] = {}

    def _apply(mapping: dict[str, str]) -> None:
        for k, v in mapping.items():
            merged.setdefault(k, v)

    _apply(_prefixes_from_jsonld_context((data or {}).get("@context")))
    if transform_metadata is not None:
        _apply(_prefixes_from_transform_context(transform_metadata))
        metadata = getattr(transform_metadata, "metadata", None) or {}
        _apply(_normalize_namespace_input(metadata.get("namespaces") or metadata.get("prefixes")))
    _apply(_normalize_namespace_input(cli_namespaces))

    return merged


class _NamespaceResolvingMap:
    """Read-only dict wrapper: exact-key lookup first, falling back to trying
    each declared namespace URI concatenated with the key's local name (and,
    for prefixed keys, the namespace registered for that exact prefix)."""

    def __init__(self, data: dict, namespaces: dict[str, str] | None):
        self._data = data
        self._namespaces = namespaces or {}

    def _candidates(self, key):
        if not self._namespaces or not isinstance(key, str):
            return
        if ":" in key and not key.startswith(("http://", "https://")):
            prefix, local = key.split(":", 1)
            ns = self._namespaces.get(prefix)
            if ns:
                yield ns + local
        local_name = _local_name(key)
        for ns in self._namespaces.values():
            yield ns + local_name

    def get(self, key, default=None):
        if key in self._data:
            return self._data[key]
        for candidate in self._candidates(key):
            if candidate in self._data:
                return self._data[candidate]
        return default

    def items(self):
        return self._data.items()

    def __len__(self):
        return len(self._data)

    def __bool__(self):
        return bool(self._data)


# ---------------------------------------------------------------------------
# Topology resolution helpers
# ---------------------------------------------------------------------------

def _resolve_refs(refs, geomsmap: dict, ttl_coords: dict):
    """
    Recursively resolve a (possibly nested) list of node references to
    coordinates, dropping unresolved (None) leaves and any sublists that
    end up empty after resolution.
    """
    if isinstance(refs, list):
        resolved = [_resolve_refs(r, geomsmap, ttl_coords) for r in refs]
        return [r for r in resolved if r is not None and r != []]
    return geomsmap.get(refs) or ttl_coords.get(refs)


def _chain_edges(edge_coord_lists: list) -> list:
    """
    Chain a list of edge LineString coordinate sequences into a closed ring.

    Each edge's direction is auto-detected (and flipped if necessary) by
    matching its endpoints against *either* end of the chain built so far —
    not just the running chain's tail. Checking only the tail would miss the
    case where the very first edge happens to be stored "backwards" relative
    to the ring's traversal order: every later edge would then fail to match
    the (wrongly oriented) tail and fall into the non-adjacent fallback,
    corrupting the ring. Matching against both ends lets a misoriented edge
    be prepended (flipped or not) instead.
    """
    chain: list = []
    for seg in edge_coord_lists:
        if not seg:
            continue
        if not chain:
            chain = list(seg)
            continue
        if seg[0] == chain[-1]:
            chain.extend(seg[1:])
        elif seg[-1] == chain[-1]:
            chain.extend(list(reversed(seg))[1:])
        elif seg[-1] == chain[0]:
            chain[0:0] = seg[:-1]
        elif seg[0] == chain[0]:
            chain[0:0] = list(reversed(seg))[:-1]
        else:
            # Non-adjacent — append without first point (best-effort)
            chain.extend(seg[1:])
    if chain and chain[0] != chain[-1]:
        chain.append(chain[0])
    return chain


def _resolve_polygon_rings(refs, geomsmap: dict, ttl_coords: dict, ttl_geoms: dict) -> list:
    """
    Resolve a Polygon topology's `references` (rings of edge IDs, or rings
    given directly as a single geometry ref) to proper GeoJSON Polygon
    coordinates: a list of rings, each ring a flat list of [x, y(, z)]
    points.

    Each ring's edge segments are chained (via `_chain_edges`) rather than
    just concatenated, since adjacent edges aren't necessarily stored
    head-to-tail or consistently oriented — naively substituting each edge
    ID with its raw (still edge-shaped) coordinates would leave the ring
    one level too deeply nested instead of a flat point list.
    """
    def lookup_coords(ref_id):
        return geomsmap.get(ref_id) or ttl_coords.get(ref_id)

    def lookup_geom(ref_id):
        return ttl_geoms.get(ref_id)

    rings = []
    for ring_item in refs:
        if isinstance(ring_item, list):
            # ring_item is a list of edge IDs; resolve each to LineString coords
            edge_segs = []
            for edge_id in ring_item:
                geom = lookup_geom(edge_id)
                if geom and geom.get("type") == "LineString":
                    edge_segs.append(geom["coordinates"])
                else:
                    c = lookup_coords(edge_id)
                    if c is not None:
                        edge_segs.append(c if isinstance(c[0], list) else [c])
            if edge_segs:
                ring = _chain_edges(edge_segs)
                if ring:
                    rings.append(ring)
        else:
            geom = lookup_geom(ring_item)
            if geom:
                rings.append(geom.get("coordinates", []))
    return rings


def _resolve_inline_topology(topo: dict, geomsmap: dict,
                             ttl_coords: dict, ttl_geoms: dict) -> dict | None:
    """
    Resolve a feature's inline topology dict to a GeoJSON geometry dict.

    Handles:
      topology.type = "Polygon",    references = [[edge_ids…], …]
      topology.type = "LineString", references = [point_ids…]
      topology.directed_references  = [{"ref": id, "orientation": "+"/"-"}, …]
    """
    topo_type = topo.get("type", "").lower()

    def lookup_coords(ref_id):
        return geomsmap.get(ref_id) or ttl_coords.get(ref_id)

    if "references" in topo:
        refs = topo["references"]

        if topo_type == "polygon":
            rings = _resolve_polygon_rings(refs, geomsmap, ttl_coords, ttl_geoms)
            return {"type": "Polygon", "coordinates": rings} if rings else None

        elif topo_type in ("linestring", "edge"):
            pts = [c for r in refs if (c := lookup_coords(r)) is not None]
            return {"type": "LineString", "coordinates": pts} if len(pts) >= 2 else None

        elif topo_type == "multilinestring":
            lines = []
            for line_refs in refs:
                pts = [c for r in line_refs if (c := lookup_coords(r)) is not None]
                if len(pts) >= 2:
                    lines.append(pts)
            return {"type": "MultiLineString", "coordinates": lines} if lines else None

        else:
            # Any other topology type (e.g. "Point", "Ring", "Face", "Shell",
            # "Solid") reaching here still carries a flat point-id reference
            # list, so the only valid GeoJSON shapes are Point or LineString —
            # never echo the topology-vocabulary type name itself as the
            # GeoJSON "type" (it isn't one). A type whose references are
            # themselves nested (e.g. "Polyhedron": shells of rings of
            # point ids) isn't representable this way — unsupported, so
            # skip rather than crash trying to treat a list as a point id.
            if any(isinstance(r, list) for r in refs):
                return None
            pts = [c for r in refs if (c := lookup_coords(r)) is not None]
            if not pts:
                return None
            if len(pts) == 1:
                return {"type": "Point", "coordinates": pts[0]}
            return {"type": "LineString", "coordinates": pts}

    elif "directed_references" in topo:
        drs = topo["directed_references"]
        chain: list = []
        startindex = 0
        for node in drs:
            ref_id = node.get("ref")
            c = lookup_coords(ref_id)
            if c is None:
                continue
            seg = list(c) if isinstance(c[0], list) else [c]
            seg = seg[:]
            if node.get("orientation") == "-":
                chain += list(reversed(seg))[startindex:]
            else:
                chain += seg[startindex:]
            startindex = 1
        return {"type": "Polygon", "coordinates": [chain]} if chain else None

    return None


# ---------------------------------------------------------------------------
# CRS reprojection / GeoJSON serialization helpers (no geopandas required)
# ---------------------------------------------------------------------------

def _reproject_coords(coords: list, transformer: Transformer) -> list:
    """Recursively reproject every [x, y] or [x, y, z] leaf in a coordinates tree."""
    if not coords:
        return coords
    if isinstance(coords[0], (int, float)):
        x, y = transformer.transform(coords[0], coords[1])
        return [x, y, *coords[2:]]
    return [_reproject_coords(c, transformer) for c in coords]


def _reproject_geometry(geom: dict | None, transformer: Transformer) -> None:
    """Reproject a GeoJSON geometry dict in place (handles GeometryCollection too)."""
    if not geom:
        return
    if geom.get("type") == "GeometryCollection":
        for g in geom.get("geometries", []):
            _reproject_geometry(g, transformer)
    elif "coordinates" in geom:
        geom["coordinates"] = _reproject_coords(geom["coordinates"], transformer)


# ---------------------------------------------------------------------------
# JSON-FG `place` / multi-CRS support
# ---------------------------------------------------------------------------
# JSON-FG (https://docs.ogc.org/DRAFTS/21-045.html) lets a Feature carry a
# `place` geometry in its own native CRS (with `geometry` left null) instead
# of — or alongside — the always-WGS84 `geometry`. `place`'s CRS is resolved
# per JSON-FG's own scoping rules: the feature's own "coordRefSys" property,
# else its containing FeatureCollection's "coordRefSys", else the document
# root's "coordRefSys", else (a convention used by 3D topology model) the root's "horizontalCRS".

_WGS84_CRS_ALIASES = {
    "4326", "epsg:4326", "urn:ogc:def:crs:epsg::4326",
    "crs84", "ogc:crs84", "urn:ogc:def:crs:ogc::crs84", "ogc:1.3:crs84",
    "http://www.opengis.net/def/crs/epsg/0/4326",
    "http://www.opengis.net/def/crs/ogc/1.3/crs84",
}


def _is_wgs84_crs(crs: str) -> bool:
    return crs.strip().strip("[]").lower() in _WGS84_CRS_ALIASES


def _get_wgs84_transformer(crs, cache: dict) -> Transformer | None:
    """Return a cached pyproj Transformer(crs -> EPSG:4326), or None if *crs*
    is falsy, already WGS84, or unparseable (a warning is printed and the
    coordinates are then left untouched rather than raising).

    A JSON-FG `coordRefSys` may also be a [horizontal, vertical] pair; only
    the first (horizontal) element is used — full compound-CRS support
    isn't implemented.
    """
    if not crs:
        return None
    if isinstance(crs, list):
        crs = crs[0] if crs else None
        if not crs:
            return None
    key = str(crs).strip()
    if _is_wgs84_crs(key):
        return None
    if key in cache:
        return cache[key]
    try:
        transformer = Transformer.from_crs(key.strip("[]"), "EPSG:4326", always_xy=True)
    except Exception as e:
        logger.warning("could not parse CRS %r (%s); leaving its coordinates unprojected", key, e)
        transformer = None
    cache[key] = transformer
    return transformer


def _normalize_geometries_to_wgs84(data: dict) -> None:
    """
    Walk the whole parsed document and reproject every feature's coordinate
    data to EPSG:4326 in place, before any topology resolution runs.

    This has to happen early rather than as a single pass over the final
    assembled output: topology resolution chains raw point coordinates
    together (matching adjacent edge endpoints, etc.), which only works if
    every coordinate is already in one consistent CRS by the time that
    happens — a single global final-reprojection step can't fix that up
    afterwards if the source document mixes features on different CRSs.

    Two independent mechanisms are handled:
      - JSON-FG `place`: reprojected into `geometry` (which existing code
        already reads everywhere), using the precedence described above.
        Left alone if `geometry` is already populated (trusted as already
        being the correct WGS84 rendering, per JSON-FG's own semantics).
      - the legacy GeoJSON `crs` extension (a single CRS for the whole
        document, declared once at the root or, when the root is itself a
        single Feature, on that Feature): applied to `geometry` wherever
        found, same as it always was — just moved here instead of a final
        pass over the assembled output.

    Features with neither `place` nor `geometry` (topology-only — the
    common case) are untouched; their coordinates get synthesized later
    from data that, by then, is already in EPSG:4326.
    """
    root_fallback_crs = data.get("coordRefSys") or data.get("horizontalCRS")
    legacy_crs_name = ((data.get("crs") or {}).get("properties") or {}).get("name")
    transformer_cache: dict = {}
    reported: set = set()

    def report(crs, via: str) -> None:
        key = (str(crs), via)
        if key not in reported:
            reported.add(key)
            logger.info("Reprojecting %s CRS %r -> EPSG:4326", via, crs)

    def walk(node, collection_crs) -> None:
        if isinstance(node, dict):
            if node.get("type") == "FeatureCollection":
                collection_crs = node.get("coordRefSys") or collection_crs
            elif node.get("type") == "Feature":
                place = node.get("place")
                if node.get("geometry") is None and isinstance(place, dict) and place.get("coordinates") is not None:
                    crs = node.get("coordRefSys") or collection_crs or root_fallback_crs
                    geom = {k: v for k, v in place.items() if k in ("type", "coordinates", "geometries")}
                    transformer = _get_wgs84_transformer(crs, transformer_cache)
                    if transformer:
                        report(crs, "place")
                        _reproject_geometry(geom, transformer)
                    node["geometry"] = geom
                elif node.get("geometry") is not None and legacy_crs_name:
                    transformer = _get_wgs84_transformer(legacy_crs_name, transformer_cache)
                    if transformer:
                        report(legacy_crs_name, "geometry")
                        _reproject_geometry(node["geometry"], transformer)
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v, collection_crs)
        elif isinstance(node, list):
            for item in node:
                walk(item, collection_crs)

    walk(data, None)


def _clean_feature(feat: dict) -> dict:
    """Strip internal-only keys (e.g. `topology`, the stray `properties` some
    geometry dicts carry) down to a plain GeoJSON Feature."""
    geometry = feat.get("geometry")
    clean_geom = None
    if geometry is not None:
        clean_geom = {k: v for k, v in geometry.items() if k in ("type", "coordinates", "geometries")}
    clean = {"type": "Feature", "geometry": clean_geom, "properties": feat.get("properties", {})}
    if "id" in feat:
        clean["id"] = feat["id"]
    return clean


# ---------------------------------------------------------------------------
# Higher-order geometry decomposition (for -m edges / -m points against a
# feature that only ever resolves as a single Polygon or MultiPolygon —
# a Ring/Face or Shell/Solid, at any nesting depth — e.g. TTL-referenced
# parcels with no top-level points/edges collections of their own)
# ---------------------------------------------------------------------------

def _components_to_features(comps: dict, mode: str) -> list[dict]:
    """Turn a {"edges": {id: geom}, "points": {id: geom}} components dict
    (from topo_rdf_geojson.load_topo_components) into GeoJSON Features,
    limited to what `mode` actually requests."""
    extra: list[dict] = []
    if "edges" in mode:
        for edge_id, geom in comps.get("edges", {}).items():
            extra.append({"type": "Feature", "id": edge_id, "geometry": geom, "properties": {}})
    if "points" in mode:
        for pt_id, geom in comps.get("points", {}).items():
            extra.append({"type": "Feature", "id": pt_id, "geometry": geom, "properties": {}})
    return extra


def _extract_topology_refs(topo: dict) -> list:
    """Flatten a Polygon topology's references into a single list of ids,
    regardless of whether they're nested rings-of-edge-ids (`references`)
    or a flat `directed_references` list."""
    refs: list = []
    if "references" in topo:
        for ring_item in topo["references"]:
            if isinstance(ring_item, list):
                refs.extend(ring_item)
            else:
                refs.append(ring_item)
    elif "directed_references" in topo:
        for node in topo["directed_references"]:
            ref_id = node.get("ref")
            if ref_id is not None:
                refs.append(ref_id)
    return refs


def _decompose_polygon_topology(topo: dict, geomsmap: dict, ttl_geoms: dict,
                                 ttl_coords: dict, mode: str) -> list[dict]:
    """
    Fallback decomposition for a single-level inline Polygon topology
    (`references` = rings of edge ids) when no richer TTL component graph
    is available for it. Only handles one level (Face made of Edges) —
    `_components_to_features`/`load_topo_components` is the general,
    depth-agnostic path used whenever the feature's id is TTL-resolved.
    """
    want_edges = "edges" in mode
    want_points = "points" in mode
    if not (want_edges or want_points):
        return []

    point_index: dict = {}
    if want_points:
        for key, geom in ttl_geoms.items():
            if geom.get("type") == "Point":
                point_index[tuple(geom["coordinates"])] = key

    extra: list[dict] = []
    seen_edges: set = set()
    seen_points: set = set()

    for ref_id in _extract_topology_refs(topo):
        geom = ttl_geoms.get(ref_id)
        if geom is None:
            c = geomsmap.get(ref_id) or ttl_coords.get(ref_id)
            if c is None:
                continue
            geom = {"type": "LineString" if isinstance(c[0], list) else "Point",
                    "coordinates": c}

        if geom.get("type") == "Point":
            if not want_points:
                continue
            key = tuple(geom["coordinates"])
            if key in seen_points:
                continue
            seen_points.add(key)
            extra.append({"type": "Feature", "id": ref_id,
                          "geometry": geom, "properties": {}})
            continue

        if geom.get("type") != "LineString":
            # A ref that resolves to something deeper than a single edge
            # (Polygon/MultiPolygon/MultiLineString — e.g. a Ring/Face/Shell
            # id reached via a Solid's directed references) isn't a flat
            # point list, so it can't be decomposed by this single-level
            # fallback; the depth-agnostic ttl_components path (see
            # docstring) is the one that actually handles that case.
            continue

        # Edge-like reference (a flat point list)
        if want_edges and ref_id not in seen_edges:
            seen_edges.add(ref_id)
            extra.append({"type": "Feature", "id": ref_id,
                          "geometry": {"type": "LineString",
                                       "coordinates": geom["coordinates"]},
                          "properties": {}})
        if want_points:
            for pt in geom["coordinates"]:
                key = tuple(pt)
                if key in seen_points:
                    continue
                seen_points.add(key)
                pt_id = point_index.get(key, f"{ref_id}:{len(seen_points)}")
                extra.append({"type": "Feature", "id": pt_id,
                              "geometry": {"type": "Point", "coordinates": list(pt)},
                              "properties": {}})

    return extra


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

_GEOM_TO_MODE = {
    "point":           "points",
    "linestring":      "edges",
    "multilinestring": "rings",
    "polygon":         "faces",
    "multipolygon":    "faces",
}


def _densify_spline_topology(topo, spline_pts, geomsmap, ttl_coords, max_offset):
    """
    Interpolate a CubicSpline topology into a densified LineString geometry.

    Uses the `splines` package (via spline_geometry) to fit a natural cubic
    spline through the resolved control points, clamped to the start/end
    tangent directions when the topology supplies startTangentVector/
    endTangentVector. Falls back to a straight polyline through the control
    points if `splines` is not installed or interpolation fails, so the
    transform still yields a reviewable geometry.
    """
    straight = {"type": "LineString", "coordinates": [list(p) for p in spline_pts]}

    try:
        from spline_geometry import densify_spline, tangent_from_references
    except ImportError:
        logger.warning("`splines` package not installed; falling back to a "
                       "straight polyline for CubicSpline topology")
        return straight

    start_tangent = end_tangent = None
    start_vec = topo.get("startTangentVector")
    end_vec = topo.get("endTangentVector")
    if isinstance(start_vec, dict) and isinstance(end_vec, dict):
        start_tangent = tangent_from_references(
            _resolve_refs(start_vec.get("references", []), geomsmap, ttl_coords))
        end_tangent = tangent_from_references(
            _resolve_refs(end_vec.get("references", []), geomsmap, ttl_coords))

    try:
        points = densify_spline(spline_pts, max_offset, start_tangent, end_tangent)
    except Exception as exc:  # noqa: BLE001 - degrade gracefully for any spline failure
        logger.warning("could not interpolate spline (%s); using straight polyline", exc)
        return straight

    return {"type": "LineString", "coordinates": [list(p) for p in points]}


# simplestyle-spec (https://github.com/mapbox/simplestyle-spec) properties used
# to distinguish a spline's tangent segments from the fitted curve. `stroke`
# (colour) is simplestyle; `stroke-dasharray` is a widely-supported extension
# for dashed rendering. GeoJSON itself carries no styling, so these are advisory
# properties honoured by simplestyle-aware renderers.
_TANGENT_STYLE = {
    "stroke": "#d62728",
    "stroke-width": 2,
    "stroke-opacity": 1,
    "stroke-dasharray": "6 4",
}


def _spline_topology_to_features(feature, topo, geomsmap, ttl_coords, max_offset, mode):
    """
    Build the output GeoJSON features for a CubicSpline topology:

    - the fitted spline curve as a LineString (when `edges` in mode);
    - the original control points as Point features (when `points` in mode);
    - the start/end tangent segments (if the topology supplies
      startTangentVector/endTangentVector) as separately-styled dashed
      LineStrings (when `edges` in mode).
    """
    feat_id = feature.get("id")
    refs = topo.get("references", [])
    resolved = _resolve_refs(refs, geomsmap, ttl_coords)
    spline_pts = [p for p in resolved if p is not None]

    features: list[dict] = []

    # Fitted spline curve.
    if "edges" in mode and len(spline_pts) >= 2:
        geom = _densify_spline_topology(topo, spline_pts, geomsmap, ttl_coords, max_offset)
        if geom is not None:
            features.append({**feature, "geometry": geom})

    # Original control points.
    if "points" in mode:
        for ref, coord in zip(refs, resolved):
            if coord is None:
                continue
            features.append({
                "type": "Feature",
                "id": ref if isinstance(ref, str) else None,
                "geometry": {"type": "Point", "coordinates": list(coord)},
                "properties": {"role": "spline-control-point", "spline": feat_id},
            })

    # Start/end tangent segments, drawn with a distinct dashed style.
    if "edges" in mode:
        for position, key in (("start", "startTangentVector"),
                              ("end", "endTangentVector")):
            vec = topo.get(key)
            if not isinstance(vec, dict):
                continue
            seg = [p for p in _resolve_refs(vec.get("references", []), geomsmap, ttl_coords)
                   if p is not None]
            if len(seg) >= 2:
                features.append({
                    "type": "Feature",
                    "id": f"{feat_id}-{position}-tangent" if feat_id else None,
                    "geometry": {"type": "LineString",
                                 "coordinates": [list(p) for p in seg]},
                    "properties": {"role": "spline-tangent", "position": position,
                                   "spline": feat_id, **_TANGENT_STYLE},
                })

    return features


def process(input_data, mode="points,edges,faces", objects=None , number=None, ttl_geoms=None, ttl_coords=None,
            ttl_components=None, namespaces=None, transform_metadata=None,
            densify=False, max_offset=DEFAULT_MAX_OFFSET) -> str:
    if ttl_geoms is None:
        ttl_geoms = {}
    if ttl_coords is None:
        ttl_coords = {}
    if ttl_components is None:
        ttl_components = {}

    if isinstance(input_data, str):
        data = json.loads(input_data)
    else:
        data = json.load(input_data)

    resolved_namespaces = _merge_namespaces(data, transform_metadata, namespaces)
    if resolved_namespaces:
        ttl_geoms = _NamespaceResolvingMap(ttl_geoms, resolved_namespaces)
        ttl_coords = _NamespaceResolvingMap(ttl_coords, resolved_namespaces)
        ttl_components = _NamespaceResolvingMap(ttl_components, resolved_namespaces)

    # Resolve place/geometry CRS(es) and reproject to EPSG:4326 up front —
    # topology resolution below chains raw point coordinates together, which
    # only works if everything is already in one consistent CRS by then.
    _normalize_geometries_to_wgs84(data)

    count = 0
    is_feature = data.get("type") == "Feature"
    if is_feature:
        data = {"type": "FeatureCollection", "features": [data], "crs": data.get("crs")}

    geomsmap: dict = {}
    all_input_features = list(walk_features(data.get("features", [])))

    # Build geomsmap from inline point features
    if "points" in data:
        data["features"] = []
        for pc in data["points"]:
            for feature in pc.get("features", []):
                if "id" in feature:
                    feature.setdefault("properties", {})["feature_id"] = feature["id"]
            if "points" in mode:
                data["features"].extend(pc.get("features", []))
        geomsmap = extract_feature_coordinates(data["points"])
    elif all_input_features:
        point_feats = [f for f in all_input_features
                       if (f.get("geometry") or {}).get("type") == "Point"]
        if point_feats:
            if "points" in mode:
                data["features"] = point_feats
            else:
                data["features"] = []
            geomsmap = {f["id"]: f["geometry"]["coordinates"]
                        for f in point_feats if "id" in f}
        else:
            data["features"] = []

    if not geomsmap and not ttl_coords:
        raise ValueError("No point geometries found in input or TTL files")

    # Process collection-level topology (data["edges"], data["rings"], data["faces"],
    # data["shells"], data["solids"])
    geomtype = {
        "edges": "LineString",
        "rings":  "MultiLineString",
        "faces":  "MultiPolygon",
        "shells": "MultiPolygon",
        "solids": "MultiPolygon",
    }
    parseobjects = ["edges", "rings", "faces", "shells", "solids"]

    # Support additional object keys to parse in form
    # key:{geojson Geometry Type}
    # e.g. parcels:MultiPolygon

    if objects is not None:
        try:
            for obj in objects.split(","):
                objkey,ogeomtype = obj.strip().split(":")
                parseobjects.append(objkey)
                geomtype[objkey] = ogeomtype
        except:
            raise ValueError("invalid list of key:geomtype for extracting objects")

    for feat_type in parseobjects:
        if feat_type not in data:
            continue
        for feat in walk_features(data[feat_type]):
            if "topology" not in feat:
                continue
            topo = feat["topology"]
            expected = topo["type"].lower() + "s"
            if expected != feat_type:
                logger.warning("expected type %s does not match %s", topo["type"].lower(), feat_type)
            if "references" in topo:
                if topo["type"].lower() == "polygon":
                    # A Polygon's references are rings of edge IDs, whose
                    # segments need chaining into flat ring point lists (see
                    # _resolve_polygon_rings) — naively substituting each
                    # edge ID with its own (still edge-shaped) coordinates
                    # via _resolve_refs leaves the result one level too
                    # deeply nested.
                    coords = _resolve_polygon_rings(topo["references"], geomsmap, ttl_coords, ttl_geoms)
                else:
                    coords = _resolve_refs(topo["references"], geomsmap, ttl_coords)
            elif "directed_references" in topo or "shells" in topo:
                # A Solid's topology carries its directed refs under "shells"
                # (pointing at Shell ids) rather than "directed_references",
                # but the {"ref", "orientation"} shape and chaining logic are
                # identical.
                drs = topo.get("directed_references") or topo["shells"]
                coords = [[]]
                startindex = 0
                for node in drs:
                    ref_id = node.get("ref")
                    if ref_id is None:
                        logger.warning("directed reference missing 'ref' (got keys %s), skipping", list(node.keys()))
                        continue
                    c = geomsmap.get(ref_id) or ttl_coords.get(ref_id)
                    if c is None:
                        continue
                    seg = list(c)[:]
                    if node.get("orientation") == "-":
                        coords[0] += list(reversed(seg))[startindex:]
                    else:
                        coords[0] += seg[startindex:]
                    if feat_type == "edges":
                        startindex = 1
            else:
                logger.warning("No references found")
                continue

            feat["geometry"] = {
                "type": geomtype[feat_type],
                "coordinates": coords,
                "properties": feat.get("properties", {}),
            }
            if "id" in feat:
                geomsmap[feat["id"]] = coords
            if feat_type in mode:
                if number and count >= int(number):
                    continue
                count += 1
                data["features"].append(feat)

    # Process individual features with inline topology, resolved via TTL
    for feature in all_input_features:
        topo = feature.get("topology")
        if not topo:
            continue

        feat_id = feature.get("id")

        # Densify arc/circle topology into true curved geometry (opt-in).
        # arc_densify computes the chord vertices of the actual circular
        # arc/circle described by the topology's point references, rather than
        # the straight-line (chord) approximation the default path produces.
        topo_type_lc = (topo.get("type") or "").lower()
        if densify and topo_type_lc in ARC_TOPOLOGY_TYPES:
            arc_coords = _resolve_refs(topo.get("references", []), geomsmap, ttl_coords)
            # ArcByChord/CircleByCenter radius may be a feature-level property
            # (topo-arc schema) rather than on the topology block itself.
            feature_radius = feature.get("radius")
            if feature_radius is None:
                feature_radius = (feature.get("properties") or {}).get("radius")
            try:
                arc_geom = arc_topology_to_geometry(topo, arc_coords, max_offset,
                                                    feature_radius=feature_radius)
            except ValueError as exc:
                logger.warning("could not densify arc feature %r: %s", feat_id, exc)
                arc_geom = None
            if arc_geom is not None:
                feat_mode = _GEOM_TO_MODE.get(arc_geom["type"].lower(), "edges")
                if feat_mode in mode and not (number and count >= int(number)):
                    count += 1
                    data["features"].append({**feature, "geometry": arc_geom})
                continue
            # Fall through to default handling if densification was not possible.
        elif topo_type_lc == "cubicspline":
            # A CubicSpline is always fitted as a proper spline curve via the
            # `splines` package (see _spline_topology_to_features), regardless
            # of the `densify` flag — a spline's defining shape is the fitted
            # curve, not a chord through its control points. In `points` mode
            # the original control points are also emitted, and any start/end
            # tangent vectors are drawn as separately-styled dashed segments.
            existing_ids = {f.get("id") for f in data["features"] if f.get("id")}
            for sf in _spline_topology_to_features(feature, topo, geomsmap,
                                                   ttl_coords, max_offset, mode):
                # Don't re-emit a control point already output as an inline point.
                if (sf.get("properties") or {}).get("role") == "spline-control-point" \
                        and sf.get("id") in existing_ids:
                    continue
                if number and count >= int(number):
                    break
                count += 1
                data["features"].append(sf)
            continue

        resolved_geom = None

        # Option 1: resolve inline topology references by chaining the
        # referenced point/edge coordinates (from inline points or TTL). This
        # is the straight-line (chord) approximation for arc/circle/spline
        # topology when densification is off.
        resolved_geom = _resolve_inline_topology(topo, geomsmap, ttl_coords, ttl_geoms)
        if resolved_geom:
            logger.info("Feature %r: geometry resolved by chaining TTL topology references", feat_id)

        # Option 2: TTL has already fully resolved this feature's geometry by
        # ID (only when inline chaining above produced nothing).
        if resolved_geom is None and feat_id:
            ttl_geom = ttl_geoms.get(feat_id)
            if ttl_geom:
                resolved_geom = ttl_geom
                logger.info("Feature %r: geometry resolved directly from TTL index", feat_id)

        if resolved_geom is None:
            logger.warning("could not resolve topology for feature %r", feat_id)
            continue

        resolved_feature = {**feature, "geometry": resolved_geom}

        geom_type_str = resolved_geom.get("type", "").lower()
        feat_mode = _GEOM_TO_MODE.get(geom_type_str, "faces")
        if feat_mode in mode:
            if number and count >= int(number):
                continue
            count += 1
            data["features"].append(resolved_feature)

        # A feature that only ever resolves as a single Polygon/MultiPolygon
        # (Ring/Face, or Shell/Solid several levels deeper) is still a valid
        # source for -m edges/-m points: decompose it into the edges/points
        # that make it up rather than yielding nothing for those modes.
        if geom_type_str in ("polygon", "multipolygon") and ("edges" in mode or "points" in mode):
            comps = ttl_components.get(feat_id) if feat_id else None
            extra_feats = (_components_to_features(comps, mode) if comps
                           else _decompose_polygon_topology(topo, geomsmap, ttl_geoms, ttl_coords, mode))
            for extra_feat in extra_feats:
                if number and count >= int(number):
                    break
                count += 1
                data["features"].append(extra_feat)

    if not data["features"]:
        return '{ "warning": "no feature geometries generated" }'

    clean_features = [_clean_feature(f) for f in data["features"]]

    if is_feature and len(clean_features) == 1:
        output_data = clean_features[0]
    else:
        output_data = {"type": "FeatureCollection", "features": clean_features}
    output_data["@context"] = [
        "https://opengeospatial.github.io/bblocks/annotated-schemas/geo/features/featureCollection/context.jsonld"
    ]
    if "@context" in data:
        context = data["@context"]
        iterable = context if isinstance(context, List) else [context]
        for c in iterable:
            output_data["@context"].append(c)

    return json.dumps(output_data, indent=2)


# ---------------------------------------------------------------------------
# Host integration (FME PythonCaller-style transformer script)
# ---------------------------------------------------------------------------
# A transform host either execs this module with `transform_metadata` and
# `input_data` already bound as globals, or imports it and calls
# run_transform(input_data, transform_metadata) directly.

def _expand_ttl_glob(pattern: str, base_dirs: list) -> list:
    """Expand a ttl glob pattern. Tries it as-is (relative to the process's
    actual cwd) first, since that's what glob.glob() does natively; if that
    finds nothing and the pattern is relative, retries it joined onto each
    of *base_dirs* in turn.

    This matters because a relative ttl path in a transform's metadata
    (e.g. "_sources/examples/model.ttl" in transforms.yaml) is written
    relative to the building block register root, but a transform host's
    actual process cwd when it invokes the transform isn't guaranteed to be
    that register root — cwd-only resolution silently finds nothing, and
    rdflib's own relative-path fallback then reports a confusingly wrong
    file path rather than a clean "not found". Passing the host-reported
    working directory (transform_metadata.context.working_dir) as a
    base_dirs fallback avoids that.
    """
    matches = sorted(glob_module.glob(pattern))
    if matches or os.path.isabs(pattern):
        return matches
    for base in base_dirs:
        if not base:
            continue
        matches = sorted(glob_module.glob(os.path.join(base, pattern)))
        if matches:
            return matches
    return []


def run_transform(input_data=None, transform_metadata=None) -> str:
    """
    Entry point for OGC Building Blocks-style transform hosts.

    `transform_metadata` exposes `.metadata`, a dict with:
      - "mode": comma-separated feature-type list (default "points,edges,faces")
      - "ttl":  a TTL path, glob pattern, or list of either, providing
                topology for features referenced but not defined inline
      - "namespaces"/"prefixes": optional {prefix: namespace_uri} fallback
                map, used to reconcile bare or unrecognized-prefix JSON refs
                (e.g. "LineP1P2") against TTL URIs (e.g. "http://somens/LineP1P2")
                when the input JSON's own @context and the bblocks example's
                examples.yaml prefixes (transform_metadata.context.example/
                .snippet["prefixes"]) don't already cover the prefix

    Both arguments are optional: a host that execs this module with
    `input_data`/`transform_metadata` already bound as globals doesn't need
    to pass them explicitly — they're picked up from globals when omitted.

    Returns the GeoJSON string a host should bind to `output_data`.
    """
    if input_data is None:
        logger.debug("seeking input_data in globals")
        input_data = globals().get("input_data")
        logger.debug("found input_data in globals")
    if transform_metadata is None:
        transform_metadata = globals().get("transform_metadata")
        logger.debug("found metadata in globals")
    if input_data is None or transform_metadata is None:
        raise RuntimeError(
            "run_transform() requires input_data and transform_metadata, "
            "either as arguments or as globals bound by the host."
        )

    mode = transform_metadata.metadata.get("mode", "points,edges,faces")

    try:
        objects = transform_metadata.metadata.get("objects", None)
    except:
        objects = None

    # Opt-in densification of arc/circle topology into true curved geometry.
    densify = bool(transform_metadata.metadata.get("densify", False))
    max_offset = float(transform_metadata.metadata.get("max_offset", DEFAULT_MAX_OFFSET))

    ttl_geoms_tm: dict = {}
    ttl_coords_tm: dict = {}
    ttl_components_tm: dict = {}
    ttl_val = transform_metadata.metadata.get("ttl")
    if ttl_val:
        ttl_paths = ttl_val if isinstance(ttl_val, list) else [ttl_val]
        context = getattr(transform_metadata, "context", None)
        base_dirs = [
            getattr(context, "working_dir", None),
            getattr(context, "bblock_files_path", None),
        ] if context is not None else []
        expanded = []
        for p in ttl_paths:
            expanded.extend(_expand_ttl_glob(p, base_dirs) or [p])
        ttl_geoms_tm, ttl_coords_tm, ttl_components_tm = load_ttl_geoms(expanded)

    logger.info("running in transformer mode")
    return process(input_data, mode, objects, None, ttl_geoms_tm, ttl_coords_tm, ttl_components_tm,
                    transform_metadata=transform_metadata, densify=densify, max_offset=max_offset)


# Guard on `transform_metadata`'s presence so that a plain `import
# topo2geojson` (e.g. from tests, or from a host calling run_transform()
# itself) stays side-effect free.
if "transform_metadata" in globals():
    output_data = run_transform()


# ---------------------------------------------------------------------------
# bblocks plugin interface
# ---------------------------------------------------------------------------
# A bblocks plugin host discovers transform_types/default_inputs/
# default_outputs on the class and calls transform(metadata), where metadata
# exposes `.input_data` and the same `.metadata` dict run_transform() reads
# ("mode", "ttl").

class Topo2GeoJsonTransform:
    transform_types = ["topo2geojson"]
    default_inputs = ["application/json"]
    default_outputs = ["application/geo+json"]

    def transform(self, metadata):
        return run_transform(metadata.input_data, metadata)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli():
    import argparse

    argparser = argparse.ArgumentParser(
        description="Convert topo-feature JSON to GeoJSON, resolving missing topology from TTL files."
    )
    argparser.add_argument("-i", "--input_data", help="Input JSON file (supports glob)")
    argparser.add_argument("-o", "--output_file", help="Output GeoJSON file")
    argparser.add_argument("-t", "--ttl", action="append", default=[],
                           metavar="TTL_FILE",
                           help="TTL file(s) providing topology for referenced features (repeatable, supports glob)")
    argparser.add_argument("-p", "--print", action="store_true", help="Print output to stdout")
    argparser.add_argument("-v", "--verbose", action="count", default=0,
                           help="Verbose logging: -v for info, -vv for debug (default: warnings only)")
    argparser.add_argument("-n", "--number", default=None, help="Max number of features to include")
    argparser.add_argument("-m", "--mode", default="points,edges,faces",
                           help="Feature types to include (default: points,edges,faces)")

    argparser.add_argument("-k", "--objects", default=None,
                           help="optional root level keys containing objects to convert")
    argparser.add_argument("-d", "--densify", action="store_true",
                           help="Densify Arc/ArcWithCenter/ArcByChord/CircleByCenter "
                                "topology into true curved geometry via arc_densify "
                                "(instead of a straight-chord approximation)")
    argparser.add_argument("--max-offset", type=float, default=DEFAULT_MAX_OFFSET,
                           dest="max_offset",
                           help=f"Maximum chord-to-arc offset (sagitta) for --densify, "
                                f"in input coordinate units (default: {DEFAULT_MAX_OFFSET})")
    argparser.add_argument("-ns", "--namespace", action="append", default=[],
                           metavar="PREFIX=URI",
                           help="Namespace/prefix declaration(s) to try (in addition to any "
                                "declared in the input JSON's own @context or in a transform's "
                                "examples.yaml prefixes) when reconciling bare or unrecognized-"
                                "prefix references (e.g. \"LineP1P2\") against TTL URIs (e.g. "
                                "\"http://somens/LineP1P2\"). Repeatable; PREFIX=URI or a bare URI.")
    args = argparser.parse_args()

    # Map -v count to a logging level: none -> WARNING, -v -> INFO, -vv -> DEBUG.
    level = logging.DEBUG if args.verbose >= 2 else logging.INFO if args.verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(message)s")

    # Expand TTL globs
    ttl_files = []
    for pattern in args.ttl:
        ttl_files.extend(sorted(glob_module.glob(pattern)))

    ttl_geoms_map: dict = {}
    ttl_coords_map: dict = {}
    ttl_components_map: dict = {}
    if ttl_files:
        ttl_geoms_map, ttl_coords_map, ttl_components_map = load_ttl_geoms(ttl_files)

    if not args.input_data:
        print("No input file specified. Use -i <file> -t <ttl> [-o <output>]")
        return

    for f in sorted(glob_module.glob(args.input_data)):
        print(f"Processing {f}")
        with open(f) as fh:
            output = process(fh, args.mode, args.objects, args.number, ttl_geoms_map, ttl_coords_map,
                              ttl_components_map, namespaces=args.namespace,
                              densify=args.densify, max_offset=args.max_offset)
        if args.print:
            print(output)
        if args.output_file:
            with open(args.output_file, "w") as out:
                out.write(output)
            print(f"Written to {args.output_file}")


if __name__ == "__main__":
    _cli()
