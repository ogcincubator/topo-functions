"""Refusal paths, a parcel-shaped document end to end, and holes in JSON-FG."""

import ast
import json
import sys
from pathlib import Path

import pytest
from builders import (
    Builder,
    cube_doc,
    two_cubes_sharing_a_face_doc,
    units_on_a_parcel_doc,
)

from topo_encodings import (
    EncodingError,
    require_coordinates,
    to_geojson_faces,
    to_jsonfg,
    traverse,
)
from topo_encodings import cli
from topo_encodings.transform import Topo2JsonFgTransform

ENU = "east-north-up"
CRS = "http://www.opengis.net/def/crs/EPSG/0/7850"
PACKAGE = Path(__file__).resolve().parents[1] / "topo_encodings"


def transformer(e, n, h):
    return 116.0 + e * 1e-5, -32.0 + n * 1e-5, h - 31.25


def _features(doc, key):
    return [f for c in doc[key] for f in c["features"]]


def _feature(doc, key, fid):
    return next(f for f in _features(doc, key) if f["id"] == fid)


def _codes(doc):
    with pytest.raises(EncodingError) as err:
        traverse(doc)
    return err.value.codes


# A parcel-shaped document ------------------------------------------------------------


def test_units_on_a_parcel_through_both_writers():
    doc = units_on_a_parcel_doc()
    seen = []

    def recording(e, n, h):
        seen.append((e, n, h))
        return transformer(e, n, h)

    jsonfg = to_jsonfg(doc, place_axes=ENU, crs=CRS)
    faces = to_geojson_faces(doc, place_axes=ENU, transformer=recording)
    link = jsonfg["features"][0]["properties"]["relationships"][0]
    assert link["href"] == "lot-3"
    assert [f["id"] for f in jsonfg["features"]] == ["A", "B"]
    assert len(faces["features"]) == 12
    # Only the units' 3D points are transformed, never the parcel's 2D marks.
    assert len(seen) == 12 and all(len(p) == 3 for p in seen)


def test_parcel_marks_without_place_still_refuse_the_document():
    doc = units_on_a_parcel_doc()
    del _feature(doc, "points", "b1")["place"]
    with pytest.raises(EncodingError) as err:
        to_jsonfg(doc, place_axes=ENU, crs=CRS)
    assert err.value.codes == {"POINT_WITHOUT_PLACE"}


# Holes in JSON-FG -------------------------------------------------------------------


def test_a_face_with_a_hole_keeps_both_rings_in_jsonfg():
    b = Builder()
    b.add_points(
        {
            "o1": (0, 0, 0), "o2": (4, 0, 0), "o3": (4, 4, 0), "o4": (0, 4, 0),
            "h1": (1, 1, 0), "h2": (3, 1, 0), "h3": (3, 3, 0), "h4": (1, 3, 0),
        }
    )  # fmt: skip
    b.face("plate", ("o1", "o2", "o3", "o4"), ("h1", "h4", "h3", "h2"))
    b.shell("s", [("plate", "-")])
    b.solid("x", [("s", "+")])
    (feature,) = to_jsonfg(b.doc(), place_axes=ENU, crs=CRS, check_orientation=False)[
        "features"
    ]
    ((outer, hole),) = feature["place"]["coordinates"][0]
    # Used with '-': each loop reversed (from its last point), the hole still
    # opposite to the exterior.
    assert outer[:-1] == [
        [0.0, 4.0, 0.0],
        [4.0, 4.0, 0.0],
        [4.0, 0.0, 0.0],
        [0.0, 0.0, 0.0],
    ]
    assert hole[:-1] == [
        [3.0, 1.0, 0.0],
        [3.0, 3.0, 0.0],
        [1.0, 3.0, 0.0],
        [1.0, 1.0, 0.0],
    ]


# Traversal refusals -----------------------------------------------------------------


def test_require_coordinates_rejects_an_unknown_source():
    with pytest.raises(ValueError):
        require_coordinates(traverse(cube_doc()), "elevation")


def test_a_feature_without_an_id_is_refused():
    doc = cube_doc()
    del _features(doc, "faces")[0]["id"]
    assert "MISSING_ID" in _codes(doc)


def test_an_empty_solid_or_face_is_refused():
    doc = cube_doc()
    _feature(doc, "solids", "cube")["topology"]["directed_references"] = []
    assert _codes(doc) == {"EMPTY_TOPOLOGY"}
    doc = cube_doc()
    del _feature(doc, "faces", "top")["topology"]["directed_references"]
    assert _codes(doc) == {"EMPTY_TOPOLOGY"}


def test_a_ring_of_two_edges_is_refused():
    doc = cube_doc()
    ring = _features(doc, "rings")[0]
    del ring["topology"]["directed_references"][2:]
    assert _codes(doc) == {"RING_TOO_SHORT"}


@pytest.mark.parametrize(
    "references",
    ["shell", [{"orientation": "+"}], ["shell"], [{"ref": 7, "orientation": "+"}]],
)
def test_malformed_directed_references_are_refused(references):
    doc = cube_doc()
    _feature(doc, "solids", "cube")["topology"]["directed_references"] = references
    assert _codes(doc) == {"BAD_REFERENCES"}


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(faces={"type": "FeatureCollection"}),
        lambda d: d["faces"].append({"type": "FeatureCollection"}),
        lambda d: d["faces"].append("faces"),
        lambda d: d["faces"][0]["features"].append("a face"),
    ],
)
def test_malformed_collections_are_refused(change):
    doc = cube_doc()
    change(doc)
    assert "BAD_COLLECTION" in _codes(doc)


# Writers, transforms and command line -----------------------------------------------


def test_a_degenerate_shared_face_is_reported_once():
    doc = two_cubes_sharing_a_face_doc()
    for point in _features(doc, "points"):
        if point["id"] in ("ap2", "ap3", "ap6", "ap7"):
            point["place"]["coordinates"][0] = 1.0
            point["place"]["coordinates"][1] = 0.0  # all four on one vertical line
    with pytest.raises(EncodingError) as err:
        to_geojson_faces(doc, place_axes=ENU, transformer=transformer)
    shared = [p for p in err.value.problems if p.id == "aeast"]
    assert len(shared) == 1


def test_a_transform_accepts_an_already_parsed_document():
    class Host:
        input_data = cube_doc()
        metadata = {"crs": CRS, "place_axes": ENU}

    out = json.loads(Topo2JsonFgTransform().transform(Host()))
    assert out == to_jsonfg(cube_doc(), place_axes=ENU, crs=CRS)


@pytest.mark.parametrize(
    "main, argv",
    [
        (cli.main_topo2jsonfg, ["--crs", CRS, "--place-axes", ENU]),
        (
            cli.main_topo2geojson_faces,
            ["--place-axes", ENU, "--heights", "ellipsoidal-geometry"],
        ),
    ],
)
def test_console_entry_points_exit_with_the_status(main, argv, tmp_path, monkeypatch):
    doc = cube_doc()
    doc["solids"] = []
    path = tmp_path / "in.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["prog", str(path), *argv])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1


# Python 3.10 ------------------------------------------------------------------------


@pytest.mark.parametrize("path", sorted(PACKAGE.glob("*.py")), ids=lambda p: p.name)
def test_the_package_parses_as_python_3_10(path):
    """Best effort: catches syntax newer than 3.10, not newer library calls."""
    ast.parse(path.read_text(encoding="utf-8"), feature_version=(3, 10))


def test_a_document_without_a_topology_key_has_nothing_from_it():
    doc = cube_doc()
    del doc["solids"]
    assert traverse(doc).solids == ()
    doc = cube_doc()
    del doc["rings"]
    assert _codes(doc) == {"MISSING_REFERENCE"}
