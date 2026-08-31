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
    ReferenceSurface,
    Ring,
    RingMember,
    Shell,
    ShellType,
    Solid,
    Surface,
    TopologyData,
)

_ORIENTATION_FLIP: dict[Orientation, Orientation] = {"+": "-", "-": "+"}

# Features carrying these topology types live in the "edges" collection but are
# not curves -- a SubtendedAngle references a vertex plus two edges, so reading
# it as a curve yields spurious unknown-point and dangling-curve issues.
# Mirrors topo2geojson.SKIP_TOPOLOGY_TYPES.
_SKIP_TOPOLOGY_TYPES = frozenset({"subtendedangle"})


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


def _iter_features(
    data: dict[str, Any], collection_name: str
) -> Iterator[dict[str, Any]]:
    """Yield dict features from GeoJSON FeatureCollections under *collection_name*."""
    for collection in data.get(collection_name, []):
        if not isinstance(collection, dict):
            continue

        features = collection.get("features", [])
        if not isinstance(features, list):
            continue

        for feature in features:
            if isinstance(feature, dict):
                yield feature


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


def _topology_type(feature: dict[str, Any]) -> str:
    """Return a feature's lowercased topology type, or an empty string."""
    topology = feature.get("topology", {})
    if not isinstance(topology, dict):
        return ""

    topology_type = topology.get("type")
    return topology_type.lower() if isinstance(topology_type, str) else ""


def _build_curves(data: dict[str, Any]) -> list[Curve]:
    """Build internal curve records from CSDM edge FeatureCollections.

    Features without a string id or list of references are skipped, as are
    features whose topology type is not a curve (see "_SKIP_TOPOLOGY_TYPES").

    Args:
        data: Parsed Topo Feature / 3D CSDM JSON object.

    Returns:
        Curve records with "id" and "vertices" fields.
    """
    curves: list[Curve] = []

    for feature in _iter_features(data, "edges"):
        if _topology_type(feature) in _SKIP_TOPOLOGY_TYPES:
            continue

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
                rings.append(ring)

        surfaces.append(
            {
                "id": surface_id,
                "rings": rings,
            }
        )

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
        shell: Shell = {
            "type": "outer",
            "faces": faces,
            "face_orientations": face_orientations,
        }
        shell_map[shell_id] = shell

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
        # noinspection PyTypeChecker
        # Both sides are dict[str, Orientation]. PyCharm widens Literal value
        # types to str through generic dict methods, so it rejects this (and
        # every equivalent spelling: |=, item assignment, comprehension,
        # setdefault). Its own message lists the satisfied overload.
        flattened_face_orientations.update(resolved_shell["face_orientations"])

    return shells, flattened_face_ids, flattened_face_orientations


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
            "servient_id": _string_or_none(solid_properties.get("servient_id")),
            "host_id": _string_or_none(solid_properties.get("host_id")),
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


def _iter_reference_surface_definitions(
    data: dict[str, Any],
) -> Iterator[dict[str, Any]]:
    """Yield "referenceSurfaces" entries from parcel spatial representations."""
    for collection in data.get("parcels", []):
        if not isinstance(collection, dict):
            continue

        properties = collection.get("properties", {})
        if not isinstance(properties, dict):
            continue

        definitions = properties.get("spatialRepresentationDefinitions", {})
        if not isinstance(definitions, dict):
            continue

        reference_surfaces = definitions.get("referenceSurfaces", [])
        if not isinstance(reference_surfaces, list):
            continue

        for reference_surface in reference_surfaces:
            if isinstance(reference_surface, dict):
                yield reference_surface


def _build_reference_surfaces(
    data: dict[str, Any],
    shell_map: dict[str, Shell],
) -> list[ReferenceSurface]:
    """Build reference surface face exemptions from parcel definitions.

    A parcel's "referenceSurfaces" entries name the surfaces a derived solid
    was computed from -- for example, the ground surface an offset solid is
    built around. Such a surface is an input to the derivation rather than part
    of any solid's boundary, so TR-18 would otherwise report its faces as
    dangling.

    Each entry's "ref" may name a shell, in which case it expands to that
    shell's faces, or a face directly. Entries without a string "ref" are
    skipped.

    Args:
        data: Parsed Topo Feature / 3D CSDM JSON object.
        shell_map: Shell records keyed by CSDM "shell" feature id.

    Returns:
        Reference surface records with "ref" and "source" fields.
    """
    reference_surfaces: list[ReferenceSurface] = []
    seen: set[tuple[str, str]] = set()

    for definition in _iter_reference_surface_definitions(data):
        ref = definition.get("ref")
        if not isinstance(ref, str):
            continue

        source = _string_or_default(definition.get("id"), ref)
        shell = shell_map.get(ref)
        face_ids = list(shell["faces"]) if shell is not None else [ref]

        for face_id in face_ids:
            key = (face_id, source)
            if key in seen:
                continue

            seen.add(key)
            reference_surfaces.append(
                {
                    "ref": face_id,
                    "source": source,
                }
            )

    return reference_surfaces


def from_csdm_json(data: dict[str, Any]) -> TopologyData:
    """Convert Topo Feature / 3D CSDM JSON to internal topology data.

    Args:
        data: Parsed Topo Feature / 3D CSDM JSON object.

    Returns:
        Internal topology data with points, curves, surfaces, solids,
        observation curve references, and reference surface exemptions.
    """
    ring_map = _build_ring_map(data)
    shell_map = _build_shell_map(data)

    return {
        "points": _build_points(data),
        "curves": _build_curves(data),
        "surfaces": _build_surfaces(data, ring_map),
        "solids": _build_solids(data, shell_map),
        "observation_curves": _build_observation_curves(data),
        "reference_surfaces": _build_reference_surfaces(data, shell_map),
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
