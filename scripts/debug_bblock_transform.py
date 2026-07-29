"""
debug_bblock_transform.py
==========================
Debug harness that invokes topo2geojson's OGC Building Blocks transform the
same way a bblocks-postprocess host does — via the `Topo2GeoJsonTransform`
plugin class, fed with `transform_metadata.metadata`/`.context` built from a
building block's own transforms.yaml/examples.yaml/bblock.json — without
needing the full bblocks-postprocess pipeline installed.

Use this when a transform works fine from the topo2geojson CLI but fails (or
behaves differently) once it's wired up as a building block transform: it
reproduces the exact metadata/context shape the transform function receives,
and reports each step (locating the example, resolving the TTL path(s),
running the transform) so a mismatch is visible instead of buried in the
postprocessing pipeline's own error handling.

Usage
-----
Defaults reproduce the "Example of a Polygon" / "Faces-as-Polygons" case in
ngsc/topo-feature's topo-feature building block:

    python scripts/debug_bblock_transform.py

Point it at a different building block / example / transform:

    python scripts/debug_bblock_transform.py \\
        --register-root "C:/repos/ngsc/topo-feature" \\
        --bblock-dir "C:/repos/ngsc/topo-feature/_sources/features/topo-feature" \\
        --example-ref examples/polygon.json \\
        --transform-id Faces-as-Polygons

`--chdir` sets the working directory before invoking the transform, so you
can compare "run from the register root" vs. "run from wherever the shell
happens to be" — the most common source of a `ttl` path in transforms.yaml
(itself register-root-relative) failing to resolve only in one of the two.
"""

from __future__ import annotations

import argparse
import glob as glob_module
import json
import sys
import traceback
import types
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_yaml(path: Path):
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _load_json(path: Path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _find_example(examples: list, ref_suffix: str) -> tuple[int, dict, int, dict]:
    """Return (example_index, example, snippet_index, snippet) for the first
    example/snippet whose `ref` ends with *ref_suffix* (e.g. "examples/polygon.json")."""
    for ex_idx, example in enumerate(examples):
        for sn_idx, snippet in enumerate(example.get("snippets", [])):
            if str(snippet.get("ref", "")).replace("\\", "/").endswith(ref_suffix):
                return ex_idx, example, sn_idx, snippet
    raise LookupError(f"No example snippet found with ref ending in {ref_suffix!r}")


def _find_transform(transforms: list, transform_id: str) -> dict:
    for t in transforms:
        if t.get("id") == transform_id:
            return t
    available = ", ".join(t.get("id", "?") for t in transforms)
    raise LookupError(f"No transform with id={transform_id!r} (available: {available})")


def _build_transform_metadata(*, bblock_dir: Path, bblock_json: dict,
                               example_index: int, example: dict,
                               snippet_index: int, snippet: dict,
                               transform: dict, output_dir: Path,
                               working_dir: Path) -> types.SimpleNamespace:
    """Approximate the transform_metadata object a bblocks-postprocess host
    passes to a Python transform plugin, per
    https://ogcincubator.github.io/bblocks-docs/create/transforms#transform-context
    Fields we can't know outside the real pipeline (annotated schema paths,
    register-wide URLs, etc.) are left None — the transform only actually
    reads context.example/.snippet (examples.yaml prefixes/base-uri) plus
    whatever it finds in `metadata`."""
    context = types.SimpleNamespace(
        bblock_id=bblock_json.get("name"),
        bblock_name=bblock_json.get("name"),
        bblock_version=bblock_json.get("version"),
        bblock_tags=bblock_json.get("tags"),
        bblock_files_path=str(bblock_dir),
        bblock_annotated_path=None,
        bblock_metadata=bblock_json,
        source_schema_path=str(bblock_dir / "schema.json"),
        annotated_schema_path=None,
        jsonld_context_path=str(bblock_dir / "context.jsonld"),
        shacl_shapes_paths=bblock_json.get("shaclRules"),
        example_index=example_index,
        example={k: v for k, v in example.items() if k != "snippets"},
        snippet_index=snippet_index,
        snippet=snippet,
        output_file=str(output_dir / f"{example.get('base-output-filename', 'output')}.geojson"),
        output_dir=str(output_dir),
        working_dir=str(working_dir),
        base_url=None,
        github_base_url=None,
        git_repository=None,
        id_prefix=None,
        imported_register_urls=None,
        transform_plugins=None,
    )
    return types.SimpleNamespace(metadata=dict(transform.get("metadata", {})), context=context)


def _report_ttl_glob_resolution(ttl_value, base_dirs: list) -> None:
    """Print, for each configured ttl path, exactly what
    topo2geojson.run_transform()'s ttl resolution (cwd-relative glob, then
    falling back to each of *base_dirs*) will find — so a mismatch is
    visible before the transform itself runs."""
    from topo2geojson import _expand_ttl_glob

    print(f"  cwd: {Path.cwd()}")
    print(f"  fallback base_dirs (context.working_dir, context.bblock_files_path): {base_dirs}")
    paths = ttl_value if isinstance(ttl_value, list) else [ttl_value]
    for p in paths:
        cwd_matches = sorted(glob_module.glob(p))
        resolved = _expand_ttl_glob(p, base_dirs)
        if cwd_matches:
            print(f"  ttl entry {p!r}: OK (cwd-relative) -> {cwd_matches}")
        elif resolved:
            print(f"  ttl entry {p!r}: OK (via base_dirs fallback) -> {resolved}")
        else:
            print(f"  ttl entry {p!r}: NO MATCH anywhere (cwd or fallback base_dirs)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--register-root", default="C:/repos/ngsc/topo-feature",
                        help="Register root (the directory containing _sources/); "
                             "used only for diagnostics/--chdir convenience")
    parser.add_argument("--bblock-dir", default="C:/repos/ngsc/topo-feature/_sources/features/topo-feature",
                        help="Building block source directory (contains bblock.json, "
                             "examples.yaml, transforms.yaml)")
    parser.add_argument("--example-ref", default="examples/polygon.json",
                        help="Suffix of the example snippet's 'ref' to run (matches "
                             "examples.yaml snippets[].ref)")
    parser.add_argument("--transform-id", action="append", dest="transform_ids", default=None,
                        help="Transform id(s) from transforms.yaml to run (repeatable). "
                             "Default: every transform in transforms.yaml.")
    parser.add_argument("--chdir", default=None,
                        help="chdir here before invoking the transform (e.g. a directory "
                             "OTHER than the register root), to reproduce a transform host "
                             "whose actual process cwd doesn't match context.working_dir. "
                             "Default: don't chdir (i.e. process cwd == wherever you ran "
                             "this script from).")
    parser.add_argument("--working-dir", default=None,
                        help="Value to report as context.working_dir ('Working directory at "
                             "postprocessing time' per the bblocks transform-context docs) — "
                             "independent of the script's actual process cwd (--chdir). "
                             "Default: the register root, which is what relative ttl paths "
                             "in transforms.yaml are written against.")
    parser.add_argument("--use-installed", action="store_true",
                        help="Import topo2geojson from the environment's installed package "
                             "instead of this repo's local source (to check whether a stale "
                             "pip install — e.g. an old `pip install git+...` cache — is the "
                             "actual problem rather than the current source).")
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "tests" / "output" / "bblock_debug"),
                        help="Where to write the generated .geojson files (kept out of the "
                             "building block's own source tree by default).")
    args = parser.parse_args()

    register_root = Path(args.register_root).resolve()
    bblock_dir = Path(args.bblock_dir).resolve()
    working_dir = Path(args.working_dir).resolve() if args.working_dir else register_root

    if args.chdir:
        import os
        os.chdir(args.chdir)

    if not args.use_installed:
        sys.path.insert(0, str(REPO_ROOT))
    else:
        sys.path = [p for p in sys.path if Path(p).resolve() != REPO_ROOT]

    import topo2geojson
    print(f"Using topo2geojson from: {topo2geojson.__file__}")
    print(f"Register root: {register_root}")
    print(f"Building block dir: {bblock_dir}")
    print(f"Process cwd: {Path.cwd()}")
    print(f"context.working_dir reported to the transform: {working_dir}")
    print()

    bblock_json = _load_json(bblock_dir / "bblock.json")
    examples = _load_yaml(bblock_dir / "examples.yaml")
    transforms = _load_yaml(bblock_dir / "transforms.yaml")["transforms"]

    ex_idx, example, sn_idx, snippet = _find_example(examples, args.example_ref)
    print(f"Example: {example.get('title')!r} (index {ex_idx}), "
          f"snippet {sn_idx}: {snippet}")

    input_path = (bblock_dir / snippet["ref"]).resolve()
    input_data = input_path.read_text(encoding="utf-8")
    print(f"Input file: {input_path}")
    print(json.dumps(json.loads(input_data), indent=2))
    print()

    transform_ids = args.transform_ids or [t["id"] for t in transforms]
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    overall_ok = True
    for transform_id in transform_ids:
        transform = _find_transform(transforms, transform_id)
        print("=" * 70)
        print(f"Transform: {transform_id!r}  metadata={transform.get('metadata')}")

        ttl_value = transform.get("metadata", {}).get("ttl")
        if ttl_value:
            print("TTL path resolution (as topo2geojson.run_transform() itself resolves it):")
            _report_ttl_glob_resolution(ttl_value, [str(working_dir), str(bblock_dir)])

        transform_metadata = _build_transform_metadata(
            bblock_dir=bblock_dir, bblock_json=bblock_json,
            example_index=ex_idx, example=example,
            snippet_index=sn_idx, snippet=snippet,
            transform=transform, output_dir=output_dir,
            working_dir=working_dir,
        )
        transform_metadata.input_data = input_data

        plugin = topo2geojson.Topo2GeoJsonTransform()
        try:
            output = plugin.transform(transform_metadata)
        except Exception:
            overall_ok = False
            print(f"FAILED: {transform_id}")
            traceback.print_exc()
            print()
            continue

        out_path = output_dir / f"{transform_id}.geojson"
        out_path.write_text(output, encoding="utf-8")
        parsed = json.loads(output) if output.strip() else {}
        if not parsed or parsed == {}:
            overall_ok = False
            print(f"FAILED: {transform_id} produced no features (empty output)")
        else:
            print(f"OK: {transform_id} -> {out_path}")
        print()

    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
