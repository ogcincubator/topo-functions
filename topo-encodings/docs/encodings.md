# Topo Feature encodings: the mapping

This document states how `topo_encodings` turns the solids of a Topo Feature document into JSON-FG 1.0.0 and into per-face GeoJSON: which element becomes which member, what is lost, and what is refused.
It is the contract the writers are tested against.
Reading JSON-FG and GeoJSON back into a Topo Feature document is planned for a later pull request and will be added here.

- [Why the mapping is needed](#why-the-mapping-is-needed)
- [Reading the Topo Feature document](#reading-the-topo-feature-document)
- [JSON-FG 1.0.0](#json-fg-100)
- [Per-face GeoJSON](#per-face-geojson)
- [Coordinates, CRS and heights](#coordinates-crs-and-heights)
- [What is lost](#what-is-lost)
- [What is refused](#what-is-refused)
- [Transforms and command line](#transforms-and-command-line)
- [Open questions](#open-questions)

## Why the mapping is needed

JSON-FG and GeoJSON have no topology: a JSON-FG `Polyhedron` repeats the coordinates of every face it bounds, and GeoJSON has no solids at all.
Topo Feature describes solids by references (solids to shells, shells to faces, faces to rings, rings to edges, edges to points), each with an orientation.
The mapping is therefore the only place where the correspondence between the two is stated: which faces make up a shell, which way each face faces in each solid, and what cannot be carried across.

## Reading the Topo Feature document

Both writers read the document through one strict traversal (`topo_encodings.traverse`).

| Topo Feature | Read as |
| :- | :- |
| `points`, `edges`, `rings`, `faces`, `shells`, `solids` | Each a list of FeatureCollections; the features of every collection under a key are read. |
| Point `place`, `geometry` | 2D or 3D `Point` coordinates; a point used by a solid must be 3D in the coordinate source a writer uses. |
| Edge `topology.references` | `[start, end]`; only straight edges with two points are supported. Features in `edges` that no ring uses (for example observed vectors of a parcel boundary) are not checked. |
| Ring, face, shell, solid `topology.directed_references` | `{"ref", "orientation"}`; a missing `orientation` is `+`. |
| First ring of a face | Its outer boundary; the others are holes. |
| First shell of a solid | Its exterior; the others are voids. |
| Solid `topology.relationships` | Kept unchanged (for example the link to a containing parcel). |
| Solid `properties.name` | Kept. |
| Point `properties.ptQualityMeasure` | Read as metres at 95 % confidence; used for each solid's or face's `ptQualityMeasure`. |

### Orientation

A face's loop of points is traversed as the face is used *in a solid*: the ring's orientation in its face, the face's orientation in its shell, and the shell's orientation in its solid all apply.
The shell's orientation matters for voids.
As `topo-solid`'s description specifies, a void shell is stored with its faces pointing out of the region it encloses and is referenced from the solid with `-`, so that in the solid its faces point into the void, away from the material.
This is the point raised in [#3](https://github.com/ogcincubator/topo-functions/issues/3): `topo_validator`'s loader does not read that orientation.

A face shared by two solids appears once in each, in that solid's orientation, so its two uses face opposite ways.

### Physical orientation

A face's outward side is physical: it is given by the right-hand rule in east, north, up, whatever the order of the `place` CRS's axes.
So a ring's order of points does not change when the same solid is given in a CRS with the other axis order, such as MGA (east-north) and NZTM (north-east); only each point's coordinates are swapped.
Applied to a north-east-up CRS's coordinates as stored, the right-hand rule gives the opposite side, because that system is left-handed; the writers therefore need the axis order stated (`place_axes`).

JSON-FG 1.0.0 states its orientation requirements for a right-handed Cartesian system: every face normal points away from the solid's material, outward on an exterior shell and into the void on a void shell.
That is the same reading, so JSON-FG coordinates are written in the CRS's axis order with the rings in the source's order, unchanged.

### Orientation check

Both writers check, in east, north, up, that each exterior shell's faces enclose a positive volume and each void's a negative one, after the ring, face and shell orientations are composed, so every normal points away from the material (`topo_encodings.orientation`).
A shell that fails is refused (`SHELL_INSIDE_OUT`), as is a shell enclosing no volume (`SHELL_WITHOUT_VOLUME`).
The check catches data written for the other reading of orientation, a wrongly stated `place_axes` (every shell then fails), and defects such as a void reversed twice.

`check_orientation=False` (library) or `--no-orientation-check` (command line) skips the check, to encode a source whose defect should be shown rather than refused; the defect is then carried into the encoding as it is.
The Building Blocks transforms always check.
Nothing is ever repaired.

## JSON-FG 1.0.0

`to_jsonfg(doc, *, crs, place_axes, features="per-solid", name=None, derived_from=None, check_orientation=True)`; OGC 21-045r1, conformance classes core and polyhedra.

| Topo Feature | JSON-FG |
| :- | :- |
| The document | A `FeatureCollection` with `conformsTo` (core and polyhedra), `coordRefSys` (the `crs` given by the caller), `geometryDimension: 3`, `name` and, when given, `derivedFrom`. No `featureType` is written, which would bring in the types-schemas class. |
| A solid (`features="per-solid"`) | A `Feature` with the solid's `id`, its `Polyhedron` in `place`, and `geometry: null` (JSON-FG allows only GeoJSON geometry types in `geometry`). |
| All solids (`features="multi"`) | One `Feature` with the document's `id` and a `MultiPolyhedron` in `place`. |
| A shell | One element of the `Polyhedron`'s coordinates, the exterior first. |
| A face | One polygon of its shell, its rings closed (the first position repeated at the end), in the face's orientation in the solid: its normal points away from the material. |
| A point | Its `place` coordinates, unchanged, in the CRS's axis order. |
| Solid `name` | `properties.name`. |
| Points' `ptQualityMeasure` | `properties.ptQualityMeasure`: the largest of the solid's points, or `null` if any point has none. |
| Shells | `properties.shells`: `[{"id", "role" ("exterior" or "void"), "faces" (count)}]`. |
| Solid `relationships` | `properties.relationships`, unchanged, when the solid has any. |

With `features="multi"`, the per-solid properties are listed in `properties.solids`, each with the solid's `id`.

## Per-face GeoJSON

`to_geojson_faces(doc, *, place_axes, transformer=None, heights=None, decimals=(9, 4), name=None, derived_from=None, check_orientation=True)`; RFC 7946.

The output is a view of the faces, not a solid, and does not claim to be one.
It is a separate encoding from `topo2geojson`'s, whose outputs are unchanged.

| Topo Feature | GeoJSON |
| :- | :- |
| The document | A `FeatureCollection` with `name` and, when given, `derivedFrom`. |
| A face, as used in a solid | A `Feature` with the face's `id` and a `Polygon` in longitude, latitude and ellipsoidal height. A face used by two solids gives two features with the same `id`. |
| The solid, shell and shell's role | `properties.solid`, `properties.shell`, `properties.shellRole` (`"exterior"` or `"void"`). |
| The face's orientation in the solid | `properties.orientationInSolid` (`"+"` or `"-"`). |
| Points' `ptQualityMeasure` | `properties.ptQualityMeasure`: the largest of the face's points, or `null`. |
| The face's outward direction | `properties.outwardNormal`: the outward unit normal (Newell's method), computed from `place` and given as east, north, up, rounded to 9 decimals. |
| Ring winding | RFC 7946 section 3.1.6: exterior counter-clockwise and holes clockwise in longitude and latitude. `properties.reversedForRfc7946` says whether the face's rings were reversed for it. |

### Winding

A face whose outward normal points down is reversed, all its rings together, so the relation between its exterior and its holes stays as the source has it.
A vertical face (the normal's vertical component at most 1e-6, a tilt of about 0.00006°) is left as it is: its winding in longitude and latitude is undefined.
Both decisions come from the normal in `place`, not from the rings' area in longitude and latitude, because for a wall that area comes only from rounding the coordinates.
This assumes that `place` is in a conformal projection, so that a face facing up is counter-clockwise in longitude and latitude.

## Coordinates, CRS and heights

- **One coordinate source per document.**
  JSON-FG is written from `place`; every point must have `place`, so CRSs are never mixed.
  Points that no solid uses may be 2D.
- **The CRS is stated by the caller.**
  A Topo Feature document does not give its `place` CRS in a form JSON-FG can use, so `crs` (a URI, or a list of URIs for a compound CRS such as MGA zone 50 with AHD) is required and written as `coordRefSys`.
- **Axis order.**
  CRS axes are in their EPSG order: east-north for some projections, north-east for others.
  JSON-FG coordinates are written unchanged, in that order.
  Both writers need the order stated (`place_axes`, one of the eight orders such as `east-north-up` or `north-east-up`) for the [orientation check](#orientation-check) and the per-face normals: orientation is [physical](#physical-orientation), and a north-east-up system is left-handed.
- **Ellipsoidal heights for GeoJSON.**
  RFC 7946 positions carry height above the ellipsoid.
  The per-face writer takes positions from a `transformer` the caller supplies (called with each point's `place` in the CRS's axis order, returning longitude, latitude and ellipsoidal height), or from `geometry` when the caller states that its heights are ellipsoidal (`heights="ellipsoidal-geometry"`).
  It never assumes heights are ellipsoidal, and it does not download geoid grids.
- **Rounding.**
  JSON-FG coordinates are not rounded.
  GeoJSON longitude and latitude are rounded to 9 decimals (about 0.1 mm) and heights to 4, by default.

## What is lost

- Edges and rings as features, and the ids of faces and rings in JSON-FG (a `Polyhedron` has no ids below the feature; the shells' ids are kept in `properties.shells`).
- Point ids and point properties, except `ptQualityMeasure`, which is summarised per solid or face.
- Solid properties other than `name` (for example a stored `volume`).
- Features other than solids and the points, edges, rings, faces and shells they use, including parcels' own 2D boundaries; a solid's link to its parcel is kept in `relationships`.
- The document's other members.
- In GeoJSON, the shell structure as geometry: faces are separate features, grouped only by their properties.

## What is refused

Every problem found is reported together, as an `EncodingError` listing a stable code, a message and the feature id for each.

| Code | Meaning |
| :- | :- |
| `NOT_A_DOCUMENT` | The input is not a JSON object. |
| `BAD_COLLECTION` | A topology key is not a list of FeatureCollections. |
| `MISSING_ID`, `DUPLICATE_ID` | A feature has no id, or an id is used twice. |
| `BAD_POINT` | A point's `place` or `geometry` is present but not a 2D or 3D `Point`. |
| `BAD_REFERENCES`, `BAD_ORIENTATION` | A directed reference has no `ref`, or an orientation other than `+` or `-`. |
| `MISSING_REFERENCE` | A reference does not resolve in the collection it should (for example a solid referencing a face). |
| `EMPTY_TOPOLOGY` | A solid with no shells, a shell with no faces, or a face with no rings. |
| `EDGE_NOT_STRAIGHT` | A ring uses an edge that is not `[start, end]` (curved or multi-point edges). |
| `RING_TOO_SHORT`, `RING_NOT_CONNECTED`, `RING_NOT_CLOSED` | A ring with fewer than three edges, or whose edges do not form one closed loop. |
| `POINT_WITHOUT_PLACE`, `POINT_WITHOUT_GEOMETRY` | A point lacks the coordinate source the writer needs. |
| `POINT_NOT_3D` | A point a solid uses is 2D. |
| `NO_SOLIDS` | The document has no solids. |
| `NO_HEIGHT_SOURCE`, `TWO_HEIGHT_SOURCES` | Per-face GeoJSON was asked for with neither, or both, of a transformer and a stated height source. |
| `TRANSFORM_FAILED` | The caller's transformer raised, or did not return three finite numbers, for a point. |
| `DEGENERATE_FACE` | A face has no area, so it has no normal. |
| `SHELL_INSIDE_OUT` | In east, north, up, an exterior shell's normals point into the solid, or a void's into the material. |
| `SHELL_WITHOUT_VOLUME` | A shell encloses no volume. |

A missing or empty `crs`, an unknown `features`, `heights` or `place_axes` value are wrong arguments and raise `ValueError`.

Not supported, and so refused or not produced:

- JSON-FG prisms (`Prism`, `MultiPrism`): Topo Feature has no extrusion concept, so the writers produce polyhedra only.
- Curved edges (`EDGE_NOT_STRAIGHT`); `topo2geojson` renders arcs and splines for its own outputs.

## Transforms and command line

The OGC Building Blocks transforms follow the same plugin convention as `topo2geojson`, in module `topo_encodings.transform`:

| Transform | Output | `metadata` |
| :- | :- | :- |
| `topo2jsonfg` | JSON-FG | `crs` and `place_axes` (both required), `features`, `name` |
| `topo2geojson-faces` | GeoJSON | `place_axes` and `heights: ellipsoidal-geometry` (both required), `name` |

Both declare their output as `application/geo+json`, as OGC 21-045r1's profile examples do: a JSON-FG document is also a GeoJSON document, and its `conformsTo` says it is JSON-FG.
`application/vnd.ogc.fg+json` (OGC testbeds) and `application/fg+json` (a possible registered form) are not used while neither is registered.

A register has no geoid grid, so `topo2geojson-faces` in a register needs `geometry` heights that are ellipsoidal.

A register installs only this distribution:

```yaml
plugins:
  transforms:
    - pip: git+https://github.com/ogcincubator/topo-functions.git#subdirectory=topo-encodings
      modules:
        - topo_encodings.transform
```

```yaml
transforms:
  - id: Solids-as-JSON-FG
    description: JSON-FG polyhedra for the solids
    type: topo2jsonfg
    metadata:
      crs:
        - http://www.opengis.net/def/crs/EPSG/0/7850
        - http://www.opengis.net/def/crs/EPSG/0/5711
      place_axes: east-north-up
```

The command-line tools write to standard output or `-o`, and exit with 1 (and the problems on standard error) when a document cannot be encoded:

```bash
topo2jsonfg solid.json --crs http://www.opengis.net/def/crs/EPSG/0/7850 \
    --crs http://www.opengis.net/def/crs/EPSG/0/5711 --place-axes east-north-up \
    -o solid.jsonfg.json
topo2geojson-faces solid.json --place-axes east-north-up \
    --heights ellipsoidal-geometry -o solid-faces.geojson
```

## Open questions

- **Orientation in Topo Feature.**
  `topo-solid`'s description does not yet say that orientation is physical; this library reads it so, as JSON-FG does.
  `topo_validator` computes a solid's signed volume from `place` as stored, so it would find a correct solid in a north-east-up CRS inside out.
