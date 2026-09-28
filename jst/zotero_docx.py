"""Zotero item resolution and docx emission with live citation fields.

Status: interface only. The underlying mechanics are already proven on this
machine and are documented in the project's Zotero notes -- pyzotero for item
lookup and creation, and hand-written OOXML field codes (``ADDIN ZOTERO_ITEM
CSL_CITATION`` / ``ADDIN ZOTERO_BIBL CSL_BIBLIOGRAPHY``) so that Word's Zotero
plugin owns the bibliography and the author can refresh it. What is *not* yet
verified is how the citation style is bound to the document: Zotero normally
records per-document preferences (including the style id) in its own hidden
preference fields, and whether emitting those from scratch is reliable needs a
test in Word before this is trusted with a real manuscript.

The one thing worth stating plainly: the style is chosen by ``csl_index`` and
passed through as a style id. It is never re-derived by the language model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The Zotero style ids Zotero writes into document preferences.
ZOTERO_STYLE_PREFIX = "http://www.zotero.org/styles/"


@dataclass
class CitationPlan:
    """One in-text citation slot and the item it should resolve to."""

    placeholder: str
    """Marker left in the draft, e.g. ``[@zhang2024]``."""

    item_key: str | None = None
    """Zotero item key once resolved; None means unresolved and blocking."""

    item_uri: str | None = None
    csl_json: dict[str, Any] | None = None
    resolved: bool = False


@dataclass
class BuildRequest:
    manuscript: Path
    journal: str
    style_id: str
    output: Path
    library_id: str
    library_type: str = "user"


def resolve_citations(plan: list[CitationPlan], library_id: str, library_type: str) -> list[CitationPlan]:
    """Look each placeholder up in the author's Zotero library.

    NotImplemented: needs pyzotero wiring plus the DOI/PMID metadata resolution
    step. Every placeholder that does not resolve must stay unresolved and be
    reported, never approximated with a plausible-looking entry.
    """
    raise NotImplementedError("citation resolution is not implemented yet")


def build_docx(request: BuildRequest, plan: list[CitationPlan]) -> Path:
    """Emit a docx whose citations and bibliography are live Zotero fields.

    NotImplemented. Two open questions have to be settled first:
      1. how the CSL style id is bound to the document (see module docstring);
      2. whether unresolved citations block the build or are emitted as
         visibly-marked placeholders.
    """
    raise NotImplementedError("docx emission is not implemented yet")
