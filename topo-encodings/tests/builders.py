"""Synthetic Topo Feature documents for the tests.

Solids are described by their faces as loops of point ids, each listed counter-clockwise
seen from outside the region the shell encloses (outward normals).
The builder makes edges (each pair of points once), rings, faces, shells and solids
with the directed references and orientations a Topo Feature document uses.
"""

from __future__ import annotations

import copy
from typing import Any, Optional

# A unit cube's corners and outward faces.
CUBE_POINTS = {
    "p1": (0.0, 0.0, 0.0),
    "p2": (1.0, 0.0, 0.0),
    "p3": (1.0, 1.0, 0.0),
    "p4": (0.0, 1.0, 0.0),
    "p5": (0.0, 0.0, 1.0),
    "p6": (1.0, 0.0, 1.0),
    "p7": (1.0, 1.0, 1.0),
    "p8": (0.0, 1.0, 1.0),
}
CUBE_FACES = {
    "bottom": ("p1", "p4", "p3", "p2"),
    "top": ("p5", "p6", "p7", "p8"),
    "south": ("p1", "p2", "p6", "p5"),
    "east": ("p2", "p3", "p7", "p6"),
    "north": ("p3", "p4", "p8", "p7"),
    "west": ("p4", "p1", "p5", "p8"),
}


def box(prefix: str, lo: tuple, hi: tuple) -> tuple[dict, dict]:
    """Points and outward faces of an axis-aligned box, ids prefixed."""
    points = {
        f"{prefix}{k}": tuple(lo[i] + v[i] * (hi[i] - lo[i]) for i in range(3))
        for k, v in CUBE_POINTS.items()
    }
    faces = {
        f"{prefix}{name}": tuple(f"{prefix}{p}" for p in loop)
        for name, loop in CUBE_FACES.items()
    }
    return points, faces


class Builder:
    """Accumulates points, edges, rings and faces; shells and solids reference them."""

    def __init__(self) -> None:
        self.points: dict[str, tuple] = {}
        self.edges: dict[frozenset, tuple[str, str, str]] = {}  # pair -> id, a, b
        self.rings: list[dict] = []
        self.faces: dict[str, dict] = {}
        self.shells: list[dict] = []
        self.solids: list[dict] = []

    def add_points(self, points: dict[str, tuple]) -> None:
        self.points.update(points)

    def face(self, face_id: str, *loops: tuple[str, ...]) -> str:
        """A face whose rings follow the given loops (the first is the outer one)."""
        ring_refs = []
        for index, loop in enumerate(loops):
            ring_id = f"{face_id}-r{index}"
            edge_refs = []
            for a, b in zip(loop, loop[1:] + loop[:1]):
                key = frozenset((a, b))
                if key not in self.edges:
                    self.edges[key] = (f"e-{a}-{b}", a, b)
                eid, start, _ = self.edges[key]
                edge_refs.append(_ref(eid, "+" if start == a else "-"))
            self.rings.append(_feature(ring_id, "Ring", edge_refs))
            ring_refs.append(_ref(ring_id, "+"))
        self.faces[face_id] = _feature(face_id, "Face", ring_refs)
        return face_id

    def shell(self, shell_id: str, faces: list[tuple[str, str]]) -> str:
        """A shell of (face id, orientation) pairs."""
        self.shells.append(_feature(shell_id, "Shell", [_ref(f, o) for f, o in faces]))
        return shell_id

    def solid(
        self,
        solid_id: str,
        shells: list[tuple[str, str]],
        properties: Optional[dict] = None,
        relationships: Optional[list] = None,
    ) -> str:
        feature = _feature(solid_id, "Solid", [_ref(s, o) for s, o in shells])
        feature["properties"] = properties or {"name": solid_id}
        if relationships is not None:
            feature["topology"]["relationships"] = relationships
        self.solids.append(feature)
        return solid_id

    def doc(self) -> dict[str, Any]:
        points = [
            {
                "id": pid,
                "type": "Feature",
                "geometry": None,
                "place": {"type": "Point", "coordinates": list(xyz)},
                "properties": {},
            }
            for pid, xyz in self.points.items()
        ]
        edges = [
            {
                "id": eid,
                "type": "Feature",
                "geometry": None,
                "topology": {"type": "Edge", "references": [a, b]},
            }
            for eid, a, b in self.edges.values()
        ]
        return copy.deepcopy(
            {
                "type": "FeatureCollection",
                "points": [_collection("pts", "CadastralMark", points)],
                "edges": [_collection("edg", "Edge", edges)],
                "rings": [_collection("rng", "Ring", self.rings)],
                "faces": [_collection("fac", "Face", list(self.faces.values()))],
                "shells": [_collection("shl", "Shell", self.shells)],
                "solids": [_collection("sld", "Solid", self.solids)],
            }
        )


def cube_doc() -> dict:
    b = Builder()
    points, faces = box("", (0, 0, 0), (1, 1, 1))
    b.add_points(points)
    b.shell("shell", [(b.face(f, loop), "+") for f, loop in faces.items()])
    b.solid("cube", [("shell", "+")])
    return b.doc()


def cube_with_void_doc() -> dict:
    """A 3 m cube with a 1 m void, both stored outward; the void is used with '-'."""
    b = Builder()
    outer_pts, outer_faces = box("o", (0, 0, 0), (3, 3, 3))
    void_pts, void_faces = box("v", (1, 1, 1), (2, 2, 2))
    b.add_points(outer_pts)
    b.add_points(void_pts)
    b.shell("outer", [(b.face(f, loop), "+") for f, loop in outer_faces.items()])
    b.shell("void", [(b.face(f, loop), "+") for f, loop in void_faces.items()])
    b.solid("hollow", [("outer", "+"), ("void", "-")])
    return b.doc()


def two_cubes_sharing_a_face_doc(relationships: Optional[list] = None) -> dict:
    """Cubes A (x 0..1) and B (x 1..2) share the face at x = 1.

    The shared face is stored once, outward for A (facing +x); B uses it with '-'.
    """
    b = Builder()
    a_pts, a_faces = box("a", (0, 0, 0), (1, 1, 1))
    b_pts, b_faces = box("b", (1, 0, 0), (2, 1, 1))
    # B's west corners are A's east corners.
    alias = {"bp1": "ap2", "bp4": "ap3", "bp5": "ap6", "bp8": "ap7"}
    for old in alias:
        del b_pts[old]
    b_faces = {
        name: tuple(alias.get(p, p) for p in loop)
        for name, loop in b_faces.items()
        if name != "bwest"
    }
    b.add_points(a_pts)
    b.add_points(b_pts)
    b.shell("shell-a", [(b.face(f, loop), "+") for f, loop in a_faces.items()])
    b.shell(
        "shell-b",
        [(b.face(f, loop), "+") for f, loop in b_faces.items()] + [("aeast", "-")],
    )
    b.solid("A", [("shell-a", "+")], relationships=relationships)
    b.solid("B", [("shell-b", "+")], relationships=relationships)
    return b.doc()


def signed_volume(points: dict, loops) -> float:
    """Divergence-theorem volume of closed loops (positive when they face outward)."""
    total = 0.0
    for loop in loops:
        p0 = points[loop[0]]
        for i in range(1, len(loop) - 1):
            a, b = points[loop[i]], points[loop[i + 1]]
            total += (
                p0[0] * (a[1] * b[2] - a[2] * b[1])
                - p0[1] * (a[0] * b[2] - a[2] * b[0])
                + p0[2] * (a[0] * b[1] - a[1] * b[0])
            )
    return total / 6.0


def _ref(ref: str, orientation: str) -> dict:
    return {"ref": ref, "orientation": orientation}


def _feature(fid: str, kind: str, refs: list) -> dict:
    return {
        "id": fid,
        "type": "Feature",
        "geometry": None,
        "topology": {"type": kind, "directed_references": refs},
        "properties": {},
    }


def _collection(cid: str, feature_type: str, features: list) -> dict:
    return {
        "id": cid,
        "type": "FeatureCollection",
        "featureType": feature_type,
        "features": features,
    }


def units_on_a_parcel_doc() -> dict:
    """Two units sharing a face, each linked to a parcel, shaped like the WA 3D CSDM
    examples: the parcel is a 2D polygon of observed vectors between 2D points, in a
    second collection under ``edges``, and no solid uses those points."""
    link = {
        "href": "lot-3",
        "rel": "topology",
        "role": "containingPrimaryParcel",
        "targetFeatureType": "surv:PrimaryParcel",
    }
    doc = two_cubes_sharing_a_face_doc(relationships=[link])
    corners = {
        "b1": (-1.0, -1.0),
        "b2": (3.0, -1.0),
        "b3": (3.0, 2.0),
        "b4": (-1.0, 2.0),
    }
    doc["points"].append(
        _collection(
            "boundary-marks",
            "BoundaryMark",
            [
                {
                    "id": pid,
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [116.0 + x, -32.0 + y],
                    },
                    "place": {"type": "Point", "coordinates": list(xy)},
                    "properties": {},
                }
                for pid, xy in corners.items()
                for x, y in [(xy[0] * 1e-5, xy[1] * 1e-5)]
            ],
        )
    )
    vectors = [
        ("v1", "b1", "b2"),
        ("v2", "b2", "b3"),
        ("v3", "b3", "b4"),
        ("v4", "b4", "b1"),
    ]
    doc["edges"].append(
        _collection(
            "observed",
            "surv:ObservedVector",
            [
                {
                    "id": vid,
                    "type": "Feature",
                    "geometry": None,
                    "topology": {"type": "LineString", "references": [a, b]},
                    "properties": {"vectorPurpose": "lot-boundary"},
                }
                for vid, a, b in vectors
            ],
        )
    )
    doc["parcels"] = [
        _collection(
            "parcels",
            "PrimaryParcel",
            [
                {
                    "id": "lot-3",
                    "type": "Feature",
                    "geometry": None,
                    "topology": {
                        "type": "Polygon",
                        "references": [[vid for vid, _, _ in vectors]],
                    },
                    "properties": {"parcelType": "freehold"},
                }
            ],
        )
    ]
    return doc
