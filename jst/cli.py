"""Command line entry point for the journal submission writer.

Usage
-----
    python -m jst resolve-csl "Nature Medicine"
    python -m jst resolve-csl "中华医学杂志" --json
    python -m jst refresh-index
    python -m jst fetch-guidelines <journal|url>      # not implemented yet
    python -m jst build <manuscript> --journal X      # not implemented yet
    python -m jst check <manuscript> --journal X      # not implemented yet
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from . import csl_index, guidelines


def _print_resolution(result: dict[str, Any], as_json: bool) -> int:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["confidence"] != "none" else 2

    confidence = result["confidence"]
    print(f"journal   : {result['journal_query']}")
    print(f"confidence: {confidence}")

    if confidence == "none":
        candidates = result.get("candidates", [])
        print("no style matched. closest slugs:")
        for cand in candidates:
            print(f"  {cand['score']:.2f}  {cand['slug']}")
        if not candidates:
            print("  (none)")
        print(f"fallback for Chinese journals: {result['fallback_for_chinese']}")
        return 2

    print(f"style     : {result['title']}")
    print(f"slug      : {result['matched_slug']}")
    print(f"path      : {result['path']}")
    if result["dependent"]:
        print(f"dependent on: {result['parent_title']}  ({result['parent_path']})")
    print(f"zotero id : {result['zotero_style_url']}")
    if confidence == "variant":
        print("note      : matched via a slug variant, verify this is the right journal")
    return 0


def cmd_resolve_csl(args: argparse.Namespace) -> int:
    try:
        result = csl_index.resolve(args.journal)
    except csl_index.ResolveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return _print_resolution(result, args.json)


def cmd_refresh_index(args: argparse.Namespace) -> int:
    try:
        index = csl_index.build_index(force=True)
    except csl_index.ResolveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"index rebuilt: {len(index)} styles")
    return 0


def cmd_fetch_guidelines(args: argparse.Namespace) -> int:
    return guidelines.main(args)


def _not_implemented(name: str) -> int:
    print(
        f"error: `{name}` is not implemented yet.\n"
        "The working piece so far is CSL resolution -- see `resolve-csl`.",
        file=sys.stderr,
    )
    return 1


def cmd_check(args: argparse.Namespace) -> int:
    from . import compliance

    manuscript = Path(args.manuscript)
    if not manuscript.exists():
        print(f"error: {manuscript} not found", file=sys.stderr)
        return 1

    card: dict[str, Any] = {"journal": args.journal}
    if args.card:
        card_path = Path(args.card)
        if not card_path.exists():
            print(f"error: {card_path} not found", file=sys.stderr)
            return 1
        card = yaml.safe_load(card_path.read_text(encoding="utf-8")) or {}
        card.setdefault("journal", args.journal)
    else:
        print(
            "# no --card given: every limit is unverified, only measurements below are real",
            file=sys.stderr,
        )

    report = compliance.analyze(manuscript)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"## Measurements: {manuscript.name}")
    print()
    print(f"totals     : {report['totals']['words']} words, "
          f"{report['totals']['cjk_chars']} CJK characters")
    print(f"body only  : {report['body_only']['words']} words, "
          f"{report['body_only']['cjk_chars']} CJK characters")
    if report["excluded_sections"]:
        print(f"excluded   : {', '.join(report['excluded_sections'])}")
    print(f"figures    : {report['figures']}")
    print(f"tables     : {report['tables']}")
    refs = report["references"]
    print(f"references : {refs if refs is not None else 'no references section found'}")
    print()
    print("### Sections")
    for section in report["sections"]:
        indent = "  " * max(0, section["level"] - 1)
        print(f"{indent}- {section['name']}: {section['words']} words, "
              f"{section['cjk_chars']} CJK characters")
    print()
    print("## Compliance matrix")
    matrix = compliance.compare(report, card)
    print(compliance.render(matrix))
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    from . import docx_build

    src = Path(args.manuscript)
    if not src.exists():
        print(f"error: {src} not found", file=sys.stderr)
        return 1
    if not args.out:
        print("error: --out is required (the source is never written over)", file=sys.stderr)
        return 1

    try:
        plan = docx_build.load_plan(Path(args.plan))
        report = docx_build.restructure(
            src, plan, Path(args.out), black_text=not args.keep_colors
        )
    except (docx_build.PlanError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(docx_build.render_report(report))
    print()
    print(f"written to {report['output']}")
    if report["warnings"]:
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jst",
        description="Adapt a manuscript to a target journal's requirements.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_resolve = sub.add_parser(
        "resolve-csl",
        help="map a journal name to its CSL citation style",
    )
    p_resolve.add_argument("journal", help="journal name, e.g. 'Nature Medicine'")
    p_resolve.add_argument("--json", action="store_true", help="emit JSON")
    p_resolve.set_defaults(func=cmd_resolve_csl)

    p_refresh = sub.add_parser(
        "refresh-index",
        help="re-fetch the CSL style listing",
    )
    p_refresh.set_defaults(func=cmd_refresh_index)

    p_guide = sub.add_parser(
        "fetch-guidelines",
        help="build a journal requirements card",
    )
    p_guide.add_argument("target", help="journal name or guidelines URL")
    p_guide.add_argument(
        "--journal",
        help="journal name for the card (derived from the URL when omitted)",
    )
    p_guide.add_argument("--text-file", help="file holding pasted guidelines text")
    p_guide.add_argument("--out", help="write the YAML card here")
    p_guide.set_defaults(func=cmd_fetch_guidelines)

    p_build = sub.add_parser(
        "build",
        help="restructure a .docx in place, preserving Zotero fields",
    )
    p_build.add_argument("manuscript", help="source .docx (never modified)")
    p_build.add_argument("--plan", required=True, help="build plan JSON")
    p_build.add_argument("--out", required=True, help="destination .docx")
    p_build.add_argument(
        "--keep-colors",
        action="store_true",
        help="keep the source's style colours (default: force all text to black)",
    )
    p_build.set_defaults(func=cmd_build)

    p_check = sub.add_parser("check", help="count the manuscript against the journal's limits")
    p_check.add_argument("manuscript", help="path to the manuscript (.md, .docx, .tex)")
    p_check.add_argument("--journal", required=True, help="target journal name")
    p_check.add_argument("--card", help="requirements card YAML from fetch-guidelines")
    p_check.add_argument("--json", action="store_true", help="emit measurements as JSON")
    p_check.set_defaults(func=cmd_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
