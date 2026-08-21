#!/usr/bin/env python3

"""
Create GeoJSON LineString features from circular arcs.

This module uses arc_densify.densify_arc() to convert arc parameters
into a sequence of points, then wraps them in a GeoJSON structure.

Example:
    python src/arc_comps/arc_to_geojson_writer.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from .arc_densify import densify_arc, ArcDirection, Point

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "output"


def arc_to_geojson_feature(
        start: Sequence[float],
        end: Sequence[float],
        centre: Sequence[float],
        max_offset: float,
        direction: ArcDirection,
        *,
        radius: float | None = None,
        radius_tolerance: float = 0.005,
        properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Convert arc parameters to a GeoJSON LineString Feature.

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
    properties:
        Optional properties for the GeoJSON Feature.

    Returns
    -------
    dict[str, Any]
        GeoJSON Feature with LineString geometry representing the arc.
    """
    points = densify_arc(
        start=start,
        end=end,
        centre=centre,
        max_offset=max_offset,
        direction=direction,
        radius=radius,
        radius_tolerance=radius_tolerance,
    )

    arc_properties = _build_arc_properties(
        start=start,
        end=end,
        centre=centre,
        direction=direction,
        max_offset=max_offset,
        point_count=len(points),
        radius=radius,
        properties=properties,
    )

    return _points_to_geojson_linestring(points, properties=arc_properties)


def arc_to_geojson_feature_collection(
        arcs: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Convert multiple arc parameter dictionaries to a GeoJSON FeatureCollection.
    Parameters
    ----------
    arcs:
        List of arc parameter dictionaries. Each must contain:
        - start, end, centre, max_offset, direction
        Optional: radius, radius_tolerance, properties
    Returns
    -------
    dict[str, Any]
        GeoJSON FeatureCollection containing all arc LineStrings.
    """
    features = [_convert_arc_params_to_feature(arc_params) for arc_params in arcs]
    return {
        "type": "FeatureCollection",
        "features": features,
    }


def arc_to_geojson_file(
    start: Sequence[float],
    end: Sequence[float],
    centre: Sequence[float],
    max_offset: float,
    direction: ArcDirection,
    output_path: Path,
    *,
    radius: float | None = None,
    radius_tolerance: float = 0.005,
    properties: dict[str, Any] | None = None,
) -> None:
    """
    Convert arc parameters to GeoJSON and write to a file.

    Parameters
    ----------
    start, end, centre, max_offset, direction, radius, radius_tolerance:
        Arc parameters (see arc_to_geojson_feature).
    output_path:
        Path where the GeoJSON file will be written.
    properties:
        Optional properties for the GeoJSON Feature.
    """
    feature = arc_to_geojson_feature(
        start=start,
        end=end,
        centre=centre,
        max_offset=max_offset,
        direction=direction,
        radius=radius,
        radius_tolerance=radius_tolerance,
        properties=properties,
    )
    write_geojson_file(feature, output_path)


def main() -> None:
    """Demonstrate arc to GeoJSON conversion with example data."""
    parser = argparse.ArgumentParser(
        description="Convert circular arc to GeoJSON LineString."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR / "arc_example.geojson",
        help="Output GeoJSON file path.",
    )
    parser.add_argument(
        "--start",
        type=float,
        nargs=2,
        default=[63613.434, 351126.454],
        help="Start point coordinates (x y).",
    )
    parser.add_argument(
        "--end",
        type=float,
        nargs=2,
        default=[63588.775, 351133.133],
        help="End point coordinates (x y).",
    )
    parser.add_argument(
        "--centre",
        type=float,
        nargs=2,
        default=[63628.464, 351230.815],
        help="Centre point coordinates (x y).",
    )
    parser.add_argument(
        "--radius",
        type=float,
        default=105.437,
        help="Expected arc radius.",
    )
    parser.add_argument(
        "--max-offset",
        type=float,
        default=0.02,
        help="Maximum chord-to-arc offset (sagitta).",
    )
    parser.add_argument(
        "--direction",
        choices=["cw", "ccw"],
        default="cw",
        help="Arc direction.",
    )
    parser.add_argument(
        "--radius-tolerance",
        type=float,
        default=0.005,
        help="Allowed radius tolerance (default: 5mm).",
    )

    args = parser.parse_args()

    arc_to_geojson_file(
        start=args.start,
        end=args.end,
        centre=args.centre,
        max_offset=args.max_offset,
        direction=args.direction,
        output_path=args.output,
        radius=args.radius,
        radius_tolerance=args.radius_tolerance,
        properties={
            "description": "Example arc from survey data",
            "units": "metres",
        },
    )

    print(f"Wrote GeoJSON to: {args.output}")
    print(f"Arc: {args.start} → {args.end}")
    print(f"Radius: {args.radius}m, Direction: {args.direction}")


def write_geojson_file(
    geojson_data: dict[str, Any],
    output_path: Path,
    *,
    indent: int = 2,
) -> None:
    """
    Write GeoJSON data to a file.

    Parameters
    ----------
    geojson_data:
        GeoJSON Feature or FeatureCollection dictionary.
    output_path:
        Path where the GeoJSON file will be written.
    indent:
        JSON indentation level (default: 2).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(geojson_data, indent=indent, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _build_arc_properties(
        start: Sequence[float],
        end: Sequence[float],
        centre: Sequence[float],
        direction: ArcDirection,
        max_offset: float,
        point_count: int,
        radius: float | None = None,
        properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build properties dictionary for arc GeoJSON feature.

    Parameters
    ----------
    start:
        Arc start coordinate (x, y).
    end:
        Arc end coordinate (x, y).
    centre:
        Circle centre coordinate (x, y).
    direction:
        Arc direction: "cw" or "ccw".
    max_offset:
        Maximum permitted chord-to-arc offset (sagitta).
    point_count:
        Number of points in the densified arc.
    radius:
        Optional radius value.
    properties:
        Optional additional properties to merge.

    Returns
    -------
    dict[str, Any]
        Combined properties dictionary for the arc.
    """
    arc_properties = {
        "start": list(start),
        "end": list(end),
        "centre": list(centre),
        "direction": direction,
        "max_offset": max_offset,
        "point_count": point_count,
    }

    if radius is not None:
        arc_properties["radius"] = radius

    if properties:
        arc_properties.update(properties)

    return arc_properties


def _convert_arc_params_to_feature(arc_params: dict[str, Any]) -> dict[str, Any]:
    """
    Convert a single arc parameter dictionary to a GeoJSON Feature.

    Parameters
    ----------
    arc_params:
        Arc parameter dictionary containing required and optional fields.

    Returns
    -------
    dict[str, Any]
        GeoJSON Feature representing the arc.
    """
    return arc_to_geojson_feature(
        start=arc_params["start"],
        end=arc_params["end"],
        centre=arc_params["centre"],
        max_offset=arc_params["max_offset"],
        direction=arc_params["direction"],
        radius=arc_params.get("radius"),
        radius_tolerance=arc_params.get("radius_tolerance", 0.005),
        properties=arc_params.get("properties"),
    )


def _points_to_geojson_linestring(
    points: list[Point],
    *,
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Convert a list of points to a GeoJSON LineString Feature.

    Parameters
    ----------
    points:
        List of (x, y) coordinate tuples.
    properties:
        Optional properties dictionary for the GeoJSON Feature.

    Returns
    -------
    dict[str, Any]
        GeoJSON Feature with LineString geometry.
    """
    return {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [[x, y] for x, y in points],
        },
        "properties": properties or {},
    }


if __name__ == "__main__":
    main()
