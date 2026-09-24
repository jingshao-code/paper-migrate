#!/usr/bin/env python3
"""body_diff.py -- verify that a LaTeX template migration changed only formatting.

Compares the document body (\\begin{document} ... \\end{document}) of a SOURCE
project and a MIGRATED project at the token level, after normalising away a
whitelist of layout-only constructs.  Every remaining difference is classified:

  format     only whitelisted layout tokens differ (float stars, widths,
             spacing, font sizes, page breaks, rules, resizebox wrappers,
             cite/ref command variants, bibliographystyle ...)    -> allowed
  structure  paragraph breaks, non-float environments, tabular column
             counts, section-level changes                        -> review
  content    anything else: words, numbers, math, macro arguments,
             citation keys, labels, captions, headings            -> FAIL

Additional checks: title equality, author/affiliation text presence, preamble
macro definitions still available to the migrated body, macro-name collisions
with files the migrated preamble \\input s, bibliography-file hashes, figure
file hashes, citation-key / label / ref sets, section-heading sequence.

Standard library only.  Reads files inside the two project roots, never runs
TeX, never touches the network, and writes only the optional --json report
(refusing to overwrite an existing file unless --force).

Exit codes: 0 = PASS (no content hunks; structure hunks allowed unless
--strict-structure), 1 = FAIL, 2 = usage / IO error.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# Whitelists                                                                  #
# --------------------------------------------------------------------------- #

# Float environments whose starred (two-column) variants and placement
# specifiers are pure layout.
FLOAT_ENVS = {"figure", "table", "algorithm", "listing", "wrapfigure", "wraptable",
              "sidewaysfigure", "sidewaystable", "SCfigure", "SCtable"}

# Environments whose mandatory argument(s) are widths / layout hints.
WIDTH_ARG_ENVS = {"subfigure": 1, "subtable": 1, "minipage": 1, "wrapfigure": 2,
                  "wraptable": 2, "adjustbox": 1, "SCfigure": 1, "SCtable": 1}

# Environments whose first mandatory argument is a tabular column spec.
TABULAR_ENVS = {"tabular", "tabular*", "tabularx", "tabulary", "array",
                "longtable", "supertabular", "xltabular"}
# tabular* and tabularx take a width before the column spec.
TABULAR_WIDTH_FIRST = {"tabular*", "tabularx", "tabulary", "xltabular"}

# Layout-only environments (begin/end are format, not structure).
LAYOUT_ENVS = {"center", "flushleft", "flushright", "minipage", "adjustbox",
               "small", "footnotesize", "scriptsize", "singlespace", "spacing",
               "sloppypar", "samepage"}

# Zero-argument layout macros.
FORMAT_0 = {
    "centering", "raggedright", "raggedleft", "noindent", "indent", "hfill",
    "vfill", "hfil", "vfil", "newpage", "clearpage", "cleardoublepage",
    "pagebreak", "nopagebreak", "linebreak", "nolinebreak", "newline",
    "smallskip", "medskip", "bigskip", "tiny", "scriptsize", "footnotesize",
    "small", "normalsize", "large", "Large", "LARGE", "huge", "Huge",
    "maketitle", "toprule", "midrule", "bottomrule", "hline", "strut",
    "onecolumn", "twocolumn", "allowbreak", "sloppy", "fussy", "FloatBarrier",
    "arraystretch", "tabcolsep", "baselineskip", "columnsep", "columnwidth",
    "linewidth", "textwidth", "hsize", "textheight", "topsep", "parskip",
    "parindent", "itemsep", "relax", "ignorespaces", "unskip", "frenchspacing",
    "nonfrenchspacing", "normalfont", "selectfont", "@", "-", ",", ";", ":",
    "!", " ", "/", "quad", "qquad", "enspace", "thinspace", "negthinspace",
    "balance", "flushcolumns", "iclrfinalcopy", "nocopyright",
}

# Layout macros taking N mandatory arguments (optional args are swallowed).
FORMAT_N = {
    "vspace": 1, "vspace*": 1, "hspace": 1, "hspace*": 1, "setlength": 2,
    "addtolength": 2, "setcounter": 2, "addtocounter": 2, "bibliographystyle": 1,
    "setcitestyle": 1, "rule": 2,
    "cmidrule": 1, "cline": 1, "specialrule": 3, "addlinespace": 0,
    "captionsetup": 1, "thispagestyle": 1, "pagestyle": 1, "definecolor": 3,
    "rowcolor": 1, "cellcolor": 1, "arrayrulecolor": 1,
    "columncolor": 1, "linespread": 1, "fontsize": 2, "graphicspath": 1,
    "floatname": 2, "floatstyle": 1, "restylefloat": 1, "hyphenation": 1,
    "enlargethispage": 1, "pdfinfo": 1, "extrarowheight": 0, "afterpage": 1,
    "setstretch": 1, "vskip": 0, "hskip": 0, "kern": 0, "penalty": 0,
    "tcbset": 1, "lstset": 1, "captionof": 0, "subref": 1,
}

# Layout wrappers: consume N args, then treat the following {group} braces as
# layout (the group's contents are still diffed as content).
# NOT whitelisted on purpose (they change what the reader sees): \\textcolor, \\color, \\phantom,
# \\uppercase/\\lowercase/\\MakeUppercase/\\MakeLowercase.  They are ordinary content macros.
WRAPPERS = {
    "resizebox": 2, "resizebox*": 2, "scalebox": 1, "rotatebox": 1,
    "parbox": 1, "makebox": 0, "mbox": 0, "hbox": 0,
    "vbox": 0, "raisebox": 1, "adjustbox": 1, "colorbox": 1, "fcolorbox": 2,
    "framebox": 0, "fbox": 0, "reflectbox": 0, "smash": 0, "clap": 0,
    "rlap": 0, "llap": 0, "mathclap": 0, "mathrlap": 0, "mathllap": 0,
    "textnormal": 0, "textrm": 0, "textsf": 0, "textup": 0, "textmd": 0,
    "textsl": 0, "textsc": 0, "ensuremath": 0, "noindent": 0,
}

# \renewcommand{\X}{..} / \newcommand{\X}{..} in the body is layout only when X
# is one of these.
FORMAT_TARGETS = {"arraystretch", "baselinestretch", "tabcolsep", "arrayrulewidth",
                  "floatpagefraction", "topfraction", "bottomfraction", "textfraction",
                  "thefootnote", "thetable", "thefigure", "figurename", "tablename",
                  "algorithmicrequire", "algorithmicensure"}

CITE_MACROS = {"cite", "citep", "citet", "citealp", "citealt", "citep*", "citet*",
               "citeauthor", "citeauthor*", "citeyear", "citeyearpar", "shortcite",
               "shortciteA", "shortciteN", "citeA", "citeN", "citenum", "Citep",
               "Citet", "parencite", "textcite", "autocite", "citetitle"}
REF_MACROS = {"ref", "eqref", "autoref", "cref", "Cref", "pageref", "nameref",
              "vref", "ref*", "cref*", "Cref*", "labelcref"}
SECTION_MACROS = {"part", "chapter", "section", "subsection", "subsubsection",
                  "paragraph", "subparagraph"}
SECTION_LEVEL = {"part": 0, "chapter": 0, "section": 1, "subsection": 2,
                 "subsubsection": 3, "paragraph": 4, "subparagraph": 5}

PROTECTED_URL_MACROS = ("url", "href", "path", "nolinkurl")
GRAPHIC_EXTS = (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".ps", ".tif", ".tiff", ".svg")

# --------------------------------------------------------------------------- #
# Text-level preprocessing                                                    #
# --------------------------------------------------------------------------- #


def strip_comments(text: str) -> str:
    """Remove % comments while protecting \\url{...}, \\href{...}, \\verb|..|."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            # \url{...}, \href{...}{...}, \path{...}, \nolinkurl{...}
            m = re.match(r"\\(" + "|".join(PROTECTED_URL_MACROS) + r")\s*\{", text[i:])
            if m:
                depth, j = 0, i + m.end() - 1
                while j < n:
                    if text[j] == "{":
                        depth += 1
                    elif text[j] == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    j += 1
                out.append(text[i:j + 1])
                i = j + 1
                continue
            m = re.match(r"\\verb\*?(.)", text[i:])
            if m and not m.group(1).isalpha() and m.group(1) != " ":
                delim = m.group(1)
                j = text.find(delim, i + m.end())
                j = n - 1 if j < 0 else j
                out.append(text[i:j + 1])
                i = j + 1
                continue
            # escaped character (\%, \\, ...) -- copy both chars
            if i + 1 < n:
                out.append(text[i:i + 2])
                i += 2
                continue
            out.append(ch)
            i += 1
            continue
        if ch == "%":
            j = text.find("\n", i)
            if j < 0:
                break
            # TeX also eats the following line's leading whitespace, but we
            # keep the newline so line numbers stay aligned.
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def find_matching_brace(text: str, open_idx: int) -> int:
    depth = 0
    i, n = open_idx, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def extract_macro_arg(text: str, macro: str) -> str | None:
    """Return the first mandatory argument of \\macro{...} in text (comments
    must already be stripped)."""
    m = re.search(r"\\" + re.escape(macro) + r"\*?\s*(\[[^\]]*\]\s*)?\{", text)
    if not m:
        return None
    start = m.end() - 1
    end = find_matching_brace(text, start)
    if end < 0:
        return None
    return text[start + 1:end]


def safe_resolve(root: Path, rel: str) -> Path | None:
    """Resolve rel under root; refuse to escape root or follow symlinks."""
    rel = rel.strip().strip('"')
    if not rel or rel.startswith("/") or "\\" in rel or ":" in rel:
        return None
    if any(p in ("..",) for p in rel.split("/")):
        return None
    cand = root / rel
    try:
        if cand.is_symlink():
            return None
        resolved = cand.resolve()
        if not resolved.is_relative_to(root.resolve()):
            return None
    except OSError:
        return None
    return cand


def flatten_inputs(text: str, root: Path, warnings: list[str], depth: int = 0) -> str:
    """Inline \\input{f} / \\include{f} found in text (comments already stripped)."""
    if depth > 8:
        warnings.append("input nesting deeper than 8 levels; stopped flattening")
        return text
    pattern = re.compile(r"\\(input|include|subfile)\s*\{([^}]*)\}")

    def repl(m: re.Match) -> str:
        name = m.group(2).strip()
        cands = [name] if name.endswith(".tex") else [name + ".tex", name]
        for c in cands:
            p = safe_resolve(root, c)
            if p is not None and p.is_file():
                sub = strip_comments(p.read_text(encoding="utf-8", errors="replace"))
                return "\n" + flatten_inputs(sub, root, warnings, depth + 1) + "\n"
        warnings.append(f"could not resolve \\{m.group(1)}{{{name}}} under {root}; left in place")
        return m.group(0)

    return pattern.sub(repl, text)


def split_document(text: str) -> tuple[str, str]:
    m1 = re.search(r"\\begin\s*\{document\}", text)
    m2 = re.search(r"\\end\s*\{document\}", text)
    if not m1 or not m2 or m2.start() < m1.end():
        raise ValueError("could not find \\begin{document} ... \\end{document}")
    return text[:m1.start()], text[m1.end():m2.start()]


# --------------------------------------------------------------------------- #
# Tokeniser                                                                   #
# --------------------------------------------------------------------------- #

RAW_RE = re.compile(
    r"(?P<cs>\\(?:[A-Za-z@]+\*?|[^A-Za-z@]))"
    r"|(?P<brace>[{}])"
    r"|(?P<bracket>[\[\]])"
    r"|(?P<math>\$\$|\$)"
    r"|(?P<amp>&)"
    r"|(?P<tilde>~)"
    r"|(?P<ws>\s+)"
    r"|(?P<word>[^\s\\{}\[\]$&~]+)",
    re.S,
)


@dataclass
class Raw:
    kind: str   # cs brace bracket math amp tilde ws word
    text: str
    line: int


@dataclass
class Tok:
    key: str        # normalised text used for diffing
    kind: str       # format | struct | content | par
    line: int
    raw: str
    name: str = ""  # macro/env name for statistics


def raw_tokens(text: str, base_line: int = 0) -> list[Raw]:
    out: list[Raw] = []
    line = 1 + base_line
    for m in RAW_RE.finditer(text):
        kind = m.lastgroup or "word"
        s = m.group(0)
        out.append(Raw(kind, s, line))
        line += s.count("\n")
    return out


def match_braces(raws: list[Raw]) -> dict[int, int]:
    """Index of matching brace for every { and }."""
    stack: list[int] = []
    match: dict[int, int] = {}
    for i, r in enumerate(raws):
        if r.kind == "brace":
            if r.text == "{":
                stack.append(i)
            elif stack:
                j = stack.pop()
                match[i] = j
                match[j] = i
    return match


def group_text(raws: list[Raw], start: int, end: int) -> str:
    """Raw text of raws[start+1:end] with whitespace collapsed."""
    parts = []
    for r in raws[start + 1:end]:
        parts.append(" " if r.kind in ("ws", "tilde") else r.text)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def normalise_colspec(spec: str) -> str:
    """Reduce a tabular column spec to its column count."""
    s = spec
    # expand *{n}{spec}
    for _ in range(5):
        m = re.search(r"\*\s*\{\s*(\d+)\s*\}\s*\{([^{}]*)\}", s)
        if not m:
            break
        s = s[:m.start()] + m.group(2) * int(m.group(1)) + s[m.end():]
    # drop decorations with arguments: @{..} !{..} >{..} <{..} and column widths
    s = re.sub(r"[@!><]\s*\{[^{}]*(\{[^{}]*\}[^{}]*)*\}", "", s)
    s = re.sub(r"[pmbLCRXYSNwW]\s*\{[^{}]*(\{[^{}]*\}[^{}]*)*\}", "C", s)
    s = re.sub(r"[|\s]", "", s)
    return str(len(re.findall(r"[A-Za-z]", s)))


class Structurer:
    """Turn raw tokens into classified, normalised tokens."""

    def __init__(self, raws: list[Raw]):
        self.r = raws
        self.match = match_braces(raws)
        self.i = 0
        self.out: list[Tok] = []
        self.format_braces: set[int] = set()
        self.arg_braces: set[int] = set()
        self.cite_variants: dict[str, int] = {}
        self.cite_keys: list[str] = []
        self.labels: list[str] = []
        self.refs: list[str] = []
        self.graphics: list[str] = []
        self.bibs: list[str] = []
        self.headings: list[tuple[int, str]] = []
        self.used_cs: set[str] = set()
        self.used_envs: set[str] = set()
        self.star_floats: dict[str, int] = {}

    # -- helpers ---------------------------------------------------------- #
    def peek_sig(self, j: int) -> int:
        n = len(self.r)
        while j < n and self.r[j].kind == "ws" and "\n\n" not in self.r[j].text and self.r[j].text.count("\n") < 2:
            j += 1
        return j

    def take_opt(self, j: int) -> tuple[int, str | None]:
        k = self.peek_sig(j)
        if k < len(self.r) and self.r[k].kind == "bracket" and self.r[k].text == "[":
            depth = 0
            for e in range(k, len(self.r)):
                t = self.r[e]
                if t.kind == "bracket":
                    depth += 1 if t.text == "[" else -1
                    if depth == 0:
                        return e + 1, group_text(self.r, k, e)
            return j, None
        return j, None

    def take_group(self, j: int, mark: str | None = None) -> tuple[int, str | None]:
        k = self.peek_sig(j)
        if k < len(self.r) and self.r[k].kind == "brace" and self.r[k].text == "{" and k in self.match:
            e = self.match[k]
            if mark == "format":
                self.format_braces.update((k, e))
            elif mark == "arg":
                self.arg_braces.update((k, e))
            return e + 1, group_text(self.r, k, e)
        return j, None

    def emit(self, key: str, kind: str, line: int, raw: str, name: str = "") -> None:
        self.out.append(Tok(key, kind, line, raw, name))

    # -- main loop -------------------------------------------------------- #
    def run(self) -> list[Tok]:
        n = len(self.r)
        prev_allows_group = False  # previous token was a cs that takes args
        while self.i < n:
            t = self.r[self.i]
            if t.kind == "ws":
                if t.text.count("\n") >= 2:
                    self.emit("<par>", "par", t.line, "<paragraph-break>")
                self.i += 1
                continue
            if t.kind == "tilde":
                self.i += 1
                continue
            if t.kind == "brace":
                if self.i in self.format_braces:
                    kind = "format"
                elif self.i in self.arg_braces:
                    kind = "content"
                elif t.text == "{":
                    if prev_allows_group:
                        kind = "content"
                        if self.i in self.match:
                            self.arg_braces.add(self.match[self.i])
                    else:
                        kind = "format"
                        if self.i in self.match:
                            self.format_braces.add(self.match[self.i])
                else:
                    kind = "format"
                self.emit(t.text, kind, t.line, t.text)
                # after closing an arg brace, another {group} may be a further arg
                prev_allows_group = (t.text == "}" and kind == "content")
                self.i += 1
                continue
            if t.kind == "bracket":
                self.emit(t.text, "content", t.line, t.text)
                prev_allows_group = False
                self.i += 1
                continue
            if t.kind in ("math", "word"):
                self.emit(t.text, "content", t.line, t.text)
                prev_allows_group = False
                self.i += 1
                continue
            if t.kind == "amp":
                self.emit("&", "struct", t.line, "&")
                prev_allows_group = False
                self.i += 1
                continue
            # control sequence
            prev_allows_group = self.handle_cs(t)
        return self.out

    def handle_cs(self, t: Raw) -> bool:
        name = t.text[1:]
        line = t.line
        self.used_cs.add(name)
        j = self.i + 1

        if name == "\\":
            j, _ = self.take_opt(j)
            self.emit("\\\\", "format", line, t.text, name)
            self.i = j
            return False

        if name in ("begin", "end"):
            j, env = self.take_group(j, mark="format")
            env = (env or "").strip()
            base = env.rstrip("*")
            self.used_envs.add(base)
            if name == "begin":
                if base in FLOAT_ENVS:
                    if env.endswith("*"):
                        self.star_floats[env] = self.star_floats.get(env, 0) + 1
                    j, _ = self.take_opt(j)          # placement
                    if base in WIDTH_ARG_ENVS:
                        for _ in range(WIDTH_ARG_ENVS[base]):
                            j, _ = self.take_group(j, mark="format")
                    self.emit(f"\\begin{{{base}}}", "struct", line, f"\\begin{{{env}}}", base)
                elif env in TABULAR_ENVS or base in TABULAR_ENVS:
                    j, _ = self.take_opt(j)          # [t]/[c]
                    if env in TABULAR_WIDTH_FIRST:
                        j, _ = self.take_group(j, mark="format")
                    j, spec = self.take_group(j, mark="format")
                    cols = normalise_colspec(spec or "")
                    self.emit(f"\\begin{{tabular}}{{{cols}}}", "struct", line,
                              f"\\begin{{{env}}}{{{spec}}}", "tabular")
                elif base in WIDTH_ARG_ENVS:
                    j, _ = self.take_opt(j)
                    for _ in range(WIDTH_ARG_ENVS[base]):
                        j, _ = self.take_group(j, mark="format")
                    kind = "format" if base in LAYOUT_ENVS else "struct"
                    self.emit(f"\\begin{{{base}}}", kind, line, f"\\begin{{{env}}}", base)
                elif base in LAYOUT_ENVS:
                    self.emit(f"\\begin{{{base}}}", "format", line, f"\\begin{{{env}}}", base)
                else:
                    # theorem-like [title] and other opts are content
                    self.emit(f"\\begin{{{env}}}", "struct", line, f"\\begin{{{env}}}", base)
            else:
                if base in FLOAT_ENVS or base in TABULAR_ENVS or env in TABULAR_ENVS:
                    key = "tabular" if (base in TABULAR_ENVS or env in TABULAR_ENVS) else base
                    self.emit(f"\\end{{{key}}}", "struct", line, f"\\end{{{env}}}", base)
                elif base in LAYOUT_ENVS:
                    self.emit(f"\\end{{{base}}}", "format", line, f"\\end{{{env}}}", base)
                else:
                    self.emit(f"\\end{{{env}}}", "struct", line, f"\\end{{{env}}}", base)
            self.i = j
            return False

        if name in ("newcommand", "renewcommand", "providecommand", "def"):
            if name == "def":
                k = self.peek_sig(j)
                target = self.r[k].text[1:] if k < len(self.r) and self.r[k].kind == "cs" else ""
                j = k + 1
            else:
                j, target = self.take_group(j, mark="format")
                target = (target or "").lstrip("\\").strip()
            j, _ = self.take_opt(j)
            j, body = self.take_group(j, mark="format" if target in FORMAT_TARGETS else "arg")
            if target in FORMAT_TARGETS:
                self.emit(f"\\{name}", "format", line, f"\\{name}{{\\{target}}}{{{body}}}", name)
            else:
                self.emit(f"\\{name}{{\\{target}}}{{{body}}}", "content", line,
                          f"\\{name}{{\\{target}}}{{{body}}}", name)
            self.i = j
            return False

        if name == "includegraphics":
            j, _ = self.take_opt(j)
            j, path = self.take_group(j, mark="format")
            path = (path or "").strip()
            self.graphics.append(path)
            base = os.path.basename(path)
            self.emit(f"\\includegraphics{{{base}}}", "content", line, t.text + "{" + path + "}", name)
            self.i = j
            return False

        if name in ("bibliography",):
            j, arg = self.take_group(j, mark="format")
            for b in (arg or "").split(","):
                if b.strip():
                    self.bibs.append(b.strip())
            self.emit(f"\\bibliography{{{arg}}}", "content", line, t.text, name)
            self.i = j
            return False

        if name in CITE_MACROS:
            self.cite_variants[name] = self.cite_variants.get(name, 0) + 1
            # optional pre/post notes are content: emit them as words
            notes = []
            for _ in range(2):
                j2, opt = self.take_opt(j)
                if opt is None:
                    break
                notes.append(opt)
                j = j2
            j, keys = self.take_group(j, mark="format")
            keys = ",".join(k.strip() for k in (keys or "").split(",") if k.strip())
            self.cite_keys.extend(keys.split(",") if keys else [])
            key = "\\CITE" + "".join(f"[{o}]" for o in notes) + "{" + keys + "}"
            self.emit(key, "content", line, t.text + "{" + keys + "}", name)
            self.i = j
            return False

        if name in REF_MACROS:
            j, arg = self.take_group(j, mark="format")
            arg = (arg or "").strip()
            self.refs.extend(a.strip() for a in arg.split(","))
            self.emit(f"\\REF{{{arg}}}", "content", line, t.text + "{" + arg + "}", name)
            self.i = j
            return False

        if name == "label":
            j, arg = self.take_group(j, mark="format")
            arg = (arg or "").strip()
            self.labels.append(arg)
            self.emit(f"\\label{{{arg}}}", "content", line, t.text + "{" + arg + "}", name)
            self.i = j
            return False

        if name == "subref":
            j, arg = self.take_group(j, mark="format")
            self.emit("\\SUBREF", "format", line, "\\subref{" + (arg or "").strip() + "}", "subref")
            self.i = j
            return False

        if name in ("caption", "caption*"):
            k = self.peek_sig(j)
            if k + 1 < len(self.r) and self.r[k].kind == "brace" and self.r[k].text == "{" and self.r[k + 1].kind == "brace" and self.r[k + 1].text == "}":
                self.format_braces.update((k, k + 1))
                self.emit("\\caption{}", "format", line, "\\caption{}", name)
                self.i = k + 2
                return False
            # a real caption: fall through (unknown macro with an argument)

        base = name.rstrip("*")
        if base in SECTION_MACROS:
            j, short = self.take_opt(j)
            k = self.peek_sig(j)
            title = ""
            if k < len(self.r) and self.r[k].kind == "brace" and k in self.match:
                title = group_text(self.r, k, self.match[k])
            self.headings.append((SECTION_LEVEL[base], re.sub(r"\s+", " ", title)))
            self.emit(f"\\{base}", "struct", line, t.text, base)
            self.i = j
            return True  # title group is an argument (content)

        if name in WRAPPERS:
            j, _ = self.take_opt(j)
            for _ in range(WRAPPERS[name]):
                j, _ = self.take_group(j, mark="format")
                j, _ = self.take_opt(j)
            # the following group is the wrapped content; its braces are layout
            k = self.peek_sig(j)
            if k < len(self.r) and self.r[k].kind == "brace" and self.r[k].text == "{" and k in self.match:
                self.format_braces.update((k, self.match[k]))
            self.emit(f"\\{name}", "format", line, t.text, name)
            self.i = j
            return False

        if name in FORMAT_N:
            j, _ = self.take_opt(j)
            for _ in range(FORMAT_N[name]):
                j, _ = self.take_group(j, mark="format")
                j, _ = self.take_opt(j)
            self.emit(f"\\{name}", "format", line, t.text, name)
            self.i = j
            return False

        if name in FORMAT_0:
            self.emit(f"\\{name}", "format", line, t.text, name)
            self.i = j
            return False

        if name in ("appendix", "item", "maketitle", "tableofcontents"):
            kind = "format" if name in ("maketitle",) else "struct"
            j2, opt = self.take_opt(j) if name == "item" else (j, None)
            if opt is not None:
                self.emit(f"\\item[{opt}]", "struct", line, t.text, name)
                j = j2
            else:
                self.emit(f"\\{name}", kind, line, t.text, name)
            self.i = j
            return False

        # unknown macro: content; following {groups} are its arguments
        self.emit(t.text, "content", line, t.text, name)
        self.i = j
        return True


def is_text(tok: "Tok") -> bool:
    """A word or math token (not a macro, brace or bracket)."""
    return tok.kind == "content" and bool(tok.raw) and tok.raw[0] not in "\\{}[]"


CAPTION_FLOATS = {"figure", "table", "wrapfigure", "wraptable", "sidewaysfigure", "sidewaystable",
                  "SCfigure", "SCtable"}
CAPTION_BOXES = {"minipage", "subfigure", "subtable"}


def canonicalize_captions(toks: list[Tok]) -> list[Tok]:
    """Move every top-level \\caption[..]{..} (+ directly following \\label{..}) of a float to the
    end of its float block.  Whether a caption sits above or below the material is layout (the
    template dictates it), so both sides are compared in one canonical order.  Captions inside
    minipage/subfigure boxes are left alone: they belong to the box, not to the float."""
    out: list[Tok] = []
    i, n = 0, len(toks)
    while i < n:
        t = toks[i]
        if t.key.startswith("\\begin{") and t.name in CAPTION_FLOATS and t.kind == "struct":
            depth, j = 0, i
            while j < n:
                u = toks[j]
                if u.name == t.name and u.key.startswith("\\begin{"):
                    depth += 1
                elif u.name == t.name and u.key.startswith("\\end{"):
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if j >= n:
                out.append(t)
                i += 1
                continue
            block = toks[i + 1:j]
            keep: list[Tok] = []
            hoisted: list[Tok] = []
            k, box_depth = 0, 0
            while k < len(block):
                b = block[k]
                if b.key.startswith("\\begin{") and b.name in CAPTION_BOXES:
                    box_depth += 1
                elif b.key.startswith("\\end{") and b.name in CAPTION_BOXES:
                    box_depth = max(0, box_depth - 1)
                if box_depth == 0 and b.key in ("\\caption", "\\caption*"):
                    e = k + 1
                    while e < len(block) and block[e].raw == "[":          # optional short caption
                        d = 0
                        while e < len(block):
                            if block[e].raw == "[":
                                d += 1
                            elif block[e].raw == "]":
                                d -= 1
                                if d == 0:
                                    e += 1
                                    break
                            e += 1
                    if e < len(block) and block[e].raw == "{":
                        d = 0
                        while e < len(block):
                            if block[e].raw == "{":
                                d += 1
                            elif block[e].raw == "}":
                                d -= 1
                                if d == 0:
                                    e += 1
                                    break
                            e += 1
                    if e < len(block) and block[e].key.startswith("\\label{"):
                        e += 1
                    hoisted.extend(block[k:e])
                    k = e
                    continue
                keep.append(b)
                k += 1
            hoisted, keep = split_subcaptions(hoisted, keep)
            out.append(t)
            out.extend(canonicalize_captions(keep) if any(x.name in CAPTION_FLOATS for x in keep) else keep)
            out.extend(hoisted)
            out.append(toks[j])
            i = j + 1
            continue
        out.append(t)
        i += 1
    return out


def split_subcaptions(hoisted: list[Tok], keep: list[Tok]) -> tuple[list[Tok], list[Tok]]:
    """A combined caption `\\caption{\\subref{A} text-A \\subref{B} text-B}` with the labels A, B sitting
    in sub-boxes is the layout form of two captions.  Rewrite it to
    `\\caption{text-A} \\label{A} \\caption{text-B} \\label{B}` and drop the labels from the boxes,
    so that it compares equal to two separately captioned floats."""
    if not any(t.name == "subref" for t in hoisted):
        return hoisted, keep
    out: list[Tok] = []
    i, n = 0, len(hoisted)
    while i < n:
        t = hoisted[i]
        if t.key in ("\\caption", "\\caption*") and i + 1 < n and hoisted[i + 1].raw == "{":
            # find the matching brace of the caption argument
            d, e = 0, i + 1
            while e < n:
                if hoisted[e].raw == "{":
                    d += 1
                elif hoisted[e].raw == "}":
                    d -= 1
                    if d == 0:
                        break
                e += 1
            inner = hoisted[i + 2:e]
            if not any(x.name == "subref" for x in inner):
                out.extend(hoisted[i:e + 1])
                i = e + 1
                continue
            # segments: [preface] [SUBREF L1 seg1] [SUBREF L2 seg2] ...
            segs: list[tuple[str | None, list[Tok]]] = [(None, [])]
            for x in inner:
                if x.name == "subref":
                    segs.append((x.raw[len("\\subref{"):-1], []))
                else:
                    segs[-1][1].append(x)
            open_b, close_b, cap = hoisted[i + 1], hoisted[e], hoisted[i]
            for lab, seg in segs:
                if lab is None and not seg:
                    continue
                out.append(Tok(cap.key, cap.kind, cap.line, cap.raw, cap.name))
                out.append(Tok(open_b.key, open_b.kind, open_b.line, open_b.raw))
                out.extend(seg)
                out.append(Tok(close_b.key, close_b.kind, close_b.line, close_b.raw))
                if lab is not None:
                    # pull the matching label out of the sub-boxes
                    for k, x in enumerate(keep):
                        if x.key == f"\\label{{{lab}}}":
                            out.append(keep.pop(k))
                            break
                    else:
                        out.append(Tok(f"\\label{{{lab}}}", "content", cap.line, f"\\label{{{lab}}}", "label"))
            i = e + 1
            continue
        out.append(t)
        i += 1
    return out, keep


def tokenize(body: str, base_line: int = 0) -> tuple[list[Tok], Structurer]:
    s = Structurer(raw_tokens(body, base_line))
    toks = canonicalize_captions(s.run())
    # drop paragraph breaks unless surrounded by content on both sides
    cleaned: list[Tok] = []
    for idx, tk in enumerate(toks):
        if tk.kind == "par":
            prev = cleaned[-1] if cleaned else None
            nxt = next((x for x in toks[idx + 1:] if x.kind != "par"), None)
            if prev is None or nxt is None or not is_text(prev) or not is_text(nxt):
                continue
            if cleaned and cleaned[-1].kind == "par":
                continue
        cleaned.append(tk)
    return cleaned, s


# --------------------------------------------------------------------------- #
# Diff + classification                                                       #
# --------------------------------------------------------------------------- #

@dataclass
class Hunk:
    cls: str
    src_line: int
    dst_line: int
    removed: list[Tok]
    added: list[Tok]
    context_before: str
    context_after: str
    note: str = ""

    def to_json(self) -> dict:
        return {
            "class": self.cls, "note": self.note, "src_line": self.src_line, "dst_line": self.dst_line,
            "removed": " ".join(t.raw for t in self.removed),
            "added": " ".join(t.raw for t in self.added),
            "context_before": self.context_before, "context_after": self.context_after,
        }


def classify(removed: list[Tok], added: list[Tok]) -> str:
    kinds = {t.kind for t in removed} | {t.kind for t in added}
    if "content" in kinds:
        return "content"
    if "struct" in kinds or "par" in kinds:
        return "structure"
    return "format"


def diff_tokens(a: list[Tok], b: list[Tok], context: int = 4) -> list[Hunk]:
    sm = difflib.SequenceMatcher(None, [t.key for t in a], [t.key for t in b], autojunk=False)
    hunks: list[Hunk] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        removed, added = a[i1:i2], b[j1:j2]
        src_line = removed[0].line if removed else (a[i1 - 1].line if i1 > 0 else 1)
        dst_line = added[0].line if added else (b[j1 - 1].line if j1 > 0 else 1)
        before = " ".join(t.raw for t in a[max(0, i1 - context):i1])
        after = " ".join(t.raw for t in a[i2:i2 + context])
        hunks.append(Hunk(classify(removed, added), src_line, dst_line, removed, added, before, after))
    return detect_moves(hunks)


def detect_moves(hunks: list[Hunk], min_tokens: int = 6) -> list[Hunk]:
    """A block deleted in one place and inserted verbatim elsewhere is a MOVE
    (layout), not a content change.  Compare the content-kind tokens only, so
    that a figure re-wrapped into a minipage still matches; the differing
    format/struct tokens are then judged as structure."""
    def content_keys(toks: list[Tok]) -> list[str]:
        return [t.key for t in toks if t.kind == "content"]

    def same_block(a: list[str], b: list[str]) -> bool:
        """Equal sequences, or a rotation of one another (difflib shifts the cut point when
        a moved block shares its prefix with a neighbouring block, e.g. two figures using
        the same graphic)."""
        if a == b:
            return True
        if len(a) != len(b) or not a:
            return False
        doubled = b + b
        return any(doubled[i:i + len(a)] == a for i in range(len(b)))
    removals = [h for h in hunks if h.cls == "content" and h.removed and not h.added]
    additions = [h for h in hunks if h.cls == "content" and h.added and not h.removed]
    used: set[int] = set()
    for r in removals:
        kr = content_keys(r.removed)
        is_caption = bool(kr) and kr[0] in ("\\caption", "\\caption*")
        is_label = len(kr) == 1 and kr[0].startswith("\\label{")
        # a repositioned caption or a moved \label is short but unambiguous
        if not is_label and len(kr) < (3 if is_caption else min_tokens):
            continue
        for a in additions:
            if id(a) in used:
                continue
            if same_block(content_keys(a.added), kr):
                r.cls = "structure"
                r.dst_line = a.dst_line
                r.added = a.added
                kind = "caption repositioned" if is_caption else ("label repositioned" if is_label else "moved block")
                r.note = (f"{kind}: {len(kr)} content tokens identical, now at dst L{a.dst_line}; "
                          f"only position/wrapping changed")
                used.add(id(a))
                break
    remaining = [h for h in hunks if id(h) not in used]
    # Aggregate test over ALL remaining content-class hunks (pure and mixed): if every content
    # token that disappeared reappears somewhere else and nothing new appears, the document was
    # only rearranged (floats combined, captions and labels regrouped) -- layout, not content.
    pool = [h for h in remaining if h.cls == "content"]
    if pool:
        from collections import Counter
        cd = Counter(k for h in pool for k in content_keys(h.removed))
        ci = Counter(k for h in pool for k in content_keys(h.added))
        if cd and cd == ci:
            for h in pool:
                h.cls = "structure"
                h.note = (f"moved block (aggregate): {sum(cd.values())} content tokens rearranged across "
                          f"{len(pool)} hunk(s); nothing added or removed")
    return remaining


# --------------------------------------------------------------------------- #
# Preamble checks                                                             #
# --------------------------------------------------------------------------- #

DEF_PATTERNS = [
    re.compile(r"\\(?:re)?newcommand\*?\s*\{?\\([A-Za-z@]+)\}?"),
    re.compile(r"\\providecommand\*?\s*\{?\\([A-Za-z@]+)\}?"),
    re.compile(r"\\def\s*\\([A-Za-z@]+)"),
    re.compile(r"\\DeclareMathOperator\*?\s*\{\\([A-Za-z@]+)\}"),
    re.compile(r"\\DeclareRobustCommand\*?\s*\{?\\([A-Za-z@]+)\}?"),
    re.compile(r"\\NewDocumentCommand\s*\{?\\([A-Za-z@]+)\}?"),
    re.compile(r"\\newif\s*\\(if[A-Za-z@]+)"),
    re.compile(r"\\let\s*\\([A-Za-z@]+)"),
]
ENVDEF_PATTERNS = [
    re.compile(r"\\newtheorem\*?\s*\{([A-Za-z*]+)\}"),
    re.compile(r"\\(?:re)?newenvironment\*?\s*\{([A-Za-z*]+)\}"),
    re.compile(r"\\newfloat\s*\{([A-Za-z]+)\}"),
    re.compile(r"\\NewDocumentEnvironment\s*\{([A-Za-z*]+)\}"),
]
COLTYPE_PATTERN = re.compile(r"\\newcolumntype\s*\{([A-Za-z])\}")
PKG_PATTERN = re.compile(r"\\(?:usepackage|RequirePackage)\s*(\[[^\]]*\])?\s*\{([^}]*)\}")


def preamble_defs(pre: str) -> tuple[set[str], set[str], set[str]]:
    macros = {m for pat in DEF_PATTERNS for m in pat.findall(pre)}
    envs = {e for pat in ENVDEF_PATTERNS for e in pat.findall(pre)}
    cols = set(COLTYPE_PATTERN.findall(pre))
    return macros, envs, cols


DEF_HEAD_RE = re.compile(r"\\(newcommand|renewcommand|providecommand|DeclareRobustCommand|DeclareMathOperator|newtheorem|newcolumntype|newenvironment|renewenvironment|def)\*?")


def macro_bodies(pre: str) -> dict[str, str]:
    """name -> normalised definition text for every macro/theorem/column type defined in a preamble."""
    out: dict[str, str] = {}
    for m in DEF_HEAD_RE.finditer(pre):
        kind = m.group(1)
        i = m.end()
        if kind == "def":
            mm = re.match(r"\s*\\([A-Za-z@]+)([^{]*)", pre[i:])
            if not mm:
                continue
            name, params = "\\" + mm.group(1), mm.group(2)
            i += mm.end()
            g = find_matching_brace(pre, i) if i < len(pre) and pre[i] == "{" else -1
            if g < 0:
                continue
            out[name] = re.sub(r"\s+", " ", params.strip() + " " + pre[i + 1:g]).strip()
            continue
        # {\name} or \name  (newtheorem/newcolumntype/newenvironment take a plain {name})
        mm = re.match(r"\s*(\{\s*\\?([A-Za-z@*]+)\s*\}|\\([A-Za-z@]+))", pre[i:])
        if not mm:
            continue
        name = mm.group(2) or mm.group(3)
        if kind in ("newcommand", "renewcommand", "providecommand", "DeclareRobustCommand", "DeclareMathOperator"):
            name = "\\" + name
        i += mm.end()
        parts = []
        # optional arguments [..][..] then one or two mandatory bodies
        while True:
            j = i
            while j < len(pre) and pre[j] in " \t\n":
                j += 1
            if j < len(pre) and pre[j] == "[":
                k = pre.find("]", j)
                if k < 0:
                    break
                parts.append(pre[j:k + 1])
                i = k + 1
                continue
            if j < len(pre) and pre[j] == "{":
                g = find_matching_brace(pre, j)
                if g < 0:
                    break
                parts.append(pre[j:g + 1])
                i = g + 1
                if kind in ("newenvironment", "renewenvironment") and sum(1 for x in parts if x.startswith("{")) < 2:
                    continue
                break
            break
        out[f"{kind}:{name}"] = re.sub(r"\s+", " ", "".join(parts)).strip()
    return out


def preamble_packages(pre: str) -> set[str]:
    out = set()
    for _opt, names in PKG_PATTERN.findall(pre):
        for nm in names.split(","):
            if nm.strip():
                out.add(nm.strip())
    return out


def preamble_inputs(pre: str, root: Path) -> list[Path]:
    files = []
    for m in re.finditer(r"\\(?:input|include)\s*\{([^}]*)\}", pre):
        name = m.group(1).strip()
        for c in ([name] if name.endswith(".tex") else [name + ".tex", name]):
            p = safe_resolve(root, c)
            if p is not None and p.is_file():
                files.append(p)
                break
    return files


def normalise_title(s: str) -> str:
    s = re.sub(r"\\\\(\[[^\]]*\])?", " ", s)          # line breaks in title
    s = re.sub(r"\\(?:thanks|footnote)\s*\{[^{}]*\}", "", s)
    s = re.sub(r"[~\s]+", " ", s)
    return s.strip()


def author_words(pre: str) -> tuple[set[str], set[str]]:
    """Words and e-mails from \\author{} and \\affiliations{} (AAAI) blocks."""
    text = ""
    for macro in ("author", "affiliations", "affiliation", "institute"):
        arg = extract_macro_arg(pre, macro)
        if arg:
            text += " " + arg
    # remove macro names but keep their arguments' text
    text = re.sub(r"\\[A-Za-z@]+\*?", " ", text)
    emails = set(re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text))
    # expand \{a, b\}@dom shorthand into no words (handled via domain match)
    words = {w for w in re.findall(r"[A-Za-z][A-Za-z'\-]{2,}", text)}
    stop = {"and", "the", "rm", "quad", "textsuperscript", "thanks", "And", "AND",
            "Department", "University", "Institute", "School", "College", "USA"}
    words -= stop
    return words, emails


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def locate(root: Path, rel: str, exts: tuple[str, ...]) -> Path | None:
    """Find a referenced asset under root: as given, with extensions, or by basename."""
    cands = [rel] + [rel + e for e in exts]
    for c in cands:
        p = safe_resolve(root, c)
        if p is not None and p.is_file():
            return p
    base = os.path.basename(rel)
    names = {base} | {base + e for e in exts}
    hits = [p for p in root.rglob("*") if p.is_file() and p.name in names and not p.is_symlink()]
    hits = [p for p in hits if ".git" not in p.parts]
    return hits[0] if len(hits) == 1 else None


# --------------------------------------------------------------------------- #
# Main                                                                        #
# --------------------------------------------------------------------------- #

def load_project(main: Path, root: Path, warnings: list[str]) -> tuple[str, str]:
    text = main.read_text(encoding="utf-8", errors="replace")
    text = strip_comments(text)
    text = flatten_inputs(text, root, warnings)
    pre, body = split_document(text)
    return pre, body


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True, help="original main .tex")
    ap.add_argument("--dst", required=True, help="migrated main .tex")
    ap.add_argument("--src-root", help="original project root (default: dir of --src)")
    ap.add_argument("--dst-root", help="migrated project root (default: dir of --dst)")
    ap.add_argument("--json", help="write a JSON report here (refuses to overwrite)")
    ap.add_argument("--force", action="store_true", help="allow --json to overwrite")
    ap.add_argument("--strict-structure", action="store_true", help="structure hunks also fail")
    ap.add_argument("--max-hunks", type=int, default=60, help="hunks to print per class")
    ap.add_argument("--quiet", action="store_true", help="summary only")
    args = ap.parse_args()

    try:
        src = Path(args.src).resolve(strict=True)
        dst = Path(args.dst).resolve(strict=True)
        src_root = Path(args.src_root).resolve(strict=True) if args.src_root else src.parent
        dst_root = Path(args.dst_root).resolve(strict=True) if args.dst_root else dst.parent
        if args.json and Path(args.json).exists() and not args.force:
            print(f"body_diff: refusing to overwrite {args.json} (use --force)", file=sys.stderr)
            return 2

        warnings: list[str] = []
        src_pre, src_body = load_project(src, src_root, warnings)
        dst_pre, dst_body = load_project(dst, dst_root, warnings)
    except (OSError, ValueError) as exc:
        print(f"body_diff: {exc}", file=sys.stderr)
        return 2

    a, sa = tokenize(src_body, src_pre.count("\n"))
    b, sb = tokenize(dst_body, dst_pre.count("\n"))
    hunks = diff_tokens(a, b)
    by_cls = {"format": [], "structure": [], "content": []}
    for h in hunks:
        by_cls[h.cls].append(h)

    report: dict = {"schema": 1, "src": str(src), "dst": str(dst), "warnings": warnings,
                    "tokens": {"src": len(a), "dst": len(b)},
                    "hunks": {k: len(v) for k, v in by_cls.items()},
                    "checks": {}}
    checks = report["checks"]
    problems: list[str] = []
    reviews: list[str] = []

    # ---- format-change statistics ---------------------------------------- #
    fmt_stats: dict[str, int] = {}
    for h in by_cls["format"]:
        for t in h.removed:
            fmt_stats["-" + (t.name or t.key)] = fmt_stats.get("-" + (t.name or t.key), 0) + 1
        for t in h.added:
            fmt_stats["+" + (t.name or t.key)] = fmt_stats.get("+" + (t.name or t.key), 0) + 1
    report["format_changes"] = dict(sorted(fmt_stats.items(), key=lambda kv: -kv[1]))
    report["starred_floats"] = {"src": sa.star_floats, "dst": sb.star_floats}
    report["cite_variants"] = {"src": sa.cite_variants, "dst": sb.cite_variants}
    if sa.cite_variants != sb.cite_variants:
        reviews.append(f"citation command variants changed {sa.cite_variants} -> {sb.cite_variants}: "
                       "parenthetical / textual / year-only forms must map to their equivalents (see make_rules notes)")

    # ---- title ------------------------------------------------------------ #
    st, dt = extract_macro_arg(src_pre, "title"), extract_macro_arg(dst_pre, "title")
    title_ok = st is not None and dt is not None and normalise_title(st) == normalise_title(dt)
    checks["title"] = {"ok": title_ok, "src": st, "dst": dt}
    if not title_ok:
        problems.append("title differs or missing")

    # ---- authors ---------------------------------------------------------- #
    words, emails = author_words(src_pre)
    dst_flat = re.sub(r"\\[A-Za-z@]+\*?", " ", dst_pre)
    missing_words = sorted(w for w in words if w not in dst_flat)
    missing_emails = sorted(e for e in emails if e not in dst_flat)
    checks["authors"] = {"src_words": len(words), "missing_words": missing_words,
                         "src_emails": len(emails), "missing_emails": missing_emails}
    if missing_words or missing_emails:
        reviews.append(f"author/affiliation text not found in migrated preamble: "
                       f"{missing_words + missing_emails}")

    # ---- macro definitions ------------------------------------------------ #
    sm, se, sc = preamble_defs(src_pre)
    dm, de, dc = preamble_defs(dst_pre)
    input_defs: dict[str, set[str]] = {}
    for f in preamble_inputs(dst_pre, dst_root):
        im, ie, _ = preamble_defs(strip_comments(f.read_text(encoding="utf-8", errors="replace")))
        input_defs[f.name] = im | ie
        dm |= im
        de |= ie
    missing_macros = sorted((sm - dm) & sb.used_cs)
    missing_envs = sorted((se - de) & sb.used_envs)
    collisions = {f: sorted(defs & (preamble_defs(dst_pre)[0] | preamble_defs(dst_pre)[1]))
                  for f, defs in input_defs.items()}
    collisions = {f: c for f, c in collisions.items() if c}
    # definitions must be identical too: a changed \\newcommand body changes the rendered paper
    src_bodies, dst_bodies = macro_bodies(src_pre), macro_bodies(dst_pre)
    changed_defs, layout_defs = [], []
    for key, body in src_bodies.items():
        if key in dst_bodies and dst_bodies[key] != body:
            kind, _, name = key.partition(":")
            plain = name.lstrip("\\")
            if kind == "newcolumntype" or plain in FORMAT_TARGETS:
                layout_defs.append(f"{name}: {body[:40]!r} -> {dst_bodies[key][:40]!r}")
            else:
                changed_defs.append(f"{name}: {body[:60]!r} -> {dst_bodies[key][:60]!r}")
    checks["macros"] = {"src_defined": len(sm | se), "missing_used_in_dst": missing_macros + missing_envs,
                        "collisions_with_inputs": collisions,
                        "columntypes_missing": sorted(sc - dc),
                        "definitions_changed": changed_defs, "layout_definitions_changed": layout_defs}
    if changed_defs:
        problems.append(f"macro definitions changed (rendered text would differ): {changed_defs}")
    if layout_defs:
        reviews.append(f"layout-only definitions changed: {layout_defs}")
    if missing_macros or missing_envs:
        problems.append(f"macros/environments used by migrated body but no longer defined: "
                        f"{missing_macros + missing_envs}")
    if collisions:
        reviews.append(f"\\newcommand collisions between migrated preamble and input files: {collisions}")
    if sc - dc:
        reviews.append(f"\\newcolumntype letters dropped: {sorted(sc - dc)}")

    # ---- packages --------------------------------------------------------- #
    sp, dp = preamble_packages(src_pre), preamble_packages(dst_pre)
    checks["packages"] = {"dropped": sorted(sp - dp), "added": sorted(dp - sp)}

    # ---- citation keys / labels / refs / headings ------------------------ #
    ck_s, ck_d = set(sa.cite_keys), set(sb.cite_keys)
    lb_s, lb_d = set(sa.labels), set(sb.labels)
    rf_s, rf_d = set(sa.refs), set(sb.refs)
    checks["citations"] = {"src": len(ck_s), "dst": len(ck_d),
                           "missing": sorted(ck_s - ck_d), "added": sorted(ck_d - ck_s),
                           "src_uses": len(sa.cite_keys), "dst_uses": len(sb.cite_keys)}
    checks["labels"] = {"src": len(lb_s), "dst": len(lb_d),
                        "missing": sorted(lb_s - lb_d), "added": sorted(lb_d - lb_s)}
    checks["refs"] = {"src": len(rf_s), "dst": len(rf_d),
                      "missing": sorted(rf_s - rf_d), "added": sorted(rf_d - rf_s),
                      "dangling_in_dst": sorted(rf_d - lb_d)}
    heads_ok = sa.headings == sb.headings
    checks["headings"] = {"ok": heads_ok, "src": len(sa.headings), "dst": len(sb.headings)}
    if not heads_ok:
        sm_h = difflib.SequenceMatcher(None, sa.headings, sb.headings, autojunk=False)
        deltas = []
        for tag, i1, i2, j1, j2 in sm_h.get_opcodes():
            if tag != "equal":
                deltas.append({"op": tag, "src": sa.headings[i1:i2], "dst": sb.headings[j1:j2]})
        checks["headings"]["deltas"] = deltas
        problems.append("section heading sequence differs")
    for key in ("citations", "labels", "refs"):
        if checks[key]["missing"] or checks[key]["added"]:
            problems.append(f"{key} set changed: -{checks[key]['missing']} +{checks[key]['added']}")
    if checks["refs"]["dangling_in_dst"]:
        reviews.append(f"refs without labels in migrated body: {checks['refs']['dangling_in_dst']}")
    if len(sa.cite_keys) != len(sb.cite_keys):
        problems.append(f"number of citation uses changed: {len(sa.cite_keys)} -> {len(sb.cite_keys)}")

    # ---- bibliography files ---------------------------------------------- #
    bib_rows = []
    for name in sa.bibs:
        ps = locate(src_root, name, (".bib",))
        pd = locate(dst_root, name, (".bib",))
        row = {"name": name, "src": str(ps) if ps else None, "dst": str(pd) if pd else None}
        if ps and pd:
            row["equal"] = sha256_file(ps) == sha256_file(pd)
        bib_rows.append(row)
        if not pd:
            problems.append(f"bibliography file {name}.bib missing in migrated project")
        elif ps and not row.get("equal"):
            problems.append(f"bibliography file {name}.bib content changed")
    checks["bib_files"] = bib_rows
    if sa.bibs != sb.bibs:
        problems.append(f"\\bibliography arguments differ: {sa.bibs} -> {sb.bibs}")

    # ---- figure files ----------------------------------------------------- #
    fig_rows = []
    for rel_s, rel_d in zip(sa.graphics, sb.graphics):
        ps = locate(src_root, rel_s, GRAPHIC_EXTS)
        pd = locate(dst_root, rel_d, GRAPHIC_EXTS)
        row = {"src_ref": rel_s, "dst_ref": rel_d, "src_found": bool(ps), "dst_found": bool(pd)}
        if ps and pd:
            row["equal"] = sha256_file(ps) == sha256_file(pd)
            if not row["equal"]:
                problems.append(f"figure file changed: {rel_d}")
        elif not pd:
            problems.append(f"figure file not found in migrated project: {rel_d}")
        elif not ps:
            reviews.append(f"figure file not found in source project (pre-existing path issue?): {rel_s}")
        fig_rows.append(row)
    if len(sa.graphics) != len(sb.graphics):
        problems.append(f"number of \\includegraphics changed: {len(sa.graphics)} -> {len(sb.graphics)}")
    checks["figure_files"] = fig_rows

    # ---- verdict ---------------------------------------------------------- #
    if by_cls["content"]:
        problems.insert(0, f"{len(by_cls['content'])} content hunk(s) in the document body")
    content_fail = bool(by_cls["content"]) or bool(problems)
    struct_fail = args.strict_structure and bool(by_cls["structure"])
    passed = not (content_fail or struct_fail)
    report["problems"] = problems
    report["reviews"] = reviews
    report["passed"] = passed
    report["hunk_details"] = {k: [h.to_json() for h in v] for k, v in by_cls.items()}

    # ---- print ------------------------------------------------------------ #
    P = print
    P(f"body_diff  src={src}  dst={dst}")
    P(f"tokens     src={len(a)}  dst={len(b)}")
    P(f"hunks      format={len(by_cls['format'])} (ok)  structure={len(by_cls['structure'])} (review)  "
      f"content={len(by_cls['content'])} (FAIL if >0)")
    moves = [h.note.split(":")[0] for h in by_cls["structure"] if h.note]
    if moves:
        P("moves      " + ", ".join(f"{k} x{moves.count(k)}" for k in sorted(set(moves))) +
          f"; other structure hunks {len(by_cls['structure']) - len(moves)}")
    if report["format_changes"]:
        top = ", ".join(f"{k}×{v}" for k, v in list(report["format_changes"].items())[:14])
        P(f"format     {top}")
    if sa.star_floats or sb.star_floats:
        P(f"floats*    src={sa.star_floats}  dst={sb.star_floats}")
    if sa.cite_variants != sb.cite_variants:
        P(f"cite cmds  src={sa.cite_variants}  dst={sb.cite_variants}")
    P(f"title      {'ok' if title_ok else 'DIFFERS'}")
    P(f"authors    {len(words)} words / {len(emails)} emails from source; missing in dst: "
      f"{missing_words + missing_emails or 'none'}")
    P(f"macros     missing-but-used: {missing_macros + missing_envs or 'none'}; "
      f"collisions: {collisions or 'none'}; definitions changed: {changed_defs or 'none'}")
    P(f"packages   dropped={sorted(sp - dp) or 'none'}  added={sorted(dp - sp) or 'none'}")
    P(f"citations  keys {len(ck_s)}->{len(ck_d)}  uses {len(sa.cite_keys)}->{len(sb.cite_keys)}  "
      f"missing={sorted(ck_s - ck_d) or 'none'} added={sorted(ck_d - ck_s) or 'none'}")
    P(f"labels     {len(lb_s)}->{len(lb_d)}  refs {len(rf_s)}->{len(rf_d)}  "
      f"dangling={checks['refs']['dangling_in_dst'] or 'none'}")
    P(f"headings   {len(sa.headings)}->{len(sb.headings)}  {'identical' if heads_ok else 'DIFFER'}")
    P(f"bib files  {[(r['name'], r.get('equal')) for r in bib_rows]}")
    P(f"figures    {len(fig_rows)} referenced; "
      f"{sum(1 for r in fig_rows if r.get('equal'))} hash-equal, "
      f"{sum(1 for r in fig_rows if not r['dst_found'])} missing in dst, "
      f"{sum(1 for r in fig_rows if not r['src_found'])} unresolved in src")
    for w in warnings:
        P(f"warning    {w}")

    def show(cls: str, label: str) -> None:
        hs = by_cls[cls]
        if not hs or args.quiet:
            return
        P(f"\n== {label} ({len(hs)}) ==")
        for n, h in enumerate(hs[:args.max_hunks], 1):
            rem = " ".join(t.raw for t in h.removed)[:300]
            add = " ".join(t.raw for t in h.added)[:300]
            P(f"[{cls[0].upper()}{n}] src L{h.src_line} / dst L{h.dst_line}   ...{h.context_before[-60:]} | {h.context_after[:60]}..."
              + (f"\n   note: {h.note}" if h.note else ""))
            if rem:
                P(f"   - {rem}")
            if add:
                P(f"   + {add}")
        if len(hs) > args.max_hunks:
            P(f"   ... {len(hs) - args.max_hunks} more (see --json)")

    show("content", "CONTENT differences")
    show("structure", "STRUCTURE differences")
    if not args.quiet and by_cls["format"]:
        P(f"\n== FORMAT differences ({len(by_cls['format'])}) == (whitelisted; first 12)")
        for n, h in enumerate(by_cls["format"][:12], 1):
            rem = " ".join(t.raw for t in h.removed)[:120]
            add = " ".join(t.raw for t in h.added)[:120]
            P(f"[F{n}] src L{h.src_line} / dst L{h.dst_line}  -{rem!s}  +{add!s}")
    if problems:
        P("\nPROBLEMS:")
        for p in problems:
            P(f"  * {p}")
    if reviews:
        P("\nREVIEW:")
        for r in reviews:
            P(f"  * {r}")
    P(f"\nRESULT: {'PASS' if passed else 'FAIL'}")

    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
