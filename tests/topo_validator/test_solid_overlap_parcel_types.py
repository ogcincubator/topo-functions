"""Tests for TR-08 (NoSolidOverlap / SOLID_OVERLAP) cadastral parcel-type scoping.

TR-08 previously grouped solids by `theme` alone and tested every pair for
overlap, with no awareness of `parcel_type` -- a Secondary/easement solid
legitimately occupying space already covered by a Primary Parcel (or by
another Secondary solid) was flagged `SOLID_OVERLAP` exactly like two
genuinely colliding Primary Parcels. This file pins the intended cadastral
semantics: TR-08 prohibits interior-volume overlap only between two Primary
Parcel-equivalent solids (`cc06_relationships._is_primary_parcel_solid`);
Secondary/easement/thematic solids are out of scope for *this* rule, but
remain fully subject to every other topology rule (see the TR-07 case below).
"""

from __future__ import annotations

from conftest import cube_data, merge_datasets

from topo_validator.conformance.cc05_solids import validate_positive_volume
from topo_validator.conformance.cc06_relationships import validate_no_solid_overlap


def test_two_disjoint_primary_parcel_solids_pass():
    """Two separated Primary Parcel solids: no overlap, no issues."""
    a = cube_data("a-", 0, 0, 0, 1, 1, 1, theme="parcels", parcel_type="primary")
    b = cube_data("b-", 5, 5, 5, 6, 6, 6, theme="parcels", parcel_type="primary")

    issues = validate_no_solid_overlap(merge_datasets(a, b))

    assert issues == []


def test_two_primary_parcel_solids_touching_on_shared_face_pass():
    """Two Primary Parcel solids sharing a boundary face (adjoining
    cadastral parcels) touch but do not interpenetrate -- valid topology."""
    a = cube_data("a-", 0, 0, 0, 1, 1, 1, theme="parcels", parcel_type="primary")
    b = cube_data("b-", 1, 0, 0, 2, 1, 1, theme="parcels", parcel_type="primary")

    issues = validate_no_solid_overlap(merge_datasets(a, b))

    assert issues == []


def test_two_primary_parcel_solids_with_overlapping_volume_fail():
    """Two Primary Parcel solids with genuine interior-volume overlap must
    be reported -- this is exactly what TR-08 exists to catch."""
    a = cube_data("a-", 0, 0, 0, 2, 2, 2, theme="parcels", parcel_type="primary")
    b = cube_data("b-", 1, 1, 1, 3, 3, 3, theme="parcels", parcel_type="primary")

    issues = validate_no_solid_overlap(merge_datasets(a, b))

    assert [issue["code"] for issue in issues] == ["SOLID_OVERLAP"]


def test_overlapping_aggregate_member_candidates_still_fail():
    """`SolidAggregate` membership is not an overlap exemption.

    Two solids a producer intends to combine into one `SolidAggregate` are
    still individually Primary Parcel geometry (`topo-solid-aggregate`
    describes `SolidAggregate` as a plain, unordered list of member Solid
    ids with no boundary geometry of its own -- it carries no cadastral
    semantics that would license overlap). Only touching/adjacency (the
    shared-face case above) is a valid aggregate relationship; genuine
    interior-volume overlap between members must still be flagged.
    """
    member_a = cube_data("agg-a-", 0, 0, 0, 2, 1, 1, theme="parcels", parcel_type="primary")
    member_b = cube_data("agg-b-", 1, 0, 0, 3, 1, 1, theme="parcels", parcel_type="primary")

    issues = validate_no_solid_overlap(merge_datasets(member_a, member_b))

    assert [issue["code"] for issue in issues] == ["SOLID_OVERLAP"]


def test_primary_parcel_overlapping_secondary_parcel_solid_passes():
    """A Secondary/easement solid (e.g. a volumetric interest) legitimately
    occupying space already covered by a Primary Parcel must not trip the
    generic TR-08 overlap rule."""
    primary = cube_data("p-", 0, 0, 0, 2, 2, 2, theme="parcels", parcel_type="primary")
    secondary = cube_data(
        "s-", 1, 1, 1, 1.5, 1.5, 1.5, theme="parcels", parcel_type="secondary"
    )

    issues = validate_no_solid_overlap(merge_datasets(primary, secondary))

    assert issues == []


def test_two_secondary_parcel_solids_overlapping_pass():
    """Two Secondary/easement solids overlapping each other (e.g. two
    distinct rights over the same volume) must not trip TR-08 either."""
    a = cube_data("sa-", 0, 0, 0, 2, 2, 2, theme="parcels", parcel_type="secondary")
    b = cube_data("sb-", 1, 1, 1, 3, 3, 3, theme="parcels", parcel_type="secondary")

    issues = validate_no_solid_overlap(merge_datasets(a, b))

    assert issues == []


def test_secondary_parcel_solid_still_fails_other_applicable_rules():
    """Exempting Secondary solids from TR-08 must not exempt them from
    topology rules in general: a Secondary solid with a non-positive
    declared volume must still fail TR-07."""
    secondary = cube_data(
        "bad-", 0, 0, 0, 1, 1, 1, theme="parcels", parcel_type="secondary", volume=0.0
    )

    overlap_issues = validate_no_solid_overlap(secondary)
    volume_issues = validate_positive_volume(secondary)

    assert overlap_issues == []
    assert [issue["code"] for issue in volume_issues] == ["ZERO_OR_NEGATIVE_VOLUME"]


def test_orphan_solid_without_parcel_type_defaults_to_primary_scope():
    """A solid with no declared `parcel_type` at all (malformed/legacy
    input, or an orphan not yet associated with any cadastral feature) is
    not silently classified as Secondary and exempted -- it defaults to
    Primary-equivalent scope, the same conservative default
    `loader._build_solids` already applies, so it remains subject to the
    overlap prohibition against another Primary Parcel solid."""
    primary = cube_data("op-", 0, 0, 0, 2, 2, 2, theme="parcels", parcel_type="primary")
    orphan = cube_data("oo-", 1, 1, 1, 3, 3, 3, theme="parcels", parcel_type="primary")
    del orphan["solids"][0]["parcel_type"]

    issues = validate_no_solid_overlap(merge_datasets(primary, orphan))

    assert [issue["code"] for issue in issues] == ["SOLID_OVERLAP"]
