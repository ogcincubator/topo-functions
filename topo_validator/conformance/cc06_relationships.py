#!/usr/bin/env python3

"""Conformance checks for CC-06 solid relationship topology.

This module validates solid relationship rules for a `TopologyData` instance,
including overlap prevention and shared-face requirements.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from ..geometry import (
    solid_bbox,
    bbox_strictly_overlaps,
)
from ..model import (
    Issue,
    Solid,
    Tolerances,
    TopologyData,
    build_indexes,
    err,
)

CONFORMANCE_CLASS_ID = "CC-06"
CONFORMANCE_CLASS_NAME = "Solid relationship topology"
RULE_IDS = ["TR-08", "TR-10"]

FACE_ADJACENCY_LIMIT_EXCEEDED_CODE = "FACE_ADJACENCY_LIMIT_EXCEEDED"
MAX_SOLIDS_PER_FACE = 2


# ---------------------------------------------------------------------------
# TR-08  No solid overlap
# ---------------------------------------------------------------------------


def _group_solids_by_theme(solids: list[Solid]) -> dict[str, list[Solid]]:
    """Group solids by theme, using 'default' when no theme is declared."""
    solids_by_theme: dict[str, list[Solid]] = defaultdict(list)

    for solid in solids:
        solids_by_theme[solid.get("theme", "default")].append(solid)

    return solids_by_theme


def _solid_pair_is_exempt_from_overlap(solid_a: Solid, solid_b: Solid) -> bool:
    """Return True when a solid pair is exempt from TR-08 overlap checking."""
    # Exemption 1: parent-child containment is validated by TR-09.
    if solid_a.get("parent_id") == solid_b["id"]:
        return True
    if solid_b.get("parent_id") == solid_a["id"]:
        return True

    # Exemption 2: solids on entirely disjoint levels occupy separate storeys.
    levels_a = set(solid_a.get("levels", []))
    levels_b = set(solid_b.get("levels", []))
    if levels_a and levels_b and levels_a.isdisjoint(levels_b):
        return True

    # Exemption 3: solids sharing boundary faces are topologically adjacent.
    faces_a = set(solid_a.get("faces", []))
    faces_b = set(solid_b.get("faces", []))
    return bool(faces_a & faces_b)


def _solid_overlap_issue(solid_id: str, other_id: str, theme: str) -> Issue:
    """Build a TR-08 solid-overlap issue."""
    return err(
        "SOLID_OVERLAP",
        f"Solids {solid_id} and {other_id} overlap in theme '{theme}'",
        object_id=solid_id,
        extra={
            "other_id": other_id,
            "theme": theme,
        },
    )


def validate_no_solid_overlap(
    data: TopologyData,
) -> list[Issue]:
    """
    TR-08: solids in the same theme must not overlap.

    Overlap is detected by strict AABB intersection. Three categories of
    a pair are exempt from the check:

    1. **Parent–child pairs** – containment is expected and verified by TR-09.
    2. **Disjoint-level pairs** – solids that declare non-overlapping "levels"
       sets occupy separate storeys; a 3-D AABB overlap between them is a
       cross-level artefact, not a genuine violation.
    3. **Topologically adjacent pairs** – solids that share one or more boundary
       faces are properly connected neighbours. Their AABBs will naturally
       overlap at the shared boundary, but the geometry does not intersect.
    """
    issues: list[Issue] = []
    indexes = build_indexes(data)
    surfaces = indexes["surfaces"]
    curves = indexes["curves"]
    points = indexes["points"]

    solids = data.get("solids", [])
    bounding_boxes: dict[str, Optional[tuple[float, ...]]] = {
        solid["id"]: solid_bbox(solid, surfaces, curves, points)
        for solid in solids
    }

    solids_by_theme = _group_solids_by_theme(solids)

    for theme, theme_solids in solids_by_theme.items():
        for i in range(len(theme_solids)):
            for j in range(i + 1, len(theme_solids)):
                solid_a = theme_solids[i]
                solid_b = theme_solids[j]

                if _solid_pair_is_exempt_from_overlap(solid_a, solid_b):
                    continue

                solid_a_id = solid_a["id"]
                solid_b_id = solid_b["id"]
                if not isinstance(solid_a_id, str) or not isinstance(solid_b_id, str):
                    continue

                bbox_a = bounding_boxes.get(solid_a_id)
                bbox_b = bounding_boxes.get(solid_b_id)
                if not bbox_a or not bbox_b:
                    continue

                if bbox_strictly_overlaps(bbox_a, bbox_b):
                    issues.append(_solid_overlap_issue(solid_a_id, solid_b_id, theme))

    return issues


# ---------------------------------------------------------------------------
# TR-10  Shared boundary face consistency
# ---------------------------------------------------------------------------


def _solid_ids_by_face(data: TopologyData) -> dict[str, list[str]]:
    """Return solid ids grouped by referenced face id."""
    face_to_solids: dict[str, list[str]] = defaultdict(list)

    for solid in data.get("solids", []):
        solid_id = solid["id"]
        for face_id in solid.get("faces", []):
            face_to_solids[face_id].append(solid_id)

    return face_to_solids


def _face_adjacency_limit_issue(face_id: str, solid_ids: list[str]) -> Issue:
    """Create a TR-10 issue for a face referenced by too many solids."""
    solid_count = len(solid_ids)

    return err(
        FACE_ADJACENCY_LIMIT_EXCEEDED_CODE,
        f"Face {face_id} is referenced by {solid_count} solids "
        f"(maximum {MAX_SOLIDS_PER_FACE} allowed)",
        object_id=face_id,
        extra={"solid_ids": solid_ids},
    )


def validate_shared_solid_face(
    data: TopologyData,
) -> list[Issue]:
    """
    TR-10: each face may be shared by at most two solids.

    A face shared by three or more solids violates the rule that every
    face is adjacent to at most one neighbour solid.
    """
    issues: list[Issue] = []

    for face_id, solid_ids in _solid_ids_by_face(data).items():
        if len(solid_ids) <= MAX_SOLIDS_PER_FACE:
            continue

        issues.append(_face_adjacency_limit_issue(face_id, solid_ids))

    return issues


def validate(data: TopologyData, tolerances: Tolerances | None = None) -> list[Issue]:
    """Validate CC-06 solid relationship topology rules.

    Args:
        data: Topology data to validate.
        tolerances: Optional tolerance overrides. Present for interface
            consistency; this validator does not currently use them.

    Returns:
        A list of validation issues found in `data`.
    """
    issues: list[Issue] = []
    issues.extend(validate_no_solid_overlap(data))
    issues.extend(validate_shared_solid_face(data))
    return issues
