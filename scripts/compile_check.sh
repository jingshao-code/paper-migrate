#!/usr/bin/env bash
# compile_check.sh -- compile a project (or a delivered .zip) in a throwaway copy and
# report page facts the venue rules care about.
#
#   compile_check.sh <project_dir | project.zip> <main.tex> [out_dir]
#
# Engine: tectonic (--untrusted) if present, else latexmk -norc / pdflatex with
# shell_escape=f and openout_any=p.  A CPU limit (ulimit -t 1500) applies.  Nothing is written
# into the source.  PAPER_MIGRATE_OFFLINE=1 makes tectonic use only cached packages.
#
# This is a hardened build, NOT a security sandbox: TeX still reads the project files and
# tectonic may download packages on first use.  Compile untrusted templates on Overleaf.
# Exit 0 = compiled, 1 = compile failed, 3 = no engine available.
set -u
proj="${1:?project dir or zip}"; main="${2:?main .tex}"; outdir="${3:-}"
zipsrc=""
if [ -f "$proj" ] && [[ "$proj" == *.zip ]]; then
  zipsrc="$proj"
  proj="$(mktemp -d "${TMPDIR:-/tmp}/paper-migrate-zip.XXXXXX")"
  python3 - "$zipsrc" "$proj" "$(dirname "$0")" <<'PYEOF' || { echo "compile_check: refused to extract $zipsrc" >&2; exit 2; }
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[3])
from verify_template import guarded_extract
guarded_extract(Path(sys.argv[1]), Path(sys.argv[2]), None)
PYEOF
  echo "extracted       $zipsrc -> temporary copy (independent compile of the delivered zip)"
fi
[ -d "$proj" ] || { echo "compile_check: no such dir $proj" >&2; exit 2; }
[ -f "$proj/$main" ] || { echo "compile_check: no $main in $proj" >&2; exit 2; }
base="${main%.tex}"
work="$(mktemp -d "${TMPDIR:-/tmp}/paper-migrate-build.XXXXXX")"
trap 'rm -rf "$work"; [ -n "$zipsrc" ] && rm -rf "$proj"' EXIT
cp -R "$proj/." "$work/"
cd "$work" || exit 2

engine=""; status=1
ulimit -t 1500 2>/dev/null || true          # CPU seconds for the whole build
offline=""; [ "${PAPER_MIGRATE_OFFLINE:-0}" = "1" ] && offline="--only-cached"
if command -v tectonic >/dev/null 2>&1; then
  engine="tectonic $(tectonic --version | awk '{print $2}') (XeTeX; shell-escape off${offline:+, offline})"
  tectonic --untrusted $offline --keep-logs "$main" >build.stdout 2>&1; status=$?
  log="$base.log"; [ -f "$log" ] || log=build.stdout
elif command -v latexmk >/dev/null 2>&1; then
  engine="latexmk -norc / pdflatex (shell_escape=f)"
  shell_escape=f openout_any=p latexmk -norc -pdf -interaction=nonstopmode -halt-on-error "$main" >build.stdout 2>&1; status=$?
  log="$base.log"
elif command -v pdflatex >/dev/null 2>&1; then
  engine="pdflatex x3 + bibtex (shell_escape=f)"
  { shell_escape=f openout_any=p pdflatex -no-shell-escape -interaction=nonstopmode -halt-on-error "$main" \
    && (bibtex "$base" || true) \
    && shell_escape=f pdflatex -no-shell-escape -interaction=nonstopmode -halt-on-error "$main" \
    && shell_escape=f pdflatex -no-shell-escape -interaction=nonstopmode -halt-on-error "$main"; } >build.stdout 2>&1; status=$?
  log="$base.log"
else
  echo "compile_check: no TeX engine found (tectonic / latexmk / pdflatex). Compile on Overleaf instead."; exit 3
fi

echo "compile_check  engine=$engine  status=$status"
if [ "$status" -ne 0 ]; then
  echo "--- first errors ---"
  grep -nE '^!|^error|Undefined control sequence|LaTeX Error|Emergency stop|is required' "$log" build.stdout 2>/dev/null | head -12
  [ -n "$outdir" ] && { mkdir -p "$outdir"; cp -f "$log" build.stdout "$outdir/" 2>/dev/null; printf '{"engine": "%s", "status": "failed"}\n' "$engine" > "$outdir/compile.json"; }
  echo "RESULT: COMPILE FAILED"; exit 1
fi

pdf="$base.pdf"
refpage=""; apppage=""
pages="$(pdfinfo "$pdf" 2>/dev/null | awk '/^Pages:/{print $2}')"
echo "pages          ${pages:-unknown}"
if command -v pdftotext >/dev/null 2>&1 && [ -n "$pages" ]; then
  refpage=""; apppage=""
  for p in $(seq 1 "$pages"); do
    t="$(pdftotext -f "$p" -l "$p" "$pdf" - 2>/dev/null | tr -s ' ')"   # reading order: works for two-column PDFs too
    [ -z "$refpage" ] && echo "$t" | grep -qiE '^ *[0-9]* *r ?e ?f ?e ?r ?e ?n ?c ?e ?s *$' && refpage=$p
    [ -z "$apppage" ] && echo "$t" | grep -qiE '^ *[0-9]* *A +(a ?p ?p ?e ?n ?d ?i ?x)' && apppage=$p
  done
  echo "references     start on page ${refpage:-not found}  (main text ends on that page; compare with the venue's main-text limit)"
  echo "appendix       starts on page ${apppage:-not found}"
fi
ov=$(grep -c 'Overfull \\hbox' "$log" 2>/dev/null); worst=$(grep -oE 'Overfull \\hbox \([0-9.]+pt' "$log" 2>/dev/null | sort -t'(' -k2 -rn | head -1 | grep -oE '[0-9.]+pt'); ur=$(grep -c 'Reference .* undefined' "$log" 2>/dev/null); uc=$(grep -c 'Citation .* undefined' "$log" 2>/dev/null)
# the worst overfull boxes with their source lines (for the report; formulas and tables are the usual culprits)
ovlist=$(grep -oE 'Overfull \\hbox \([0-9.]+pt too wide\) (in paragraph at lines [0-9-]+|detected at line [0-9]+|in alignment at lines [0-9-]+)' "$log" 2>/dev/null | sort -t'(' -k2 -rn | head -8 | sed -E 's/Overfull \\hbox \(([0-9.]+)pt too wide\) .*(lines? [0-9-]+)/\1pt at \2/' | paste -sd ';' - | sed 's/;/; /g')
echo "overfull hbox  ${ov:-0}   (worst: ${worst:-none})"
echo "undefined refs ${ur:-0}   undefined cites ${uc:-0}"
wf=$(grep -c 'Package wrapfig Warning' "$log" 2>/dev/null); ov2=$(grep -c 'Overfull \\vbox' "$log" 2>/dev/null)
echo "wrapfig warns  ${wf:-0}   overfull vbox ${ov2:-0}   (a wrapped float that collides or is forced to float shows up here)"
if [ -n "$outdir" ]; then
  mkdir -p "$outdir"; cp -f "$pdf" "$log" "$outdir/" 2>/dev/null
  printf '{"engine": "%s", "status": "compiled", "pages": %s, "references_page": %s, "appendix_page": %s, "overfull_hbox": %s, "worst_overfull_pt": "%s", "overfull_worst_lines": "%s", "undefined_refs": %s, "undefined_cites": %s, "wrapfig_warnings": %s, "overfull_vbox": %s}\n' \
    "$engine" "${pages:-null}" "${refpage:-null}" "${apppage:-null}" "${ov:-0}" "${worst:-none}" "${ovlist:-}" "${ur:-0}" "${uc:-0}" "${wf:-0}" "${ov2:-0}" > "$outdir/compile.json"
  echo "copied         $pdf, $log and compile.json -> $outdir"
fi
echo "RESULT: COMPILED"
exit 0
