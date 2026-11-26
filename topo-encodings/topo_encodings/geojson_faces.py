"""Topo Feature solids to per-face GeoJSON (RFC 7946).

One ``Polygon`` feature per face per solid, in longitude, latitude and ellipsoidal
height, with the solid, the shell, the shell's role and the face's orientation in the
solid in ``properties``.
A face shared by two solids appears once for each, in that solid's orientation.
The collection is a view of the faces: it is not a solid and does not claim to be one.

RFC 7946 (section 3.1.6) wants exterior rings counter-clockwise and holes clockwise in
longitude and latitude, which says nothing about a face's 3D orientation; a face whose
outward normal points down is rewound (all its rings together), a vertical face is
left as it is (its winding is undefined), and the face's outward unit normal is kept
in ``outwardNormal``, so nothing is lost.
Both decisions are taken from that normal, not from the rings' area in longitude and
latitude: for a wall, that area comes only from coordinate rounding.

The normal is computed from ``place`` and given as (east, north, up) whatever the
``place`` CRS's axis order, which is taken from ``crs`` or stated (``place_axes``);
orientation is physical (see :mod:`topo_encodings.orientation`), and each shell is
checked to face away from its solid's material unless the caller turns the check off.
This assumes the ``place`` CRS is a conformal projection, so that a face facing up is
counter-clockwise in longitude and latitude.

Heights: GeoJSON positions carry height above the ellipsoid (RFC 7946, section 4).
They come either from a ``transformer`` the caller supplies, from ``place`` to WGS 84
longitude, latitude and ellipsoidal height, or, when the caller states that the
document's ``geometry`` heights are ellipsoidal (``heights="ellipsoidal-geometry"``),
from ``geometry``.
Otherwise the writer refuses: it never assumes heights are ellipsoidal, and it does
not download geoid grids.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Mapping, Optional, Sequence

from .diagnostics import EncodingError, Problem
from .jsonfg_write import point_quality
from .orientation import PLACE_AXES, Crs, crs_uris, resolve_axes
from .orientation import check_orientation as _check_orientation
from .traverse import Traversal, require_coordinates, traverse

ELLIPSOIDAL_GEOMETRY = "ellipsoidal-geometry"
LONLAT_DECIMALS = 9  # about 0.1 mm
HEIGHT_DECIMALS = 4
NORMAL_DECIMALS = 9
VERTICAL_TOLERANCE = (
    1e-6  # |vertical component| of the unit normal; about 0.00006 degrees
)

Transformer = Callable[[float, float, float], Sequence[float]]

__all__ = ["PLACE_AXES", "to_geojson_faces"]


def to_geojson_faces(
    doc: Mapping[str, Any],
    *,
    crs: Optional[Crs] = None,
    place_axes: Optional[str] = None,
    transformer: Optional[Transformer] = None,
    heights: Optional[str] = None,
    decimals: tuple[int, int] = (LONLAT_DECIMALS, HEIGHT_DECIMALS),
    name: Optional[str] = None,
    derived_from: Optional[Mapping[str, Any]] = None,
    check_orientation: bool = True,
) -> dict[str, Any]:
    """The RFC 7946 FeatureCollection of faces for a Topo Feature document.

    ``place`` coordinates are in the CRS's authority axis order: give ``crs`` (a URI,
    or a list for a compound CRS) to take it from the CRS with pyproj (the ``crs``
    extra), or ``place_axes``, such as ``"east-north-up"`` (MGA) or
    ``"north-east-up"`` (NZTM; see :data:`PLACE_AXES`), without pyproj or for a CRS
    it cannot look up; given both, they must agree
    (see :func:`topo_encodings.orientation.resolve_axes`).
    ``crs`` is used only for the axis order: it is not written.
    Give exactly one height source: ``transformer``, called with each point's
    ``place`` coordinates in that axis order and returning
    ``(longitude, latitude, ellipsoidal height)``; or ``heights="ellipsoidal-geometry"``
    to use each point's ``geometry`` as it is.
    Every point needs ``place``, which ``outwardNormal`` is computed in; with
    ``heights="ellipsoidal-geometry"`` every point also needs ``geometry``.
    ``decimals`` rounds longitude and latitude, and height.
    ``check_orientation=False`` skips the shell orientation check, to encode a
    source whose defect should be shown rather than refused.
    Raises :class:`EncodingError` when the document cannot be encoded.
    """
    if crs is not None:
        crs_uris(crs)
    to_enu = resolve_axes(place_axes, crs)
    if (transformer is None) == (heights is None):
        raise EncodingError(
            [
                Problem(
                    "NO_HEIGHT_SOURCE" if transformer is None else "TWO_HEIGHT_SOURCES",
                    "give either a transformer from place to longitude, latitude and "
                    "ellipsoidal height, or heights='ellipsoidal-geometry' to state "
                    "that the geometry heights are ellipsoidal",
                )
            ]
        )
    if heights is not None and heights != ELLIPSOIDAL_GEOMETRY:
        raise ValueError(f"heights must be {ELLIPSOIDAL_GEOMETRY!r}, not {heights!r}")
    traversal = traverse(doc)
    if not traversal.solids:
        raise EncodingError([Problem("NO_SOLIDS", "the document has no solids")])
    require_coordinates(traversal, "place")
    if check_orientation:
        _check_orientation(traversal, to_enu)
    if heights is not None:
        require_coordinates(traversal, "geometry")
    lonlath = _positions(traversal, transformer, decimals)

    features = []
    degenerate = []
    for solid in traversal.solids:
        for shell in solid.shells:
            for face in shell.faces:
                rings = [_closed([lonlath[p] for p in loop]) for loop in face.loops]
                normal = unit_normal(
                    [
                        [to_enu(traversal.points[p].place) for p in loop]
                        for loop in face.loops
                    ]
                )
                if normal is None:
                    problem = Problem("DEGENERATE_FACE", "face has no area", face.id)
                    if problem not in degenerate:
                        degenerate.append(problem)
                    continue
                rewound = rfc7946_winding(rings, normal)
                features.append(
                    {
                        "type": "Feature",
                        "id": face.id,
                        "geometry": {"type": "Polygon", "coordinates": rewound},
                        "properties": {
                            "solid": solid.id,
                            "shell": shell.id,
                            "shellRole": shell.role,
                            "orientationInSolid": "+" if face.sign > 0 else "-",
                            "ptQualityMeasure": point_quality(
                                traversal, [p for loop in face.loops for p in loop]
                            ),
                            "reversedForRfc7946": rewound != rings,
                            "outwardNormal": normal,
                        },
                    }
                )
    if degenerate:
        raise EncodingError(degenerate)
    collection: dict[str, Any] = {
        "type": "FeatureCollection",
        "name": (
            name if name is not None else (doc.get("surveyTitle") or doc.get("name"))
        ),
    }
    if derived_from is not None:
        collection["derivedFrom"] = dict(derived_from)
    collection["features"] = features
    return collection


def _positions(
    traversal: Traversal,
    transformer: Optional[Transformer],
    decimals: tuple[int, int],
) -> dict[str, list[float]]:
    """Rounded longitude, latitude and ellipsoidal height of every point the solids
    use."""
    lonlat_decimals, height_decimals = decimals
    used = dict.fromkeys(p for s in traversal.solids for p in s.point_ids())
    out: dict[str, list[float]] = {}
    problems = []
    for pid in used:
        point = traversal.points[pid]
        if transformer is None:
            values: Sequence[Any] = point.geometry
        else:
            try:
                values = transformer(*point.place)
            except Exception as exc:  # the caller's transformer: report, don't crash
                problems.append(
                    Problem("TRANSFORM_FAILED", f"transformer raised {exc!r}", pid)
                )
                continue
        try:
            lon, lat, height = (float(v) for v in values)
        except (TypeError, ValueError):
            problems.append(
                Problem("TRANSFORM_FAILED", f"not three numbers: {values!r}", pid)
            )
            continue
        if not all(math.isfinite(v) for v in (lon, lat, height)):
            problems.append(
                Problem("TRANSFORM_FAILED", f"not finite: {(lon, lat, height)}", pid)
            )
            continue
        out[pid] = [
            round(lon, lonlat_decimals),
            round(lat, lonlat_decimals),
            round(height, height_decimals),
        ]
    if problems:
        raise EncodingError(problems)
    return out


def is_vertical(normal: Sequence[float]) -> bool:
    """Whether a face is vertical: its winding in longitude and latitude is then
    undefined."""
    return abs(normal[2]) <= VERTICAL_TOLERANCE


def rfc7946_winding(
    rings: list[list[list[float]]], normal: Sequence[float]
) -> list[list[list[float]]]:
    """A face's rings wound as RFC 7946 section 3.1.6 asks (exterior counter-clockwise,
    holes clockwise), given the face's outward unit normal in ``place``.

    A face facing down is reversed, all its rings together, so the relation between
    its exterior and its holes is kept as the source has it; a vertical face is left
    as it is.
    """
    if is_vertical(normal) or normal[2] > 0:
        return rings
    return [ring[::-1] for ring in rings]


def unit_normal(loops: Sequence[Sequence[Sequence[float]]]) -> Optional[list[float]]:
    """Outward unit normal (Newell's method) of a face's loops, rounded; None when
    the face has no area.
    The coordinates must be in a right-handed system, such as east-north-up.

    Each loop is taken about its own centroid, so large projected coordinates do not
    lose precision.
    """
    total = [0.0, 0.0, 0.0]
    for loop in loops:
        n = len(loop)
        centroid = [sum(p[i] for p in loop) / n for i in range(3)]
        points = [[p[i] - centroid[i] for i in range(3)] for p in loop]
        for a, b in zip(points, points[1:] + points[:1]):
            total[0] += 0.5 * (a[1] * b[2] - a[2] * b[1])
            total[1] += 0.5 * (a[2] * b[0] - a[0] * b[2])
            total[2] += 0.5 * (a[0] * b[1] - a[1] * b[0])
    length = math.sqrt(sum(v * v for v in total))
    if length == 0.0:
        return None
    return [round(v / length, NORMAL_DECIMALS) for v in total]


def _closed(ring: list[list[float]]) -> list[list[float]]:
    return ring + [ring[0]]
