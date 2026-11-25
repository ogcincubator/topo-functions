import json

import pytest
from builders import (
    cube_doc,
    cube_with_void_doc,
    signed_volume,
    two_cubes_sharing_a_face_doc,
)

from topo_encodings import EncodingError, to_jsonfg
from topo_encodings.jsonfg_write import CORE, POLYHEDRA

ENU = "east-north-up"

CRS = [
    "http://www.opengis.net/def/crs/EPSG/0/7850",
    "http://www.opengis.net/def/crs/EPSG/0/5711",
]


def _volume(coordinates):
    """Signed volume of Polyhedron coordinates (closed rings, as written)."""
    points, loops = {}, []
    for shell in coordinates:
        for face in shell:
            for ring in face:
                assert ring[0] == ring[-1]
                ids = []
                for xyz in ring[:-1]:
                    points.setdefault(tuple(xyz), tuple(xyz))
                    ids.append(tuple(xyz))
                loops.append(ids)
    return signed_volume(points, loops)


def test_cube_collection_members():
    out = to_jsonfg(cube_doc(), place_axes=ENU, crs=CRS, name="Cube")
    assert list(out) == [
        "type",
        "conformsTo",
        "coordRefSys",
        "geometryDimension",
        "name",
        "features",
    ]
    assert out["conformsTo"] == [CORE, POLYHEDRA]
    assert out["coordRefSys"] == CRS
    assert out["geometryDimension"] == 3
    (feature,) = out["features"]
    assert feature["id"] == "cube"
    assert feature["geometry"] is None
    assert feature["place"]["type"] == "Polyhedron"
    assert feature["properties"] == {
        "name": "cube",
        "ptQualityMeasure": None,
        "shells": [{"id": "shell", "role": "exterior", "faces": 6}],
    }
    assert _volume(feature["place"]["coordinates"]) == pytest.approx(1.0)
    json.dumps(out, allow_nan=False)  # plain JSON


def test_rings_are_closed_and_coordinates_unchanged():
    doc = cube_doc()
    doc["points"][0]["features"][0]["place"]["coordinates"] = [0, 0, 0]
    out = to_jsonfg(doc, place_axes=ENU, crs=CRS)
    rings = [
        r for s in out["features"][0]["place"]["coordinates"] for f in s for r in f
    ]
    assert all(len(r) == 5 and r[0] == r[-1] for r in rings)
    assert [0.0, 0.0, 0.0] in [xyz for r in rings for xyz in r]


def test_void_faces_into_the_void():
    (feature,) = to_jsonfg(cube_with_void_doc(), place_axes=ENU, crs=CRS)["features"]
    outer, void = feature["place"]["coordinates"]
    assert _volume([outer]) == pytest.approx(27.0)
    assert _volume([void]) == pytest.approx(-1.0)
    assert feature["properties"]["shells"] == [
        {"id": "outer", "role": "exterior", "faces": 6},
        {"id": "void", "role": "void", "faces": 6},
    ]


def test_several_solids_with_a_shared_face():
    out = to_jsonfg(two_cubes_sharing_a_face_doc(), place_axes=ENU, crs=CRS)
    a, b = out["features"]
    assert (a["id"], b["id"]) == ("A", "B")
    for feature in (a, b):
        assert _volume(feature["place"]["coordinates"]) == pytest.approx(1.0)
    shared_a = a["place"]["coordinates"][0][3]  # "aeast", A's fourth face
    shared_b = b["place"]["coordinates"][0][-1]  # used by B with '-', its last face
    assert shared_b[0][:-1] == shared_a[0][:-1][::-1]


def test_multi_polyhedron():
    doc = two_cubes_sharing_a_face_doc()
    doc["id"] = "survey-1"
    per_solid = to_jsonfg(doc, place_axes=ENU, crs=CRS)
    out = to_jsonfg(doc, place_axes=ENU, crs=CRS, features="multi")
    (feature,) = out["features"]
    assert feature["id"] == "survey-1"
    assert feature["place"]["type"] == "MultiPolyhedron"
    assert feature["place"]["coordinates"] == [
        f["place"]["coordinates"] for f in per_solid["features"]
    ]
    assert [s["id"] for s in feature["properties"]["solids"]] == ["A", "B"]
    assert feature["properties"]["solids"][0]["name"] == "A"


def test_relationships_are_copied_unchanged():
    link = {"href": "parcel-1", "rel": "topology", "targetRole": "containingParcel"}
    out = to_jsonfg(
        two_cubes_sharing_a_face_doc(relationships=[link]), place_axes=ENU, crs=CRS
    )
    assert [f["properties"]["relationships"] for f in out["features"]] == [[link]] * 2


def test_point_quality_is_the_largest_or_none():
    doc = cube_doc()
    points = doc["points"][0]["features"]
    for i, point in enumerate(points):
        point["properties"]["ptQualityMeasure"] = 0.01 * (i + 1)
    (feature,) = to_jsonfg(doc, place_axes=ENU, crs=CRS)["features"]
    assert feature["properties"]["ptQualityMeasure"] == pytest.approx(0.08)
    points[3]["properties"]["ptQualityMeasure"] = 0
    (feature,) = to_jsonfg(doc, place_axes=ENU, crs=CRS)["features"]
    assert feature["properties"]["ptQualityMeasure"] is None


def test_name_and_provenance():
    doc = cube_doc()
    doc["surveyTitle"] = "Survey"
    provenance = {"generator": "test"}
    out = to_jsonfg(
        doc,
        place_axes=ENU,
        crs="http://www.opengis.net/def/crs/EPSG/0/4978",
        derived_from=provenance,
    )
    assert out["name"] == "Survey"
    assert out["coordRefSys"] == "http://www.opengis.net/def/crs/EPSG/0/4978"
    assert out["derivedFrom"] == provenance
    assert list(out)[-2:] == ["derivedFrom", "features"]


@pytest.mark.parametrize("crs", ["", [], [""], [None]])
def test_a_missing_crs_is_refused(crs):
    with pytest.raises(ValueError):
        to_jsonfg(cube_doc(), place_axes=ENU, crs=crs)


def test_an_unknown_features_option_is_refused():
    with pytest.raises(ValueError):
        to_jsonfg(cube_doc(), place_axes=ENU, crs=CRS, features="per-face")


def test_a_document_without_solids_is_refused():
    doc = cube_doc()
    doc["solids"] = []
    with pytest.raises(EncodingError) as err:
        to_jsonfg(doc, place_axes=ENU, crs=CRS)
    assert err.value.codes == {"NO_SOLIDS"}


def test_mixed_place_and_geometry_points_are_refused():
    doc = cube_doc()
    point = doc["points"][0]["features"][0]
    point["geometry"] = {"type": "Point", "coordinates": point["place"]["coordinates"]}
    del point["place"]
    with pytest.raises(EncodingError) as err:
        to_jsonfg(doc, place_axes=ENU, crs=CRS)
    assert err.value.codes == {"POINT_WITHOUT_PLACE"}


def test_a_topology_problem_is_refused():
    doc = cube_doc()
    doc["shells"][0]["features"][0]["topology"]["directed_references"][0][
        "ref"
    ] = "nowhere"
    with pytest.raises(EncodingError) as err:
        to_jsonfg(doc, place_axes=ENU, crs=CRS)
    assert err.value.codes == {"MISSING_REFERENCE"}
