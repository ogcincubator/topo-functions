import json
import math

import pytest
from builders import Builder, cube_doc, cube_with_void_doc, two_cubes_sharing_a_face_doc

from topo_encodings import EncodingError, to_geojson_faces
from topo_encodings.geojson_faces import rfc7946_winding

ENU = "east-north-up"


def ring_area_2d(ring):
    """Signed shoelace area (positive: counter-clockwise), from the first point."""
    x0, y0 = ring[0][0], ring[0][1]
    return 0.5 * sum(
        (a[0] - x0) * (b[1] - y0) - (b[0] - x0) * (a[1] - y0)
        for a, b in zip(ring[:-1], ring[1:])
    )


def transformer(e, n, h):
    """A stand-in for a grid transformation: east and north keep their directions."""
    return 116.0 + e * 1e-5, -32.0 + n * 1e-5, h - 31.25


def _by_face(out):
    return {f["id"]: f for f in out["features"]}


def test_one_polygon_per_face_with_its_solid_and_shell():
    out = to_geojson_faces(
        cube_doc(), place_axes=ENU, transformer=transformer, name="Cube"
    )
    assert list(out) == ["type", "name", "features"]
    assert out["name"] == "Cube"
    assert len(out["features"]) == 6
    top = _by_face(out)["top"]
    assert top["geometry"]["type"] == "Polygon"
    assert list(top["properties"]) == [
        "solid",
        "shell",
        "shellRole",
        "orientationInSolid",
        "ptQualityMeasure",
        "reversedForRfc7946",
        "outwardNormal",
    ]
    assert top["properties"]["solid"] == "cube"
    assert top["properties"]["shell"] == "shell"
    assert top["properties"]["shellRole"] == "exterior"
    assert top["properties"]["orientationInSolid"] == "+"
    assert top["properties"]["outwardNormal"] == [0.0, 0.0, 1.0]
    json.dumps(out, allow_nan=False)


def test_heights_come_from_the_transformer_and_are_rounded():
    out = to_geojson_faces(cube_doc(), place_axes=ENU, transformer=transformer)
    ring = _by_face(out)["top"]["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1]
    assert {p[2] for p in ring} == {-30.25}
    assert all(p[0] == round(p[0], 9) and p[1] == round(p[1], 9) for p in ring)


def test_rfc7946_winding_with_the_3d_orientation_kept():
    faces = _by_face(
        to_geojson_faces(cube_doc(), place_axes=ENU, transformer=transformer)
    )
    top, bottom, south = faces["top"], faces["bottom"], faces["south"]
    # Top is counter-clockwise from above already; bottom, seen from below, is not.
    assert top["properties"]["reversedForRfc7946"] is False
    assert bottom["properties"]["reversedForRfc7946"] is True
    for face in (top, bottom):
        assert ring_area_2d(face["geometry"]["coordinates"][0]) > 0
    assert bottom["properties"]["outwardNormal"] == [0.0, 0.0, -1.0]
    # Vertical faces keep their 3D winding.
    assert south["properties"]["reversedForRfc7946"] is False
    assert south["properties"]["outwardNormal"] == [0.0, -1.0, 0.0]


def test_winding_is_decided_by_the_normal_not_by_rounding():
    # A west-facing wall of topo-feature's polyhedron example, near longitude 116:
    # after rounding to 1e-9 degrees its footprint has a spurious area of 2.6e-14
    # square degrees, about three times the old area threshold; its normal in place
    # is horizontal, so it is vertical and kept as it is.
    ring = [
        [116.006604274, -31.889144786, -0.1436],
        [116.006604268, -31.889145318, -20.1426],
        [116.0066033, -31.889235497, -20.1136],
        [116.006603306, -31.889234965, -0.1136],
        [116.006604274, -31.889144786, -0.1436],
    ]
    assert ring_area_2d(ring) != 0.0
    assert rfc7946_winding([ring], [-1.0, 0.0, 0.0]) == [ring]
    assert rfc7946_winding([ring], [-1.0, 0.0, 1e-6]) == [ring]
    assert rfc7946_winding([ring], [-1.0, 0.0, 2e-6]) == [ring]
    assert rfc7946_winding([ring], [-1.0, 0.0, -2e-6]) == [ring[::-1]]


def test_a_slightly_tilted_face_is_wound_by_its_normal():
    b = Builder()
    # A west-facing wall leaning 0.01 degrees outward (its top further west), so its
    # outward normal points slightly up.
    lean = 3.0 * math.tan(math.radians(0.01))
    b.add_points(
        {
            "w1": (0, 0, 0), "w2": (0, 4, 0),
            "w3": (-lean, 4, 3), "w4": (-lean, 0, 3),
        }
    )  # fmt: skip
    b.face("wall", ("w1", "w2", "w3", "w4"))
    b.shell("s", [("wall", "+")])
    b.solid("x", [("s", "+")])
    (feature,) = to_geojson_faces(
        b.doc(), place_axes=ENU, transformer=transformer, check_orientation=False
    )["features"]
    assert feature["properties"]["outwardNormal"][2] > 1e-6
    assert feature["properties"]["reversedForRfc7946"] is False
    assert ring_area_2d(feature["geometry"]["coordinates"][0]) > 0


def _plate(orientation):
    """A horizontal 4 m plate with a 2 m hole, facing up when used with '+'."""
    b = Builder()
    b.add_points(
        {
            "o1": (0, 0, 0), "o2": (4, 0, 0), "o3": (4, 4, 0), "o4": (0, 4, 0),
            "h1": (1, 1, 0), "h2": (3, 1, 0), "h3": (3, 3, 0), "h4": (1, 3, 0),
        }
    )  # fmt: skip
    b.face("plate", ("o1", "o2", "o3", "o4"), ("h1", "h4", "h3", "h2"))
    b.shell("s", [("plate", orientation)])
    b.solid("x", [("s", "+")])
    (feature,) = to_geojson_faces(
        b.doc(), place_axes=ENU, transformer=transformer, check_orientation=False
    )["features"]
    return feature


@pytest.mark.parametrize("orientation, reversed_", [("+", False), ("-", True)])
def test_a_hole_is_wound_clockwise(orientation, reversed_):
    feature = _plate(orientation)
    outer, hole = feature["geometry"]["coordinates"]
    assert ring_area_2d(outer) > 0 and ring_area_2d(hole) < 0
    assert feature["properties"]["reversedForRfc7946"] is reversed_


def test_void_faces_point_into_the_void():
    out = to_geojson_faces(
        cube_with_void_doc(), place_axes=ENU, transformer=transformer
    )
    void = [f for f in out["features"] if f["properties"]["shell"] == "void"]
    assert len(void) == 6
    assert all(f["properties"]["shellRole"] == "void" for f in void)
    assert all(f["properties"]["orientationInSolid"] == "-" for f in void)
    bottom = next(f for f in void if f["id"] == "vbottom")
    assert bottom["properties"]["outwardNormal"] == [0.0, 0.0, 1.0]


def test_a_shared_face_appears_once_per_solid():
    out = to_geojson_faces(
        two_cubes_sharing_a_face_doc(), place_axes=ENU, transformer=transformer
    )
    assert len(out["features"]) == 12
    shared = [f for f in out["features"] if f["id"] == "aeast"]
    assert [f["properties"]["solid"] for f in shared] == ["A", "B"]
    assert [f["properties"]["orientationInSolid"] for f in shared] == ["+", "-"]
    assert [f["properties"]["outwardNormal"] for f in shared] == [
        [1.0, 0.0, 0.0],
        [-1.0, 0.0, 0.0],
    ]


def test_stated_ellipsoidal_geometry_heights():
    doc = cube_doc()
    for point in doc["points"][0]["features"]:
        e, n, h = point["place"]["coordinates"]
        point["geometry"] = {"type": "Point", "coordinates": list(transformer(e, n, h))}
    out = to_geojson_faces(doc, place_axes=ENU, heights="ellipsoidal-geometry")
    assert out == to_geojson_faces(doc, place_axes=ENU, transformer=transformer)


def test_name_and_provenance():
    doc = cube_doc()
    doc["name"] = "From the document"
    out = to_geojson_faces(
        doc, place_axes=ENU, transformer=transformer, derived_from={"g": 1}
    )
    assert out["name"] == "From the document"
    assert list(out) == ["type", "name", "derivedFrom", "features"]


# Refusals ---------------------------------------------------------------------------


def _codes(doc=None, **kwargs):
    with pytest.raises(EncodingError) as err:
        to_geojson_faces(doc or cube_doc(), **{"place_axes": ENU, **kwargs})
    return err.value.codes


def test_no_height_source_is_refused():
    assert _codes() == {"NO_HEIGHT_SOURCE"}


def test_two_height_sources_are_refused():
    assert _codes(transformer=transformer, heights="ellipsoidal-geometry") == {
        "TWO_HEIGHT_SOURCES"
    }


def test_an_unknown_height_statement_is_refused():
    with pytest.raises(ValueError):
        to_geojson_faces(cube_doc(), place_axes=ENU, heights="ahd")


def test_geometry_heights_need_geometry_on_every_point():
    assert _codes(heights="ellipsoidal-geometry") == {"POINT_WITHOUT_GEOMETRY"}


def test_every_point_needs_place():
    doc = cube_doc()
    del doc["points"][0]["features"][0]["place"]
    assert _codes(doc, transformer=transformer) == {"POINT_WITHOUT_PLACE"}


def test_a_failing_transformer_is_reported_per_point():
    def failing(e, n, h):
        if e > 0:
            raise RuntimeError("outside the grid")
        return transformer(e, n, h)

    with pytest.raises(EncodingError) as err:
        to_geojson_faces(cube_doc(), place_axes=ENU, transformer=failing)
    assert err.value.codes == {"TRANSFORM_FAILED"}
    assert len(err.value.problems) == 4  # the four points with easting 1


@pytest.mark.parametrize(
    "result", [(math.nan, 0.0, 0.0), (1.0, 2.0), ("east", 0.0, 0.0), None]
)
def test_an_unusable_transformer_result_is_refused(result):
    assert _codes(transformer=lambda e, n, h: result) == {"TRANSFORM_FAILED"}


def test_a_face_without_area_is_refused():
    b = Builder()
    b.add_points({"q1": (0, 0, 0), "q2": (1, 0, 0), "q3": (2, 0, 0)})
    b.face("flat", ("q1", "q2", "q3"))
    b.shell("s", [("flat", "+")])
    b.solid("x", [("s", "+")])
    with pytest.raises(EncodingError) as err:
        to_geojson_faces(
            b.doc(), place_axes=ENU, transformer=transformer, check_orientation=False
        )
    assert err.value.codes == {"DEGENERATE_FACE"}
    assert err.value.problems[0].id == "flat"


def test_a_document_without_solids_is_refused():
    doc = cube_doc()
    doc["solids"] = []
    assert _codes(doc, transformer=transformer) == {"NO_SOLIDS"}


# Axis order of place ---------------------------------------------------------------


def _north_east(doc):
    """The same document with place in north-east-up order (as NZTM is)."""
    for collection in doc["points"]:
        for point in collection["features"]:
            e, n, h = point["place"]["coordinates"]
            point["place"]["coordinates"] = [n, e, h]
    return doc


def test_north_east_place_gives_the_same_faces():
    expected = to_geojson_faces(
        cube_with_void_doc(), place_axes=ENU, transformer=transformer
    )
    out = to_geojson_faces(
        _north_east(cube_with_void_doc()),
        place_axes="north-east-up",
        transformer=lambda n, e, h: transformer(e, n, h),
    )
    assert out == expected


def test_reading_north_east_place_as_east_north_turns_normals_over():
    def encode(**kwargs):
        return to_geojson_faces(
            _north_east(cube_doc()),
            place_axes=ENU,
            transformer=lambda n, e, h: transformer(e, n, h),
            **kwargs,
        )

    with pytest.raises(EncodingError) as err:
        encode()
    assert err.value.codes == {"SHELL_INSIDE_OUT"}
    out = encode(check_orientation=False)
    assert _by_face(out)["top"]["properties"]["outwardNormal"] == [0.0, 0.0, -1.0]


def test_south_west_place_gives_the_same_normals():
    doc = cube_doc()
    for point in doc["points"][0]["features"]:
        e, n, h = point["place"]["coordinates"]
        point["place"]["coordinates"] = [-n, -e, h]  # southing, westing
    out = to_geojson_faces(
        doc,
        place_axes="south-west-up",
        transformer=lambda s, w, h: transformer(-w, -s, h),
    )
    expected = to_geojson_faces(cube_doc(), place_axes=ENU, transformer=transformer)
    assert out == expected


@pytest.mark.parametrize("axes", ["", "east-east-up", "east-north-down", "x-y-z"])
def test_an_unknown_axis_order_is_refused(axes):
    with pytest.raises(ValueError):
        to_geojson_faces(cube_doc(), place_axes=axes, transformer=transformer)


def test_the_axis_order_is_required():
    with pytest.raises(TypeError):
        to_geojson_faces(cube_doc(), transformer=transformer)
