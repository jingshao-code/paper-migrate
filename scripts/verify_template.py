#!/usr/bin/env python3
"""verify_template.py -- check an official conference template against venues.yaml.

    verify_template.py --venue iclr2027 --dir  ~/Desktop/iclr2027
    verify_template.py --venue iclr2027 --zip  iclr-2027-style-files.zip --extract-to ./template

Every template file is hashed (SHA-256) and compared with `venues.<id>.template.files`
in the manifest.  With --zip the archive itself is hashed against
`official.template_zip_sha256` and extracted through a guard that refuses absolute
paths, `..`, backslashes, symlinks, oversized members and non-empty targets.

Standard library only (PyYAML is used when installed, otherwise a small built-in
reader handles the manifest's YAML subset).  No network, no TeX.

Exit 0: every file the venue needs (`template.needed_in_project`) matches.
Exit 1: mismatch / missing file / unknown extra file that shadows a needed one.
Exit 2: usage, IO, or guard refusal.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
import zipfile
from pathlib import Path

MAX_MEMBER_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 200 * 1024 * 1024
MAX_MEMBERS = 2000


# --------------------------------------------------------------------------- #
# Manifest loading                                                            #
# --------------------------------------------------------------------------- #

def load_manifest(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:
        return mini_yaml(text)


def _strip_comment(v: str) -> str:
    if v[:1] in ("'", '"'):
        q = v[0]
        end = v.find(q, 1)
        while end != -1 and q == "'" and v[end + 1:end + 2] == "'":
            end = v.find(q, end + 2)
        return v[:end + 1] if end != -1 else v
    return v.split(" #", 1)[0].rstrip()


def _split_flow(inner: str) -> list[str]:
    out, buf, depth, quote = [], "", 0, ""
    for ch in inner:
        if quote:
            buf += ch
            if ch == quote:
                quote = ""
            continue
        if ch in "'\"":
            quote = ch
            buf += ch
        elif ch in "[{":
            depth += 1
            buf += ch
        elif ch in "]}":
            depth -= 1
            buf += ch
        elif ch == "," and depth == 0:
            out.append(buf)
            buf = ""
        else:
            buf += ch
    if buf.strip():
        out.append(buf)
    return out


def _scalar(v: str):
    v = _strip_comment(v.strip())
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        return [_scalar(x.strip()) for x in _split_flow(inner)] if inner else []
    if v.startswith("{") and v.endswith("}"):
        out: dict = {}
        for item in _split_flow(v[1:-1].strip()):
            if ":" not in item:
                continue
            k, _, val = item.partition(":")
            out[k.strip().strip("'\"")] = _scalar(val.strip())
        return out
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        body = v[1:-1]
        return body.replace("''", "'") if v[0] == "'" else body
    if v in ("null", "~", ""):
        return None
    if v in ("true", "false"):
        return v == "true"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def mini_yaml(text: str):
    """Nested mappings, block lists, flow lists and scalars -- enough for venues.yaml."""
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        lines.append((len(raw) - len(raw.lstrip(" ")), raw.strip()))
    pos = 0

    def parse_block(indent: int):
        return parse_list(indent) if lines[pos][1].startswith("- ") else parse_map(indent)

    def parse_map(indent: int) -> dict:
        nonlocal pos
        out: dict = {}
        while pos < len(lines):
            ind, s = lines[pos]
            if ind < indent or s.startswith("- "):
                break
            if ind > indent:
                raise ValueError(f"unexpected indentation: {s!r}")
            key, _, val = s.partition(":")
            key = key.strip().strip("'\"")
            pos += 1
            val = val.strip()
            if val.startswith("#"):
                val = ""                      # `key:   # comment` opens a nested block
            if val == "":
                if pos < len(lines) and lines[pos][0] > indent:
                    out[key] = parse_block(lines[pos][0])
                else:
                    out[key] = None
            else:
                out[key] = _scalar(val)
        return out

    def parse_list(indent: int) -> list:
        nonlocal pos
        out: list = []
        while pos < len(lines):
            ind, s = lines[pos]
            if ind != indent or not s.startswith("- "):
                break
            item = s[2:].strip()
            if ":" in item and item[:1] not in "'\"[" and not item.startswith("http"):
                lines[pos] = (indent + 2, item)
                out.append(parse_map(indent + 2))
            else:
                pos += 1
                out.append(_scalar(item))
        return out

    return parse_map(lines[0][0]) if lines else {}


# --------------------------------------------------------------------------- #
# Hashing and zip guard                                                       #
# --------------------------------------------------------------------------- #

def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def guarded_extract(zip_path: Path, dest: Path, root_in_zip: str | None) -> list[str]:
    """Extract zip_path into dest (must be empty/non-existent).  Returns member names."""
    if dest.exists() and any(dest.iterdir()):
        raise ValueError(f"extract target is not empty: {dest}")
    names: list[str] = []
    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise ValueError(f"zip has {len(infos)} members (> {MAX_MEMBERS})")
        for info in infos:
            name = info.filename
            if name.startswith("/") or "\\" in name or ":" in name:
                raise ValueError(f"refusing unsafe member path: {name!r}")
            if any(part in ("..", "") for part in name.rstrip("/").split("/")):
                raise ValueError(f"refusing path traversal: {name!r}")
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise ValueError(f"refusing symlink member: {name!r}")
            if info.file_size > MAX_MEMBER_BYTES:
                raise ValueError(f"member too large: {name!r} ({info.file_size} bytes)")
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise ValueError("archive too large after decompression")
        dest.mkdir(parents=True, exist_ok=True)
        for info in infos:
            name = info.filename
            if name.startswith("__MACOSX/") or name.endswith("/.DS_Store"):
                continue
            rel = name
            if root_in_zip and rel.startswith(root_in_zip):
                rel = rel[len(root_in_zip):]
            if not rel or rel.endswith("/"):
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                out.write(src.read())
            names.append(rel)
    return names


# --------------------------------------------------------------------------- #
# Main                                                                        #
# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--venue", required=True, help="venue id in venues.yaml, e.g. iclr2027")
    ap.add_argument("--manifest", default=str(Path(__file__).resolve().parent.parent / "venues.yaml"))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dir", help="already-extracted template directory")
    g.add_argument("--zip", help="official template zip")
    ap.add_argument("--extract-to", help="where to extract --zip (must be empty)")
    ap.add_argument("--json", help="write a JSON report here")
    args = ap.parse_args()

    try:
        manifest = load_manifest(Path(args.manifest))
        venue = manifest["venues"][args.venue]
    except (OSError, KeyError, ValueError) as exc:
        print(f"verify_template: cannot read venue {args.venue!r} from {args.manifest}: {exc}", file=sys.stderr)
        return 2

    tmpl = venue.get("template", {}) or {}
    expected: dict[str, str] = tmpl.get("files", {}) or {}
    needed: list[str] = tmpl.get("needed_in_project", []) or list(expected)
    report: dict = {"venue": args.venue, "manifest": args.manifest, "zip": None, "files": [], "passed": False}

    try:
        if args.zip:
            zp = Path(args.zip).resolve(strict=True)
            zsha = sha256_file(zp)
            exp_z = (venue.get("official", {}) or {}).get("template_zip_sha256")
            report["zip"] = {"path": str(zp), "sha256": zsha, "expected": exp_z,
                             "match": (exp_z is None) or (zsha == exp_z)}
            if not args.extract_to:
                print("verify_template: --zip requires --extract-to", file=sys.stderr)
                return 2
            dest = Path(args.extract_to)
            guarded_extract(zp, dest, tmpl.get("root_in_zip"))
            tdir = dest
        else:
            tdir = Path(args.dir).resolve(strict=True)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"verify_template: {exc}", file=sys.stderr)
        return 2

    present = {p.name: p for p in tdir.rglob("*") if p.is_file() and not p.is_symlink()}
    ok = True
    for name, exp in expected.items():
        p = present.get(name)
        if p is None:
            status = "missing"
        else:
            status = "match" if sha256_file(p) == exp else "MISMATCH"
        report["files"].append({"file": name, "status": status, "needed": name in needed})
        if name in needed and status != "match":
            ok = False
    extras = sorted(set(present) - set(expected))
    report["extra_files"] = extras
    if report["zip"] and not report["zip"]["match"]:
        ok = False
    report["passed"] = ok

    print(f"verify_template  venue={args.venue}  dir={tdir}")
    if report["zip"]:
        z = report["zip"]
        print(f"zip sha256       {z['sha256']}  -> {'match' if z['match'] else 'MISMATCH'}"
              f"{'' if z['expected'] else ' (manifest has no zip hash; recorded only)'}")
    for row in report["files"]:
        flag = "*" if row["needed"] else " "
        print(f"  {flag} {row['status']:<9} {row['file']}")
    if extras:
        print(f"extra files      {extras}  (not in manifest; not used unless the paper needs them)")
    print(f"RESULT: {'PASS' if ok else 'FAIL'}  (* = required by the venue)")
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
