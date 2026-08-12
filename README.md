# topo-rdf-geojson

Two modules for converting topology-based feature models to GeoJSON:

- **`topo_rdf_geojson`** — reads an RDF Turtle topology model (geojson-topo vocabulary) and returns a dict of GeoJSON geometry objects for every feature, indexed by both full URI string and qname (`prefix:local`).
- **`topo2geojson`** — converts topo-feature JSON (points/edges/rings/faces/shells/solids, inline or referenced) into GeoJSON. Some inputs are fully self-contained; others only carry bare topology references to features that live in a separate RDF Turtle model, in which case `topo2geojson` resolves them via `topo_rdf_geojson.load_topo()`. It also renders the non-linear [topo-arc](#arc-circle-and-spline-topology-curved-geometry) topology types (`Arc`, `ArcWithCenter`, `ArcByChord`, `CircleByCenter`, `CubicSpline`) as true curved geometry.

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

### Arc, circle and spline topology (curved geometry)

Beyond the straight-line topology types, `topo2geojson` renders the non-linear
`topo-arc` building-block topology types — which describe curves by *reference*
to point features rather than by storing vertices — as true curved GeoJSON. The geometry each type leaves implicit
(a circle centre, a sweep direction, a spline's shape) is computed by two
companion modules, `arc_geometry` (via [`arc_densify`](README_arc_densify.md))
and `spline_geometry` (via the [`splines`](https://pypi.org/project/splines/)
package); see [`README_arc_densify.md`](README_arc_densify.md) for the geometry
details.

| `topology.type` | `references` (ordered points) | Extra properties | GeoJSON output |
|-----------------|-------------------------------|------------------|----------------|
| `Arc` | start, point-on-arc, end (3) | — | `LineString` (densified arc) |
| `ArcWithCenter` | start, end, centre (3) | `orientation`: `"cw"`/`"ccw"` | `LineString` (densified arc) |
| `ArcByChord` | start, end (2) | `radius`, `orientation`: `"cw"`/`"ccw"` | `LineString` (densified arc) |
| `CircleByCenter` | centre (1) | `radius` | `Polygon` (densified circle) |
| `CubicSpline` | control points (≥3, or ≥2 with tangents) | optional `startTangentVector` / `endTangentVector` | `LineString` (fitted spline) |

The `references` resolve like any other topology reference — inline point
features, or bare/prefixed IDs resolved against a TTL model (see
[Namespace/prefix resolution](#namespaceprefix-resolution)). `orientation` and
the tangent vectors are read from the feature's `topology` block. For
`ArcByChord`/`CircleByCenter`, `radius` is read from the `topology` block if
present, otherwise from the feature's own `radius` property (per the topo-arc
schema, which allows the radius to live at either level). A
`startTangentVector`/`endTangentVector` is itself a small object with a
two-point `references` list; the tangent direction is the vector from the first
referenced point to the second.

**When curves are produced:**

- **`CubicSpline` is always fitted** as a spline curve whenever the type is
  encountered — a spline's defining shape *is* the fitted curve, not a chord
  through its control points. When `startTangentVector`/`endTangentVector` are
  supplied, the spline is *clamped* so its start/end directions match them.
- **`Arc`/`ArcWithCenter`/`ArcByChord`/`CircleByCenter` are rendered as true
  curves only when densification is enabled** (see below). Otherwise their point
  references are chained directly into a straight-line (chord) approximation.

**Additional spline output.** When a `CubicSpline` is rendered:

- in `points` mode, its original control points are also emitted as `Point`
  features (`properties.role = "spline-control-point"`), so the referenced
  points show up even when they live only in a TTL model. Points already
  emitted inline are not duplicated.
- when `startTangentVector`/`endTangentVector` are present, each tangent is
  drawn (in `edges` mode) as its own two-point `LineString`
  (`properties.role = "spline-tangent"`), styled distinctly from the fitted
  curve using [simplestyle-spec](https://github.com/mapbox/simplestyle-spec)
  properties — a `stroke` colour plus a `stroke-dasharray` for dashed
  rendering. GeoJSON has no native styling, so these are advisory properties
  honoured by simplestyle-aware renderers.

**Parameters:**

| Parameter | `process()` arg | Transform metadata | CLI flag | Meaning |
|-----------|-----------------|--------------------|----------|---------|
| Densify arcs/circles | `densify=True` | `densify: true` | `-d`, `--densify` | Render `Arc`/`Circle` types as true curves rather than chords (splines are always fitted regardless) |
| Curve tolerance | `max_offset=0.02` | `max_offset: 0.02` | `--max-offset 0.02` | Maximum chord-to-curve offset (sagitta), in input coordinate units, for densified arcs and fitted splines. Smaller → more vertices. Default `0.02` |
| Spline parameterization | `spline_alpha=0.5` | `spline_alpha: 0.5` | `--spline-alpha 0.5` | Catmull-Rom α for `CubicSpline` fitting: `0` uniform, `0.5` centripetal (default), `1` chordal. See below. |

#### Spline fitting algorithm

`CubicSpline` topology is interpolated with the [`splines`](https://pypi.org/project/splines/)
package as a **natural cubic spline** that passes through every control point.
Two aspects are tunable:

- **`max_offset` (tolerance).** The fitted curve is continuous; it is sampled
  into output vertices by *adaptive subdivision* — a segment is split until the
  curve's deviation from the chord drops below `max_offset` (the same sagitta
  tolerance used for arcs). Smaller values give more vertices / a smoother
  polyline. Because the tolerance is in coordinate units, splines (like arcs)
  are fitted in the input's **native CRS** and reprojected, so `max_offset`
  stays meaningful even when output is WGS84 degrees.

- **`spline_alpha` (parameterization).** The knot spacing used along the curve,
  as the Catmull-Rom α exponent applied to the distances between control points:

  | α | Name | Behaviour |
  |---|------|-----------|
  | `0` | uniform | Ignores point spacing. **Overshoots / kinks** badly when points are unevenly spaced. |
  | `0.5` | **centripetal** (default) | Guaranteed no cusps or self-intersections; follows the control polygon closely. Recommended. |
  | `1` | chordal | Even smoother through very uneven spacing, but can bulge away from the control polygon. |

  Real survey boundaries mix long and very short segments; uniform
  parameterization produces sharp artefacts at the closely-spaced points, so
  the default is **centripetal**. Use `--spline-alpha 1` for a smoother
  (chordal) curve, or `0` to reproduce uniform behaviour.

- **Tangents.** When the topology supplies `startTangentVector` /
  `endTangentVector`, the spline is *clamped* so its start/end directions match
  those vectors; otherwise natural (zero-second-derivative) end conditions are
  used.

Both 2-D and 3-D control points are supported; a spline whose points carry a
Z value is interpolated in 3-D (Z carried through to the output vertices).

**Curves as edges of rings/polygons, and native-CRS fitting.** Arc/circle/spline
topology is generated wherever it appears — not only on standalone features but
also on features in the `edges`/`rings`/`faces` collections (or a custom `-k`
collection). When such a curve is an *edge* of a ring or `Polygon`, the fitted
curve (not a straight chord through its points) is what gets chained into the
ring. Because `max_offset` is expressed in the source coordinate units, curves
are densified in their **native CRS** (captured before the up-front reprojection
to WGS84) and the fitted result is reprojected — so the tolerance stays
meaningful even when the output is in lon/lat degrees.

```python
from topo2geojson import process

# Arc/circle features densified into true curves; splines fitted either way.
with open("arc_by_center.json") as fh:
    output = process(fh, mode="edges,faces",
                     densify=True, max_offset=0.02,
                     ttl_geoms=ttl_geoms, ttl_coords=ttl_coords)
```

```bash
# CLI equivalent
topo2geojson -i arc_by_center.json -t referenced-objects.ttl \
    -m edges,faces --densify --max-offset 0.02 -p
```

### JSON-FG `place` and multi-CRS input

A [JSON-FG](https://docs.ogc.org/DRAFTS/21-045.html) feature may carry its geometry under `place` (native CRS, `geometry` left `null`) instead of `geometry` (always WGS84). `place` is reprojected to EPSG:4326 and copied into `geometry` — a feature whose `geometry` is already populated is left untouched, since that's already trusted as the correct WGS84 rendering. The CRS applied to a given `place` is resolved in order:

1. the feature's own `coordRefSys`
2. its containing `FeatureCollection`'s `coordRefSys`
3. the document root's `coordRefSys`
4. the document root's `horizontalCRS` (a convention used by the CSDM/topo-feature example data, not part of JSON-FG itself)

This reprojection happens up front, before any topology resolution — a document can legitimately mix features sourced from different CRSs (e.g. two different `FeatureCollection`s each declaring their own `coordRefSys`), and everything needs to already be in one consistent CRS by the time topology resolution starts chaining coordinates together (matching adjacent edge endpoints, etc.). A single reprojection pass over the finished output — the old approach — can't correctly handle more than one source CRS at a time.

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
  - `"densify"` — `true` to render `Arc`/`ArcWithCenter`/`ArcByChord`/`CircleByCenter` topology as true curves rather than chord approximations (`CubicSpline` is always fitted regardless); default `false`. See [Arc, circle and spline topology](#arc-circle-and-spline-topology-curved-geometry)
  - `"max_offset"` — maximum chord-to-curve offset (sagitta) for densified arcs and fitted splines, in input coordinate units; default `0.02`
  - `"spline_alpha"` — `CubicSpline` parameterization exponent (`0` uniform, `0.5` centripetal, `1` chordal); default `0.5`. See [Spline fitting algorithm](#spline-fitting-algorithm)
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
topo2geojson -i <input.json> [-t <model.ttl> ...] [-o <output.json>] [-m MODE] [-k KEY:TYPE ...] [-n NUMBER] [-d] [--max-offset F] [--spline-alpha A] [-p]
```

| Option | Description |
|--------|-------------|
| `-i`, `--input_data` | Input JSON file (supports glob) |
| `-t`, `--ttl` | TTL file providing topology for referenced features (repeatable, supports glob) |
| `-o`, `--output_file` | Output GeoJSON file |
| `-m`, `--mode` | Comma-separated feature types to include: `points`, `edges`, `faces`, `shells`, `solids`, plus any key registered via `-k` (default: `points,edges,faces`). Densified arcs are `edges`; a densified circle is `faces` |
| `-k`, `--objects` | Comma-separated `key:GeometryType` pairs registering additional top-level object keys to parse, beyond the built-in `edges`/`rings`/`faces`/`shells`/`solids` (e.g. `-k parcels:Polygon`) |
| `-n`, `--number` | Max number of features to include |
| `-d`, `--densify` | Render `Arc`/`ArcWithCenter`/`ArcByChord`/`CircleByCenter` topology as true curved geometry instead of a straight-chord approximation (`CubicSpline` is always fitted). See [Arc, circle and spline topology](#arc-circle-and-spline-topology-curved-geometry) |
| `--max-offset` | Maximum chord-to-curve offset (sagitta) for `--densify` and spline fitting, in input coordinate units (default `0.02`) |
| `--spline-alpha` | `CubicSpline` parameterization: `0` uniform, `0.5` centripetal (default), `1` chordal. See [Spline fitting algorithm](#spline-fitting-algorithm) |
| `-p`, `--print` | Print output to stdout |
| `-ns`, `--namespace` | `PREFIX=URI` (or a bare `URI`) [namespace/prefix declaration](#namespaceprefix-resolution) for resolving references (repeatable; the last-resort fallback below the input JSON's own `@context` and examples.yaml prefixes) |

A feature that only ever resolves as a single Polygon or MultiPolygon (a Ring/Face, or a Shell/Solid several levels deeper — typically one whose topology is entirely TTL-referenced, with no top-level `points`/`edges` collection of its own) is still a valid source for `-m edges`/`-m points`: it's decomposed down to the edges/points that make it up, however many Ring/Face/Shell/Solid levels sit in between, rather than yielding nothing for those modes.

#### `-k`/`--objects`: custom top-level object keys

By default, only `data["edges"]`, `data["rings"]`, `data["faces"]`, `data["shells"]` and `data["solids"]` are scanned for features carrying a `topology` block. `-k` takes one `key:GeometryType` pair, or several comma-separated in a single `-k` argument (e.g. `-k a:TypeA,b:TypeB` — not `-k a:TypeA -k b:TypeB`), registering additional top-level keys to scan the same way, each tagged with whichever GeoJSON geometry type its resolved coordinates should be labeled as. This is how domain-specific collection names (e.g. a `"parcels"` array of Features with `topology.type: "Polygon"`, as used by CSDM's `extended_example.json`) get processed without renaming them to `"faces"`:

```bash
topo2geojson -i extended_example.json -m parcels -k parcels:Polygon -p
```

`-m parcels` is required alongside `-k parcels:Polygon` — registering the key only makes it eligible for parsing; `-m` still controls which resolved feature types actually make it into the output. A `Polygon`-typed entry whose `topology.references` is a ring of edge IDs has those edges chained (and flipped as needed to match orientation) into a single flat ring, the same as the built-in `faces` key does — not left as a raw list of unflattened edge segments.

A feature within such a collection whose `topology.type` is **`AggregatePolygon`** is emitted as a GeoJSON **`MultiPolygon`** regardless of the geometry type registered for the key: its `references` are the ids of the `Polygon` features to combine (which must resolve earlier in the same collection), and collecting their ring lists yields the correct MultiPolygon nesting. This matches CSDM `extended_example.json` parcels like `BalanceParcel`.

#### Skipped topology types

Some topology types describe non-spatial relations rather than renderable geometry and are silently skipped (never emitted, no warning): currently **`SubtendedAngle`** (an observed angle between two vectors, as in the CSDM survey examples).

Examples:

```bash
# Self-contained input — no -t needed
topo2geojson -i tests/cube-with-void.json -m faces -o cube-faces.geojson

# Input needs an external TTL to resolve its topology references
topo2geojson -i tests/parcel1.json -t tests/topoobjects.ttl -m faces -o parcel1.geojson

# Custom object key ("parcels") tagged as Polygon geometry
topo2geojson -i extended_example.json -m parcels -k parcels:Polygon -p

# Arc/circle topology densified into true curves (splines fitted either way)
topo2geojson -i arc_by_center.json -t referenced-objects.ttl -m edges,faces --densify --max-offset 0.02 -p
```

## Tests

```bash
pip install topo-rdf-geojson[test]
pytest
```

Tests persist the GeoJSON they generate under `tests/output/`, split by submodule (`tests/output/topo_rdf_geojson/`, `tests/output/topo2geojson/`) so outputs can be inspected afterward.

## Dependencies

- [rdflib](https://rdflib.readthedocs.io/) >= 6.0 (both modules) — MIT/BSD
- [pyproj](https://pyproj4.github.io/pyproj/) >= 3.5 (`topo2geojson`, for CRS reprojection) — MIT
- [splines](https://pypi.org/project/splines/) >= 0.3 (`topo2geojson`, for `CubicSpline` fitting) — MIT
- [numpy](https://numpy.org/) >= 1.21 (required by `splines`) — BSD-3-Clause

All third-party dependencies are distributed under permissive (MIT / BSD-3-Clause) licences compatible with this project's MIT licence; full licence texts and attributions are in [`LICENCE.md`](LICENCE.md).
