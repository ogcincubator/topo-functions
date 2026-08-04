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

### Namespace/prefix resolution

A JSON topology reference may be a bare local name (`"LineP1P2"`) or carry a prefix the TTL graph doesn't itself declare (`"eg:LineP1P2"`). To reconcile these against the TTL's real URIs (`"http://somens/LineP1P2"`), pass an optional `namespaces` map of `{prefix: namespace_uri}` — for an unprefixed ref, every declared namespace is tried as `namespace_uri + local_name`; for a prefixed ref, the namespace registered for that exact prefix is tried first.

`namespaces` merges declarations from several sources, in order of preference (first declaration of a given prefix wins):

1. the input JSON's own `@context` (a JSON-LD context dict, or a list containing one)
2. [examples.yaml prefixes](https://ogcincubator.github.io/bblocks-docs/create/examples#prefixes), exposed to bblocks transforms via the [transform context](https://ogcincubator.github.io/bblocks-docs/create/transforms#transform-context) (`transform_metadata.context.example`/`.snippet["prefixes"]`)
3. metadata globals (`transform_metadata.metadata["namespaces"]` or `["prefixes"]`) or the CLI's `-ns`/`--namespace` arguments — the fallback, used when neither of the above declares the prefix

```python
output = process(fh, mode="faces", number=None,
                  ttl_geoms=ttl_geoms, ttl_coords=ttl_coords,
                  namespaces={"eg": "http://somens/"})
```

### Use as an OGC Building Blocks transform

`topo2geojson.py` doubles as a transform script for the [OGC Building Blocks](https://github.com/opengeospatial/bblocks) convention, which runs with two values already bound —

- `input_data` — the raw input document (a JSON string or file-like object) to convert
- `transform_metadata` — an object exposing `.metadata`, a dict of parameters for this transform run:
  - `"mode"` — same comma-separated feature-type list as the CLI `-m` flag (default `"points,edges,faces"`)
  - `"ttl"` — a TTL path, a glob pattern, or a list of either, providing topology for features referenced but not defined inline
  - `"namespaces"`/`"prefixes"` — optional `{prefix: namespace_uri}` fallback map for [namespace/prefix resolution](#namespaceprefix-resolution), used below the input JSON's own `@context` and any examples.yaml prefixes the host exposes via `transform_metadata.context`

Call `run_transform()` to get the GeoJSON string to bind to `output_data`. Both arguments are optional — if omitted, they're picked up from `input_data`/`transform_metadata` globals (e.g. a host that `exec`s the whole module, or one that sets them as module attributes after importing it):

```python
from topo2geojson import run_transform

output_data = run_transform(input_data, transform_metadata)   # explicit args
# or, if a host has already bound input_data/transform_metadata as globals:
output_data = run_transform()
```

Hosts that discover transforms as installed plugin classes (rather than `exec`ing a `code:` snippet) can instead use `Topo2GeoJsonTransform`:

```python
from topo2geojson import Topo2GeoJsonTransform

plugin = Topo2GeoJsonTransform()
plugin.transform_types    # ["topo2geojson"]
plugin.default_inputs     # ["application/json"]
plugin.default_outputs    # ["application/geo+json"]

output_data = plugin.transform(metadata)   # metadata.input_data + metadata.metadata (mode/ttl)
```

The transform configuration looks like this:
```yaml
transforms:
  - id: Faces-as-Polygons
    description: extract GeoJSON Polygons for faces (and example of usage)
    type: python
    inputs:
      mediaTypes: [ application/json ]
    outputs:
      mediaTypes: [ application/geo+json ]
    metadata:
      dependencies:
        pip: [git+https://github.com/ogcincubator/topo-functions.git ]
        python: ">=3.10"   # optional; skipped if not met
      ttl: test.ttl
      mode: faces
    code: |
      from topo2geojson import run_transform
      output_data = run_transform()
```


### CLI

```bash
topo2geojson -i <input.json> [-t <model.ttl> ...] [-o <output.json>] [-m MODE] [-k KEY:TYPE ...] [-n NUMBER] [-p]
```

| Option | Description |
|--------|-------------|
| `-i`, `--input_data` | Input JSON file (supports glob) |
| `-t`, `--ttl` | TTL file providing topology for referenced features (repeatable, supports glob) |
| `-o`, `--output_file` | Output GeoJSON file |
| `-m`, `--mode` | Comma-separated feature types to include: `points`, `edges`, `faces`, `shells`, `solids`, plus any key registered via `-k` (default: `points,edges,faces`) |
| `-k`, `--objects` | Comma-separated `key:GeometryType` pairs registering additional top-level object keys to parse, beyond the built-in `edges`/`rings`/`faces`/`shells`/`solids` (e.g. `-k parcels:Polygon`) |
| `-n`, `--number` | Max number of features to include |
| `-p`, `--print` | Print output to stdout |
| `-ns`, `--namespace` | `PREFIX=URI` (or a bare `URI`) [namespace/prefix declaration](#namespaceprefix-resolution) for resolving references (repeatable; the last-resort fallback below the input JSON's own `@context` and examples.yaml prefixes) |

A feature that only ever resolves as a single Polygon or MultiPolygon (a Ring/Face, or a Shell/Solid several levels deeper — typically one whose topology is entirely TTL-referenced, with no top-level `points`/`edges` collection of its own) is still a valid source for `-m edges`/`-m points`: it's decomposed down to the edges/points that make it up, however many Ring/Face/Shell/Solid levels sit in between, rather than yielding nothing for those modes.

#### `-k`/`--objects`: custom top-level object keys

By default, only `data["edges"]`, `data["rings"]`, `data["faces"]`, `data["shells"]` and `data["solids"]` are scanned for features carrying a `topology` block. `-k` takes one `key:GeometryType` pair, or several comma-separated in a single `-k` argument (e.g. `-k a:TypeA,b:TypeB` — not `-k a:TypeA -k b:TypeB`), registering additional top-level keys to scan the same way, each tagged with whichever GeoJSON geometry type its resolved coordinates should be labeled as. This is how domain-specific collection names (e.g. a `"parcels"` array of Features with `topology.type: "Polygon"`, as used by CSDM's `extended_example.json`) get processed without renaming them to `"faces"`:

```bash
topo2geojson -i extended_example.json -m parcels -k parcels:Polygon -p
```

`-m parcels` is required alongside `-k parcels:Polygon` — registering the key only makes it eligible for parsing; `-m` still controls which resolved feature types actually make it into the output. A `Polygon`-typed entry whose `topology.references` is a ring of edge IDs has those edges chained (and flipped as needed to match orientation) into a single flat ring, the same as the built-in `faces` key does — not left as a raw list of unflattened edge segments.

Examples:

```bash
# Self-contained input — no -t needed
topo2geojson -i tests/cube-with-void.json -m faces -o cube-faces.geojson

# Input needs an external TTL to resolve its topology references
topo2geojson -i tests/parcel1.json -t tests/topoobjects.ttl -m faces -o parcel1.geojson

# Custom object key ("parcels") tagged as Polygon geometry
topo2geojson -i extended_example.json -m parcels -k parcels:Polygon -p
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
