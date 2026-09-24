#!/usr/bin/env python3
"""draft_preamble.py -- draft the target preamble from the source preamble and venues.yaml.

    draft_preamble.py --src orig/main.tex --src-venue aaai2027 --dst-venue iclr2027 \
                      --stage submission --out preamble.tex

Keeps every line of the source preamble except the source venue's own lines (document class,
style line, mandatory preamble lines, venue macros), prepends the target's document class,
style line, packages and extras, keeps \\title verbatim, and rebuilds the author block in the
target's shape with the source text copied verbatim (venue macros such as \\corresponding are
mapped through `template.venue_macro_map`).  The result is a DRAFT: the agent reads it once,
adjusts separators in the author block if needed, and body_diff.py later verifies that the
title and every author/affiliation word survived.  Standard library only.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_template import load_manifest  # noqa: E402


def brace_group(text: str, open_idx: int) -> tuple[int, int] | None:
    depth, i = 0, open_idx
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return open_idx, i
        i += 1
    return None


def cut_macro_block(text: str, macro: str) -> tuple[str, str | None]:
    """Remove \\macro{...} (whole lines) from text; return (text, inner)."""
    m = re.search(r"^[ \t]*\\" + re.escape(macro) + r"\s*\{", text, re.M)
    if not m:
        return text, None
    g = brace_group(text, m.end() - 1)
    if not g:
        return text, None
    ls = text.rfind("\n", 0, m.start()) + 1
    le = text.find("\n", g[1])
    le = len(text) if le < 0 else le + 1
    return text[:ls] + text[le:], text[g[0] + 1:g[1]]


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s.split("%")[0])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True)
    ap.add_argument("--src-venue", required=True)
    ap.add_argument("--dst-venue", required=True)
    ap.add_argument("--stage", default="submission", choices=["submission", "rebuttal", "camera_ready"])
    ap.add_argument("--manifest", default=str(Path(__file__).resolve().parent.parent / "venues.yaml"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    try:
        man = load_manifest(Path(args.manifest))
        src_v, dst_v = man["venues"][args.src_venue], man["venues"][args.dst_venue]
        text = Path(args.src).read_text(encoding="utf-8")
        out = Path(args.out)
        if out.exists() and not args.force:
            print(f"draft_preamble: refusing to overwrite {out} (use --force)", file=sys.stderr)
            return 2
    except (OSError, KeyError, ValueError) as exc:
        print(f"draft_preamble: {exc}", file=sys.stderr)
        return 2
    m = re.search(r"\\begin\s*\{document\}", text)
    if not m:
        print("draft_preamble: no \\begin{document}", file=sys.stderr)
        return 2
    pre = text[:m.start()]
    s_tpl, d_tpl = src_v.get("template") or {}, dst_v.get("template") or {}
    log: list[str] = []

    # ---- pull out title / author / affiliations ---------------------------
    pre, title = cut_macro_block(pre, "title")
    pre, author = cut_macro_block(pre, "author")
    pre, affil = cut_macro_block(pre, "affiliations")
    if affil is None:
        pre, affil = cut_macro_block(pre, "affiliation")
    if title is None:
        print("draft_preamble: no \\title found", file=sys.stderr)
        return 2

    # ---- drop the source venue's own lines --------------------------------
    src_style = Path(s_tpl.get("files", {}) and next((f for f in s_tpl["files"] if f.endswith(".sty")), "")).stem
    drop_patterns = [r"\\documentclass\b"]
    if src_style:
        drop_patterns.append(r"\\usepackage(\[[^\]]*\])?\{[^}]*" + re.escape(src_style) + r"[^}]*\}")
    mandatory = [norm(str(x).split("(")[0]) for x in (s_tpl.get("mandatory_preamble") or [])]
    venue_macros = list(s_tpl.get("venue_macros") or [])
    natbib_by_dst = bool(d_tpl.get("natbib_loaded_by_style"))
    # packages the target provides itself (style file, header) -> a duplicate source line may go
    provided = set(d_tpl.get("style_loads") or []) | set(d_tpl.get("packages_after_style") or []) | {"graphicx"}
    if natbib_by_dst:
        provided.add("natbib")
    for extra in d_tpl.get("preamble_extras") or []:
        mm = re.search(r"\\usepackage(\[[^\]]*\])?\{([^}]*)\}", str(extra))
        if mm:
            provided |= {x.strip() for x in mm.group(2).split(",")}

    def is_generic_package_line(line: str) -> str | None:
        mm = re.match(r"\s*\\usepackage(\[[^\]]*\])?\{([^}]*)\}", line)
        return mm.group(2) if mm else None
    kept: list[str] = []
    lines = pre.split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        n = norm(ln)
        drop = any(re.search(p, ln) for p in drop_patterns)
        if not drop and n and any(n.startswith(mp) for mp in mandatory if mp):
            pk = is_generic_package_line(ln)
            if pk is None:
                drop = True                                   # venue command (\frenchspacing, \pdfinfo, \urlstyle ...)
            else:
                names = {x.strip() for x in pk.split(",")}
                if names <= provided:
                    drop = True                               # the target loads it anyway
                else:
                    log.append(f"kept (target does not provide it): {ln.strip()[:60]}")
        if not drop and natbib_by_dst and re.match(r"\s*\\usepackage\s*\{natbib\}", ln):
            drop, why = True, "target style loads natbib"
        if not drop and d_tpl.get("section_numbering") == "numbered" and re.match(r"\s*\\setcounter\s*\{secnumdepth\}", ln):
            drop = True
        if not drop and any(re.match(r"\s*\\" + re.escape(vm) + r"\b", ln) for vm in venue_macros):
            drop = True
        if drop:
            # multi-line macro (e.g. \pdfinfo{ ... })
            opens = ln.count("{") - ln.count("}")
            log.append(f"dropped: {ln.strip()[:70]}")
            while opens > 0 and i + 1 < len(lines):
                i += 1
                opens += lines[i].count("{") - lines[i].count("}")
            i += 1
            continue
        kept.append(ln)
        i += 1
    kept_text = "\n".join(kept).strip("\n")
    # the target may need xcolor before \definecolor when the source style loaded it implicitly
    if re.search(r"\\definecolor", kept_text) and not re.search(r"\\usepackage(\[[^\]]*\])?\{[^}]*xcolor", kept_text.split("\\definecolor")[0]):
        mm = re.search(r"^\s*(\\usepackage(\[[^\]]*\])?\{[^}]*xcolor[^}]*\}).*$", kept_text, re.M)
        if mm:
            line = mm.group(1)                                # keep every package on that line (e.g. xcolor, colortbl)
            kept_text = kept_text[:mm.start()] + kept_text[mm.end():].lstrip("\n")
            kept_text = line + " % paper-migrate: moved before the first \\definecolor\n" + kept_text
            log.append(f"moved before \\definecolor: {line}")
        elif "xcolor" not in provided:
            kept_text = "\\usepackage{xcolor} % paper-migrate: the source style loaded xcolor implicitly\n" + kept_text
            log.append("xcolor added before \\definecolor")

    # ---- author block --------------------------------------------------------
    def strip_inline_comments(t: str) -> str:
        out = []
        for line in t.split("\n"):
            i, cut = 0, len(line)
            while i < len(line):
                if line[i] == "\\":
                    i += 2
                    continue
                if line[i] == "%":
                    cut = i
                    break
                i += 1
            out.append(line[:cut].rstrip())
        return "\n".join(l for l in out if l.strip())

    macro_map = s_tpl.get("venue_macro_map") or {}
    auth = strip_inline_comments(author or "").strip()
    affil = strip_inline_comments(affil) if affil else affil
    for k, v in macro_map.items():
        auth = re.sub(r"\\" + re.escape(k) + r"\b", lambda _m: v, auth)
        if affil:
            affil = re.sub(r"\\" + re.escape(k) + r"\b", lambda _m: v, affil)
    for vm in venue_macros:
        if vm not in macro_map:
            auth = re.sub(r"\\" + re.escape(vm) + r"\b", "", auth)
    auth = re.sub(r"(?m)^\s*%.*\n?", "", auth).strip()
    affil_text = re.sub(r"(?m)^\s*%.*\n?", "", (affil or "")).strip()
    author_block = "\\author{" + auth
    if affil_text:
        author_block += " \\\\\n    " + affil_text
    author_block += "\n}"

    # ---- anonymisation line for the stage -----------------------------------
    anon = d_tpl.get("anonymization") or {}
    stage_line = ""
    st = anon.get(args.stage) or {}
    if st.get("line"):
        stage_line = st["line"]

    header = [f"% Preamble drafted by paper-migrate for {dst_v.get('name', args.dst_venue)} ({args.stage}); source: {src_v.get('name', args.src_venue)}.",
              "% Everything below the marker is copied from the source preamble minus the source venue's own lines.",
              str(d_tpl.get("documentclass") or "\\documentclass{article}"),
              str(d_tpl.get("style_line") or "% TODO: style line")]
    for pk in d_tpl.get("packages_after_style") or []:
        header.append(f"\\usepackage{{{pk}}}")
    for extra in d_tpl.get("preamble_extras") or []:
        header.append(str(extra))
    if not re.search(r"\\usepackage(\[[^\]]*\])?\{[^}]*graphicx", kept_text + "\n".join(header)):
        header.append("\\usepackage{graphicx}")
    body = "\n".join(header) + "\n\n% ---- carried over from the source preamble (paper-migrate) ----\n" + kept_text + \
        "\n\n\\title{" + title + "}\n\n" + \
        f"% Author block rebuilt in the {dst_v.get('name', args.dst_venue)} shape; all names/affiliations/e-mails copied verbatim.\n" + \
        (f"% Target shape: {d_tpl['author_block']}\n" if d_tpl.get("author_block") else "") + \
        author_block + "\n" + (("\n" + stage_line + "\n") if stage_line else "")
    out.write_text(body, encoding="utf-8")
    print(f"draft_preamble  {args.src_venue} -> {args.dst_venue} ({args.stage}) -> {out}")
    for l in log:
        print("  " + l)
    print(f"  kept {len(kept_text.splitlines())} source preamble lines; author block {'with' if affil_text else 'without'} separate affiliations")
    print("  NEXT: read the draft once (author-block separators), then migrate_tex.py --preamble it; body_diff.py checks title/author text")
    return 0


if __name__ == "__main__":
    sys.exit(main())
