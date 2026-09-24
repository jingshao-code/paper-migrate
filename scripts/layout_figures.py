#!/usr/bin/env python3
"""layout_figures.py -- re-derive figure sizes after a column-model change.

A two-column source states widths as fractions of \\columnwidth, \\linewidth or
\\textwidth.  After a mechanical move to a single-column template those
fractions refer to a different physical width, so figures silently grow or
shrink (a 0.9\\linewidth column figure becomes 0.9 of the whole text width).

This script restores every graphic's PHYSICAL size from the source layout and
re-expresses it as a fraction of the target line width:

  * standalone \\includegraphics  -> width=<frac>\\linewidth (capped at --max-frac)
  * graphics inside subfigure / minipage -> the container width is rewritten,
    the inner width becomes relative to the container
  * --min-frac F      enlarge standalone figures narrower than F (off by default)
  * --pair a,b[,c]    put the floats labelled a and b side by side in ONE float.
                      Default --pair-mode subfloats: sub-boxes (subfigure/subtable) with
                      empty sub-captions that print (a), (b), the original labels moved
                      onto the sub-boxes, and one main caption made of the original
                      caption texts verbatim, each after its \\subref marker; \\ref{a}
                      then renders "3a", so no sentence in the text changes.
                      --pair-mode minipage keeps two independent captions/numbers.
                      Two labels inside the same float (a table block holding two
                      tabulars) are split into sub-boxes the same way.
  * --placement t     normalise figure placement specifiers, e.g. [h] -> [t]
  * --wrap LABEL[@SIDE][@FRAC]
                      turn a small standalone figure or table into a text-wrapped
                      wrapfigure/wraptable anchored at the paragraph that first references
                      it (or at --wrap-anchor LABEL@SNIPPET).  Figure width comes from the
                      size pass; table width is estimated from the column spec, cell text
                      and font size (override with @FRAC).  The script estimates the lines
                      the box needs and REFUSES when the anchor paragraph(s) are too short
                      or the table is wider than --wrap-max-table.  \\usepackage{wrapfig}
                      is added if missing.
  * --list-tables     print every table with its estimated natural width, so wrap
                      candidates can be chosen (nothing is written)
  * --fit-table LABEL wrap that table's outermost tabular in \\resizebox{\\linewidth}{!}{...}
                      (for tables that overflow the text width after compiling)
  * --verbatim-size SIZE
                      put every verbatim block inside a float into {\\SIZE ... } (e.g. scriptsize):
                      verbatim never re-wraps, so lines hand-wrapped for a wider source column
                      overflow a narrower target; a smaller font is the only layout-level fix
  * --table-captions above|below
                      move each table's \\caption (+ following \\label) above or below the
                      tabular material; ICLR: "The table number and title always appear
                      before the table"

Only widths, container widths, float grouping and placement change.  Captions,
labels, graphic files and their order are untouched -- run body_diff.py after.

    layout_figures.py --src orig/main.tex --in new/main.tex --out new/main.tex --force \\
                      --src-venue aaai2027 --dst-venue iclr2027 \\
                      --pair fig:a,fig:b --report layout.json

Venue geometry comes from venues.yaml (`layout.text_width_in`, `layout.column_width_in`,
`layout.columns`) or from --src-text-width/--src-column-width/--dst-text-width (inches).
Standard library only.  Exit 0 ok, 1 nothing to do / refused pairing, 2 error.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_template import load_manifest  # noqa: E402

FLOATS_WIDE = {"figure*", "table*", "algorithm*"}
FLOATS = {"figure", "table", "algorithm", "listing", "wrapfigure", "wraptable",
          "sidewaysfigure", "sidewaystable"} | FLOATS_WIDE
CONTAINERS = {"subfigure": 1, "subtable": 1, "minipage": 1, "wrapfigure": 2, "wraptable": 2}
UNIT_IN = {"in": 1.0, "cm": 1 / 2.54, "mm": 1 / 25.4, "pt": 1 / 72.27, "bp": 1 / 72.0, "pc": 12 / 72.27}

BEGIN_RE = re.compile(r"\\begin\s*\{([A-Za-z*]+)\}")
END_RE = re.compile(r"\\end\s*\{([A-Za-z*]+)\}")
GRAPHIC_RE = re.compile(r"\\includegraphics\s*(\[[^\]]*\])?\s*\{([^}]*)\}")


def strip_comments_keep_len(text: str) -> str:
    """Blank out % comments (keeping offsets) so regexes ignore them."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "%":
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        i += 1
    return "".join(out)


def brace_group(text: str, open_idx: int) -> tuple[int, int] | None:
    """(start, end) indices of the {...} group starting at open_idx."""
    if open_idx >= len(text) or text[open_idx] != "{":
        return None
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


def skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t\n":
        i += 1
    return i


def env_args(text: str, after: int, nopt: int, nargs: int) -> tuple[list[str], list[tuple[int, int]], int]:
    """Read optional [..] then nargs {..} groups after a \\begin{env}."""
    i = skip_ws(text, after)
    if i < len(text) and text[i] == "[":
        j = text.find("]", i)
        i = skip_ws(text, j + 1) if j > 0 else i
    vals, spans = [], []
    for _ in range(nargs):
        g = brace_group(text, i)
        if not g:
            break
        vals.append(text[g[0] + 1:g[1]])
        spans.append(g)
        i = skip_ws(text, g[1] + 1)
    return vals, spans, i


@dataclass
class Geometry:
    text_width_in: float
    column_width_in: float
    columns: int
    text_height_in: float = 9.0


@dataclass
class Graphic:
    idx: int
    file: str
    span: tuple[int, int]           # span of the whole \includegraphics command
    opts: str
    width_expr: str | None
    float_env: str | None
    float_span: tuple[int, int] | None
    container: str | None
    container_width_expr: str | None
    container_width_span: tuple[int, int] | None
    phys_in: float | None = None
    container_phys_in: float | None = None
    note: str = ""


def parse_width(opts: str) -> str | None:
    inner = (opts or "").strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    m = re.search(r"(?:^|,)\s*width\s*=\s*([^,\]]+)", inner)
    return m.group(1).strip() if m else None


def eval_length(expr: str, ctx_in: float, geo: Geometry, wide: bool) -> float | None:
    """Evaluate `0.9\\linewidth`, `\\textwidth`, `3in` ... in inches."""
    if expr is None:
        return None
    e = expr.replace(" ", "")
    m = re.fullmatch(r"([0-9]*\.?[0-9]*)\\(columnwidth|linewidth|textwidth|hsize|paperwidth)", e)
    if m:
        f = float(m.group(1)) if m.group(1) not in ("", ".") else 1.0
        name = m.group(2)
        if name == "textwidth":
            return f * geo.text_width_in
        if name == "columnwidth":
            return f * (geo.column_width_in if geo.columns > 1 else geo.text_width_in)
        if name == "paperwidth":
            return f * 8.5
        return f * ctx_in                       # \linewidth / \hsize: the enclosing box
    m = re.fullmatch(r"([0-9]*\.?[0-9]+)(in|cm|mm|pt|bp|pc)", e)
    if m:
        return float(m.group(1)) * UNIT_IN[m.group(2)]
    return None


def scan(text: str, geo: Geometry) -> list[Graphic]:
    """Find every \\includegraphics with its float/container context and physical width."""
    clean = strip_comments_keep_len(text)
    events = []
    for m in BEGIN_RE.finditer(clean):
        events.append((m.start(), "begin", m.group(1), m.end()))
    for m in END_RE.finditer(clean):
        events.append((m.start(), "end", m.group(1), m.end()))
    events.sort()
    graphics: list[Graphic] = []
    stack: list[dict] = []
    gi = iter(sorted((m.start(), m) for m in GRAPHIC_RE.finditer(clean)))
    pending = next(gi, None)

    def context():
        flt = next((s for s in reversed(stack) if s["env"] in FLOATS), None)
        cont = next((s for s in reversed(stack) if s["env"] in CONTAINERS and s is not flt), None)
        return flt, cont

    def width_of_float(flt) -> float:
        if flt is None:
            return geo.text_width_in
        if flt["env"] in ("wrapfigure", "wraptable") and flt.get("width_in"):
            return flt["width_in"]                      # the wrap box, whatever the column model
        if flt["env"] in FLOATS_WIDE or geo.columns == 1:
            return geo.text_width_in
        return geo.column_width_in

    def flush_graphics_before(pos: int):
        nonlocal pending
        while pending and pending[0] < pos:
            _, m = pending
            flt, cont = context()
            base_in = width_of_float(flt)
            cont_in = cont["width_in"] if cont and cont.get("width_in") else None
            ctx_in = cont_in if cont_in else base_in
            wexpr = parse_width(m.group(1) or "")
            phys = eval_length(wexpr, ctx_in, geo, bool(flt and flt["env"] in FLOATS_WIDE)) if wexpr else None
            g = Graphic(idx=len(graphics), file=m.group(2).strip(), span=(m.start(), m.end()),
                        opts=(m.group(1) or ""), width_expr=wexpr,
                        float_env=flt["env"] if flt else None,
                        float_span=(flt["start"], None) if flt else None,
                        container=cont["env"] if cont else None,
                        container_width_expr=cont.get("width_expr") if cont else None,
                        container_width_span=cont.get("width_span") if cont else None,
                        phys_in=phys, container_phys_in=cont_in)
            if wexpr is None:
                g.note = "no width= option (height/scale/natural size); left unchanged"
            elif phys is None:
                g.note = f"width expression {wexpr!r} not understood; left unchanged"
            graphics.append(g)
            pending = next(gi, None)

    for pos, kind, env, end in events:
        flush_graphics_before(pos)
        if kind == "begin":
            frame = {"env": env, "start": pos}
            if env in CONTAINERS:
                nargs = CONTAINERS[env]
                vals, spans, _ = env_args(clean, end, 1, nargs)
                if vals:
                    wexpr = vals[-1]
                    flt, cont = context()
                    outer_in = cont["width_in"] if cont and cont.get("width_in") else width_of_float(flt)
                    frame["width_expr"] = wexpr
                    frame["width_span"] = spans[-1]
                    frame["width_in"] = eval_length(wexpr, outer_in, geo, False)
            stack.append(frame)
        else:
            for k in range(len(stack) - 1, -1, -1):
                if stack[k]["env"] == env:
                    stack[k]["end"] = end
                    del stack[k:]
                    break
    flush_graphics_before(len(clean) + 1)
    # fill float spans (start,end) by re-scanning for the enclosing float block ends
    for g in graphics:
        if g.float_span:
            start = g.float_span[0]
            m = re.compile(r"\\end\s*\{" + re.escape(g.float_env) + r"\}").search(clean, g.span[1])
            g.float_span = (start, m.end() if m else None)
    return graphics


def fmt_frac(x: float) -> str:
    s = f"{x:.2f}".rstrip("0").rstrip(".")
    return s if s else "0"


def replace_width_opt(opts: str, new_expr: str) -> str:
    inner = opts[1:-1] if opts.startswith("[") else opts
    parts = [p.strip() for p in inner.split(",") if p.strip()]
    parts = [p for p in parts if not re.match(r"width\s*=", p)]
    parts.insert(0, f"width={new_expr}")
    return "[" + ",".join(parts) + "]"


PREFIX_LINE_RE = re.compile(r"^\s*\\(centering|small|footnotesize|scriptsize|normalsize|setlength\s*\{\\(tabcolsep|extrarowheight)\}|renewcommand\s*\{\\arraystretch\})")


def _caption_groups(cblock: str) -> list[tuple[int, int, str, str | None]]:
    """(line_start, line_end, caption_text, label) for every \caption in a float block."""
    out = []
    for cm in re.finditer(r"\\caption\s*(\[[^\]]*\])?\s*\{", cblock):
        g = brace_group(cblock, cm.end() - 1)
        if not g:
            continue
        cap_text = cblock[g[0] + 1:g[1]]
        end = g[1] + 1
        lab = None
        tail = re.match(r"\s*\\label\s*\{([^}]*)\}", cblock[end:])
        if tail:
            lab = tail.group(1).strip()
            end += tail.end()
        ls = cblock.rfind("\n", 0, cm.start()) + 1
        le = cblock.find("\n", end)
        le = len(cblock) if le < 0 else le + 1
        out.append((ls, le, cap_text.strip(), lab))
    return out


def _float_items(text: str, label: str, dst_text_in: float, table_max_frac: float = 0.6, geo: "Geometry | None" = None) -> tuple[tuple[int, int, str] | None, list[dict]]:
    """Split the float that holds `label` into items {label, caption, material, frac, prefix}."""
    b = figure_block_with_label(text, label, envs="figure|table")
    if not b:
        return None, []
    bs, be, env = b
    base = env.rstrip("*")
    block = text[bs:be]
    cblock = strip_comments_keep_len(block)
    m1 = re.match(r"\\begin\s*\{" + re.escape(env) + r"\}\s*(\[[^\]]*\])?", block)
    m2 = re.search(r"\\end\s*\{" + re.escape(env) + r"\}\s*$", block)
    caps = _caption_groups(cblock)
    items: list[dict] = []
    if base == "figure":
        inner = block[m1.end():m2.start()]
        cinner = cblock[m1.end():m2.start()]
        # material = inner minus caption/label lines
        cut = [(ls - m1.end(), le - m1.end()) for ls, le, _, _ in caps]
        mat = inner
        for a, z in sorted(cut, reverse=True):
            mat = mat[:a] + mat[z:]
        g = GRAPHIC_RE.search(cinner)
        we = parse_width(g.group(1) or "") if g else None
        mm = re.fullmatch(r"([0-9.]+)?\\(linewidth|columnwidth|textwidth)", (we or "").replace(" ", ""))
        frac = float(mm.group(1)) if mm and mm.group(1) else 1.0
        if mm and mm.group(2) == "columnwidth" and geo is not None and geo.columns == 2:
            frac = frac * geo.column_width_in / dst_text_in          # express as a fraction of the text width
        mat = GRAPHIC_RE.sub(_relative_graphic, mat, count=1)
        cap = next((c for c in caps if c[3] == label), caps[0] if caps else (0, 0, "", None))
        items.append({"env": "figure", "label": label, "caption": cap[2], "material": mat.strip("\n"), "frac": frac, "prefix": ""})
    else:
        tabs = _toplevel_tabulars(cblock, 0, len(cblock))
        prefix_lines = [ln for ln in cblock[m1.end():tabs[0][0] if tabs else m2.start()].split("\n") if PREFIX_LINE_RE.match(ln)] if tabs else []
        prefix = "\n".join(ln.strip() for ln in prefix_lines if ln.strip() != "\\centering")
        used = set()
        for ts, te in tabs:
            # the nearest caption group (before or after this tabular) not yet used
            best, bd = None, None
            for idx, (ls, le, ctext, lab) in enumerate(caps):
                if idx in used:
                    continue
                d = min(abs(ls - te), abs(ts - le))
                if bd is None or d < bd:
                    best, bd = idx, d
            if best is not None:
                used.add(best)
            ls_, le_, ctext, lab = caps[best] if best is not None else (0, 0, "", None)
            start = _subtable_start(cblock, te, 0) or ts
            # include a resizebox/center wrapper end
            end = te
            extra = re.match(r"\s*\}?\s*(\\end\s*\{(center|adjustbox)\})?", cblock[end:])
            if extra and extra.group(0).strip():
                end += extra.end()
            material = block[start:end].strip("\n")
            est = estimate_table(prefix + "\n" + material, dst_text_in)
            frac = min(table_max_frac, max(0.25, (est["width_pt"] or 0.4 * dst_text_in * 72.27) * 1.14 / (dst_text_in * 72.27)))
            items.append({"env": "table", "label": lab or label, "caption": ctext, "material": material, "frac": frac, "prefix": prefix})
    return (bs, be, base), items


def pair_floats(text: str, labels: list[str], dst_text_in: float, gap: float, placement: str | None,
                table_captions: str | None, mode: str = "subfloats", geo: "Geometry | None" = None) -> tuple[str, dict]:
    """Combine the floats (or the sub-tables of one float) named by `labels` into one float."""
    info: dict = {"labels": labels, "mode": mode, "status": "refused"}
    blocks: dict[tuple[int, int, str], list[dict]] = {}
    order: list[tuple[int, int, str]] = []
    for lab in labels:
        b, items = _float_items(text, lab, dst_text_in, geo=geo)
        if not b:
            info["reason"] = f"no figure/table block directly contains \\label{{{lab}}}"
            return text, info
        if b not in blocks:
            blocks[b] = items
            order.append(b)
    order.sort(key=lambda b: b[0])
    kinds = {b[2] for b in order}
    if len(kinds) != 1:
        info["reason"] = "cannot combine a figure with a table"
        return text, info
    # Floats may only be combined when they are discussed together: no section/subsection
    # heading between them and at most a few paragraphs apart.  Otherwise a float would be
    # moved away from the text that refers to it, which is the authors' decision, not ours.
    for (bs1, be1, _), (bs2, be2, _) in zip(order, order[1:]):
        between = strip_comments_keep_len(text[be1:bs2])
        if re.search(r"\\(section|subsection|subsubsection)\b", between):
            info["reason"] = "a section heading lies between the floats; pairing would move one away from its discussion"
            return text, info
        paras = [r for r in re.split(r"\n\s*\n", between) if r.strip() and not FLOAT_RUN_RE.match(r.strip())]
        if len(paras) > 3:
            info["reason"] = f"the floats are {len(paras)} paragraphs apart; pairing would move one away from its discussion (limit 3)"
            return text, info
    base = kinds.pop()
    items: list[dict] = []
    for b in order:
        its = blocks[b]
        if len(order) == 1:
            its = [it for it in its if it["label"] in labels] or its       # same-block split
        elif len(its) > 1:
            info["reason"] = f"a block already holds {len(its)} captions; pair it alone (same-block split) instead"
            return text, info
        items.extend(its)
    if len(items) < 2:
        info["reason"] = "fewer than two items found"
        return text, info
    fracs = [it["frac"] for it in items]                       # fractions of the text width
    float_env = base
    if geo is not None and geo.columns == 2:
        total_in = sum(f * dst_text_in for f in fracs)
        if total_in <= geo.column_width_in * 1.02:
            fracs = [f * dst_text_in / geo.column_width_in for f in fracs]   # relative to the column
        else:
            float_env = base + "*"                                            # spans both columns
    scale = min(1.0, (1.0 - gap * (len(items) - 1)) / sum(fracs))
    fracs = [f * scale for f in fracs]
    plac = f"[{placement}]" if placement else "[t]"
    sub_env = "subfigure" if base == "figure" else "subtable"
    boxes = []
    for it, fr in zip(items, fracs):
        empty_cap = "\\caption{}\n\\label{" + it["label"] + "}"
        material = it["material"]
        if base == "table" and "\\resizebox" not in material and "adjustbox" not in material:
            # never overflow the sub-box: shrink only when the estimate was too small
            material = "\\begin{adjustbox}{max width=\\linewidth}\n" + material + "\n\\end{adjustbox}"
        body = (it["prefix"] + "\n" if it["prefix"] else "") + material
        if base == "table" and (table_captions or "above") == "above":
            inner = empty_cap + "\n" + body
        else:
            inner = body + "\n" + empty_cap
        boxes.append(f"\\begin{{{sub_env}}}[t]{{{fmt_frac(fr)}\\linewidth}}\n\\centering\n{inner}\n\\end{{{sub_env}}}")
    main_cap = "\\caption{" + " ".join(f"\\subref{{{it['label']}}} {it['caption']}" for it in items) + "}"
    head = (f"% paper-migrate layout: {len(items)} {base}s combined into one float with sub-captions; the original caption texts are kept verbatim after their \\subref markers, "
            f"and \\ref{{<label>}} now renders as e.g. 3a\n\\begin{{{float_env}}}{plac}\n\\centering\n")
    if base == "table" and (table_captions or "above") == "above":
        merged = head + main_cap + "\n" + "\\hfill\n".join(boxes) + f"\n\\end{{{float_env}}}"
    else:
        merged = head + "\\hfill\n".join(boxes) + "\n" + main_cap + f"\n\\end{{{float_env}}}"
    if mode == "minipage":
        boxes = []
        for it, fr in zip(items, fracs):
            body = (it["prefix"] + "\n" if it["prefix"] else "") + it["material"]
            cap = "\\caption{" + it["caption"] + "}\n\\label{" + it["label"] + "}"
            inner = (cap + "\n" + body) if (base == "table" and (table_captions or "above") == "above") else (body + "\n" + cap)
            boxes.append(f"\\begin{{minipage}}[t]{{{fmt_frac(fr)}\\linewidth}}\n\\centering\n{inner}\n\\end{{minipage}}")
        merged = (f"% paper-migrate layout: floats placed side by side; each keeps its own caption and label\n"
                  f"\\begin{{{float_env}}}{plac}\n\\centering\n" + "\\hfill\n".join(boxes) + f"\n\\end{{{float_env}}}")
    # remove later blocks (from the end), then replace the first block with the merged one
    for bs, be, _ in sorted(order[1:], key=lambda b: -b[0]):
        tail = text[be:]
        cut = min(1, len(tail) - len(tail.lstrip("\n")))
        text = text[:bs].rstrip(" ") + text[be + cut:]
    first = order[0]
    b = figure_block_with_label(text, items[0]["label"], envs="figure|table")
    text = text[:b[0]] + merged + text[b[1]:]
    info.update({"status": "paired", "env": float_env, "fracs": [round(f, 3) for f in fracs], "scaled_by": round(scale, 3),
                 "items": [it["label"] for it in items], "same_block": len(order) == 1})
    return text, info


def _relative_graphic(g: re.Match) -> str:
    """Rewrite one \\includegraphics so its width is the enclosing minipage width."""
    return "\\includegraphics" + replace_width_opt(g.group(1) or "", "\\linewidth") + "{" + g.group(2) + "}"


def figure_block_with_label(text: str, label: str, envs: str = "figure") -> tuple[int, int, str] | None:
    """Span of the top-level \\begin{figure|table}..\\end{...} directly containing \\label{label}."""
    clean = strip_comments_keep_len(text)
    pat = r"\\begin\s*\{(figure\*?)\}" if envs == "figure" else r"\\begin\s*\{(figure\*?|table\*?)\}"
    for m in re.finditer(pat, clean):
        env = m.group(1)
        e = re.compile(r"\\end\s*\{" + re.escape(env) + r"\}").search(clean, m.end())
        if not e:
            continue
        block = clean[m.start():e.end()]
        # label must not be inside a nested subfigure/minipage
        depth_text = re.sub(r"\\begin\{(subfigure|minipage|subtable)\}.*?\\end\{\1\}", "", block, flags=re.S)
        if re.search(r"\\label\s*\{\s*" + re.escape(label) + r"\s*\}", depth_text):
            return m.start(), e.end(), env
    return None


# --------------------------------------------------------------------------- #
# Text wrapping (wrapfigure / wraptable)                                      #
# --------------------------------------------------------------------------- #

WRAP_ENV = {"figure": "wrapfigure", "table": "wraptable"}
CHARS_PER_INCH = 17.0        # 10 pt Times body text, approximate
CHARS_PER_WORD = 6.0         # including the space
LINE_PT = 12.0               # baseline skip of the 10 pt ICLR / NeurIPS body
GRAPHIC_EXTS = (".pdf", ".png", ".jpg", ".jpeg", ".eps")
STOP_RUN_RE = re.compile(r"^\s*(\\(section|subsection|subsubsection|paragraph|appendix|bibliography|item|begin\{(itemize|enumerate|description|equation|align|gather|algorithm|proof|theorem|lemma|proposition|corollary|tcolorbox)|\[)|\$\$)")
FLOAT_RUN_RE = re.compile(r"^\s*\\begin\{(figure|table|wrapfigure|wraptable|algorithm|listing)\*?\}")
# lines skipped at the top of an anchor paragraph: headings and labels.  \\noindent is NOT skipped:
# the wrap environment must come before it, otherwise it lands inside the started paragraph.
LEAD_LINE_RE = re.compile(r"^\s*\\(section|subsection|subsubsection|label|vspace|paragraph\{[^}]*\}\s*$)")


def graphic_size(path: Path) -> tuple[float, float] | None:
    """(width, height) of a PDF (pt) or PNG/JPEG (px); only the ratio is used."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if data[:4] == b"%PDF":
        m = re.search(rb"/MediaBox\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*\]", data)
        if m:
            x0, y0, x1, y1 = (float(v) for v in m.groups())
            return abs(x1 - x0), abs(y1 - y0)
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return float(w), float(h)
    if data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return float(w), float(h)
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


def resolve_graphic(project: Path, rel: str) -> Path | None:
    rel = rel.strip().strip('"')
    if rel.startswith("/") or ".." in rel.split("/"):
        return None
    for cand in [rel] + [rel + e for e in GRAPHIC_EXTS]:
        p = project / cand
        if p.is_file():
            return p
    hits = [p for p in project.rglob(Path(rel).name + "*") if p.is_file() and p.suffix.lower() in GRAPHIC_EXTS]
    return hits[0] if len(hits) == 1 else None


def word_count(tex: str) -> int:
    t = re.sub(r"\\[A-Za-z@]+\*?(\[[^\]]*\])?", " ", tex)
    t = re.sub(r"\$[^$]*\$", " x ", t)
    t = re.sub(r"[{}~]", " ", t)
    return len([w for w in t.split() if re.search(r"[A-Za-z0-9]", w)])


def text_runs(text: str) -> list[tuple[int, int]]:
    """Spans of non-blank line runs (paragraph candidates) in comment-stripped text."""
    runs, start = [], None
    pos = 0
    for line in text.split("\n"):
        blank = not line.strip()
        if blank and start is not None:
            runs.append((start, pos))
            start = None
        elif not blank and start is None:
            start = pos
        pos += len(line) + 1
    if start is not None:
        runs.append((start, len(text)))
    return runs


def float_spans(clean: str) -> list[tuple[int, int]]:
    spans = []
    for m in re.finditer(r"\\begin\s*\{(figure\*?|table\*?|wrapfigure|wraptable|algorithm\*?)\}", clean):
        e = re.compile(r"\\end\s*\{" + re.escape(m.group(1)) + r"\}").search(clean, m.end())
        if e:
            spans.append((m.start(), e.end()))
    return spans


def find_anchor(text: str, label: str, snippet: str | None) -> tuple[int, str] | None:
    """Start offset of the paragraph text that should wrap around the float."""
    clean = strip_comments_keep_len(text)
    floats = float_spans(clean)
    doc = clean.find("\\begin{document}")
    def outside_floats(i: int) -> bool:
        return i > doc and not any(a <= i < b for a, b in floats)
    pos = None
    if snippet:
        for m in re.finditer(re.escape(snippet), clean):
            if outside_floats(m.start()):
                pos = m.start()
                break
        if pos is None:
            return None
    else:
        for m in re.finditer(r"\\(?:auto|c|C)?ref\s*\{\s*" + re.escape(label) + r"\s*\}", clean):
            if outside_floats(m.start()):
                pos = m.start()
                break
        if pos is None:
            return None
    run = next(((a, b) for a, b in text_runs(clean) if a <= pos < b), None)
    if run is None:
        return None
    a, b = run
    # skip heading / label-only lines at the top of the run
    off = a
    for line in clean[a:b].split("\n"):
        if LEAD_LINE_RE.match(line) or not line.strip():
            off += len(line) + 1
            continue
        break
    if off >= b:
        return None
    # the anchor must be at a line start in the original text
    return off, clean[off:off + 60].replace("\n", " ")


def capacity_lines(text: str, anchor: int, frac: float, dst_text_in: float) -> tuple[float, int]:
    """Lines of text available for wrapping from the anchor onwards (stops at headings/lists)."""
    clean = strip_comments_keep_len(text)
    runs = text_runs(clean)
    words = 0
    first = True
    for a, b in runs:
        if b <= anchor:
            continue
        seg = clean[max(a, anchor):b]
        if first:
            first = False
        else:
            if FLOAT_RUN_RE.match(seg):
                continue                       # floats do not consume lines
            if STOP_RUN_RE.match(seg):
                break
        words += word_count(seg)
    words_per_line = max(1.0, (1.0 - frac) * dst_text_in * CHARS_PER_INCH / CHARS_PER_WORD)
    return words / words_per_line, words


def caption_chars(block: str) -> int:
    m = re.search(r"\\caption\s*(\[[^\]]*\])?\s*\{", block)
    if not m:
        return 0
    g = brace_group(block, m.end() - 1)
    return len(re.sub(r"\\[A-Za-z@]+\*?|[{}$]", "", block[g[0] + 1:g[1]])) if g else 0


FONT_PT = {"tiny": 5.0, "scriptsize": 7.0, "footnotesize": 8.0, "small": 9.0, "normalsize": 10.0,
           "large": 12.0, "Large": 14.4}
CHAR_EM = 0.5                 # average advance of Times digits / mixed text, in em


def _colspec_count(spec: str) -> int:
    s = spec
    for _ in range(5):
        m = re.search(r"\*\s*\{\s*(\d+)\s*\}\s*\{([^{}]*)\}", s)
        if not m:
            break
        s = s[:m.start()] + m.group(2) * int(m.group(1)) + s[m.end():]
    s = re.sub(r"[@!><]\s*\{[^{}]*(\{[^{}]*\}[^{}]*)*\}", "", s)
    s = re.sub(r"[pmbLCRXYSNwW]\s*\{[^{}]*(\{[^{}]*\}[^{}]*)*\}", "C", s)
    return len(re.findall(r"[A-Za-z]", re.sub(r"[|\s]", "", s)))


def _fixed_col_widths_pt(spec: str, dst_text_in: float) -> float:
    total = 0.0
    for m in re.finditer(r"[pmbL]\s*\{\s*([0-9.]*)\\?(textwidth|linewidth|columnwidth)?\s*(in|cm|mm|pt|em)?\s*\}", spec):
        num = float(m.group(1)) if m.group(1) else 1.0
        if m.group(2):
            total += num * dst_text_in * 72.27
        elif m.group(3):
            total += num * UNIT_IN.get(m.group(3), 1 / 72.27) * 72.27 if m.group(3) != "em" else num * 10
    return total


def _cell_chars(cell: str) -> int:
    c = re.sub(r"\\multicolumn\s*\{\d+\}\s*\{[^}]*\}", "", cell)
    c = re.sub(r"\\multirow\s*\{[^}]*\}\s*\{[^}]*\}", "", c)
    c = re.sub(r"\\(cmidrule|cline)\s*(\([^)]*\))?\s*\{[^}]*\}", "", c)
    c = re.sub(r"\\[A-Za-z@]+\*?", "", c)
    c = re.sub(r"[{}$^_~\\]", "", c)
    return len(c.strip())


def estimate_table(block_clean: str, dst_text_in: float) -> dict:
    """Rough natural width of the tabular material in a table float, in points."""
    info = {"font_pt": 10.0, "rows": 0, "cols": 0, "width_pt": None, "full_width": False}
    m = re.search(r"\\(tiny|scriptsize|footnotesize|small|normalsize|large|Large)\b", block_clean)
    font = FONT_PT[m.group(1)] if m else 10.0
    info["font_pt"] = font
    tcs = 6.0
    m = re.search(r"\\setlength\s*\{\\tabcolsep\}\s*\{([0-9.]+)\s*(pt|em)\}", block_clean)
    if m:
        tcs = float(m.group(1)) * (font if m.group(2) == "em" else 1.0)
    if re.search(r"\\resizebox\s*\{\\(linewidth|textwidth|columnwidth)\}", block_clean) or "tabularx" in block_clean:
        info["full_width"] = True          # scaled/stretched to the line; content width still estimated below
    t = re.search(r"\\begin\s*\{(tabular\*?|tabularx|tabulary)\}\s*(\{[^{}]*\}\s*)?(\[[^\]]*\])?\s*\{((?:[^{}]|\{[^{}]*\})*)\}(.*?)\\end\s*\{\1\}", block_clean, re.S)
    if not t:
        return info
    spec, body = t.group(4), t.group(5)
    ncols = max(1, _colspec_count(spec))
    maxch = [0] * ncols
    rows = 0
    for raw in re.split(r"\\\\(?:\[[^\]]*\])?", body):
        row = re.sub(r"\\(toprule|midrule|bottomrule|hline|addlinespace)(\[[^\]]*\])?", "", raw)
        row = re.sub(r"\\cmidrule\s*(\([^)]*\))?\s*\{[^}]*\}", "", row).strip()
        if not row:
            continue
        rows += 1
        cells, depth, cur = [], 0, ""
        for ch in row:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            if ch == "&" and depth == 0:
                cells.append(cur)
                cur = ""
            else:
                cur += ch
        cells.append(cur)
        col = 0
        for cell in cells:
            span = re.match(r"\s*\\multicolumn\s*\{(\d+)\}", cell)
            n = int(span.group(1)) if span else 1
            if n == 1 and col < ncols:
                maxch[col] = max(maxch[col], _cell_chars(cell))
            col += n
    fixed = _fixed_col_widths_pt(spec, dst_text_in)
    text_pt = sum(maxch) * CHAR_EM * font
    info.update({"rows": rows, "cols": ncols,
                 "width_pt": (fixed if fixed else text_pt) + 2 * tcs * ncols + 1.0})
    return info


def list_tables(text: str, dst_text_in: float) -> list[dict]:
    clean = strip_comments_keep_len(text)
    out = []
    for m in re.finditer(r"\\begin\s*\{(table\*?|wraptable)\}", clean):
        e = re.compile(r"\\end\s*\{" + re.escape(m.group(1)) + r"\}").search(clean, m.end())
        if not e:
            continue
        block = clean[m.start():e.end()]
        lab = re.search(r"\\label\s*\{([^}]*)\}", block)
        est = estimate_table(block, dst_text_in)
        c = re.search(r"\\caption\s*(\[|\{)", block)
        tb = re.search(r"\\begin\s*\{(tabular\*?|tabularx|tabulary|longtable)\}", block)
        cap_pos = "above" if (c and tb and c.start() < tb.start()) else "below"
        frac = None if est["width_pt"] is None else est["width_pt"] / (dst_text_in * 72.27)
        out.append({"label": lab.group(1) if lab else "?", "env": m.group(1), "cols": est["cols"], "rows": est["rows"],
                    "font_pt": est["font_pt"], "est_width_in": None if est["width_pt"] is None else round(est["width_pt"] / 72.27, 2),
                    "est_frac": None if frac is None else round(frac, 2), "full_width": est["full_width"],
                    "caption": cap_pos})
    return out


TABULAR_BEGIN_RE = re.compile(r"\\begin\s*\{(tabular\*?|tabularx|tabulary|longtable)\}")
TABULAR_END_RE = re.compile(r"\\end\s*\{(tabular\*?|tabularx|tabulary|longtable)\}")


def _toplevel_tabulars(cblock: str, lo: int, hi: int) -> list[tuple[int, int]]:
    """(begin, end) spans of OUTERMOST tabulars in cblock[lo:hi]; nested tabulars in cells are skipped."""
    events = [(m.start(), 1, m) for m in TABULAR_BEGIN_RE.finditer(cblock, lo, hi)]
    events += [(m.start(), -1, m) for m in TABULAR_END_RE.finditer(cblock, lo, hi)]
    events.sort(key=lambda e: e[0])
    spans, depth, start = [], 0, None
    for pos, d, m in events:
        if d == 1:
            if depth == 0:
                start = pos
            depth += 1
        else:
            depth -= 1
            if depth == 0 and start is not None:
                spans.append((start, m.end()))
                start = None
    if start is not None:
        spans.append((start, hi))
    return spans


def _subtable_start(cblock: str, cap_start: int, prev_end: int) -> int | None:
    """Line start of the outermost tabular the caption at cap_start belongs to (the last one
    between the previous caption and this one), walking back over an immediately preceding
    \resizebox / \begin{center} / \begin{adjustbox} line."""
    spans = _toplevel_tabulars(cblock, prev_end, cap_start)
    if not spans:
        return None
    start = spans[-1][0]
    line_start = cblock.rfind("\n", 0, start) + 1
    while line_start > 0:
        prev_ls = cblock.rfind("\n", 0, line_start - 1) + 1
        prev_line = cblock[prev_ls:line_start].strip()
        if prev_line and re.match(r"(\\resizebox|\\scalebox|\\begin\{(center|adjustbox)\}|\\adjustbox|\{$)", prev_line):
            line_start = prev_ls
            continue
        break
    return line_start


def fit_table(text: str, label: str) -> tuple[str, dict]:
    """Wrap the outermost tabular of the table labelled `label` in \\resizebox{\\linewidth}{!}{...}."""
    info = {"label": label, "status": "skipped"}
    b = figure_block_with_label(text, label, envs="figure|table")
    if not b or not b[2].startswith("table"):
        info["reason"] = "no table block directly contains this label"
        return text, info
    bs, be, env = b
    block = text[bs:be]
    cblock = strip_comments_keep_len(block)
    spans = _toplevel_tabulars(cblock, 0, len(cblock))
    if not spans:
        info["reason"] = "no tabular in the block"
        return text, info
    ts, te = spans[0]
    before = cblock[:ts]
    if re.search(r"\\resizebox\s*\{[^}]*\}\s*\{[^}]*\}\s*\{\s*$", before.rstrip()):
        info["reason"] = "already inside a \\resizebox"
        return text, info
    new_block = (block[:ts] + "\\resizebox{\\linewidth}{!}{% paper-migrate: layout only, the table overflowed the text width\n"
                 + block[ts:te] + "\n}" + block[te:])
    info["status"] = "fitted"
    return text[:bs] + new_block + text[be:], info


def _box_spans(cblock: str) -> list[tuple[int, int]]:
    """Spans of sub-boxes (subtable/subfigure/minipage) inside a float block."""
    spans = []
    for m in re.finditer(r"\\begin\s*\{(subtable|subfigure|minipage)\}", cblock):
        e = re.compile(r"\\end\s*\{" + m.group(1) + r"\}").search(cblock, m.end())
        if e:
            spans.append((m.start(), e.end()))
    return spans


def _assign_captions(cblock: str) -> list[tuple[tuple[int, int], tuple[int, int, str, str | None] | None]]:
    """Pair every outermost tabular with its caption group.  Structure first: a caption inside a
    sub-box belongs to the tabular in that box; only top-level captions use the nearest rule."""
    tabs = _toplevel_tabulars(cblock, 0, len(cblock))
    caps = _caption_groups(cblock)
    boxes = _box_spans(cblock)

    def box_of(pos: int) -> int:
        return next((i for i, (a, b) in enumerate(boxes) if a <= pos < b), -1)

    pairs: list[tuple[int, int, int]] = []          # (distance, tab_idx, cap_idx)
    for ti, (ts, te) in enumerate(tabs):
        for ci, (ls, le, _, _) in enumerate(caps):
            bt, bc = box_of(ts), box_of(ls)
            if bt != bc:
                continue                             # different boxes never pair
            if le <= ts:
                d = ts - le                          # caption before the tabular
            elif ls >= te:
                d = ls - te                          # caption after the tabular
            else:
                d = 0                                # overlapping (should not happen)
            if bt >= 0:
                d = 0                                # same box: settled by structure
            pairs.append((d, ti, ci))
    pairs.sort()
    used_t, used_c = set(), set()
    assign: dict[int, int] = {}
    for d, ti, ci in pairs:
        if ti in used_t or ci in used_c:
            continue
        assign[ti] = ci
        used_t.add(ti)
        used_c.add(ci)
    return [((ts, te), caps[assign[ti]] if ti in assign else None) for ti, (ts, te) in enumerate(tabs)]


def move_table_captions(text: str, where: str) -> tuple[str, int]:
    """Put every table caption (+ directly following \label) above or below ITS tabular
    material.  Each caption belongs to the nearest outermost tabular (before or after it), so a
    float with several tables, or with sub-boxes, is handled per sub-table.  Only caption lines
    move; no text changes."""
    moved = 0
    pos = 0
    while True:
        clean = strip_comments_keep_len(text)
        m = re.compile(r"\\begin\s*\{(table\*?|wraptable)\}").search(clean, pos)
        if not m:
            break
        env = m.group(1)
        e = re.compile(r"\\end\s*\{" + re.escape(env) + r"\}").search(clean, m.end())
        if not e:
            break
        bs, be = m.start(), e.end()
        block, cblock = text[bs:be], clean[bs:be]
        assignment = _assign_captions(cblock)
        edits: list[tuple[int, int, int]] = []          # (cap_ls, cap_le, target_pos)
        for (ts, te), cap in assignment:
            if cap is None:
                continue
            ls, le, _, _ = cap
            currently_above = le <= ts
            if where == "above" and not currently_above:
                # walk back from the tabular over wrapper lines (\resizebox, \begin{adjustbox}, \begin{center})
                line_start = cblock.rfind("\n", 0, ts) + 1
                while line_start > 0:
                    prev_ls = cblock.rfind("\n", 0, line_start - 1) + 1
                    prev_line = cblock[prev_ls:line_start].strip()
                    if prev_line and re.match(r"(\\resizebox|\\scalebox|\\begin\{(center|adjustbox)\}|\\adjustbox|\{$)", prev_line):
                        line_start = prev_ls
                        continue
                    break
                edits.append((ls, le, line_start))
            elif where == "below" and currently_above:
                end = te
                extra = re.match(r"\s*\}?\s*(\\end\s*\{(center|adjustbox)\})?", cblock[end:])
                if extra and extra.group(0).strip():
                    end += extra.end()
                nl = cblock.find("\n", end)
                edits.append((ls, le, len(cblock) if nl < 0 else nl + 1))
        if not edits:
            pos = be
            continue
        # apply edits on the block text: cut all caption lines first, then insert at adjusted targets
        pieces = []
        cuts = sorted((ls, le) for ls, le, _ in edits)
        new_block = block
        offset_map = []
        for ls, le in reversed(cuts):
            pieces.insert(0, block[ls:le])
            new_block = new_block[:ls] + new_block[le:]
        def adjust(pos_: int) -> int:
            shift = sum(le - ls for ls, le in cuts if le <= pos_)
            return pos_ - shift
        inserts = sorted(((adjust(t), block[ls:le]) for ls, le, t in edits), key=lambda x: -x[0])
        for tpos, lines in inserts:
            new_block = new_block[:tpos] + lines + new_block[tpos:]
        moved += len(edits)
        text = text[:bs] + new_block + text[be:]
        pos = bs + len(new_block)
    return text, moved


def wrap_float(text: str, spec: str, anchors: dict[str, str], project: Path, dst_text_in: float,
               default_max: float = 0.5, table_max: float = 0.6, dst_text_height_in: float = 9.0,
               dry: bool = False) -> tuple[str, dict]:
    parts = spec.split("@")
    label = parts[0].strip()
    side = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "r"
    frac_explicit = float(parts[2]) if len(parts) > 2 and parts[2].strip() else None
    info: dict = {"label": label, "side": side, "status": "refused"}
    b = figure_block_with_label(text, label, envs="figure|table")
    if not b:
        info["reason"] = "no top-level figure/table block directly contains this label"
        return text, info
    bs, be, env = b
    base = env.rstrip("*")
    block = text[bs:be]
    clean_block = strip_comments_keep_len(block)
    m1 = re.match(r"\\begin\s*\{" + re.escape(env) + r"\}\s*(\[[^\]]*\])?", block)
    m2 = re.search(r"\\end\s*\{" + re.escape(env) + r"\}\s*$", block)
    inner = block[m1.end():m2.start()].strip("\n")
    if base == "figure":
        g = GRAPHIC_RE.search(clean_block)
        if not g:
            info["reason"] = "figure block has no \\includegraphics"
            return text, info
        we = parse_width(g.group(1) or "")
        mm = re.fullmatch(r"([0-9.]+)?\\linewidth", (we or "").replace(" ", ""))
        cur = float(mm.group(1)) if mm and mm.group(1) else (1.0 if mm else None)
        if frac_explicit is None and cur is None:
            info["reason"] = f"width {we!r} is not <frac>\\linewidth; run the size pass first or give @FRAC"
            return text, info
        frac = frac_explicit if frac_explicit is not None else min(cur, default_max)
        gpath = resolve_graphic(project, g.group(2))
        size = graphic_size(gpath) if gpath else None
        ratio = (size[1] / size[0]) if size and size[0] else 0.75
        if not size:
            info["note"] = "graphic size unknown; assumed height/width = 0.75"
        height_pt = frac * dst_text_in * 72.27 * ratio
        inner = GRAPHIC_RE.sub(_relative_graphic, inner, count=1)
        if frac > default_max:
            info["note"] = (info.get("note", "") + f" width {fmt_frac(frac)} > {default_max}: little room for text").strip()
    else:
        est = estimate_table(clean_block, dst_text_in)
        if frac_explicit is not None:
            frac = frac_explicit
        elif est["width_pt"] is None:
            info["reason"] = "table width cannot be estimated (no tabular found); give @FRAC to force"
            return text, info
        else:
            frac = est["width_pt"] * 1.14 / (dst_text_in * 72.27)     # heuristic estimate; 14% safety margin
            frac = math.ceil(frac * 100) / 100
            if frac > table_max:
                info["reason"] = f"estimated table width {fmt_frac(frac)} of the line exceeds --wrap-max-table {table_max}; kept as a float"
                return text, info
        rows = max(est["rows"], clean_block.count("\\\\")) + 2
        height_pt = rows * LINE_PT * (est["font_pt"] / 10.0)
        if est["full_width"]:
            # a \resizebox{\textwidth} inside a wraptable would still mean the page width: retarget it
            inner, n_rb = re.subn(r"\\resizebox\s*\{\\(textwidth|columnwidth)\}", r"\\resizebox{\\linewidth}", inner)
            if n_rb:
                info["note"] = f"\\resizebox target changed to \\linewidth ({n_rb})"
        info["est_table_width_in"] = None if est["width_pt"] is None else round(est["width_pt"] / 72.27, 2)
    n_caps = len(re.findall(r"\\caption\s*(\[|\{)", clean_block))
    if n_caps > 1:
        info["reason"] = f"float holds {n_caps} captions (several tables/figures); a wrapped box cannot break across pages -- pair them instead (--pair)"
        return text, info
    cap_lines = math.ceil(caption_chars(clean_block) / max(1.0, frac * dst_text_in * CHARS_PER_INCH))
    need = math.ceil(height_pt / LINE_PT) + cap_lines + 2
    max_lines = int(0.45 * dst_text_height_in * 72.27 / LINE_PT)
    if need > max_lines:
        info["reason"] = f"box needs ~{need} lines, more than 45% of the text height ({max_lines} lines); too tall to wrap safely"
        return text, info
    # remove the block, then find the anchor in the remaining text
    tail = text[be:]
    cut = min(2, len(tail) - len(tail.lstrip("\n")))
    without = text[:bs].rstrip(" ") + text[be + cut:]
    anc = find_anchor(without, label, anchors.get(label))
    info["anchor"] = "reference" if anc else None
    if anc is None:
        # never referenced: anchor at the text paragraph the author placed the float after, i.e. the
        # last text run BEFORE its source position (keeps the float ahead of later floats, so the
        # numbering does not shift); fall back to the first text run after it
        cw = strip_comments_keep_len(without)
        runs = text_runs(cw)
        def text_start(a: int, b: int) -> int | None:
            # skip heading/label lead lines first, then judge what remains
            off = a
            for line in cw[a:b].split("\n"):
                if LEAD_LINE_RE.match(line) or not line.strip():
                    off += len(line) + 1
                    continue
                break
            if off >= b:
                return None
            rest = cw[off:b]
            if FLOAT_RUN_RE.match(rest) or STOP_RUN_RE.match(rest):
                return None
            return off
        before = [(a, b) for a, b in runs if b <= bs]
        after = [(a, b) for a, b in runs if a >= bs]
        for a, b in reversed(before[-2:]) if before else []:
            off = text_start(a, b)
            if off is not None and word_count(cw[off:b]) >= 40:
                anc = (off, cw[off:off + 60].replace("\n", " "))
                info["anchor"] = "source position: the paragraph before the float (never referenced in the text)"
                break
        if anc is None:
            for a, b in after:
                off = text_start(a, b)
                if off is not None:
                    anc = (off, cw[off:off + 60].replace("\n", " "))
                    info["anchor"] = "source position: the paragraph after the float (never referenced in the text)"
                    break
    if anc is None:
        info["reason"] = "no paragraph references this label and no text follows its source position"
        return text, info
    anchor, preview = anc
    have, words = capacity_lines(without, anchor, frac, dst_text_in)
    info.update({"env": WRAP_ENV[base], "frac": round(frac, 3), "need_lines": need,
                 "capacity_lines": round(have, 1), "anchor_words": words, "anchor_preview": preview})
    if have < need:
        info["reason"] = f"anchor paragraph(s) offer ~{have:.0f} lines, box needs ~{need}; kept as a float"
        return text, info
    if dry:
        info["status"] = "possible"
        return text, info
    wrap = (f"% paper-migrate layout: {base} wrapped by text (was a standalone float); caption and label unchanged\n"
            f"\\begin{{{WRAP_ENV[base]}}}{{{side}}}{{{fmt_frac(frac)}\\linewidth}}\n{inner}\n\\end{{{WRAP_ENV[base]}}}\n")
    ls = without.rfind("\n", 0, anchor) + 1
    new_text = without[:ls] + wrap + without[ls:]
    info["status"] = "wrapped"
    return new_text, info


def ensure_package(text: str, package: str, comment: str) -> tuple[str, bool]:
    m = re.search(r"\\begin\s*\{document\}", text)
    if not m:
        return text, False
    pre = strip_comments_keep_len(text[:m.start()])
    if re.search(r"\\usepackage(\[[^\]]*\])?\{[^}]*\b" + re.escape(package) + r"\b", pre):
        return text, False
    return text[:m.start()] + f"\\usepackage{{{package}}} % {comment}\n" + text[m.start():], True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True, help="original main .tex (defines the physical sizes)")
    ap.add_argument("--in", dest="inp", required=True, help="migrated main .tex to adjust")
    ap.add_argument("--out", required=True)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--manifest", default=str(Path(__file__).resolve().parent.parent / "venues.yaml"))
    ap.add_argument("--src-venue")
    ap.add_argument("--dst-venue")
    ap.add_argument("--src-text-width", type=float)
    ap.add_argument("--src-column-width", type=float)
    ap.add_argument("--src-columns", type=int)
    ap.add_argument("--dst-text-width", type=float)
    ap.add_argument("--max-frac", type=float, default=1.0)
    ap.add_argument("--min-frac", type=float, default=0.0,
                    help="enlarge standalone graphics and sub-figure containers below this fraction of the line width (0 = never; reported)")
    ap.add_argument("--pair", action="append", default=[], metavar="LABEL_A,LABEL_B",
                    help="place these figures side by side in one float (repeatable)")
    ap.add_argument("--pair-gap", type=float, default=0.03, help="fraction of line width kept between paired figures")
    ap.add_argument("--pair-mode", choices=["subfloats", "minipage"], default="subfloats",
                    help="subfloats: one caption with (a)/(b) sub-captions (default); minipage: independent captions")
    ap.add_argument("--placement", help="normalise figure placement specifier, e.g. t or !t")
    ap.add_argument("--wrap", action="append", default=[], metavar="LABEL[@SIDE][@FRAC]",
                    help="wrap this figure/table with text (repeatable); refused when the anchor text is too short")
    ap.add_argument("--wrap-anchor", action="append", default=[], metavar="LABEL@SNIPPET",
                    help="anchor the wrap at the paragraph containing SNIPPET instead of the first \\ref")
    ap.add_argument("--wrap-max", type=float, default=0.5, help="default cap on a wrapped figure's width (fraction)")
    ap.add_argument("--wrap-max-table", type=float, default=0.6, help="refuse to wrap tables estimated wider than this fraction")
    ap.add_argument("--list-tables", action="store_true", help="print table width estimates and exit (writes nothing)")
    ap.add_argument("--suggest", action="store_true",
                    help="after the size pass, print for every figure/table whether it could be wrapped or paired and why; writes nothing")
    ap.add_argument("--table-captions", choices=["above", "below"], help="move table captions above or below the tabular material")
    ap.add_argument("--fit-table", action="append", default=[], metavar="LABEL", help="scale this table to the text width with \\resizebox (repeatable)")
    ap.add_argument("--verbatim-size", metavar="SIZE", help="font size switch applied around verbatim blocks that sit inside floats (small, footnotesize, scriptsize)")
    ap.add_argument("--report", help="write a JSON report here")
    args = ap.parse_args()

    try:
        if args.src_venue or args.dst_venue:
            man = load_manifest(Path(args.manifest))
        def geo_from(venue_id, tw, cw, cols) -> Geometry:
            th = 9.0
            if venue_id:
                lay = man["venues"][venue_id].get("layout") or {}
                tw = tw or float(lay["text_width_in"])
                cols = cols or int(lay.get("columns", 1))
                cw = cw or float(lay.get("column_width_in", tw))
                th = float(lay.get("text_height_in", 9.0))
            if tw is None:
                raise ValueError("text width unknown: give --*-venue or --*-text-width")
            return Geometry(tw, cw or tw, cols or 1, th)
        src_geo = geo_from(args.src_venue, args.src_text_width, args.src_column_width, args.src_columns)
        dst_geo = geo_from(args.dst_venue, args.dst_text_width, None, None)
        src_text = Path(args.src).read_text(encoding="utf-8")
        text = Path(args.inp).read_text(encoding="utf-8")
        out = Path(args.out)
        if out.exists() and not args.force:
            print(f"layout_figures: refusing to overwrite {out} (use --force)", file=sys.stderr)
            return 2
    except (OSError, KeyError, ValueError) as exc:
        print(f"layout_figures: {exc}", file=sys.stderr)
        return 2

    if args.list_tables:
        DW0 = dst_geo.text_width_in
        print(f"{'label':<24} {'env':<9} {'cols':>4} {'rows':>4} {'font':>4} {'est width':>10} {'frac':>5}  caption  note")
        for t in list_tables(text, DW0):
            cand = t["est_frac"] is not None and t["est_frac"] <= 0.6
            note = ("wrap candidate" if cand else "") + (" (has resizebox/tabularx to the line width)" if t["full_width"] else "")
            print(f"{t['label']:<24} {t['env']:<9} {t['cols']:>4} {t['rows']:>4} {t['font_pt']:>4.0f} "
                  f"{(str(t['est_width_in']) + 'in') if t['est_width_in'] is not None else '?':>10} "
                  f"{(fmt_frac(t['est_frac']) if t['est_frac'] is not None else '?'):>5}  {t['caption']:<7}  {note}")
        return 0

    src_g = scan(src_text, src_geo)
    dst_g = scan(text, dst_geo)
    if [Path(g.file).name for g in src_g] != [Path(g.file).name for g in dst_g]:
        print("layout_figures: graphic sequence differs between --src and --in; run body_diff first", file=sys.stderr)
        print("  src:", [Path(g.file).name for g in src_g], file=sys.stderr)
        print("  dst:", [Path(g.file).name for g in dst_g], file=sys.stderr)
        return 2

    DW = dst_geo.text_width_in
    CW = dst_geo.column_width_in
    two_col = dst_geo.columns == 2
    rows = []
    edits: list[tuple[int, int, str]] = []          # (start, end, replacement) on `text`
    container_done: set[tuple[int, int]] = set()
    env_wanted: dict[tuple[int, int], bool] = {}    # dst float span -> starred?
    # total physical width per dst float (containers side by side share one row)
    float_total: dict[int, float] = {}
    for s, d in zip(src_g, dst_g):
        if d.float_span and s.phys_in is not None:
            key = d.float_span[0]
            width = s.container_phys_in if (d.container and s.container_phys_in) else s.phys_in
            float_total[key] = float_total.get(key, 0.0) + width
    for s, d in zip(src_g, dst_g):
        row = {"idx": d.idx, "file": Path(d.file).name, "src_float": s.float_env, "src_container": s.container,
               "src_width_expr": s.width_expr, "src_phys_in": s.phys_in, "action": ""}
        if s.phys_in is None:
            row["action"] = s.note or "unchanged"
            rows.append(row)
            continue
        base = DW
        base_name = "\\linewidth"
        if two_col and d.float_span:
            total = float_total.get(d.float_span[0], s.phys_in)
            starred = total > CW * 1.02
            env_wanted[d.float_span] = starred
            base = DW if starred else CW
            base_name = "\\textwidth" if starred else "\\columnwidth"
        if d.container and d.container_width_span and s.container_phys_in:
            frac = min(s.container_phys_in / base, args.max_frac)
            enlarged = ""
            if args.min_frac and frac < args.min_frac:
                if fmt_frac(frac) != fmt_frac(args.min_frac):
                    enlarged = f" (enlarged from {fmt_frac(frac)}; keep n x min-frac <= 1)"
                frac = args.min_frac
            if d.container_width_span not in container_done:
                edits.append((d.container_width_span[0] + 1, d.container_width_span[1], f"{fmt_frac(frac)}\\linewidth"))
                container_done.add(d.container_width_span)
            inner = s.phys_in / s.container_phys_in
            new_opts = replace_width_opt(d.opts, f"{fmt_frac(min(inner, 1.0))}\\linewidth")
            edits.append((d.span[0], d.span[1], f"\\includegraphics{new_opts}{{{d.file}}}"))
            row["action"] = (f"container {d.container} -> {fmt_frac(frac)}\\linewidth ({s.container_phys_in:.2f}in), "
                             f"inner {fmt_frac(min(inner,1.0))}\\linewidth{enlarged}"
                             + (f"; float {'spans both columns (figure*)' if env_wanted.get(d.float_span) else 'stays in one column'}" if two_col else ""))
            row["dst_frac"] = round(frac, 3)
        else:
            frac = s.phys_in / base
            note = ""
            if frac > args.max_frac:
                frac, note = args.max_frac, " (capped)"
            if args.min_frac and frac < args.min_frac and fmt_frac(frac) != fmt_frac(args.min_frac):
                frac, note = args.min_frac, f" (enlarged from {fmt_frac(s.phys_in / base)})"
            elif args.min_frac and frac < args.min_frac:
                frac = args.min_frac
            new_opts = replace_width_opt(d.opts, f"{fmt_frac(frac)}{base_name}")
            edits.append((d.span[0], d.span[1], f"\\includegraphics{new_opts}{{{d.file}}}"))
            row["action"] = f"{s.width_expr} = {s.phys_in:.2f}in -> {fmt_frac(frac)}{base_name}{note}" + \
                            (f" ({'figure*' if env_wanted.get(d.float_span) else 'single column'})" if two_col and d.float_span else "")
            row["dst_frac"] = round(frac, 3)
        rows.append(row)

    # two-column target: star or un-star each figure float according to its physical width
    env_changes = 0
    if two_col:
        for span, starred in env_wanted.items():
            start, end = span
            if end is None:
                continue
            mb = re.match(r"\\begin\s*\{(figure\*?)\}", text[start:])
            me = re.search(r"\\end\s*\{(figure\*?)\}\s*$", text[start:end])
            if not mb or not me:
                continue
            want = "figure*" if starred else "figure"
            if mb.group(1) != want:
                edits.append((start, start + mb.end(), f"\\begin{{{want}}}"))
                edits.append((start + me.start(), start + me.end(), f"\\end{{{want}}}"))
                env_changes += 1

    for start, end, rep in sorted(edits, key=lambda e: -e[0]):
        text = text[:start] + rep + text[end:]

    table_env_changes = 0
    unwrapped = 0
    if two_col:
        # wrapped floats do not belong in a two-column layout: back to ordinary floats
        text, n1 = re.subn(r"\\begin\s*\{wrapfigure\}\s*(\[[^\]]*\])?\s*\{[^}]*\}\s*\{[^}]*\}", lambda _m: "\\begin{figure}[t]", text)
        text, n2 = re.subn(r"\\end\s*\{wrapfigure\}", lambda _m: "\\end{figure}", text)
        text, n3 = re.subn(r"\\begin\s*\{wraptable\}\s*(\[[^\]]*\])?\s*\{[^}]*\}\s*\{[^}]*\}", lambda _m: "\\begin{table}[t]", text)
        text, n4 = re.subn(r"\\end\s*\{wraptable\}", lambda _m: "\\end{table}", text)
        unwrapped = n1 + n3
        # tables: span both columns when the estimated natural width exceeds the column
        clean = strip_comments_keep_len(text)
        spans = []
        for m in re.finditer(r"\\begin\s*\{(table\*?)\}", clean):
            e = re.compile(r"\\end\s*\{" + re.escape(m.group(1)) + r"\}").search(clean, m.end())
            if e:
                spans.append((m.start(), e.start(), e.end(), m.group(1)))
        for bs, es, ee, env in sorted(spans, key=lambda x: -x[0]):
            est = estimate_table(clean[bs:ee], DW)
            if est["width_pt"] is None:
                continue
            want = "table*" if est["width_pt"] > CW * 72.27 * 1.02 else "table"
            if want != env:
                text = text[:bs] + f"\\begin{{{want}}}" + text[bs + len(f"\\begin{{{env}}}"):es] + f"\\end{{{want}}}" + text[ee:]
                table_env_changes += 1

    anchors = {}
    for spec in args.wrap_anchor:
        if "@" in spec:
            k, v = spec.split("@", 1)
            anchors[k.strip()] = v
    anchors_for_suggest = anchors

    # ---- suggestions: what could be wrapped or paired, and why not ----------- #
    if args.suggest:
        clean_s = strip_comments_keep_len(text)
        rows_s = []
        blocks = []
        for m in re.finditer(r"\\begin\s*\{(figure\*?|table\*?)\}", clean_s):
            e = re.compile(r"\\end\s*\{" + re.escape(m.group(1)) + r"\}").search(clean_s, m.end())
            if not e:
                continue
            blk = clean_s[m.start():e.end()]
            labs = re.findall(r"\\label\s*\{([^}]*)\}", blk)
            ncap = len(re.findall(r"\\caption\s*(\[|\{)", blk))
            if not labs:
                continue
            base = m.group(1).rstrip("*")
            if base == "figure":
                gs = list(GRAPHIC_RE.finditer(blk))
                fr = 0.0
                for g in gs:
                    we = parse_width(g.group(1) or "")
                    mm = re.fullmatch(r"([0-9.]+)?\\(linewidth|columnwidth|textwidth)", (we or "").replace(" ", ""))
                    fr += float(mm.group(1)) if mm and mm.group(1) else (1.0 if mm else 0)
                if re.search(r"\\begin\{subfigure\}", blk):
                    fr = sum(float(x) for x in re.findall(r"\\begin\{subfigure\}\s*(?:\[[^\]]*\])?\s*\{([0-9.]+)\\linewidth\}", blk)) or fr
            else:
                est = estimate_table(blk, DW)
                fr = (est["width_pt"] or 0) / (DW * 72.27) if est["width_pt"] else 1.0
            blocks.append({"label": labs[0], "env": m.group(1), "frac": round(min(fr, 1.0), 2), "captions": ncap, "start": m.start()})
        for i, b in enumerate(blocks):
            verdict = ""
            if two_col:
                verdict = "two-column target: no wrapping"
            elif b["captions"] > 1:
                verdict = "holds several captions: pair/split instead of wrapping"
            elif b["frac"] > (0.6 if b["env"].startswith("table") else 0.5):
                verdict = f"too wide to wrap ({b['frac']:.2f} of the line; limit {0.6 if b['env'].startswith('table') else 0.5})"
            else:
                _, info = wrap_float(text, b["label"], anchors_for_suggest if 'anchors_for_suggest' in dir() else {}, Path(args.inp).resolve().parent, DW,
                                     args.wrap_max, args.wrap_max_table, dst_geo.text_height_in, dry=True)
                if info["status"] == "possible":
                    verdict = f"WRAP ok at {fmt_frac(info['frac'])}: needs ~{info['need_lines']} lines, anchor ({info['anchor']}) offers ~{info['capacity_lines']}"
                else:
                    verdict = "cannot wrap: " + info.get("reason", "")
            nb = blocks[i + 1] if i + 1 < len(blocks) else None
            if nb and nb["env"].rstrip("*") == b["env"].rstrip("*") and b["frac"] + nb["frac"] <= 1.05 and b["captions"] == 1 and nb["captions"] == 1:
                between = clean_s[b["start"]:nb["start"]]
                if not re.search(r"\\(section|subsection)\b", between):
                    verdict += f" | PAIR with {nb['label']} possible ({b['frac']:.2f}+{nb['frac']:.2f})"
            rows_s.append({**b, "verdict": verdict})
        print(f"{'label':<26} {'env':<8} {'width':>5} caps  verdict")
        for r in rows_s:
            print(f"{r['label']:<26} {r['env']:<8} {r['frac']:>5.2f}  {r['captions']:>2}   {r['verdict'][:150]}")
        if args.report:
            Path(args.report).write_text(json.dumps({"suggestions": rows_s}, indent=2) + "\n", encoding="utf-8")
        return 0

    # ---- placement -------------------------------------------------------- #
    placement_n = 0
    if args.placement:
        text, placement_n = re.subn(r"(\\begin\s*\{figure\*?\})\s*\[[^\]]*\]", r"\1[" + args.placement + "]", text)

    # ---- pairing ---------------------------------------------------------- #
    pairs_done = []
    for spec in args.pair:
        labels = [x.strip() for x in spec.split(",") if x.strip()]
        text, pinfo = pair_floats(text, labels, DW, args.pair_gap, args.placement, args.table_captions, args.pair_mode, dst_geo)
        pairs_done.append(pinfo)
        if pinfo["status"] != "paired":
            print(f"layout_figures: --pair {spec}: {pinfo.get('reason')}", file=sys.stderr)
    if any(p["status"] == "paired" and p["mode"] == "subfloats" for p in pairs_done):
        text, added = ensure_package(text, "subcaption", "paper-migrate: sub-captions for combined floats")
        if added:
            pairs_done.append({"labels": ["-"], "status": "package", "note": "\\usepackage{subcaption} added to the preamble"})
    if any(p["status"] == "paired" and p.get("env") == "table" for p in pairs_done) and "\\begin{adjustbox}" in text:
        text, added = ensure_package(text, "adjustbox", "paper-migrate: sub-tables shrink to their box only when needed")
        if added:
            pairs_done.append({"labels": ["-"], "status": "package", "note": "\\usepackage{adjustbox} added to the preamble"})

    # ---- wrapping --------------------------------------------------------- #
    wraps_done = []
    for spec in args.wrap:
        if two_col:
            wraps_done.append({"label": spec.split("@")[0], "side": "r", "status": "refused",
                               "reason": "two-column target: text wrapping is not used; the float keeps its column"})
            continue
        text, info = wrap_float(text, spec, anchors, Path(args.inp).resolve().parent, DW, args.wrap_max, args.wrap_max_table, dst_geo.text_height_in)
        wraps_done.append(info)
    fits_done = []
    for lab in args.fit_table:
        text, info = fit_table(text, lab)
        fits_done.append(info)
    verbatim_n = 0
    if args.verbatim_size:
        # only blocks inside a float; skip ones already preceded by a size switch on the previous line
        out_parts, pos = [], 0
        clean = strip_comments_keep_len(text)
        floats = float_spans(clean)
        for m in re.finditer(r"\\begin\s*\{verbatim\}.*?\\end\s*\{verbatim\}", clean, re.S):
            if not any(a <= m.start() < b for a, b in floats):
                continue
            before = clean[:m.start()].rstrip()                 # comment-stripped: a commented %{\\small does not count
            if re.search(r"\\(tiny|scriptsize|footnotesize|small)\s*$", before):
                continue
            out_parts.append(text[pos:m.start()])
            # the closing brace must start a new line: TeX drops anything after \\end{verbatim} on that line
            out_parts.append("{\\" + args.verbatim_size + " % paper-migrate: verbatim lines were wrapped for a wider column\n" + text[m.start():m.end()] + "\n}")
            pos = m.end()
            verbatim_n += 1
        out_parts.append(text[pos:])
        text = "".join(out_parts)
    captions_moved = 0
    if args.table_captions:
        text, captions_moved = move_table_captions(text, args.table_captions)
    if any(w["status"] == "wrapped" for w in wraps_done):
        text, added = ensure_package(text, "wrapfig", "paper-migrate: text-wrapped floats")
        if added:
            wraps_done.append({"label": "-", "status": "package", "note": "\\usepackage{wrapfig} added to the preamble"})

    out.write_text(text, encoding="utf-8")

    print(f"layout_figures  src={args.src}  ->  {out}")
    print(f"geometry        src text {src_geo.text_width_in:.3f}in / column {src_geo.column_width_in:.3f}in "
          f"({src_geo.columns} col)  ->  dst text {DW:.2f}in (1 col)")
    print(f"{'#':>2} {'file':<26} {'src context':<22} {'action'}")
    for r in rows:
        ctx = (r["src_float"] or "-") + (f" > {r['src_container']}" if r["src_container"] else "")
        print(f"{r['idx']:>2} {r['file']:<26} {ctx:<22} {r['action']}")
    if placement_n:
        print(f"placement       {placement_n} figure environment(s) set to [{args.placement}]")
    if two_col:
        print(f"two columns     {env_changes} figure float(s) starred/un-starred by physical width, {table_env_changes} table float(s), {unwrapped} wrapped float(s) turned back into ordinary floats")
    for p in pairs_done:
        if p["status"] == "paired":
            print(f"paired          {p['items']} -> one {p['env']} ({p['mode']}), widths {[fmt_frac(f) for f in p['fracs']]}\\linewidth"
                  f"{' (scaled by ' + str(p['scaled_by']) + ')' if p['scaled_by'] < 1 else ''}"
                  + ("; split one block into sub-boxes" if p.get("same_block") else "; later block(s) moved up")
                  + ("; \\ref{} renders 3a/3b" if p["mode"] == "subfloats" else ""))
        elif p["status"] == "package":
            print(f"preamble        {p['note']}")
        else:
            print(f"NOT paired      {p['labels']}: {p.get('reason')}")
    for f in fits_done:
        print(f"fit table       {f['label']}: {f['status']}" + (f" ({f['reason']})" if f.get("reason") else " -> \\resizebox{\\linewidth}{!}"))
    if args.verbatim_size:
        print(f"verbatim        {verbatim_n} block(s) inside floats set to \\{args.verbatim_size}")
    if args.table_captions:
        print(f"table captions  {captions_moved} caption(s) moved {args.table_captions} the tabular material")
    for w in wraps_done:
        if w["status"] == "wrapped":
            print(f"wrapped         {w['label']} -> {w['env']}{{{w['side']}}}{{{fmt_frac(w['frac'])}\\linewidth}}; "
                  f"needs ~{w['need_lines']} lines, anchor offers ~{w['capacity_lines']} ({w['anchor_words']} words): "
                  f"\"{w['anchor_preview'][:50]}...\"" + (f"  note: {w['note']}" if w.get("note") else ""))
        elif w["status"] == "package":
            print(f"preamble        {w['note']}")
        else:
            print(f"NOT wrapped     {w['label']}: {w['reason']}")
    print("next: run body_diff.py (expect only format hunks, plus one structure hunk per --pair/--wrap)")
    if args.report:
        Path(args.report).write_text(json.dumps({"rows": rows, "pairs": pairs_done, "wraps": wraps_done, "placement": placement_n,
                                                  "table_captions": {"where": args.table_captions, "moved": captions_moved},
                                                  "fits": fits_done, "verbatim_resized": verbatim_n,
                                                  "two_column_target": {"figure_env_changes": env_changes, "table_env_changes": table_env_changes, "unwrapped": unwrapped} if two_col else None,
                                                  "src_geometry": src_geo.__dict__, "dst_geometry": dst_geo.__dict__},
                                                 indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
