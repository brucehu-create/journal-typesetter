"""Restructure a .docx by moving paragraph blocks, preserving every run.

Why not convert to markdown and back?
-------------------------------------
A manuscript prepared with Zotero carries live citation fields -- runs of
``fldChar`` / ``instrText`` XML holding ``ADDIN ZOTERO_ITEM`` and
``ADDIN ZOTERO_BIBL``. Round-tripping through markdown would flatten those
fields to plain text, and the author's whole reference workflow depends on
them surviving so that switching the citation style in Zotero refreshes the
bibliography. So restructuring happens *inside* the document: whole paragraph
blocks are moved, and nothing inside them is touched.

The module only does what the plan says. Deciding the target order, the new
heading names, and which statements to insert is the skill's judgement; this
code executes that decision mechanically and reports exactly what it did.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

# Word localises style names, so "Heading 2" may arrive as "标题 2".
HEADING_STYLE_RE = re.compile(r"(?:heading|标题)\s*(\d+)", re.IGNORECASE)


class PlanError(ValueError):
    """Raised when a build plan is unusable."""


@dataclass
class BuildPlan:
    journal: str = "unknown"
    order: list[str] = field(default_factory=list)
    renames: dict[str, str] = field(default_factory=dict)
    insert_after: list[dict[str, str]] = field(default_factory=list)
    # What to do with sections the plan does not mention.
    keep_unlisted: str = "end"  # end | drop | error

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BuildPlan":
        keep = data.get("keep_unlisted", "end")
        if keep not in ("end", "drop", "error"):
            raise PlanError(f"keep_unlisted must be end, drop or error, got {keep!r}")
        return cls(
            journal=str(data.get("journal", "unknown")),
            order=list(data.get("order") or []),
            renames=dict(data.get("renames") or {}),
            insert_after=list(data.get("insert_after") or []),
            keep_unlisted=keep,
        )


def load_plan(path: Path) -> BuildPlan:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PlanError(f"{path} is not valid JSON: {exc}") from exc
    return BuildPlan.from_dict(data)


# --------------------------------------------------------------------------
# Heading and block detection
# --------------------------------------------------------------------------


def heading_level(par: Paragraph) -> int | None:
    """Heading level of a paragraph, or None if it is body text."""
    match = HEADING_STYLE_RE.search(par.style.name or "")
    if match:
        return int(match.group(1))

    pPr = par._p.pPr
    if pPr is not None:
        outline = pPr.find(qn("w:outlineLvl"))
        if outline is not None:
            value = outline.get(qn("w:val"))
            if value is not None and value.isdigit():
                return int(value) + 1
    return None


@dataclass
class Block:
    """One top-level section: its heading plus everything until the next one."""

    heading: str
    level: int | None
    elements: list[Any]

    @property
    def key(self) -> str:
        return _normalise(self.heading)


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9一-鿿]+", "", text.strip().lower())


def split_blocks(doc) -> tuple[list[Any], list[Block]]:
    """Split the body into a preamble and one block per top-level section.

    The top-level heading level is taken to be the shallowest heading actually
    present, so a document whose sections are all Heading 2 still splits
    correctly.
    """
    body = doc.element.body
    children = list(body)

    sectPr = children[-1] if children and children[-1].tag == qn("w:sectPr") else None
    content = [el for el in children if el is not sectPr]

    levels = []
    for el in content:
        if el.tag == qn("w:p"):
            level = heading_level(Paragraph(el, doc))
            if level is not None:
                levels.append(level)
    top_level = min(levels) if levels else 1

    preamble: list[Any] = []
    blocks: list[Block] = []
    current: Block | None = None

    for el in content:
        level = heading_level(Paragraph(el, doc)) if el.tag == qn("w:p") else None
        if level is not None and level == top_level:
            current = Block(
                heading=Paragraph(el, doc).text.strip(), level=level, elements=[el]
            )
            blocks.append(current)
        elif current is None:
            preamble.append(el)
        else:
            current.elements.append(el)

    return preamble, blocks


# --------------------------------------------------------------------------
# Restructuring
# --------------------------------------------------------------------------


def normalize_colors(doc) -> list[str]:
    """Force paragraph and character styles to black text.

    python-docx documents inherit Word's Office theme heading colours --
    Heading 1 is #365F91, Heading 2 #4F81BD, Title #17365D -- so every heading
    renders blue unless this is undone. Body text is unaffected (Normal
    carries no colour of its own), which is why the blue shows up only on
    headings and looks deliberate.

    Table and numbering styles are deliberately left alone. Their colours
    often mean the opposite of what a blanket change assumes: a table style
    may set white text for a dark header row, and forcing that to black would
    make the header invisible.

    Both ``w:val`` and ``w:themeColor`` must be handled: Word prefers the
    theme reference when present, so clearing only the literal value leaves
    the heading blue.
    """
    changed: list[str] = []
    for style in doc.styles.element.findall(qn("w:style")):
        if style.get(qn("w:type")) in ("table", "numbering"):
            continue

        rPr = style.find(qn("w:rPr"))
        if rPr is None:
            continue
        color = rPr.find(qn("w:color"))
        if color is None:
            continue

        value = color.get(qn("w:val"))
        theme = color.get(qn("w:themeColor"))
        if (value or "").lower() in ("auto", "000000") and not theme:
            continue

        color.set(qn("w:val"), "000000")
        if theme is not None:
            del color.attrib[qn("w:themeColor")]
        for extra in ("w:themeTint", "w:themeShade"):
            if color.get(qn(extra)) is not None:
                del color.attrib[qn(extra)]
        changed.append(style.get(qn("w:styleId")) or "?")
    return changed


def _set_heading_text(par: Paragraph, text: str) -> None:
    """Replace a heading's text, keeping the first run's formatting intact."""
    if par.runs:
        par.runs[0].text = text
        for run in par.runs[1:]:
            run.text = ""
    else:
        par.add_run(text)


def restructure(
    src: Path, plan: BuildPlan, out: Path, black_text: bool = True
) -> dict[str, Any]:
    """Apply the plan to a copy of ``src``, writing to ``out``.

    ``src`` is never modified. Returns a report of what happened.

    ``black_text`` forces all text to black, undoing the blue Office-theme
    heading colours that python-docx documents carry by default.
    """
    if src.resolve() == out.resolve():
        raise PlanError("refusing to write over the source manuscript")

    doc = Document(str(src))
    preamble, blocks = split_blocks(doc)

    if not blocks:
        raise PlanError(f"no headings found in {src.name}; nothing to reorder")

    by_key: dict[str, Block] = {}
    duplicates: list[str] = []
    for block in blocks:
        if block.key in by_key:
            duplicates.append(block.heading)
        else:
            by_key[block.key] = block

    report: dict[str, Any] = {
        "journal": plan.journal,
        "source": str(src),
        "output": str(out),
        "order_before": [b.heading for b in blocks],
        "order_after": [],
        "renamed": [],
        "inserted": [],
        "unlisted": [],
        "warnings": [],
    }
    if duplicates:
        report["warnings"].append(
            f"duplicate top-level headings, only the first of each was reordered: {duplicates}"
        )

    ordered: list[Block] = []
    used: set[str] = set()

    for wanted in plan.order:
        block = by_key.get(_normalise(wanted))
        if block is None:
            report["warnings"].append(f"planned section not found in manuscript: {wanted!r}")
            continue
        if block.key in used:
            continue
        used.add(block.key)
        ordered.append(block)

    unlisted = [b for b in blocks if b.key not in used]
    if unlisted:
        if plan.keep_unlisted == "error":
            names = [b.heading for b in unlisted]
            raise PlanError(f"unlisted sections present and keep_unlisted is 'error': {names}")
        if plan.keep_unlisted == "end":
            ordered.extend(unlisted)
        report["unlisted"] = [b.heading for b in unlisted]

    # Rename headings after ordering, so matching used the original text.
    for block in ordered:
        new_name = plan.renames.get(block.heading) or plan.renames.get(_normalise(block.heading))
        if new_name and new_name != block.heading:
            _set_heading_text(Paragraph(block.elements[0], doc), new_name)
            report["renamed"].append(f"{block.heading} -> {new_name}")

    _rebuild_body(doc, preamble, ordered)
    _insert_statements(doc, plan, report)

    report["colors_normalized"] = normalize_colors(doc) if black_text else []

    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))

    # Report the order actually in the written document, not the order the
    # plan implied -- inserted sections included.
    report["order_after"] = _final_order(doc)
    return report


def _final_order(doc) -> list[str]:
    """Top-level heading text in document order, as written."""
    levels = [lv for lv in (heading_level(p) for p in doc.paragraphs) if lv is not None]
    if not levels:
        return []
    top = min(levels)
    return [p.text.strip() for p in doc.paragraphs if heading_level(p) == top]


def _rebuild_body(doc, preamble: list[Any], ordered: list[Block]) -> None:
    """Append every element in the new order, keeping sectPr last."""
    body = doc.element.body
    sectPr = None
    for child in reversed(list(body)):
        if child.tag == qn("w:sectPr"):
            sectPr = child
            break

    for el in preamble:
        body.append(el)
    for block in ordered:
        for el in block.elements:
            body.append(el)
    if sectPr is not None:
        body.append(sectPr)


def _insert_statements(doc, plan: BuildPlan, report: dict[str, Any]) -> None:
    """Insert new paragraphs after a named section's heading.

    Items sharing one anchor are chained in plan order: each is placed after
    the previous insertion rather than after the anchor. Anchoring every item
    to the original heading would reverse them, which silently puts a journal's
    required statements in the wrong order.
    """
    cursors: dict[str, Any] = {}  # anchor key -> element to insert after next
    templates: dict[str, Any] = {}  # anchor key -> heading paragraph to clone

    for item in plan.insert_after:
        after = item.get("after")
        title = item.get("title")
        body_text = item.get("body", "")
        if not after or not title:
            report["warnings"].append(f"ignoring malformed insert_after entry: {item}")
            continue

        key = _normalise(after)
        if key not in cursors:
            anchor_par = None
            for par in doc.paragraphs:
                if heading_level(par) is not None and _normalise(par.text) == key:
                    anchor_par = par
                    break
            if anchor_par is None:
                report["warnings"].append(f"insert_after target not found: {after!r}")
                continue
            cursors[key] = anchor_par._p
            templates[key] = anchor_par

        title_el = copy.deepcopy(templates[key]._p)
        _set_heading_text(Paragraph(title_el, doc), title)
        body_par = doc.add_paragraph(body_text)

        cursor = cursors[key]
        cursor.addnext(body_par._p)
        cursor.addnext(title_el)
        cursors[key] = body_par._p

        report["inserted"].append(f"{title} (after {after})")


def render_report(report: dict[str, Any]) -> str:
    lines = [f"## Restructure report: {report['journal']}", ""]
    lines.append("sections before: " + ", ".join(report["order_before"]))
    lines.append("sections after : " + ", ".join(report["order_after"]))
    for label, key in (
        ("renamed", "renamed"), ("inserted", "inserted"), ("unlisted", "unlisted"),
        ("warnings", "warnings"),
    ):
        values = report.get(key) or []
        if values:
            lines.append(f"{label}:")
            lines.extend(f"  - {v}" for v in values)

    colored = report.get("colors_normalized") or []
    if colored:
        lines.append(f"text forced to black in {len(colored)} styles: {', '.join(colored)}")
    return "\n".join(lines)
