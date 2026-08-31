"""Tests for topo_validator.loader's CSDM JSON adaptation.

Covers the two solid construction styles the loader must support:

* the original solid -> shell -> face -> ring -> edge -> point chain, and
* the derived-by-offset variant, where a shell's directed references may name
  other shells (the offset upper/lower surfaces) alongside plain faces.
"""

from pathlib import Path

from topo_validator.loader import from_csdm_json, load_json
from topo_validator.validator import validate_topology

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
DERIVED_SOLID_FIXTURE = FIXTURES_DIR / "derived-3d-solid.json"

# Face ids of the two offset surface shells nested inside the solid's outer
# shell in derived-3d-solid.json.
UPPER_SURFACE_FACES = (
    "uuid:8412a394-e951-4859-aaf7-7f53d18e7994",
    "uuid:7045501d-ce9b-4325-8a0c-6ef4d7cae2cc",
)
LOWER_SURFACE_FACES = (
    "uuid:db75fa76-3e57-48de-9c80-2b39ba998580",
    "uuid:5d931b84-8505-46de-a37d-990266c02fe9",
)
NESTED_SHELL_IDS = (
    "uuid:350d3b01-a2cc-415b-8f9b-8ed12bb6fc0d",
    "uuid:82b3d84c-b6eb-400e-bb86-83f67d9c7a45",
)
GROUND_SURFACE_FACES = (
    "uuid:785fe569-93c7-4a5a-b8dc-1ed6a4964029",
    "uuid:b631b943-8681-4ba7-baba-cbf90555d550",
)


def _ref(ref, orientation="+"):
    return {"ref": ref, "orientation": orientation}


def _build_shells(shells, faces=("F1", "F2", "F3", "F4", "F5")):
    """Resolve the given shells through the public loader entry point.

    Shells surface in TopologyData only via the solids that own them, so each
    shell is given a solid of its own. "solid['shells'][0]" is then that shell
    exactly as the loader resolved it, keyed here by shell id for readability.

    Args:
        shells: (shell id, directed references) pairs, in declaration order.
        faces: Face feature ids the shells may reference.

    Returns:
        Resolved shell records keyed by shell id.
    """
    shell_ids = [shell_id for shell_id, _ in shells]
    data = from_csdm_json(
        {
            "faces": [{"features": [{"id": face_id} for face_id in faces]}],
            "shells": [
                {
                    "features": [
                        {
                            "id": shell_id,
                            "topology": {
                                "type": "Shell",
                                "directed_references": directed_references,
                            },
                        }
                        for shell_id, directed_references in shells
                    ]
                }
            ],
            "solids": [
                {
                    "features": [
                        {
                            "id": f"solid-of-{shell_id}",
                            "topology": {
                                "type": "Solid",
                                "directed_references": [_ref(shell_id)],
                            },
                        }
                        for shell_id in shell_ids
                    ]
                }
            ],
        }
    )

    return {
        shell_id: solid["shells"][0]
        for shell_id, solid in zip(shell_ids, data["solids"], strict=True)
    }


# ---------------------------------------------------------------------------
# Nested shell resolution
# ---------------------------------------------------------------------------


def test_flat_shell_of_faces_is_unchanged():
    """The original construction -- a shell of plain faces -- still resolves."""
    shells = _build_shells([("S", [_ref("F1"), _ref("F2", "-")])])

    assert shells["S"]["faces"] == ["F1", "F2"]
    assert shells["S"]["face_orientations"] == {"F1": "+", "F2": "-"}


def test_shell_referencing_a_shell_is_flattened_to_its_faces():
    shells = _build_shells(
        [
            ("inner", [_ref("F1"), _ref("F2", "-")]),
            ("S", [_ref("F3"), _ref("inner")]),
        ]
    )

    assert shells["S"]["faces"] == ["F3", "F1", "F2"]
    assert shells["S"]["face_orientations"] == {"F3": "+", "F1": "+", "F2": "-"}


def test_nested_shells_remain_addressable_in_their_own_right():
    """Flattening a parent must not consume the nested shell's own record.

    A parcel's verticalExtent geometryRef / referenceSurfaces point at these
    shell ids directly, so they have to survive as shells too.
    """
    shells = _build_shells(
        [("inner", [_ref("F1")]), ("S", [_ref("F2"), _ref("inner")])]
    )

    assert shells["inner"]["faces"] == ["F1"]
    assert shells["S"]["faces"] == ["F2", "F1"]


def test_negative_shell_reference_flips_nested_face_orientations():
    shells = _build_shells(
        [
            ("inner", [_ref("F1"), _ref("F2", "-")]),
            ("S", [_ref("F3"), _ref("inner", "-")]),
        ]
    )

    assert shells["S"]["face_orientations"] == {"F3": "+", "F1": "-", "F2": "+"}


def test_double_negation_through_three_levels_restores_orientations():
    shells = _build_shells(
        [
            ("a", [_ref("F1"), _ref("F2", "-")]),
            ("b", [_ref("a", "-")]),
            ("S", [_ref("b", "-")]),
        ]
    )

    assert shells["S"]["face_orientations"] == {"F1": "+", "F2": "-"}


def test_shell_may_reference_a_shell_declared_later_in_the_collection():
    """Resolution is two-pass, so declaration order must not matter."""
    shells = _build_shells([("S", [_ref("later")]), ("later", [_ref("F4", "-")])])

    assert shells["S"]["faces"] == ["F4"]
    assert shells["S"]["face_orientations"] == {"F4": "-"}


def test_a_face_reached_by_two_paths_is_recorded_once():
    """face_orientations cannot hold one face twice, and a double-counted face
    would break the exactly-twice curve counting in TR-06."""
    shells = _build_shells(
        [("inner", [_ref("F1", "-")]), ("S", [_ref("F1"), _ref("inner")])]
    )

    assert shells["S"]["faces"] == ["F1"]
    assert shells["S"]["face_orientations"] == {"F1": "+"}


def test_cyclic_shell_references_terminate():
    shells = _build_shells(
        [("A", [_ref("F1"), _ref("B")]), ("B", [_ref("F2"), _ref("A")])]
    )

    assert shells["A"]["faces"] == ["F1", "F2"]
    assert "A" not in shells["B"]["faces"]


def test_self_referencing_shell_terminates():
    shells = _build_shells([("A", [_ref("A"), _ref("F1")])])

    assert shells["A"]["faces"] == ["F1"]


def test_unresolvable_reference_is_kept_as_a_face_id():
    """An id that is neither a known face nor a known shell stays in faces, so
    downstream missing-reference rules can report it."""
    shells = _build_shells([("S", [_ref("F1"), _ref("no-such-id")])])

    assert shells["S"]["faces"] == ["F1", "no-such-id"]


def test_malformed_directed_references_are_skipped():
    shells = _build_shells(
        [("S", ["not-an-object", {"orientation": "+"}, _ref("F1")])]
    )

    assert shells["S"]["faces"] == ["F1"]


def test_unknown_orientation_value_falls_back_to_positive():
    shells = _build_shells([("S", [_ref("F1", "?")])])

    assert shells["S"]["face_orientations"] == {"F1": "+"}


# ---------------------------------------------------------------------------
# SubtendedAngle features in the edges collection
# ---------------------------------------------------------------------------


def test_subtended_angle_features_are_not_loaded_as_curves():
    """SubtendedAngle features share the "edges" collection with real edges but
    reference a vertex plus two edges, so reading them as curves would yield
    spurious unknown-point and dangling-curve issues."""
    data = from_csdm_json(
        {
            "edges": [
                {
                    "features": [
                        {
                            "id": "edge-1",
                            "topology": {
                                "type": "Edge",
                                "references": ["p1", "p2"],
                            },
                        },
                        {
                            "id": "angle-1",
                            "topology": {
                                "type": "SubtendedAngle",
                                "references": ["p2", "edge-1", "edge-2"],
                            },
                        },
                    ]
                }
            ]
        }
    )

    assert [curve["id"] for curve in data["curves"]] == ["edge-1"]


# ---------------------------------------------------------------------------
# Reference surface exemptions
# ---------------------------------------------------------------------------


def test_reference_surface_shell_expands_to_its_faces():
    data = from_csdm_json(
        {
            "faces": [{"features": [{"id": "F1"}, {"id": "F2"}]}],
            "shells": [
                {
                    "features": [
                        {
                            "id": "ground-shell",
                            "topology": {
                                "type": "Shell",
                                "directed_references": [_ref("F1"), _ref("F2")],
                            },
                        }
                    ]
                }
            ],
            "parcels": [
                {
                    "properties": {
                        "spatialRepresentationDefinitions": {
                            "referenceSurfaces": [
                                {"id": "surface-ground-1", "ref": "ground-shell"}
                            ]
                        }
                    }
                }
            ],
        }
    )

    assert data["reference_surfaces"] == [
        {"ref": "F1", "source": "surface-ground-1"},
        {"ref": "F2", "source": "surface-ground-1"},
    ]


def test_reference_surface_may_name_a_face_directly():
    data = from_csdm_json(
        {
            "faces": [{"features": [{"id": "F1"}]}],
            "parcels": [
                {
                    "properties": {
                        "spatialRepresentationDefinitions": {
                            "referenceSurfaces": [{"ref": "F1"}]
                        }
                    }
                }
            ],
        }
    )

    assert data["reference_surfaces"] == [{"ref": "F1", "source": "F1"}]


def test_no_reference_surfaces_when_a_dataset_has_no_parcels():
    assert from_csdm_json({})["reference_surfaces"] == []


# ---------------------------------------------------------------------------
# End-to-end: the derived-by-offset fixture
# ---------------------------------------------------------------------------


def test_derived_solid_fixture_resolves_nested_offset_surface_shells():
    data = from_csdm_json(load_json(DERIVED_SOLID_FIXTURE))
    solid = data["solids"][0]

    # The nested shell ids must not leak into the solid's faces as phantom
    # faces, and the offset surface faces they carry must be present.
    for shell_id in NESTED_SHELL_IDS:
        assert shell_id not in solid["faces"]
    for face_id in UPPER_SURFACE_FACES + LOWER_SURFACE_FACES:
        assert face_id in solid["faces"]

    assert len(solid["faces"]) == 12
    assert len(solid["shells"]) == 1
    assert solid["shells"][0]["type"] == "outer"


def test_derived_solid_fixture_exempts_the_ground_reference_surface():
    data = from_csdm_json(load_json(DERIVED_SOLID_FIXTURE))

    exempt = {entry["ref"] for entry in data["reference_surfaces"]}
    assert exempt == set(GROUND_SURFACE_FACES)

    # The reference surface belongs to no solid, which is the point of the
    # exemption.
    solid_faces = set(data["solids"][0]["faces"])
    assert not solid_faces & set(GROUND_SURFACE_FACES)


def test_derived_solid_fixture_validates_without_issues():
    data = from_csdm_json(load_json(DERIVED_SOLID_FIXTURE))

    assert validate_topology(data) == []
