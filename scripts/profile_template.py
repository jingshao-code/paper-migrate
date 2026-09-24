#!/usr/bin/env python3
"""profile_template.py -- read a venue's official template package and extract the facts a
migration needs, BEFORE anything is converted.

    profile_template.py --dir ~/Desktop/iclr2027 --venue-id iclr2027 [--json profile.json] [--yaml]

Looks at the style files (.sty/.cls) and the sample .tex in the directory and reports:

  geometry        text width/height, columns, paper size (from \\textwidth, \\twocolumn, ...)
  style line      the \\documentclass and \\usepackage line the sample uses
  packages        what the style file itself loads (natbib, hyperref, fontenc, times, ...)
  options         option/toggle names the style declares (final, preprint, submission, review,
                  nonatbib, ...) and \\...finalcopy-style macros -> anonymisation mechanism
  citations       author-year vs numeric (natbib \\setcitestyle / \\bibpunct / cite commands used)
  bibliography    \\bibliographystyle used by the sample, .bst files present
  captions        sentences in the sample about where figure/table captions go
  page limit      sentences mentioning page limits / "pages"
  required        sentences mentioning required / mandatory sections, statements, checklists
  anonymity       sentences about anonymity / double-blind / author names

Everything textual is QUOTED from the package so the agent (and the user) can confirm it
against the official call for papers; fields that cannot be derived are marked TODO_CONFIRM.
With --yaml a venues.yaml entry skeleton is printed.  Standard library only; read-only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

UNIT_IN = {"in": 1.0, "cm": 1 / 2.54, "mm": 1 / 25.4, "pt": 1 / 72.27, "bp": 1 / 72.0, "pc": 12 / 72.27}
KEY_SENTENCES = {
    "captions": r"caption|title (?:always )?appears?",
    "page_limit": r"page limit|pages? (?:long|maximum|max|limit|at most|not exceed)|limited to \d+ pages|\b\d+ pages\b",
    "required": r"\brequired\b|\bmandatory\b|\bmust (?:be )?includ|checklist|statement",
    "anonymity": r"anonym|double[- ]blind|author names|de-identif|identity",
    "fonts": r"type 1|type 3|truetype|font(?:s)? (?:must|should)",
    "appendix": r"appendi|supplement",
}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def strip_comments(text: str) -> str:
    out = []
    for line in text.splitlines():
        i, n = 0, len(line)
        cut = n
        while i < n:
            if line[i] == "\\":
                i += 2
                continue
            if line[i] == "%":
                cut = i
                break
            i += 1
        out.append(line[:cut])
    return "\n".join(out)


def to_inches(num: str, unit: str) -> float | None:
    try:
        return float(num) * UNIT_IN[unit]
    except (KeyError, ValueError):
        return None


def geometry(sty: str) -> dict:
    g: dict = {}
    for name in ("textwidth", "textheight", "columnsep", "oddsidemargin", "topmargin"):
        vals = re.findall(r"\\(?:setlength\s*\{?\\" + name + r"\}?|" + name + r")\s*=?\s*\{?\s*(-?[0-9.]+)\s*(?:true\s*)?(in|cm|mm|pt|bp|pc)\b",
                          sty)
        vals += re.findall(r"(?<![A-Za-z\\])" + name + r"\s*=\s*(-?[0-9.]+)\s*(in|cm|mm|pt|bp|pc)\b", sty)   # geometry package keys
        if vals:
            num, unit = vals[-1]
            g[name + "_in"] = round(to_inches(num, unit) or 0, 4)
    cols = 2 if re.search(r"\\twocolumn\b|\\@twocolumntrue|\\columnsep", sty) and re.search(r"\\twocolumn|twocolumntrue", sty) else 1
    g["columns"] = cols
    if cols == 2 and "textwidth_in" in g:
        g["column_width_in"] = round((g["textwidth_in"] - g.get("columnsep_in", 0.25)) / 2, 4)
    elif "textwidth_in" in g:
        g["column_width_in"] = g["textwidth_in"]
    pw = re.findall(r"\\(?:setlength\s*\{?\\paperwidth\}?|paperwidth)\s*\{?\s*([0-9.]+)\s*(in|cm|mm|pt)", sty)
    if pw:
        w = to_inches(*pw[-1])
        g["paper"] = "letter" if w and abs(w - 8.5) < 0.05 else ("a4" if w and abs(w - 8.27) < 0.05 else f"{w:.2f}in wide")
    elif re.search(r"letterpaper", sty):
        g["paper"] = "letter"
    elif re.search(r"a4paper", sty):
        g["paper"] = "a4"
    return g


def packages_loaded(sty: str) -> list[str]:
    out = []
    for m in re.finditer(r"\\(?:RequirePackage|usepackage)\s*(\[[^\]]*\])?\s*\{([^}]*)\}", sty):
        for p in m.group(2).split(","):
            if p.strip():
                out.append(p.strip())
    return sorted(set(out))


def options_declared(sty: str) -> dict:
    opts = sorted(set(re.findall(r"\\DeclareOption\s*\{([A-Za-z*]+)\}", sty)))
    newifs = sorted(set(re.findall(r"\\newif\s*\\if([A-Za-z@]+)", sty)))
    toggles = sorted(set(re.findall(r"\\def\s*\\([A-Za-z]*(?:final|camera|preprint|anonym|review|submission)[A-Za-z]*)\b", sty, re.I)))
    anon_words = [o for o in opts + newifs + toggles if re.search(r"final|preprint|anonym|review|submission|camera|accepted", o, re.I)]
    return {"options": opts, "newifs": newifs, "toggle_macros": toggles, "anonymisation_candidates": sorted(set(anon_words))}


def citation_style(sty: str, sample: str) -> dict:
    d: dict = {}
    m = re.search(r"\\setcitestyle\s*\{([^}]*)\}", sty)
    if m:
        d["setcitestyle"] = m.group(1)
        d["style"] = "authoryear" if "authoryear" in m.group(1) else ("numeric" if "numbers" in m.group(1) else "TODO_CONFIRM")
    m = re.search(r"\\bibpunct(\[[^\]]*\])?\s*((?:\{[^}]*\}){6})", sty)
    if m and "style" not in d:
        d["bibpunct"] = m.group(2)
    used = sorted(set(re.findall(r"\\(citep|citet|cite|citealp|citeauthor|citeyear|shortcite|newcite)\b", sample)))
    d["commands_in_sample"] = used
    if "style" not in d:
        if "citep" in used or "citet" in used:
            d["style"] = "authoryear"
        elif "cite" in used and "natbib" not in sty:
            d["style"] = "numeric"
        else:
            d["style"] = "TODO_CONFIRM"
    d["natbib_loaded_by_style"] = bool(re.search(r"\\RequirePackage\s*(\[[^\]]*\])?\s*\{natbib\}", sty))
    return d


def sentences(text: str, pattern: str, limit: int = 8) -> list[str]:
    plain = re.sub(r"\\(?:section|subsection|subsubsection|paragraph)\*?\{([^}]*)\}", r" \1. ", text)
    plain = re.sub(r"\\(?:textbf|emph|textit|texttt)\{([^}]*)\}", r"\1", plain)
    plain = re.sub(r"\\[A-Za-z@]+\*?(\[[^\]]*\])?", " ", plain)
    plain = re.sub(r"[{}~]", " ", plain)
    plain = re.sub(r"\s+", " ", plain)
    out = []
    for s in re.split(r"(?<=[.!?])\s+", plain):
        if re.search(pattern, s, re.I) and 15 < len(s) < 400:
            out.append(s.strip())
        if len(out) >= limit:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True, help="extracted official template directory")
    ap.add_argument("--venue-id", default="VENUE_ID")
    ap.add_argument("--json", help="write the profile here")
    ap.add_argument("--yaml", action="store_true", help="print a venues.yaml entry skeleton")
    args = ap.parse_args()

    d = Path(args.dir).resolve()
    if not d.is_dir():
        print(f"profile_template: not a directory: {d}", file=sys.stderr)
        return 2
    files = sorted(p for p in d.rglob("*") if p.is_file() and not p.is_symlink() and ".git" not in p.parts)
    stys = [p for p in files if p.suffix in (".sty", ".cls")]
    bsts = [p for p in files if p.suffix == ".bst"]
    texs = [p for p in files if p.suffix == ".tex"]
    # the venue's own style: the one that is not a well-known helper
    helpers = {"natbib.sty", "fancyhdr.sty", "times.sty", "hyperref.sty", "graphicx.sty", "url.sty"}
    main_sty = next((p for p in stys if p.name not in helpers), stys[0] if stys else None)
    # the sample paper: a .tex with \documentclass, preferring the one named like the style file
    with_dc = [p for p in texs if re.search(r"\\documentclass", p.read_text(encoding="utf-8", errors="replace"))]
    sample = None
    if with_dc:
        sample = next((p for p in with_dc if main_sty and p.stem.replace("_conference", "") in (main_sty.stem, main_sty.stem.replace("_conference", ""))),
                      max(with_dc, key=lambda p: p.stat().st_size))
    elif texs:
        sample = max(texs, key=lambda p: p.stat().st_size)
    sty_text = strip_comments(main_sty.read_text(encoding="utf-8", errors="replace")) if main_sty else ""
    sample_raw = sample.read_text(encoding="utf-8", errors="replace") if sample else ""
    sample_text = strip_comments(sample_raw)

    prof: dict = {"venue_id": args.venue_id, "dir": str(d),
                  "files": {p.relative_to(d).as_posix(): sha256(p) for p in files},
                  "style_file": main_sty.name if main_sty else None,
                  "bst_files": [p.name for p in bsts],
                  "sample_tex": sample.name if sample else None}
    prof["geometry"] = geometry(sty_text)
    prof["style_loads"] = packages_loaded(sty_text)
    prof["options"] = options_declared(sty_text)
    m = re.search(r"\\documentclass\s*(\[[^\]]*\])?\s*\{[^}]*\}", sample_text)
    prof["documentclass"] = m.group(0) if m else "TODO_CONFIRM"
    style_line = None
    if main_sty:
        base = main_sty.stem
        m = re.search(r"\\usepackage\s*(\[[^\]]*\])?\s*\{[^}]*" + re.escape(base) + r"[^}]*\}", sample_raw)
        style_line = m.group(0) if m else None
        # the commented alternative lines around it (e.g. %\usepackage[final]{...}) reveal the toggles
        alts = re.findall(r"^\s*%\s*(\\usepackage\s*(?:\[[^\]]*\])?\s*\{[^}]*" + re.escape(base) + r"[^}]*\}.*)$", sample_raw, re.M)
        prof["style_line_alternatives_commented"] = [a.strip() for a in alts]
    prof["style_line"] = style_line or "TODO_CONFIRM"
    toggles = re.findall(r"^\s*(%?)\s*\\([A-Za-z]*(?:finalcopy|final|preprint|anonymous)[A-Za-z]*)\s*(%.*)?$", sample_raw, re.M)
    prof["sample_toggle_lines"] = [{"commented": bool(c), "macro": "\\" + n, "comment": (cm or "").strip()} for c, n, cm in toggles]
    prof["packages_after_style_in_sample"] = [m.group(0) for m in re.finditer(r"\\usepackage\s*(\[[^\]]*\])?\s*\{(?!" + re.escape(main_sty.stem if main_sty else "___") + r")[^}]*\}", sample_text)]
    prof["citations"] = citation_style(sty_text, sample_text)
    m = re.search(r"\\bibliographystyle\s*\{([^}]*)\}", sample_text)
    prof["bibliographystyle_in_sample"] = m.group(1) if m else ("set by the style file" if re.search(r"\\bibliographystyle", sty_text) else "TODO_CONFIRM")
    m = re.search(r"\\setcounter\s*\{secnumdepth\}\s*\{(\d)\}", sty_text + sample_text)
    prof["section_numbering"] = ("unnumbered" if m and m.group(1) == "0" else "numbered")
    prof["sample_sections"] = re.findall(r"\\(?:sub)?section\*?\s*\{([^}]*)\}", sample_text)[:40]
    prof["quotes"] = {k: sentences(sample_text, pat) for k, pat in KEY_SENTENCES.items()}
    cap = " ".join(prof["quotes"]["captions"]).lower()
    prof["captions_inferred"] = {
        "figure": "below" if re.search(r"figure.*(after|below|under)", cap) else ("above" if re.search(r"figure.*(before|above)", cap) else "TODO_CONFIRM"),
        "table": "above" if re.search(r"table.*(before|above)", cap) else ("below" if re.search(r"table.*(after|below)", cap) else "TODO_CONFIRM"),
    }
    m = re.search(r"(\d+)\s*pages?", " ".join(prof["quotes"]["page_limit"]))
    prof["page_limit_inferred"] = int(m.group(1)) if m else "TODO_CONFIRM"

    print(f"profile_template  {d}")
    print(f"style file        {prof['style_file']}   bst: {prof['bst_files']}   sample: {prof['sample_tex']}")
    print(f"geometry          {prof['geometry']}")
    print(f"documentclass     {prof['documentclass']}")
    print(f"style line        {prof['style_line']}   alternatives: {prof.get('style_line_alternatives_commented')}")
    print(f"toggles in sample {prof['sample_toggle_lines']}")
    print(f"style loads       {prof['style_loads']}")
    print(f"options/newifs    {prof['options']}")
    print(f"citations         {prof['citations']}")
    print(f"bibliographystyle {prof['bibliographystyle_in_sample']}   sections {prof['section_numbering']}")
    print(f"captions          {prof['captions_inferred']}")
    print(f"page limit        {prof['page_limit_inferred']} (from the sample text; confirm on the CFP page)")
    for k, qs in prof["quotes"].items():
        for q in qs[:4]:
            print(f"  [{k}] {q[:160]}")
    if args.json:
        Path(args.json).write_text(json.dumps(prof, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.yaml:
        g = prof["geometry"]
        pkgs_after = []
        for line in prof["packages_after_style_in_sample"]:
            mm = re.search(r"\{([^}]*)\}", line)
            if mm:
                pkgs_after.extend(x.strip() for x in mm.group(1).split(","))
        author_hint = "TODO_CONFIRM  # copy the author block shape from " + str(prof["sample_tex"])
        anon_cands = prof["options"]["anonymisation_candidates"] + [t["macro"] for t in prof["sample_toggle_lines"]]
        needed = [p.name for p in stys + bsts]
        print("\n# ---- venues.yaml skeleton (confirm every TODO_CONFIRM against the official CFP) ----")
        print(f"""  {args.venue_id}:
    name: TODO_CONFIRM
    kind: conference
    official:
      author_guidelines: TODO_CONFIRM
      call_for_papers: TODO_CONFIRM
      template_zip: TODO_CONFIRM
      template_zip_sha256: TODO_CONFIRM
      verified_on: TODO_CONFIRM
      bundled_copy: null
    layout:
      columns: {g.get('columns', 'TODO_CONFIRM')}
      text_width_in: {g.get('textwidth_in', 'TODO_CONFIRM')}
      column_width_in: {g.get('column_width_in', 'TODO_CONFIRM')}
      text_height_in: {g.get('textheight_in', 'TODO_CONFIRM')}
      source: '{prof['style_file']}'
    template:
      files:""")
        for f, h in prof["files"].items():
            print(f"        {f}: {h}")
        print(f"""      needed_in_project: {needed}
      documentclass: '{prof['documentclass']}'
      style_line: '{prof['style_line']}'
      packages_after_style: {pkgs_after}
      preamble_extras: []
      bibliographystyle: {prof['bibliographystyle_in_sample']}
      citation_style: {prof['citations'].get('style')}
      natbib_loaded_by_style: {str(prof['citations'].get('natbib_loaded_by_style')).lower()}
      section_numbering: {prof['section_numbering']}
      author_block: '{author_hint}'
      venue_macros: []
      anonymization:
        mechanism: TODO_CONFIRM   # candidates: {anon_cands}
        submission: {{must_match: null, must_not_match: null}}
        camera_ready: {{must_match: null, must_not_match: null}}
    rules:
      page_limit: {{submission: {prof['page_limit_inferred']}, camera_ready: TODO_CONFIRM, counts: TODO_CONFIRM, excluded: [], source: TODO_CONFIRM}}
      required_sections: []     # list of {{name, status: required|recommended, pattern (regex on the .tex), counts_toward_limit, source}}
      captions: {{figure: {prof['captions_inferred']['figure']}, table: {prof['captions_inferred']['table']}, source: TODO_CONFIRM}}
      paper_size: {g.get('paper', 'TODO_CONFIRM')}
      forbidden_packages: []
      forbidden_commands: []
      anonymity: TODO_CONFIRM
      references_page: TODO_CONFIRM""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
