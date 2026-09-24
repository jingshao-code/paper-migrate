#!/usr/bin/env python3
"""check_compliance.py -- re-check a migrated project against the TARGET venue's recorded rules.

    check_compliance.py --venue iclr2027 --project new/ --main main.tex --stage submission \
                        [--src orig/main.tex] [--pdf build/main.pdf] [--json compliance.json]

Every rule in venues.<venue> that can be checked mechanically is checked; each result is one of

  pass           the project satisfies the rule
  FAIL           a FORMAT violation the migration itself must fix (wrong/foreign style file,
                 forbidden package or command, wrong anonymisation state, caption on the wrong
                 side, missing \\bibliographystyle, wrong paper size)
  author action  something only the authors can decide or write (page count over the limit,
                 a required section that does not exist, identity hints in the body, Type 3 fonts
                 inside figures)
  info           facts the authors should know (page count within the limit, references position)
  n/a            the venue records no such rule, or it cannot be checked here

Exit 0 when there is no FAIL, 1 otherwise, 2 on usage/IO error.  Standard library only, plus
poppler's pdfinfo / pdftotext / pdffonts when a PDF is given and the tools exist.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_template import load_manifest  # noqa: E402
from layout_figures import strip_comments_keep_len, _toplevel_tabulars, GRAPHIC_RE  # noqa: E402
from body_diff import author_words, strip_comments  # noqa: E402


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, text=True, capture_output=True, timeout=120).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def pdf_facts(pdf: Path) -> dict:
    facts: dict = {}
    info = run(["pdfinfo", str(pdf)]) if shutil.which("pdfinfo") else ""
    m = re.search(r"^Pages:\s+(\d+)", info, re.M)
    facts["pages"] = int(m.group(1)) if m else None
    m = re.search(r"^Page size:\s+([\d.]+) x ([\d.]+) pts(?: \((\w+)\))?", info, re.M)
    if m:
        w, h = float(m.group(1)), float(m.group(2))
        facts["page_size_pt"] = [w, h]
        facts["paper"] = "letter" if abs(w - 612) < 2 and abs(h - 792) < 2 else ("a4" if abs(w - 595) < 3 and abs(h - 842) < 3 else "other")
    if facts.get("pages") and shutil.which("pdftotext"):
        for p in range(1, facts["pages"] + 1):
            # reading order (no -layout): in two-column PDFs a heading then sits on its own line
            t = re.sub(r" +", " ", run(["pdftotext", "-f", str(p), "-l", str(p), str(pdf), "-"]))
            if "references_page" not in facts and re.search(r"^\s*\d*\s*r\s?e\s?f\s?e\s?r\s?e\s?n\s?c\s?e\s?s\s*$", t, re.M | re.I):
                facts["references_page"] = p
            if "appendix_page" not in facts and re.search(r"^\s*\d*\s*(?:A\s+)?a\s?p\s?p\s?e\s?n\s?d\s?i\s?x", t, re.M | re.I) and p > 1:
                facts["appendix_page"] = p
    if shutil.which("pdffonts"):
        fonts = run(["pdffonts", str(pdf)])
        facts["type3_fonts"] = [ln.split()[0] for ln in fonts.splitlines()[2:] if "Type 3" in ln]
    # float page vs. the page of its first mention in the text
    if facts.get("pages") and shutil.which("pdftotext"):
        cap_page: dict[str, int] = {}
        first_ref: dict[str, int] = {}
        for p in range(1, facts["pages"] + 1):
            t = re.sub(r"[ \t]+", " ", run(["pdftotext", "-f", str(p), "-l", str(p), "-layout", str(pdf), "-"]))
            for m in re.finditer(r"^\s*\d*\s*(Table|Figure) (\d+)[a-z]?:", t, re.M):
                cap_page.setdefault(f"{m.group(1)} {m.group(2)}", p)
            body_t = re.sub(r"^\s*\d*\s*(Table|Figure) \d+[a-z]?:.*$", "", t, flags=re.M)
            for m in re.finditer(r"\b(Tab|Table|Fig|Figure)\.?~?\s?(\d+)[a-z]?\b", body_t):
                key = ("Table" if m.group(1).startswith("Tab") else "Figure") + f" {m.group(2)}"
                first_ref.setdefault(key, p)
        facts["floats_before_first_mention"] = sorted(
            f"{k} on p.{cap_page[k]}, first mentioned p.{first_ref[k]}" for k in cap_page if k in first_ref and cap_page[k] < first_ref[k])
        facts["floats_far_after_mention"] = sorted(
            f"{k} on p.{cap_page[k]}, first mentioned p.{first_ref[k]}" for k in cap_page if k in first_ref and cap_page[k] > first_ref[k] + 1)
    return facts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--venue", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--main", default="main.tex")
    ap.add_argument("--stage", default="submission", choices=["submission", "rebuttal", "camera_ready"])
    ap.add_argument("--src", help="original main .tex (for the identity scan)")
    ap.add_argument("--pdf", help="compiled PDF (page facts)")
    ap.add_argument("--manifest", default=str(Path(__file__).resolve().parent.parent / "venues.yaml"))
    ap.add_argument("--json")
    args = ap.parse_args()
    try:
        man = load_manifest(Path(args.manifest))
        venue = man["venues"][args.venue]
        project = Path(args.project).resolve(strict=True)
        raw = (project / args.main).read_text(encoding="utf-8")
    except (OSError, KeyError, ValueError) as exc:
        print(f"check_compliance: {exc}", file=sys.stderr)
        return 2
    clean = strip_comments(raw)
    m = re.search(r"\\begin\s*\{document\}", clean)
    pre, body = (clean[:m.start()], clean[m.end():]) if m else (clean, "")
    tpl, rules = venue.get("template") or {}, venue.get("rules") or {}
    checks: list[dict] = []

    def add(rule: str, status: str, detail: str, source: str = "") -> None:
        checks.append({"rule": rule, "status": status, "detail": detail, "source": source})

    # ---- template files -----------------------------------------------------
    files = tpl.get("files") or {}
    needed = tpl.get("needed_in_project") or list(files)
    bad = []
    for name in needed:
        p = project / name
        if not p.is_file():
            bad.append(f"{name} missing")
        elif name in files and sha256(p) != files[name]:
            bad.append(f"{name} differs from the official file")
    add("official template files present and unmodified", "FAIL" if bad else "pass",
        "; ".join(bad) if bad else f"{len(needed)} file(s) match venues.yaml", "venues.yaml template.files")
    foreign = set()
    for vid, v in man["venues"].items():
        if vid != args.venue:
            foreign |= set(((v.get("template") or {}).get("files") or {}))
    foreign -= set(files)
    present_foreign = sorted(n for n in foreign if (project / n).exists())
    add("no other venue's template files in the project", "FAIL" if present_foreign else "pass",
        ", ".join(present_foreign) if present_foreign else "none found")

    # ---- document class / style line ----------------------------------------
    want_dc = str(tpl.get("documentclass") or "")
    have_dc = re.search(r"\\documentclass\s*(\[[^\]]*\])?\s*\{[^}]*\}", pre)
    if want_dc and want_dc != "TODO_CONFIRM":
        ok = have_dc and re.sub(r"\s+", "", have_dc.group(0)) == re.sub(r"\s+", "", want_dc)
        add("document class as in the official sample", "pass" if ok else "FAIL",
            f"{have_dc.group(0) if have_dc else 'none'} (expected {want_dc})")
    style = next((Path(f).stem for f in files if f.endswith((".sty", ".cls"))), None)
    if style:
        ok = re.search(r"\\usepackage\s*(\[[^\]]*\])?\s*\{[^}]*" + re.escape(style) + r"[^}]*\}", pre) or \
             re.search(r"\\documentclass\s*(\[[^\]]*\])?\s*\{" + re.escape(style) + r"\}", pre)
        add("venue style file loaded", "pass" if ok else "FAIL", f"\\usepackage{{...{style}...}} {'found' if ok else 'NOT found'}")

    # ---- forbidden packages / commands --------------------------------------
    fp = rules.get("forbidden_packages") or tpl.get("disallowed_packages") or []
    if fp:
        loaded = set()
        for mm in re.finditer(r"\\(?:usepackage|RequirePackage)\s*(\[[^\]]*\])?\s*\{([^}]*)\}", pre):
            loaded |= {x.strip() for x in mm.group(2).split(",")}
        hit = sorted(set(fp) & loaded)
        add("no forbidden packages", "FAIL" if hit else "pass", ", ".join(hit) if hit else f"{len(fp)} forbidden names checked")
    fc = rules.get("forbidden_commands") or tpl.get("disallowed_commands") or []
    if fc:
        hits = []
        for c in fc:
            name = str(c).split("{")[0].lstrip("\\")
            if re.search(r"\\" + re.escape(name) + r"(?![A-Za-z])" + (r"\s*\{\s*-" if str(c).endswith("{-") else ""), body + pre):
                hits.append(str(c))
        add("no forbidden commands", "FAIL" if hits else "pass", ", ".join(hits) if hits else f"{len(fc)} forbidden commands checked")

    # ---- anonymisation state for the stage -----------------------------------
    anon = (tpl.get("anonymization") or {}).get(args.stage) or {}
    if anon.get("must_match") or anon.get("must_not_match"):
        probs = []
        if anon.get("must_match") and not re.search(anon["must_match"], clean):
            probs.append(f"expected /{anon['must_match']}/")
        if anon.get("must_not_match") and re.search(anon["must_not_match"], clean):
            probs.append(f"must not contain /{anon['must_not_match']}/ (active)")
        add(f"anonymisation state for {args.stage}", "FAIL" if probs else "pass",
            "; ".join(probs) if probs else str((tpl.get("anonymization") or {}).get("mechanism", "ok")))

    # ---- captions --------------------------------------------------------------
    caps = rules.get("captions") or {}
    cb = strip_comments_keep_len(raw)
    if caps.get("table") in ("above", "below"):
        viol = 0
        for mm in re.finditer(r"\\begin\s*\{(table\*?|wraptable)\}", cb):
            e = re.compile(r"\\end\s*\{" + re.escape(mm.group(1)) + r"\}").search(cb, mm.end())
            if not e:
                continue
            blk = cb[mm.start():e.end()]
            tabs = _toplevel_tabulars(blk, 0, len(blk))
            c = re.search(r"\\caption\s*(\[|\{)", blk)
            if tabs and c:
                above = c.start() < tabs[0][0]
                if (caps["table"] == "above") != above:
                    viol += 1
        add(f"table captions {caps['table']} the table", "FAIL" if viol else "pass",
            f"{viol} table(s) on the wrong side" if viol else "all tables comply", caps.get("source", ""))
    if caps.get("figure") in ("above", "below"):
        viol = 0
        for mm in re.finditer(r"\\begin\s*\{(figure\*?|wrapfigure)\}", cb):
            e = re.compile(r"\\end\s*\{" + re.escape(mm.group(1)) + r"\}").search(cb, mm.end())
            if not e:
                continue
            blk = cb[mm.start():e.end()]
            # sub-boxes (subfigure/minipage) carry their own captions; judge only the float's top-level caption
            boxes = [(b.start(), (re.compile(r"\\end\s*\{" + b.group(1) + r"\}").search(blk, b.end()) or b).end())
                     for b in re.finditer(r"\\begin\s*\{(subfigure|minipage|subtable)\}", blk)]
            def top(pos: int) -> bool:
                return not any(a <= pos < z for a, z in boxes)
            caps_top = [c for c in re.finditer(r"\\caption\s*(\[|\{)", blk) if top(c.start())]
            material = [g.start() for g in GRAPHIC_RE.finditer(blk)] + [a for a, _ in boxes]
            if caps_top and material:
                below = caps_top[0].start() > min(material)
                if (caps["figure"] == "below") != below:
                    viol += 1
        add(f"figure captions {caps['figure']} the figure", "FAIL" if viol else "pass",
            f"{viol} figure(s) on the wrong side" if viol else "all figures comply", caps.get("source", ""))

    # ---- bibliography style ------------------------------------------------------
    bst = str(tpl.get("bibliographystyle") or "")
    have = re.findall(r"\\bibliographystyle\s*\{([^}]*)\}", body)
    if bst and bst != "TODO_CONFIRM":
        if bst.lower().startswith("set by"):
            add("bibliography style left to the style file", "FAIL" if have else "pass", f"found {have}" if have else "no \\bibliographystyle in the body")
        elif tpl.get("bibliographystyle_default") or "choice" in bst.lower():
            add("bibliography style", "pass" if len(have) == 1 else "FAIL",
                f"found {have or 'none'}; the venue leaves the style to the authors (registry default {tpl.get('bibliographystyle_default')})")
        else:
            add("bibliography style", "pass" if have == [bst] else "FAIL", f"found {have or 'none'}, expected {bst}")

    # ---- required sections -------------------------------------------------------
    for sec in rules.get("required_sections") or []:
        pat = sec.get("pattern")
        status = str(sec.get("status", "")).lower()
        if not pat:
            add(f"{sec.get('name')} ({status})", "n/a", "no detection pattern recorded", sec.get("source", ""))
            continue
        present = re.search(pat, body, re.I)
        if present:
            add(f"{sec.get('name')} ({status})", "pass", "section present", sec.get("source", ""))
        else:
            add(f"{sec.get('name')} ({status})", "author action" if status in ("required", "recommended") else "info",
                f"not present; {'write it' if status == 'required' else 'consider adding it'} at the % TODO comment", sec.get("source", ""))

    # ---- PDF facts ---------------------------------------------------------------
    facts = pdf_facts(Path(args.pdf)) if args.pdf and Path(args.pdf).is_file() else {}
    lim = rules.get("page_limit") or {}
    limit = lim.get(args.stage) or lim.get("submission") or lim.get("content_pages")
    if facts.get("pages"):
        rp = facts.get("references_page")
        if rp and limit:
            try:
                lim_n = int(limit)
                if rp > lim_n:
                    add("main-text page limit", "author action",
                        f"main text runs into page {rp} (the references start on that page); limit {lim_n} -> up to {rp - lim_n} page(s) over. "
                        f"Nothing was cut; shortening is the authors' decision", lim.get("source", ""))
                elif rp == lim_n:
                    add("main-text page limit", "info", f"main text ends on page {rp} = limit {lim_n}; check the exact end on Overleaf", lim.get("source", ""))
                else:
                    add("main-text page limit", "info", f"main text ends on page {rp}; limit {lim_n}", lim.get("source", ""))
            except (TypeError, ValueError):
                add("main-text page limit", "info", f"main text ends on page {rp}; limit recorded as {limit!r}", lim.get("source", ""))
        else:
            add("main-text page limit", "info", f"{facts['pages']} pages total; references page not detected" if not rp else f"references start on page {rp}; no limit recorded")
        if facts.get("appendix_page"):
            add("appendix position", "info", f"appendix starts on page {facts['appendix_page']}, after the references" if rp and facts["appendix_page"] > rp else f"appendix starts on page {facts['appendix_page']}")
        ps = rules.get("paper_size")
        if ps and facts.get("paper"):
            add("paper size", "pass" if facts["paper"] == str(ps).lower() else "FAIL", f"PDF is {facts['paper']} ({facts.get('page_size_pt')}), rule {ps}")
        if "floats_before_first_mention" in facts:
            early, late = facts["floats_before_first_mention"], facts["floats_far_after_mention"]
            if early or late:
                add("float position vs. first mention", "author action",
                    ("appear before their first mention: " + "; ".join(early) if early else "")
                    + ("; " if early and late else "")
                    + ("more than one page after it: " + "; ".join(late) if late else "")
                    + ". Float order follows the source; move the float in the .tex if you prefer it after the text",
                    "convention: a float appears on or after the page of its first mention")
            else:
                add("float position vs. first mention", "pass", "every figure and table appears on or right after the page of its first mention")
        if facts.get("type3_fonts"):
            add("fonts", "author action" if rules.get("fonts") else "info",
                f"Type 3 fonts embedded ({len(facts['type3_fonts'])}, usually from figure files); "
                + ("this venue requires Type 1/TrueType -- re-export the figures" if rules.get("fonts") else "no font rule recorded for this venue; many venues require Type 1/TrueType"),
                rules.get("fonts", ""))
        elif "type3_fonts" in facts:
            add("fonts", "pass", "no Type 3 fonts", rules.get("fonts", ""))
    else:
        add("main-text page limit", "n/a", "no PDF given; compile (or use Overleaf) and re-run with --pdf", lim.get("source", ""))

    # ---- identity scan -------------------------------------------------------------
    hits = []
    if args.src and Path(args.src).is_file():
        spre = strip_comments(Path(args.src).read_text(encoding="utf-8", errors="replace"))
        spre = spre[:re.search(r"\\begin\s*\{document\}", spre).start()] if re.search(r"\\begin\s*\{document\}", spre) else spre
        words, emails = author_words(spre)
        generic = {"University", "Institute", "Department", "School", "College", "Lab", "Laboratory", "Center", "Centre", "USA", "China", "Boston", "London"}
        for w in sorted(words - generic):
            if len(w) > 3 and re.search(r"\b" + re.escape(w) + r"\b", body):
                hits.append(f"'{w}'")
        for e in emails:
            if e in body:
                hits.append(e)
    for pat, label in ((r"[Aa]cknowledg", "acknowledgements"), (r"github\.com/[\w.-]+", "GitHub URL"),
                       (r"https?://(?!www\.overleaf)[^\s}]+", "URL"), (r"our (?:prior|previous|earlier) work", "self-reference")):
        for mm in re.finditer(pat, body):
            hits.append(f"{label} at body offset {mm.start()}: {body[mm.start():mm.start() + 50].strip()!r}")
    if rules.get("anonymity"):
        add("anonymity (body scan)", "author action" if hits else "pass",
            ("; ".join(hits[:8]) + (" ..." if len(hits) > 8 else "")) if hits else "no institution names, e-mails, acknowledgements or URLs found in the body",
            str(rules.get("anonymity")))
    if rules.get("references_page"):
        add("references position", "info", str(rules["references_page"]))

    # ---- output ----------------------------------------------------------------
    order = {"FAIL": 0, "author action": 1, "info": 2, "pass": 3, "n/a": 4}
    checks.sort(key=lambda c: order.get(c["status"], 9))
    summary = {k: sum(1 for c in checks if c["status"] == k) for k in order}
    print(f"check_compliance  venue={args.venue}  stage={args.stage}  project={project}")
    for c in checks:
        print(f"  [{c['status']:<13}] {c['rule']}: {c['detail'][:150]}")
    print("summary  " + "  ".join(f"{k}={v}" for k, v in summary.items()))
    print("RESULT: " + ("FORMAT CONFLICTS -- fix before delivering" if summary["FAIL"] else "no format conflicts"))
    if args.json:
        Path(args.json).write_text(json.dumps({"venue": args.venue, "stage": args.stage, "checks": checks,
                                               "summary": summary, "pdf_facts": facts}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 1 if summary["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
