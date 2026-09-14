#!/usr/bin/env python3
"""Tests for TR-05 (SharedSurfaceEdges / SHARED_EDGE_SAME_ORIENTATION).

TR-05 had no dedicated tests before this file -- neither for the 3D `faces`
path (explicit, author-supplied `RingMember.orientation`) nor the 2D
`parcels` path (orientation inferred from undirected curve-id references).

The 2D cases below exercise `loader._canonicalize_ring_winding`: a `parcels`
ring's raw chase (`loader._chain_ring_curve_orientations`) only guarantees a
*closed* traversal, not a canonical winding -- which of its two possible
rotational directions it lands on is an accident of the order curves happen
to be listed in `references`. Comparing that listing-order-dependent
orientation across two different parcel surfaces (exactly what TR-05 does)
produced false positives for ordinary adjacent cadastral parcels (see
`aggregate-polygon.json` in the `topo-feature` register) until winding was
pinned to real point coordinates (counter-clockwise exterior rings,
clockwise holes).
"""

from __future__ import annotations

from typing import Any

from topo_validator.conformance.cc03_surfaces import validate_shared_surface_edges
from topo_validator.loader import from_csdm_json

# ---------------------------------------------------------------------------
# 3D `faces` path -- explicit, author-supplied orientation (unaffected by the
# 2D fix; previously untested in either direction).
# ---------------------------------------------------------------------------


def _ring(*members: tuple[str, str]) -> dict[str, Any]:
    return {
        "type": "outer",
        "members": [{"ref": ref, "orientation": orientation} for ref, orientation in members],
    }


def _surface(surface_id: str, *rings: dict[str, Any]) -> dict[str, Any]:
    return {"id": surface_id, "rings": list(rings)}


def test_3d_faces_sharing_an_edge_with_opposite_orientation_pass():
    """Two adjacent unit-square faces, correctly traversing their shared
    edge `e1` in opposite directions -- the expected outward-normal
    convention -- must not trip TR-05."""
    sf1 = _surface("sf1", _ring(("e0", "+"), ("e1", "+"), ("e2", "+"), ("e3", "+")))
    sf2 = _surface("sf2", _ring(("e7", "-"), ("e6", "-"), ("e5", "-"), ("e1", "-")))

    issues = validate_shared_surface_edges({"surfaces": [sf1, sf2]})

    assert issues == []


def test_3d_faces_sharing_an_edge_with_same_orientation_fails():
    """Two adjacent faces both traversing their shared edge `e1` the same
    direction is a broken outward-normal convention on that edge -- TR-05
    must catch it."""
    sf1 = _surface("sf1", _ring(("e0", "+"), ("e1", "+"), ("e2", "+"), ("e3", "+")))
    sf2 = _surface("sf2", _ring(("e7", "-"), ("e6", "-"), ("e5", "-"), ("e1", "+")))

    issues = validate_shared_surface_edges({"surfaces": [sf1, sf2]})

    assert [issue["code"] for issue in issues] == ["SHARED_EDGE_SAME_ORIENTATION"]
    assert issues[0]["object_id"] == "e1"


# ---------------------------------------------------------------------------
# 2D `parcels` path -- orientation derived from ring winding.
# ---------------------------------------------------------------------------


def _csdm_point(point_id: str, x: float, y: float) -> dict[str, Any]:
    return {
        "id": point_id,
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [x, y]},
        "properties": {},
    }


def _csdm_edge(edge_id: str, start_id: str, end_id: str) -> dict[str, Any]:
    return {
        "id": edge_id,
        "type": "Feature",
        "geometry": None,
        "topology": {"type": "Edge", "references": [start_id, end_id]},
        "properties": {},
    }


def _csdm_parcel(parcel_id: str, *rings: list[str]) -> dict[str, Any]:
    return {
        "id": parcel_id,
        "type": "Feature",
        "geometry": None,
        "topology": {"type": "Polygon", "references": [list(ring) for ring in rings]},
        "properties": {},
    }


def _csdm_doc(
    points: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    parcels: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "featureType": "CSD",
        "features": [],
        "points": [
            {"type": "FeatureCollection", "featureType": "BoundaryMark", "features": points}
        ],
        "edges": [{"type": "FeatureCollection", "featureType": "Edge", "features": edges}],
        "parcels": [
            {"type": "FeatureCollection", "featureType": "PrimaryParcel", "features": parcels}
        ],
    }


# Shared by several tests below: two triangles on opposite sides of the same
# diagonal edge AE1, exactly matching `topo-feature`'s
# `aggregate-polygon.json` example (AP1=(10,10), AP2=(20,20), AP3 above the
# diagonal, AP4 below it).
_ADJACENT_POINTS = [
    _csdm_point("AP1", 10.0, 10.0),
    _csdm_point("AP2", 20.0, 20.0),
    _csdm_point("AP3", 13.0, 17.0),
    _csdm_point("AP4", 20.0, 10.0),
]
_ADJACENT_EDGES = [
    _csdm_edge("AE1", "AP1", "AP2"),
    _csdm_edge("AE2", "AP2", "AP3"),
    _csdm_edge("AE3", "AP3", "AP1"),
    _csdm_edge("AE4", "AP1", "AP4"),
    _csdm_edge("AE5", "AP4", "AP2"),
]


def test_adjacent_2d_parcels_sharing_an_edge_pass():
    """Two genuinely adjacent cadastral parcels -- non-overlapping triangles
    on opposite sides of diagonal AE1 -- share AE1 and must not trip TR-05,
    even though both parcels list AE1 first in their `references` (the
    listing order that used to force both to a spurious `+`)."""
    parcels = [
        _csdm_parcel("AParcel1", ["AE1", "AE2", "AE3"]),
        _csdm_parcel("AParcel2", ["AE1", "AE5", "AE4"]),
    ]
    topology = from_csdm_json(_csdm_doc(_ADJACENT_POINTS, _ADJACENT_EDGES, parcels))

    issues = validate_shared_surface_edges(topology)

    assert issues == []


def test_2d_parcel_ring_listing_order_does_not_change_the_result():
    """The same two parcels, with AParcel2's ring rotated to list a
    different edge first (`AE5, AE4, AE1` instead of `AE1, AE5, AE4`) --
    same edges, same polygon -- must produce the identical TR-05 result.
    Guards against reintroducing order-dependence in ring canonicalization."""
    parcels = [
        _csdm_parcel("AParcel1", ["AE1", "AE2", "AE3"]),
        _csdm_parcel("AParcel2", ["AE5", "AE4", "AE1"]),
    ]
    topology = from_csdm_json(_csdm_doc(_ADJACENT_POINTS, _ADJACENT_EDGES, parcels))

    issues = validate_shared_surface_edges(topology)

    assert issues == []

    surfaces = {surface["id"]: surface for surface in topology["surfaces"]}
    ae1_orientation = {
        member["orientation"]
        for surface in surfaces.values()
        for ring in surface["rings"]
        for member in ring["members"]
        if member["ref"] == "AE1"
    }
    assert ae1_orientation == {"+", "-"}


def test_two_overlapping_2d_parcels_on_the_same_side_of_shared_edge_fails():
    """Two triangles both on the *same* side of diagonal AE1 -- a genuine
    overlap along the shared edge, not an authoring-order artifact -- must
    still trip TR-05. Confirms the fix derives real orientation rather than
    just suppressing the rule for 2D content."""
    points = _ADJACENT_POINTS + [_csdm_point("AP5", 14.0, 18.0)]
    edges = _ADJACENT_EDGES[:3] + [
        _csdm_edge("AE6", "AP2", "AP5"),
        _csdm_edge("AE7", "AP5", "AP1"),
    ]
    parcels = [
        _csdm_parcel("AParcel1", ["AE1", "AE2", "AE3"]),
        _csdm_parcel("AParcel2b", ["AE1", "AE6", "AE7"]),
    ]
    topology = from_csdm_json(_csdm_doc(points, edges, parcels))

    issues = validate_shared_surface_edges(topology)

    assert [issue["code"] for issue in issues] == ["SHARED_EDGE_SAME_ORIENTATION"]
    assert issues[0]["object_id"] == "AE1"


def test_2d_parcel_with_hole_ring_gets_opposite_winding_from_its_outer_ring():
    """A parcel with an interior (hole) ring: the outer ring canonicalizes
    to counter-clockwise, the hole ring to clockwise -- the standard
    exterior/hole winding convention -- regardless of how each ring's edges
    happen to be listed."""
    points = [
        _csdm_point("OP1", 0.0, 0.0),
        _csdm_point("OP2", 10.0, 0.0),
        _csdm_point("OP3", 10.0, 10.0),
        _csdm_point("OP4", 0.0, 10.0),
        _csdm_point("HP1", 3.0, 3.0),
        _csdm_point("HP2", 7.0, 3.0),
        _csdm_point("HP3", 7.0, 7.0),
        _csdm_point("HP4", 3.0, 7.0),
    ]
    edges = [
        _csdm_edge("OE1", "OP1", "OP2"),
        _csdm_edge("OE2", "OP2", "OP3"),
        _csdm_edge("OE3", "OP3", "OP4"),
        _csdm_edge("OE4", "OP4", "OP1"),
        # Hole ring listed in the same "increasing" order as the outer ring
        # -- the raw chase alone would land it CCW, same as the outer ring.
        _csdm_edge("HE1", "HP1", "HP2"),
        _csdm_edge("HE2", "HP2", "HP3"),
        _csdm_edge("HE3", "HP3", "HP4"),
        _csdm_edge("HE4", "HP4", "HP1"),
    ]
    parcels = [
        _csdm_parcel(
            "DonutParcel",
            ["OE1", "OE2", "OE3", "OE4"],
            ["HE1", "HE2", "HE3", "HE4"],
        )
    ]
    topology = from_csdm_json(_csdm_doc(points, edges, parcels))

    surface = topology["surfaces"][0]
    assert surface["rings"][0]["type"] == "outer"
    assert surface["rings"][1]["type"] == "inner"

    def signed_area(ring: dict[str, Any]) -> float:
        curves = {curve["id"]: curve for curve in topology["curves"]}
        points_by_id = {point["id"]: point for point in topology["points"]}
        coords = []
        for member in ring["members"]:
            vertices = curves[member["ref"]]["vertices"]
            ordered = vertices[:-1] if member["orientation"] == "+" else list(reversed(vertices))[:-1]
            coords.extend(points_by_id[v]["coordinates"] for v in ordered)
        total = 0.0
        n = len(coords)
        for i in range(n):
            x1, y1 = coords[i]
            x2, y2 = coords[(i + 1) % n]
            total += x1 * y2 - x2 * y1
        return total

    assert signed_area(surface["rings"][0]) > 0  # outer ring: CCW
    assert signed_area(surface["rings"][1]) < 0  # hole ring: CW
