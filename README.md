# topo-rdf-geojson

Two modules for converting topology-based feature models to GeoJSON:

- **`topo_rdf_geojson`** — reads an RDF Turtle topology model (geojson-topo vocabulary) and returns a dict of GeoJSON geometry objects for every feature, indexed by both full URI string and qname (`prefix:local`).
- **`topo2geojson`** — converts topo-feature JSON (points/edges/rings/faces/shells/solids, inline or referenced) into GeoJSON. Some inputs are fully self-contained; others only carry bare topology references to features that live in a separate RDF Turtle model, in which case `topo2geojson` resolves them via `topo_rdf_geojson.load_topo()`.

## Installation

```bash
pip install topo-rdf-geojson

# topo2geojson also needs pyproj (for CRS reprojection); install the extra to get it:
pip install topo-rdf-geojson[geojson]
```

## `topo_rdf_geojson`

### Usage

```python
from topo_rdf_geojson import load_topo

geometries = load_topo("path/to/model.ttl")

# index by full URI
geom = geometries["http://csdm-example-surveys/DP-572532/8446454"]

# index by qname when a matching prefix is declared
geom = geometries["eg2:8446454"]

print(geom)
# {"type": "Polygon", "coordinates": [[[lon, lat], ...]]}
```

### CLI

```bash
topo-rdf-geojson <source.ttl> [--key URI_OR_QNAME] [--keys-only] [--indent N]
```

| Option | Description |
|--------|-------------|
| `--key` | Return geometry for a single feature (URI or qname) |
| `--keys-only` | Print only the index keys |
| `--indent N` | JSON indent level (default 2) |

### Supported topology types

| RDF type | GeoJSON output |
|----------|----------------|
| Point / `geojson:geometry` | Point |
| Edge / `geojson:LineString` | LineString |
| Ring | Polygon (single ring) |
| Face | Polygon (multiple rings) |
| Shell | MultiPolygon |
| Solid | MultiPolygon (union of all faces) |
| `geojson:Polygon` | Polygon (edges auto-chained by adjacency) |

Multiple `geojson:topology` triples on the same feature are merged: all LineStrings → MultiLineString, all Polygons → MultiPolygon, mixed → GeometryCollection.

## `topo2geojson`

### Usage

```python
from topo2geojson import process, load_ttl_geoms

# Fully self-contained input (all topology inline) — no TTL needed
with open("cube-with-void.json") as fh:
    output = process(fh, mode="points,edges,faces", number=None)

# Input whose topology only references features defined elsewhere —
# resolve those via a companion TTL model first
ttl_geoms, ttl_coords = load_ttl_geoms(["topoobjects.ttl"])
with open("parcel1.json") as fh:
    output = process(fh, mode="faces", number=None,
                      ttl_geoms=ttl_geoms, ttl_coords=ttl_coords)
```

`process()` returns a GeoJSON string (a `Feature` if the input was a single Feature, otherwise a `FeatureCollection`). If the input declares a GeoJSON `crs` member (e.g. `{"type": "name", "properties": {"name": "EPSG:7850"}}`), the output geometries are reprojected to WGS84 (EPSG:4326) using [pyproj](https://pyproj4.github.io/pyproj/) — no geopandas/shapely/GDAL required.

### Use as an OGC Building Blocks transform

`topo2geojson.py` doubles as a transform script for the [OGC Building Blocks](https://github.com/opengeospatial/bblocks) convention: a host `exec`s the file with two globals already bound —

- `input_data` — the raw input document (a JSON string or file-like object) to convert
- `transform_metadata` — an object exposing `.metadata`, a dict of parameters for this transform run:
  - `"mode"` — same comma-separated feature-type list as the CLI `-m` flag (default `"points,edges,faces"`)
  - `"ttl"` — a TTL path, a glob pattern, or a list of either, providing topology for features referenced but not defined inline

The script leaves its result in the `output_data` global as a GeoJSON string, annotated with the bblocks `featureCollection` context. To exercise this locally (e.g. from a bblocks transform harness or your own test), bind the globals and `exec` the module:

```python
import types

transform_metadata = types.SimpleNamespace(metadata={
    "mode": "faces",
    "ttl": "tests/topoobjects.ttl",
})
with open("tests/parcel1.json") as fh:
    input_data = fh.read()

ns = {
    "transform_metadata": transform_metadata,
    "input_data": input_data,
    "__name__": "topo2geojson_transform",
}
exec(open("topo2geojson.py").read(), ns)
output_data = ns["output_data"]
```

This path is only taken when `transform_metadata` is present in the executing namespace, so a plain `import topo2geojson` (as used by the CLI and the test suite) never triggers it.

### CLI

```bash
topo2geojson -i <input.json> [-t <model.ttl> ...] [-o <output.json>] [-m MODE] [-n NUMBER] [-p]
```

| Option | Description |
|--------|-------------|
| `-i`, `--input_data` | Input JSON file (supports glob) |
| `-t`, `--ttl` | TTL file providing topology for referenced features (repeatable, supports glob) |
| `-o`, `--output_file` | Output GeoJSON file |
| `-m`, `--mode` | Comma-separated feature types to include: `points`, `edges`, `faces` (default: `points,edges,faces`) |
| `-n`, `--number` | Max number of features to include |
| `-p`, `--print` | Print output to stdout |

Examples:

```bash
# Self-contained input — no -t needed
topo2geojson -i tests/cube-with-void.json -m faces -o cube-faces.geojson

# Input needs an external TTL to resolve its topology references
topo2geojson -i tests/parcel1.json -t tests/topoobjects.ttl -m faces -o parcel1.geojson
```

## Tests

```bash
pip install topo-rdf-geojson[test]
pytest
```

Tests persist the GeoJSON they generate under `tests/output/`, split by submodule (`tests/output/topo_rdf_geojson/`, `tests/output/topo2geojson/`) so outputs can be inspected afterward.

## Dependencies

- [rdflib](https://rdflib.readthedocs.io/) >= 6.0 (both modules)
- [pyproj](https://pyproj4.github.io/pyproj/) >= 3.5 (`topo2geojson` only, for CRS reprojection; install via the `geojson` extra)
