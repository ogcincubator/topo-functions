# topo-encodings

Topo Feature solids to JSON-FG 1.0.0 polyhedra and to per-face GeoJSON (RFC 7946), from one strict traversal of solids, shells, faces and rings.
Standard library only: no runtime dependencies; pyproj, the optional `crs` extra, lets the writers take the `place` CRS's axis order from the CRS.

This is a second distribution in [`topo-functions`](../README.md), beside `topo-rdf-geojson`; `topo2geojson`'s outputs and `topo_validator` are unchanged by it.
The mapping (what becomes what, what is lost, what is refused) is in [`docs/encodings.md`](docs/encodings.md).

## Installation

```bash
pip install "topo-encodings[crs] @ git+https://github.com/ogcincubator/topo-functions.git#subdirectory=topo-encodings"
```

Without `[crs]`, pyproj is not installed and the caller states the axis order (`place_axes`).

This provides two console scripts, `topo2jsonfg` and `topo2geojson-faces`, and the OGC Building Blocks transforms `topo2jsonfg` and `topo2geojson-faces` in `topo_encodings.transform`.

## Usage

```python
import json

from topo_encodings import EncodingError, to_geojson_faces, to_jsonfg

doc = json.load(open("solid.json"))

jsonfg = to_jsonfg(
    doc,
    crs=[
        "http://www.opengis.net/def/crs/EPSG/0/7850",  # MGA zone 50
        "http://www.opengis.net/def/crs/EPSG/0/5711",  # AHD
    ],
    # place is in the CRS's axis order, taken from crs with pyproj (the crs extra);
    # without it, state it: place_axes="east-north-up" (NZTM: "north-east-up").
)

# Ellipsoidal heights need a transformation from place (here with pyproj and the
# AUSGeoid2020 grid, installed separately); the library itself downloads nothing.
from pyproj import Transformer

to_gda2020 = Transformer.from_crs("EPSG:7850+5711", "EPSG:7843", always_xy=True)
faces = to_geojson_faces(
    doc,
    crs="http://www.opengis.net/def/crs/EPSG/0/7850",  # for the axis order only
    transformer=lambda e, n, h: to_gda2020.transform(e, n, h, errcheck=True),
)
```

Orientation is physical (right-hand rule in east, north, up), so both writers need the `place` CRS's axis order; `place` is in the CRS's authority (EPSG) axis order, as JSON-FG coordinates are.
With pyproj the order comes from `crs`, and a stated `place_axes` must agree with it; without pyproj, or for a CRS pyproj cannot look up, `place_axes` is used as stated.
Both writers check that every shell faces away from its solid's material; when every shell is inside out they add `AXIS_ORDER_SUSPECTED`, since coordinates in the other axis order give exactly that.
`check_orientation=False` encodes a defective source as it is.
A document that cannot be encoded raises `EncodingError`, whose `problems` list every problem found, each with a stable `code`.

## Tests

```bash
cd topo-encodings
pip install -e ".[test]"
python -m pytest              # the pyproj tests are skipped
pip install -e ".[test,crs]"
python -m pytest              # all of them
```

The tests use synthetic solids and need no network.
