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
from typing import Generator, List

from pyproj import Transformer

from topo_rdf_geojson import load_topo


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
    Parse TTL files with topo_geojson.load_topo() and return two dicts:
      geoms:  local_name / full_key / qname → {"type": ..., "coordinates": ...}
      coords: local_name / full_key / qname → coordinates only (for geomsmap fallback)
    """
    geoms = {}
    coords = {}
    for path in ttl_files:
        print(f"Loading TTL: {path}")
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
    unique = len({id(v) for v in geoms.values()})
    print(f"  -> {unique} unique TTL geometries indexed")
    return geoms, coords


# ---------------------------------------------------------------------------
# Topology resolution helpers
# ---------------------------------------------------------------------------

def _chain_edges(edge_coord_lists: list) -> list:
    """
    Chain a list of edge LineString coordinate sequences into a closed ring.
    Direction of each edge is auto-detected by adjacency with the previous end-point.
    """
    chain: list = []
    for seg in edge_coord_lists:
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
                # Non-adjacent — append without first point (best-effort)
                chain.extend(seg[1:])
    if chain and chain[0] != chain[-1]:
        chain.append(chain[0])
    return chain


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

    def lookup_geom(ref_id):
        return ttl_geoms.get(ref_id)

    if "references" in topo:
        refs = topo["references"]

        if topo_type == "polygon":
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
            return {"type": "Polygon", "coordinates": rings} if rings else None

        elif topo_type == "linestring":
            pts = [c for r in refs if (c := lookup_coords(r)) is not None]
            return {"type": "LineString", "coordinates": pts} if len(pts) >= 2 else None

        else:
            pts = [c for r in refs if (c := lookup_coords(r)) is not None]
            return {"type": topo.get("type", "Unknown"), "coordinates": pts} if pts else None

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
# Main processing
# ---------------------------------------------------------------------------

_GEOM_TO_MODE = {
    "point":           "points",
    "linestring":      "edges",
    "multilinestring": "rings",
    "polygon":         "faces",
    "multipolygon":    "faces",
}


def process(input_data, mode="points,edges,faces", number=None, ttl_geoms=None, ttl_coords=None) -> str:
    if ttl_geoms is None:
        ttl_geoms = {}
    if ttl_coords is None:
        ttl_coords = {}

    if isinstance(input_data, str):
        data = json.loads(input_data)
    else:
        data = json.load(input_data)

    count = 0
    is_feature = data.get("type") == "Feature"
    if is_feature:
        data = {"type": "FeatureCollection", "features": [data], "crs": data.get("crs")}

    crs_name = ((data.get("crs") or {}).get("properties") or {}).get("name")
    epsg_code = crs_name.split(":")[-1] if crs_name else "4326"

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

    # Process collection-level topology (data["edges"], data["rings"], data["faces"])
    geomtype = {
        "edges": "LineString",
        "rings":  "MultiLineString",
        "faces":  "MultiPolygon",
        "shells": "Solid",
        "solids": "Solid",
    }
    for feat_type in ["edges", "rings", "faces"]:
        if feat_type not in data:
            continue
        for feat in walk_features(data[feat_type]):
            if "topology" not in feat:
                continue
            topo = feat["topology"]
            expected = topo["type"].lower() + "s"
            if expected != feat_type:
                print(f"Warning: expected type {topo['type'].lower()} does not match {feat_type}")

            if "references" in topo:
                coords = [
                    geomsmap.get(node) or ttl_coords.get(node)
                    for node in topo["references"]
                ]
                coords = [c for c in coords if c is not None]
            elif "directed_references" in topo:
                drs = topo["directed_references"]
                coords = [[]]
                startindex = 0
                for node in drs:
                    c = geomsmap.get(node["ref"]) or ttl_coords.get(node["ref"])
                    if c is None:
                        continue
                    seg = list(c)[:]
                    if node["orientation"] == "-":
                        coords[0] += list(reversed(seg))[startindex:]
                    else:
                        coords[0] += seg[startindex:]
                    if feat_type == "edges":
                        startindex = 1
            else:
                print("No references found")
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
        resolved_geom = None


        # Option 1: resolve inline topology references using TTL edge/point coords if required
        if resolved_geom is None:
            resolved_geom = _resolve_inline_topology(topo, geomsmap, ttl_coords, ttl_geoms)
            if resolved_geom:
                print(f"Feature {feat_id!r}: geometry resolved by chaining TTL topology references")

        # Option 2: TTL has already fully resolved this feature's geometry by ID
        if feat_id:
            ttl_geom = ttl_geoms.get(feat_id)
            if ttl_geom:
                resolved_geom = ttl_geom
                print(f"Feature {feat_id!r}: geometry resolved directly from TTL index")

        if resolved_geom is None:
            print(f"Warning: could not resolve topology for feature {feat_id!r}")
            continue

        resolved_feature = {**feature, "geometry": resolved_geom}

        geom_type_str = resolved_geom.get("type", "").lower()
        feat_mode = _GEOM_TO_MODE.get(geom_type_str, "faces")
        if feat_mode in mode:
            if number and count >= int(number):
                continue
            count += 1
            data["features"].append(resolved_feature)

    if not data["features"]:
        print("No feature geometries generated")
        return "{}"

    clean_features = [_clean_feature(f) for f in data["features"]]

    print(f"Source CRS EPSG:{epsg_code}")
    if epsg_code != "4326":
        transformer = Transformer.from_crs(f"EPSG:{epsg_code}", "EPSG:4326", always_xy=True)
        for feature in clean_features:
            _reproject_geometry(feature.get("geometry"), transformer)
        print("Transformed to EPSG:4326")

    if is_feature:
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

def run_transform(input_data=None, transform_metadata=None) -> str:
    """
    Entry point for OGC Building Blocks-style transform hosts.

    `transform_metadata` exposes `.metadata`, a dict with:
      - "mode": comma-separated feature-type list (default "points,edges,faces")
      - "ttl":  a TTL path, glob pattern, or list of either, providing
                topology for features referenced but not defined inline

    Both arguments are optional: a host that execs this module with
    `input_data`/`transform_metadata` already bound as globals doesn't need
    to pass them explicitly — they're picked up from globals when omitted.

    Returns the GeoJSON string a host should bind to `output_data`.
    """
    if input_data is None:
        input_data = globals().get("input_data")
    if transform_metadata is None:
        transform_metadata = globals().get("transform_metadata")
    if input_data is None or transform_metadata is None:
        raise RuntimeError(
            "run_transform() requires input_data and transform_metadata, "
            "either as arguments or as globals bound by the host."
        )

    mode = transform_metadata.metadata.get("mode", "points,edges,faces")

    ttl_geoms_tm: dict = {}
    ttl_coords_tm: dict = {}
    ttl_val = transform_metadata.metadata.get("ttl")
    if ttl_val:
        ttl_paths = ttl_val if isinstance(ttl_val, list) else [ttl_val]
        expanded = []
        for p in ttl_paths:
            expanded.extend(sorted(glob_module.glob(p)) or [p])
        ttl_geoms_tm, ttl_coords_tm = load_ttl_geoms(expanded)

    print("running in transformer mode")
    return process(input_data, mode, None, ttl_geoms_tm, ttl_coords_tm)


# Guard on `transform_metadata`'s presence so that a plain `import
# topo2geojson` (e.g. from tests, or from a host calling run_transform()
# itself) stays side-effect free.
if "transform_metadata" in globals():
    output_data = run_transform()


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
    argparser.add_argument("-n", "--number", default=None, help="Max number of features to include")
    argparser.add_argument("-m", "--mode", default="points,edges,faces",
                           help="Feature types to include (default: points,edges,faces)")
    args = argparser.parse_args()

    # Expand TTL globs
    ttl_files = []
    for pattern in args.ttl:
        ttl_files.extend(sorted(glob_module.glob(pattern)))

    ttl_geoms_map: dict = {}
    ttl_coords_map: dict = {}
    if ttl_files:
        ttl_geoms_map, ttl_coords_map = load_ttl_geoms(ttl_files)

    if not args.input_data:
        print("No input file specified. Use -i <file> -t <ttl> [-o <output>]")
        return

    for f in sorted(glob_module.glob(args.input_data)):
        print(f"Processing {f}")
        with open(f) as fh:
            output = process(fh, args.mode, args.number, ttl_geoms_map, ttl_coords_map)
        if args.print:
            print(output)
        if args.output_file:
            with open(args.output_file, "w") as out:
                out.write(output)
            print(f"Written to {args.output_file}")


if __name__ == "__main__":
    _cli()
