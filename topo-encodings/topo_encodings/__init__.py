"""Topo Feature encodings: a strict traversal of solids, and writers to JSON-FG 1.0.0
and per-face GeoJSON (RFC 7946). Standard library only."""

from .diagnostics import EncodingError, Problem
from .geojson_faces import to_geojson_faces
from .jsonfg_write import to_jsonfg
from .traverse import (
    FaceUse,
    Point,
    ShellView,
    SolidView,
    Traversal,
    require_coordinates,
    solids,
    traverse,
)

__all__ = [
    "EncodingError",
    "FaceUse",
    "Point",
    "Problem",
    "ShellView",
    "SolidView",
    "Traversal",
    "require_coordinates",
    "solids",
    "to_geojson_faces",
    "to_jsonfg",
    "traverse",
]
