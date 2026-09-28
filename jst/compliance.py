"""Counting and compliance: measure the manuscript, then compare to the card.

The rule this module enforces is that a requirement's status comes from a
check that ran, not from an impression. Word counts are counted, figure and
table counts are parsed, statement presence is looked for. Three statuses, and
only three:

    met        a check ran and passed
    violated   a check ran and failed, with the measured value recorded
    unverified no check could run -- no limit in the card, or no retrievable
               source behind the limit

Anything a language model judges (is this abstract structured *enough*?) is a
qualitative note, not a status.

Two counting units are reported because they measure different things and
journals differ on which they mean: ``words`` (whitespace tokens, the English
convention) and ``cjk_chars`` (CJK codepoints, the Chinese convention). A
Chinese manuscript's length is its character count, not its token count.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Status = Literal["met", "violated", "unverified"]

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.MULTILINE)
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(")
FIG_LINE_RE = re.compile(r"^\s*(?:Figure|Fig\.?|图)\s*\d+", re.IGNORECASE | re.MULTILINE)
TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|[\s:|-]*$", re.MULTILINE)
REF_HEADING_RE = re.compile(
    r"^#{1,6}\s*(references|bibliography|literature cited|参考文献|引用文献)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
REF_ITEM_RE = re.compile(r"^\s*(?:\[?\d+[\.\)\]]|\[\d+\])\s+", re.MULTILINE)

# Live Zotero citation fields, as they appear in word/document.xml.
ZOTERO_ITEM_RE = re.compile(r"ADDIN ZOTERO_ITEM")
ZOTERO_ITEM_URI_RE = re.compile(r"zotero\.org/users/\d+/items/([A-Za-z0-9]+)")

# Sections whose words are commonly excluded from a journal's word limit.
DEFAULT_EXCLUDES = ("abstract", "references", "bibliography", "acknowledg")


@dataclass
class Section:
    name: str
    level: int
    body: str

    @property
    def is_excluded_by_default(self) -> bool:
        lowered = self.name.strip().lower()
        return any(token in lowered for token in DEFAULT_EXCLUDES)


@dataclass
class Finding:
    requirement: str
    status: Status
    where: str | None = None
    measured: str | None = None
    note: str | None = None
    qualitative: bool = False


@dataclass
class Matrix:
    journal: str
    findings: list[Finding] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        counts = {"met": 0, "violated": 0, "unverified": 0}
        for finding in self.findings:
            counts[finding.status] += 1
        return counts


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def read_text(path: Path) -> str:
    """Read a manuscript as markdown-ish text.

    For .docx this re-emits heading paragraphs with ``#`` marks derived from
    their heading style. Flattening a docx to bare paragraph text would lose
    the heading styles, and every downstream step that finds sections, counts
    per-section words, or locates the references list depends on them.
    """
    suffix = path.suffix.lower()
    if suffix == ".docx":
        try:
            import docx  # python-docx
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "python-docx is needed to read .docx; export to markdown instead"
            ) from exc

        from .docx_build import heading_level

        document = docx.Document(str(path))
        lines: list[str] = []
        for paragraph in document.paragraphs:
            text = paragraph.text
            level = heading_level(paragraph)
            if level is not None and text.strip():
                lines.append(f"{'#' * min(level, 6)} {text.strip()}")
            else:
                lines.append(text)
        for table in document.tables:
            for row in table.rows:
                lines.append(" | ".join(cell.text for cell in row.cells))
        return "\n".join(lines)
    if suffix == ".tex":
        raw = path.read_text(encoding="utf-8", errors="replace")
        raw = re.sub(r"%.*", "", raw)  # strip comments
        raw = re.sub(r"\\(?:begin|end)\{[^}]*\}", "", raw)
        return raw
    return path.read_text(encoding="utf-8", errors="replace")


def count_zotero_citations(path: Path) -> dict[str, int] | None:
    """Distinct cited items in a .docx's Zotero fields, or None if absent.

    A journal's reference limit applies to distinct references, not to how
    many times each is cited, so the citation fields are read and their item
    URIs de-duplicated rather than counted naively.
    """
    if path.suffix.lower() != ".docx":
        return None
    try:
        import zipfile

        xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    except (OSError, KeyError):
        return None

    fields = len(ZOTERO_ITEM_RE.findall(xml))
    if not fields:
        return None

    keys = set(ZOTERO_ITEM_URI_RE.findall(xml))
    return {"citation_fields": fields, "distinct_items": len(keys)}


# --------------------------------------------------------------------------
# Counting
# --------------------------------------------------------------------------


def count_words(text: str) -> dict[str, int]:
    """Both conventions: whitespace tokens and CJK codepoints."""
    stripped = re.sub(r"`[^`]*`", " ", text)  # ignore inline code
    stripped = re.sub(r"^\s*[#>|*-]+", " ", stripped, flags=re.MULTILINE)
    tokens = [t for t in stripped.split() if any(ch.isalnum() or CJK_RE.match(ch) for ch in t)]
    return {"words": len(tokens), "cjk_chars": len(CJK_RE.findall(text))}


def count_figures(text: str) -> int:
    """Image embeds plus explicit Figure N / 图 N caption lines, whichever is more."""
    return max(len(IMAGE_RE.findall(text)), len(FIG_LINE_RE.findall(text)))


def count_tables(text: str) -> int:
    """Markdown tables, counted by their header separator rows."""
    return len(TABLE_SEP_RE.findall(text))


def split_sections(text: str) -> list[Section]:
    """Split on markdown headings. Text before the first heading is its own section."""
    matches = list(HEADING_RE.finditer(text))
    if not matches:
        return [Section(name="(whole document)", level=0, body=text)]

    sections: list[Section] = []
    preamble = text[: matches[0].start()]
    if preamble.strip():
        sections.append(Section(name="(preamble)", level=0, body=preamble))

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append(
            Section(
                name=match.group(2),
                level=len(match.group(1)),
                body=text[match.end() : end],
            )
        )
    return sections


def count_references(text: str) -> int | None:
    """Entries under a references heading. None when there is no such heading."""
    match = REF_HEADING_RE.search(text)
    if not match:
        return None
    tail = text[match.end() :]
    numbered = REF_ITEM_RE.findall(tail)
    if numbered:
        return len(numbered)
    # Unnumbered styles: count non-empty paragraphs.
    paragraphs = [p for p in re.split(r"\n\s*\n", tail) if p.strip()]
    return len(paragraphs) if paragraphs else 0


def analyze(path: Path) -> dict[str, Any]:
    """Full measurement of a manuscript. No comparison, just facts."""
    text = read_text(path)
    sections = split_sections(text)

    body_text = "\n".join(s.body for s in sections if not s.is_excluded_by_default)
    everything = count_words(text)
    body_only = count_words(body_text)

    return {
        "path": str(path),
        "totals": everything,
        "body_only": body_only,
        "excluded_sections": [s.name for s in sections if s.is_excluded_by_default],
        "sections": [
            {"name": s.name, "level": s.level, **count_words(s.body)} for s in sections
        ],
        "figures": count_figures(text),
        "tables": count_tables(text),
        "references": count_references(text),
        "citations": count_zotero_citations(path),
    }


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------


def _limit(card: dict[str, Any], *path: str) -> Any:
    node: Any = card
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def compare(report: dict[str, Any], card: dict[str, Any]) -> Matrix:
    """Turn measurements plus the requirements card into a compliance matrix."""
    matrix = Matrix(journal=str(card.get("journal", "unknown")))

    excludes = _limit(card, "word_limit", "excludes")
    count_for_limit = (
        report["totals"]
        if excludes is None
        else report["body_only"]
    )
    # A Chinese manuscript's length is its character count.
    used_cjk = count_for_limit["cjk_chars"] > count_for_limit["words"] * 0.3
    measured_words = (
        count_for_limit["cjk_chars"] if used_cjk else count_for_limit["words"]
    )
    unit = "characters" if used_cjk else "words"

    total_limit = _limit(card, "word_limit", "total")
    matrix.findings.append(
        _compare_count(
            "Word limit", measured_words, total_limit, unit,
            note=f"excluding {excludes}" if excludes else None,
        )
    )

    for label, key, counted in (
        ("Figure limit", "figures", report["figures"]),
        ("Table limit", "tables", report["tables"]),
    ):
        matrix.findings.append(
            _compare_count(label, counted, _limit(card, key, "max_count"), "items")
        )

    # A reference limit applies to distinct references. When the manuscript
    # carries live Zotero fields instead of a plain list, count distinct cited
    # items from the fields rather than reporting "not found".
    ref_count = report["references"]
    ref_note = None
    citations = report.get("citations")
    if ref_count is None and citations:
        ref_count = citations["distinct_items"]
        ref_note = (
            f"counted from {citations['citation_fields']} live Zotero citation "
            "fields (no plain references list in the document); distinct items"
        )
    elif ref_count is None:
        ref_note = "no references list and no Zotero citation fields found"

    matrix.findings.append(
        _compare_count(
            "Reference limit",
            ref_count,
            _limit(card, "references", "max_count"),
            "entries",
            note=ref_note,
        )
    )

    for statement in card.get("special_requirements") or []:
        if not isinstance(statement, dict):
            continue
        name = statement.get("name", "statement")
        required = statement.get("required", True)
        if not required:
            continue
        matrix.findings.append(
            Finding(
                requirement=f"Statement: {name}",
                status="unverified",
                where=None,
                note="presence not checked by the program; verify manually",
            )
        )

    if card.get("source_kind") == "unretrieved":
        matrix.findings.append(
            Finding(
                requirement="Requirements card completeness",
                status="unverified",
                note="guidelines were never retrieved; every requirement is unconfirmed",
            )
        )

    return matrix


def _compare_count(
    label: str,
    measured: int | None,
    limit: Any,
    unit: str,
    note: str | None = None,
) -> Finding:
    if measured is None:
        return Finding(
            requirement=label,
            status="unverified",
            measured="not found",
            note=note or "could not locate this in the manuscript",
        )
    if limit is None:
        return Finding(
            requirement=label,
            status="unverified",
            measured=f"{measured} {unit}",
            note=note or "the journal does not state a limit",
        )
    try:
        limit_value = int(limit)
    except (TypeError, ValueError):
        return Finding(
            requirement=label,
            status="unverified",
            measured=f"{measured} {unit}",
            note=f"unreadable limit in card: {limit!r}",
        )
    status: Status = "met" if measured <= limit_value else "violated"
    return Finding(
        requirement=label,
        status=status,
        measured=f"{measured} / {limit_value} {unit}",
        note=note,
    )


def render(matrix: Matrix) -> str:
    """Render the matrix as markdown for the handoff report."""
    lines = [
        "| Requirement | Where satisfied | Status | Measured | Note |",
        "|---|---|---|---|---|",
    ]
    for finding in matrix.findings:
        status = f"{finding.status}*" if finding.qualitative else finding.status
        lines.append(
            f"| {finding.requirement} | {finding.where or '-'} | {status} "
            f"| {finding.measured or '-'} | {finding.note or '-'} |"
        )
    summary = matrix.summary()
    lines.append("")
    lines.append(
        f"met {summary['met']} / violated {summary['violated']} "
        f"/ unverified {summary['unverified']}"
    )
    lines.append("")
    lines.append("`*` = language-model judgement, not a verified check.")
    return "\n".join(lines)
