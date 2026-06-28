#!/usr/bin/env python3
"""Build + validate every module that has a curated board.yaml.

One-command repeat of the whole generation step: for each modules/<m>/board.yaml
it ensures pinout.json exists (extract_pinout), runs build_board, then validate,
and prints a summary table. Run resolve_library.py once first.

Usage:
  build_all.py              # build + validate all curated modules
  build_all.py --clean      # remove prior output first
  build_all.py --list       # just list the modules that will be built
"""
from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
PY = [sys.executable]


def modules():
    """(symbol_name, safe_dir_name) for every module with a board.yaml."""
    out = []
    for yml in sorted((REPO / "modules").glob("*/board.yaml")):
        cfg = yaml.safe_load(yml.read_text()) or {}
        sym = cfg.get("module") or yml.parent.name
        out.append((sym, yml.parent.name))
    return out


def run(script, *args):
    return subprocess.run(PY + [str(REPO / "scripts" / script), *args],
                          capture_output=True, text=True)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true",
                    help="remove prior generated output first")
    ap.add_argument("--list", action="store_true",
                    help="just list the modules that will be built")
    args = ap.parse_args(argv)

    if not (REPO / "library.json").exists():
        print("ERROR: library.json missing — run resolve_library.py first.", file=sys.stderr)
        return 1
    mods = modules()
    if args.list:
        for sym, _ in mods:
            print(sym)
        return 0
    if not mods:
        print("No modules with a board.yaml found.", file=sys.stderr)
        return 1

    if args.clean:
        c = run("clean.py")
        sys.stdout.write(c.stdout)

    total = len(mods)
    rows, failures = [], 0
    for i, (sym, safe) in enumerate(mods, 1):
        print(f"[{i:2}/{total}] {sym:22} building…", end="", flush=True)
        if not (REPO / "modules" / safe / "pinout.json").exists():
            run("lib/extract_pinout.py", sym)
        b = run("lib/build_board.py", sym)
        if b.returncode != 0:
            print(" BUILD-FAIL", flush=True)
            rows.append((sym, "BUILD-FAIL")); failures += 1
            print(f"[{sym}] build_board failed:\n{b.stderr}", file=sys.stderr)
            continue
        print(" validating…", end="", flush=True)
        v = run("lib/validate.py", f"out/{safe}/{safe}.kicad_sch")
        ok = v.returncode == 0
        print(" PASS" if ok else " VALIDATE-FAIL", flush=True)
        rows.append((sym, "PASS" if ok else "VALIDATE-FAIL"))
        if not ok:
            failures += 1

    print(f"\n{'MODULE':<22} RESULT")
    for sym, res in rows:
        print(f"{sym:<22} {res}")
    print(f"\n{len(rows) - failures}/{len(rows)} passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
