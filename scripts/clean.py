#!/usr/bin/env python3
"""Remove generated artifacts to set up for a fresh build/route/render run.

The pipeline writes EVERYTHING it generates under two top-level trees, kept
separate from the curated source on purpose:

  - out/    generated KiCad projects (one out/<M>/ per module: .kicad_sch/pcb/pro,
            fp-lib-table, copied *.pretty/ + 3d-models/, route_debug/, KiCad
            transients). build_board/route_board/render write here.
  - build/  validation artifacts (ERC JSON, PDF render) per module.

So cleaning is just "delete those two dirs" — no name-matching, no risk to the
source. The curated source in modules/ (board.yaml + the extracted pinout.json)
is never touched, and neither is anything you keep elsewhere. The output dir is
DISPOSABLE: don't keep manual routing experiments inside out/ — they'll be wiped.

Usage:
  clean.py                 # remove out/ and build/
  clean.py -n / --dry-run  # show what would be removed, delete nothing
"""
from __future__ import annotations
import argparse
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGETS = (REPO / "out", REPO / "build")


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--dry-run", action="store_true", help="list only; delete nothing")
    args = ap.parse_args(argv)

    verb = "Would remove" if args.dry_run else "Removed"
    hit = []
    for t in TARGETS:
        if t.is_dir():
            hit.append(t.name + "/")
            if not args.dry_run:
                shutil.rmtree(t)
    print(f"{verb}: {', '.join(hit) if hit else '(nothing — out/ and build/ already absent)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
