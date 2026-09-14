#!/usr/bin/env python3

"""Load topology JSON and adapt Topo Feature / 3D CSDM JSON to validator data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from .model import (
    Curve,
    ObservationCurve,
    Orientation,
    Point,
    Relationship,
    Ring,
    RingMember,
    Shell,
    ShellType,
    Solid,
    Surface,
    SurfaceShellFaceReference,
    TopologyData,
)

_ORIENTATION_FLIP: dict[Orientation, Orientation] = {"+": "-", "-": "+"}


def load_json(path: str | Path) -> dict[str, Any]:
    """Load a JSON object from the disk.

    Args:
        path: Path to the JSON file.

    Returns:
        Parsed JSON object.

    Raises:
        ValueError: If the JSON root is not an object.
    """
    with Path(path).open("r", encoding="utf-8") as fh:
        value = json.load(fh)

    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object at {path!s}")

    return value


CSDM_COLLECTION_KEYS = ("points", "edges", "rings", "faces", "shells", "solids")


def _iter_features(
    data: dict[str, Any],
    collection_name: str,
    excluded_feature_types: set[str] | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield dict features from GeoJSON FeatureCollections under *collection_name*.

    When *excluded_feature_types* is provided, entire FeatureCollections, whose
    ``featureType`` is in the set, are skipped.
    """
    for collection in data.get(collection_name, []):
        if not isinstance(collection, dict):
            continue

        if excluded_feature_types is not None:
            feature_type = collection.get("featureType")
            if isinstance(feature_type, str) and feature_type in excluded_feature_types:
                continue

        features = collection.get("features", [])
        if not isinstance(features, list):
            continue

        for feature in features:
            if isinstance(feature, dict):
                yield feature


def count_features(data: dict[str, Any]) -> dict[str, int]:
    """Count Feature objects per Topo Feature / 3D CSDM collection key.

    Args:
        data: Parsed Topo Feature / 3D CSDM JSON object.

    Returns:
        Mapping of each of `CSDM_COLLECTION_KEYS` to the number of features
        found under it (0 for keys the document doesn't use).
    """
    return {key: sum(1 for _ in _iter_features(data, key)) for key in CSDM_COLLECTION_KEYS}


def _topology_list(feature: dict[str, Any], key: str) -> list[Any]:
    """Return a topology list from a feature, or an empty list if absent/invalid."""
    topology = feature.get("topology", {})
    if not isinstance(topology, dict):
        return []

    value = topology.get(key, [])
    return value if isinstance(value, list) else []


def _build_points(data: dict[str, Any]) -> list[Point]:
    """Build internal point records from CSDM point FeatureCollections.

    Prefers projected "place.coordinates" values when present, falling back to
    "geometry.coordinates". Features without a string id or list coordinates
    are skipped.

    Args:
        data: Parsed Topo Feature / 3D CSDM JSON object.

    Returns:
        Point records with "id" and "coordinates" fields.
    """
    points: list[Point] = []

    for feature in _iter_features(data, "points"):
        point_id = feature.get("id")
        geometry_source = feature.get("place") or feature.get("geometry", {})
        coordinates = (
            geometry_source.get("coordinates")
            if isinstance(geometry_source, dict)
            else None
        )

        if isinstance(point_id, str) and isinstance(coordinates, list):
            points.append(
                {
                    "id": point_id,
                    "coordinates": coordinates,
                }
            )

    return points


def _build_curves(data: dict[str, Any]) -> list[Curve]:
    """Build internal curve records from CSDM edge FeatureCollections.

    Features without a string id or list of references are skipped.

    Args:
        data: Parsed Topo Feature / 3D CSDM JSON object.

    Returns:
        Curve records with "id" and "vertices" fields.
    """
    curves: list[Curve] = []

    for feature in _iter_features(
        data, "edges", excluded_feature_types={"SubtendedAngle"}
    ):
        curve_id = feature.get("id")
        references = _topology_list(feature, "references")

        if not isinstance(curve_id, str):
            continue
        if not all(isinstance(ref, str) for ref in references):
            continue

        curves.append(
            {
                "id": curve_id,
                "vertices": references,
            }
        )

    return curves


def _build_ring_map(data: dict[str, Any]) -> dict[str, Ring]:
    """Build internal ring records from CSDM face FeatureCollections.

    Features without a string id or list of references are skipped.

    Args:
        data: Parsed Topo Feature / 3D CSDM JSON object.

    Returns:
        Ring records with "id", "type", and "members" fields.
    """
    ring_map: dict[str, Ring] = {}

    for feature in _iter_features(data, "rings"):
        ring_id = feature.get("id")
        if not isinstance(ring_id, str):
            continue

        ring_map[ring_id] = {
            "type": "outer",
            "members": _ring_members_from_raw(
                _topology_list(feature, "directed_references")
            ),
        }

    return ring_map


def _build_surfaces(data: dict[str, Any], ring_map: dict[str, Ring]) -> list[Surface]:
    """Build internal surface records from CSDM face FeatureCollections.

    Resolves each face topology reference through "ring_map" and skips invalid
    ring references or references to missing rings. Face features without a
    string "id" are skipped.

    Args:
        data: Parsed Topo Feature / 3D CSDM JSON object.
        ring_map: Ring records keyed by CSDM ring feature id.

    Returns:
        Surface records with "id" and resolved "rings" fields.
    """
    surfaces: list[Surface] = []

    for feature in _iter_features(data, "faces"):
        surface_id = feature.get("id")
        if not isinstance(surface_id, str):
            continue

        rings: list[Ring] = []
        for ring_ref in _topology_list(feature, "directed_references"):
            if not isinstance(ring_ref, dict):
                continue

            ring_id = ring_ref.get("ref")
            if not isinstance(ring_id, str):
                continue

            ring = ring_map.get(ring_id)
            if ring is not None:
                # Outer-vs-hole is inferred from the face's reference order:
                # the first resolved ring is the outer boundary, the rest are
                # holes.  CSDM carries no hole flag, and the STEP producer
                # guarantees this order (``_get_face_wires`` relies on OCC
                # listing the outer boundary wire first).  Mirrors the shell
                # ordering in ``_resolve_solid_shells``.
                #
                # Copy rather than mutate: ``ring_map`` holds ONE dict per ring
                # id and rings are deduplicated across faces, so a ring can be
                # the outer boundary of one face and a hole in another.
                # Writing a per-face conclusion into the shared object would
                # corrupt every other face that uses it.
                rings.append({
                    "type": "outer" if not rings else "inner",
                    "members": ring["members"],
                })

        surfaces.append(
            {
                "id": surface_id,
                "rings": rings,
            }
        )

    return surfaces


def _curve_endpoints(curve_id: str, curves: dict[str, Curve]) -> tuple[str, str] | None:
    """Return a curve's (start, end) vertex ids, or None if unresolvable."""
    curve = curves.get(curve_id)
    vertices = curve.get("vertices") if isinstance(curve, dict) else None

    if not isinstance(vertices, list) or len(vertices) < 2:
        return None

    return vertices[0], vertices[-1]


def _chain_ring_curve_orientations(
    curve_ids: list[str],
    curves: dict[str, Curve],
) -> list[RingMember]:
    """Infer each curve's directed orientation by chaining *curve_ids*.

    A `parcels` ring (unlike a `faces` ring) lists its boundary as plain
    curve ids with no explicit per-curve orientation, and -- unlike a
    `faces` ring -- not necessarily in walk order (a boundary's curves can be
    listed in property-definition order rather than traversal order). This
    matches each curve's endpoints against *either* end of the chain built so
    far, mirroring `topo2geojson._chain_edges`'s four-way matching strategy
    exactly, but at the id level: it accumulates directed `RingMember`
    entries and a parallel vertex-id chain instead of resolved coordinates.

    A curve that's missing, has fewer than two vertices, or connects to
    neither end of the chain built so far still gets an entry (appended,
    defaulting to "+") rather than being dropped -- this is a best-effort
    structural build; a resulting non-closed or inconsistent ring is
    `validator`'s TR-04 (SurfaceClosedRing) and related rules' job to
    diagnose, not this function's.

    Args:
        curve_ids: Curve ids forming one ring, as given by a `parcels`
            feature's `topology.references`.
        curves: Curve records already built from `data["edges"]`, keyed by id.

    Returns:
        Directed ring members, one per input curve id.
    """
    members: list[RingMember] = []
    vertex_chain: list[str] = []

    for curve_id in curve_ids:
        endpoints = _curve_endpoints(curve_id, curves)

        if endpoints is None:
            members.append({"ref": curve_id, "orientation": "+"})
            continue

        start_vertex, end_vertex = endpoints

        if not vertex_chain:
            members.append({"ref": curve_id, "orientation": "+"})
            vertex_chain = [start_vertex, end_vertex]
            continue

        if start_vertex == vertex_chain[-1]:
            members.append({"ref": curve_id, "orientation": "+"})
            vertex_chain.append(end_vertex)
        elif end_vertex == vertex_chain[-1]:
            members.append({"ref": curve_id, "orientation": "-"})
            vertex_chain.append(start_vertex)
        elif end_vertex == vertex_chain[0]:
            members.insert(0, {"ref": curve_id, "orientation": "+"})
            vertex_chain.insert(0, start_vertex)
        elif start_vertex == vertex_chain[0]:
            members.insert(0, {"ref": curve_id, "orientation": "-"})
            vertex_chain.insert(0, end_vertex)
        else:
            # Connects to neither end of the chain built so far.
            members.append({"ref": curve_id, "orientation": "+"})
            vertex_chain.append(end_vertex)

    return members


def _build_parcel_surfaces(
    data: dict[str, Any],
    curves: dict[str, Curve],
) -> list[Surface]:
    """Build internal surface records from CSDM parcel FeatureCollections.

    A `parcels` feature with `topology.type == "Polygon"` describes its
    boundary as one or more rings of ordered edge/curve ids -- a different
    shape from a `faces` feature's `RingMember`-with-explicit-orientation
    references -- so each ring's per-curve orientation is inferred by
    chaining consecutive curve endpoints (see
    `_chain_ring_curve_orientations`), mirroring how `topo2geojson` resolves
    the same reference shape into geometry, but at the id level to build a
    structural `Ring` instead.

    Scoped to `Polygon`; `AggregatePolygon` (combining several already-built
    parcel polygons into one MultiPolygon, per `topo2geojson`'s handling) is
    not supported here. Parcel features without a string "id", without a
    `Polygon`-typed topology, or without a usable references list are
    skipped.

    A built surface also carries `feature_type`, copied from its parcel
    FeatureCollection's own `featureType` (e.g. `"PrimaryParcel"`) rather
    than from the feature itself -- CSDM carries the type at the collection
    level, the same place `_iter_features`'s `excluded_feature_types` reads
    it from. This is a plain, un-prefixed string (e.g. `"PrimaryParcel"`,
    not a qname like `"surv:PrimaryParcel"`), matching how `featureType` is
    written everywhere else in a CSDM document; a declared relationship's
    `targetFeatureType` is expected to match this exactly (see
    `conformance.cc07_containment`).

    Args:
        data: Parsed Topo Feature / 3D CSDM JSON object.
        curves: Curve records already built from `data["edges"]`, keyed by
            id -- a parcel ring's curve ids resolve against these, since a
            parcel's edges are ordinary `edges` collection features.

    Returns:
        Surface records, one per `Polygon`-typed parcel feature with at
        least one usable ring.
    """
    surfaces: list[Surface] = []

    for collection in data.get("parcels", []):
        if not isinstance(collection, dict):
            continue

        feature_type = collection.get("featureType")
        features = collection.get("features", [])
        if not isinstance(features, list):
            continue

        for feature in features:
            if not isinstance(feature, dict):
                continue

            feature_id = feature.get("id")
            if not isinstance(feature_id, str):
                continue

            topology = feature.get("topology")
            if not isinstance(topology, dict) or topology.get("type") != "Polygon":
                continue

            raw_rings = topology.get("references")
            if not isinstance(raw_rings, list):
                continue

            rings: list[Ring] = []
            for curve_ids in raw_rings:
                if not isinstance(curve_ids, list) or not all(
                    isinstance(curve_id, str) for curve_id in curve_ids
                ):
                    continue

                rings.append(
                    {
                        "type": "outer" if not rings else "inner",
                        "members": _chain_ring_curve_orientations(curve_ids, curves),
                    }
                )

            if not rings:
                continue

            surface: Surface = {"id": feature_id, "rings": rings}
            if isinstance(feature_type, str):
                surface["feature_type"] = feature_type
            surfaces.append(surface)

    return surfaces


def _feature_ids(data: dict[str, Any], collection_name: str) -> set[str]:
    """Return the set of feature ids declared in a CSDM collection."""
    return {
        feature["id"]
        for feature in _iter_features(data, collection_name)
        if isinstance(feature.get("id"), str)
    }


def _compose_orientations(outer: Orientation, inner: Orientation) -> Orientation:
    """Compose a referencing shell's orientation with a nested member's."""
    return inner if outer == "+" else _ORIENTATION_FLIP[inner]


def _resolve_shell_members(
    shell_id: str,
    raw_shells: dict[str, list[Any]],
    face_ids: set[str],
    resolved: dict[str, tuple[list[str], dict[str, Orientation]]],
    visiting: frozenset[str],
) -> tuple[list[str], dict[str, Orientation]]:
    """Flatten one shell's directed references into face ids and orientations.

    A directed reference is treated as a face when its id belongs to a known
    face feature, or when it is not a known shell feature -- so unresolvable
    ids remain in "faces" for downstream missing-reference reporting. Any
    other reference is a nested shell, which is resolved recursively and whose
    face orientations are composed with the referencing orientation.

    Faces are deduplicated, first reference winning, because
    "Shell['face_orientations']" cannot represent one face at two orientations
    and because a double-counted face would break the closed-shell curve
    counting in TR-06.

    Args:
        shell_id: Shell feature id to resolve.
        raw_shells: Raw directed reference lists keyed by shell feature id.
        face_ids: Ids of every face feature in the dataset.
        resolved: Memo of already-resolved shells, held at "+" orientation.
        visiting: Shell ids on the current recursion path, used to break cycles.

    Returns:
        A tuple of ordered face ids and their orientations keyed by face id.
    """
    if shell_id in resolved:
        return resolved[shell_id]
    if shell_id in visiting:
        # Reference cycle: contribute nothing rather than recursing forever.
        # A shell first reached inside a broken cycle memoises the partial
        # result, so a cyclic (malformed) shell graph flattens to something
        # order-dependent but stable. Such a shell will not close, and the
        # closed-shell rule TR-06 reports it.
        return [], {}

    faces: list[str] = []
    face_orientations: dict[str, Orientation] = {}

    def add_face(face_id: str, face_orientation: Orientation) -> None:
        if face_id not in face_orientations:
            faces.append(face_id)
            face_orientations[face_id] = face_orientation

    for raw_ref in raw_shells.get(shell_id, []):
        if not isinstance(raw_ref, dict):
            continue

        ref = raw_ref.get("ref")
        if not isinstance(ref, str):
            continue

        raw_orientation = raw_ref.get("orientation", "+")
        orientation: Orientation = (
            raw_orientation if raw_orientation in {"+", "-"} else "+"
        )

        if ref in face_ids or ref not in raw_shells:
            add_face(ref, orientation)
            continue

        nested_faces, nested_orientations = _resolve_shell_members(
            ref,
            raw_shells,
            face_ids,
            resolved,
            visiting | {shell_id},
        )
        for nested_face_id in nested_faces:
            add_face(
                nested_face_id,
                _compose_orientations(
                    orientation,
                    nested_orientations[nested_face_id],
                ),
            )

    resolved[shell_id] = (faces, face_orientations)
    return faces, face_orientations


def _build_shell_map(data: dict[str, Any]) -> dict[str, Shell]:
    """Build internal shell records from CSDM shell FeatureCollections.

    A shell's directed references may point at faces, at other shells, or at a
    mixture of the two -- the latter arising when a solid is constructed by
    offset from a reference surface, so that its upper and lower boundaries are
    themselves shells of faces. Nested shell references are flattened
    recursively, so every returned shell exposes the flat face id and
    orientation collections that the validation rules consume. Shell features
    without a string "id" are skipped.

    Args:
        data: Parsed Topo Feature / 3D CSDM JSON object.

    Returns:
        Shell records keyed by CSDM "shell" feature id.
    """
    face_ids = _feature_ids(data, "faces")

    raw_shells: dict[str, list[Any]] = {}
    for feature in _iter_features(data, "shells"):
        shell_id = feature.get("id")
        if isinstance(shell_id, str):
            raw_shells[shell_id] = _topology_list(feature, "directed_references")

    resolved: dict[str, tuple[list[str], dict[str, Orientation]]] = {}
    shell_map: dict[str, Shell] = {}

    for shell_id in raw_shells:
        faces, face_orientations = _resolve_shell_members(
            shell_id,
            raw_shells,
            face_ids,
            resolved,
            frozenset(),
        )
        # Each shell id resolves to its own faces/orientations objects, and
        # "resolved" is discarded when this function returns, so these need no
        # defensive copy. Callers that hand shells on to solids copy already.
        shell_map[shell_id] = {
            "type": "outer",
            "faces": faces,
            "face_orientations": face_orientations,
        }

    return shell_map


def _resolve_solid_shells(
    raw_shell_refs: list[Any],
    shell_map: dict[str, Shell],
) -> tuple[list[Shell], list[str], dict[str, Orientation]]:
    """Resolve CSDM solid shell references into internal shell structures.

    Looks up each shell reference in "shell_map" and classifies the first
    resolved shell as "outer" and subsequent resolved shells as "inner".
    Invalid references and references to missing shells are skipped. Also
    builds the flattened face id and orientation collections used by legacy
    solid validation rules.

    Args:
        raw_shell_refs: Raw shell reference objects from a CSDM solid topology.
        shell_map: Shell records keyed by CSDM "shell" feature id.

    Returns:
        A tuple containing resolved shells, flattened face ids, and flattened
        face orientations keyed by face id.
    """
    shells: list[Shell] = []
    flattened_face_ids: list[str] = []
    flattened_face_orientations: dict[str, Orientation] = {}

    for shell_index, shell_ref in enumerate(raw_shell_refs):
        if not isinstance(shell_ref, dict):
            continue

        shell_id = shell_ref.get("ref")
        if not isinstance(shell_id, str):
            continue

        shell = shell_map.get(shell_id)
        if shell is None:
            continue

        shell_type: ShellType = "outer" if shell_index == 0 else "inner"
        resolved_shell: Shell = {
            "type": shell_type,
            "faces": list(shell["faces"]),
            "face_orientations": shell["face_orientations"].copy(),
        }

        shells.append(resolved_shell)
        flattened_face_ids.extend(resolved_shell["faces"])
        # PyCharm widens the Literal values of ``Orientation`` to ``str`` when
        # they cross a generic boundary, so it rejects this as dict[str, str]
        # into dict[str, Orientation].  Both sides are dict[str, Orientation];
        # ``.items()`` and ``|=`` mis-infer the same way, so the suppression is
        # on the clearest spelling rather than on a contorted one.
        # noinspection PyTypeChecker
        flattened_face_orientations.update(resolved_shell["face_orientations"])

    return shells, flattened_face_ids, flattened_face_orientations


def _build_relationships(feature: dict[str, Any]) -> list[Relationship]:
    """Build declared relationship records from a feature's `topology.relationships`.

    The `topo-feature` topology datatype schema already defines
    `relationships` on the `topology` object, constrained to `rel: "topology"`
    (`bblocks://ogc.geo.json-fg.link-role`) -- `_topology_list` reads it the
    same generic way it reads `references`/`directed_references`. Entries
    with any other `rel`, or missing a string `href`/`role`/
    `targetFeatureType`, are skipped.

    Args:
        feature: A raw CSDM feature (e.g. a `solids` collection entry).

    Returns:
        Declared relationship records.
    """
    relationships: list[Relationship] = []

    for raw in _topology_list(feature, "relationships"):
        if not isinstance(raw, dict) or raw.get("rel") != "topology":
            continue

        href = raw.get("href")
        role = raw.get("role")
        target_feature_type = raw.get("targetFeatureType")

        if not isinstance(href, str) or not isinstance(role, str):
            continue
        if not isinstance(target_feature_type, str):
            continue

        relationships.append(
            {
                "href": href,
                "rel": "topology",
                "role": role,
                "targetFeatureType": target_feature_type,
            }
        )

    return relationships


def _build_solids(data: dict[str, Any], shell_map: dict[str, Shell]) -> list[Solid]:
    """Build internal solid records from CSDM solid FeatureCollections.

    Resolves each solid's shell references through "shell_map" and populates
    both structured shell data and flattened face collections for compatibility
    with legacy validation rules. Solid features without a string "id" are skipped.
    Missing or invalid properties fall back to default internal values.

    Args:
        data: Parsed Topo Feature / 3D CSDM JSON object.
        shell_map: Shell records keyed by CSDM "shell" feature id.

    Returns:
        Solid records with topology, volume, level, theme, parcel type, and
        relationship fields.
    """
    solids: list[Solid] = []

    for feature in _iter_features(data, "solids"):
        solid_id = feature.get("id")
        if not isinstance(solid_id, str):
            continue

        shells, face_ids, face_orientations = _resolve_solid_shells(
            _topology_list(feature, "directed_references"),
            shell_map,
        )

        solid_properties = feature.get("properties", {})
        if not isinstance(solid_properties, dict):
            solid_properties = {}

        levels = solid_properties.get(
            "levels",
            solid_properties.get("floors", []),
        )

        solid: Solid = {
            "id": solid_id,
            "shells": shells,
            "faces": face_ids,
            "face_orientations": face_orientations,
            "volume": _float_or_default(solid_properties.get("volume")),
            "levels": _string_list_or_empty(levels),
            "theme": _string_or_default(
                solid_properties.get("theme"),
                "default",
            ),
            "parcel_type": _string_or_default(
                solid_properties.get("parcel_type"),
                "primary",
            ),
            "parent_id": _string_or_none(solid_properties.get("parent_id")),
            # Both spellings are carried through; TR-20 prefers burdened_id
            # and falls back to servient_id.  Without this the canonical
            # field would be unreadable from CSDM properties, leaving the
            # rule's preference for it unreachable outside hand-built data.
            "burdened_id": _string_or_none(solid_properties.get("burdened_id")),
            "servient_id": _string_or_none(solid_properties.get("servient_id")),
            "host_id": _string_or_none(solid_properties.get("host_id")),
            "relationships": _build_relationships(feature),
        }
        solids.append(solid)

    return solids


def _build_observation_curves(data: dict[str, Any]) -> list[ObservationCurve]:
    """Build observation curve exemption records from CSDM observation features.

    Collects curve references from "observedVectors" and
    "vectorObservations" so dangling-curve validation can exempt supporting
    observation geometry. Observation features without string references are
    skipped.

    Args:
        data: Parsed Topo Feature / 3D CSDM JSON object.

    Returns:
        Observation curve records with "ref" and "source" fields.
    """
    observation_curves: list[ObservationCurve] = []

    for feature in _iter_features(data, "observedVectors"):
        topology = feature.get("topology", {})
        ref = topology.get("ref") if isinstance(topology, dict) else None
        if isinstance(ref, str):
            observation_curves.append(
                {
                    "ref": ref,
                    "source": "observedVectors",
                }
            )

    for feature in _iter_features(data, "vectorObservations"):
        for ref_obj in _topology_list(feature, "directed_references"):
            if not isinstance(ref_obj, dict):
                continue

            ref = ref_obj.get("ref")
            if isinstance(ref, str):
                observation_curves.append(
                    {
                        "ref": ref,
                        "source": "vectorObservations",
                    }
                )

    return observation_curves


def _build_surface_shell_face_refs(
    shell_map: dict[str, Shell],
) -> list[SurfaceShellFaceReference]:
    """Collect face references from every CSDM shell in the dataset.

    Surface-only shells (shells that are not referenced by any solid, e.g.
    ground-surface shells) legitimately own faces that have no solid claims.
    TR-18 would otherwise flag those faces as dangling. Recording every
    shell-owned face here lets the dangling-face check exempt them, matching
    the pattern used for observation curves.

    Reads the flattened shells rather than the raw directed references, so a
    shell that reaches its faces through nested shells contributes those real
    face ids. Recording the nested shell's id instead would exempt an id no
    surface has, leaving the faces it carries reported as dangling.

    Args:
        shell_map: Flattened shell records keyed by CSDM "shell" feature id.

    Returns:
        Face reference records with "ref" and "shell_id" fields.
    """
    return [
        {"ref": face_id, "shell_id": shell_id}
        for shell_id, shell in shell_map.items()
        for face_id in shell["faces"]
    ]


def from_csdm_json(data: dict[str, Any]) -> TopologyData:
    """Convert Topo Feature / 3D CSDM JSON to internal topology data.

    Args:
        data: Parsed Topo Feature / 3D CSDM JSON object.

    Returns:
        Internal topology data with points, curves, surfaces, solids,
        observation curve references, and surface shell face references.
    """
    ring_map = _build_ring_map(data)
    shell_map = _build_shell_map(data)
    curves = _build_curves(data)
    curve_map = {curve["id"]: curve for curve in curves}

    return {
        "points": _build_points(data),
        "curves": curves,
        "surfaces": _build_surfaces(data, ring_map) + _build_parcel_surfaces(data, curve_map),
        "solids": _build_solids(data, shell_map),
        "observation_curves": _build_observation_curves(data),
        "surface_shell_face_refs": _build_surface_shell_face_refs(shell_map),
    }


def _ring_members_from_raw(raw_members: Any) -> list[RingMember]:
    """Convert raw directed references into typed internal ring members."""
    members: list[RingMember] = []

    if not isinstance(raw_members, list):
        return members

    for raw_member in raw_members:
        if not isinstance(raw_member, dict):
            continue

        ref = raw_member.get("ref")
        orientation = raw_member.get("orientation", "+")

        if not isinstance(ref, str):
            continue
        if orientation not in {"+", "-"}:
            continue

        members.append(
            {
                "ref": ref,
                "orientation": orientation,
            }
        )

    return members


def _string_or_none(value: Any) -> str | None:
    """Return value when it is a string, otherwise None."""
    return value if isinstance(value, str) else None


def _string_or_default(value: Any, default: str) -> str:
    """Return value when it is a string, otherwise a default string."""
    return value if isinstance(value, str) else default


def _float_or_default(value: Any, default: float = 0.0) -> float:
    """Return value as float when numeric, otherwise default."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return float(value)
    return default


def _string_list_or_empty(value: Any) -> list[str]:
    """Return a value when it is a list of strings, otherwise an empty list."""
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return []
