#!/usr/bin/env python3
"""End-to-end tests for the paper-migrate scripts.  Standard library only.

    python3 tests/run_tests.py

Builds a migrated copy of the synthetic AAAI-style sample with migrate_tex.py,
checks body_diff.py passes on it and fails on injected content edits, packages
it with make_zip.py, and exercises verify_template.py's manifest and zip guard.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
S = ROOT / "scripts"
FIX = ROOT / "tests" / "fixtures"
PY = sys.executable
failures: list[str] = []


def run(*cmd: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], cwd=cwd, text=True, capture_output=True)


def check(cond: bool, msg: str) -> None:
    print(("  ok    " if cond else "  FAIL  ") + msg)
    if not cond:
        failures.append(msg)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="paper-migrate-tests-"))
    try:
        src = tmp / "src"
        shutil.copytree(FIX / "sample_aaai", src)
        dst = tmp / "dst"
        dst.mkdir()
        shutil.copytree(src / "figures", dst / "figures")
        shutil.copy(src / "refs.bib", dst / "refs.bib")
        # stand-in template files so make_zip's needed-file check has something to see
        for name in ("iclr2027_conference.sty", "iclr2027_conference.bst", "fancyhdr.sty", "natbib.sty"):
            (dst / name).write_text("% stand-in for tests\n")

        print("[1] body_diff self-comparison")
        r = run(PY, S / "body_diff.py", "--src", src / "main.tex", "--dst", src / "main.tex", "--quiet")
        check(r.returncode == 0 and "content=0" in r.stdout, "identical files -> PASS")

        print("[1b] make_rules derives the pair rules from venues.yaml; draft_preamble drafts the preamble")
        rules = tmp / "rules.json"
        r = run(PY, S / "make_rules.py", "--src-venue", "aaai2027", "--dst-venue", "iclr2027", "--out", rules)
        rj = json.loads(rules.read_text()) if rules.exists() else {"ops": []}
        kinds = [(o["op"], o.get("from", o.get("name", o.get("marker", "")))) for o in rj["ops"]]
        check(r.returncode == 0 and ("rename_env", "figure*") in kinds, "two-column -> one-column: starred floats renamed")
        check(("replace_cs", "cite") in kinds and any(o["op"] == "replace_cs" and o["from"] == "shortcite" and o["to"] == "citeyearpar" for o in rj["ops"]),
              "AAAI aliases unaliased: \\cite -> \\citep, \\shortcite -> \\citeyearpar (year-only stays year-only)")
        check(("ensure_bibliographystyle", "iclr2027_conference") in kinds, "explicit bibliographystyle for ICLR")
        check(any(o["op"] == "insert_before" and "AI use statement" in o.get("text", "") for o in rj["ops"]), "required-section TODO comment op")
        r2 = run(PY, S / "make_rules.py", "--src-venue", "iclr2027", "--dst-venue", "aaai2027", "--out", tmp / "rules_back.json")
        rb = json.loads((tmp / "rules_back.json").read_text())
        check(any(o["op"] == "delete_lines" and "bibliographystyle" in o["regex"] for o in rb["ops"]) and any("figure*" in n for n in rb["notes"]),
              "reverse direction: bibliographystyle removed (set by AAAI style), figure* left to the layout pass")
        r3 = run(PY, S / "make_rules.py", "--src-venue", "aaai2027", "--dst-venue", "neurips2026", "--out", tmp / "rules_neu.json")
        rn = json.loads((tmp / "rules_neu.json").read_text())
        check(any(o["op"] == "ensure_bibliographystyle" and o["name"] == "plainnat" for o in rn["ops"]) and
              any(o["op"] == "insert_before" and o["marker"] == "\\end{document}" for o in rn["ops"]),
              "NeurIPS: default bibliographystyle plainnat; checklist TODO at the end of the document")
        drafted = tmp / "preamble_draft.tex"
        r = run(PY, S / "draft_preamble.py", "--src", src / "main.tex", "--src-venue", "aaai2027", "--dst-venue", "iclr2027", "--out", drafted)
        dp = drafted.read_text() if drafted.exists() else ""
        check(r.returncode == 0 and "\\usepackage{iclr2027_conference,times}" in dp and "aaai2027" not in dp.replace("source: AAAI-27", ""),
              "draft has the ICLR style line and no AAAI style line")
        check("\\title{A Toy Paper for Testing Template Migration}" in dp and "Ada Lovelace" in dp and "alan@example.org" in dp, "title and author text carried over")
        check("\\thanks{Corresponding author.}" in dp and "\\corresponding" not in dp and "\\affiliations" not in dp, "AAAI author macros mapped/dropped")
        check("\\pdfinfo" not in dp and "\\frenchspacing" not in dp and "secnumdepth" not in dp, "AAAI mandatory lines dropped")
        check("\\newcommand{\\method}{\\textsc{Toy}}" in dp and "\\newtheorem{theorem}{Theorem}" in dp, "custom macros kept")
        check("%\\iclrfinalcopy" in dp, "anonymisation line for submission emitted")

        print("[2] migrate_tex applies the generated rules with the drafted preamble")
        r = run(PY, S / "migrate_tex.py", "--in", src / "main.tex", "--out", dst / "main.tex",
                "--rules", rules, "--preamble", drafted)
        check(r.returncode == 0, "migrate_tex exit 0")
        out = (dst / "main.tex").read_text()
        check("figure*" not in out and "table*" not in out, "starred floats renamed")
        check("0.9\\columnwidth" in out, "widths untouched by migrate_tex (layout pass owns them)")
        check("\\cite{smith2020" not in out and "\\citep{smith2020" in out, "\\cite -> \\citep")
        check("\\shortcite" not in out, "\\shortcite -> \\citep")
        check("\n\\newpage" not in out and "\n\\clearpage" not in out, "page breaks removed")
        check("\\bibliographystyle{iclr2027_conference}" in out, "bibliographystyle inserted")
        check("TODO(ICLR 2027, REQUIRED)" in out, "AI-use TODO comment inserted")
        check("% a comment with \\newpage inside" in out, "comments untouched")
        check("\\resizebox{\\textwidth}{!}{" in out, "existing resizebox kept")
        r2 = run(PY, S / "migrate_tex.py", "--in", src / "main.tex", "--out", dst / "main.tex")
        check(r2.returncode == 2, "refuses to overwrite without --force")
        check("\\usepackage{wrapfig}" not in out.split("\\begin{document}")[0], "no wrapfig yet (layout pass adds it when needed)")

        print("[2b] layout_figures restores physical sizes and pairs two figures")
        lay = tmp / "layout.json"
        r = run(PY, S / "layout_figures.py", "--src", src / "main.tex", "--in", dst / "main.tex",
                "--out", dst / "main.tex", "--force", "--src-venue", "aaai2027", "--dst-venue", "iclr2027",
                "--pair", "fig:plot,fig:time", "--placement", "t", "--report", lay)
        check(r.returncode == 0, "layout_figures exit 0" + ("" if r.returncode == 0 else f": {r.stderr[-300:]}"))
        out = (dst / "main.tex").read_text()
        lj = json.loads(lay.read_text()) if lay.exists() else {"rows": [], "pairs": []}
        rows = {row["file"] + str(row["idx"]): row for row in lj["rows"]}
        # 0.9 * 3.3125in = 2.98in -> 2.98/5.5 = 0.54 ; 0.3 * 7in = 2.1in -> 0.38 ; 0.8 * 7in = 5.6in -> capped 1
        fr = [round(row.get("dst_frac", -1), 2) for row in lj["rows"]]
        check(fr[:3] == [0.54, 0.38, 1.0], f"widths from physical size: {fr[:3]} (expect [0.54, 0.38, 1.0])")
        check("width=1\\linewidth]{figures/plot.png}" in out, "former figure* capped at 1\\linewidth")
        check(out.count("\\begin{subfigure}[t]") == 2 and out.count("\\begin{figure}") == 2, "two figures combined into one float with two sub-boxes")
        merged_cap = [l for l in out.splitlines() if l.startswith("\\caption{\\subref{fig:plot}")]
        check(bool(merged_cap) and "Error versus training steps." in merged_cap[0] and "\\subref{fig:time} Time versus training steps." in merged_cap[0],
              "one main caption: original texts verbatim after their \\subref markers")
        check(out.count("\\caption{}") == 2 and "\\label{fig:plot}" in out and "\\label{fig:time}" in out, "empty sub-captions print (a)/(b); labels moved onto the sub-boxes")
        check("\\usepackage{subcaption}" in out.split("\\begin{document}")[0], "subcaption package added")
        check(lj["pairs"] and sum(lj["pairs"][0]["fracs"]) <= 0.98 and lj["pairs"][0]["fracs"] == [0.54, 0.38],
              f"paired widths keep physical sizes when they fit ({lj['pairs'] and lj['pairs'][0]['fracs']})")
        check("\\begin{figure}[t]" in out and "\\begin{figure}[h]" not in out, "placement normalised to [t]")
        r = run(PY, S / "layout_figures.py", "--src", src / "main.tex", "--in", dst / "main.tex", "--out", dst / "main.tex", "--force",
                "--src-venue", "aaai2027", "--dst-venue", "iclr2027", "--fit-table", "tab:main", "--report", tmp / "fit.json")
        fj = json.loads((tmp / "fit.json").read_text()) if (tmp / "fit.json").exists() else {"fits": []}
        check(r.returncode == 0 and fj["fits"] and fj["fits"][0]["status"] == "skipped" and "already" in fj["fits"][0].get("reason", ""),
              "--fit-table skips a table that already has a resizebox and says why")

        print("[2c] layout_figures --wrap: wraps when the anchor paragraph is long enough, refuses otherwise")
        wdst = tmp / "wrapdst"
        shutil.copytree(dst, wdst)
        # fresh migrated copy without pairing, then wrap fig:plot at its first reference (long intro paragraph)
        r = run(PY, S / "migrate_tex.py", "--in", src / "main.tex", "--out", wdst / "main.tex", "--force",
                "--rules", rules, "--preamble", drafted)
        wl = tmp / "wrap.json"
        r = run(PY, S / "layout_figures.py", "--src", src / "main.tex", "--in", wdst / "main.tex",
                "--out", wdst / "main.tex", "--force", "--src-venue", "aaai2027", "--dst-venue", "iclr2027",
                "--wrap", "fig:plot@r@0.4", "--wrap", "fig:overview", "--report", wl)
        wj = json.loads(wl.read_text()) if wl.exists() else {"wraps": []}
        wraps = {w["label"]: w for w in wj["wraps"] if w["label"] != "-"}
        wout = (wdst / "main.tex").read_text()
        check(r.returncode == 0, "layout_figures --wrap exit 0" + ("" if r.returncode == 0 else f": {r.stderr[-300:]}"))
        check(wraps.get("fig:plot", {}).get("status") == "wrapped", f"small figure wrapped ({wraps.get('fig:plot', {}).get('reason', '')})")
        check("\\begin{wrapfigure}{r}{0.4\\linewidth}" in wout and "\\label{fig:plot}" in wout, "wrapfigure emitted with caption+label")
        check(wout.count("\\begin{figure}") == 2, "the wrapped figure's original float removed, others kept")
        check("\\usepackage{wrapfig}" in wout.split("\\begin{document}")[0], "wrapfig package added to the preamble")
        intro = wout.split("\\section{Introduction}")[1][:400]
        check("\\begin{wrapfigure}" in intro and intro.index("\\begin{wrapfigure}") < intro.index("Prior work"), "wrap anchored at the referencing paragraph, before its first word")
        check(wraps.get("fig:overview", {}).get("status") == "refused", f"full-width figure refused: {wraps.get('fig:overview', {}).get('reason', '')[:60]}")
        r = run(PY, S / "body_diff.py", "--src", src / "main.tex", "--dst", wdst / "main.tex", "--json", tmp / "wrapdiff.json")
        wd = json.loads((tmp / "wrapdiff.json").read_text())
        check(r.returncode == 0 and wd["hunks"]["content"] == 0, "body_diff PASS after wrapping (0 content hunks)")
        check(wd["hunks"]["structure"] == 1 and "moved block" in wd["hunk_details"]["structure"][0]["note"], "wrap shows as one moved-block structure hunk")

        # tables: estimate width, wrap the small one, move captions above (ICLR rule)
        r = run(PY, S / "layout_figures.py", "--src", src / "main.tex", "--in", wdst / "main.tex",
                "--out", wdst / "main.tex", "--force", "--src-venue", "aaai2027", "--dst-venue", "iclr2027",
                "--list-tables")
        check(r.returncode == 0 and "tab:main" in r.stdout and "wrap candidate" in r.stdout, "--list-tables estimates the small table as a wrap candidate")
        wl2 = tmp / "wrap2.json"
        r = run(PY, S / "layout_figures.py", "--src", src / "main.tex", "--in", wdst / "main.tex",
                "--out", wdst / "main.tex", "--force", "--src-venue", "aaai2027", "--dst-venue", "iclr2027",
                "--wrap", "tab:main", "--table-captions", "above", "--report", wl2)
        wj2 = json.loads(wl2.read_text()) if wl2.exists() else {"wraps": [], "table_captions": {}}
        tw = next((w for w in wj2["wraps"] if w["label"] == "tab:main"), {})
        wout = (wdst / "main.tex").read_text()
        check(tw.get("status") == "wrapped" and tw.get("env") == "wraptable", f"small table wrapped as wraptable ({tw.get('reason', '')[:60]})")
        check(0.2 <= tw.get("frac", 0) <= 0.6, f"table width estimated from its cells ({tw.get('frac')})")
        wt = wout[wout.find("\\begin{wraptable}"):wout.find("\\end{wraptable}")] if "\\begin{wraptable}" in wout else ""
        check(bool(wt) and wt.find("\\caption{Main results") < wt.find("\\begin{tabular}"), "table caption moved above the tabular")
        check("\\resizebox{\\linewidth}" in wt, "resizebox inside the wraptable retargeted to \\linewidth")
        check(wj2["table_captions"]["moved"] >= 1, "caption move counted in the report")
        r = run(PY, S / "body_diff.py", "--src", src / "main.tex", "--dst", wdst / "main.tex", "--json", tmp / "wrapdiff2.json")
        wd2 = json.loads((tmp / "wrapdiff2.json").read_text())
        notes = [h["note"] for h in wd2["hunk_details"]["structure"]]
        check(r.returncode == 0 and wd2["hunks"]["content"] == 0, "body_diff PASS after table wrap + caption move (0 content hunks)")
        check(wd2["hunks"]["structure"] == 2 and all(n.startswith("moved block") for n in notes),
              f"figure wrap + table wrap = two moved-block structure hunks ({len(notes)})")
        # caption repositioning alone (no wrap) is recognised as such
        cdst = tmp / "capdst"
        shutil.copytree(dst, cdst)
        run(PY, S / "migrate_tex.py", "--in", src / "main.tex", "--out", cdst / "main.tex", "--force",
            "--rules", rules, "--preamble", drafted)
        run(PY, S / "layout_figures.py", "--src", src / "main.tex", "--in", cdst / "main.tex", "--out", cdst / "main.tex", "--force",
            "--src-venue", "aaai2027", "--dst-venue", "iclr2027", "--table-captions", "above")
        r = run(PY, S / "body_diff.py", "--src", src / "main.tex", "--dst", cdst / "main.tex", "--json", tmp / "capdiff.json")
        cd = json.loads((tmp / "capdiff.json").read_text())
        cnotes = [h["note"] for h in cd["hunk_details"]["structure"]]
        check(r.returncode == 0 and cd["hunks"]["content"] == 0 and cd["hunks"]["structure"] == 0,
              f"moving a table caption above is normalised away (structure {cd['hunks']['structure']}, content {cd['hunks']['content']}, notes {cnotes})")
        shutil.rmtree(cdst)
        shutil.rmtree(wdst)

        print("[2d] move_table_captions handles a float with two tabulars and two captions")
        sys.path.insert(0, str(S))
        from layout_figures import move_table_captions  # noqa: E402
        two = ("\\begin{table}[t]\n\\centering\n\\small\n\\begin{tabular}{lc}\na & 1 \\\\\n\\end{tabular}\n"
               "\\caption{First.}\n\\label{tab:a}\n\\vspace{1em}\n\\resizebox{\\linewidth}{!}{\n\\begin{tabular}{lc}\nb & 2 \\\\\n\\end{tabular}\n}\n"
               "\\caption{Second.}\n\\label{tab:b}\n\\end{table}\n")
        moved_txt, n = move_table_captions(two, "above")
        ia, ta = moved_txt.index("\\caption{First.}"), moved_txt.index("\\begin{tabular}{lc}\na")
        ib, rb = moved_txt.index("\\caption{Second.}"), moved_txt.index("\\resizebox")
        check(n == 2 and ia < ta and ib < rb and ia < ib, "each caption moved above its own tabular (second one above its resizebox)")
        back, n2 = move_table_captions(moved_txt, "below")
        check(n2 == 2 and back.index("\\caption{First.}") > back.index("\\end{tabular}") and
              back.index("\\caption{Second.}") > back.rindex("\\end{tabular}"), "captions can be moved back below")
        check(sorted(w for w in two.split() if w.startswith("\\caption")) == sorted(w for w in moved_txt.split() if w.startswith("\\caption")),
              "caption text untouched")
        nested = ("\\begin{table}[t]\n\\centering\n\\begin{tabular}{ll}\nx & \\begin{tabular}[c]{@{}l@{}} p \\\\ q \\end{tabular} \\\\\ny & z \\\\\n\\end{tabular}\n"
                  "\\caption{Nested.}\n\\label{tab:n}\n\\end{table}\n")
        mn, nn = move_table_captions(nested, "above")
        check(nn == 1 and mn.index("\\caption{Nested.}") < mn.index("\\begin{tabular}{ll}"), "caption goes above the OUTER tabular, not a nested cell tabular")
        boxed = ("\\begin{table}[t]\n\\caption{\\subref{tab:p} A. \\subref{tab:q} B.}\n"
                 "\\begin{subtable}[t]{0.47\\linewidth}\n\\centering\n\\caption{}\n\\label{tab:p}\n\\setlength{\\tabcolsep}{5pt}\n\\begin{tabular}{l}x\\end{tabular}\n\\end{subtable}\\hfill\n"
                 "\\begin{subtable}[t]{0.5\\linewidth}\n\\centering\n\\caption{}\n\\label{tab:q}\n\\small\n\\setlength{\\tabcolsep}{2pt}\n\\renewcommand{\\arraystretch}{1.08}\n\\begin{tabular}{l}y\\end{tabular}\n\\end{subtable}\n\\end{table}\n")
        same, nb = move_table_captions(boxed, "above")
        check(nb == 0 and same == boxed, "captions already above their tabular inside sub-boxes are left alone (structure beats distance)")

        print("[2e] pair_floats splits a two-table float into sub-tables with one shared caption")
        from layout_figures import pair_floats  # noqa: E402
        two_tab = ("\\begin{table}[t]\n\\centering\n\\small\n\\begin{tabular}{lc}\na & 1 \\\\\n\\end{tabular}\n"
                   "\\caption{First table.}\n\\label{tab:a}\n\\vspace{1em}\n\\begin{tabular}{lc}\nb & 2 \\\\\n\\end{tabular}\n"
                   "\\caption{Second table.}\n\\label{tab:b}\n\\end{table}\n")
        doc = "\\begin{document}\nSee Tab.~\\ref{tab:a} and Tab.~\\ref{tab:b}.\n\n" + two_tab + "\\end{document}\n"
        merged, pinfo = pair_floats(doc, ["tab:a", "tab:b"], 5.5, 0.03, "t", "above")
        check(pinfo["status"] == "paired" and pinfo.get("same_block") and merged.count("\\begin{subtable}") == 2,
              f"same-block split into two subtables ({pinfo.get('reason', pinfo['status'])})")
        mc = [l for l in merged.splitlines() if l.startswith("\\caption{\\subref{tab:a} First table.")]
        check(bool(mc) and "\\subref{tab:b} Second table." in mc[0] and merged.index(mc[0]) < merged.index("\\begin{subtable}"), "shared table caption above the sub-tables")
        check(merged.count("\\small") == 2 and "\\vspace{1em}" not in merged, "prefix font lines copied into each sub-table; spacing between them dropped")
        check(merged.count("\\label{tab:a}") == 1 and merged.count("\\label{tab:b}") == 1 and "\\ref{tab:a}" in merged, "labels kept once each; text references untouched")
        _, pbad = pair_floats(doc, ["tab:a", "fig:none"], 5.5, 0.03, "t", "above")
        check(pbad["status"] == "refused", "unknown label refused")
        far = ("\\begin{document}\n\\begin{table}[t]\\centering\\begin{tabular}{l}x\\end{tabular}\\caption{A.}\\label{tab:x}\\end{table}\n\n"
               "Text.\n\n\\section{Next}\n\nMore.\n\n\\begin{table}[t]\\centering\\begin{tabular}{l}y\\end{tabular}\\caption{B.}\\label{tab:y}\\end{table}\n\\end{document}\n")
        _, pfar = pair_floats(far, ["tab:x", "tab:y"], 5.5, 0.03, "t", "above")
        check(pfar["status"] == "refused" and "section heading" in pfar["reason"], "pairing across a section heading refused (float would leave its discussion)")

        print("[3] body_diff on the migrated copy")
        rep = tmp / "good.json"
        r = run(PY, S / "body_diff.py", "--src", src / "main.tex", "--dst", dst / "main.tex", "--json", rep)
        j = json.loads(rep.read_text())
        check(r.returncode == 0 and j["passed"], "format-only migration -> PASS")
        check(j["hunks"]["content"] == 0, "0 content hunks")
        check(j["hunks"]["format"] >= 2, f"format hunks recorded ({j['hunks']['format']})")
        sh = j["hunk_details"]["structure"]
        check(j["hunks"]["content"] == 0 and all(("repositioned" in h["note"]) or ("moved block" in h["note"]) or (not h["note"]) for h in sh),
              f"pairing yields only layout-level structure hunks ({len(sh)}: {[h['note'].split(':')[0] or 'env change' for h in sh]})")
        check(j["checks"]["title"]["ok"], "title preserved")
        check(not j["checks"]["authors"]["missing_words"] and not j["checks"]["authors"]["missing_emails"],
              "author words and e-mails present in new preamble")
        check(not j["checks"]["citations"]["missing"], "citation keys preserved")
        check(j["cite_variants"]["src"].get("cite") == 1 and j["cite_variants"]["dst"].get("citep") == 1 and j["cite_variants"]["dst"].get("citeyearpar") == 1,
              "cite command variants reported (\\cite->\\citep, \\shortcite->\\citeyearpar)")
        check(any("citation command variants changed" in r for r in j["reviews"]), "variant change listed for review")
        check(all(row.get("equal") for row in j["checks"]["figure_files"]), "figure files hash-equal")
        check(all(row.get("equal") for row in j["checks"]["bib_files"]), "bib file hash-equal")

        print("[4] body_diff catches injected content edits")
        bad = out.replace("41.2\\% to 17.5\\%", "41.3\\% to 17.5\\%", 1)          # number in abstract
        bad = bad.replace("\\citep{smith2020,doe2021}", "\\citep{doe2021}", 1)     # dropped citation
        bad = bad.replace("Error versus training steps.", "Error vs. training steps.", 1)  # caption wording
        bad = bad.replace("the effect is large.\nWe contribute", "the effect is large. We contribute", 1)  # paragraph merge? (same para) 
        bad = bad.replace("\\section{Conclusion}\nWe conclude.\n", "\\section{Conclusion}\n\nWe conclude.\n\nWe also add a sentence.\n", 1)
        (dst / "main_bad.tex").write_text(bad)
        rep2 = tmp / "bad.json"
        r = run(PY, S / "body_diff.py", "--src", src / "main.tex", "--dst", dst / "main_bad.tex", "--json", rep2)
        j2 = json.loads(rep2.read_text())
        check(r.returncode == 1 and not j2["passed"], "content edits -> FAIL")
        check(j2["hunks"]["content"] >= 4, f"content hunks detected ({j2['hunks']['content']})")
        check(j2["checks"]["citations"]["missing"] == ["smith2020"], "missing citation key named")
        joined = " ".join(h["removed"] + " " + h["added"] for h in j2["hunk_details"]["content"])
        check("41.3" in joined and "vs." in joined and "add a sentence" in joined, "hunks show the edited tokens")

        print("[4b] body_diff catches a changed macro definition and hidden/recased text")
        chg = out.replace("\\newcommand{\\method}{\\textsc{Toy}}", "\\newcommand{\\method}{\\textsc{Other}}", 1)
        (dst / "main_def.tex").write_text(chg)
        r = run(PY, S / "body_diff.py", "--src", src / "main.tex", "--dst", dst / "main_def.tex", "--quiet")
        check(r.returncode == 1 and "definitions changed" in r.stdout and "method" in r.stdout, "changed \\newcommand body -> FAIL")
        hid = out.replace("We conclude.", "\\textcolor{white}{We conclude.}", 1)
        (dst / "main_hid.tex").write_text(hid)
        r = run(PY, S / "body_diff.py", "--src", src / "main.tex", "--dst", dst / "main_hid.tex", "--quiet")
        check(r.returncode == 1, "text wrapped in \\textcolor{white} -> content hunk (not whitelisted)")
        (dst / "main_def.tex").unlink(); (dst / "main_hid.tex").unlink()

        print("[5] body_diff flags a dropped macro definition")
        broken = out.replace("\\newcommand{\\method}{\\textsc{Toy}}\n", "")
        (dst / "main_nomacro.tex").write_text(broken)
        r = run(PY, S / "body_diff.py", "--src", src / "main.tex", "--dst", dst / "main_nomacro.tex", "--quiet")
        check(r.returncode == 1 and "method" in r.stdout, "missing \\method reported")
        (dst / "main_bad.tex").unlink()
        (dst / "main_nomacro.tex").unlink()

        print("[5b] check_compliance re-checks the migrated project against the ICLR rules")
        comp = tmp / "compliance.json"
        r = run(PY, S / "check_compliance.py", "--venue", "iclr2027", "--project", dst, "--main", "main.tex",
                "--stage", "submission", "--src", src / "main.tex", "--json", comp)
        cj = json.loads(comp.read_text()) if comp.exists() else {"checks": [], "summary": {}}
        by = {c["rule"]: c for c in cj["checks"]}
        check(by.get("venue style file loaded", {}).get("status") == "pass", "style line detected")
        check(by.get("anonymisation state for submission", {}).get("status") == "pass", "\\iclrfinalcopy commented -> anonymous")
        check(by.get("bibliography style", {}).get("status") == "pass", "bibliographystyle iclr2027_conference present")
        check(by.get("AI use statement (required)", {}).get("status") == "author action", "missing required section -> author action")
        check(by.get("official template files present and unmodified", {}).get("status") == "FAIL", "stand-in .sty files reported as FAIL (hash mismatch)")
        # a project with the caption on the wrong side and a forbidden state must FAIL
        bad2 = (dst / "main.tex").read_text().replace("%\\iclrfinalcopy", "\\iclrfinalcopy")
        (dst / "main_bad2.tex").write_text(bad2)
        r = run(PY, S / "check_compliance.py", "--venue", "iclr2027", "--project", dst, "--main", "main_bad2.tex", "--stage", "submission", "--json", tmp / "c2.json")
        c2 = {c["rule"]: c for c in json.loads((tmp / "c2.json").read_text())["checks"]}
        check(r.returncode == 1 and c2["anonymisation state for submission"]["status"] == "FAIL", "active \\iclrfinalcopy at submission -> FAIL")
        (dst / "main_bad2.tex").unlink()
        r = run(PY, S / "check_compliance.py", "--venue", "aaai2027", "--project", dst, "--main", "main.tex", "--stage", "submission", "--json", tmp / "c3.json")
        c3 = {c["rule"]: c for c in json.loads((tmp / "c3.json").read_text())["checks"]}
        check(c3.get("no forbidden packages", {}).get("status") == "FAIL" and "hyperref" in c3["no forbidden packages"]["detail"], "AAAI rules: hyperref flagged as forbidden")

        print("[5c] make_report assembles the report with an author to-do list")
        rep_md = tmp / "REPORT.md"
        r = run(PY, S / "make_report.py", "--paper", "Toy", "--src-venue", "aaai2027", "--dst-venue", "iclr2027", "--stage", "submission",
                "--rules", rules, "--body-diff", rep, "--layout", lay, "--compliance", comp, "--deliverable", "toy.zip", "--out", rep_md)
        md = rep_md.read_text() if rep_md.exists() else ""
        check(r.returncode == 0 and md.startswith("# Migration report: Toy, AAAI-27 -> ICLR 2027"), "report written with title")
        check("## Author to-do" in md and "AI use statement" in md.split("## Template")[0], "author to-do lists the missing required section first")
        check("compile_check: not run" in md and "## Rule check against ICLR 2027" in md, "missing inputs reported as not run; rule table present")

        print("[5d] reverse direction: the migrated ICLR sample -> AAAI-27 (two-column target)")
        rev = tmp / "rev"
        rev.mkdir()
        shutil.copytree(dst / "figures", rev / "figures")
        shutil.copy(dst / "refs.bib", rev / "refs.bib")
        for name in ("aaai2027.sty", "aaai2027.bst"):
            (rev / name).write_text("% stand-in for tests\n")
        rr = tmp / "rules_rev.json"
        run(PY, S / "make_rules.py", "--src-venue", "iclr2027", "--dst-venue", "aaai2027", "--out", rr)
        pr = tmp / "preamble_rev.tex"
        r = run(PY, S / "draft_preamble.py", "--src", dst / "main.tex", "--src-venue", "iclr2027", "--dst-venue", "aaai2027", "--stage", "submission", "--out", pr)
        dp2 = pr.read_text() if pr.exists() else ""
        check(r.returncode == 0 and "\\usepackage[submission]{aaai2027}" in dp2, "AAAI style line for the submission stage")
        check("hyperref" not in dp2 and "wrapfig" not in dp2 and "\\iclrfinalcopy" not in dp2, "packages AAAI forbids and the ICLR toggle removed")
        check("\\affiliations{" in dp2 and "\\pdfinfo{" in dp2 and "\\frenchspacing" in dp2, "AAAI affiliations block and mandatory lines emitted")
        r = run(PY, S / "migrate_tex.py", "--in", dst / "main.tex", "--out", rev / "main.tex", "--rules", rr, "--preamble", pr)
        lr = tmp / "layout_rev.json"
        r = run(PY, S / "layout_figures.py", "--src", dst / "main.tex", "--in", rev / "main.tex", "--out", rev / "main.tex", "--force",
                "--src-venue", "iclr2027", "--dst-venue", "aaai2027", "--placement", "t", "--report", lr)
        lrj = json.loads(lr.read_text()) if lr.exists() else {}
        rout = (rev / "main.tex").read_text()
        check(r.returncode == 0 and (lrj.get("two_column_target") or {}).get("figure_env_changes", 0) >= 1, "wide floats starred for the two-column target")
        check("\\begin{figure*}" in rout and "\\textwidth" in rout, "full-width graphic sits in figure* sized in \\textwidth")
        check("\\begin{wrapfigure}" not in rout and "\\begin{wraptable}" not in rout, "no wrapped floats in a two-column target")
        r = run(PY, S / "body_diff.py", "--src", dst / "main.tex", "--dst", rev / "main.tex", "--json", tmp / "rev_diff.json")
        rd = json.loads((tmp / "rev_diff.json").read_text())
        check(r.returncode == 0 and rd["hunks"]["content"] == 0, "reverse migration keeps every word (content 0)")
        r = run(PY, S / "check_compliance.py", "--venue", "aaai2027", "--project", rev, "--main", "main.tex", "--stage", "submission", "--json", tmp / "rev_comp.json")
        rc = json.loads((tmp / "rev_comp.json").read_text())
        fails = [c["rule"] for c in rc["checks"] if c["status"] == "FAIL"]
        check(fails == ["official template files present and unmodified"], f"AAAI rules: only the stand-in style files fail ({fails})")
        by2 = {c["rule"]: c["status"] for c in rc["checks"]}
        check(by2.get("no forbidden packages") == "pass" and by2.get("anonymisation state for submission") == "pass" and by2.get("bibliography style left to the style file") == "pass",
              "AAAI: no forbidden packages, [submission] active, no explicit bibliographystyle")

        print("[6] make_zip packages an Overleaf-ready archive")
        z = tmp / "out.zip"
        r = run(PY, S / "make_zip.py", "--project", dst, "--main", "main.tex", "--out", z, "--venue", "iclr2027")
        check(r.returncode == 0 and z.exists(), "zip written")
        with zipfile.ZipFile(z) as zf:
            names = set(zf.namelist())
        check({"main.tex", "refs.bib", "figures/plot.png", "iclr2027_conference.sty"} <= names, "main, bib, figure, sty at root")
        check(Path(str(z) + ".sha256.txt").exists(), "hash sidecar written")
        (dst / "prompts.json").write_text('{"system": "toy"}\n')
        (dst / "body_diff.json").write_text("{}\n")
        r = run(PY, S / "make_zip.py", "--project", dst, "--main", "main.tex", "--out", tmp / "out3.zip", "--venue", "iclr2027")
        with zipfile.ZipFile(tmp / "out3.zip") as zf:
            n3 = set(zf.namelist())
        check("prompts.json" in n3 and "body_diff.json" not in n3, "paper data .json kept, evidence .json excluded")
        (dst / "prompts.json").unlink(); (dst / "body_diff.json").unlink()
        (dst / "aaai2027.sty").write_text("% foreign\n")
        r = run(PY, S / "make_zip.py", "--project", dst, "--main", "main.tex", "--out", tmp / "out2.zip", "--venue", "iclr2027")
        check(r.returncode == 1 and "foreign template file" in r.stdout, "refuses to ship another venue's .sty")
        (dst / "aaai2027.sty").unlink()

        print("[7] verify_template: manifest parses, hashes compare, zip guard refuses traversal")
        r = run(PY, S / "verify_template.py", "--venue", "iclr2027", "--dir", dst, "--json", tmp / "vt.json")
        vt = json.loads((tmp / "vt.json").read_text())
        check(r.returncode == 1 and any(f["status"] == "MISMATCH" for f in vt["files"]), "stand-in files reported as MISMATCH")
        evil = tmp / "evil.zip"
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("iclr2027/../../escape.txt", "x")
        r = run(PY, S / "verify_template.py", "--venue", "iclr2027", "--zip", evil, "--extract-to", tmp / "ex1")
        check(r.returncode == 2 and "traversal" in r.stderr, "path traversal refused")
        good = tmp / "good.zip"
        with zipfile.ZipFile(good, "w") as zf:
            zf.writestr("iclr2027/iclr2027_conference.sty", "% stand-in\n")
        r = run(PY, S / "verify_template.py", "--venue", "iclr2027", "--zip", good, "--extract-to", tmp / "ex2")
        check((tmp / "ex2" / "iclr2027_conference.sty").exists(), "root_in_zip prefix stripped on extract")
        check(r.returncode == 1 and "MISMATCH" in r.stdout, "zip with wrong content -> FAIL")
        # built-in YAML reader must agree with PyYAML when available
        sys.path.insert(0, str(S))
        from verify_template import mini_yaml  # noqa: E402
        m = mini_yaml((ROOT / "venues.yaml").read_text())
        check({"iclr2027", "aaai2027", "neurips2026", "acl2026"} <= set(m["venues"]), "mini_yaml reads all registered venues")
        check(isinstance(m["venues"]["iclr2027"]["template"]["anonymization"]["submission"], dict), "mini_yaml parses flow mappings")
        check(len(m["venues"]["iclr2027"]["template"]["files"]) == 7, "mini_yaml reads the 7 ICLR file hashes")
        check(m["venues"]["iclr2027"]["rules"]["page_limit"]["submission"] == 9, "mini_yaml reads nested ints")
        check(m["venues"]["iclr2027"]["rules"]["required_sections"][0]["status"] == "required", "mini_yaml reads block lists of maps")
        try:
            import yaml  # type: ignore
            full = yaml.safe_load((ROOT / "venues.yaml").read_text())
            check(full["venues"]["iclr2027"]["template"]["files"] == m["venues"]["iclr2027"]["template"]["files"],
                  "mini_yaml agrees with PyYAML on file hashes")
        except ImportError:
            print("  skip  PyYAML not installed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} FAILED:")
        for f in failures:
            print("  - " + f)
        return 1
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
