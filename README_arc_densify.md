# Arc Densification Module

## Overview

The `arc_densify.py` module converts circular arcs defined by parametric geometry (start point, end point, centre, and direction) into a sequence of straight-line chord vertices suitable for visualisation and geometric processing. 
The module ensures that the maximum perpendicular distance (sagitta) between any chord and the true circular arc does not exceed a specified tolerance.

## Purpose

This module is designed for processing survey data and cadastral plans where circular arcs are commonly used to represent curved boundaries. 
It provides:

- **Precise arc-to-polyline conversion** with controlled deviation tolerance
- **Survey data validation** including radius consistency checking
- **Robust input validation** for production deployment
- **Geometric statistics** for quality assurance and reporting

## Key Features

### Core Functionality

- **`densify_arc()`:** Main function that converts arc parameters to a list of coordinate points
- **`chord_statistics()`:** Returns detailed geometric statistics about the arc densification
- **Configurable precision:** Control the maximum chord-to-arc offset (sagitta)
- **Direction support:** Handles both clockwise (`"cw"`) and counter-clockwise (`"ccw"`) arcs
- **Topology preservation:** Uses exact start/end coordinates to ensure adjoining geometry shares endpoints

### Input Validation

The module includes pre-validation (fail-early):

- Coordinate length validation (ensures x, y pairs)
- Finite value checking (rejects NaN and infinity)
- Radius consistency validation with configurable tolerance
- Direction parameter validation
- Chord count overflow protection (prevents memory exhaustion)
- Max. offset and tolerance parameter validation

### Mathematical Foundation

The densification algorithm uses the sagitta formula to determine the number of chords required:
```
s = R × (1 - cos(θ/2))
```

Where:
- `s` = maximum chord-to-arc offset (sagitta)
- `R` = arc radius
- `θ` = central angle subtended by one chord

## Usage

### Basic Example
```python
python from arc_densify import densify_arc

# Define a circular arc
start = (63613.434, 351126.454) 
end = (63588.775, 351133.133) 
centre = (63628.464, 351230.815) 
max_offset = 0.02 # 20mm tolerance for coordinates in metres 
direction = "cw" # clockwise arc

# Convert arc to point sequence
points = densify_arc(
    start=start,
    end=end,
    centre=centre,
    max_offset=max_offset,
    direction=direction
)

# Result: list of (x, y) coordinate tuples
# [(63613.434, 351126.454), ..., (63588.775, 351133.133)]
```
### With Survey Radius Validation

When working with adjusted survey data, you can provide an expected radius for validation:
```python 
points = densify_arc(
    start=start,
    end=end,
    centre=centre,
    max_offset=0.02,
    direction="cw",
    radius=105.437,          # Expected radius from survey
    radius_tolerance=0.005   # 5mm tolerance
)
``` 

### Getting Arc Statistics

```python 
from arc_densify import chord_statistics

stats = chord_statistics(
    start=start,
    end=end,
    centre=centre,
    max_offset=0.02,
    direction="cw"
)  

# Returns ArcStatistics dictionary with:
# - radius: computed arc radius
# - sweep_radians: arc sweep angle
# - sweep_degrees: arc sweep angle in degrees
# - arc_length: length along the arc
# - number_of_chords: number of straight-line segments
# - chord_angle_degrees: angle subtended by each chord
# - chord_length: length of each chord
# - maximum_actual_offset: actual maximum sagitta
``` 

## Parameters

### Required Parameters

| Parameter    | Type                   | Description                                                         |
|--------------|------------------------|---------------------------------------------------------------------|
| `start`      | `Sequence[float]`      | Arc start coordinate (x, y)                                         |
| `end`        | `Sequence[float]`      | Arc end coordinate (x, y)                                           |
| `centre`     | `Sequence[float]`      | Circle centre coordinate (x, y)                                     |
| `max_offset` | `float`                | Maximum permitted chord-to-arc offset (sagitta) in coordinate units |
| `direction`  | `Literal["cw", "ccw"]` | Arc direction: clockwise or counter-clockwise                       |

### Optional Parameters

| Parameter          | Type            | Default | Description                                        |
|--------------------|-----------------|---------|----------------------------------------------------|
| `radius`           | `float \| None` | `None`  | Expected radius from survey data for validation    |
| `radius_tolerance` | `float`         | `0.005` | Allowed difference in computed vs. expected radius |

## Coordinate Systems

The module assumes coordinates are in a **projected coordinate system** where:
- X and Y units are consistent (e.g. metres)
- `max_offset` uses the same units as coordinates
- Angles are measured counter-clockwise from the positive X-axis

**Example:** For coordinates in metres with MGA2020 (GDA2020):
- `max_offset=0.02` means 20 mm maximum deviation
- `radius_tolerance=0.005` means 5 mm radius tolerance

## Implementation Example

The companion module **`arc_to_geojson_writer.py`** provides a complete implementation example that demonstrates how to:

- Use `densify_arc()` to convert arcs to coordinate sequences
- Wrap the results in GeoJSON LineString features
- Write output to files
- Handle command-line arguments for batch processing
- Build feature properties from arc parameters

Run the example:
```bash 
python src/arc_comps/arc_to_geojson_writer.py
``` 

This generates `data/output/arc_example.geojson` with a densified arc as a GeoJSON LineString feature.

## Topology Integration (topo-arc building block)

The densification functions are wired into the `topo2geojson` transform so that
[topo-arc](../topo-feature/_sources/features/topo-arc) topology — which
describes curves by *reference* to point features rather than by storing
vertices — can be rendered as true curved GeoJSON.

Two small bridge modules connect the topology descriptions to the interpolators:

- **`arc_geometry.py`** — converts `Arc`, `ArcWithCenter`, `ArcByChord` and
  `CircleByCenter` topology into densified geometry via `densify_arc()`. It
  supplies the geometry each topology type leaves implicit: the circumcircle
  centre for a 3-point `Arc`, the centre implied by a chord + radius for
  `ArcByChord`, and a full sweep for `CircleByCenter` (emitted as a closed
  GeoJSON Polygon).
- **`spline_geometry.py`** — interpolates `CubicSpline` topology using the
  [`splines`](https://pypi.org/project/splines/) package. A natural cubic
  spline is fitted through the control points; when the topology supplies
  `startTangentVector` / `endTangentVector`, the spline is *clamped* so its
  start and end directions match those vectors. The continuous curve is sampled
  with adaptive subdivision so the chord-to-curve offset stays within the same
  `max_offset` tolerance used for arcs. Both 2-D and 3-D control points are
  supported: when any referenced point carries a Z value the spline is
  interpolated in 3-D (Z carried through the output vertices), otherwise the
  output stays 2-D.

  Spline fitting is **unconditional**: whenever a `CubicSpline` topology is
  encountered it is rendered as the fitted curve, in both the Points-n-Edges
  and Densified-Curves transforms (and regardless of the `--densify` flag) — a
  spline's defining shape *is* the fitted curve, not a chord through its
  control points. The `Arc`/`Circle` types, by contrast, are only rendered as
  true curves when densification is enabled.

  In `points` mode a spline's original control points are also emitted as
  `Point` features, and any start/end tangent vectors are drawn as separate
  dashed, distinctly-coloured `LineString` segments (simplestyle-spec `stroke`
  / `stroke-dasharray` properties). See the *Arc, circle and spline topology*
  section of [`README.md`](README.md) for details.

### Running the densified transform

```bash
# Densify arc/circle/spline topology into true curved geometry
python topo2geojson.py \
  -i ../topo-feature/_sources/features/topo-arc/examples/arc_by_center.json \
  -t ../topo-feature/_sources/examples/referenced-objects.ttl \
  -m edges,faces --densify --max-offset 0.02 -p
```

The building block's `transforms.yaml` exposes this as the **Densified-Curves**
transform (`densify: true`, `max_offset: 0.02`), alongside the **Points-n-Edges**
transform which renders the raw point references as a straight-chord
approximation.

`--max-offset` (metadata key `max_offset`) controls the maximum chord-to-curve
offset for both arcs and splines; smaller values produce more vertices.

## Error Handling

The module raises `ValueError` for invalid inputs:

- Coordinates with fewer than 2 elements
- NaN or infinite coordinate values
- Zero or negative `max_offset`
- Negative `radius_tolerance`
- Invalid direction (not `"cw"` or `"ccw"`)
- Start/end points are not on the same circle (within tolerance)
- Start/end point coinciding with the centre
- Chord count exceeding safety limit (default: 100,000)

## Performance Considerations

- **Memory usage** is proportional to the number of chords generated
- For large radii and small `max_offset`, chord counts can be very high
- The default chord count limit (100,000) prevents memory exhaustion
- Typical survey arcs require 5 to 50 chords with 5 to 20 mm tolerance

## Testing

Run the built-in demonstration:
```bash 
python src/arc_comps/arc_densify.py
``` 

This displays arc statistics and coordinate output for a sample cadastral arc.

## References

- Sagitta (chord-to-arc offset): geometric measure of arc deviation
- Survey-accurate coordinate handling for cadastral boundary representation
- GeoJSON LineString specification for arc representation

## Dependencies

`arc_densify.py` itself depends only on the Python standard library. The wider
topo-arc integration adds:

| Package | Purpose | Licence |
|---------|---------|---------|
| [`splines`](https://pypi.org/project/splines/) | Cubic-spline interpolation for `CubicSpline` topology (`spline_geometry.py`) | MIT |
| [`numpy`](https://pypi.org/project/numpy/) | Numerical arrays (required by `splines`) | BSD-3-Clause |
| [`pyproj`](https://pypi.org/project/pyproj/) | CRS reprojection in `topo2geojson.py` | MIT |
| [`rdflib`](https://pypi.org/project/rdflib/) | Parsing referenced topology from RDF Turtle | BSD-3-Clause |

Install everything via the project's packaging metadata:

```bash
pip install -e .          # runtime dependencies
pip install -e ".[test]"  # plus pytest for the test suite
```

## Licensing

This project is MIT licensed. Its third-party dependencies are all distributed
under permissive licences (MIT / BSD-3-Clause) that are compatible with MIT —
see [`LICENCE.md`](LICENCE.md) for the project licence text and the full list
of dependency licences and attributions.

