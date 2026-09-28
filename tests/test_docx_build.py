"""Regression test for docx restructuring.

Run directly:  python tests/test_docx_build.py

The invariant worth guarding above all others is that live Zotero citation
fields survive restructuring. If they do not, the author's reference workflow
breaks silently: the bibliography stops refreshing and the citations become
plain text with no warning. Everything else here is ordinary behaviour.

Uses only the standard library plus python-docx, and exits non-zero on
failure, so it runs without a test framework installed.
"""

from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

from jst import docx_build

ZOTERO_FIELD_PARAGRAPH = f'''<w:p {nsdecls("w")}>
  <w:r><w:t>As shown previously </w:t></w:r>
  <w:r><w:fldChar w:fldCharType="begin"/></w:r>
  <w:r><w:instrText xml:space="preserve"> ADDIN ZOTERO_ITEM CSL_CITATION {{"citationID":"abc123"}} </w:instrText></w:r>
  <w:r><w:fldChar w:fldCharType="separate"/></w:r>
  <w:r><w:t>(Smith et al., 2020)</w:t></w:r>
  <w:r><w:fldChar w:fldCharType="end"/></w:r>
  <w:r><w:t>.</w:t></w:r>
</w:p>'''


def make_source(path: Path) -> None:
    """A manuscript whose sections are deliberately out of order."""
    doc = Document()
    doc.add_heading("Title of Manuscript", level=0)
    doc.add_heading("Introduction", level=1)
    doc.add_paragraph("Intro text one.")
    doc.add_heading("Methods", level=1)
    doc.add_paragraph("Methods text.")
    doc.add_heading("Results", level=1)
    doc.add_paragraph("Results text.")
    doc.element.body.append(parse_xml(ZOTERO_FIELD_PARAGRAPH))
    doc.add_heading("Discussion", level=1)
    doc.add_paragraph("Discussion text.")
    doc.add_heading("References", level=1)
    doc.add_paragraph("[1] Smith J. A study. J Immunol. 2020.")
    doc.save(str(path))


def run() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="jst_test_"))
    src, out = tmp / "src.docx", tmp / "out.docx"
    make_source(src)
    before = src.read_bytes()

    plan_path = tmp / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "journal": "Test Journal",
                "order": ["Introduction", "Results", "Discussion", "Methods", "References"],
                "renames": {"Results": "Results and Findings"},
                "insert_after": [
                    {
                        "after": "Discussion",
                        "title": "Data Availability",
                        "body": "[AUTHOR TO FILL: data availability statement]",
                    },
                    {
                        "after": "Discussion",
                        "title": "Code Availability",
                        "body": "[AUTHOR TO FILL: code availability statement]",
                    },
                    {
                        "after": "Discussion",
                        "title": "Competing Interests",
                        "body": "[AUTHOR TO FILL: competing interests]",
                    },
                ],
                "keep_unlisted": "end",
            }
        ),
        encoding="utf-8",
    )

    report = docx_build.restructure(src, docx_build.load_plan(plan_path), out)

    xml = zipfile.ZipFile(out).read("word/document.xml").decode("utf-8")
    document = Document(str(out))
    text = "\n".join(p.text for p in document.paragraphs)

    failures: list[str] = []

    def check(label: str, condition: bool, detail: str = "") -> None:
        if condition:
            print(f"PASS  {label}")
        else:
            print(f"FAIL  {label}  {detail}")
            failures.append(label)

    check(
        "section order applied",
        report["order_after"]
        == [
            "Introduction",
            "Results and Findings",
            "Discussion",
            "Data Availability",
            "Code Availability",
            "Competing Interests",
            "Methods",
            "References",
        ],
        str(report["order_after"]),
    )
    check("heading renamed", "Results and Findings" in text)
    check("statements inserted", "Data Availability" in text)
    check("inserted body is a placeholder", "[AUTHOR TO FILL" in text)

    # Items sharing an anchor must land in plan order, not reversed. Getting
    # this wrong silently puts a journal's required statements out of order.
    check(
        "SAME-ANCHOR INSERTS KEEP PLAN ORDER",
        report["order_after"][3:6]
        == ["Data Availability", "Code Availability", "Competing Interests"],
        str(report["order_after"][3:6]),
    )

    check("ZOTERO CITATION FIELD SURVIVED", "ADDIN ZOTERO_ITEM" in xml)
    check(
        "field codes balanced",
        xml.count('fldCharType="begin"') == 1
        and xml.count('fldCharType="separate"') == 1
        and xml.count('fldCharType="end"') == 1,
    )
    check("citation display text preserved", "(Smith et al., 2020)" in text)

    check("body text not lost", "Methods text." in text and "Discussion text." in text)
    check("SOURCE FILE UNMODIFIED", src.read_bytes() == before)

    # python-docx documents carry Word's blue Office-theme heading colours
    # (Heading 1 #365F91, Heading 2 #4F81BD, Title #17365D). If those survive,
    # every heading in the output renders blue. Table and numbering styles are
    # exempt: their colours may be an intentional white-on-dark header.
    from lxml import etree

    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    styles_xml = zipfile.ZipFile(out).read("word/styles.xml")
    non_black = []
    for style in etree.fromstring(styles_xml).findall(f"{ns}style"):
        if style.get(f"{ns}type") in ("table", "numbering"):
            continue
        rPr = style.find(f"{ns}rPr")
        if rPr is None:
            continue
        color = rPr.find(f"{ns}color")
        if color is None:
            continue
        value = color.get(f"{ns}val")
        if value and value.lower() not in ("auto", "000000"):
            non_black.append((style.get(f"{ns}styleId"), value))
    check("NO NON-BLACK TEXT STYLE COLOURS REMAIN", not non_black, str(non_black))

    check(
        "TABLE STYLE COLOURS LEFT ALONE",
        any(
            s.get(f"{ns}type") == "table"
            for s in etree.fromstring(styles_xml).findall(f"{ns}style")
        ),
    )

    print()
    if failures:
        print(f"{len(failures)} failure(s): {failures}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
