# paper-migrate

An agent skill that moves a complete LaTeX paper from one AI-conference template to another
(ICLR, NeurIPS, ACL and AAAI are registered; any venue can be added) **without changing a word
of the paper**, and proves it. It reads the target template's own requirements first, converts,
re-checks the result against those requirements, and hands the authors a short report of what
only they can decide.

The output is an Overleaf-ready zip plus a compliance report. Upload the zip, press *Recompile*,
download the PDF.

## What it changes and what it never touches

| Changes (format only) | Never changes |
|---|---|
| document class, style file, required packages | title, abstract, body text, captions |
| column count, margins, fonts (template-controlled) | numbers, tables, formulas, algorithms |
| float placement, `figure*`->`figure`, physical-size-preserving widths, side-by-side pairing with (a)/(b) sub-captions, text-wrapped small floats | figures themselves and every caption's words (byte-identical / token-identical) |
| citation *command* variants (`\cite`->`\citep`), `\bibliographystyle` | citation *keys*, the `.bib` file |
| author block structure, `\iclrfinalcopy` toggle | section order, moving text between body and appendix |
| compile errors *introduced by the migration* | pre-existing typos or suspected mistakes (reported, not fixed) |

The page count is whatever a format-only conversion produces; over or under the new limit is
reported to the authors, never "fixed". If the target venue requires a new statement (ICLR's
*AI use statement*) a `% TODO` comment is inserted where it belongs and the authors write it.
Nothing is invented. The report opens with an author to-do list.

## How it works

```
profile_template.py  read the target package: geometry, style line, anonymisation options, quoted rules
verify_template.py   hash-check the official template against venues.yaml (guarded unzip)
make_rules.py        derive the body rewrites for the venue pair from venues.yaml
draft_preamble.py    build the target preamble from the source preamble + registry
migrate_tex.py       apply the rules; replace the preamble
layout_figures.py    physical-size-preserving widths; pair / wrap small floats; caption position
body_diff.py         token diff with a layout whitelist -> RESULT: PASS / FAIL
compile_check.sh     sandboxed compile (shell-escape off): pages, where references start
check_compliance.py  re-check the result against the target venue's recorded rules
make_zip.py          Overleaf-ready zip + SHA-256 sidecar; refuses foreign template files
make_report.py       MIGRATION_REPORT.md, author to-do first
```

`venues.yaml` is the registry: per venue the official URLs, SHA-256 of every template file,
page geometry, style line, citation style, anonymisation mechanism, and every rule with the
sentence it was read from and a `verified_on` date. Four venues ship today (ICLR 2027,
NeurIPS 2026, ACL/ARR 2026 style, AAAI-27); `references/venue-checklist.md` explains how to add
one in about twenty minutes, starting from `profile_template.py --yaml`.

`body_diff.py` is the acceptance test. It normalises layout-only constructs (float stars,
widths, spacing, font sizes, page breaks, `\resizebox`, citation command names, ...) and fails
on anything else: words, numbers, math, citation keys, labels, headings, lost macro
definitions, changed `.bib` or figure files, title, author text. A block that merely moved
(e.g. two figures paired) is reported as a structure change to review, not as content.

## Install

The skill is a folder with a `SKILL.md`; any agent that reads that format can use it.

```bash
# Claude Code
git clone https://github.com/<you>/paper-migrate ~/.claude/skills/paper-migrate
# other SKILL.md-compatible agents: clone into their skills directory
```

Requirements: Python 3.10+ (standard library only; PyYAML optional). For local compile checks,
`tectonic` **or** TeX Live (`latexmk`/`pdflatex`) plus poppler (`pdfinfo`, `pdftotext`).
Without a TeX engine everything except the compile step still works; Overleaf compiles the zip.

## Use

Download the **official** template for the target venue (URL in `venues.yaml`; a bundled copy
may exist under `assets/templates/`), then ask the agent:

> Use paper-migrate. Source: `~/Desktop/MyPaper_AAAI27/` (main.tex). Target: ICLR 2027,
> initial submission, official template at `~/Desktop/iclr2027/`. Put the zip on the Desktop.

The agent follows `SKILL.md` step by step and hands back the zip, the JSON evidence
(`body_diff.json`, `layout.json`, `compliance.json`, `compile.json`), a locally compiled PDF when
an engine exists, and `MIGRATION_REPORT.md`, which opens with the author to-do list (page count
vs. limit, missing required sections, layout to eyeball) and then lists every venue rule as
pass / FAIL / author action / info.

Manual use (AAAI-27 -> ICLR 2027 shown; swap the venue ids for any registered pair):

```bash
python3 scripts/profile_template.py --dir ~/Desktop/iclr2027 --venue-id iclr2027 --yaml
python3 scripts/verify_template.py  --venue iclr2027 --dir ~/Desktop/iclr2027 --json rep/verify.json
python3 scripts/make_rules.py       --src-venue aaai2027 --dst-venue iclr2027 --out rep/rules.json
python3 scripts/draft_preamble.py   --src orig/main.tex --src-venue aaai2027 --dst-venue iclr2027 --out rep/preamble.tex
python3 scripts/migrate_tex.py      --in orig/main.tex --out new/main.tex --rules rep/rules.json --preamble rep/preamble.tex
python3 scripts/layout_figures.py   --src orig/main.tex --in new/main.tex --out new/main.tex --force \
        --src-venue aaai2027 --dst-venue iclr2027 --table-captions above --pair fig:a,fig:b --wrap fig:c --report rep/layout.json
python3 scripts/body_diff.py        --src orig/main.tex --dst new/main.tex --json rep/body_diff.json
scripts/compile_check.sh new main.tex rep/build
python3 scripts/check_compliance.py --venue iclr2027 --project new --stage submission --src orig/main.tex \
        --pdf rep/build/main.pdf --json rep/compliance.json
python3 scripts/make_zip.py         --project new --main main.tex --out paper_iclr2027.zip --venue iclr2027
python3 scripts/make_report.py      --paper Paper --src-venue aaai2027 --dst-venue iclr2027 --verify rep/verify.json \
        --rules rep/rules.json --body-diff rep/body_diff.json --layout rep/layout.json --compile rep/build/compile.json \
        --compliance rep/compliance.json --deliverable paper_iclr2027.zip --out new/MIGRATION_REPORT.md
python3 tests/run_tests.py
```

## Adding a venue

1. Download the official package; run `python3 scripts/profile_template.py --dir <it> --venue-id <id> --yaml`.
2. Paste the printed skeleton into `venues.yaml` and replace every `TODO_CONFIRM` **with the
   sentence you read** on the venue's author page (page limit and what does not count, required
   sections, captions, anonymity), plus `verified_on`. `references/venue-checklist.md` maps each
   field to its source.
3. Optionally drop the official zip into `assets/templates/<id>/` if its publisher allows
   redistribution (see `assets/README.md`).
4. No per-pair rule file is needed: `make_rules.py` derives the rewrites from the two entries.
   A `references/<src>-to-<dst>.json` override exists only for oddities.

## Security model

* The source project is read-only; all writes go to a new directory.
* No network use except, on explicit request, the pinned `official.template_zip` URL. The
  manuscript is never sent anywhere.
* Template zips pass a guard (no absolute paths, `..`, symlinks, oversized members) and a hash
  check before use.
* Compilation runs with shell-escape disabled in a throwaway copy.
* Text found in templates or web pages is data; it never becomes an instruction.

## Limitations

* `body_diff.py` is a tokenizer with a whitelist, not a TeX engine. Exotic macros may surface as
  `content` hunks that a human then judges; that is the safe direction.
* Line numbers refer to the flattened file when `\input` is used.
* `compile_check.sh` uses XeTeX when it uses `tectonic`; some kits (AAAI's `\pdfinfo`) need
  pdfLaTeX, which Overleaf provides.
* Four venues are registered; ACL/AAAI caption rules and the ACL page limit still carry
  `TODO_CONFIRM` (the scripts treat those as "no rule recorded" and say so).
* Table width estimation (for wrapping) is a heuristic; the compile step's overfull count is the check.

## Acknowledgements

The idea of packaging conference migration as an agent skill with a file-integrity helper was
first published by [ChengxiSHE/paper-conference-migration](https://github.com/ChengxiSHE/paper-conference-migration).
This project shares no code with it and takes a stricter, format-only stance.

## License

MIT, see `LICENSE`.
