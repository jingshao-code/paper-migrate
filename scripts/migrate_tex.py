#!/usr/bin/env python3
"""migrate_tex.py -- apply deterministic, format-only rewrites to a LaTeX main file.

    migrate_tex.py --in  orig/main.tex --out new/main.tex \
                   --rules references/aaai2027-to-iclr2027.json \
                   --preamble new/preamble.tex \
                   --replace '{MyPaper_AAAI27/figures/={figures/'

* The preamble (everything before \\begin{document}) is replaced verbatim by
  --preamble when given; the agent writes that file following the venue's
  mapping table.  Without --preamble the original preamble is kept.
* The body is rewritten by the ops in --rules (JSON) and by --replace pairs.
  Every op is a mechanical text operation; nothing here can reword prose.
* Afterwards ALWAYS run body_diff.py on the result -- this script does not
  verify anything by itself.

Ops (JSON objects in "ops"):
  {"op":"rename_env",   "from":"figure*", "to":"figure"}
  {"op":"replace",      "from":"\\\\columnwidth", "to":"\\\\linewidth"}
  {"op":"replace_cs",   "from":"shortcite", "to":"citep"}        # \\from followed by [ or {
  {"op":"regex",        "pattern":"...", "repl":"..."}           # re.MULTILINE
  {"op":"delete_lines", "regex":"^\\\\s*\\\\\\\\(newpage|clearpage)\\\\s*$"}
  {"op":"unwrap",       "macro":"resizebox", "args":["\\\\textwidth","!"]}  # remove \\macro{a}{b}{ ... }
  {"op":"insert_before","marker":"\\\\bibliography{", "text":"% TODO ...", "once":true}
  {"op":"insert_after", "marker_regex":"^\\\\\\\\bibliography\\\\{[^}]*\\\\}", "text":"\\\\bibliographystyle{x}", "once":true}
  {"op":"strip_graphics_prefix", "prefix":"MyPaper_AAAI27/"}
  {"op":"ensure_bibliographystyle", "name":"iclr2027_conference"}   # replace existing or insert after \\bibliography

Exit 0 on success, 2 on usage/IO error.  Refuses to overwrite --out unless --force.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def split_document(text: str) -> tuple[str, str, str]:
    m = re.search(r"\\begin\s*\{document\}", text)
    if not m:
        raise ValueError("no \\begin{document} found")
    return text[:m.start()], text[m.start():m.end()], text[m.end():]


def find_matching_brace(text: str, open_idx: int) -> int:
    depth, i, n = 0, open_idx, len(text)
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


def apply_op(body: str, op: dict, log: list[str]) -> str:
    kind = op["op"]
    if kind == "rename_env":
        n = body.count(f"\\begin{{{op['from']}}}") + body.count(f"\\end{{{op['from']}}}")
        body = body.replace(f"\\begin{{{op['from']}}}", f"\\begin{{{op['to']}}}")
        body = body.replace(f"\\end{{{op['from']}}}", f"\\end{{{op['to']}}}")
        log.append(f"rename_env {op['from']}->{op['to']}: {n}")
    elif kind == "replace":
        n = body.count(op["from"])
        body = body.replace(op["from"], op["to"])
        log.append(f"replace {op['from']!r}->{op['to']!r}: {n}")
    elif kind == "replace_cs":
        pat = re.compile(r"\\" + re.escape(op["from"]) + r"(?=\s*[\[{])")
        body, n = pat.subn(lambda _m: "\\" + op["to"], body)
        log.append(f"replace_cs \\{op['from']}->\\{op['to']}: {n}")
    elif kind == "regex":
        body, n = re.subn(op["pattern"], op["repl"], body, flags=re.MULTILINE)
        log.append(f"regex {op['pattern']!r}: {n}")
    elif kind == "delete_lines":
        pat = re.compile(op["regex"])
        lines = body.split("\n")
        kept = [ln for ln in lines if not pat.search(ln)]
        log.append(f"delete_lines {op['regex']!r}: {len(lines) - len(kept)}")
        body = "\n".join(kept)
    elif kind == "unwrap":
        head = "\\" + op["macro"] + "".join("{" + a + "}" for a in op.get("args", [])) + "{"
        n = 0
        while True:
            i = body.find(head)
            if i < 0:
                break
            start = i + len(head) - 1
            end = find_matching_brace(body, start)
            if end < 0:
                log.append(f"unwrap {op['macro']}: unbalanced braces, stopped")
                break
            body = body[:i] + body[start + 1:end] + body[end + 1:]
            n += 1
        log.append(f"unwrap {head}: {n}")
    elif kind == "insert_before":
        marker, text = op["marker"], op["text"]
        if op.get("once", True):
            i = body.find(marker)
            if i >= 0:
                ls = body.rfind("\n", 0, i) + 1
                body = body[:ls] + text.rstrip("\n") + "\n" + body[ls:]
                log.append(f"insert_before {marker!r}: 1")
            else:
                log.append(f"insert_before {marker!r}: marker not found")
        else:
            n = body.count(marker)
            body = body.replace(marker, text.rstrip("\n") + "\n" + marker)
            log.append(f"insert_before {marker!r}: {n}")
    elif kind == "insert_after":
        pat = re.compile(op["marker_regex"], re.MULTILINE)
        m = pat.search(body)
        if m:
            body = body[:m.end()] + "\n" + op["text"] + body[m.end():]
            log.append(f"insert_after {op['marker_regex']!r}: 1")
        else:
            log.append(f"insert_after {op['marker_regex']!r}: marker not found")
    elif kind == "ensure_bibliographystyle":
        name = op["name"]
        pat = re.compile(r"\\bibliographystyle\s*\{[^}]*\}")

        def commented(pos: int) -> bool:
            line_start = body.rfind("\n", 0, pos) + 1
            seg, i = body[line_start:pos], 0
            while i < len(seg):
                if seg[i] == "\\":
                    i += 2
                    continue
                if seg[i] == "%":
                    return True
                i += 1
            return False

        live = [m for m in pat.finditer(body) if not commented(m.start())]
        if live:
            for m in reversed(live):
                body = body[:m.start()] + "\\bibliographystyle{" + name + "}" + body[m.end():]
            log.append(f"ensure_bibliographystyle: replaced {len(live)} existing (commented ones ignored)")
        else:
            m = re.search(r"^\\bibliography\{[^}]*\}", body, re.MULTILINE)
            if m:
                body = body[:m.end()] + "\n\\bibliographystyle{" + name + "}" + body[m.end():]
                log.append("ensure_bibliographystyle: inserted after \\bibliography")
            else:
                log.append("ensure_bibliographystyle: no \\bibliography found; nothing done")
    elif kind == "strip_graphics_prefix":
        pat = re.compile(r"(\\includegraphics(?:\[[^\]]*\])?\s*\{)" + re.escape(op["prefix"]))
        body, n = pat.subn(r"\1", body)
        log.append(f"strip_graphics_prefix {op['prefix']!r}: {n}")
    else:
        raise ValueError(f"unknown op {kind!r}")
    return body


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rules", help="JSON file with an \"ops\" list")
    ap.add_argument("--preamble", help="file whose content replaces the preamble verbatim")
    ap.add_argument("--replace", action="append", default=[], metavar="OLD=NEW",
                    help="extra literal body replacement (repeatable)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print the change log, write nothing")
    args = ap.parse_args()

    try:
        text = Path(args.inp).read_text(encoding="utf-8")
        pre, begin, body = split_document(text)
        ops = json.loads(Path(args.rules).read_text(encoding="utf-8")).get("ops", []) if args.rules else []
        new_pre = Path(args.preamble).read_text(encoding="utf-8") if args.preamble else pre
        out = Path(args.out)
        if out.exists() and not args.force and not args.dry_run:
            print(f"migrate_tex: refusing to overwrite {out} (use --force)", file=sys.stderr)
            return 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"migrate_tex: {exc}", file=sys.stderr)
        return 2

    log: list[str] = []
    for op in ops:
        body = apply_op(body, op, log)
    for pair in args.replace:
        if "=" not in pair:
            print(f"migrate_tex: --replace expects OLD=NEW, got {pair!r}", file=sys.stderr)
            return 2
        old, new = pair.split("=", 1)
        body = apply_op(body, {"op": "replace", "from": old, "to": new}, log)

    if not new_pre.endswith("\n"):
        new_pre += "\n"
    result = new_pre + begin + body
    for line in log:
        print("  " + line)
    if args.dry_run:
        print("migrate_tex: dry run, nothing written")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result, encoding="utf-8")
    print(f"migrate_tex: wrote {out}  (preamble {'replaced' if args.preamble else 'kept'}, {len(log)} ops)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
