# AAAI-27 -> ICLR 2027: mapping table (deterministic part)

Every row is a "formatting or LaTeX-compatibility change required by the target template".
All of them are inside `body_diff.py`'s whitelist; after the migration body_diff must PASS.
Body rows are executed by `references/aaai2027-to-iclr2027.json` via `migrate_tex.py`; the
preamble is written by the agent from the first table; sizes by `layout_figures.py`.

## 1. Preamble

| AAAI-27 source | ICLR 2027 target | Note |
|---|---|---|
| `\documentclass[letterpaper]{article}` | `\documentclass{article}` | the .sty sets paper size |
| `\usepackage[submission]{aaai2027}` | `\usepackage{iclr2027_conference,times}` | anonymity via commented `\iclrfinalcopy` |
| `\usepackage[hyphens]{url}` `\urlstyle{rm}` `\def\UrlFont{\rm}` | `\usepackage{hyperref}` `\usepackage{url}` | AAAI forbids hyperref; ICLR's sample loads it |
| `\usepackage{natbib}` | drop (the .sty does `\RequirePackage{natbib}`) | loading twice without options also works |
| `\usepackage{caption}` | keep | needed for `\DeclareCaptionStyle` etc. |
| `\frenchspacing` | drop | AAAI-specific |
| `\pdfinfo{/TemplateVersion (2027.1)}` | drop | pdfTeX primitive, AAAI-specific |
| `\setcounter{secnumdepth}{0}` | drop | ICLR numbers sections by default; both are compliant, follow the template |
| - | `\usepackage[T1]{fontenc}` | `times` + OT1 renders text-mode `>` `<` `\|` as `¿` `¡` `-`; the AAAI kit's newtx fonts did not. Add it whenever the body has such characters in text mode |
| other packages (amsmath, booktabs, subcaption, algorithm, tcolorbox, microtype, ...) | keep verbatim, same order | move `xcolor` above the first `\definecolor` if AAAI's .sty used to load it implicitly |
| `\newtheorem` / `\newcolumntype` / `\definecolor` / `\newcommand` | keep verbatim | body_diff reports any definition the body still uses but lost |
| `\title{...}` | keep verbatim | body_diff compares it |
| `\author{A\textsuperscript{\rm 1}...}` + `\affiliations{...}` | `\author{A\textsuperscript{\rm 1} ... \\ affil \\ \texttt{email}}` | ICLR has no `\affiliations`, `\corresponding`, `\equalcontrib`. In aaai2027.sty `\corresponding` = `\footnote{Corresponding author.}` and `\equalcontrib` = `\footnote{These authors contributed equally.}`; use `\thanks{}` with the same words. All names, affiliations and e-mails must survive (body_diff checks presence) |
| - | `%\iclrfinalcopy` stays commented | submission is anonymous |

Do not `\input{math_commands.tex}`: it defines many one-letter macros (`\R`, `\E`, `\vx`, ...) that collide with paper macros, and the paper does not need it.

## 2. Body (executed by the JSON rules)

| Source | Target | Reason |
|---|---|---|
| `\begin{figure*}` / `\end{figure*}` | `figure` | no spanning floats in one column |
| `\begin{table*}` / `\end{table*}` | `table` | same |
| `\cite` / `\shortcite` | `\citep` | AAAI's `\cite` already renders author-year; ICLR uses natbib names |
| `\newpage` / `\clearpage` lines | delete | AAAI forbids them anyway; ICLR's appendix follows the references naturally |
| `\bibliography{ref}` | `\bibliography{ref}` + `\bibliographystyle{iclr2027_conference}` | AAAI's .sty sets the bst, ICLR needs it explicitly |
| before `\bibliography` | `% TODO(ICLR 2027, REQUIRED): \subsection*{AI use statement}` comments | required section; the authors write it |
| `\includegraphics{<ProjectName>/figures/x.pdf}` | `figures/x.pdf` via `--replace` | Overleaf folder residue; a pre-existing path issue, reported as such |

## 3. Sizes (executed by layout_figures.py; illustrative values)

AAAI: text 7.0 in, column 3.3125 in (two columns). ICLR: text 5.5 in (one column).

| Source expression | Physical width | ICLR width |
|---|---|---|
| `figure`, `0.9\linewidth` | 2.98 in | `0.54\linewidth` |
| `figure*`, `0.9\textwidth` | 6.30 in | `1\linewidth` (capped) |
| `figure`, `0.4\textwidth` | 2.80 in | `0.51\linewidth` -> candidate for `--pair` with a neighbour |
| `subfigure{0.48\columnwidth}` | 1.59 in each | container `0.29\linewidth` each; consider `--min-frac` or leave |
| wide `table*` with `\resizebox{\textwidth}{!}` | scaled to 7.0 in | **keep**: now scales to 5.5 in |
| wide `table*` without `\resizebox` that overflows | - | add `\resizebox{\linewidth}{!}{...}` or reduce `\tabcolsep`; report the scale, suggest `sidewaystable` below ~0.7 |

## 4. Rule check after compiling

- Main text <= 9 pages at submission (references, appendix and the three statements do not count). Read the page where `References` starts from the PDF; never hide figures with page breaks.
- AI use statement is required: missing -> report + TODO comment. Existing appendix paragraphs about AI assistance, ethics or reproducibility may be *material* for the authors; moving them is a content relocation and needs approval.
- Anonymity: the template hides the author block; scan the body for acknowledgements, "our prior work", code links, institution names and list them.
- AAAI's `checklist.tex`: not needed at ICLR; do not package it, say so.
- Figures: all graphic files byte-identical (body_diff hashes).
