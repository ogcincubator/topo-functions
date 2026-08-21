#!/usr/bin/env python3

"""Shared data model, tolerances, issue helpers, and topology indexes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypedDict

try:
    from typing import NotRequired
except ImportError:
    from typing_extensions import NotRequired

TOLERANCE_POINT: float = 1e-6
TOLERANCE_VOLUME: float = 1e-9
TOLERANCE_LENGTH: float = 1e-3
TOLERANCE_THICKNESS: float = 1e-3

Severity = Literal["error", "warning"]
Orientation = Literal["+", "-"]
ShellType = Literal["outer", "inner"]
Coordinate3D = list[float]


class Issue(TypedDict):
    """Validation issue returned by topology rule checks."""

    code: str
    severity: Severity
    message: str
    object_id: str | None
    path: str | None
    extra: dict[str, Any]


class Point(TypedDict):
    """Internal point record with an id and 3D coordinates."""

    id: str
    coordinates: Coordinate3D


class Curve(TypedDict):
    """Internal curve record referencing ordered point ids."""

    id: str
    vertices: list[str]


class RingMember(TypedDict):
    """Directed curve reference used as a surface ring member."""

    ref: str
    orientation: Orientation


class Ring(TypedDict):
    """Surface boundary ring made from directed curve members."""

    type: str
    members: list[RingMember]


class Surface(TypedDict):
    """Internal surface record containing one or more boundary rings."""

    id: str
    rings: list[Ring]


class Shell(TypedDict):
    """Solid shell containing face ids and per-face orientations."""

    type: ShellType
    faces: list[str]
    face_orientations: dict[str, Orientation]


class Solid(TypedDict):
    """Internal solid record used by topology validation rules."""

    id: str
    shells: NotRequired[list[Shell]]
    faces: list[str]
    face_orientations: NotRequired[dict[str, Orientation]]
    volume: float
    theme: str
    parcel_type: str
    parent_id: str | None
    servient_id: str | None
    burdened_id: NotRequired[str | None]
    host_id: str | None
    levels: list[str]


class ObservationCurve(TypedDict):
    """Observation curve exemption for dangling-curve validation."""

    ref: str
    source: Literal["observedVectors", "vectorObservations"]


class TopologyData(TypedDict):
    """Complete internal topology dataset used by the validator."""

    points: list[Point]
    curves: list[Curve]
    surfaces: list[Surface]
    solids: list[Solid]
    observation_curves: NotRequired[list[ObservationCurve]]


class TopologyIndexes(TypedDict):
    """Lookup indexes for topology records keyed by object id."""

    points: dict[str, Point]
    curves: dict[str, Curve]
    surfaces: dict[str, Surface]
    solids: dict[str, Solid]


@dataclass(frozen=True)
class Tolerances:
    """Validation tolerances used by topology rule checks."""

    point: float = TOLERANCE_POINT
    volume: float = TOLERANCE_VOLUME
    length: float = TOLERANCE_LENGTH
    thickness: float = TOLERANCE_THICKNESS


def err(
    code: str,
    message: str,
    object_id: str | None = None,
    path: str | None = None,
    extra: dict[str, Any] | None = None,
    severity: Severity = "error",
) -> Issue:
    """Build a validation issue.

    Args:
        code: Stable issue code.
        message: Human-readable issue description.
        object_id: Optional id of the affected topology object.
        path: Optional data path for structural issues.
        extra: Optional machine-readable issue metadata.
        severity: Issue severity.

    Returns:
        Validation issue dictionary.
    """
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "object_id": object_id,
        "path": path,
        "extra": extra or {},
    }


def warn(
    code: str,
    message: str,
    object_id: str | None = None,
    path: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Issue:
    """Build a warning validation issue.

    Args:
        code: Stable issue code.
        message: Human-readable issue description.
        object_id: Optional id of the affected topology object.
        path: Optional data path for structural issues.
        extra: Optional machine-readable issue metadata.

    Returns:
        Warning validation issue dictionary.
    """
    return err(code, message, object_id, path, extra, severity="warning")


def has_error(items: list[Issue], code: str) -> bool:
    """Return whether any issue has the requested code.

    Args:
        items: Validation issues to search.
        code: Issue code to find.

    Returns:
        True when an issue with the requested code exists.
    """
    return any(e["code"] == code for e in items)


def errors_only(items: list[Issue]) -> list[Issue]:
    """Return only error-severity issues

    Args:
        items: Validation issues to filter.

    Returns:
        Issues whose severity is "error" or omitted.
    """
    return [e for e in items if e.get("severity", "error") == "error"]


def build_indexes(data: TopologyData) -> TopologyIndexes:
    """Build lookup indexes for topology collections.

    Args:
        data: Valid internal topology data.

    Returns:
        Lookup dictionaries keyed by object id.
    """
    points: dict[str, Point] = {point["id"]: point for point in data.get("points", [])}
    curves: dict[str, Curve] = {curve["id"]: curve for curve in data.get("curves", [])}
    surfaces: dict[str, Surface] = {
        surface["id"]: surface for surface in data.get("surfaces", [])
    }
    solids: dict[str, Solid] = {solid["id"]: solid for solid in data.get("solids", [])}

    return {
        "points": points,
        "curves": curves,
        "surfaces": surfaces,
        "solids": solids,
    }
