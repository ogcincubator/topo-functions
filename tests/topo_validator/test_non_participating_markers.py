"""Tests for the generalized non-participating exemption mechanism.

`observedVectors`/`vectorObservations` (curves) and shell-derived
`surface_shell_face_refs` (faces) were previously the *only* ways to exempt
geometry from DANGLING_CURVE/DANGLING_FACE, and both were closed to
cadastral-survey-specific collection names or shapes. A document can now opt
any collection or feature into the same exemption explicitly, by setting
`topologyRole: "nonParticipating"`:

* on a FeatureCollection wrapper (any name, not just the two legacy ones) ->
  its features' curve references are exempt from DANGLING_CURVE; or
* on an individual face Feature's own `properties` -> that face is exempt
  from DANGLING_FACE, independent of whether any shell references it.

The defaults are unconditional and unaffected: existing documents using
"observedVectors"/"vectorObservations" need no marker at all. The guiding
regression concern throughout is that *unmarked* geometry must keep failing
exactly as before -- only explicitly marked geometry gets a pass.
"""

from __future__ import annotations

from conftest import cube_data, merge_datasets

from topo_validator.loader import (
    _build_marked_non_participating_faces,
    _build_observation_curves,
    _marked_observation_curve_collection_names,
    from_csdm_json,
)
from topo_validator.merge import merge_topology
from topo_validator.model import errors_only
from topo_validator.validator import (
    _validate_marked_non_participating_faces_structure,
    _validate_observation_curves_structure,
    validate_topology,
)

# ---------------------------------------------------------------------------
# Loader: collection-level marker recognition for observation curves
# ---------------------------------------------------------------------------


def test_marked_collection_with_arbitrary_feature_type_is_recognized_as_observation_curve_source():
    """A collection named and typed nothing like the cadastral defaults is
    still recognized as an observation-curve source once it carries the
    marker -- the whole point of generalizing beyond "observedVectors"/
    "vectorObservations"."""
    data = {
        "siteVectors": [
            {
                "type": "FeatureCollection",
                "featureType": "anything",
                "topologyRole": "nonParticipating",
                "features": [
                    {"id": "f1", "type": "Feature", "topology": {"ref": "curve-1"}}
                ],
            }
        ]
    }

    assert _marked_observation_curve_collection_names(data) == {"siteVectors"}

    observation_curves = _build_observation_curves(data)
    assert {"ref": "curve-1", "source": "siteVectors"} in observation_curves


def test_unmarked_unknown_collection_name_is_not_recognized_as_observation_curve_source():
    """Without the marker, an arbitrarily-named collection contributes
    nothing -- a curve it references must still be caught as dangling. This
    is the "don't widen the net" guard for the collection-level mechanism."""
    data = {
        "siteVectors": [
            {
                "type": "FeatureCollection",
                "featureType": "anything",
                "features": [
                    {"id": "f1", "type": "Feature", "topology": {"ref": "curve-1"}}
                ],
            }
        ]
    }

    assert _marked_observation_curve_collection_names(data) == set()
    assert _build_observation_curves(data) == []


def test_marked_collection_extraction_tries_both_known_feature_shapes():
    """A marked collection may use either the `observedVectors`-style
    singular `topology.ref`, or the `vectorObservations`-style
    `topology.directed_references[].ref` list -- both must resolve without
    the caller having to pick a hardcoded shape by collection name."""
    data = {
        "customSource": [
            {
                "type": "FeatureCollection",
                "featureType": "custom",
                "topologyRole": "nonParticipating",
                "features": [
                    {"id": "f1", "type": "Feature", "topology": {"ref": "curve-1"}},
                    {
                        "id": "f2",
                        "type": "Feature",
                        "topology": {
                            "directed_references": [
                                {"ref": "curve-2", "orientation": "+"}
                            ]
                        },
                    },
                ],
            }
        ]
    }

    refs = {oc["ref"] for oc in _build_observation_curves(data)}
    assert refs == {"curve-1", "curve-2"}


def test_extra_observation_curve_sources_override_extends_defaults_without_disabling_them():
    """The `extra_observation_curve_sources` escape hatch for callers who
    cannot edit their documents must add to the recognized set, never
    replace it -- a legacy "observedVectors" collection stays recognized
    even when the caller also passes an extra name."""
    data = {
        "observedVectors": [
            {
                "type": "FeatureCollection",
                "featureType": "observedVectors",
                "features": [
                    {"id": "f1", "type": "Feature", "topology": {"ref": "curve-1"}}
                ],
            }
        ],
        "myLegacySource": [
            {
                "type": "FeatureCollection",
                "featureType": "myLegacySource",
                "features": [
                    {"id": "f2", "type": "Feature", "topology": {"ref": "curve-2"}}
                ],
            }
        ],
    }

    observation_curves = _build_observation_curves(
        data, extra_observation_curve_sources={"myLegacySource"}
    )
    refs = {oc["ref"] for oc in observation_curves}
    assert refs == {"curve-1", "curve-2"}


def test_from_csdm_json_passes_extra_observation_curve_sources_through():
    data = {
        "myLegacySource": [
            {
                "type": "FeatureCollection",
                "featureType": "myLegacySource",
                "features": [
                    {"id": "f1", "type": "Feature", "topology": {"ref": "curve-1"}}
                ],
            }
        ]
    }

    topology = from_csdm_json(data, extra_observation_curve_sources={"myLegacySource"})

    assert {oc["ref"] for oc in topology["observation_curves"]} == {"curve-1"}


# ---------------------------------------------------------------------------
# Loader: feature-level marker recognition for non-participating faces
# ---------------------------------------------------------------------------


def test_feature_marked_non_participating_face_is_collected_regardless_of_shell():
    data = {
        "faces": [
            {
                "id": "face-1",
                "type": "Feature",
                "topology": {"type": "Face", "directed_references": []},
                "properties": {"topologyRole": "nonParticipating"},
            }
        ]
    }

    assert _build_marked_non_participating_faces(data) == [{"ref": "face-1"}]


def test_unmarked_face_is_not_collected_as_non_participating():
    data = {
        "faces": [
            {
                "id": "face-1",
                "type": "Feature",
                "topology": {"type": "Face", "directed_references": []},
                "properties": {},
            }
        ]
    }

    assert _build_marked_non_participating_faces(data) == []


# ---------------------------------------------------------------------------
# End-to-end: marked geometry is exempt; unmarked geometry still fails.
# ---------------------------------------------------------------------------


def test_marked_observation_curve_exempts_an_otherwise_dangling_curve(unit_cube):
    """A curve with no surface reference is normally DANGLING_CURVE once
    surfaces are present (see test_geometry_tier_gating). Recording it as an
    observation curve under an arbitrary, non-cadastral `source` name must
    exempt it exactly as the two legacy names would."""
    orphan_curve = {"id": "orphan-curve", "vertices": ["p0", "p1"]}
    data = {
        **unit_cube,
        "curves": [*unit_cube["curves"], orphan_curve],
        "observation_curves": [{"ref": "orphan-curve", "source": "siteVectors"}],
    }

    issues = validate_topology(data)

    assert not any(issue["code"] == "DANGLING_CURVE" for issue in issues)


def test_unmarked_dangling_curve_still_fails_even_with_unrelated_observation_curves(unit_cube):
    """A curve that is not recorded in `observation_curves` at all must
    still be reported dangling, even when the dataset legitimately exempts
    some other curve -- marking one curve must not widen the net for
    others."""
    orphan_curve = {"id": "orphan-curve", "vertices": ["p0", "p1"]}
    data = {
        **unit_cube,
        "curves": [*unit_cube["curves"], orphan_curve],
        "observation_curves": [{"ref": "some-other-curve", "source": "siteVectors"}],
    }

    issues = validate_topology(data)

    assert any(
        issue["code"] == "DANGLING_CURVE" and issue["object_id"] == "orphan-curve"
        for issue in issues
    )


def test_marked_non_participating_face_exempts_a_face_with_no_shell_at_all():
    """A face with no shell membership at all (not even a surface-only
    shell) is still exempt from DANGLING_FACE once it is individually
    marked `topologyRole: "nonParticipating"` -- the whole point of the
    feature-level mechanism being independent of `surface_shell_face_refs`."""
    solid = cube_data("", 0, 0, 0, 1, 1, 1)
    marked_face = {"id": "marked-face", "rings": []}
    data = merge_datasets(
        solid, {"points": [], "curves": [], "surfaces": [marked_face], "solids": []}
    )
    data["marked_non_participating_faces"] = [{"ref": "marked-face"}]

    issues = validate_topology(data)

    assert not any(
        issue["code"] == "DANGLING_FACE" and issue["object_id"] == "marked-face"
        for issue in issues
    )


def test_unmarked_face_with_no_shell_still_reports_dangling_face():
    """Control case for the above: an identical orphan face with no marker
    and no shell must still fail -- confirms the feature-level mechanism
    only exempts what is explicitly marked."""
    solid = cube_data("", 0, 0, 0, 1, 1, 1)
    orphan_face = {"id": "orphan-face", "rings": []}
    data = merge_datasets(
        solid, {"points": [], "curves": [], "surfaces": [orphan_face], "solids": []}
    )

    issues = validate_topology(data)

    assert any(
        issue["code"] == "DANGLING_FACE" and issue["object_id"] == "orphan-face"
        for issue in issues
    )


# ---------------------------------------------------------------------------
# merge_topology: marked_non_participating_faces must survive merging an
# inline CSDM source with an external RDF source (the CLI's --ttl path),
# the same way observation_curves/surface_shell_face_refs already do.
# ---------------------------------------------------------------------------


def test_merge_topology_carries_marked_non_participating_faces_forward():
    base = {"points": [], "curves": [], "surfaces": [], "solids": []}
    inline = {**base, "marked_non_participating_faces": [{"ref": "face-1"}]}
    other = {**base, "marked_non_participating_faces": [{"ref": "face-2"}]}

    merged = merge_topology(inline, other)

    assert {f["ref"] for f in merged["marked_non_participating_faces"]} == {
        "face-1",
        "face-2",
    }


def test_merge_topology_deduplicates_marked_non_participating_faces_by_ref():
    base = {"points": [], "curves": [], "surfaces": [], "solids": []}
    first = {**base, "marked_non_participating_faces": [{"ref": "face-1"}]}
    second = {**base, "marked_non_participating_faces": [{"ref": "face-1"}]}

    merged = merge_topology(first, second)

    assert merged["marked_non_participating_faces"] == [{"ref": "face-1"}]


# ---------------------------------------------------------------------------
# Structural validation
# ---------------------------------------------------------------------------


def test_observation_curve_structure_accepts_any_non_empty_source_string():
    data = {"observation_curves": [{"ref": "curve-1", "source": "siteVectors"}]}

    assert _validate_observation_curves_structure(data) == []


def test_observation_curve_structure_rejects_missing_or_empty_source():
    data = {
        "observation_curves": [
            {"ref": "curve-1", "source": ""},
            {"ref": "curve-2"},
        ]
    }

    issues = _validate_observation_curves_structure(data)
    assert errors_only(issues)
    assert all(issue["code"] == "INVALID_OBSERVATION_CURVE_SOURCE" for issue in issues)


def test_marked_non_participating_faces_structure_accepts_valid_records():
    data = {"marked_non_participating_faces": [{"ref": "face-1"}]}

    assert _validate_marked_non_participating_faces_structure(data) == []


def test_marked_non_participating_faces_structure_rejects_non_string_ref():
    data = {"marked_non_participating_faces": [{"ref": 123}]}

    issues = _validate_marked_non_participating_faces_structure(data)
    assert len(issues) == 1
    assert issues[0]["code"] == "INVALID_MARKED_NON_PARTICIPATING_FACE_REF"


def test_marked_non_participating_faces_structure_rejects_non_list():
    data = {"marked_non_participating_faces": "not-a-list"}

    issues = _validate_marked_non_participating_faces_structure(data)
    assert len(issues) == 1
    assert issues[0]["code"] == "INVALID_MARKED_NON_PARTICIPATING_FACES"
