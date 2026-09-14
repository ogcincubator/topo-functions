"""Tests for the TR-28/TR-29 shared engine's `target_feature_type` parameter.

`_parcel_surfaces_by_id`/`_validate_declared_parcel_relationship`
(`topo_validator.conformance.cc07_containment`) used to hardcode the pool of
resolvable declared-relationship targets to 2D surfaces whose `feature_type`
is exactly `"PrimaryParcel"`, regardless of what `targetFeatureType` a
solid's own relationship declared. That meant the shared engine could only
ever resolve against Primary Parcels, even though the relationship-lookup
step itself (matching `role` against a solid's `relationships`) was already
generic. This file proves the engine now resolves against whichever
`target_feature_type` is passed in, using a non-parcel example
(`"Building"`) to make clear the mechanism carries no parcel-specific
knowledge -- both TR-28 and TR-29 still pass `PRIMARY_PARCEL_FEATURE_TYPE`
explicitly at their own call sites, so their behaviour is unchanged.
"""

from __future__ import annotations

from typing import Any

from topo_validator.conformance.cc07_containment import (
    _parcel_surfaces_by_id,
    _validate_declared_parcel_relationship,
)


def _surface(surface_id: str, feature_type: str) -> dict[str, Any]:
    return {"id": surface_id, "feature_type": feature_type, "rings": []}


def _solid(solid_id: str, target_href: str, target_feature_type: str) -> dict[str, Any]:
    return {
        "id": solid_id,
        "faces": [],
        "parcel_type": "primary",
        "relationships": [
            {
                "href": target_href,
                "rel": "topology",
                "role": "hasHost",
                "targetFeatureType": target_feature_type,
            }
        ],
    }


def test_parcel_surfaces_by_id_filters_by_the_requested_feature_type():
    topology_2d = {
        "surfaces": [
            _surface("parcel-1", "PrimaryParcel"),
            _surface("building-1", "Building"),
        ]
    }

    assert set(_parcel_surfaces_by_id(topology_2d, "PrimaryParcel")) == {"parcel-1"}
    assert set(_parcel_surfaces_by_id(topology_2d, "Building")) == {"building-1"}
    assert _parcel_surfaces_by_id(topology_2d, "Easement") == {}


def test_declared_relationship_resolves_against_a_non_primary_parcel_target_type():
    """A relationship declaring `targetFeatureType: "Building"` resolves
    cleanly when the engine is asked to resolve against `"Building"` -- no
    UNKNOWN_PARCEL_REFERENCE, even though the referenced surface is not a
    PrimaryParcel."""
    topology_3d = {"solids": [_solid("s1", "building-1", "Building")]}
    topology_2d = {"surfaces": [_surface("building-1", "Building")]}

    issues = _validate_declared_parcel_relationship(
        topology_3d,
        topology_2d,
        {"primary"},
        "Building",
        "hasHost",
        "MISSING",
        "MULTIPLE",
        "UNKNOWN",
        "MISMATCH",
        "NOT_WITHIN",
    )

    assert issues == []


def test_declared_relationship_does_not_fall_back_to_primary_parcel_surfaces():
    """The same solid/relationship, but the engine is asked to resolve
    against `"PrimaryParcel"` -- and although the dataset does declare a
    PrimaryParcel surface (so the rule doesn't just skip for lack of any
    surface of that type), it isn't the one the solid's relationship
    references. Proves the target type is a real constraint on the
    resolution pool, not ignored: the relationship's href must not be found
    among surfaces of a different actual type."""
    topology_3d = {"solids": [_solid("s1", "building-1", "Building")]}
    topology_2d = {
        "surfaces": [
            _surface("building-1", "Building"),
            _surface("parcel-1", "PrimaryParcel"),
        ]
    }

    issues = _validate_declared_parcel_relationship(
        topology_3d,
        topology_2d,
        {"primary"},
        "PrimaryParcel",
        "hasHost",
        "MISSING",
        "MULTIPLE",
        "UNKNOWN",
        "MISMATCH",
        "NOT_WITHIN",
    )

    assert [issue["code"] for issue in issues] == ["UNKNOWN"]
    assert issues[0]["extra"]["href"] == "building-1"
