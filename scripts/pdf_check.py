#!/usr/bin/env python3
"""pdf_check.py -- final acceptance on the rendered PDF: every page as an image, a per-page
summary, and (when the authors have the source PDF) a word-level comparison of the rendered text.

    pdf_check.py --pdf new/main.pdf --out rep/pages [--src-pdf orig.pdf] [--dpi 50] [--json rep/pdf_check.json]

* Renders every page to PNG (pdftoppm) so the agent, and the authors, can look at each page:
  overlapping floats, boxes running into the margin, missing figures, wrong caption order.
* Per page: word count, figure/table captions found, section headings found.
* With --src-pdf: extracts the text of both PDFs in reading order, drops what the template adds
  or moves (line numbers, running heads, page numbers, hyphenation), and compares the multiset
  of words.  Words that exist in only one of the two documents are listed.  This is the check
  that catches a changed macro definition or a lost sentence at the rendered level.
  It is a *review* aid, not a proof: layout-induced hyphenation and template text produce
  a few spurious differences, which are listed too.

Needs poppler (pdftoppm, pdfinfo, pdftotext).  Exit 0 = every page rendered (and, with --src-pdf,
no number differs); 1 = numbers differ between the two renderings (review before delivering);
2 = the PDF is unreadable, has no pages, a page failed to render, or poppler is missing.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

TEMPLATE_NOISE = {
    "anonymous", "authors", "author(s)", "paper", "under", "double-blind", "review", "published", "as", "a",
    "conference", "at", "iclr", "neurips", "acl", "aaai", "icml", "preprint", "submitted", "workshop",
    "anonymized", "submission", "camera-ready", "figure", "table", "references", "appendix",
}


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, text=True, capture_output=True, timeout=300).stdout


def pages_of(pdf: Path) -> int:
    m = re.search(r"^Pages:\s+(\d+)", run(["pdfinfo", str(pdf)]), re.M)
    return int(m.group(1)) if m else 0


def page_text(pdf: Path, p: int) -> str:
    return run(["pdftotext", "-f", str(p), "-l", str(p), str(pdf), "-"])


def clean_words(text: str) -> list[str]:
    lines = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or re.fullmatch(r"\d{1,4}", s):                       # line numbers / page numbers
            continue
        if re.match(r"(Under review|Published as a conference paper|Preprint|Anonymous Author)", s):
            continue
        lines.append(s)
    t = "\n".join(lines)
    t = re.sub(r"-\n(?=[a-z])", "", t)                                   # re-join hyphenated words
    t = re.sub(r"\s+", " ", t)
    words = re.findall(r"[A-Za-z][A-Za-z0-9'\-]*|\d+(?:\.\d+)?%?", t)
    return [w for w in words if w.lower() not in TEMPLATE_NOISE]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", required=True, help="directory for the page images")
    ap.add_argument("--src-pdf", help="the source venue's compiled PDF, if the authors have it")
    ap.add_argument("--dpi", type=int, default=50)
    ap.add_argument("--json")
    args = ap.parse_args()
    for tool in ("pdftoppm", "pdfinfo", "pdftotext"):
        if not shutil.which(tool):
            print(f"pdf_check: {tool} (poppler) not found; install poppler or inspect the PDF on Overleaf", file=sys.stderr)
            return 2
    pdf = Path(args.pdf)
    if not pdf.is_file():
        print(f"pdf_check: no such file {pdf}", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    n = pages_of(pdf)
    if n <= 0:
        print(f"pdf_check: {pdf} is not a readable PDF (pdfinfo reports no pages) -- acceptance FAILED", file=sys.stderr)
        return 2
    for old_img in out.glob("page-*.png"):
        old_img.unlink()                                             # only images from THIS run count
    rc = subprocess.run(["pdftoppm", "-r", str(args.dpi), "-png", str(pdf), str(out / "page")], check=False).returncode
    images = sorted(out.glob("page-*.png"))
    if rc != 0 or len(images) != n:
        print(f"pdf_check: rendering failed (pdftoppm exit {rc}, {len(images)} image(s) for {n} page(s)) -- acceptance FAILED", file=sys.stderr)
        return 2

    summary = []
    for p in range(1, n + 1):
        t = page_text(pdf, p)
        caps = re.findall(r"^\s*(?:Figure|Table) \d+[a-z]?:", t, re.M)
        # section headings: "3 Experiments", "2.1 Setup" (a one/two-digit number, not a 3-digit line number)
        heads = [f"{a} {b.strip()}" for a, b in re.findall(r"^[ \t]*(\d{1,2}(?:\.\d{1,2})*)[ \t]+([A-Z][A-Za-z ,:&-]{2,60})[ \t]*$", t, re.M)]
        summary.append({"page": p, "words": len(clean_words(t)), "captions": [c.strip() for c in caps],
                        "headings": [h.strip() for h in heads][:6],
                        "image": str(next((i for i in images if i.name.endswith(f"-{p:0{len(str(n))}d}.png") or i.name.endswith(f"-{p}.png")), ""))})

    result = {"pdf": str(pdf), "pages": n, "images": [str(i) for i in images], "per_page": summary}
    print(f"pdf_check  {pdf}: {n} pages rendered to {out} ({len(images)} images)")
    for row in summary:
        print(f"  p{row['page']:>2}  {row['words']:>4} words  {' '.join(row['captions']) or '-':<40} {'; '.join(row['headings'])[:70]}")

    status = 0
    if args.src_pdf:
        src = Path(args.src_pdf)
        if not src.is_file():
            print(f"pdf_check: no such file {src}", file=sys.stderr)
            return 2
        ns = pages_of(src)
        ws = Counter(clean_words("\n".join(page_text(src, p) for p in range(1, ns + 1))))
        wd = Counter(clean_words("\n".join(page_text(pdf, p) for p in range(1, n + 1))))
        only_src = sorted((ws - wd).items(), key=lambda kv: -kv[1])
        only_dst = sorted((wd - ws).items(), key=lambda kv: -kv[1])
        # numbers deserve a separate look: a changed macro or a lost table cell shows up here
        num_src, num_dst = Counter(w for w in ws.elements() if re.match(r"\d", w)), Counter(w for w in wd.elements() if re.match(r"\d", w))
        num_only_src, num_only_dst = sorted((num_src - num_dst).items()), sorted((num_dst - num_src).items())
        result["text_compare"] = {"src_pdf": str(src), "src_pages": ns, "src_words": sum(ws.values()), "dst_words": sum(wd.values()),
                                  "only_in_source": only_src[:80], "only_in_result": only_dst[:80],
                                  "numbers_only_in_source": num_only_src, "numbers_only_in_result": num_only_dst}
        print(f"\ntext compare  source {ns} pages / {sum(ws.values())} words  vs  result {n} pages / {sum(wd.values())} words")
        print(f"  words only in the source ({len(only_src)} distinct): {', '.join(f'{w}x{c}' for w, c in only_src[:25])}")
        print(f"  words only in the result ({len(only_dst)} distinct): {', '.join(f'{w}x{c}' for w, c in only_dst[:25])}")
        print(f"  numbers only in source: {num_only_src[:20] or 'none'}")
        print(f"  numbers only in result: {num_only_dst[:20] or 'none'}")
        if num_only_src or num_only_dst:
            print("  -> NUMBERS differ between the two renderings: review these before delivering")
            status = 1
        elif only_src or only_dst:
            print("  -> word differences are expected from hyphenation and template text; scan the lists once")
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("next: open every page image; look for overlaps, boxes past the margin, missing graphics, caption order")
    return status


if __name__ == "__main__":
    sys.exit(main())
