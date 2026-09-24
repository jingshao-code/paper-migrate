#!/usr/bin/env python3
"""make_zip.py -- package a migrated LaTeX project as an Overleaf-ready zip.

    make_zip.py --project new/ --main main.tex --out paper_iclr2027.zip \
                --venue iclr2027 [--manifest venues.yaml]

* Files land at the zip root (Overleaf detects main.tex there).
* Build artifacts, OS junk and paper-migrate's evidence files are excluded (.aux .log .bbl
  .blg .out .fls .fdb_latexmk .synctex.gz .DS_Store __MACOSX .git __pycache__, body_diff.json
  and the other evidence JSON files, and the PDF produced from --main).  MIGRATION_REPORT.md
  travels with the project so the authors see it on Overleaf; the paper's own data files
  (.json, .csv, ...) are kept.
* The project is scanned for \\input/\\include, \\bibliography and
  \\includegraphics references; anything referenced but missing is an error,
  anything present but unreferenced is listed as a warning.
* Template files that belong to OTHER venues in the manifest (e.g. aaai2027.sty
  when packaging for ICLR) are refused -- they must not travel with the paper.
* A sidecar <out>.sha256.txt lists every packaged file's hash.

Standard library only.  Refuses to overwrite an existing zip unless --force.
Exit 0 ok, 1 packaging problem, 2 usage/IO error.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_template import load_manifest  # noqa: E402

EXCLUDE_SUFFIXES = (".aux", ".log", ".bbl", ".blg", ".out", ".fls", ".fdb_latexmk",
                    ".synctex.gz", ".toc", ".lof", ".lot", ".nav", ".snm", ".vrb", ".pyc")
# paper-migrate's own evidence files; any other .json (data, prompts) belongs to the paper and stays
EXCLUDE_NAMES = {".DS_Store", "Thumbs.db", "tectonic.log", "body_diff.json", "compliance.json", "layout.json",
                 "compile.json", "zip_compile.json", "rules.json", "verify.json", "profile.json", "pdf_check.json", "suggest.json"}
EXCLUDE_DIRS = {"__MACOSX", ".git", "__pycache__", ".svn", "build", ".tectonic", "pages"}   # pages/ = pdf_check renders
GRAPHIC_EXTS = (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".ps", ".tif", ".tiff", ".svg")
OVERLEAF_MAX_BYTES = 50 * 1024 * 1024


def strip_comments(text: str) -> str:
    out = []
    for line in text.splitlines():
        i, n, esc = 0, len(line), False
        cut = n
        while i < n:
            if line[i] == "\\":
                i += 2
                continue
            if line[i] == "%":
                cut = i
                break
            i += 1
        out.append(line[:cut])
    return "\n".join(out)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def references(project: Path, main: Path) -> tuple[set[Path], list[str]]:
    """Files referenced from main (recursively through \\input) + unresolved refs."""
    seen: set[Path] = set()
    missing: list[str] = []
    graphicspath: list[str] = [""]

    def resolve(rel: str, exts: tuple[str, ...]) -> Path | None:
        rel = rel.strip().strip('"')
        if not rel or rel.startswith("/") or ".." in rel.split("/"):
            return None
        for gp in graphicspath:
            for cand in [rel] + [rel + e for e in exts]:
                p = project / gp / cand
                if p.is_file() and not p.is_symlink():
                    return p
        return None

    def walk(tex: Path) -> None:
        if tex in seen:
            return
        seen.add(tex)
        text = strip_comments(tex.read_text(encoding="utf-8", errors="replace"))
        for m in re.finditer(r"\\graphicspath\s*\{((?:\{[^}]*\})+)\}", text):
            graphicspath.extend(re.findall(r"\{([^}]*)\}", m.group(1)))
        for m in re.finditer(r"\\(?:input|include|subfile)\s*\{([^}]*)\}", text):
            p = resolve(m.group(1), (".tex",))
            if p:
                walk(p)
            else:
                missing.append(f"\\input{{{m.group(1)}}}")
        for m in re.finditer(r"\\bibliography\s*\{([^}]*)\}", text):
            for b in m.group(1).split(","):
                p = resolve(b, (".bib",))
                if p:
                    seen.add(p)
                else:
                    missing.append(f"\\bibliography{{{b.strip()}}}")
        for m in re.finditer(r"\\includegraphics\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", text):
            p = resolve(m.group(1), GRAPHIC_EXTS)
            if p:
                seen.add(p)
            else:
                missing.append(f"\\includegraphics{{{m.group(1)}}}")
        for m in re.finditer(r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", text):
            for pk in m.group(1).split(","):
                p = resolve(pk.strip(), (".sty",))
                if p:
                    seen.add(p)
        for m in re.finditer(r"\\bibliographystyle\s*\{([^}]*)\}", text):
            p = resolve(m.group(1), (".bst",))
            if p:
                seen.add(p)
        for m in re.finditer(r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", text):
            p = resolve(m.group(1), (".cls",))
            if p:
                seen.add(p)

    walk(main)
    return seen, missing


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--main", default="main.tex")
    ap.add_argument("--out", required=True)
    ap.add_argument("--venue", help="target venue id (enables foreign-template refusal and needed-file check)")
    ap.add_argument("--manifest", default=str(Path(__file__).resolve().parent.parent / "venues.yaml"))
    ap.add_argument("--exclude", action="append", default=[], help="extra file name to exclude (repeatable)")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    try:
        project = Path(args.project).resolve(strict=True)
        main_tex = (project / args.main).resolve(strict=True)
        out = Path(args.out)
        if out.exists() and not args.force:
            print(f"make_zip: refusing to overwrite {out} (use --force)", file=sys.stderr)
            return 2
        manifest = load_manifest(Path(args.manifest)) if args.venue else None
    except (OSError, ValueError) as exc:
        print(f"make_zip: {exc}", file=sys.stderr)
        return 2

    foreign: set[str] = set()
    needed: list[str] = []
    if manifest:
        for vid, v in manifest["venues"].items():
            files = ((v.get("template") or {}).get("files") or {})
            if vid == args.venue:
                needed = (v.get("template") or {}).get("needed_in_project") or list(files)
            else:
                foreign |= set(files)
        foreign -= set(needed)

    main_pdf = main_tex.with_suffix(".pdf").name
    excl_names = EXCLUDE_NAMES | set(args.exclude) | {main_pdf}
    candidates: list[Path] = []
    for p in sorted(project.rglob("*")):
        rel = p.relative_to(project)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if not p.is_file() or p.is_symlink():
            continue
        if p.name in excl_names or p.name.endswith(EXCLUDE_SUFFIXES):
            continue
        candidates.append(p)

    problems: list[str] = []
    warnings: list[str] = []
    refd, missing = references(project, main_tex)
    problems += [f"referenced but not found: {m}" for m in missing]
    for p in candidates:
        if p.name in foreign:
            problems.append(f"foreign template file present: {p.relative_to(project)} (belongs to another venue)")
    for name in needed:
        if not any(p.name == name for p in candidates):
            problems.append(f"required template file missing: {name}")
    for p in candidates:
        if p not in refd and p.suffix.lower() in (".tex", ".bib", ".sty", ".bst", ".cls") + GRAPHIC_EXTS \
                and p.name not in needed:
            warnings.append(f"unreferenced: {p.relative_to(project)}")

    total = sum(p.stat().st_size for p in candidates)
    if total > OVERLEAF_MAX_BYTES:
        problems.append(f"uncompressed size {total} exceeds Overleaf's 50 MB upload limit")

    print(f"make_zip  project={project}  main={args.main}  files={len(candidates)}  bytes={total}")
    for w in warnings:
        print(f"  warning  {w}")
    for pr in problems:
        print(f"  PROBLEM  {pr}")
    if problems:
        print("RESULT: FAIL (nothing written)")
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in candidates:
            arc = p.relative_to(project).as_posix()
            zf.write(p, arc)
            rows.append(f"{sha256_file(p)}  {arc}")
    side = Path(str(out) + ".sha256.txt")
    side.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"wrote {out}  ({out.stat().st_size} bytes, {len(rows)} files)  hashes -> {side.name}")
    for r in rows:
        print("  " + r[:16] + "  " + r.split("  ", 1)[1])
    print("RESULT: OK  -- upload the zip at overleaf.com -> New Project -> Upload Project")
    return 0


if __name__ == "__main__":
    sys.exit(main())
