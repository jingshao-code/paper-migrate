# paper-migrate

Move a LaTeX paper from one AI-conference template to another — AAAI → ICLR, NeurIPS → ICML,
ACL → NAACL, any venue whose official template you have — **changing only the format, never a
word of the paper**. The result is an Overleaf-ready zip and a one-page report that tells you
what only you can decide (page limit, required statements, anything to eyeball).

Works with any venue. Four are pre-registered with their rules (ICLR 2027, NeurIPS 2026, ACL/ARR,
AAAI-27); for any other, point the skill at the official template package and it reads the rules
from the package first, asks you to confirm what it could not read, then converts.

## Use

Install the skill (a folder with a `SKILL.md`; works with Claude Code and other agents that read
that format):

```bash
git clone https://github.com/jingshao-code/paper-migrate ~/.claude/skills/paper-migrate
```

Download the **official** template of the target venue (its author page; not the Overleaf
gallery), then tell the agent:

> Use paper-migrate. Source: `~/papers/MyPaper_AAAI27/` (main.tex). Target: ICLR 2027,
> submission, official template at `~/Downloads/iclr2027/`. Output to `~/papers/MyPaper_ICLR27/`.

Requirements: Python 3.10+, nothing else for the conversion itself. Optional: `tectonic` or TeX Live
for a local test compile and poppler (`pdfinfo`, `pdftotext`) for page facts; without them the
zip is still produced and Overleaf compiles it.

## What happens

1. **Read the target first** — geometry, style line, anonymisation switch, caption rule, page limit,
   required sections, all quoted from the official package (`venues.yaml` records them with sources).
2. **Verify** the template files against the recorded SHA-256.
3. **Convert mechanically** — document class, style line, author block, citation commands,
   bibliography style; figure and table widths recomputed from the two venues' geometry so a
   two-column figure keeps its physical size; small floats paired with (a)/(b) sub-captions or
   wrapped with text when the paragraph is long enough; caption position per the venue rule.
4. **Prove nothing changed** — `body_diff` compares every word, number, formula, citation key,
   label, heading and file hash; any content difference fails the run.
5. **Compile** in a sandbox (shell-escape off) and **re-check** the result against the target's
   rules (style file, anonymity state, forbidden packages, captions, paper size, ...).
6. **Deliver** the zip and the report.

## Where to look

In the output folder:

| File | What it is |
|---|---|
| `MIGRATION_REPORT.md` | **Start here.** Author to-do on top, evidence below. |
| `<paper>_<venue>.zip` | Upload at overleaf.com → New Project → Upload Project; compile with pdfLaTeX. |
| `main.tex`, `figures/`, `.bib`, style files | The migrated project (same files as the zip). |
| `main.pdf` | Local test compile, if a TeX engine was available. |
| `body_diff.json`, `compliance.json`, `layout.json`, `compile.json` | Machine-readable evidence behind the report. |

## What to check in the report

- **Author to-do** — the tool never does these for you:
  - *Page limit*: a format-only migration lands wherever it lands ("main text ends on page 10,
    limit 9"). Shortening or extending is your decision.
  - *Required sections* the target has and the source lacks (ICLR's AI use statement, NeurIPS's
    checklist, ACL's Limitations): a `% TODO(<venue>, REQUIRED)` comment marks the place; you write it.
  - *Layout to eyeball*: which floats were paired, wrapped, enlarged or scaled, all reversible.
  - *Floats vs. first mention*: floats that print before the paragraph that first cites them
    (usually LaTeX's top-of-page placement of a block written before the paragraph).
  - *Identity hints* in the body (institutions, URLs, acknowledgements) for anonymous submission.
- **Content invariance** must read `RESULT: PASS` with `content 0`. Structure hunks are the
  pairings and wraps, listed one by one.
- **Rule check**: every rule marked `pass`, `author action`, `info` or `n/a`. A `FAIL` means the
  migration itself is not finished.
- Then open the PDF on Overleaf and read it once; the report tells you which pages changed layout.

## Adding a venue

`python3 scripts/profile_template.py --dir <official package> --venue-id icml2027 --yaml` prints a
`venues.yaml` entry with everything the package states and `TODO_CONFIRM` for the rest. Fill the
TODOs from the venue's author page (quote the sentence, add `verified_on`), and the venue is
available in both directions. `references/venue-checklist.md` says where each field comes from.
Bundle the official zip under `assets/templates/` only if its publisher allows redistribution.

<details>
<summary>Manual use of the scripts</summary>

```bash
S=scripts; SRC=orig/main.tex; NEW=new; REP=rep
python3 $S/profile_template.py  --dir template/ --venue-id iclr2027 --json $REP/profile.json
python3 $S/verify_template.py   --venue iclr2027 --dir template/ --json $REP/verify.json
python3 $S/make_rules.py        --src-venue aaai2027 --dst-venue iclr2027 --out $REP/rules.json
python3 $S/draft_preamble.py    --src $SRC --src-venue aaai2027 --dst-venue iclr2027 --out $REP/preamble.tex
python3 $S/migrate_tex.py       --in $SRC --out $NEW/main.tex --rules $REP/rules.json --preamble $REP/preamble.tex
python3 $S/layout_figures.py    --src $SRC --in $NEW/main.tex --out $NEW/main.tex --force \
        --src-venue aaai2027 --dst-venue iclr2027 --table-captions above --placement t \
        [--pair fig:a,fig:b] [--wrap fig:c] [--list-tables] --report $REP/layout.json
python3 $S/body_diff.py         --src $SRC --dst $NEW/main.tex --json $REP/body_diff.json
$S/compile_check.sh $NEW main.tex $REP/build
python3 $S/check_compliance.py  --venue iclr2027 --project $NEW --stage submission --src $SRC \
        --pdf $REP/build/main.pdf --json $REP/compliance.json
python3 $S/make_zip.py          --project $NEW --main main.tex --out paper_iclr2027.zip --venue iclr2027
python3 $S/make_report.py       --paper MyPaper --src-venue aaai2027 --dst-venue iclr2027 \
        --verify $REP/verify.json --rules $REP/rules.json --body-diff $REP/body_diff.json --layout $REP/layout.json \
        --compile $REP/build/compile.json --compliance $REP/compliance.json --deliverable paper_iclr2027.zip \
        --out $NEW/MIGRATION_REPORT.md
python3 tests/run_tests.py
```
</details>

## Guarantees and limits

| Changes (format) | Never changes |
|---|---|
| class, style file, packages, author-block structure | title, abstract, body, captions, footnotes |
| float sizes, placement, pairing, wrapping, caption side | numbers, tables, formulas, algorithms |
| `\cite` ↔ `\citep`, `\bibliographystyle` | citation keys, the `.bib`, figure files (hash-checked) |
| compile errors the migration introduced | pre-existing typos or errors (reported only) |

Safety: the source folder is read-only; nothing is uploaded anywhere; template zips are
hash-checked and unpacked through a path-traversal guard; compilation runs with shell-escape off.

Limits: `body_diff` is a tokenizer with a layout whitelist, not a TeX engine — exotic macros may
surface as content hunks for a human to judge, which is the safe direction. Table widths for
wrapping are estimated; the compile step's overfull count is the check. Rules marked
`TODO_CONFIRM` in `venues.yaml` are treated as "no rule recorded" and said so in the report.

## Acknowledgements

The idea of packaging conference migration as an agent skill was first published by
[ChengxiSHE/paper-conference-migration](https://github.com/ChengxiSHE/paper-conference-migration);
this project shares no code with it and takes a stricter, format-only stance.

## License

MIT, see `LICENSE`.
