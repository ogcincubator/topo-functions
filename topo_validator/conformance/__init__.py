#!/usr/bin/env python3

"""Conformance class registry for the topology boundary-block validator."""

from __future__ import annotations

from . import (
    cc01_points,
    cc02_curves,
    cc03_surfaces,
    cc04_shells,
    cc05_solids,
    cc06_relationships,
    cc07_containment,
)

CONFORMANCE_CLASSES = [
    cc01_points,
    cc02_curves,
    cc03_surfaces,
    cc04_shells,
    cc05_solids,
    cc06_relationships,
    cc07_containment,
]


def get_conformance_class(class_id: str):
    """Return the conformance class module for a given class ID.

    Args:
        class_id: Conformance class ID to look up, such as `CC-01`.

    Returns:
        The conformance class module matching `class_id`.

    Raises:
        KeyError: If `class_id` is not a known conformance class ID.
    """
    for cc in CONFORMANCE_CLASSES:
        if cc.CONFORMANCE_CLASS_ID == class_id:
            return cc
    raise KeyError(f"Unknown conformance class: {class_id}")


__all__ = [
    "CONFORMANCE_CLASSES",
    "get_conformance_class",
    "cc01_points",
    "cc02_curves",
    "cc03_surfaces",
    "cc04_shells",
    "cc05_solids",
    "cc06_relationships",
    "cc07_containment",
]
