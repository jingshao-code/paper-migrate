#!/usr/bin/env python3
"""make_rules.py -- derive the mechanical body rewrites for a venue pair from venues.yaml.

    make_rules.py --src-venue aaai2027 --dst-venue iclr2027 --out rules.json \
                  [--stage submission] [--keep-page-breaks] [--override references/aaai2027-to-iclr2027.json]

The ops are the same JSON that migrate_tex.py executes.  They come from facts recorded for
the two venues (column model, citation style, bibliography style, required sections), so
any pair of venues in the registry can be migrated without hand-written rules.  A pair
override file, when given, has its "ops" appended.  Notes list what the script cannot do
mechanically and what the layout pass must handle.  Standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_template import load_manifest  # noqa: E402



def build(src: dict, dst: dict, stage: str, keep_page_breaks: bool, dst_id: str) -> dict:
    ops: list[dict] = []
    notes: list[str] = []
    s_lay, d_lay = src.get("layout") or {}, dst.get("layout") or {}
    s_tpl, d_tpl = src.get("template") or {}, dst.get("template") or {}
    d_rules = dst.get("rules") or {}

    # ---- column model ------------------------------------------------------
    if int(s_lay.get("columns", 1)) == 2 and int(d_lay.get("columns", 1)) == 1:
        for env in ("figure", "table", "algorithm"):
            ops.append({"op": "rename_env", "from": env + "*", "to": env,
                        "why": "single-column target: spanning floats do not exist"})
    elif int(s_lay.get("columns", 1)) == 1 and int(d_lay.get("columns", 1)) == 2:
        notes.append("two-column target: floats wider than a column must become figure*/table*; "
                     "layout_figures.py decides per float from the physical width")

    # ---- citation commands -------------------------------------------------
    # Meaning must survive: parenthetical stays parenthetical, textual stays textual,
    # year-only stays year-only.  Source venues that alias natbib commands (AAAI:
    # \cite=\citep, \shortcite=\citeyearpar) are unaliased; nothing else is rewritten
    # while the target has natbib, because natbib renders every variant correctly in both
    # author-year and numeric mode.  Without natbib, only \citep/\citealp map to \cite and
    # the textual/year forms are reported as conflicts for the authors.
    s_cite, d_cite = s_tpl.get("citation_style"), d_tpl.get("citation_style")
    s_alias = s_tpl.get("cite_alias") or {}
    d_natbib = bool(d_tpl.get("natbib_loaded_by_style")) or bool(d_tpl.get("natbib_allowed", True))
    for a, b in s_alias.items():
        if a != b:
            ops.append({"op": "replace_cs", "from": a, "to": b,
                        "why": f"the source style defines \\{a} as \\{b}; natbib's own \\{a} renders differently"})
    if d_cite == "numeric" and not d_natbib:
        for a in ("citep", "citealp"):
            ops.append({"op": "replace_cs", "from": a, "to": "cite", "why": "numeric target without natbib"})
        notes.append("CONFLICT to report: \\citet, \\citeauthor, \\citeyear, \\citeyearpar, \\citealt have no plain-\\cite "
                     "equivalent (\"Published in \\citeyear{x}\" would become \"Published in [3]\"); left unchanged, "
                     "the authors must add natbib or reword")
    elif d_cite == "numeric":
        notes.append("numeric target with natbib: \\citep/\\citet/\\citeyear keep their meaning in numbers mode; no rewrite")
    if s_cite and d_cite and s_cite != d_cite:
        notes.append(f"citation style changes {s_cite} -> {d_cite}; the .bib file is untouched")

    # ---- page breaks -------------------------------------------------------
    if not keep_page_breaks:
        ops.append({"op": "delete_lines", "regex": "^\\s*\\\\(newpage|clearpage)\\s*$",
                    "why": "author-inserted page breaks (often before the references) carry the source venue's "
                           "layout; the target's sample template does not use them. Use --keep-page-breaks to keep."})

    # ---- bibliography style ------------------------------------------------
    bst = str(d_tpl.get("bibliographystyle") or "")
    bst_default = d_tpl.get("bibliographystyle_default")
    if bst.lower().startswith("set by"):
        pass  # handled below
    elif bst_default:
        ops.append({"op": "ensure_bibliographystyle", "name": str(bst_default),
                    "why": f"the target ships no .bst ({bst}); {bst_default} is the registry default -- authors may pick another natbib-compatible style"})
        notes.append(f"bibliography style set to {bst_default} (target leaves the choice to the authors)")
    elif bst and bst != "TODO_CONFIRM" and "choice" not in bst.lower():
        ops.append({"op": "ensure_bibliographystyle", "name": bst,
                    "why": "the target requires an explicit \\bibliographystyle"})
    if bst.lower().startswith("set by"):
        ops.append({"op": "delete_lines", "regex": "^\\s*\\\\bibliographystyle\\s*\\{[^}]*\\}\\s*$",
                    "why": "the target style file sets the bibliography style itself"})

    # ---- required / recommended sections -> TODO comments ------------------
    todo_before_bib, todo_end = [], []
    for sec in d_rules.get("required_sections") or []:
        status = str(sec.get("status", "")).lower()
        if status not in ("required", "recommended"):
            continue
        line = (f"% TODO({dst.get('name', dst_id)}, {status.upper()}): {sec.get('name')}"
                + (" -- does not count toward the page limit" if sec.get("counts_toward_limit") is False else "")
                + (f". Source: {sec['source']}" if sec.get("source") else ""))
        if str(sec.get("placement", "")).startswith("end"):
            todo_end.append(line)
        else:
            todo_before_bib.append(line)
    if todo_before_bib:
        ops.append({"op": "insert_before", "marker": "\\bibliography{", "once": True,
                    "text": "\n".join(todo_before_bib) + "\n% paper-migrate inserted these comments only; the sections themselves must be written by the authors.",
                    "why": "target venue requires/recommends sections the source does not have"})
    if todo_end:
        ops.append({"op": "insert_before", "marker": "\\end{document}", "once": True,
                    "text": "\n".join(todo_end) + "\n% paper-migrate inserted these comments only; the material itself must be written by the authors.",
                    "why": "target venue requires material at the end of the document"})

    # ---- notes for the non-mechanical parts -------------------------------
    caps = d_rules.get("captions") or {}
    if caps.get("table") in ("above", "below"):
        notes.append(f"table captions must be {caps['table']} the table at the target: run layout_figures.py --table-captions {caps['table']}")
    if s_lay and d_lay:
        notes.append("figure/table widths are recomputed by layout_figures.py from the two venues' geometry")
    if s_tpl.get("venue_macros"):
        notes.append(f"source-venue macros to remove or map in the preamble: {s_tpl['venue_macros']} (draft_preamble.py does this)")
    return {"description": f"Generated by make_rules.py for {src.get('name')} -> {dst.get('name')} ({stage}) from venues.yaml",
            "ops": ops, "notes": notes}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src-venue", required=True)
    ap.add_argument("--dst-venue", required=True)
    ap.add_argument("--manifest", default=str(Path(__file__).resolve().parent.parent / "venues.yaml"))
    ap.add_argument("--stage", default="submission")
    ap.add_argument("--keep-page-breaks", action="store_true")
    ap.add_argument("--override", help="pair-specific JSON whose ops are appended")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    try:
        man = load_manifest(Path(args.manifest))
        src, dst = man["venues"][args.src_venue], man["venues"][args.dst_venue]
    except (OSError, KeyError, ValueError) as exc:
        print(f"make_rules: {exc}", file=sys.stderr)
        return 2
    rules = build(src, dst, args.stage, args.keep_page_breaks, args.dst_venue)
    if args.override:
        try:
            extra = json.loads(Path(args.override).read_text(encoding="utf-8"))
            rules["ops"].extend(extra.get("ops", []))
            rules["notes"].extend(extra.get("not_done_by_script", []))
            rules["override"] = args.override
        except (OSError, json.JSONDecodeError) as exc:
            print(f"make_rules: cannot read override: {exc}", file=sys.stderr)
            return 2
    Path(args.out).write_text(json.dumps(rules, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"make_rules  {args.src_venue} -> {args.dst_venue}: {len(rules['ops'])} ops -> {args.out}")
    for op in rules["ops"]:
        head = {k: v for k, v in op.items() if k not in ("why", "text")}
        print(f"  {json.dumps(head, ensure_ascii=False)[:110]}")
    for n in rules["notes"]:
        print(f"  note: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
