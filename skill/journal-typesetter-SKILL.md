---
name: journal-typesetter
description: >-
  Restructures and reformats an existing manuscript to match a named target
  journal's requirements. Use when the author says '按 <期刊> 的格式排版',
  '改投 <期刊>', 'format this for <journal>', 'restructure this for the
  journal', 'check this against the journal's requirements', or names a target
  journal and wants an existing manuscript adapted for submission. Covers
  locating the journal's guidelines, extracting them into a requirements card,
  reordering sections, renaming headings, restructuring the abstract, adding
  required statements, counting against the journal's limits, resolving the
  journal's CSL citation style, and reporting a compliance matrix.
license: CC-BY-4.0
---

# Journal Submission Writer

## Overview

An author with a finished manuscript should have to supply one routing fact:
the target journal. This skill finds that journal's author guidelines, reads
them, and reshapes the manuscript to comply — section order, heading
conventions, abstract structure, required statements, and citation style —
then reports a compliance matrix showing what was changed and what still needs
the author.

**The scientific content is not touched.** This skill reorders, renames,
reorganizes, and reformats. It does not rewrite sentences, alter numbers, or
change what the manuscript claims. That constraint is not a limitation the
skill works around; it is what makes the output trustworthy.

## Scope

**In scope**

- Reordering sections into the journal's required order.
- Renaming headings to the journal's convention and case style.
- Restructuring the abstract into the journal's required format.
- Adding required statements (data availability, ethics, funding, conflicts,
  author contributions) — **as placeholders for the author to fill**.
- Conforming figures and tables to stated conventions (count, caption rules).
- Resolving the journal's citation style and telling the author which Zotero
  style to select.
- Counting against the journal's stated limits and reporting the result.

**Out of scope**

- Writing an article from an outline. The input is a finished manuscript.
- Rewriting or polishing prose. Permitted changes are structural and
  formatting only.
- Choosing the journal; the author names the target.
- Substantive critique of the science — that is `pre-submission-reviewer`.
- Designing figures — that is `figure-designer`.

## Division of labour

This skill is a thin wrapper around a program. Respecting the split is the
whole reason it exists rather than being another prompt.

| Work | Done by | Why |
|---|---|---|
| Journal name → CSL citation style | `jst resolve-csl` | Deterministic and checkable |
| Style library download and cache | the program | Network and caching are engineering |
| Retrieving guidelines text, with source and date | `jst fetch-guidelines` | Provenance must be mechanical |
| Counting words, figures, references | `jst check` | What can be counted must not be estimated |
| Reading guidelines, filling the card | you | Interpreting prose instructions is what a model is for |
| Reordering, renaming, restructuring | you | Judgement over the manuscript's shape |
| Journal compliance judgement | you, using the program's counts | Judgement is yours; arithmetic is not |

Do not recompute a CSL style by reasoning about citation formats, and do not
count words by eye.

## Relation to the closest prior art

The closest existing project reformats a finished manuscript too, but requires
the author to supply the guidelines **URL** alongside the journal name, ships
only three pre-extracted journals, and declines automatic reference-format
conversion outright, calling it "too error-prone". Its narrow contract is what
makes it trustworthy and this skill keeps that same contract.

Three differences remain:

1. **Journal name alone is the entry point.** Locating the guidelines is this
   skill's job, not the author's.
2. **Citation style is resolved deterministically** through the CSL style
   repository rather than attempted by a language model or abandoned.
3. **Chinese journals are covered**, including the GB/T 7714 numeric style
   that the prior art does not address.

## Program location

    D:\datas\agent_local\output_files\32_Journal_Typesetter

Run as `python -m jst <command>` from that directory.

- `resolve-csl <journal> [--json]` — journal name to CSL style. Working.
- `fetch-guidelines <journal|url> [--text-file F] [--journal N] [--out F]` —
  guideline card with provenance. Working.
- `build <src.docx> --plan plan.json --out <dst.docx>` — restructure the
  document, preserving live Zotero fields. Working.
- `check <manuscript> --journal <name> [--card F]` — counting against limits.
  Working.
- `refresh-index` — rebuild the style listing.

There is also `tests/test_docx_build.py`, which guards the invariant that
Zotero citation fields survive restructuring. Run it after changing anything
in `docx_build.py`.

## Core procedure

### Step 0: Intake

Collect, asking only for what blocks:

- **Target journal** — required. Exact title, and the article type being
  submitted (research article, review, brief report), since requirements often
  differ per type.
- **The manuscript** — required. docx, markdown, or LaTeX.
- **Author-supplied facts** — author list, affiliations, funding, ethics
  approval numbers. Never invent these.

State back what was received and what was assumed.

### Step 1: Resolve the citation style

    python -m jst resolve-csl "<journal>"

- `confidence: exact` — proceed.
- `confidence: variant` — a slug variant matched; confirm the returned title
  is really the target journal before using it.
- `confidence: none` — **stop and ask the author.** Ranked candidates are
  listed; a human picks one. For a Chinese journal with no style, the fallback
  is GB/T 7714 (`china-national-standard-gb-t-7714-2015-numeric`), and the
  author should confirm the journal accepts it.

Never hand-build a reference format because resolution failed.

### Step 2: Obtain the guidelines

Try to locate and fetch the publisher's Guide for Authors.

    python -m jst fetch-guidelines "<journal>" --out card.yml

Publisher sites resist retrieval. Measured from this machine: Frontiers
returns 200, while **Elsevier returns 403, MDPI 403, and Springer Nature times
out**. So a failed fetch is expected, not exceptional. The ladder:

1. Try the publisher's author-guidelines URL directly.
2. If blocked, use web search to find the guidelines page, then fetch that URL.
3. If still blocked, **ask the author to paste the guidelines** and re-run with
   `--text-file`. One paste is enough; the card is reusable afterwards.
4. If the text cannot be obtained at all, say so plainly. Do not fill the card
   from memory of what the publisher usually requires.

Tell the author which rung you landed on. Do not imply automatic discovery
worked when it did not.

### Step 3: Fill the requirements card

Read the retrieved text and fill every field: article type and section order,
abstract structure and limit, heading conventions, word and figure limits,
reference style, figure and table conventions, required statements, submission
package, and language and nomenclature rules.

- Every field carries the source URL and retrieval date.
- A requirement the text does not state is recorded as "not specified" —
  never inferred, never filled from general knowledge of the publisher.
- Soft wording ("should") and hard wording ("must") are recorded differently.

### Step 4: Gap audit

Compare the manuscript against the card and produce one consolidated list:
satisfied, violated, or needing author input. Raise everything the author must
supply here rather than interrupting the work.

### Step 5: Restructure

Decide the target shape, then let the program apply it.

1. Read the card and decide the top-level section order, the heading renames,
   and which required statements are missing.
2. Write that decision to a plan file:

   ```json
   {
     "journal": "<name>",
     "order": ["Abstract", "Introduction", "Results", "Discussion", "Methods", "References"],
     "renames": {"Study Design": "Methods"},
     "insert_after": [{"after": "Discussion", "title": "Data Availability", "body": "[AUTHOR TO FILL]"}],
     "keep_unlisted": "end"
   }
   ```

3. Apply it:

       python -m jst build <src.docx> --plan plan.json --out <dst.docx>

The program moves whole paragraph blocks *inside* the document, so **live
Zotero citation fields survive** — which the reference workflow in Step 6
depends on. Never convert the manuscript to markdown and back; that flattens
the fields to plain text and silently breaks the author's bibliography.

`build` needs a `.docx`. If the manuscript is markdown or LaTeX, convert it to
`.docx` first, then restructure.

The source file is never modified. Read the report the command prints: a
`warnings` entry about a planned section not found means the plan and the
manuscript disagree, and the command exits 2.

**What the program will not do, and you must not do either:**

- **Restructure the abstract.** Reorganizing and labelling existing abstract
  sentences into the journal's required headings is judgement; do it by hand
  and mark it for the author's review.
- Rephrase sentences, alter numbers, change citations, or reorder information
  within a section. Permitted changes are structural and formatting only.
- Fill a statement section from anything except author-supplied facts;
  otherwise leave a visible placeholder.

If a change might touch scientific content, flag it for the author rather than
making it.

### Step 6: References

The program resolves the style; **the author applies it.**

Tell the author which Zotero style to select — the `zotero id` from Step 1, or
its plain name — and where: Zotero's document preferences in Word. If the
manuscript already carries Zotero citation fields, switching the style
refreshes the whole bibliography at once.

Do not hand-edit reference entries into the target format. The style is either
applied through Zotero or reported as an unresolved action item.

### Step 7: Count and check

    python -m jst check <manuscript> --journal "<journal>"

Use the program's counts; never estimate. Report each limit with its measured
value. Word and figure limits are warnings for the author to act on, not
grounds for deleting content.

### Step 8: Compliance matrix and handoff

Emit the matrix, then state plainly what was not done, what the author must
verify, and which requirements could not be confirmed against a source.

## Integrity gate

Tagged [inspection] (verifiable from the artefacts) or [attestation] (stated by
the skill; the author confirms).

1. **[inspection]** The CSL style came from `resolve-csl` with confidence
   `exact`, or an author-confirmed `variant`/candidate. No style was reasoned
   out by hand.
2. **[inspection]** Every requirement in the card carries a source URL and a
   retrieval date; nothing was filled from memory.
3. **[inspection]** The author was told which retrieval rung was used, and was
   never told discovery succeeded when it fell back to a paste.
4. **[inspection]** No sentence was rephrased, no number altered, no citation
   changed, and no information reordered within a section. Structural and
   formatting changes only.
5. **[inspection]** Every statement added in the author's name is either
   author-supplied or a visible placeholder.
6. **[inspection]** Word, figure, and reference limits were counted by the
   program, not estimated.
7. **[inspection]** The author's original file is unmodified.
8. **[inspection]** Placeholders the author must edit are marked by structure
   (brackets, comments), never by font colour; generated text uses one font
   colour.
9. **[attestation]** Where a change was ambiguous as to whether it touched
   scientific content, it was flagged for the author instead of made.

Any failed [inspection] check marks the output "needs author attention" and
names the failure.

## Output format

### Citation style
- Journal, resolved style, Zotero style id, confidence.

### Requirements card
- Every field carrying its source and retrieval date, plus which rung of the
  retrieval ladder was used.

### Gap list
- Satisfied / violated / needing author input.

### Restructured manuscript
- The manuscript in the required order, on a copy, with changes itemized.

### Compliance matrix
| Requirement | Where satisfied | Status | Measured |
|---|---|---|---|
| ... | ... | met / violated / unverified | ... |

### Author actions
- **Apply citation style**: select <style name> in Zotero's document preferences.
- **Fill these placeholders**: ...
- **Decide on these warnings**: word/figure limits exceeded by ...

### Sources
- Guidelines URL — retrieved <date> (rung used).
- CSL style — resolved via `jst resolve-csl` on <date>.
