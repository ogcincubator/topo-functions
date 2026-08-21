#!/usr/bin/env python3

"""Conformance checks for CC-04 shell topology.

This module validates shell-level topology rules for a `TopologyData` instance,
including closed solids and dangling face detection.
"""

from __future__ import annotations

from collections import defaultdict

from ..model import (
    Issue,
    Solid,
    Surface,
    Tolerances,
    TopologyData,
    build_indexes,
    err,
)

CONFORMANCE_CLASS_ID = "CC-04"
CONFORMANCE_CLASS_NAME = "Shell topology"
RULE_IDS = ["TR-06", "TR-18"]

OPEN_SOLID_SHELL_CODE = "OPEN_SOLID_SHELL"
MAX_OPEN_CURVES_IN_ISSUE = 10
DANGLING_FACE_CODE = "DANGLING_FACE"


# ---------------------------------------------------------------------------
# TR-06  Closed solid
# ---------------------------------------------------------------------------


def _surface_curve_references(surface: Surface) -> list[str]:
    """Return curve ids referenced by all rings in a surface."""
    curve_references: list[str] = []

    for ring in surface.get("rings", []):
        for member in ring.get("members", []):
            curve_references.append(member["ref"])

    return curve_references


def _count_solid_shell_curve_references(
    solid: Solid,
    surfaces: dict[str, Surface],
) -> dict[str, int]:
    """Count how many times each curve is referenced by a solid's faces."""
    curve_reference_counts: dict[str, int] = defaultdict(int)

    for face_id in solid.get("faces", []):
        surface = surfaces.get(face_id)
        if surface is None:
            continue

        for curve_id in _surface_curve_references(surface):
            curve_reference_counts[curve_id] += 1

    return curve_reference_counts


def _open_curve_ids(curve_reference_counts: dict[str, int]) -> list[str]:
    """Return curve ids whose reference count does not satisfy closed-shell rules."""
    return [
        curve_id
        for curve_id, reference_count in curve_reference_counts.items()
        if reference_count != 2
    ]


def _open_solid_shell_issue(solid_id: str, open_curves: list[str]) -> Issue:
    """Create a TR-06 issue for a solid shell that is not closed."""
    return err(
        OPEN_SOLID_SHELL_CODE,
        f"Solid {solid_id} shell is not closed: "
        f"{len(open_curves)} curve(s) do not appear exactly twice",
        object_id=solid_id,
        extra={
            "open_curve_count": len(open_curves),
            "open_curves": open_curves[:MAX_OPEN_CURVES_IN_ISSUE],
        },
    )


def validate_closed_solid(
    data: TopologyData,
) -> list[Issue]:
    """
    TR-06: the shell of a solid must be a closed 2-manifold.
    In a closed shell every curve is used by the solid's faces exactly
    twice (once in each direction). A count other than 2 means the shell
    has a gap or a hole.
    """
    issues: list[Issue] = []
    surfaces = build_indexes(data)["surfaces"]

    for solid in data.get("solids", []):
        solid_id = solid["id"]
        curve_reference_counts = _count_solid_shell_curve_references(solid, surfaces)
        open_curves = _open_curve_ids(curve_reference_counts)

        if open_curves:
            issues.append(_open_solid_shell_issue(solid_id, open_curves))

    return issues


# ---------------------------------------------------------------------------
# TR-18  No dangling faces
# ---------------------------------------------------------------------------


def _referenced_face_ids(data: TopologyData) -> set[str]:
    """Return all face ids referenced by solids in the topology dataset."""
    return {
        face_id
        for solid in data.get("solids", [])
        for face_id in solid.get("faces", [])
    }


def _dangling_face_issue(surface_id: str) -> Issue:
    """Create a TR-18 issue for a surface that is not owned by any solid."""
    return err(
        DANGLING_FACE_CODE,
        f"Surface {surface_id} is not referenced by any solid",
        object_id=surface_id,
    )


def validate_no_dangling_faces(
    data: TopologyData,
) -> list[Issue]:
    """
    TR-18: every surface (face) must be referenced by at least one solid shell.

    A face that no solid owns cannot form part of any closed shell and is
    topologically orphaned.
    """
    referenced_faces = _referenced_face_ids(data)
    issues: list[Issue] = []

    for surface in data.get("surfaces", []):
        surface_id = surface["id"]
        if surface_id not in referenced_faces:
            issues.append(_dangling_face_issue(surface_id))

    return issues


def validate(data: TopologyData, tolerances: Tolerances | None = None) -> list[Issue]:
    """Validate CC-04 shell topology rules.

    Args:
        data: Topology data to validate.
        tolerances: Optional tolerance overrides. Present for interface
            consistency; this validator does not currently use them.

    Returns:
        A list of validation issues found in `data`.
    """
    issues: list[Issue] = []
    issues.extend(validate_closed_solid(data))
    issues.extend(validate_no_dangling_faces(data))
    return issues
