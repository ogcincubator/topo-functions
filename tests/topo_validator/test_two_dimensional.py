"""Tests for 2D-dataset handling: validating a dataset whose points are all
2D (no z) must not fail -- it should produce a single NO_3D_TOPOLOGY warning
and skip the 3D-only conformance classes, per the documented "2D validation
is not yet implemented" contract. A dataset that's genuinely malformed (not
just 2D) must still fail as before.
"""

from topo_validator.model import errors_only
from topo_validator.validator import points_are_all_two_dimensional, validate_structure, validate_topology

TWO_D_TOPOLOGY = {
    "points": [
        {"id": "p1", "coordinates": [0.0, 0.0]},
        {"id": "p2", "coordinates": [1.0, 0.0]},
        {"id": "p3", "coordinates": [1.0, 1.0]},
    ],
    "curves": [],
    "surfaces": [],
    "solids": [],
}

THREE_D_TOPOLOGY = {
    "points": [
        {"id": "p1", "coordinates": [0.0, 0.0, 0.0]},
        {"id": "p2", "coordinates": [1.0, 0.0, 0.0]},
    ],
    "curves": [],
    "surfaces": [],
    "solids": [],
}

MIXED_TOPOLOGY = {
    "points": [
        {"id": "p1", "coordinates": [0.0, 0.0]},
        {"id": "p2", "coordinates": [1.0, 0.0, 5.0]},
    ],
    "curves": [],
    "surfaces": [],
    "solids": [],
}

MALFORMED_TOPOLOGY = {
    "points": [
        {"id": "p1", "coordinates": [0.0]},   # only one number -- not even 2D
    ],
    "curves": [],
    "surfaces": [],
    "solids": [],
}


def test_points_are_all_two_dimensional_true_for_a_consistent_2d_set():
    assert points_are_all_two_dimensional(TWO_D_TOPOLOGY["points"]) is True


def test_points_are_all_two_dimensional_false_for_3d_mixed_empty_or_malformed():
    assert points_are_all_two_dimensional(THREE_D_TOPOLOGY["points"]) is False
    assert points_are_all_two_dimensional(MIXED_TOPOLOGY["points"]) is False
    assert points_are_all_two_dimensional([]) is False
    assert points_are_all_two_dimensional(MALFORMED_TOPOLOGY["points"]) is False


def test_all_2d_dataset_produces_a_warning_not_an_error():
    issues = validate_structure(TWO_D_TOPOLOGY)

    assert errors_only(issues) == []
    codes = [i["code"] for i in issues]
    assert codes == ["NO_3D_TOPOLOGY"]
    assert issues[0]["severity"] == "warning"


def test_all_2d_dataset_validate_topology_passes_and_skips_conformance_classes():
    progress_messages: list[str] = []
    issues = validate_topology(TWO_D_TOPOLOGY, progress=progress_messages.append)

    assert errors_only(issues) == []
    assert [i["code"] for i in issues] == ["NO_3D_TOPOLOGY"]
    assert any("no 3D topology found" in m for m in progress_messages)
    assert not any(m.startswith("Running CC-") for m in progress_messages)


def test_mixed_2d_and_3d_points_still_fails_structurally():
    """A points collection that isn't consistently 2D (some points do have a
    z, one doesn't) is a real structural inconsistency, not "this is a 2D
    dataset" -- it must still be flagged as before."""
    issues = validate_structure(MIXED_TOPOLOGY)

    errors = errors_only(issues)
    assert len(errors) == 1
    assert errors[0]["code"] == "INVALID_COORDINATES"
    assert errors[0]["object_id"] == "p1"


def test_malformed_single_value_coordinates_still_fails_structurally():
    """A point with only one coordinate value isn't valid 2D either -- still
    a hard structural error."""
    issues = validate_structure(MALFORMED_TOPOLOGY)

    errors = errors_only(issues)
    assert len(errors) == 1
    assert errors[0]["code"] == "INVALID_COORDINATES"


def test_valid_3d_dataset_is_unaffected():
    """A normal 3D dataset must behave exactly as before -- no NO_3D_TOPOLOGY
    warning, conformance classes still run."""
    issues = validate_structure(THREE_D_TOPOLOGY)
    assert issues == []
