# WA 3D CSDM Topology Rules

The following is a summary of Section 4 Topology Rules extracted from NGSC Delivery 1, normalised into common topology rule names with short descriptions.
The summary forms the basis for the rule implementations in `topo_validator/validator.py` and `topo_validator/conformance/`, exercised by the tests under `tests/topo_validator/` in this repository.

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

**2D data.** Most rules below assume 3D coordinates, but a dataset may legitimately mix 2D content (e.g. a cadastral parcel outline) with 3D topology, or be entirely 2D — neither is a structural failure. Structural validation checks each point against its own minimum of 2 coordinate values, not one dataset-wide length, so a 2D point is never rejected merely because the rest of the dataset is 3D (see `validator._validate_points_structure`). `topo_validator.dimensionality.partition_topology()` then splits the topology into a 3D-only view and a 2D-only view: a point, curve, or surface is 2D-tainted when every point it touches is 2D (a curve touching *both* a 2D and a 3D point is a genuine defect, reported as `MIXED_DIMENSION_CURVE`, not silently routed either way). The 3D-only view goes through the conformance classes below exactly as documented. The 2D-only view is validated by the same rules under the same codes wherever the underlying check is purely referential or degrades correctly for consistently-2D input — TR-01, TR-02, TR-03, TR-04, TR-05, TR-11, TR-12, TR-13, TR-14, TR-15, TR-16, TR-17, TR-22, TR-23 (`validator._run_2d_applicable_rules`) — with each resulting issue tagged `extra.dimensionality: "2d"` and rendered in its own report section, separate from the main pass/fail table. Shell/solid/volume-specific rules (TR-06 onward) have no 2D analogue; a solid excluded from 3D validation purely because it owns 2D-tainted geometry produces a summarized `EXCLUDED_FROM_3D_VALIDATION` notice instead of being silently dropped. A dataset whose points are *all* 2D gets this same treatment uniformly (plus a single `NO_3D_TOPOLOGY` warning) rather than a separate code path. Dedicated 2D/2.5D *parcel-fabric* coverage rules (row 36 below — primary-parcel overlap and contiguity, the 2D analogue of TR-08) remain a future extension, not yet implemented.

A CSDM `parcels` collection (`topology.type: "Polygon"`) is parsed into a real `Surface`: its ring's ordered curve ids are chained into a closed ring by matching endpoints against either end of the chain built so far (`loader._chain_ring_curve_orientations`), the same way `topo2geojson._chain_edges` resolves the identical reference shape into geometry. This is what lets a parcel's own boundary be checked (TR-04 closed ring, TR-16 no duplicates, etc.) via the 2D-applicable rules above, rather than only ever appearing as an opaque collection of orphan points and curves.

The chase above only guarantees a *closed* ring, not a canonical winding — which of a ring's two possible rotational directions it lands on is an accident of the order its curves happen to be listed in `references`, not a geometric fact (the same ring re-listed starting from a different curve can chase out in the opposite direction). That matters for TR-05: comparing `+`/`-` for the same curve across two *different* parcel surfaces is only meaningful once every ring's winding is pinned to a common, geometry-derived convention. So when real point coordinates are available, `loader._canonicalize_ring_winding` reverses a ring (and every member's orientation) as needed so it resolves to counter-clockwise for an outer ring, clockwise for a hole (the standard exterior/hole convention) — independent of how its curves were listed. Two ordinarily-adjacent cadastral parcels sharing an edge then correctly resolve to opposite effective orientations on it, as TR-05 expects; two parcels genuinely overlapping on the same side of a shared edge still resolve to the *same* orientation and are still flagged. A ring whose points aren't locally resolvable keeps its raw chased orientation (best-effort, same as before).

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
| 21 |                              | Shell closure                    | Every shell must be a closed, consistently oriented (watertight) surface                                                                                                                                | ✅ TR-27                                               |
| 22 | **Solid Rules**              | Closed solid                     | Solid must be bounded by closed shell(s)                                                                                                                                                                | ✅ TR-06                                               |
| 23 |                              | Solid non self-intersection      | Solids must not intersect themselves                                                                                                                                                                    | ✅ TR-24                                               |
| 24 |                              | Positive volume                  | Solid must have non-zero volume                                                                                                                                                                         | ✅ TR-07                                               |
| 25 |                              | Minimum thickness                | Avoid sliver solids (thin AABB in any axis)                                                                                                                                                             | ✅ TR-19                                               |
| 26 |                              | Shell orientation                | Outer shell outward, inner shells inward                                                                                                                                                                | ✅ TR-25                                               |
| 27 |                              | Declared volume consistency      | A solid's declared volume must match the volume its own topology encloses                                                                                                                              | ✅ TR-26                                               |
| 28 | **Solid Relationship Rules** | Shared boundary consistency      | Adjacent solids share a common face                                                                                                                                                                     | ✅ TR-10                                               |
| 29 |                              | Face adjacency limit             | Face adjacent to at most one neighbour solid                                                                                                                                                            | ✅ TR-10                                               |
| 30 |                              | No overlapping solids            | Solids shall not overlap                                                                                                                                                                                | ✅ TR-08                                               |
| 31 | **Containment Rules**        | Parent-child containment         | Child parcel bbox must lie within parent parcel bbox - Solid Primary Parcels only                                                                                                                       | ✅ TR-09: partially implemented, need to add type test |
| 32 |                              | Secondary Parcel containment     | Secondary Parcel must lie within their burdened parcel(s)                                                                                                                                               | ✅ TR-20                                               |
| 33 |                              | Thematic host relationship       | Thematic solids must reference a valid host parcel                                                                                                                                                      | ✅ TR-21                                               |
| 34 |                              | Declared parcel containment      | A cadastral parcel/spatial-unit solid must declare exactly one `containingPrimaryParcel` relationship to the Primary Parcel that contains it, cross-checked geometrically                                | ✅ TR-28                                               |
| 35 |                              | Declared easement burden         | A secondary/easement solid must declare exactly one `burdenedBySecondaryParcel` relationship to the Primary Parcel it burdens, cross-checked geometrically                                                | ✅ TR-29                                               |
| 36 | **2D / 2.5D Parcels**        | Primary Parcel Coverage          | Primary parcels of the same type must not overlap in 2D / 2.5D space.</br>Where they are intended to form a continuous parcel fabric, they must also be contiguous, with no unintended gaps or slivers. | ? Partially implemented                               |

---

## Implemented Rules — Detail

The following twenty-nine rules are fully implemented (TR-28/TR-29 in `conformance/cc07_containment.py`, invoked directly by `validator.validate_topology` rather than via a conformance class's own `validate()` — see the 2D data note above and their own entries below for why) and tested under `tests/topo_validator/` in this repository.

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

#### TR-27 — ShellClosure
**Function:** `validate_shell_closure(data)`
**Error code:** `SHELL_NOT_CLOSED`
Every shell must be a closed, consistently oriented surface.
Applies the divergence theorem to a constant field: the oriented area vectors of a closed, consistently wound surface sum to zero, so a non-zero residual is either the area of a hole or the area of a region double-covered by a reversed face.
The residual is compared against a tolerance relative to the shell's own total surface area (`1e-6`), with an absolute floor (`1e-9`) so a tiny shell isn't held to a bound below double precision.
This rule closes a gap left by the other shell/solid rules: `TR-06` counts curve references without regard to direction, so two faces walking a shared edge the *same* way still satisfy it; `TR-25` tests only the *sign* of the volume integral and `TR-26` only its *magnitude* — both presume closure, so an open shell reaches them as a strange number rather than as a closure error. A solid with two coplanar faces of opposing normals can pass all of TR-06/TR-25/TR-26 while enclosing nothing well-defined; TR-27 is what catches that case (e.g. a reversed face surviving a modelling tool's own "solid" check, which validates edge counts rather than consistent face orientation).
Runs before the CC-05 volume rules, since neither TR-25's sign nor TR-26's magnitude means anything over a surface with a hole in it.

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

#### TR-26 — DeclaredVolumeConsistency
**Function:** `validate_declared_volume_matches_topology(data)`
**Error code:** `DECLARED_VOLUME_MISMATCH`
A solid's declared volume must match the volume its own topology encloses.
The declared value is typically derived by the producer from published face areas and face normals via the divergence theorem's z-integral; this rule independently recomputes the enclosed volume from the points and rings alone (summing each shell's *signed* volume before taking the absolute value, so a void's negative contribution correctly subtracts from the outer shell's), so an error in any of the underlying geometry inputs shows up as a mismatch rather than cancelling out.
Solids whose topology encloses no volume are skipped — their geometry is degenerate/unusable and other rules in this class (`TR-07`, `TR-24`, `TR-25`) report that instead.
The tolerance is relative (`0.2%` of the enclosed volume) for solids large enough that coordinate rounding is negligible, with an absolute floor (`0.02` m³) for small solids — rounding of published coordinates perturbs the integral by an amount set by the solid's own dimensions, not by its volume, so a purely relative bound would be unreasonably tight on a small solid.
This rule is distinct from `TR-07` (which only checks that the declared volume is positive) and `TR-25` (which checks only the *sign*, not the magnitude, of the volume integral).

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
Primary Parcel-equivalent solids of the same theme must not have overlapping interior volume.
Secondary/easement solids may legitimately overlap Primary Parcels and other Secondary/easement solids (rights, restrictions, volumetric interests occupying space already represented by another cadastral parcel), and are therefore out of scope for *this* rule — they remain fully subject to every other applicable topology rule (e.g. TR-20 easement containment, TR-21 thematic host, TR-28/TR-29 declared relationships).
Scoping is per-solid, via `Solid.parcel_type` (`cc06_relationships._is_primary_parcel_solid`, reusing `cc07_containment.SECONDARY_PARCEL_TYPES`/`THEMATIC_PARCEL_TYPE`), not via a FeatureCollection's `featureType` — that 2D-only association (a `parcels` collection's own `featureType: "PrimaryParcel"`) is a separate mechanism used by TR-28/TR-29. A solid with no declared `parcel_type` at all defaults to Primary-equivalent scope (the same conservative default `loader._build_solids` already applies), so an orphan/malformed solid is never silently exempted. `SolidAggregate` membership is *not* an overlap exemption in its own right: two solids intended as members of one aggregate must still not have overlapping interior volume unless one of the categories below already applies to them.
Four categories of pairs are exempt:

0. **Non-Primary pairs** — either solid has `parcel_type` in `{"secondary", "easement", "thematic"}`.
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

#### TR-28 — DeclaredParcelContainment

**Function:** `conformance.cc07_containment.validate_declared_parcel_containment(topology_3d, topology_2d)`

**Error codes:** `MISSING_PARCEL_CONTAINMENT_RELATIONSHIP` (warning), `MULTIPLE_PARCEL_CONTAINMENT_RELATIONSHIPS`, `UNKNOWN_PARCEL_REFERENCE`, `PARCEL_TYPE_MISMATCH`, `SOLID_NOT_WITHIN_DECLARED_PARCEL`

Implements the "explicit parent parcel relationships" proposal: rather than relying only on geometric containment (which can become ambiguous after geometry edits, tolerance changes, or topology repairs), a solid declares which Primary Parcel contains it. A solid whose `parcel_type` is `"primary"` or `"secondary"` must declare exactly one `topology.relationships` entry with `rel: "topology"` and `role: "containingPrimaryParcel"`:

```json
{
  "topology": {
    "relationships": [
      {
        "href": "uuid:...parcel-id...",
        "rel": "topology",
        "role": "containingPrimaryParcel",
        "targetFeatureType": "PrimaryParcel"
      }
    ]
  }
}
```

The declaration is one-way (the solid declares it; the Primary Parcel does not declare a reciprocal relationship back) — the reverse index is derivable by querying which solids declare `containingPrimaryParcel` toward a given parcel, so there is only one place the fact can drift out of sync. `targetFeatureType` must match the referenced feature's actual `featureType` exactly (the plain string, e.g. `"PrimaryParcel"`, not a prefixed qname) — a mismatch is reported rather than resolved leniently, since a mismatch usually means the data itself disagrees with what it claims to reference.

Once the declaration resolves, the solid's footprint (every distinct vertex bounding it, projected to (x, y)) must lie within the declared parcel's own ring, treated as an unlimited vertical prism (`topo_validator.parcel_geometry`).

This rule is **conditionally scoped**, unlike every other rule in this document: it only activates when the dataset actually declares at least one `PrimaryParcel` surface (i.e. it has real `parcels` content — see the 2D data note above), and only for solids whose `parcel_type` marks them as cadastral parcel/spatial-unit geometry. A plain geometry fixture with no parcel content at all produces no findings from this rule, rather than every solid being flagged for a relationship it was never meant to declare. A missing declaration is a warning, not an error — there is no separate "strict" mode; a consumer wanting to treat it as a hard failure can do so from the issue list itself (via its severity), the same way any other warning-vs-error consumer decision is made elsewhere in this package.

#### TR-29 — DeclaredEasementBurden

**Function:** `conformance.cc07_containment.validate_declared_easement_burden(topology_3d, topology_2d)`

**Error codes:** `MISSING_EASEMENT_BURDEN_RELATIONSHIP` (warning), `MULTIPLE_EASEMENT_BURDEN_RELATIONSHIPS`, `UNKNOWN_BURDENED_PARCEL_REFERENCE`, `BURDENED_PARCEL_TYPE_MISMATCH`, `SOLID_NOT_WITHIN_BURDENED_PARCEL`

The same mechanism as TR-28, for the easement/secondary side of a relationship: a solid whose `parcel_type` is `"easement"` or `"secondary"` (the same set TR-20 uses) must declare exactly one `topology.relationships` entry with `role: "burdenedBySecondaryParcel"` pointing at the Primary Parcel it burdens, cross-checked geometrically the same way. This is fully additive to TR-20's existing `burdened_id`/`servient_id` (solid-to-solid) check — a solid can be validated by both, and a `parcel_type == "secondary"` solid is legitimately in scope for *both* TR-28 and TR-29 at once (it sits within some Primary Parcel physically and separately burdens one via an easement), which is why TR-29 has its own distinct `UNKNOWN_BURDENED_PARCEL_REFERENCE`/`BURDENED_PARCEL_TYPE_MISMATCH` codes rather than sharing TR-28's — a shared code would make both rules' rows in a report show FAIL whenever only one of them actually found something.

TR-21 (thematic/host) has no declared-relationship equivalent yet — deferred.

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

This section describes only files and modules that exist in this repository. It intentionally does not carry a hand-maintained per-rule test-count table — that has drifted from reality before (twice) and the fix is to read it from the suite itself, not to re-derive a third static number: run `pytest tests/topo_validator/` for the current count, or `pytest tests/topo_validator/ --collect-only` for the current list of test files and functions.

### `topo_validator/validator.py` and `topo_validator/conformance/`
`validator.py` runs structural pre-checks, then dispatches to each conformance class's own `validate()` — one module per class, `topo_validator/conformance/cc01_points.py` through `cc07_containment.py` — plus TR-28/TR-29 (called directly; see the note under "Implemented Rules — Detail" above for why). Each TR-xx rule is a standalone function that accepts the topology dict and returns a list of issue dicts:
```python
{"code": str, "severity": "error"|"warning", "message": str,
 "object_id": str|None, "path": str|None, "extra": dict}
```
The top-level entry point `validate_topology(data, tol={})` (in `validator.py`) runs all twenty-nine rules and returns the combined issue list.
Optional tolerance overrides: `"point"` (TR-01), `"volume"` (TR-07), `"length"` (TR-12), `"thickness"` (TR-19).

**Key geometry helper — `segments_intersect_3d`** (`topo_validator/geometry.py`)

Rules `TR-02`, `TR-14`, `TR-15`, and `TR-24` all rely on this shared 3D segment intersection test.

The algorithm:

1. **Parallel guard** — computes `n = d1 × d2` (cross product of the two segment directions).  
If `|n|² < tol²` the segments are parallel and cannot properly cross.
2. **Coplanarity guard** — the perpendicular distance between the two infinite lines is `|r · n| / |n|` where `r = p3 − p1`.  
If this exceeds `tol` the segments are *skew* (non-coplanar) and cannot intersect.  
This is the critical step for 3D geometry: curves on separate building levels that only appear to cross in the XY projection are correctly identified as skew and ignored.
3. **Parametric solve** — for coplanar segments, computes the intersection parameters `t` and `s` along each segment.  
A proper interior crossing requires `0 < t < 1` and `0 < s < 1` (endpoints excluded).

### `tests/topo_validator/conftest.py`
Pytest fixtures and topology builders used by this repository's own test suite.

- **`cube_data(prefix, x0,y0,z0, x1,y1,z1, **kwargs)`** — factory that constructs a topologically valid axis-aligned cube with mathematically verified outward-normal ring orientations.  Accepts `theme`, `parcel_type`, `parent_id`, `servient_id`, `host_id`, `levels`, and `volume`.
- **`merge_datasets(*datasets)`** — combines multiple topology dicts into one, running a three-stage deduplication pipeline (points → curves → surfaces) that models the CSDM requirement that shared boundary elements are the same cadastral objects.
- **Pytest fixtures:** `unit_cube`, `two_adjacent_cubes`, `nested_cubes`, `hollow_cube`.
- **`fixture_data`/`--fixture <filename>`** — loads a JSON geometry fixture from `tests/topo_validator/fixtures/` (default: `tetrahedron.json`) via `topo_validator.loader.from_csdm_json`. Defined for fixture-driven tests but not currently consumed by any test class in this suite — a JSON fixture is instead loaded directly where needed (e.g. `tests/topo_validator/test_shared_surface_edges.py`).

### `tests/topo_validator/`
This repository's test suite: `test_loader.py`, `test_plugin.py`, `test_rdf_loader.py`, `test_shared_surface_edges.py` (TR-05), `test_solid_overlap_parcel_types.py` (TR-08 parcel-type scoping), `test_transform_report_output.py`, `test_two_dimensional.py`, and `test_in_memory_samples.py` (runs the in-memory cube fixtures above through `validate_topology` end-to-end), plus the `tr0*-*-fail.json` regression fixtures under `fixtures/` for TR-01/02/03/04/11/12/13/14/22. Not every implemented rule yet has a dedicated regression test in this repository — treat this list as where a rule's tests would live, not a coverage guarantee; `pytest tests/topo_validator/` is authoritative for what currently exists and passes.

---

## Rules Not Yet Implemented

The following rules from the full NGSC Delivery 1 specification are identified but not yet implemented in this POC. Point/curve/surface-level 2D consistency for parcel content (duplicate points, dangling curves, closed rings, duplicate surfaces, etc.) *is* covered — see the "2D data" note near the top of this document — and a solid's declared relationship to its containing/burdened Primary Parcel *is* covered (TR-28/TR-29); what remains below is specifically the 2D/2.5D *coverage* concept (whether a set of parcels tile a fabric correctly), which has no rule-reuse shortcut and needs real 2D polygon-overlap geometry:

| Rule                             | Description                                                                                                                   | Notes                                          |
|----------------------------------|-------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------|
| Surface form constraint          | Surfaces must meet model-specific form rules                                                                                  | Dataset-specific; out of scope for generic POC |
| No duplicate shells              | Shells must not be duplicated                                                                                                 | Implementation pending                         |
| No gaps / no overlaps in Parcels | Primary parcels of the same type must not overlap in 2D / 2.5D space.                                                         | The 2D analogue of TR-08; needs real 2D polygon-overlap geometry, not achievable via the padding/reuse tricks the other 2D-applicable rules use |
| Parcels must be Contiguous       | Where they are intended to form a continuous parcel fabric, they must also be contiguous, with no unintended gaps or slivers. | Implementation pending                         |