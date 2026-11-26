import json
from types import SimpleNamespace

import pytest
from builders import (
    Builder,
    box,
    cube_doc,
    cube_with_void_doc,
    two_cubes_sharing_a_face_doc,
    units_on_a_parcel_doc,
)

from topo_encodings import EncodingError, to_geojson_faces, to_jsonfg
from topo_encodings.cli import topo2jsonfg
from topo_encodings.orientation import (
    PLACE_AXES,
    axes,
    check_orientation,
    shell_volume,
)
from topo_encodings.transform import Topo2JsonFgTransform
from topo_encodings.traverse import traverse

CRS = "http://www.opengis.net/def/crs/EPSG/0/7850"
NZTM = "http://www.opengis.net/def/crs/EPSG/0/2193"  # north, east
ENU = "east-north-up"
NEU = "north-east-up"


def _codes(doc, place_axes=ENU):
    with pytest.raises(EncodingError) as err:
        check_orientation(traverse(doc), axes(place_axes))
    return err.value


def _north_east(doc):
    """The same document with place in north-east-up order (as NZTM is), the rings
    unchanged: the same physical solid."""
    for collection in doc["points"]:
        for point in collection["features"]:
            e, n, *rest = point["place"]["coordinates"]
            point["place"]["coordinates"] = [n, e, *rest]
    return doc


def _void_reversed_twice():
    """The void's faces used with '-' in its shell and the shell with '-' in the
    solid, so its normals point into the material (as topo-feature#12 reports)."""
    b = Builder()
    outer_pts, outer_faces = box("o", (0, 0, 0), (3, 3, 3))
    void_pts, void_faces = box("v", (1, 1, 1), (2, 2, 2))
    b.add_points(outer_pts)
    b.add_points(void_pts)
    b.shell("outer", [(b.face(f, loop), "+") for f, loop in outer_faces.items()])
    b.shell("void", [(b.face(f, loop), "-") for f, loop in void_faces.items()])
    b.solid("hollow", [("outer", "+"), ("void", "-")])
    return b.doc()


def _inside_out_cube():
    b = Builder()
    pts, faces = box("c", (0, 0, 0), (1, 1, 1))
    b.add_points(pts)
    b.shell("s", [(b.face(f, loop), "+") for f, loop in faces.items()])
    b.solid("x", [("s", "-")])
    return b


def test_there_are_eight_axis_orders():
    assert len(PLACE_AXES) == 8
    assert {"east-north-up", "north-east-up"} <= set(PLACE_AXES)


@pytest.mark.parametrize(
    "doc",
    [cube_doc(), cube_with_void_doc(), two_cubes_sharing_a_face_doc()],
    ids=["cube", "cube-with-void", "shared-face"],
)
def test_outward_solids_pass(doc):
    check_orientation(traverse(doc), axes(ENU))


def test_units_on_a_parcel_pass():
    check_orientation(traverse(units_on_a_parcel_doc()), axes(ENU))


def test_the_same_solid_in_north_east_place_passes_and_keeps_its_rings():
    doc = _north_east(cube_with_void_doc())
    check_orientation(traverse(doc), axes(NEU))
    out = to_jsonfg(doc, crs=NZTM, place_axes=NEU)
    expected = to_jsonfg(cube_with_void_doc(), crs=CRS, place_axes=ENU)
    swapped = [
        [[[[n, e, h] for e, n, h in ring] for ring in face] for face in shell]
        for shell in expected["features"][0]["place"]["coordinates"]
    ]
    assert out["features"][0]["place"]["coordinates"] == swapped


@pytest.mark.parametrize("place_axes", [NEU, "south-west-up"])
def test_a_wrongly_stated_axis_order_turns_every_shell_inside_out(place_axes):
    err = _codes(cube_with_void_doc(), place_axes)
    assert [(p.code, p.id) for p in err.problems] == [
        ("SHELL_INSIDE_OUT", "outer"),
        ("SHELL_INSIDE_OUT", "void"),
        ("AXIS_ORDER_SUSPECTED", None),
    ]
    assert "axis order" in err.problems[-1].message


def test_an_exterior_shell_used_inside_out_is_refused():
    err = _codes(_inside_out_cube().doc())
    # Every shell inside out reads the same as a wrong axis order, so both are said.
    assert err.codes == {"SHELL_INSIDE_OUT", "AXIS_ORDER_SUSPECTED"}
    assert "into the solid" in err.problems[0].message


def test_a_shell_used_by_two_solids_is_reported_once():
    b = _inside_out_cube()
    b.solid("y", [("s", "-")])
    assert [p.id for p in _codes(b.doc()).problems] == ["s", None]


def test_a_void_reversed_twice_is_refused_by_both_writers():
    doc = _void_reversed_twice()
    err = _codes(doc)
    assert [(p.code, p.id) for p in err.problems] == [("SHELL_INSIDE_OUT", "void")]
    assert "into the material" in err.problems[0].message
    with pytest.raises(EncodingError):
        to_jsonfg(doc, crs=CRS, place_axes=ENU)
    with pytest.raises(EncodingError):
        to_geojson_faces(doc, place_axes=ENU, transformer=lambda e, n, h: (e, n, h))


def test_the_switch_encodes_a_defect_as_it_is():
    doc = _void_reversed_twice()
    out = to_jsonfg(doc, crs=CRS, place_axes=ENU, check_orientation=False)
    assert len(out["features"][0]["place"]["coordinates"]) == 2
    faces = to_geojson_faces(
        doc,
        place_axes=ENU,
        transformer=lambda e, n, h: (e, n, h),
        check_orientation=False,
    )
    void_top = next(
        f for f in faces["features"] if f["properties"]["shell"] == "void"
        and f["id"] == "vtop"
    )  # fmt: skip
    assert void_top["properties"]["outwardNormal"] == [0.0, 0.0, 1.0]


def test_a_flat_shell_has_no_volume():
    b = Builder()
    b.add_points({"p1": (0, 0, 0), "p2": (1, 0, 0), "p3": (1, 1, 0), "p4": (0, 1, 0)})
    b.face("up", ("p1", "p2", "p3", "p4"))
    b.face("down", ("p4", "p3", "p2", "p1"))
    b.shell("s", [("up", "+"), ("down", "+")])
    b.solid("x", [("s", "+")])
    err = _codes(b.doc())
    assert [(p.code, p.id) for p in err.problems] == [("SHELL_WITHOUT_VOLUME", "s")]


def test_shell_volume_keeps_precision_on_projected_coordinates():
    b = Builder()
    pts, faces = box("c", (400000.0, 6400000.0, 20.0), (400001.0, 6400001.0, 21.0))
    b.add_points(pts)
    b.shell("s", [(b.face(f, loop), "+") for f, loop in faces.items()])
    b.solid("x", [("s", "+")])
    (solid,) = traverse(b.doc()).solids
    points = traverse(b.doc()).points
    loops = [[points[p].place for p in loop] for f in solid.shells[0].faces
             for loop in f.loops]  # fmt: skip
    volume, extent = shell_volume(loops)
    assert volume == pytest.approx(1.0, abs=1e-9)
    assert extent == pytest.approx(1.0)


def test_the_transform_always_checks():
    host = SimpleNamespace(
        input_data=json.dumps(_void_reversed_twice()),
        metadata={"crs": CRS, "place_axes": ENU},
    )
    with pytest.raises(EncodingError):
        Topo2JsonFgTransform().transform(host)


def test_the_cli_switch(tmp_path, capsys):
    path = tmp_path / "in.json"
    path.write_text(json.dumps(_void_reversed_twice()), encoding="utf-8")
    args = [str(path), "--crs", CRS, "--place-axes", ENU]
    assert topo2jsonfg(args) == 1
    assert "SHELL_INSIDE_OUT" in capsys.readouterr().err
    assert topo2jsonfg([*args, "--no-orientation-check"]) == 0
