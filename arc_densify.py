#!/usr/bin/env python3

"""
Convert a circular arc to a sequence of points suitable for GeoJSON,
using a maximum chord-to-arc offset (sagitta) tolerance.

The resulting LineString consists of straight chords whose maximum
departure from the true circular arc does not exceed max_offset.

Coordinates are assumed to be in a projected coordinate system where
the coordinate units are the same as max_offset, e.g. metres.

Example:
    points = densify_arc(
        start=(100.0, 200.0),
        end=(120.0, 220.0),
        centre=(100.0, 220.0),
        max_offset=0.2
    )
"""

from __future__ import annotations

import math
from typing import Literal, Sequence, TypedDict


class ArcStatistics(TypedDict):
    radius: float
    sweep_radians: float
    sweep_degrees: float
    arc_length: float
    number_of_chords: int
    chord_angle_degrees: float
    chord_length: float
    maximum_actual_offset: float


TWO_PI = 2.0 * math.pi
START_POINT = (63613.434, 351126.454)
END_POINT = (63588.775, 351133.133)
CENTRE_POINT = (63628.464, 351230.815)
RADIUS = 105.438
MAX_OFFSET = 0.05
ARC_DIRECTION = "cw"
RADIUS_TOLERANCE = 0.005


Point = tuple[float, float]
ArcDirection = Literal["cw", "ccw"]


def distance(p1: Sequence[float], p2: Sequence[float]) -> float:
    """Return the 2D Euclidean distance between two points."""
    return math.hypot(
        p2[0] - p1[0],
        p2[1] - p1[1],
    )


def point_angle(point: Sequence[float], centre: Sequence[float]) -> float:
    """
    Return the angle from centre to point in radians.

    Angle is measured counter-clockwise from the positive X axis and
    normalised to [0, 2*pi).
    """
    angle = math.atan2(
        point[1] - centre[1],
        point[0] - centre[0],
    )

    return angle % (2.0 * math.pi)


def arc_sweep_angle(
        start: Sequence[float],
        end: Sequence[float],
        centre: Sequence[float],
        direction: ArcDirection,
) -> float:
    """
    Return the signed sweep angle of the arc in radians.
    Positive values indicate counter-clockwise rotation.
    Negative values indicate clockwise rotation.
    """
    start_angle = point_angle(start, centre)
    end_angle = point_angle(end, centre)
    ccw = (end_angle - start_angle) % TWO_PI
    cw = ccw - TWO_PI

    direction_map = {
        "ccw": ccw,
        "cw": cw,
    }

    if direction not in direction_map:
        raise ValueError(
            "direction must be 'clockwise' or 'counterclockwise'"
        )

    return direction_map[direction]


def max_chord_angle(radius: float, max_offset: float) -> float:
    """
    Calculate the maximum central angle permitted for one chord.
    The maximum chord-to-arc offset (sagitta) is:
        s = R * (1 - cos(theta / 2))
    Therefore:
        theta = 2 * acos(1 - s / R)
    Returns:
        Maximum central angle in radians.
    """
    if radius <= 0:
        raise ValueError("Radius must be greater than zero.")
    if max_offset <= 0:
        raise ValueError("max_offset must be greater than zero.")
    # If the permitted offset is greater than or equal to the radius,
    # any semicircle can be represented by a single chord.
    if max_offset >= radius:
        return math.pi

    normalized_offset = max_offset / radius
    return _calculate_angle_from_sagitta_ratio(normalized_offset)


def number_of_chords(
    radius: float,
    sweep_angle: float,
    max_offset: float,
) -> int:
    """
    Calculate the number of chords required to represent an arc.

    The number is rounded upward so the maximum sagitta cannot exceed
    max_offset.
    """
    theta_max = max_chord_angle(radius, max_offset)

    return max(
        1,
        math.ceil(abs(sweep_angle) / theta_max),
    )


def densify_arc(
        start: Sequence[float],
        end: Sequence[float],
        centre: Sequence[float],
        max_offset: float,
        direction: ArcDirection,
        radius: float | None = None,
        radius_tolerance: float = 0.005,
) -> list[Point]:
    """
    Convert a circular arc to a sequence of chord vertices.
    Parameters
    ----------
    start:
        Arc start coordinate (x, y).
    end:
        Arc end coordinate (x, y).
    centre:
        Circle centre coordinate (x, y).
    max_offset:
        Maximum permitted chord-to-arc offset (sagitta).
        Must use the same units as the coordinates.
        For projected coordinates in metres:
            max_offset=0.2
        means a maximum offset of 200 mm.
    direction:
        Arc direction:
            "cw"
                Clockwise arc.
            "ccw"
                Counter-clockwise arc.
    radius:
        Optional expected radius from survey data. If provided, computed radii
        from start and end points are validated against this value. Useful when
        working with adjusted survey data where start/end points may not lie
        exactly on the same circle.
    radius_tolerance:
        Allowed difference between computed and expected radii, or between
        start and end radii when no expected radius is provided.
        Default is 0.005 (5mm for coordinates in metres).
    Returns
    -------
    list[(float, float)]
        Coordinates including the exact supplied start and end points.
        The number of coordinates is:
            number_of_chords + 1
    """
    # validation of input data:
    _validate_coordinate_length(start, "start")
    _validate_coordinate_length(end, "end")
    _validate_coordinate_length(centre, "centre")
    _validate_max_offset(max_offset)
    _validate_radius_tolerance(radius_tolerance)
    _validate_arc_direction(direction)

    start, end, centre = _convert_to_float_points(start, end, centre)
    # Validate coordinates are finite after conversion
    _validate_finite_coordinates(start, "start")
    _validate_finite_coordinates(end, "end")
    _validate_finite_coordinates(centre, "centre")

    validated_radius = _validate_arc_radii(start, end, centre, radius_tolerance, radius)

    sweep = arc_sweep_angle(
        start,
        end,
        centre,
        direction=direction,
    )

    if math.isclose(sweep, 0.0, abs_tol=1e-15):
        return [start, end]

    chord_count = number_of_chords(
        validated_radius,
        sweep,
        max_offset,
    )

    # Before generating points:
    _validate_chord_count(chord_count)

    start_angle = point_angle(start, centre)
    angular_increment = sweep / chord_count

    points: list[Point] = [start]

    for i in range(1, chord_count):
        angle = start_angle + i * angular_increment
        x = centre[0] + validated_radius * math.cos(angle)
        y = centre[1] + validated_radius * math.sin(angle)
        points.append((x, y))

    # Use the supplied coordinate rather than a calculated coordinate
    # so that adjoining topology shares exactly the same endpoint.
    points.append(end)
    return points


def chord_statistics(
        start: Sequence[float],
        end: Sequence[float],
        centre: Sequence[float],
        max_offset: float= 0.05,
        direction: ArcDirection = "cw",
        radius: float | None = None,
        radius_tolerance: float = 0.005,
) -> ArcStatistics:
    """
    Return useful information about arc densification.

    Parameters
    ----------
    start:
        Arc start coordinate (x, y).
    end:
        Arc end coordinate (x, y).
    centre:
        Circle centre coordinate (x, y).
    max_offset:
        Maximum permitted chord-to-arc offset (sagitta).
    direction:
        Arc direction: "cw" or "ccw".
    radius:
        Optional expected radius from survey data.
    radius_tolerance:
        Allowed difference in radii (default: 0.005m = 5mm).

    Returns
    -------
    ArcStatistics
        Dictionary containing arc statistics.
    """
    start_pt, end_pt, centre_pt = _convert_to_float_points(start, end, centre)
    validated_radius = _validate_arc_radii(
        start_pt, end_pt, centre_pt, radius_tolerance, radius
    )
    sweep = arc_sweep_angle(start, end, centre, direction)
    chord_count = number_of_chords(validated_radius, sweep, max_offset)

    chord_angle = abs(sweep) / chord_count
    chord_length, actual_offset = _calculate_chord_properties(validated_radius, chord_angle)
    arc_length = validated_radius * abs(sweep)

    return {
        "radius": validated_radius,
        "sweep_radians": sweep,
        "sweep_degrees": math.degrees(sweep),
        "arc_length": arc_length,
        "number_of_chords": chord_count,
        "chord_angle_degrees": math.degrees(chord_angle),
        "chord_length": chord_length,
        "maximum_actual_offset": actual_offset,
    }


def main() -> None:
    """Run a short demonstration of the arc densification."""

    points = densify_arc(
        start=START_POINT,
        end=END_POINT,
        centre=CENTRE_POINT,
        max_offset=MAX_OFFSET,
        direction=ARC_DIRECTION,
        radius=RADIUS,  # Provide the known curve radius
        radius_tolerance=RADIUS_TOLERANCE  # 5mm tolerance
    )
    stats = chord_statistics(
        start=START_POINT,
        end=END_POINT,
        centre=CENTRE_POINT,
        max_offset=MAX_OFFSET,
        direction=ARC_DIRECTION,
    )

    _print_statistics(stats)
    _print_coordinates(points)


def _calculate_angle_from_sagitta_ratio(normalized_offset: float) -> float:
    """
    Calculate the central angle from normalized sagitta (offset/radius ratio).

    Args:
        normalized_offset: The ratio of sagitta to radius (s/R).

    Returns:
        Central angle in radians: theta = 2 * acos(1 - s/R)
    """
    return 2.0 * math.acos(1.0 - normalized_offset)





def _validate_arc_direction(direction: ArcDirection) -> None:
    """Validate that the arc direction is either 'cw' or 'ccw'."""
    if not isinstance(direction, str):
        raise TypeError(
            f"direction must be a string, got {type(direction).__name__}"
        )
    if direction not in ("cw", "ccw"):
        raise ValueError(
            f"Arc direction must be 'cw' or 'ccw', got {direction!r}"
        )


def _convert_to_float_points(
        start: Sequence[float],
        end: Sequence[float],
        centre: Sequence[float],
) -> tuple[Point, Point, Point]:
    """Convert input sequences to float coordinate tuples.

    Parameters
    ----------
    start:
        Arc start coordinate (x, y).
    end:
        Arc end coordinate (x, y).
    centre:
        Circle centre coordinate (x, y).

    Returns
    -------
    tuple[Point, Point, Point]
        Converted start, end, and centre points as float tuples.
    """
    return (
        (float(start[0]), float(start[1])),
        (float(end[0]), float(end[1])),
        (float(centre[0]), float(centre[1])),
    )


def _validate_arc_radii(
        start: Point,
        end: Point,
        centre: Point,
        radius_tolerance: float,
        expected_radius: float | None = None,
) -> float:
    """Validate arc start and end radii and return the radius to use.

    Parameters
    ----------
    start:
        Arc start coordinate (x, y).
    end:
        Arc end coordinate (x, y).
    centre:
        Circle centre coordinate (x, y).
    radius_tolerance:
        Allowed difference between computed and expected radii.
    expected_radius:
        Optional expected radius from survey data. If provided, computed radii
        are validated against this value rather than against each other.

    Returns
    -------
    float
        The radius to use: expected_radius if provided and valid, otherwise
        the average of computed start and end radii.

    Raises
    ------
    ValueError
        If the start or end point coincides with the centre, or if the computed
        radius differs from the expected radius beyond tolerance.
    """
    start_radius = distance(start, centre)
    end_radius = distance(end, centre)

    if start_radius == 0:
        raise ValueError(
            "Arc start point cannot coincide with the centre."
        )

    if end_radius == 0:
        raise ValueError(
            "Arc end point cannot coincide with the centre."
        )

    # When an expected radius is provided, validate both computed radii against it
    if expected_radius is not None:
        if expected_radius <= 0:
            raise ValueError(
                f"Expected radius must be positive, got {expected_radius:.6f}"
            )

        start_difference = abs(start_radius - expected_radius)
        end_difference = abs(end_radius - expected_radius)

        if start_difference > radius_tolerance:
            raise ValueError(
                f"Start point radius differs from expected radius by "
                f"{start_difference:.6f} (tolerance: {radius_tolerance:.6f}): "
                f"computed={start_radius:.6f}, expected={expected_radius:.6f}"
            )

        if end_difference > radius_tolerance:
            raise ValueError(
                f"End point radius differs from expected radius by "
                f"{end_difference:.6f} (tolerance: {radius_tolerance:.6f}): "
                f"computed={end_radius:.6f}, expected={expected_radius:.6f}"
            )

        return expected_radius

    # Original behaviour: validate start and end radii against each other
    if not math.isclose(
            start_radius,
            end_radius,
            rel_tol=0.0,
            abs_tol=radius_tolerance,
    ):
        raise ValueError(
            "Start and end points are not on the same circle: "
            f"start radius={start_radius:.4f}, "
            f"end radius={end_radius:.4f}"
        )

    return (start_radius + end_radius) / 2.0


def _calculate_chord_properties(
        radius: float,
        chord_angle: float,
) -> tuple[float, float]:
    """Calculate chord length and actual offset from radius and angle."""
    chord_length = 2.0 * radius * math.sin(chord_angle / 2.0)
    actual_offset = radius * (1.0 - math.cos(chord_angle / 2.0))
    return chord_length, actual_offset


def _print_statistics(stats: ArcStatistics) -> None:
    """Print arc statistics in a formatted table."""
    print("Arc statistics")
    print("--------------")
    for key, value in stats.items():
        print(f"{key}: {value}")


def _print_coordinates(points: list) -> None:
    """Print coordinate points."""
    print("\nCoordinates")
    print("-----------")
    for point in points:
        print(point)


def _validate_coordinate_length(
    coord: Sequence[float],
    coord_name: str,
) -> None:
    """Validate that a coordinate has exactly 2 elements (x, y)."""
    if len(coord) < 2:
        raise ValueError(
            f"{coord_name} coordinate must have at least 2 elements (x, y), "
            f"got {len(coord)} element(s): {list(coord)}"
        )
    if len(coord) > 2:
        # Warning: extra dimensions will be ignored
        pass  # Could log a warning in production


def _validate_finite_value(value: float, param_name: str) -> None:
    """Validate that a numeric value is finite (not NaN or inf)."""
    if math.isnan(value):
        raise ValueError(f"{param_name} cannot be NaN")
    if math.isinf(value):
        raise ValueError(f"{param_name} cannot be infinite")


def _validate_max_offset(max_offset: float) -> None:
    """Validate that max_offset is positive and reasonable."""
    if math.isnan(max_offset) or math.isinf(max_offset):
        raise ValueError("max_offset must be a finite number")
    if max_offset <= 0:
        raise ValueError(f"max_offset must be positive, got {max_offset}")
    # Optional: warn about unreasonably large values
    # This prevents excessive memory usage from too many chords


def _validate_radius_tolerance(radius_tolerance: float) -> None:
    """Validate that radius_tolerance is non-negative and reasonable."""
    if math.isnan(radius_tolerance) or math.isinf(radius_tolerance):
        raise ValueError("radius_tolerance must be a finite number")
    if radius_tolerance < 0:
        raise ValueError(
            f"radius_tolerance must be non-negative, got {radius_tolerance}"
        )
    # Optional upper-bound check for sanity
    if radius_tolerance > 1000:  # Configurable threshold
        # Could warn: tolerance seems unreasonably large
        pass


def _validate_distinct_points(
    start: Point,
    end: Point,
    tolerance: float = 1e-10,
) -> None:
    """Warn or validate that start and end points are distinct enough."""
    dist = distance(start, end)
    if dist < tolerance:
        # For production: might want to return early or warn instead of raising
        raise ValueError(
            f"Start and end points are nearly identical "
            f"(distance: {dist:.10f}), which may indicate a data error"
        )


def _validate_chord_count(chord_count: int, max_chords: int = 100000) -> None:
    """Validate that chord count is reasonable to prevent memory issues."""
    if chord_count > max_chords:
        raise ValueError(
            f"Arc densification would require {chord_count} chords, "
            f"exceeding maximum of {max_chords}. "
            f"Consider increasing max_offset or checking input parameters."
        )

def _validate_finite_coordinates(point: Point, coord_name: str) -> None:
    """Validate that both x and y coordinates are finite (not NaN or inf)."""
    _validate_finite_value(point[0], f"{coord_name}[0]")
    _validate_finite_value(point[1], f"{coord_name}[1]")


if __name__ == "__main__":
    main()