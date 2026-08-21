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
    Ring,
    RingMember,
    Shell,
    ShellType,
    Solid,
    Surface,
    TopologyData,
)


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


def _build_curves(data: dict[str, Any]) -> list[Curve]:
    """Build internal curve records from CSDM edge FeatureCollections.

    Features without a string id or list of references are skipped.

    Args:
        data: Parsed Topo Feature / 3D CSDM JSON object.

    Returns:
        Curve records with "id" and "vertices" fields.
    """
    curves: list[Curve] = []

    for feature in _iter_features(data, "edges"):
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


def _build_shell_map(data: dict[str, Any]) -> dict[str, Shell]:
    """Build internal shell records from CSDM shell FeatureCollections.

    Converts each shell's directed face references into internal face id and
    orientation lists. Shell features without a string "id" are skipped.

    Args:
        data: Parsed Topo Feature / 3D CSDM JSON object.

    Returns:
        Shell records keyed by CSDM "shell" feature id.
    """
    shell_map: dict[str, Shell] = {}

    for feature in _iter_features(data, "shells"):
        shell_id = feature.get("id")
        if not isinstance(shell_id, str):
            continue

        faces, face_orientations = _shell_faces_from_raw(
            _topology_list(feature, "directed_references")
        )
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
            "face_orientations": dict(shell["face_orientations"]),
        }

        shells.append(resolved_shell)
        flattened_face_ids.extend(resolved_shell["faces"])
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
            _topology_list(feature, "shells"),
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


def from_csdm_json(data: dict[str, Any]) -> TopologyData:
    """Convert Topo Feature / 3D CSDM JSON to internal topology data.

    Args:
        data: Parsed Topo Feature / 3D CSDM JSON object.

    Returns:
        Internal topology data with points, curves, surfaces, solids, and
        observation curve references.
    """
    ring_map = _build_ring_map(data)
    shell_map = _build_shell_map(data)

    return {
        "points": _build_points(data),
        "curves": _build_curves(data),
        "surfaces": _build_surfaces(data, ring_map),
        "solids": _build_solids(data, shell_map),
        "observation_curves": _build_observation_curves(data),
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


def _shell_faces_from_raw(
    raw_face_refs: Any,
) -> tuple[list[str], dict[str, Orientation]]:
    """Convert raw shell face references into typed face ids and orientations."""
    faces: list[str] = []
    face_orientations: dict[str, Orientation] = {}

    if not isinstance(raw_face_refs, list):
        return faces, face_orientations

    for raw_face_ref in raw_face_refs:
        if not isinstance(raw_face_ref, dict):
            continue

        face_id = raw_face_ref.get("ref")
        raw_orientation = raw_face_ref.get("orientation", "+")

        if not isinstance(face_id, str):
            continue

        orientation: Orientation = (
            raw_orientation if raw_orientation in {"+", "-"} else "+"
        )

        faces.append(face_id)
        face_orientations[face_id] = orientation

    return faces, face_orientations


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
