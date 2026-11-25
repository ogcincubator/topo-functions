"""Topo Feature solids to JSON-FG 1.0.0 (OGC 21-045r1), conformance classes core and
polyhedra.

Each solid becomes a ``Polyhedron`` in ``place``, in the source's ``place`` coordinate
reference system with the coordinates unchanged; ``geometry`` is ``null``, because
JSON-FG allows only GeoJSON geometry types there (the per-face GeoJSON writer gives the
WGS 84 view).
With ``features="multi"`` all solids become one ``MultiPolyhedron``.
Each feature has ``"time": null``, temporarily: a Topo Feature solid has no temporal
extent, and 1.0.0 requires neither ``time`` nor ``place`` (a deliberate simplification
after the 2024 feedback in ogc-feat-geo-json#128,
https://github.com/opengeospatial/ogc-feat-geo-json/issues/128), but the OGC Building
Blocks JSON-FG ``feature`` block (based on JSON-FG 0.1.1) still requires ``time``.
The member is to be dropped once the blocks follow 1.0.0.

Orientation comes from the topology through :mod:`topo_encodings.traverse`, and the
ring order is written as it is: orientation is physical in both Topo Feature and
JSON-FG (see :mod:`topo_encodings.orientation`), so it needs no change in a
left-handed ``place`` CRS.
Each shell is checked to face away from its solid's material; a source that fails
(such as a void reversed twice) is refused, or, with ``check_orientation=False``,
carried into the encoding as it is, never repaired.
No ``featureType`` is written, which would bring in the types-schemas class.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence, Union

from .diagnostics import EncodingError, Problem
from .orientation import axes, check_orientation as _check_orientation
from .traverse import SolidView, Traversal, require_coordinates, traverse

CORE = "http://www.opengis.net/spec/json-fg-1/1.0/conf/core"
POLYHEDRA = "http://www.opengis.net/spec/json-fg-1/1.0/conf/polyhedra"
CONFORMS_TO = (CORE, POLYHEDRA)
FEATURES = ("per-solid", "multi")

Crs = Union[str, Sequence[str]]


def to_jsonfg(
    doc: Mapping[str, Any],
    *,
    crs: Crs,
    place_axes: str,
    features: str = "per-solid",
    name: Optional[str] = None,
    derived_from: Optional[Mapping[str, Any]] = None,
    check_orientation: bool = True,
) -> dict[str, Any]:
    """The JSON-FG FeatureCollection for a Topo Feature document.

    ``crs`` is the ``place`` coordinate reference system as a URI, or a list of URIs
    for a compound one (for example horizontal and vertical); it is written as
    ``coordRefSys`` and is required, because a Topo Feature document does not state
    it in a form JSON-FG can use.
    ``place_axes`` states that CRS's axis order, such as ``"east-north-up"`` (MGA) or
    ``"north-east-up"`` (NZTM), for the orientation check; ``check_orientation=False``
    skips the check, to encode a source whose defect should be shown.
    ``name`` defaults to the document's ``surveyTitle`` or ``name``; ``derived_from``,
    when given, is written as the collection's ``derivedFrom`` (provenance).
    Raises :class:`EncodingError` when the document cannot be encoded.
    """
    coord_ref_sys = _coord_ref_sys(crs)
    to_enu = axes(place_axes)
    if features not in FEATURES:
        raise ValueError(f"features must be one of {FEATURES}, not {features!r}")
    traversal = traverse(doc)
    if not traversal.solids:
        raise EncodingError([Problem("NO_SOLIDS", "the document has no solids")])
    require_coordinates(traversal, "place")
    if check_orientation:
        _check_orientation(traversal, to_enu)

    if features == "per-solid":
        out_features = [_solid_feature(traversal, s) for s in traversal.solids]
    else:
        out_features = [_multi_feature(doc, traversal)]
    collection: dict[str, Any] = {
        "type": "FeatureCollection",
        "conformsTo": list(CONFORMS_TO),
        "coordRefSys": coord_ref_sys,
        "geometryDimension": 3,
        "name": name if name is not None else _document_name(doc),
    }
    if derived_from is not None:
        collection["derivedFrom"] = dict(derived_from)
    collection["features"] = out_features
    return collection


def polyhedron_coordinates(traversal: Traversal, solid: SolidView) -> list:
    """A solid's ``Polyhedron`` coordinates: shells of faces of closed rings."""
    return [
        [
            [
                _closed([list(traversal.points[p].place) for p in loop])
                for loop in face.loops
            ]
            for face in shell.faces
        ]
        for shell in solid.shells
    ]


def _solid_feature(traversal: Traversal, solid: SolidView) -> dict[str, Any]:
    return {
        "type": "Feature",
        "id": solid.id,
        "time": None,
        "place": {
            "type": "Polyhedron",
            "coordinates": polyhedron_coordinates(traversal, solid),
        },
        "geometry": None,
        "properties": _solid_properties(traversal, solid),
    }


def _multi_feature(doc: Mapping[str, Any], traversal: Traversal) -> dict[str, Any]:
    feature: dict[str, Any] = {"type": "Feature"}
    if isinstance(doc.get("id"), (str, int)):
        feature["id"] = doc["id"]
    feature["time"] = None
    feature["place"] = {
        "type": "MultiPolyhedron",
        "coordinates": [polyhedron_coordinates(traversal, s) for s in traversal.solids],
    }
    feature["geometry"] = None
    feature["properties"] = {
        "solids": [
            {"id": s.id, **_solid_properties(traversal, s)} for s in traversal.solids
        ]
    }
    return feature


def _solid_properties(traversal: Traversal, solid: SolidView) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "name": solid.properties.get("name"),
        "ptQualityMeasure": point_quality(traversal, solid.point_ids()),
        "shells": [
            {"id": shell.id, "role": shell.role, "faces": len(shell.faces)}
            for shell in solid.shells
        ],
    }
    if solid.relationships is not None:
        properties["relationships"] = [dict(r) for r in solid.relationships]
    return properties


def point_quality(traversal: Traversal, point_ids: Sequence[str]) -> Optional[float]:
    """The largest ``ptQualityMeasure`` of the points, or None if any point has none.

    A point's value counts only when it is a positive number (metres, 95% confidence).
    """
    values = []
    for pid in point_ids:
        value = traversal.points[pid].properties.get("ptQualityMeasure")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            return None
        values.append(float(value))
    return max(values) if values else None


def _coord_ref_sys(crs: Crs) -> Union[str, list[str]]:
    if isinstance(crs, str):
        if not crs:
            raise ValueError("crs is empty")
        return crs
    uris = list(crs)
    if not uris or not all(isinstance(u, str) and u for u in uris):
        raise ValueError("crs must be a URI or a non-empty list of URIs")
    return uris


def _document_name(doc: Mapping[str, Any]) -> Optional[str]:
    return doc.get("surveyTitle") or doc.get("name")


def _closed(ring: list[list[float]]) -> list[list[float]]:
    return ring + [ring[0]]
