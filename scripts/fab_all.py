#!/usr/bin/env python3
"""Export a fabrication zip for every routed board.

For each out/<M>/<M>.kicad_pcb, runs fab_export (Gerbers + Excellon drill) to
produce out/<M>/<M>-fab.zip, then prints a summary table. Run after routing.

Usage:
  fab_all.py              # fab zip for every routed board
"""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = [sys.executable]


def boards():
    """Module dir names under out/ that have a board to fabricate."""
    return [d.name for d in sorted((REPO / "out").glob("*/"))
            if (d / f"{d.name}.kicad_pcb").exists()]


def main():
    if not (REPO / "library.json").exists():
        print("ERROR: library.json missing — run resolve_library.py first.", file=sys.stderr)
        return 1
    mods = boards()
    if not mods:
        print("No routed boards in out/ — run build + route first.", file=sys.stderr)
        return 1

    total, fails = len(mods), 0
    for i, m in enumerate(mods, 1):
        print(f"[{i:2}/{total}] {m:22} fab export…", end="", flush=True)
        r = subprocess.run(PY + [str(REPO / "scripts" / "lib" / "fab_export.py"), m],
                           capture_output=True, text=True)
        if r.returncode == 0:
            print(" OK", flush=True)
        else:
            print(" FAIL", flush=True)
            fails += 1
            sys.stderr.write(r.stdout + r.stderr)
    print(f"\n{total - fails}/{total} fab zip(s) written to out/<M>/<M>-fab.zip")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
