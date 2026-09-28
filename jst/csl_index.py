"""Journal name -> CSL citation style resolution.

This is the part that makes the tool worth building: reference formatting is
resolved *deterministically* against the Citation Style Language style
repository, never guessed by a language model. The prior-art project
(my-submission-formatting-agent) explicitly declined this step, calling
automatic reference-format conversion "too error-prone".

Data source: https://github.com/citation-style-language/styles

The repository is obtained as a *partial* clone -- ``--depth 1
--filter=blob:none --no-checkout`` -- which is under half a megabyte and
carries the full file listing without downloading any style bodies. Reading a
single style fetches just that blob, lazily. This deliberately avoids the
GitHub REST API, whose unauthenticated limit of 60 requests/hour is easy to
exhaust while exploring.

Resolution is a three-stage funnel, cheapest first:

  1. exact slug match against the cached listing;
  2. slug variants (leading "the", "journal of X" -> "x", "&" -> "and");
  3. fuzzy token-overlap scoring, returned as ranked *candidates* with
     confidence marked low, for a human to choose from.

Stage 3 never silently picks a winner. A wrong reference style is worse than
no suggestion.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

REPO_URL = "https://github.com/citation-style-language/styles.git"
STYLE_ID_BASE = "http://www.zotero.org/styles/"

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
REPO_DIR = DATA_DIR / "csl_styles"
BLOB_CACHE = DATA_DIR / "blob_cache"
INDEX_CACHE = DATA_DIR / "csl_index.json"
INDEX_TTL_SECONDS = 7 * 24 * 3600

# The usual fallback for Chinese journals: GB/T 7714 numeric.
GB_T_7714 = "china-national-standard-gb-t-7714-2015-numeric"

_GIT_TIMEOUT = 180


class ResolveError(RuntimeError):
    """Raised when the style repository cannot be reached or queried."""


# --------------------------------------------------------------------------
# Partial clone of the style repository
# --------------------------------------------------------------------------


def _git(*args: str, timeout: int = _GIT_TIMEOUT) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise ResolveError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def ensure_repo(force: bool = False) -> Path:
    """Make sure the partial clone exists and is not stale."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not (REPO_DIR / ".git").exists():
        if REPO_DIR.exists():
            raise ResolveError(
                f"{REPO_DIR} exists but is not a git clone; move it aside and retry"
            )
        proc = subprocess.run(
            [
                "git", "clone", "--depth", "1",
                "--filter=blob:none", "--no-checkout",
                REPO_URL, str(REPO_DIR),
            ],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT,
        )
        if proc.returncode != 0:
            raise ResolveError(f"clone failed: {proc.stderr.strip()[:300]}")
        return REPO_DIR

    age = time.time() - (REPO_DIR / ".git" / "FETCH_HEAD").stat().st_mtime
    if force or age > INDEX_TTL_SECONDS:
        try:
            _git("fetch", "--depth", "1", "origin")
            _git("reset", "--soft", "FETCH_HEAD")
        except ResolveError:
            if INDEX_CACHE.exists():
                return REPO_DIR  # stale listing beats no listing
            raise
    return REPO_DIR


def list_paths() -> list[str]:
    """Every tracked path in the repository."""
    ensure_repo()
    return [p for p in _git("ls-tree", "-r", "--name-only", "HEAD").splitlines() if p]


def build_index(force: bool = False) -> dict[str, str]:
    """Map style slug -> repo path, for independent and dependent styles.

    Cached to disk because the listing is stable for days and the clone is
    the slow part.
    """
    if not force and INDEX_CACHE.exists():
        age = time.time() - INDEX_CACHE.stat().st_mtime
        if age < INDEX_TTL_SECONDS:
            return json.loads(INDEX_CACHE.read_text(encoding="utf-8"))

    index: dict[str, str] = {}
    for path in list_paths():
        if not path.endswith(".csl"):
            continue
        slug = path.rsplit("/", 1)[-1][: -len(".csl")]
        # Root styles win over dependent/ on a slug collision: the dependent
        # file only points at its parent, and we can follow that link.
        if slug not in index or path.count("/") == 0:
            index[slug] = path

    INDEX_CACHE.parent.mkdir(parents=True, exist_ok=True)
    INDEX_CACHE.write_text(json.dumps(index), encoding="utf-8")
    return index


# --------------------------------------------------------------------------
# Name normalisation
# --------------------------------------------------------------------------


def _slugify(name: str) -> str:
    s = name.strip().lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[‘’“”'\"`.,:;()\[\]{}!?]", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def slug_variants(name: str) -> list[str]:
    """Plausible slug spellings for a journal name, most specific first."""
    base = _slugify(name)
    out: list[str] = []

    def add(candidate: str) -> None:
        if candidate and candidate not in out:
            out.append(candidate)

    add(base)
    for pattern in (
        r"^the-", r"-the$", r"^journal-of-", r"-journal$",
        r"^the-journal-of-", r"^international-journal-of-",
        r"^british-journal-of-", r"^european-journal-of-",
    ):
        add(re.sub(pattern, "", base))
    # Many journals are officially titled "The Journal of X" and are filed
    # under the leading article, so try adding it as well as removing it.
    if not base.startswith("the-"):
        add(f"the-{base}")
    if not base.endswith("-journal"):
        add(f"{base}-journal")
    add(re.sub(r"-and-", "-", base))
    return out


def _tokens(slug: str) -> set[str]:
    return {t for t in slug.split("-") if t}


# --------------------------------------------------------------------------
# Style file access
# --------------------------------------------------------------------------


_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
_ID_RE = re.compile(r"<id>(.*?)</id>", re.DOTALL)
# Attribute order varies between styles, so match each attribute independently
# rather than assuming href-before-rel or the reverse.
_LINK_RE = re.compile(r"<link\b[^>]*>")
_HREF_RE = re.compile(r'href="([^"]+)"')
_REL_RE = re.compile(r'rel="([^"]+)"')


def fetch_style(path: str, use_cache: bool = True) -> str:
    """Read one .csl body, lazily fetching the blob and caching it."""
    cached = BLOB_CACHE / path.replace("/", "__")
    if use_cache and cached.exists():
        return cached.read_text(encoding="utf-8")

    text = _git("show", f"HEAD:{path}")
    BLOB_CACHE.mkdir(parents=True, exist_ok=True)
    cached.write_text(text, encoding="utf-8")
    return text


def parse_style(xml_text: str) -> dict[str, str | None]:
    """Pull the fields we care about out of a CSL style document."""
    title = _TITLE_RE.search(xml_text)
    style_id = _ID_RE.search(xml_text)

    parent = None
    for link in _LINK_RE.findall(xml_text):
        rel = _REL_RE.search(link)
        href = _HREF_RE.search(link)
        if rel and href and rel.group(1) == "independent-parent":
            parent = href
            break

    return {
        "title": title.group(1).strip() if title else None,
        "id": style_id.group(1).strip() if style_id else None,
        "parent": parent.group(1).strip() if parent else None,
    }


def load_style(path: str) -> dict[str, str | None]:
    """Fetch and parse a style, resolving a dependent style to its parent."""
    info = parse_style(fetch_style(path))
    info["path"] = path
    if info["parent"]:
        parent_path = info["parent"].rsplit("/", 1)[-1] + ".csl"
        try:
            parent_info = parse_style(fetch_style(parent_path))
            info["parent_title"] = parent_info["title"]
            info["parent_id"] = parent_info["id"]
            info["parent_path"] = parent_path
        except ResolveError as exc:
            info["parent_path"] = parent_path
            info["parent_error"] = str(exc)[:200]
    return info


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


def resolve(journal: str, index: dict[str, str] | None = None) -> dict[str, Any]:
    """Resolve a journal name to a CSL style.

    ``confidence`` is ``exact``, ``variant`` or ``none``. On ``none`` the
    caller gets ranked ``candidates`` and should not proceed without a human
    decision.
    """
    index = index if index is not None else build_index()

    for variant in slug_variants(journal):
        path = index.get(variant)
        if path:
            info = load_style(path)
            return {
                "journal_query": journal,
                "confidence": "exact" if variant == _slugify(journal) else "variant",
                "matched_slug": variant,
                "path": path,
                "title": info.get("title"),
                "style_id": info.get("id"),
                "dependent": bool(info.get("parent")),
                "parent_title": info.get("parent_title"),
                "parent_id": info.get("parent_id"),
                "parent_path": info.get("parent_path"),
                # What Zotero keys on: the parent style for a journal-specific
                # dependent style, otherwise the style itself.
                "zotero_style_url": info.get("parent") or info.get("id"),
            }

    query_tokens = _tokens(_slugify(journal))
    scored: list[tuple[float, str]] = []
    for slug in index:
        slug_tokens = _tokens(slug)
        if not slug_tokens or not query_tokens:
            continue
        overlap = len(query_tokens & slug_tokens) / len(query_tokens | slug_tokens)
        if overlap >= 0.6:
            scored.append((overlap, slug))
    scored.sort(key=lambda pair: -pair[0])

    return {
        "journal_query": journal,
        "confidence": "none",
        "candidates": [
            {"slug": slug, "path": index[slug], "score": round(score, 3)}
            for score, slug in scored[:10]
        ],
        "fallback_for_chinese": GB_T_7714,
    }


def resolve_or_raise(journal: str) -> dict[str, Any]:
    result = resolve(journal)
    if result["confidence"] == "none":
        names = [c["slug"] for c in result.get("candidates", [])]
        raise ResolveError(
            f"no CSL style matched {journal!r}. "
            f"Closest slugs: {names or 'none'}. "
            f"Pass an explicit slug, or use {GB_T_7714} for GB/T 7714."
        )
    return result
