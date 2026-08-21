# WA 3D CSDM Topology Rules

The following is a summary of Section 4 Topology Rules extracted from NGSC Delivery 1, normalised into common topology rule names with short descriptions.
The summary forms the designing basis for a set of proof-of-concept tests implemented in `validator.py`, `test_validator.py`, and `conftest.py`.

## Terminology

In [ISO 19107:2019 Geographic Information - Spatial Schema](https://www.iso.org/standard/66175.html), a **Curve** is the general term for a one-dimensional primitive that supports various interpolation methods for defining the path that a curve follows from its start point to its end point.
Interpolation methods include linear, circular, clothoid, or parametric. 
For the 3D CSDM, with respect to Solids, a **Curve** is a boundary of a **Surface** of a **Solid**.

A **LineString** (or MultiLineString) is a specific type of curve that uses linear interpolation between its start and end points.

A **Ring** is a single closed oriented set of curves that define the boundary of a surface.

A **Surface** is a two- or three-dimensional bounded primitive, that is, a bounded area defined by at least one outer ring, and optionally a set of inner rings for holes. 
They can be planar or non-planar (curved). For most implementations, surfaces are planar (linear).

A **Face** is a topological collection of directed edges whose geometric realisation is a surface.

A **Shell** is a collection of oriented surfaces that form a closed 2-manifold. 
They do not have holes, they are watertight, and the surfaces are oriented outwards for Solids.
A 2-manifold is a surface where every point looks locally flat. 
For example, if you zoom-in anywhere on the earth, it appears flat. 
There are no pinch points, nor edges.
A sphere is a 2-manifold. 
A figure 8 is not.
A watertight Shell is closed so that it forms a well-defined interior that does not connect to the exterior.

A **Solid** is a bounded volume in 3D described by an exterior Shell and zero or more interior shells (for cavities). 
Shells must be watertight, with the surfaces oriented outwards. Intersection of shells is limited to edges and vertices.
There must be no volume overlap.
This ensures valid topology for volume computation.

**2D data.** All rules below assume 3D coordinates. A dataset whose points are all valid 2D `[x, y]` pairs (no z) is not run through these rules at all — `validate_topology()` records a single `NO_3D_TOPOLOGY` warning and skips the conformance classes, rather than failing structurally. Dedicated 2D/2.5D topology rules (row 32 below) are a possible future extension, not yet implemented.

## 3D CSDM Topology Rules: Test-Oriented Summary

Rules marked **✅ TR-##** have a corresponding validator function and unit tests.  Rules marked **— Not implemented** are in scope for future work.

| #  | Rule Category                | Common Rule Name                 | Short Description                                                                                                                                                                                       | Status                                                |
|----|------------------------------|----------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------|
| 1  | **Point Rules**              | Unique cadastral points          | No duplicate points within tolerance                                                                                                                                                                    | ✅ TR-01                                               |
| 2  |                              | Point fabric consistency         | Every vertex used by a curve must reference a cadastral mark that exists in the point set for a 3D CSDM data set.                                                                                       | ✅ TR-11                                               |
| 3  | **Curve Rules**              | Simple curve                     | Curves must not self-intersect except at endpoints                                                                                                                                                      | ✅ TR-02                                               |
| 4  |                              | Minimum curve length             | Curves must exceed minimum length tolerance                                                                                                                                                             | ✅ TR-12                                               |
| 5  |                              | No duplicate curves              | Identical curves cannot exist in same dataset                                                                                                                                                           | ✅ TR-13                                               |
| 6  |                              | Curve must not repeat in Ring    | Within a single surface ring, the same curve must not be referenced more than once.                                                                                                                     | ✅ TR-22                                               |
| 7  |                              | Curve intersection at nodes only | Curves may only meet at shared cadastral points                                                                                                                                                         | ✅ TR-14                                               |
| 8  |                              | No dangling curves               | Curves must form boundaries or be explicitly flagged. Curves defined in `observedVectors` and `vectorObservations` are exempt.                                                                          | ✅ TR-03                                               |
| 9  |                              | Curve must bound surface         | Curves must belong to at least one surface. Curves defined in `observedVectors` and `vectorObservations` are exempt.                                                                                    | ✅ TR-03                                               |
| 10 | **Surface Rules**            | Closed surface rings             | Surface boundaries must be closed                                                                                                                                                                       | ✅ TR-04                                               |
| 11 |                              | No surface self-intersection     | Surface rings must not intersect each other                                                                                                                                                             | ✅ TR-15                                               |
| 12 |                              | Connected interior               | Surface interior must be continuous                                                                                                                                                                     | ✅ TR-23                                               |
| 13 |                              | No duplicate surfaces            | Identical surfaces not allowed                                                                                                                                                                          | ✅ TR-16                                               |
| 14 |                              | Surface-curve consistency        | Surface edges must reference known curves                                                                                                                                                               | ✅ TR-17                                               |
| 15 |                              | Shared edges consistency         | Adjacent surfaces must use opposite edge orientations                                                                                                                                                   | ✅ TR-05                                               |
| 16 |                              | Surface form constraint          | Surfaces must meet model-specific form rules (optional)                                                                                                                                                 | ? Not implemented                                     |
| 17 | **Shell / Face Rules**       | Surfaces form shells             | Surfaces assemble into closed shells                                                                                                                                                                    | ✅ TR-06                                               |
| 18 |                              | No shell gaps or overlaps        | Shell surfaces must meet perfectly                                                                                                                                                                      | ✅ TR-06                                               |
| 19 |                              | No dangling faces                | Every face must participate in at least one shell                                                                                                                                                       | ✅ TR-18                                               |
| 20 |                              | Two faces per edge               | Each shell edge shared by exactly two faces                                                                                                                                                             | ✅ TR-06                                               |
| 21 | **Solid Rules**              | Closed solid                     | Solid must be bounded by closed shell(s)                                                                                                                                                                | ✅ TR-06                                               |
| 22 |                              | Solid non self-intersection      | Solids must not intersect themselves                                                                                                                                                                    | ✅ TR-24                                               |
| 23 |                              | Positive volume                  | Solid must have non-zero volume                                                                                                                                                                         | ✅ TR-07                                               |
| 24 |                              | Minimum thickness                | Avoid sliver solids (thin AABB in any axis)                                                                                                                                                             | ✅ TR-19                                               |
| 25 |                              | Shell orientation                | Outer shell outward, inner shells inward                                                                                                                                                                | ✅ TR-25                                               |
| 26 | **Solid Relationship Rules** | Shared boundary consistency      | Adjacent solids share a common face                                                                                                                                                                     | ✅ TR-10                                               |
| 27 |                              | Face adjacency limit             | Face adjacent to at most one neighbour solid                                                                                                                                                            | ✅ TR-10                                               |
| 28 |                              | No overlapping solids            | Solids shall not overlap                                                                                                                                                                                | ✅ TR-08                                               |
| 29 | **Containment Rules**        | Parent-child containment         | Child parcel bbox must lie within parent parcel bbox - Solid Primary Parcels only                                                                                                                       | ✅ TR-09: partially implemented, need to add type test |
| 30 |                              | Secondary Parcel containment     | Secondary Parcel must lie within their burdened parcel(s)                                                                                                                                               | ✅ TR-20                                               |
| 31 |                              | Thematic host relationship       | Thematic solids must reference a valid host parcel                                                                                                                                                      | ✅ TR-21                                               |
| 32 | **2D / 2.5D Parcels**        | Primary Parcel Coverage          | Primary parcels of the same type must not overlap in 2D / 2.5D space.</br>Where they are intended to form a continuous parcel fabric, they must also be contiguous, with no unintended gaps or slivers. | ? Partially implemented                               |

---

## Implemented Rules — Detail

The following twenty-five rules are fully implemented in `validator.py` and tested in `test_validator.py`.

### Point Rules

#### TR-01 — UniquePoints
**Function:** `validate_unique_points(data, tol=1e-6)`
**Error code:** `DUPLICATE_POINT_PROXIMITY`
No two cadastral points may lie within `tol` metres of each other.  
The tolerance guards against duplicate survey marks that would corrupt the geometry fabric.

#### TR-11 — PointFabricConsistency
**Function:** `validate_point_fabric_consistency(data)`
**Error code:** `UNKNOWN_POINT_REFERENCE`
Every vertex id referenced in a curve must exist in the `points` collection.  
A broken link between the curve layer and the point layer indicates an incomplete or corrupt topology dataset.

---

### Curve Rules

#### TR-02 — CurveNoSelfIntersection
**Function:** `validate_curve_no_self_intersection(data)`
**Error code:** `CURVE_SELF_INTERSECTION`
Curves must not cross themselves except at their endpoints.  
Only curves with four or more vertices (three or more segments) can self-intersect.  
Non-adjacent segment pairs are tested using the 3D coplanarity and parametric intersection algorithm (`_segments_intersect_3d`): segments that are skew (at different elevations) are correctly identified as non-intersecting.

#### TR-12 — MinimumCurveLength
**Function:** `validate_minimum_curve_length(data, min_length=1e-3)`
**Error code:** `CURVE_BELOW_MINIMUM_LENGTH`
The total arc length of each curve must exceed `min_length` metres.  
Curves shorter than the snapping tolerance produce degenerate topology and may indicate coincident survey marks.

#### TR-13 — NoDuplicateCurves
**Function:** `validate_no_duplicate_curves(data)`
**Error code:** `DUPLICATE_CURVE`
No two curves may connect the same sequence of vertices.  
Two curves are considered duplicates when their vertex lists are identical or are exact reverses of each other (same geometry, opposite traversal direction).

#### TR-14 — CurveIntersectionAtNodesOnly
**Function:** `validate_curve_intersection_at_nodes_only(data)`
**Error code:** `CURVE_INTERSECTION_NOT_AT_NODE`
Two distinct curves may only meet at shared cadastral point nodes.  
A crossing between the interiors of two curve segments (not at a shared vertex endpoint) is flagged.  
The 3D coplanarity test ensures that curves on separate building levels whose XY projections overlap are not falsely reported — only genuinely coplanar, crossing segments are flagged.

#### TR-03 — NoDanglingCurves
**Function:** `validate_no_dangling_curves(data)`
**Error code:** `DANGLING_CURVE`
Every curve used as a boundary edge in a parcel or solid topology must be referenced by at least one surface ring.
A boundary curve not used by any surface ring is topologically orphaned and cannot contribute to a valid solid boundary.
This test does not apply to curves included only as survey, abuttal, observation, or other supporting geometry.

#### TR-22 — CurveNoRepeatInRing
**Function:** `validate_no_repeated_curves_in_rings(data)`
**Error code:** `CURVE_REPEATED_IN_RING`
Within a single surface ring, a curve must not be referenced more than once.  
A curve appearing twice (or more) in the same ring means the boundary visits the same segment more than once, creating a degenerate self-touching boundary regardless of the orientations used.  
This rule is distinct from `TR-04` (which checks that consecutive members connect end-to-start but does not enforce uniqueness) and `TR-05` (which checks orientation consistency between different surfaces).

---

### Surface Rules

#### TR-04 — SurfaceClosedRing
**Function:** `validate_surface_closed_rings(data)`
**Error code:** `SURFACE_RING_NOT_CLOSED`
Every ring in a surface must form a closed chain.
The end-point of each ring member must equal the start-point of the next, and the ring must close back to its first start-point.

#### TR-23 — ConnectedInterior
**Function:** `validate_surface_connected_interior(data)`
**Error code:** `SURFACE_RING_REPEATED_VERTEX`
Within each surface ring, no vertex may appear as the directed start-point of more than one member.  
A vertex that is the start-point of two or more members means the ring visits that vertex twice, forming a pinch point where the interior splits into two or more disconnected pieces.  
This rule is distinct from `TR-04` (which checks consecutive connectivity but not vertex uniqueness), `TR-15` (which detects segment crossings in interiors but not shared-vertex touches), and `TR-22` (which detects a repeated curve; different curves can share a vertex, which this rule catches instead).

#### TR-24 — SolidNonSelfIntersection
**Function:** `validate_no_solid_self_intersection(data)`
**Error code:** `SOLID_SELF_INTERSECTION`
No two faces of the same solid may have curve segments that properly cross each other in 3D space.
For each solid, every consecutive vertex pair (segment) from every face is collected with its face index.  
Any pair of segments from *different* faces is tested with `_segments_intersect_3d`.  
Collinear shared-edge segments between adjacent faces are naturally skipped by the parallel guard (zero cross-product).  
At most one error per solid is reported.
This rule is distinct from `TR-08` (which detects AABB overlap between two *different* solids) and `TR-15` (which detects a single surface ring crossing itself within one face).

#### TR-15 — NoSurfaceSelfIntersection
**Function:** `validate_no_surface_self_intersection(data)`
**Error code:** `SURFACE_SELF_INTERSECTION`
The edges within a surface ring must not cross each other.  
Non-adjacent segments of the same ring are tested using the 3D coplanarity and parametric intersection algorithm.  
A self-intersecting ring defines an invalid (bowtie) polygon.  
Segments that are skew (non-coplanar) are correctly treated as non-intersecting.

#### TR-16 — NoDuplicateSurfaces
**Function:** `validate_no_duplicate_surfaces(data)`
**Error code:** `DUPLICATE_SURFACE`
No two surfaces may reference the same set of curves.
Two surfaces are considered duplicates when the frozenset of all curve ids referenced in their rings is identical, regardless of ring order, member order, or the orientation (`"+"` / `"-"`) of each member.
Reversed winding and/or reversed per-edge orientation do not make two faces distinct.

#### TR-17 — SurfaceCurveConsistency
**Function:** `validate_surface_curve_consistency(data)`
**Error code:** `UNKNOWN_CURVE_REFERENCE`
Every curve id referenced in a surface ring must exist in the `curves` collection.  
A broken reference between the surface layer and the curve layer indicates an incomplete or corrupt topology dataset.

#### TR-05 — SharedSurfaceEdges
**Function:** `validate_shared_surface_edges(data)`
**Error code:** `SHARED_EDGE_SAME_ORIENTATION`
When the same curve is used by two adjacent surfaces, it must be referenced with opposite direction/orientation in each surface.
Here, `+` and `−` refer to the directed use of the shared curve, not to the shape of the surface.
The test does not require the surface to be square; it only requires consistent orientation across the shared boundary.

---

### Shell / Face Rules

#### TR-18 — NoDanglingFaces
**Function:** `validate_no_dangling_faces(data)`
**Error code:** `DANGLING_FACE`
Every surface used as a boundary face of a solid must be referenced by at least one solid shell.
A boundary face that no solid shell owns is topologically orphaned and cannot contribute to a valid closed shell.
This test does not apply to terrain or other supporting surface geometry not intended to participate in solid topology.

#### TR-06 — ClosedSolid
**Function:** `validate_closed_solid(data)`
**Error code:** `OPEN_SOLID_SHELL`
The shell of a solid must be a closed 2-manifold.  
In a closed shell every curve is used by the solid's faces exactly twice (once in each direction).  
A count other than 2 means the shell has a gap or a hole.

---

### Solid Rules

#### TR-07 — PositiveVolume
**Function:** `validate_positive_volume(data, tol=1e-9)`
**Error code:** `ZERO_OR_NEGATIVE_VOLUME`
Every solid must declare a strictly positive volume.  
A zero or negative declared volume indicates a degenerate or inverted solid.

#### TR-19 — MinimumSolidThickness
**Function:** `validate_minimum_solid_thickness(data, min_thickness=1e-3)`
**Error code:** `SOLID_BELOW_MINIMUM_THICKNESS`
The bounding box of each solid must have a minimum extent of `min_thickness` metres in every axis direction.
Solids that are effectively flat in one or more directions (slivers) are numerically unstable.

#### TR-25 — ShellOrientation
**Function:** `validate_shell_orientation(data, tol=1e-9)`
**Error code:** `SHELL_ORIENTATION_REVERSED`
The outer shell of every solid must have outward-facing face normals (right-hand winding relative to the exterior).
The signed volume of the solid is computed via the divergence theorem — `V = (1/6) · Σ v0 · (v1 × v2)` over all fan-triangulated face polygons.  
A positive result means outward normals; a negative result means the winding is reversed.
This rule is distinct from `TR-07` (which checks the *declared* volume field) and `TR-19` (which checks AABB dimensions).
Note: the current data model does not distinguish inner shells (voids) from the outer shell within a single solid record; this rule treats all faces as belonging to one outer shell.
This test applies to geometries intended to represent a closed solid shell.
In the 3D CSDM, boundary faces are orientable, and orientation carries topological meaning because it distinguishes the inward/outward role of the face relative to connected solids.
The signed-volume check is an implementation method for testing whether the shell orientation is consistent; it is not itself the source of the topological concept.

---

### Solid Relationship Rules

#### TR-10 — SharedSolidFace
**Function:** `validate_shared_solid_face(data)`
**Error code:** `FACE_ADJACENCY_LIMIT_EXCEEDED`
Each face used as a boundary face between solids may be shared by at most two solids.
A face claimed by three or more solids exceeds the permitted solid adjacency.
This test applies only to fully topological solid boundaries and does not apply to terrain surfaces, 2.5D parcel surfaces, or transitional nominal boundaries that are not yet represented as shared solid faces.

#### TR-08 — NoSolidOverlap
**Function:** `validate_no_solid_overlap(data)`
**Error code:** `SOLID_OVERLAP`
Primary cadastral parcels of the same type must not overlap.
Secondary parcels may overlap primary parcels and other secondary parcels.
Validate overlap only between BREP solid parcel features contained in a FeatureCollection where featureType is PrimaryParcel.  
A SecondaryParcel feature collection is outside the scope of TR-08.
Three categories of pairs are exempt:

1. **Parent–child pairs** — containment is expected and verified by TR-09.
2. **Disjoint-level pairs** — solids that declare non-overlapping `levels` sets occupy separate storeys; an AABB overlap is a cross-level artefact.
3. **Topologically adjacent pairs** — solids that share a boundary face are properly connected neighbours whose AABBs naturally touch at the shared face.

---

### Containment Rules

#### TR-09 — ParentContainment

**Function:** `validate_parent_containment(data)`

**Error codes:** `CHILD_NOT_CONTAINED_IN_PARENT`, `UNKNOWN_PARENT_REFERENCE`

A child parcel or parcel component must be contained within the bounding box of its relevant parent spatial unit.
For ordinary parcel decomposition, the parent is the enclosing parcel.
Validate containment where solid parcel features contained in a **parcel** FeatureCollection where featureType is PrimaryParcel.  
A SecondaryParcel feature collection is outside the scope of this test.
For shared boundary components or subdivision-wide components, the parent may be a parcel aggregate or other higher-level parent extent.
Components intended to straddle a parcel boundary, such as a party wall, should be split at the boundary or modelled as a shared boundary feature, rather than treated as a child of only one parent.

#### TR-20 — EasementContainment

**Function:** `validate_easement_containment(data)`

**Error codes:** `EASEMENT_MISSING_BURDENED`, `UNKNOWN_BURDENED_REFERENCE`, `EASEMENT_NOT_CONTAINED_IN_SERVIENT`

Every Secondary parcel, or Secondary parcel part, must be fully contained within the Primary parcel or Primary parcel aggregate identified as burdened by the associated interest.
Where one interest burdens multiple parcels, the preferred representation is to split the interest at parcel boundaries and validate each part against its corresponding burdened parcel.
`benefitted` references describe the parcel or parcels that receive the benefit of the interest, but they do not drive the containment test.

#### TR-21 — ThematicHostRelationship

**Function:** `validate_thematic_host_relationship(data)`

**Error codes:** `THEMATIC_SOLID_MISSING_HOST`, `UNKNOWN_HOST_REFERENCE`

Every thematic solid (`parcel_type == "thematic"`) must reference a valid host parcel solid via the `host_id` field.  
The `host_id` must resolve to a known solid in the same dataset.

---

## Data Model

The validator operates on a plain Python dict with four top-level lists:

```
{
  "points":   [{"id": str, "coordinates": [x, y, z]}, ...],
  "curves":   [{"id": str, "vertices": [pt_id, ...]}, ...],
  "surfaces": [{"id": str,
                "rings": [{"type": "outer"|"inner",
                           "members": [{"ref": curve_id,
                                        "orientation": "+"|"-"}, ...]
                          }, ...]
               }, ...],
  "solids":   [{"id": str,
                "faces": [surface_id, ...],
                "volume": float,
                "theme": str,
                "parcel_type": "primary"|"child"|"easement"|"thematic",
                "parent_id": str | null,
                "servient_id": str | null,
                "host_id": str | null,
                "levels": [str, ...]
               }, ...]
}
```

**Curve orientation convention:**
- `"+"` — traverse `vertices[0]` → `vertices[-1]`
- `"-"` — traverse `vertices[-1]` → `vertices[0]`

Topology JSON fixtures in the CSDM geometry schema (using `edges`/`faces`/`solids` with GeoJSON Feature wrappers) are converted to this format via `from_csdm_json()` in `validator.py`.

---

## Test Infrastructure

### `validator.py`
Core validation module.  Each TR-xx rule is a standalone function that accepts the topology dict and returns a list of issue dicts:
```python
{"code": str, "severity": "error"|"warning", "message": str,
 "object_id": str|None, "path": str|None, "extra": dict}
```
The top-level entry point `validate_topology(data, tol={})` runs all twenty-five rules and returns the combined issue list.
Optional tolerance overrides: `"point"` (TR-01), `"volume"` (TR-07), `"length"` (TR-12), `"thickness"` (TR-19).

**Key geometry helper — `_segments_intersect_3d`**

Rules `TR-02`, `TR-14`, `TR-15`, and `TR-24` all rely on a shared 3D segment intersection test.

The algorithm:

1. **Parallel guard** — computes `n = d1 × d2` (cross product of the two segment directions).  
If `|n|² < tol²` the segments are parallel and cannot properly cross.
2. **Coplanarity guard** — the perpendicular distance between the two infinite lines is `|r · n| / |n|` where `r = p3 − p1`.  
If this exceeds `tol` the segments are *skew* (non-coplanar) and cannot intersect.  
This is the critical step for 3D geometry: curves on separate building levels that only appear to cross in the XY projection are correctly identified as skew and ignored.
3. **Parametric solve** — for coplanar segments, computes the intersection parameters `t` and `s` along each segment.  
A proper interior crossing requires `0 < t < 1` and `0 < s < 1` (endpoints excluded).

A 2D implementation (`_segments_intersect_2d`) projects all geometry onto the XY plane and could generate false positives for legitimate 3D topology such as boundaries on different levels.

### `conftest.py`
Pytest fixtures and topology builders.

- **`cube_data(prefix, x0,y0,z0, x1,y1,z1, **kwargs)`** — factory that constructs a topologically valid axis-aligned cube with mathematically verified outward-normal ring orientations.  Accepts `theme`, `parcel_type`, `parent_id`, `servient_id`, `host_id`, `levels`, and `volume`.
- **`merge_datasets(*datasets)`** — combines multiple topology dicts into one, running a three-stage deduplication pipeline (points → curves → surfaces) that models the CSDM requirement that shared boundary elements are the same cadastral objects.
- **Pytest fixtures:** `unit_cube`, `two_adjacent_cubes`, `nested_cubes`
- **`--fixture <filename>`** CLI option — selects a JSON geometry fixture file for the `TestFixture` class (default: `tetrahedron.json`).

### `test_validator.py`
111 unit tests across 27 classes.  Each implemented rule has at least one valid (happy-path) test and one invalid (violation-injection) test.

| Class                                  | Rule           | Tests |
|----------------------------------------|----------------|-------|
| `TestTR01UniquePoints`                 | TR-01          | 4     |
| `TestTR02CurveNoSelfIntersection`      | TR-02          | 3     |
| `TestTR03NoDanglingCurves`             | TR-03          | 2     |
| `TestTR04SurfaceClosedRing`            | TR-04          | 3     |
| `TestTR05SharedSurfaceEdges`           | TR-05          | 2     |
| `TestTR06ClosedSolid`                  | TR-06          | 3     |
| `TestTR07PositiveVolume`               | TR-07          | 4     |
| `TestTR08NoSolidOverlap`               | TR-08          | 4     |
| `TestTR09ParentContainment`            | TR-09          | 4     |
| `TestTR10SharedSolidFace`              | TR-10          | 3     |
| `TestTR11PointFabricConsistency`       | TR-11          | 2     |
| `TestTR12MinimumCurveLength`           | TR-12          | 3     |
| `TestTR13NoDuplicateCurves`            | TR-13          | 3     |
| `TestTR14CurveIntersectionAtNodesOnly` | TR-14          | 3     |
| `TestTR15NoSurfaceSelfIntersection`    | TR-15          | 2     |
| `TestTR16NoDuplicateSurfaces`          | TR-16          | 5     |
| `TestTR17SurfaceCurveConsistency`      | TR-17          | 2     |
| `TestTR18NoDanglingFaces`              | TR-18          | 2     |
| `TestTR19MinimumSolidThickness`        | TR-19          | 3     |
| `TestTR20EasementContainment`          | TR-20          | 4     |
| `TestTR21ThematicHostRelationship`     | TR-21          | 4     |
| `TestTR22CurveOrientation`             | TR-22          | 4     |
| `TestTR23ConnectedInterior`            | TR-23          | 4     |
| `TestTR24SolidNonSelfIntersection`     | TR-24          | 4     |
| `TestTR25ShellOrientation`             | TR-25          | 4     |
| `TestIntegration`                      | All            | 4     |
| `TestFixture`                          | All (via JSON) | 26    |

`TestFixture` covers all 25 rules individually (TR-01 through TR-25) plus one combined `test_fixture_passes_all_tr_rules` test.
`TestTR14` includes a dedicated skew-segment test (`test_skew_curves_at_different_elevations_pass`) that confirms the 3D intersection upgrade does not generate false positives for curves on separate building levels.

---

## Rules Not Yet Implemented

The following rules from the full NGSC Delivery 1 specification are identified but not yet implemented in this POC:

| Rule                             | Description                                                                                                                   | Notes                                          |
|----------------------------------|-------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------|
| Surface form constraint          | Surfaces must meet model-specific form rules                                                                                  | Dataset-specific; out of scope for generic POC |
| No duplicate shells              | Shells must not be duplicated                                                                                                 | Implementation pending                         |
| No gaps / no overlaps in Parcels | Primary parcels of the same type must not overlap in 2D / 2.5D space.                                                         | Implementation pending                         |  
| Parcels must be Contiguous       | Where they are intended to form a continuous parcel fabric, they must also be contiguous, with no unintended gaps or slivers. | Implementation pending                         |