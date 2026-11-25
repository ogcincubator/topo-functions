"""Physical orientation: the ``place`` CRS's axis order, and the shell check.

A face's outward side is physical: it is given by the right-hand rule in east, north,
up, so it does not depend on the order of the ``place`` CRS's axes.
CRS axes are in their EPSG order, east-north for some projections (MGA) and
north-east for others (NZTM); a north-east-up system is left-handed, so the right-hand
rule applied to its coordinates as stored would give the opposite side.
The caller states the axis order (``place_axes``); the library cannot look a CRS up.
JSON-FG 1.0.0 states its orientation requirements for a right-handed Cartesian
system, all face normals pointing away from the solid's material, which is the same
reading.

:func:`check_orientation` checks that, in east, north, up, each exterior shell's faces
(after the ring, face and shell orientations are composed) enclose a positive volume
and each void's a negative one, so every normal points away from the material.
It catches data written with the other reading and a wrongly stated ``place_axes``.
"""

from __future__ import annotations

from typing import Callable, Sequence

from .diagnostics import EncodingError, Problem
from .traverse import ShellView, Traversal

# |volume| at or below this fraction of the cube of a shell's largest extent counts
# as no volume.
VOLUME_TOLERANCE = 1e-9

# Each horizontal axis direction as (index in east-north-up, sign).
_DIRECTIONS = {"east": (0, 1), "west": (0, -1), "north": (1, 1), "south": (1, -1)}
PLACE_AXES = tuple(
    f"{a}-{b}-up"
    for a in _DIRECTIONS
    for b in _DIRECTIONS
    if _DIRECTIONS[a][0] != _DIRECTIONS[b][0]
)

ToEnu = Callable[[Sequence[float]], list[float]]


def axes(place_axes: str) -> ToEnu:
    """A function from ``place`` coordinates in the stated axis order to
    (east, north, up)."""
    if place_axes not in PLACE_AXES:
        raise ValueError(
            f"place_axes must be one of {', '.join(PLACE_AXES)}, not {place_axes!r}"
        )
    first, second, _ = place_axes.split("-")
    (i, si), (j, sj) = _DIRECTIONS[first], _DIRECTIONS[second]

    def to_enu(coordinates: Sequence[float]) -> list[float]:
        enu = [0.0, 0.0, coordinates[2]]
        enu[i] = si * coordinates[0]
        enu[j] = sj * coordinates[1]
        return enu

    return to_enu


def check_orientation(traversal: Traversal, to_enu: ToEnu) -> None:
    """Raise :class:`EncodingError` unless every shell's normals point away from its
    solid's material in east, north, up.

    ``SHELL_INSIDE_OUT``: an exterior shell encloses a negative volume, or a void a
    positive one; ``SHELL_WITHOUT_VOLUME``: a shell encloses no volume.
    Each shell is reported once, even when several solids use it.
    """
    problems: list[Problem] = []
    for solid in traversal.solids:
        for shell in solid.shells:
            problem = _shell_problem(traversal, shell, to_enu)
            if problem is not None and problem not in problems:
                problems.append(problem)
    if problems:
        raise EncodingError(problems)


def _shell_problem(traversal: Traversal, shell: ShellView, to_enu: ToEnu):
    loops = [
        [to_enu(traversal.points[p].place) for p in loop]
        for face in shell.faces
        for loop in face.loops
    ]
    volume, extent = shell_volume(loops)
    if abs(volume) <= VOLUME_TOLERANCE * extent**3:
        return Problem("SHELL_WITHOUT_VOLUME", "shell encloses no volume", shell.id)
    expected = 1 if shell.role == "exterior" else -1
    if volume * expected < 0:
        side = "into the solid" if shell.role == "exterior" else "into the material"
        return Problem(
            "SHELL_INSIDE_OUT",
            f"{shell.role} shell's normals point {side} in east, north, up",
            shell.id,
        )
    return None


def shell_volume(
    loops: Sequence[Sequence[Sequence[float]]],
) -> tuple[float, float]:
    """The signed volume enclosed by a shell's loops in a right-handed system,
    positive when their normals point outward, and the shell's largest extent.

    Each loop contributes a third of its centroid's dot product with its vector area
    (Newell), taken about the shell's centroid so that large projected coordinates do
    not lose precision; a hole's loop, wound the other way, subtracts its area.
    """
    vertices = [p for loop in loops for p in loop]
    origin = [sum(p[i] for p in vertices) / len(vertices) for i in range(3)]
    extent = max(
        max(p[i] for p in vertices) - min(p[i] for p in vertices) for i in range(3)
    )
    volume = 0.0
    for loop in loops:
        points = [[p[i] - origin[i] for i in range(3)] for p in loop]
        n = len(points)
        centroid = [sum(p[i] for p in points) / n for i in range(3)]
        area = [0.0, 0.0, 0.0]
        for a, b in zip(points, points[1:] + points[:1]):
            area[0] += 0.5 * (a[1] * b[2] - a[2] * b[1])
            area[1] += 0.5 * (a[2] * b[0] - a[0] * b[2])
            area[2] += 0.5 * (a[0] * b[1] - a[1] * b[0])
        volume += sum(c * s for c, s in zip(centroid, area)) / 3.0
    return volume, extent
