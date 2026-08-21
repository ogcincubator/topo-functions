#!/usr/bin/env python3
"""
Pytest fixtures for the production topology boundary-block validator.

All fixtures produce in-memory topology dicts. A valid unit cube is the
canonical base fixture; specialised fixtures extend or combine it to cover
topology-rule behaviour.

Cube topology reference
-----------------------
Points (corners of the unit cube):

    p0=(0,0,0)  p1=(1,0,0)  p2=(1,1,0)  p3=(0,1,0)
    p4=(0,0,1)  p5=(1,0,1)  p6=(1,1,1)  p7=(0,1,1)

Curves (directed from first to second vertex):

    e0 p0→p1   e1 p1→p2   e2  p2→p3   e3  p3→p0  (bottom ring)
    e4 p4→p5   e5 p5→p6   e6  p6→p7   e7  p7→p4  (top ring)
    e8 p0→p4   e9 p1→p5   e10 p2→p6   e11 p3→p7  (verticals)

Surface rings (outward-normal convention):

    sf-bot  e3–  e2–  e1–  e0–   normal = (0, 0,−1)
    sf-top  e4+  e5+  e6+  e7+   normal = (0, 0,+1)
    sf-frt  e0+  e9+  e4–  e8–   normal = (0,−1, 0)
    sf-bak  e2+  e11+ e6–  e10–  normal = (0,+1, 0)
    sf-lft  e8+  e7–  e11– e3+   normal = (−1, 0, 0)
    sf-rgt  e1+  e10+ e5–  e9–   normal = (+1, 0, 0)
"""

import json
from pathlib import Path
from typing import Any

import pytest
from _pytest.config.argparsing import Parser
from _pytest.fixtures import FixtureRequest

from topo_validator.loader import from_csdm_json
from topo_validator.model import TopologyData

_FIXTURES_DIR = Path(__file__).parent

# ---------------------------------------------------------------------------
# Low-level builders
# ---------------------------------------------------------------------------


def _pt(pid: str, x: float, y: float, z: float) -> dict[str, Any]:
    """Build a point dict with an id and 3D coordinates."""
    return {"id": pid, "coordinates": [x, y, z]}


def _cv(cid: str, *vertex_ids: str) -> dict[str, Any]:
    """Build a curve dict from an id and vertex ids."""
    return {"id": cid, "vertices": list(vertex_ids)}


def _ring(*members: tuple[str, str]) -> dict[str, Any]:
    """Build a ring dict from (curve_id, orientation) pairs."""
    return {
        "type": "outer",
        "members": [{"ref": cid, "orientation": o} for cid, o in members],
    }


def _sf(sid: str, *rings: dict[str, Any]) -> dict[str, Any]:
    """Build a surface dict with an id and ring list."""
    return {"id": sid, "rings": list(rings)}


def _solid(
    sid: str,
    face_ids: list[str],
    volume: float,
    theme: str = "default",
    parcel_type: str = "primary",
    parent_id: str | None = None,
    servient_id: str | None = None,
    burdened_id: str | None = None,
    host_id: str | None = None,
    levels: list[str] | None = None,
    shells: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a solid dict with shells, faces, and metadata."""
    if shells is None:
        shells = [{"type": "outer", "faces": face_ids, "face_orientations": {}}]
    return {
        "id": sid,
        "shells": shells,
        "faces": face_ids,
        "face_orientations": {},
        "volume": volume,
        "theme": theme,
        "parcel_type": parcel_type,
        "parent_id": parent_id,
        "servient_id": servient_id,
        "burdened_id": burdened_id,
        "host_id": host_id,
        "levels": levels or [],
    }


# ---------------------------------------------------------------------------
# Cube factory
# ---------------------------------------------------------------------------


def cube_data(
    prefix: str,
    x0: float,
    y0: float,
    z0: float,
    x1: float,
    y1: float,
    z1: float,
    theme: str = "default",
    parcel_type: str = "primary",
    parent_id: str | None = None,
    volume: float | None = None,
    servient_id: str | None = None,
    burdened_id: str | None = None,
    host_id: str | None = None,
    levels: list[str] | None = None,
) -> dict[str, Any]:
    """Return a topology dict for an axis-aligned box from (x0,y0,z0) to (x1,y1,z1).

    All ids are prefixed with *prefix* so multiple cubes can coexist in one
    dataset without id clashes.  The topology reproduces the unit-cube
    convention described in the module docstring, scaled/translated to the
    requested extents.

    Args:
        prefix: String prepended to every object id to avoid collisions.
        x0: Minimum x coordinate.
        y0: Minimum y coordinate.
        z0: Minimum z coordinate.
        x1: Maximum x coordinate.
        y1: Maximum y coordinate.
        z1: Maximum z coordinate.
        theme: Solid theme label (e.g. "parcels").
        parcel_type: Parcel classification (e.g. "primary", "child").
        parent_id: Id of the enclosing parent solid, or None.
        volume: Override volume; computed from extents when None.
        servient_id: Id of the servient parcel (easements only).
        burdened_id: Canonical id of the burdened parcel, or None.
        host_id: Id of the host parcel (thematic solids only).
        levels: Building storey identifiers, or None for an empty list.

    Returns:
        A topology dict with points, curves, surfaces, and
        solids collections.
    """

    def p(i: int) -> str:
        """Return a prefixed point id."""
        return f"{prefix}p{i}"

    def e(i: int) -> str:
        """Return a prefixed curve id."""
        return f"{prefix}e{i}"

    def s(n: str) -> str:
        """Return a prefixed surface id."""
        return f"{prefix}sf-{n}"

    points = [
        _pt(p(0), x0, y0, z0),
        _pt(p(1), x1, y0, z0),
        _pt(p(2), x1, y1, z0),
        _pt(p(3), x0, y1, z0),
        _pt(p(4), x0, y0, z1),
        _pt(p(5), x1, y0, z1),
        _pt(p(6), x1, y1, z1),
        _pt(p(7), x0, y1, z1),
    ]
    curves = [
        _cv(e(0), p(0), p(1)),
        _cv(e(1), p(1), p(2)),
        _cv(e(2), p(2), p(3)),
        _cv(e(3), p(3), p(0)),
        _cv(e(4), p(4), p(5)),
        _cv(e(5), p(5), p(6)),
        _cv(e(6), p(6), p(7)),
        _cv(e(7), p(7), p(4)),
        _cv(e(8), p(0), p(4)),
        _cv(e(9), p(1), p(5)),
        _cv(e(10), p(2), p(6)),
        _cv(e(11), p(3), p(7)),
    ]
    surfaces = [
        _sf(s("bot"), _ring((e(3), "-"), (e(2), "-"), (e(1), "-"), (e(0), "-"))),
        _sf(s("top"), _ring((e(4), "+"), (e(5), "+"), (e(6), "+"), (e(7), "+"))),
        _sf(s("frt"), _ring((e(0), "+"), (e(9), "+"), (e(4), "-"), (e(8), "-"))),
        _sf(s("bak"), _ring((e(2), "+"), (e(11), "+"), (e(6), "-"), (e(10), "-"))),
        _sf(s("lft"), _ring((e(8), "+"), (e(7), "-"), (e(11), "-"), (e(3), "+"))),
        _sf(s("rgt"), _ring((e(1), "+"), (e(10), "+"), (e(5), "-"), (e(9), "-"))),
    ]
    if volume is None:
        volume = abs((x1 - x0) * (y1 - y0) * (z1 - z0))
    face_ids = [s("bot"), s("top"), s("frt"), s("bak"), s("lft"), s("rgt")]
    solid = _solid(
        f"{prefix}sol",
        face_ids,
        volume=volume,
        theme=theme,
        parcel_type=parcel_type,
        parent_id=parent_id,
        servient_id=servient_id,
        burdened_id=burdened_id,
        host_id=host_id,
        levels=levels,
    )
    return {
        "points": points,
        "curves": curves,
        "surfaces": surfaces,
        "solids": [solid],
    }


def _deduplicate_points(
    data: dict[str, Any],
    tol: float = 1e-9,
) -> dict[str, Any]:
    """
    Merge geometrically coincident points and remap all curve vertex references.

    When two cubes share a boundary, each builder assigns distinct ids to
    what is physically the same cadastral point.  This helper collapses
    those into a single canonical point (the first one encountered) so
    that TR-01 (unique points) does not fire on legitimately shared
    boundary nodes.
    """
    # Build canonical id map: each point id → id of the first point at same location
    canonical: dict[str, str] = {}
    unique_pts: list[dict[str, Any]] = []

    for pt in data["points"]:
        coords = pt["coordinates"]
        match = next(
            (
                u
                for u in unique_pts
                if all(abs(u["coordinates"][i] - coords[i]) < tol for i in range(3))
            ),
            None,
        )
        if match:
            canonical[pt["id"]] = match["id"]
        else:
            canonical[pt["id"]] = pt["id"]
            unique_pts.append(pt)

    # Remap vertex references in every curve
    new_curves = [
        {**cv, "vertices": [canonical.get(v, v) for v in cv["vertices"]]}
        for cv in data["curves"]
    ]

    return {**data, "points": unique_pts, "curves": new_curves}


def _deduplicate_curves(data: dict[str, Any]) -> dict[str, Any]:
    """
    Merge geometrically coincident curves and remap all surface ring member refs.

    After point deduplication two cubes sharing a boundary face will have
    separate curve objects that connect the *same* canonical points.  This
    helper canonicalises those to a single curve (the first one encountered)
    and fixes ring member orientations so that the outward-normal convention
    is preserved: if the canonical curve goes va→vb but the duplicate went
    vb→va (reverse direction), every ring member that used the duplicate with
    orientation "+" is remapped to the canonical with orientation "-".
    """
    seen: dict[tuple, str] = {}  # normalised vertex key → canonical curve id
    reversed_of: dict[str, bool] = {}  # curve_id → True when direction is reversed
    canonical_id: dict[str, str] = {}  # curve_id → canonical curve_id
    unique_curves: list[dict[str, Any]] = []

    for cv in data["curves"]:
        verts = tuple(cv["vertices"])
        fwd = verts
        rev = verts[::-1]
        if fwd in seen:
            canonical_id[cv["id"]] = seen[fwd]
            reversed_of[cv["id"]] = False
        elif rev in seen:
            canonical_id[cv["id"]] = seen[rev]
            reversed_of[cv["id"]] = True  # duplicate is the reverse
        else:
            canonical_id[cv["id"]] = cv["id"]
            reversed_of[cv["id"]] = False
            seen[fwd] = cv["id"]
            unique_curves.append(cv)

    def _flip(o: str) -> str:
        """Flip a ring orientation sign."""
        return "-" if o == "+" else "+"

    new_surfaces = []
    for sf in data["surfaces"]:
        new_rings = []
        for ring in sf.get("rings", []):
            new_members = []
            for m in ring.get("members", []):
                cid = m["ref"]
                canon = canonical_id.get(cid, cid)
                orig = m["orientation"]
                orient = _flip(orig) if reversed_of.get(cid) else orig
                new_members.append({"ref": canon, "orientation": orient})
            new_rings.append({**ring, "members": new_members})
        new_surfaces.append({**sf, "rings": new_rings})

    return {**data, "curves": unique_curves, "surfaces": new_surfaces}


def _deduplicate_surfaces(data: dict[str, Any]) -> dict[str, Any]:
    """
    Merge surfaces that reference the same set of curves and remap solid face refs.
    """
    seen: dict[frozenset, str] = {}
    canonical_id: dict[str, str] = {}
    unique_surfaces: list[dict[str, Any]] = []

    for sf in data["surfaces"]:
        key: frozenset = frozenset(
            m["ref"] for ring in sf.get("rings", []) for m in ring.get("members", [])
        )
        if key in seen:
            canonical_id[sf["id"]] = seen[key]
        else:
            canonical_id[sf["id"]] = sf["id"]
            seen[key] = sf["id"]
            unique_surfaces.append(sf)

    def _remap_unique_face_ids(face_ids: list[Any]) -> list[str]:
        """Return canonical face ids in the original order, with duplicates removed."""
        remapped: list[str] = []
        seen_faces: set[str] = set()

        for face_id in face_ids:
            if not isinstance(face_id, str):
                continue

            canonical_face_id = canonical_id.get(face_id, face_id)
            if canonical_face_id not in seen_faces:
                remapped.append(canonical_face_id)
                seen_faces.add(canonical_face_id)

        return remapped

    def _remap_face_orientations(value: Any) -> dict[str, str]:
        """Return face orientations keyed by canonical face id."""
        if not isinstance(value, dict):
            return {}

        remapped: dict[str, str] = {}
        for face_id, orientation in value.items():
            if not isinstance(face_id, str):
                continue

            canonical_face_id = canonical_id.get(face_id, face_id)
            remapped.setdefault(canonical_face_id, orientation)

        return remapped

    new_solids = []
    for solid in data["solids"]:
        new_solid: dict[str, Any] = {
            **solid,
            "faces": _remap_unique_face_ids(solid.get("faces", [])),
            "face_orientations": _remap_face_orientations(
                solid.get("face_orientations", {})
            ),
        }

        new_shells: list[dict[str, Any]] = [
            {
                **shell,
                "faces": _remap_unique_face_ids(shell.get("faces", [])),
                "face_orientations": _remap_face_orientations(
                    shell.get("face_orientations", {})
                ),
            }
            for shell in solid.get("shells", [])
            if isinstance(shell, dict)
        ]

        new_solid["shells"] = new_shells

        new_solids.append(new_solid)

    return {**data, "surfaces": unique_surfaces, "solids": new_solids}


def merge_datasets(*datasets: dict[str, Any]) -> dict[str, Any]:
    """Combine multiple topology dicts into one, deduplicating coincident geometry.

    Points at identical coordinates (within 1e-9) are merged into a single
    canonical point.  Curves that connect the same canonical points are then
    also merged into a single canonical curve with ring orientations adjusted.
    Finally, surfaces that reference the same set of canonical curves are merged
    into a single canonical surface with solid face lists updated accordingly.
    This models the CSDM requirement that shared boundary elements must be
    the same cadastral objects in the dataset.

    Args:
        *datasets: Two or more topology dicts (each with "points",
            "curves", "surfaces", and "solids" lists) to merge.

    Returns:
        A single merged topology dict with deduplicated geometry.
    """
    result: dict[str, Any] = {"points": [], "curves": [], "surfaces": [], "solids": []}
    for ds in datasets:
        for key in result:
            result[key] += ds.get(key, [])
    result = _deduplicate_points(result)
    result = _deduplicate_curves(result)
    return _deduplicate_surfaces(result)


def _reverse_surface_winding(
    surfaces: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return copies of *surfaces* with every ring winding reversed.

    Reversal = reverse the member list, then flip each orientation.  This
    negates the signed volume, so outward-normal surfaces (sv > 0) become
    inward-normal surfaces (sv < 0), which is the correct convention for
    inner-shell (void) faces required by TR-25.
    """
    result = []
    for sf in surfaces:
        new_rings = []
        for ring in sf.get("rings", []):
            new_members = [
                {
                    "ref": m["ref"],
                    "orientation": "-" if m["orientation"] == "+" else "+",
                }
                for m in reversed(ring["members"])
            ]
            new_rings.append({**ring, "members": new_members})
        result.append({**sf, "rings": new_rings})
    return result


def hollow_cube_data() -> dict[str, Any]:
    """Return a topology dict for a 2×2×2 outer solid with a 1×1×1 inner void.

    The outer shell is built with "cube_data" (outward-facing normals,
    signed volume ≈ +8).  The inner void shell is the same function applied
    to a 1×1×1 box then **reversed** so its normals point inward toward the
    void (signed volume ≈ −1), which is the correct TR-25 convention.

    The two shells share no points, curves, or surfaces, so no deduplication
    step is required.
    """
    outer = cube_data("out-", 0, 0, 0, 2, 2, 2)
    inner_raw = cube_data("inn-", 0.5, 0.5, 0.5, 1.5, 1.5, 1.5)

    # Reverse inner cube winding → inward-facing normals (sv = −1)
    inner_surfaces = _reverse_surface_winding(inner_raw["surfaces"])

    outer_face_ids = [sf["id"] for sf in outer["surfaces"]]
    inner_face_ids = [sf["id"] for sf in inner_surfaces]

    shells = [
        {"type": "outer", "faces": outer_face_ids, "face_orientations": {}},
        {"type": "inner", "faces": inner_face_ids, "face_orientations": {}},
    ]
    solid = {
        "id": "hollow-sol",
        "shells": shells,
        "faces": outer_face_ids + inner_face_ids,
        "face_orientations": {},
        "volume": 7.0,  # 2³ − 1³ = 7
        "theme": "default",
        "parcel_type": "primary",
        "parent_id": None,
        "servient_id": None,
        "host_id": None,
        "levels": [],
    }
    return {
        "points": outer["points"] + inner_raw["points"],
        "curves": outer["curves"] + inner_raw["curves"],
        "surfaces": outer["surfaces"] + inner_surfaces,
        "solids": [solid],
    }


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def unit_cube() -> dict[str, Any]:
    """Valid 1×1×1 cube topology — the base fixture for most TR tests."""
    return cube_data("", 0, 0, 0, 1, 1, 1)


@pytest.fixture
def two_adjacent_cubes() -> dict[str, Any]:
    """
    Two non-overlapping unit cubes side-by-side along the X axis.

    Cube A occupies x=[0,1], cube B occupies x=[1,2].  Their bounding
    boxes touch at the x=1 plane but do not strictly overlap.  Both
    solids share the same theme, so the no-overlap rule applies.
    """
    a = cube_data("a-", 0, 0, 0, 1, 1, 1, theme="parcels")
    b = cube_data("b-", 1, 0, 0, 2, 1, 1, theme="parcels")
    return merge_datasets(a, b)


@pytest.fixture
def nested_cubes() -> dict[str, Any]:
    """
    A 2×2×2 parent cube containing a 1×1×1 child cube.

    The child solid declares the parent's solid id as its parent_id so
    TR-09 (parent containment) and the TR-08 overlap exemption both apply.
    """
    parent = cube_data(
        "par-",
        0,
        0,
        0,
        2,
        2,
        2,
        theme="parcels",
        parcel_type="primary")
    child = cube_data(
        "chd-",
        0,
        0,
        0,
        1,
        1,
        1,
        theme="parcels",
        parcel_type="child",
        parent_id="par-sol",
    )
    return merge_datasets(parent, child)


@pytest.fixture
def hollow_cube() -> dict[str, Any]:
    """2×2×2 outer shell with a correctly oriented 1×1×1 inner void."""
    return hollow_cube_data()


# ---------------------------------------------------------------------------
# JSON fixture support  (mirrors the geometry test suite pattern)
# ---------------------------------------------------------------------------


def pytest_addoption(parser: Parser) -> None:
    """Add the "--fixture" command-line option for selecting a JSON fixture file."""
    parser.addoption(
        "--fixture",
        default="tetrahedron.json",
        help="Fixture filename to validate (default: tetrahedron.json)",
    )


@pytest.fixture
def fixture_file(request: FixtureRequest) -> str:
    """Return the value of the "--fixture" CLI option."""
    return request.config.getoption("--fixture")


@pytest.fixture
def fixture_data(fixture_file: str) -> TopologyData:
    """Load a package-local JSON fixture and convert it to topology data.

    The fixture file is resolved relative to this package-local test directory,
    beside this conftest module.

    Args:
        fixture_file: JSON fixture filename selected by the "--fixture" option.

    Returns:
        Topology data converted by "topo_bblock_validator.loader.from_csdm_json".
    """
    fixture_path = _FIXTURES_DIR / fixture_file
    with fixture_path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)
    return from_csdm_json(raw)
