# paper-migrate

Move a LaTeX paper from one AI-conference template to another — AAAI → ICLR, NeurIPS → ICML,
ACL → NAACL, any venue whose official template you have — **changing only the format, never a
word of the paper**, and checking that claim as far as source and PDF can be checked. The result is
an Overleaf-ready zip and a one-page report that tells you what only you can decide (page limit,
required statements, anything to eyeball).

Works with any venue. Four are pre-registered with their rules (ICLR 2027, NeurIPS 2026, ACL/ARR,
AAAI-27); for any other (ICML, NAACL, CVPR, ...), point the skill at the official template package
and it reads the rules from the package first, asks you to confirm what it could not read, then converts.

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
4. **Check that nothing changed** — `body_diff` compares every word, number, formula, citation
   key, label, heading, macro definition and file hash in the LaTeX sources; any content
   difference fails the run. It reads tokens, not pixels, which is why the next two steps exist.
5. **Compile** with shell-escape and rc files off, **re-check** the result against the target's
   rules (style file, anonymity state, forbidden packages, captions, paper size, ...), compile
   the delivered zip on its own, render every page, and compare the rendered words with your
   source PDF when you have one.
6. **Deliver** the zip, the page images and the report.

## Where to look

In the output folder:

| File | What it is |
|---|---|
| `MIGRATION_REPORT.md` | **Start here.** Author to-do on top, evidence below. It is inside the zip too, so it shows in the Overleaf file tree; `main.tex` itself only carries `% TODO(...)` marks where a required section must be written. |
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
  pairings and wraps, listed one by one. `REVIEW` lines (citation command variants, layout
  definitions) deserve a look.
- **PDF check**: the `pages/` folder holds every page as an image; with a source PDF the report
  lists words and numbers that appear in only one rendering. A differing number is a stop.
- **Rule check**: every rule marked `pass`, `author action`, `info` or `n/a`. A `FAIL` means the
  migration itself is not finished.
- Then open the PDF on Overleaf and read it once; the report tells you which pages changed layout.

## Templates

`assets/templates/` holds unmodified copies of **official** conference packages (currently ICLR 2027
and NeurIPS 2026), each with its download URL, date and SHA-256 recorded in `venues.yaml`. The
folder is updated as venues publish new packages, and contributions are welcome: open a pull
request with the latest official package and its `venues.yaml` entry (the skill prints a draft
entry with `python3 scripts/profile_template.py --dir <package> --yaml`; `references/venue-checklist.md`
says where each field comes from). Only packages whose publisher allows redistribution are stored
here; for the others (e.g. AAAI's author kit) point the skill at your own download.

You do not have to wait for a venue to be added: give the skill the official template package and
it reads the rules from the package, asks you to confirm anything it could not read, and converts.
Step-by-step commands for running the scripts by hand are in `SKILL.md`.

## Guarantees and limits

| Changes (format) | Never changes |
|---|---|
| class, style file, packages, author-block structure | title, abstract, body, captions, footnotes |
| float sizes, placement, opt-in pairing/wrapping, caption side | numbers, tables, formulas, algorithms, macro definitions |
| citation command spelling when the meaning is kept, `\bibliographystyle` | citation keys, the `.bib`, the parenthetical/textual/year form of a citation, figure files (hash-checked) |
| compile errors the migration introduced | pre-existing typos or errors (reported only) |

Safety: the source folder is read-only; the scripts make no network calls except, on request,
the pinned official template URL (a hosted AI agent running the skill still reads your files as
part of its normal operation); template zips are hash-checked and unpacked through a
path-traversal guard; compilation runs with shell-escape and rc files off under a CPU limit.
That is a hardened build, not a security sandbox: compile templates you do not trust on Overleaf.

Limits: `body_diff` is a tokenizer with a layout whitelist, not a TeX engine — exotic macros may
surface as content hunks for a human to judge, which is the safe direction; what the target style
file itself does to a token is only visible in the PDF, hence the page images and the rendered-word
comparison. Table widths for
wrapping are estimated; the compile step's overfull count is the check. Rules marked
`TODO_CONFIRM` in `venues.yaml` are treated as "no rule recorded" and said so in the report.


## License

MIT, see `LICENSE`.
