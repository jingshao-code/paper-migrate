# Adding or re-verifying a venue: what to read, and where each field comes from

Run `scripts/profile_template.py --dir <official package> --venue-id <id> --yaml` first. It fills
what the package itself states and marks the rest `TODO_CONFIRM`. Then read the two official
pages (author guidelines / call for papers) and replace every `TODO_CONFIRM` **with the sentence
you read**, plus `verified_on`. Never fill a field from memory or from an Overleaf gallery copy.

| Field in `venues.yaml` | Where it comes from | Used by |
|---|---|---|
| `official.template_zip`, `template_zip_sha256` | the download link on the venue's author page; `shasum -a 256` of the file you downloaded | verify_template |
| `template.files` | `shasum -a 256 *` in the extracted package (profile prints them) | verify_template, make_zip, check_compliance |
| `template.needed_in_project` | the `.sty`/`.cls`/`.bst` the sample `.tex` actually loads | make_zip, check_compliance |
| `layout.columns/text_width_in/column_width_in/text_height_in` | `\textwidth`, `\columnsep`, `\twocolumn` or `geometry{...}` in the `.sty`; A4 = 8.27 x 11.69 in, letter = 8.5 x 11 in | layout_figures, make_rules |
| `template.documentclass`, `style_line`, `style_line_by_stage` | the sample `.tex` header and its commented alternatives (`[final]`, `[review]`, `[submission]`) | draft_preamble, check_compliance |
| `template.packages_after_style`, `preamble_extras` | packages the sample loads right after the style (hyperref, url, fontenc...) -- only what the venue needs, not the whole sample | draft_preamble |
| `template.citation_style`, `natbib_loaded_by_style` | `\RequirePackage{natbib}` + `\setcitestyle`/`\bibpunct` in the `.sty`; cite commands in the sample | make_rules, draft_preamble |
| `template.bibliographystyle` / `bibliographystyle_default` | `\bibliographystyle{...}` in the sample; "set by the style file" when the `.sty` sets it; a default when the venue ships no `.bst` | make_rules, check_compliance |
| `template.author_block` | the `\author{}` shape in the sample | draft_preamble (comment), agent |
| `template.venue_macros`, `venue_macro_map`, `mandatory_preamble` | macros only this style defines (`\affiliations`, `\corresponding`, `\iclrfinalcopy`...) and lines its kit marks "do not change" | draft_preamble |
| `template.anonymization.<stage>` | `\DeclareOption{final/review/submission}` semantics in the `.sty`; write `must_match` / `must_not_match` regexes on the comment-stripped `.tex` and the `line` to emit | draft_preamble, check_compliance |
| `rules.page_limit` | the CFP / handbook sentence; record what does **not** count (references, appendix, checklist, statements) | make_rules (notes), check_compliance |
| `rules.required_sections[]` | CFP + sample text: name, required/recommended, a regex `pattern` that detects it, `placement` (`before_bibliography` or `end_of_document`) | make_rules (TODO comments), check_compliance |
| `rules.captions` | the sample's own sentence ("caption always appears after the figure", "title appears before the table") | layout_figures, check_compliance |
| `rules.paper_size`, `fonts`, `forbidden_packages/commands` | `.sty` (`letterpaper`/`a4paper`), the kit's DISALLOWED lists, the CFP font sentence | check_compliance |
| `rules.anonymity`, `references_page` | CFP sentences; the sample's own structure | check_compliance (scan + info) |

Rules of thumb:

* A venue whose year changes gets a **new** entry (`iclr2028`), never an edit of the old one.
* When the style file hash changes but the year does not (a kit revision), update `template.files` and `verified_on`, and note the revision in `official`.
* `TODO_CONFIRM` is allowed to ship; the scripts treat it as "no rule recorded" and say so in the report. It is not allowed to be silently guessed.
* Bundle the official package under `assets/templates/<id>/` only if its publisher allows redistribution; otherwise record the URL or repository commit.
