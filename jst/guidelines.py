"""Journal requirements cards: fetch, store, and record provenance.

Division of labour
------------------
This module does the parts that must not be a language model's judgement call:
it retrieves the guidelines text, records where it came from and when, and
refuses to emit a field it did not actually see. Reading that text and filling
the structured fields is the skill's job, because interpreting prose
instructions is what a language model is genuinely good at. So the program
produces a card skeleton plus the raw text; the skill fills it.

Why the fetch ladder matters
----------------------------
Publisher sites resist automated retrieval. Measured from this machine on
2026-09-29 with a plain HTTP client:

    frontiersin.org       200   1.37 MB   usable
    elsevier.com          403              blocked
    mdpi.com              403              blocked
    springernature.com    timeout          blocked

So a single fetch attempt is not a strategy. The ladder below tries, in order,
the publisher page, a browser-like retry, the user-pasted text, and finally
gives up honestly. It never fills a field from memory.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Fields the skill must fill. Left null here rather than guessed.
CARD_FIELDS = [
    "article_type",
    "sections_required",
    "abstract",
    "headings",
    "word_limit",
    "figures",
    "tables",
    "references",
    "special_requirements",
    "submission_package",
    "language_and_nomenclature",
]


@dataclass
class GuidelineCard:
    journal: str
    source_url: str | None = None
    source_kind: str = "unknown"  # publisher_page | pasted_text | unretrieved
    retrieved: str = ""
    fetch_status: str = ""
    raw_text_path: str | None = None
    notes: list[str] = field(default_factory=list)
    # Filled by the skill, not by this module.
    article_type: Any = None
    sections_required: Any = None
    abstract: Any = None
    headings: Any = None
    word_limit: Any = None
    figures: Any = None
    tables: Any = None
    references: Any = None
    special_requirements: Any = None
    submission_package: Any = None
    language_and_nomenclature: Any = None

    def to_yaml(self) -> str:
        return yaml.safe_dump(
            asdict(self), allow_unicode=True, sort_keys=False, width=100
        )


def _today() -> str:
    return dt.date.today().isoformat()


def fetch_text(url: str, timeout: int = 30) -> tuple[str | None, str]:
    """Try to retrieve a URL. Returns (text_or_None, status_note).

    A failure is reported, never worked around by inventing content.
    """
    if requests is None:
        return None, "requests not installed"

    for attempt, headers in enumerate(
        (
            {"User-Agent": USER_AGENT},
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
        ),
        start=1,
    ):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 200 and resp.text.strip():
                return resp.text, f"200 via attempt {attempt}"
            note = f"HTTP {resp.status_code} via attempt {attempt}"
        except Exception as exc:  # noqa: BLE001 - report, do not mask
            note = f"{type(exc).__name__} via attempt {attempt}"
    return None, note


def _slug_for_filename(text: str, fallback: str = "guidelines") -> str:
    """Turn a journal name or URL into a safe, readable filename stem."""
    from urllib.parse import urlparse

    if text.startswith(("http://", "https://")):
        parsed = urlparse(text)
        host = parsed.netloc.replace("www.", "")
        parts = [host] + [p for p in parsed.path.split("/") if p]
        text = "-".join(parts)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")
    return safe[:80] or fallback


def build_card(
    journal: str | None = None,
    target: str | None = None,
    text_file: str | None = None,
    out_dir: Path | None = None,
) -> GuidelineCard:
    """Produce a card skeleton, retrieving raw guidelines text where possible.

    ``journal`` names the journal for the card. When it is omitted and
    ``target`` is a URL, a readable label is derived from the URL rather than
    stuffing the whole URL into the journal field.
    """
    out_dir = out_dir or Path.cwd()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not journal:
        journal = _slug_for_filename(target or text_file or "unknown")

    card = GuidelineCard(journal=journal, retrieved=_today())
    raw_text: str | None = None

    if text_file:
        path = Path(text_file)
        raw_text = path.read_text(encoding="utf-8")
        card.source_kind = "pasted_text"
        card.notes.append(f"guidelines supplied by the author from {path.name}")
    elif target and target.startswith(("http://", "https://")):
        card.source_url = target
        raw_text, status = fetch_text(target)
        card.fetch_status = status
        if raw_text:
            card.source_kind = "publisher_page"
        else:
            card.source_kind = "unretrieved"
            card.notes.append(
                f"could not retrieve {target} ({status}). "
                "Ask the author to paste the guidelines, or open the page in a "
                "browser and save it. Do not fill fields from memory."
            )
    else:
        card.source_kind = "unretrieved"
        card.fetch_status = "no URL supplied"
        card.notes.append(
            "No guidelines URL given. Automatic discovery is the skill's job; "
            "it should locate the Guide for Authors and re-run with the URL."
        )

    if raw_text:
        raw_path = out_dir / f"guidelines_{_slug_for_filename(card.journal)}.txt"
        raw_path.write_text(raw_text, encoding="utf-8")
        card.raw_text_path = str(raw_path)

    for name in CARD_FIELDS:
        setattr(card, name, None)

    return card


def main(args: argparse.Namespace) -> int:
    out_path = Path(args.out) if args.out else None
    out_dir = out_path.parent if out_path else Path.cwd()
    card = build_card(
        journal=getattr(args, "journal", None),
        target=args.target,
        text_file=args.text_file,
        out_dir=out_dir,
    )

    if out_path:
        out_path.write_text(card.to_yaml(), encoding="utf-8")
        print(f"# written to {out_path}", file=sys.stderr)
        if card.raw_text_path:
            print(f"# raw guidelines: {card.raw_text_path}", file=sys.stderr)
    else:
        print(card.to_yaml())

    if card.source_kind == "unretrieved":
        print(
            "# NOTE: no guidelines text retrieved. Fields above are null on "
            "purpose. The skill must obtain the guidelines before filling them.",
            file=sys.stderr,
        )
        return 2
    return 0
