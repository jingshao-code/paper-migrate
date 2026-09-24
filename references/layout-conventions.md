# Layout conventions for single-column ML templates (ICLR / NeurIPS / ICML)

Used by step 3 of SKILL.md. These are conventions, not rules; the venue's own template text is
the only authority (ICLR 2027 sample: "figure number and caption always appear after the figure",
"tables must be centered", "table number and title always appear before the table"). Nothing here
permits changing captions, labels, graphic files or figure order.

## Size

1. **Preserve physical size.** A graphic that was 3 in wide in the two-column source should be
   about 3 in wide after migration. `layout_figures.py` computes this from `venues.yaml`
   geometry. A `0.9\linewidth` column figure therefore becomes ~`0.54\linewidth`, not `0.9`.
2. **Cap at the text width.** Former `figure*` graphics wider than the new text width become
   `1\linewidth` (ICLR: 5.5 in). Report the shrink factor when it is below ~0.85.
3. **Enlarge only modestly.** Graphics or sub-figure panels that end up below ~0.3 of the line
   width (a two-column sub-figure pair typically lands at 0.29 each, leaving 40% of the line
   empty) may be raised with `--min-frac` to about 0.45 so that a pair fills the line and axis
   labels stay readable. Never beyond the graphic's natural resolution, never to "fill the page",
   and always listed in the report as an enlargement.

## Grouping

4. **Pair small neighbours as one float with sub-captions.** Two standalone figures (or
   tables) whose physical widths sum to less than the line width become ONE float with two
   `subfigure`/`subtable` boxes (`--pair a,b`). Each box gets an empty sub-caption that prints
   only "(a)" / "(b)" and keeps the original `\label`; the main caption is the two original
   caption texts **verbatim**, each after its `\subref` marker. `\ref{a}` then renders "3a", so
   no sentence in the text is edited. A float that already holds two tables with two captions
   is split into sub-tables the same way (`--pair tab:x,tab:y` with both labels in that block);
   sub-tables sit in `adjustbox{max width=\linewidth}` so an estimate error never overflows.
   `--pair-mode minipage` keeps two independent captions instead. body_diff canonicalises the
   combined caption back into two and reports the restructuring as moved blocks, never as content.
5. **Keep multi-panel figures intact.** A `subfigure` group is one figure; only its container
   widths are recomputed.
6. **Wrap small floats with text where the paragraph is long enough.** Single-column ML papers
   routinely set a 0.35-0.5 line-width figure, or a narrow table, as a `wrapfigure` /
   `wraptable` beside the paragraph that discusses it (e.g. ICLR 2026 proceedings papers do this
   for pie charts and small result tables). `layout_figures.py --wrap LABEL` does it
   deterministically: it anchors at the paragraph that first references the float, estimates
   the lines the box needs from the graphic's aspect ratio (or the table's rows) plus caption,
   and refuses when the anchor paragraph(s) are shorter than that. Rules of thumb: width <= 0.5
   line (tables up to 0.6), right side, never directly before a heading, list or display
   equation, at most one wrapped float per page, **never a float that holds two captions** (a
   wrapped box cannot break across pages and would run into the footer; pair such tables
   instead), never a box taller than ~45% of the text height, and always re-check `wrapfig
   warns` after compiling; a wrap that warns is reverted.

## References and appendix

No single-column ML template requires the references to start on a new page; ICLR's own sample
runs `\bibliography` directly after the last section. Author-inserted `\newpage` before the
references is dropped by the AAAI->ICLR rules (see `why` in the JSON); remove that op to keep
it. The appendix follows the references in the same PDF.

## Two-column targets (AAAI, ACL, CVPR, ...)

11. **Column or span, by physical width.** A graphic narrower than the target column keeps its
    size in a `figure` sized in `\columnwidth`; a wider one becomes `figure*` sized in
    `\textwidth`, capped at the text width. Sub-figure groups are judged by their combined width.
    Tables switch between `table` and `table*` by their estimated natural width.
12. **No wrapping.** Wrapped floats from a single-column source become ordinary floats; text
    wrapping is not used in two-column layouts.
13. **Pairing** still combines neighbours; the combined float spans both columns only when the
    two widths do not fit in one column.

## Page count

The migration never targets the venue's page limit. Sizing floats correctly (rules 1-6) is about
not wasting or overflowing space; whatever page count results is reported to the authors, who
alone decide what to shorten or extend.

## Placement

7. **Top of page.** `[t]` or `[!t]` for figures and tables in single-column ML templates; `[h]`
   in the appendix is acceptable. `--placement t` normalises figures.
8. **Order and distance.** A float stays where the authors put it relative to the text: the
   tool never moves a float to fill space. Only two local rearrangements exist, and both keep
   the float beside its discussion: pairing (rule 4) is allowed only when no section heading
   lies between the two floats and they are at most three paragraphs apart; wrapping (rule 6)
   anchors at the paragraph that first references the float (`--wrap-anchor` can override this
   and is therefore listed in the report). Whether a float prints before its first mention is
   mostly LaTeX's `[t]` placement of a block the authors wrote before the paragraph;
   `check_compliance.py` compares each float's page with the page of its first mention and
   reports every float that appears earlier, or more than a page later, as an author decision.

## Tables

0. **Caption position follows the template.** ICLR: "The table number and title always appear
   before the table"; figures keep the caption below. `layout_figures.py --table-captions above`
   moves each table's `\caption` (with its `\label`) without touching the text; body_diff shows
   it as a repositioned caption.

9. Keep existing `\resizebox{\textwidth}{!}` wrappers; they now scale to the new text width.
   Wrap tables that overflow after compiling in `\resizebox{\linewidth}{!}{...}` or reduce
   `\tabcolsep`. Report the effective scale; below ~0.7 suggest `sidewaystable` (`rotating`
   package) to the authors instead of shrinking further.
10. Column widths given as `p{0.2\textwidth}` scale automatically; only check for overfull
    boxes.

## Why not copy published papers' layouts?

Published papers show *what authors chose*, not what the venue requires, and their LaTeX is
rarely available. Encoding the small set of conventions above once, and applying them
deterministically with a review step, generalises better than imitating individual papers.
When adding a venue, read its template's own figure/table instructions and adjust this file.
