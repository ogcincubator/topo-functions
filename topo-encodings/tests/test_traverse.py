import pytest
from builders import (
    cube_doc,
    cube_with_void_doc,
    signed_volume,
    two_cubes_sharing_a_face_doc,
)

from topo_encodings import EncodingError, require_coordinates, solids, traverse


def _places(traversal):
    return {pid: p.place for pid, p in traversal.points.items()}


def _features(doc, key):
    return [f for c in doc[key] for f in c["features"]]


def _feature(doc, key, fid):
    return next(f for f in _features(doc, key) if f["id"] == fid)


# Positive cases ---------------------------------------------------------------------


def test_cube_is_one_outward_shell():
    t = traverse(cube_doc())
    (solid,) = t.solids
    (shell,) = solid.shells
    assert (shell.id, shell.sign, shell.role) == ("shell", 1, "exterior")
    assert len(shell.faces) == 6
    assert all(len(face.loops) == 1 and len(face.loops[0]) == 4 for face in shell.faces)
    loops = [loop for face in shell.faces for loop in face.loops]
    assert signed_volume(_places(t), loops) == pytest.approx(1.0)


def test_void_used_with_minus_faces_into_the_void():
    t = traverse(cube_with_void_doc())
    (solid,) = t.solids
    outer, void = solid.shells
    assert (outer.role, outer.sign) == ("exterior", 1)
    assert (void.id, void.role, void.sign) == ("void", "void", -1)
    assert all(face.sign == -1 for face in void.faces)
    places = _places(t)

    def volume(shell):
        return signed_volume(places, [lp for f in shell.faces for lp in f.loops])

    assert volume(outer) == pytest.approx(27.0)
    assert volume(void) == pytest.approx(-1.0)  # faces point into the void
    assert volume(outer) + volume(void) == pytest.approx(26.0)


def test_a_reversed_ring_is_traversed_backwards():
    doc = cube_doc()
    face = _feature(doc, "faces", "top")
    ring_id = face["topology"]["directed_references"][0]["ref"]
    ring = _feature(doc, "rings", ring_id)
    refs = ring["topology"]["directed_references"]
    # Store the ring the other way round, and use it with '-': same loop in the face.
    ring["topology"]["directed_references"] = [
        {"ref": r["ref"], "orientation": "-" if r["orientation"] == "+" else "+"}
        for r in reversed(refs)
    ]
    face["topology"]["directed_references"][0]["orientation"] = "-"
    (shell,) = traverse(doc).solids[0].shells
    top = next(f for f in shell.faces if f.id == "top")
    original = next(
        f for f in traverse(cube_doc()).solids[0].shells[0].faces if f.id == "top"
    )
    loop, expected = top.loops[0], original.loops[0]
    i = loop.index(expected[0])
    assert loop[i:] + loop[:i] == expected


def test_a_shared_face_appears_once_per_solid_in_opposite_orientations():
    t = traverse(two_cubes_sharing_a_face_doc())
    a, b = t.solids
    face_a = next(f for f in a.shells[0].faces if f.id == "aeast")
    face_b = next(f for f in b.shells[0].faces if f.id == "aeast")
    assert (face_a.sign, face_b.sign) == (1, -1)
    assert face_b.loops[0] == face_a.loops[0][::-1]
    places = _places(t)
    for solid in (a, b):
        loops = [lp for f in solid.shells[0].faces for lp in f.loops]
        assert signed_volume(places, loops) == pytest.approx(1.0)


def test_relationships_and_properties_are_kept_unchanged():
    link = {
        "href": "parcel-1",
        "rel": "topology",
        "role": "containingPrimaryParcel",
        "targetFeatureType": "PrimaryParcel",
    }
    a, b = solids(two_cubes_sharing_a_face_doc(relationships=[link]))
    assert a.relationships == (link,) and b.relationships == (link,)
    assert a.properties == {"name": "A"}
    (cube,) = solids(cube_doc())
    assert cube.relationships is None


def test_every_collection_under_a_key_is_read_and_non_edges_are_ignored_unless_used():
    doc = cube_doc()
    doc["points"].append(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "id": "q1",
                    "type": "Feature",
                    "place": {"type": "Point", "coordinates": [5.0, 5.0, 0.0]},
                },
                {
                    "id": "q2",
                    "type": "Feature",
                    "place": {"type": "Point", "coordinates": [6.0, 5.0, 0.0]},
                },
            ],
        }
    )
    # An observed vector, as in a parcel's boundary: a LineString, not an Edge.
    doc["edges"].append(
        {
            "type": "FeatureCollection",
            "featureType": "surv:ObservedVector",
            "features": [
                {
                    "id": "ov1",
                    "type": "Feature",
                    "topology": {"type": "LineString", "references": ["q1", "q2"]},
                },
                {
                    "id": "ov-unusable",
                    "type": "Feature",
                    "topology": {"type": "LineString", "references": ["q1"]},
                },
            ],
        }
    )
    t = traverse(doc)
    assert {"q1", "q2"} <= set(t.points)
    assert len(t.solids) == 1


def test_a_document_without_solids_has_none():
    doc = cube_doc()
    doc["solids"] = []
    assert solids(doc) == []


def test_require_coordinates_accepts_place_and_refuses_missing_geometry():
    t = traverse(cube_doc())
    require_coordinates(t, "place")
    with pytest.raises(EncodingError) as err:
        require_coordinates(t, "geometry")
    assert err.value.codes == {"POINT_WITHOUT_GEOMETRY"}
    assert len(err.value.problems) == 8


# Negative cases ---------------------------------------------------------------------


def _codes(doc):
    with pytest.raises(EncodingError) as err:
        traverse(doc)
    return err.value.codes, err.value.problems


@pytest.mark.parametrize(
    "key, fid",
    [("solids", "cube"), ("shells", "shell"), ("faces", "top")],
)
def test_an_unresolved_directed_reference_is_reported(key, fid):
    doc = cube_doc()
    _feature(doc, key, fid)["topology"]["directed_references"][0]["ref"] = "nowhere"
    codes, problems = _codes(doc)
    assert codes == {"MISSING_REFERENCE"}
    assert problems[0].id == fid


def test_an_unresolved_edge_reference_is_reported():
    doc = cube_doc()
    ring = _features(doc, "rings")[0]
    ring["topology"]["directed_references"][1]["ref"] = "nowhere"
    codes, problems = _codes(doc)
    assert codes == {"MISSING_REFERENCE"}
    assert problems[0].id == ring["id"]


def test_an_edge_to_a_missing_point_is_reported():
    doc = cube_doc()
    _features(doc, "edges")[0]["topology"]["references"][1] = "nowhere"
    codes, _ = _codes(doc)
    assert codes == {"MISSING_REFERENCE"}


def test_a_reference_to_the_wrong_collection_is_reported():
    doc = cube_doc()
    _feature(doc, "solids", "cube")["topology"]["directed_references"][0]["ref"] = "top"
    codes, problems = _codes(doc)
    assert codes == {"MISSING_REFERENCE"}
    assert "not in shells" in problems[0].message


def test_all_problems_are_reported_together():
    doc = cube_doc()
    _feature(doc, "shells", "shell")["topology"]["directed_references"][0][
        "ref"
    ] = "nowhere"
    _feature(doc, "faces", "top")["topology"]["directed_references"][0][
        "orientation"
    ] = "x"
    codes, problems = _codes(doc)
    assert codes == {"MISSING_REFERENCE", "BAD_ORIENTATION"}
    assert len(problems) == 2


def test_a_disconnected_ring_is_refused():
    doc = cube_doc()
    ring = _features(doc, "rings")[0]
    refs = ring["topology"]["directed_references"]
    refs[0], refs[1] = refs[1], refs[0]
    codes, _ = _codes(doc)
    assert codes == {"RING_NOT_CONNECTED"}


def test_an_open_ring_is_refused():
    doc = cube_doc()
    ring = _features(doc, "rings")[0]
    ring["topology"]["directed_references"].pop()
    codes, _ = _codes(doc)
    assert codes == {"RING_NOT_CLOSED"}


def test_a_curved_or_multi_point_edge_used_by_a_ring_is_refused():
    doc = cube_doc()
    edge = _features(doc, "edges")[0]
    edge["topology"]["references"].insert(1, "p7")
    codes, problems = _codes(doc)
    assert codes == {"EDGE_NOT_STRAIGHT"}
    assert problems[0].id == edge["id"]


def test_duplicate_ids_are_refused():
    doc = cube_doc()
    _features(doc, "faces")[1]["id"] = _features(doc, "faces")[0]["id"]
    codes, _ = _codes(doc)
    assert "DUPLICATE_ID" in codes


def test_a_malformed_point_is_refused():
    doc = cube_doc()
    _features(doc, "points")[0]["place"]["coordinates"] = [0.0, "north", 1.0]
    codes, _ = _codes(doc)
    assert codes == {"BAD_POINT"}


def test_a_solid_point_with_two_dimensional_place_is_refused():
    doc = cube_doc()
    _feature(doc, "points", "p1")["place"]["coordinates"] = [0.0, 0.0]
    t = traverse(doc)
    with pytest.raises(EncodingError) as err:
        require_coordinates(t, "place")
    assert err.value.codes == {"POINT_NOT_3D"}
    assert err.value.problems[0].id == "p1"


def test_two_dimensional_points_that_no_solid_uses_are_accepted():
    doc = cube_doc()
    doc["points"][0]["features"].append(
        {
            "id": "boundary",
            "type": "Feature",
            "place": {"type": "Point", "coordinates": [9.0, 9.0]},
            "geometry": {"type": "Point", "coordinates": [116.0, -31.9]},
        }
    )
    require_coordinates(traverse(doc), "place")


def test_an_empty_shell_is_refused():
    doc = cube_doc()
    _feature(doc, "shells", "shell")["topology"]["directed_references"] = []
    codes, _ = _codes(doc)
    assert codes == {"EMPTY_TOPOLOGY"}


def test_a_non_object_input_is_refused():
    with pytest.raises(EncodingError) as err:
        traverse([])
    assert err.value.codes == {"NOT_A_DOCUMENT"}


def test_a_shared_problem_is_reported_once():
    doc = two_cubes_sharing_a_face_doc()
    _feature(doc, "faces", "aeast")["topology"]["directed_references"][0][
        "ref"
    ] = "nowhere"
    _, problems = _codes(doc)
    assert len(problems) == 1
