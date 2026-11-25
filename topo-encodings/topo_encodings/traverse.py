"""A strict walk of a Topo Feature document's solids, shells, faces and rings.

The walk gives each solid its shells, each shell its faces and each face its rings as
loops of point ids, in the orientation the face has *in that solid*: the ring's
orientation in its face, the face's in its shell and the shell's in its solid all
apply.
A void shell referenced with ``-`` is therefore reversed, as ``topo-solid`` specifies
(its faces then point into the void), and a face shared by two solids is given once per
solid, in each solid's orientation.

The walk is strict: every reference reachable from a solid must resolve, every ring
must be a connected, closed loop of straight edges, and ids must be unique.
All problems are collected and raised together as an :class:`EncodingError`; nothing
is skipped or repaired, so a defect in the source is either refused or carried into
the encodings unchanged.

Conventions read from the Topo Feature encoding:

- each of ``points``, ``edges``, ``rings``, ``faces``, ``shells`` and ``solids`` is a
  list of FeatureCollections, all of whose features are read (``edges`` may also hold
  features that are not edges, such as observed vectors; they are only checked when a
  ring uses them);
- an edge's ``topology.references`` is ``[start, end]``; rings, faces, shells and
  solids hold ``topology.directed_references`` of ``{"ref", "orientation"}``, where a
  missing orientation means ``+``;
- the first ring of a face is its outer boundary, the others are holes;
- the first shell of a solid is its exterior, the others are voids.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from .diagnostics import EncodingError, Problem

SIGN = {"+": 1, "-": -1}
COLLECTIONS = ("points", "edges", "rings", "faces", "shells", "solids")

Coordinates = tuple[float, ...]


@dataclass(frozen=True)
class Point:
    id: str
    place: Optional[Coordinates]
    geometry: Optional[Coordinates]
    properties: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FaceUse:
    """A face as used in a solid: ``sign`` is the face's orientation in its shell
    times the shell's in the solid; each loop is a ring's point ids, not repeated at
    the end, traversed in that orientation."""

    id: str
    sign: int
    loops: tuple[tuple[str, ...], ...]
    properties: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ShellView:
    id: str
    sign: int
    role: str  # "exterior" or "void"
    faces: tuple[FaceUse, ...]


@dataclass(frozen=True)
class SolidView:
    id: str
    shells: tuple[ShellView, ...]
    properties: Mapping[str, Any] = field(default_factory=dict)
    relationships: Optional[tuple[Mapping[str, Any], ...]] = None

    def point_ids(self) -> list[str]:
        """Every point id the solid's loops use, in traversal order, with repeats."""
        return [
            p
            for shell in self.shells
            for face in shell.faces
            for loop in face.loops
            for p in loop
        ]


@dataclass(frozen=True)
class Traversal:
    points: Mapping[str, Point]
    solids: tuple[SolidView, ...]


def traverse(doc: Mapping[str, Any]) -> Traversal:
    """Walk every solid in ``doc``; raise :class:`EncodingError` with all problems."""
    if not isinstance(doc, Mapping):
        raise EncodingError(
            [Problem("NOT_A_DOCUMENT", "the input is not a JSON object")]
        )
    walker = _Walker(doc)
    solids = tuple(walker.solid(fid) for fid in walker.features["solids"])
    if walker.problems:
        raise EncodingError(walker.problems)
    return Traversal(points=walker.points, solids=solids)


def solids(doc: Mapping[str, Any]) -> list[SolidView]:
    """The document's solids (see :func:`traverse`)."""
    return list(traverse(doc).solids)


def require_coordinates(traversal: Traversal, source: str) -> None:
    """Refuse a document unless every point has ``source`` coordinates, and every
    point a solid uses has 3D ones.

    ``source`` is ``"place"`` or ``"geometry"``.
    Requiring the member on every point, not only those the solids use, keeps one
    coordinate source per document, so coordinate reference systems are never mixed;
    points the solids do not use (such as a parcel's 2D boundary) may be 2D.
    """
    if source not in ("place", "geometry"):
        raise ValueError(f"unknown coordinate source {source!r}")
    used = {p for solid in traversal.solids for p in solid.point_ids()}
    problems = []
    for pid, point in traversal.points.items():
        coordinates = getattr(point, source)
        if coordinates is None:
            problems.append(
                Problem(
                    f"POINT_WITHOUT_{source.upper()}", f"point has no {source}", pid
                )
            )
        elif pid in used and len(coordinates) != 3:
            problems.append(
                Problem(
                    "POINT_NOT_3D",
                    f"a solid uses this point, whose {source} is not 3D",
                    pid,
                )
            )
    if problems:
        raise EncodingError(problems)


class _Walker:
    def __init__(self, doc: Mapping[str, Any]):
        self.problems: list[Problem] = []
        self.features: dict[str, dict[str, Mapping[str, Any]]] = {
            key: {} for key in COLLECTIONS
        }
        seen: set[str] = set()
        for key in COLLECTIONS:
            for feature in _features(doc, key, self.problems):
                fid = feature.get("id")
                if not isinstance(fid, str) or not fid:
                    self.problems.append(
                        Problem("MISSING_ID", f"a feature in {key} has no id")
                    )
                    continue
                if fid in seen:
                    self.problems.append(
                        Problem("DUPLICATE_ID", "id used more than once", fid)
                    )
                    continue
                seen.add(fid)
                self.features[key][fid] = feature
        self.points = {
            fid: _point(fid, feature, self.problems)
            for fid, feature in self.features["points"].items()
        }
        self._edges: dict[str, Optional[tuple[str, str]]] = {}
        self._loops: dict[str, Optional[tuple[str, ...]]] = {}

    def solid(self, fid: str) -> SolidView:
        feature = self.features["solids"][fid]
        topology = feature.get("topology") or {}
        refs, complete = self._directed(fid, feature, "shells")
        if complete and not refs:
            self._problem("EMPTY_TOPOLOGY", "solid has no shells", fid)
        shells = tuple(
            self._shell(ref, sign, "exterior" if index == 0 else "void")
            for index, (ref, sign) in enumerate(refs)
        )
        relationships = topology.get("relationships")
        return SolidView(
            id=fid,
            shells=shells,
            properties=feature.get("properties") or {},
            relationships=(
                tuple(relationships) if isinstance(relationships, list) else None
            ),
        )

    def _shell(self, fid: str, shell_sign: int, role: str) -> ShellView:
        refs, complete = self._directed(fid, self.features["shells"][fid], "faces")
        if complete and not refs:
            self._problem("EMPTY_TOPOLOGY", "shell has no faces", fid)
        faces = tuple(self._face(ref, shell_sign * sign) for ref, sign in refs)
        return ShellView(id=fid, sign=shell_sign, role=role, faces=faces)

    def _face(self, fid: str, sign: int) -> FaceUse:
        feature = self.features["faces"][fid]
        refs, complete = self._directed(fid, feature, "rings")
        if complete and not refs:
            self._problem("EMPTY_TOPOLOGY", "face has no rings", fid)
        loops = []
        for ring_id, ring_sign in refs:
            loop = self._loop(ring_id)
            if loop is not None:
                loops.append(loop if sign * ring_sign > 0 else loop[::-1])
        return FaceUse(
            id=fid,
            sign=sign,
            loops=tuple(loops),
            properties=feature.get("properties") or {},
        )

    def _loop(self, fid: str) -> Optional[tuple[str, ...]]:
        """A ring's point ids in its own orientation (computed once per ring)."""
        if fid in self._loops:
            return self._loops[fid]
        refs, complete = self._directed(fid, self.features["rings"][fid], "edges")
        directed = []
        for edge_id, sign in refs:
            ends = self._edge(edge_id)
            if ends is None:
                continue
            directed.append(ends if sign > 0 else (ends[1], ends[0]))
        loop: Optional[tuple[str, ...]] = None
        if not complete or len(directed) != len(refs):
            pass  # problems already recorded for the edges
        elif len(directed) < 3:
            self._problem("RING_TOO_SHORT", f"ring has {len(directed)} edges", fid)
        elif any(
            directed[i][1] != directed[i + 1][0] for i in range(len(directed) - 1)
        ):
            self._problem(
                "RING_NOT_CONNECTED", "consecutive edges do not share a point", fid
            )
        elif directed[-1][1] != directed[0][0]:
            self._problem("RING_NOT_CLOSED", "last edge does not end at the first", fid)
        else:
            loop = tuple(start for start, _ in directed)
        self._loops[fid] = loop
        return loop

    def _edge(self, fid: str) -> Optional[tuple[str, str]]:
        if fid in self._edges:
            return self._edges[fid]
        refs = (self.features["edges"][fid].get("topology") or {}).get("references")
        ends: Optional[tuple[str, str]] = None
        if not isinstance(refs, list) or len(refs) != 2:
            count = len(refs) if isinstance(refs, list) else 0
            self._problem(
                "EDGE_NOT_STRAIGHT",
                f"edge has {count} point references; only [start, end] is supported",
                fid,
            )
        else:
            missing = [p for p in refs if p not in self.points]
            for p in missing:
                self._problem(
                    "MISSING_REFERENCE", f"edge references missing point {p}", fid
                )
            if not missing:
                ends = (refs[0], refs[1])
        self._edges[fid] = ends
        return ends

    def _directed(
        self, fid: str, feature: Mapping[str, Any], target: str
    ) -> tuple[list[tuple[str, int]], bool]:
        """The resolved directed references of a feature to the ``target`` collection;
        unresolved ones are recorded as problems and left out, and ``complete`` is False
        so that callers do not report consequences of the same problem."""
        refs = (feature.get("topology") or {}).get("directed_references")
        if refs is None:
            refs = []
        if not isinstance(refs, list):
            self._problem("BAD_REFERENCES", "directed_references is not a list", fid)
            return [], False
        out = []
        complete = True
        for ref in refs:
            target_id = ref.get("ref") if isinstance(ref, Mapping) else None
            orientation = (
                ref.get("orientation", "+") if isinstance(ref, Mapping) else None
            )
            if not isinstance(target_id, str):
                self._problem("BAD_REFERENCES", "a directed reference has no ref", fid)
                complete = False
            elif orientation not in SIGN:
                self._problem(
                    "BAD_ORIENTATION",
                    f"orientation {orientation!r} of {target_id} is not '+' or '-'",
                    fid,
                )
                complete = False
            elif target_id not in self.features[target]:
                self._problem(
                    "MISSING_REFERENCE",
                    f"references {target_id}, which is not in {target}",
                    fid,
                )
                complete = False
            else:
                out.append((target_id, SIGN[orientation]))
        return out, complete

    def _problem(self, code: str, message: str, fid: str) -> None:
        problem = Problem(code, message, fid)
        if problem not in self.problems:  # a shared ring or face is reached twice
            self.problems.append(problem)


def _features(
    doc: Mapping[str, Any], key: str, problems: list[Problem]
) -> list[Mapping[str, Any]]:
    collections = doc.get(key)
    if collections is None:
        return []
    if not isinstance(collections, list):
        problems.append(
            Problem("BAD_COLLECTION", f"{key} is not a list of FeatureCollections")
        )
        return []
    out = []
    for collection in collections:
        features = (
            collection.get("features") if isinstance(collection, Mapping) else None
        )
        if not isinstance(features, list):
            problems.append(
                Problem("BAD_COLLECTION", f"a collection in {key} has no features list")
            )
            continue
        for feature in features:
            if isinstance(feature, Mapping):
                out.append(feature)
            else:
                problems.append(
                    Problem("BAD_COLLECTION", f"a feature in {key} is not an object")
                )
    return out


def _point(fid: str, feature: Mapping[str, Any], problems: list[Problem]) -> Point:
    return Point(
        id=fid,
        place=_coordinates(fid, feature.get("place"), "place", problems),
        geometry=_coordinates(fid, feature.get("geometry"), "geometry", problems),
        properties=feature.get("properties") or {},
    )


def _coordinates(
    fid: str, member: Any, name: str, problems: list[Problem]
) -> Optional[Coordinates]:
    """2D or 3D coordinates of a point's ``place`` or ``geometry``; None when absent.

    A member that is present but is not a 2D or 3D ``Point`` is a problem, not an
    absence; whether 3D is needed is decided by :func:`require_coordinates`.
    """
    if member is None:
        return None
    coordinates = member.get("coordinates") if isinstance(member, Mapping) else None
    if (
        not isinstance(member, Mapping)
        or member.get("type") != "Point"
        or not isinstance(coordinates, list)
        or len(coordinates) not in (2, 3)
        or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in coordinates
        )
    ):
        problems.append(Problem("BAD_POINT", f"{name} is not a 2D or 3D Point", fid))
        return None
    return tuple(float(v) for v in coordinates)
