"""Command-line tools: ``topo2jsonfg`` and ``topo2geojson-faces``.

Both read a Topo Feature document (a file, or ``-`` for standard input) and write the
encoding to standard output or ``--output``.
Exit status: 0 written; 1 the document cannot be encoded (each problem is printed to
standard error); 2 wrong arguments.
Both need the ``place`` CRS's axis order (``--place-axes``) for the orientation check,
which ``--no-orientation-check`` turns off.
``topo2geojson-faces`` takes heights only from ``geometry`` stated to be ellipsoidal:
the library has no geoid grids, so a transformation from ``place`` needs the library
called from Python with a transformer.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Callable, Optional, Sequence

from .diagnostics import EncodingError
from .geojson_faces import ELLIPSOIDAL_GEOMETRY, to_geojson_faces
from .jsonfg_write import FEATURES, to_jsonfg
from .orientation import PLACE_AXES
from .transform import dumps


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("input", help="Topo Feature JSON document, or - for stdin")
    parser.add_argument("-o", "--output", help="output file (default: stdout)")
    parser.add_argument("--name", help="collection name (default: from the document)")
    parser.add_argument(
        "--place-axes",
        required=True,
        choices=PLACE_AXES,
        help="axis order of the place CRS (EPSG order), for orientation",
    )
    parser.add_argument(
        "--no-orientation-check",
        dest="check_orientation",
        action="store_false",
        help="encode shells whose normals do not face away from the material",
    )


def _run(args: argparse.Namespace, encode: Callable[[dict], dict]) -> int:
    try:
        if args.input == "-":
            doc = json.load(sys.stdin)
        else:
            with open(args.input, encoding="utf-8") as handle:
                doc = json.load(handle)
    except (OSError, ValueError) as exc:
        print(f"cannot read {args.input}: {exc}", file=sys.stderr)
        return 1
    try:
        text = dumps(encode(doc))
    except EncodingError as exc:
        print(f"cannot encode {args.input}: {exc}", file=sys.stderr)
        return 1
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)
    return 0


def topo2jsonfg(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="topo2jsonfg",
        description="Topo Feature solids to JSON-FG 1.0.0 polyhedra, in place.",
    )
    _common(parser)
    parser.add_argument(
        "--crs",
        action="append",
        required=True,
        metavar="URI",
        help="the place CRS URI; repeat for a compound CRS (horizontal, vertical)",
    )
    parser.add_argument("--features", choices=FEATURES, default="per-solid")
    args = parser.parse_args(argv)
    crs = args.crs[0] if len(args.crs) == 1 else args.crs
    return _run(
        args,
        lambda doc: to_jsonfg(
            doc,
            crs=crs,
            place_axes=args.place_axes,
            features=args.features,
            name=args.name,
            check_orientation=args.check_orientation,
        ),
    )


def topo2geojson_faces(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="topo2geojson-faces",
        description="Topo Feature solids to per-face GeoJSON (RFC 7946).",
    )
    _common(parser)
    parser.add_argument(
        "--heights",
        required=True,
        choices=[ELLIPSOIDAL_GEOMETRY],
        help="state that the document's geometry heights are ellipsoidal",
    )
    args = parser.parse_args(argv)
    return _run(
        args,
        lambda doc: to_geojson_faces(
            doc,
            place_axes=args.place_axes,
            heights=args.heights,
            name=args.name,
            check_orientation=args.check_orientation,
        ),
    )


def main_topo2jsonfg() -> None:
    sys.exit(topo2jsonfg())


def main_topo2geojson_faces() -> None:
    sys.exit(topo2geojson_faces())
