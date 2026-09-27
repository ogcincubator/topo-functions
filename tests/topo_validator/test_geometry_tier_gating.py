"""Tests for geometry-tier-aware conformance dispatch.

`validate_topology` previously ran every CC-01..CC-07 conformance class
unconditionally, regardless of what geometry the dataset actually declared.
For most classes an absent tier just meant a silent no-op (zero solids ->
zero solid-relationship issues, indistinguishable from "checked and
clean"). For CC-04's TR-18 (`NoDanglingFaces`) it was worse: a dataset with
faces but no shells at all had *every* face flagged `DANGLING_FACE`, since
no shell existed to reference any of them -- a false failure for perfectly
valid points/edges/rings/faces-only content.

`_present_geometry_tiers` now gates each conformance class (and the
TR-28/TR-29 declared-relationship pair) on whether its required tier is
actually present, replacing both the false failure and the silent no-op
with an explicit, non-silent `GEOMETRY_TIER_NOT_TESTED` warning.
"""

from __future__ import annotations

from conftest import cube_data, merge_datasets

from topo_validator.model import errors_only
from topo_validator.report import rule_results
from topo_validator.validator import _present_geometry_tiers, validate_topology

NOT_TESTED = "GEOMETRY_TIER_NOT_TESTED"


def _rule_ids(issues: list[dict]) -> set[str]:
    return {
        rule_id
        for issue in issues
        if issue["code"] == NOT_TESTED
        for rule_id in issue["extra"]["rule_ids"]
    }


# ---------------------------------------------------------------------------
# _present_geometry_tiers: direct unit tests
# ---------------------------------------------------------------------------


def test_present_geometry_tiers_all_present():
    data = {
        "points": [{"id": "p1"}],
        "curves": [{"id": "c1"}],
        "surfaces": [{"id": "s1"}],
        "solids": [
            {
                "id": "sol1",
                "shells": [{"type": "outer", "faces": ["s1"], "face_orientations": {}}],
            }
        ],
    }

    assert _present_geometry_tiers(data) == {
        "points": True,
        "curves": True,
        "surfaces": True,
        "shells": True,
        "solids": True,
    }


def test_present_geometry_tiers_all_absent():
    data = {"points": [], "curves": [], "surfaces": [], "solids": []}

    assert _present_geometry_tiers(data) == {
        "points": False,
        "curves": False,
        "surfaces": False,
        "shells": False,
        "solids": False,
    }


def test_present_geometry_tiers_shells_detected_via_surface_only_shell_refs():
    """A surface-only shell (no owning solid) still counts as shells present
    -- this is exactly the ground-surface-shell case `surface_shell_face_refs`
    exists for."""
    data = {
        "points": [],
        "curves": [],
        "surfaces": [{"id": "s1"}],
        "solids": [],
        "surface_shell_face_refs": [{"ref": "s1", "shell_id": "sh1"}],
    }

    assert _present_geometry_tiers(data)["shells"] is True


def test_present_geometry_tiers_shells_detected_via_solid_embedded_shells():
    """A shell embedded directly in a solid's own `shells` field (the shape
    hand-built test fixtures commonly use, bypassing the loader and its
    `surface_shell_face_refs` bookkeeping) must also count -- otherwise
    every conftest-style cube fixture would look shell-less."""
    data = {
        "points": [],
        "curves": [],
        "surfaces": [{"id": "s1"}],
        "solids": [
            {"id": "sol1", "shells": [{"type": "outer", "faces": ["s1"], "face_orientations": {}}]}
        ],
    }

    assert _present_geometry_tiers(data)["shells"] is True


# ---------------------------------------------------------------------------
# End-to-end: a faces-only dataset (points/edges/rings/faces, no
# shells/solids) must not misfire TR-18, and must explicitly report every
# skipped rule.
# ---------------------------------------------------------------------------


def test_faces_only_dataset_skips_shell_and_solid_rules_without_false_failures(unit_cube):
    """`unit_cube` is a fully valid, closed solid; dropping its `solids` list
    (and never populating `surface_shell_face_refs`, since this bypasses the
    loader) reproduces the exact reported scenario: points, edges, rings,
    and faces present, nothing shell- or solid-level at all."""
    data = {**unit_cube, "solids": []}

    issues = validate_topology(data)

    assert errors_only(issues) == []
    codes = [issue["code"] for issue in issues]
    assert "DANGLING_FACE" not in codes
    assert "OPEN_SOLID_SHELL" not in codes

    assert _rule_ids(issues) == {
        "TR-06", "TR-18",                                          # CC-04
        "TR-07", "TR-19", "TR-24", "TR-25", "TR-26", "TR-27",      # CC-05
        "TR-08", "TR-10",                                          # CC-06
        "TR-09", "TR-20", "TR-21",                                 # CC-07
        "TR-28", "TR-29",                                          # declared parcel-relationship checks
    }


def test_full_dataset_runs_every_geometry_class_normally(unit_cube):
    """The common case is unaffected: a fully valid cube (points, curves,
    surfaces, and a solid with an embedded shell) has every geometry tier
    present, so CC-01..CC-07 -- and TR-28/TR-29 -- all run as before,
    producing zero notices. `unit_cube` declares no PrimaryParcel surface at
    all, but that's not a missing geometry tier -- it's ordinary,
    non-cadastral content, the same as most files -- so it must not be
    reported as not-tested either (see
    `test_declared_relationship_rules_stay_silent_with_no_parcel_content`)."""
    issues = validate_topology(unit_cube)

    assert errors_only(issues) == []
    assert [issue["code"] for issue in issues if issue["code"] == NOT_TESTED] == []


def test_declared_relationship_rules_stay_silent_with_no_parcel_content(unit_cube):
    """No PrimaryParcel surface declared anywhere is deliberately *not* a
    GEOMETRY_TIER_NOT_TESTED case, unlike "no solids present" below -- it's
    cadastral parcel content being out of scope for this dataset entirely,
    the same situation TR-20/TR-21 are already silent about when there are
    no easements/thematic solids. Flagging it would misreport the
    overwhelmingly common case (an ordinary, non-cadastral file) as
    something left untested."""
    issues = validate_topology(unit_cube)

    not_tested_rule_ids = _rule_ids(issues)
    assert "TR-28" not in not_tested_rule_ids
    assert "TR-29" not in not_tested_rule_ids


def test_declared_relationship_rules_report_not_tested_with_no_solids():
    """"No solids present" is a genuine geometry-tier absence -- the
    dataset may well intend cadastral relationships, it just has no solids
    yet for them to attach to -- so, unlike the no-parcel-content case
    above, this one does get an explicit notice."""
    data = {"points": [], "curves": [], "surfaces": [], "solids": []}

    issues = validate_topology(data)

    not_tested = [issue for issue in issues if issue["code"] == NOT_TESTED]
    matching = [
        issue
        for issue in not_tested
        if issue["extra"]["rule_ids"] == ["TR-28", "TR-29"]
    ]
    assert len(matching) == 1
    assert "no solids present" in matching[0]["message"]


def test_report_rule_results_show_not_tested_status_not_a_false_pass(unit_cube):
    """A rule whose class was skipped must render `NOT_TESTED` in the main
    rule-results table, not `PASS` -- a reader scanning only that table
    (without noticing the separate "Not tested" section) must not be able
    to mistake "never ran" for "ran and found nothing"."""
    data = {**unit_cube, "solids": []}

    issues = validate_topology(data)
    not_tested_rule_ids = _rule_ids(issues)
    results_by_id = {result["id"]: result for result in rule_results(issues, not_tested_rule_ids)}

    for rule_id in ("TR-06", "TR-18", "TR-07", "TR-08", "TR-09", "TR-28"):
        assert results_by_id[rule_id]["status"] == "NOT_TESTED"

    # Rules whose tier *is* present still report normally.
    for rule_id in ("TR-01", "TR-04"):
        assert results_by_id[rule_id]["status"] == "PASS"


def test_shells_present_but_a_face_genuinely_dangling_still_fails():
    """The gate only suppresses TR-18 when shells are entirely absent -- a
    dataset that does declare shells, but where a face is genuinely
    unreferenced by any of them, must still fail."""
    solid = cube_data("", 0, 0, 0, 1, 1, 1)
    orphan_face = {
        "id": "orphan-face",
        "rings": [
            {
                "type": "outer",
                "members": [
                    {"ref": e, "orientation": "+"}
                    for e in ("e0",)
                ],
            }
        ],
    }
    data = merge_datasets(solid, {"points": [], "curves": [], "surfaces": [orphan_face], "solids": []})

    issues = validate_topology(data)

    assert any(issue["code"] == "DANGLING_FACE" for issue in issues)
