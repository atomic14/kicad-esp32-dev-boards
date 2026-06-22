#!/usr/bin/env python3
"""Remove generated artifacts to set up for a fresh build/route/render run.

Deletes ONLY what the pipeline produces, matched by the pipeline's own output
naming so hand-authored sources and ad-hoc routing experiments are never hit:

  - modules/<M>/<M>.kicad_{sch,pcb,pro,dru,prl}, fp-lib-table, sym-lib-table,
    fp-info-cache  (build_board / route_board output — routing writes the board
    back in place, so the routed result is just <M>.kicad_pcb)
  - modules/<M>/*.pretty/ and modules/<M>/3d-models/  (copied libraries)
  - KiCad transients: *-backups/, .history/, *.lck, ~* (under modules/)
  - the whole build/ tree (renders + montages)

KEPT: board.yaml, pinout.json, library.json, baseline/, .venv/, and any file
whose name does NOT match a generated pattern (reported as "left alone") — this
includes any manual *_routed.* experiments, which are yours to keep.

Usage:
  clean.py                 # remove generated output
  clean.py -n / --dry-run  # show what would be removed, delete nothing
"""
from __future__ import annotations
import argparse
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODULES = REPO / "modules"
BUILD = REPO / "build"

# Per-module names that are pure build_board output (stem == module dir name).
BASE_SUFFIXES = (".kicad_sch", ".kicad_pcb", ".kicad_pro", ".kicad_dru", ".kicad_prl")
BASE_NAMES = ("fp-lib-table", "sym-lib-table", "fp-info-cache")
# Names that are always preserved.
KEEP_NAMES = {"board.yaml", "pinout.json"}
# Transient dirs/globs to sweep inside each module dir.
TRANSIENT_DIRS = ("*-backups", ".history")
TRANSIENT_GLOBS = ("*.lck", "~*")


def classify(mod_dir: Path):
    """Bucket a module dir's entries into (base, transient, left)."""
    name = mod_dir.name
    base, transient, left = [], [], []
    for p in sorted(mod_dir.iterdir()):
        n = p.name
        if n in KEEP_NAMES:
            continue
        if p.is_file() and p.stem == name and p.suffix in BASE_SUFFIXES:
            base.append(p)
        elif p.is_file() and n in BASE_NAMES:
            base.append(p)
        elif p.is_dir() and (n.endswith(".pretty") or n == "3d-models"):
            base.append(p)
        elif p.is_dir() and any(p.match(g) for g in TRANSIENT_DIRS):
            transient.append(p)
        elif p.is_file() and any(p.match(g) for g in TRANSIENT_GLOBS):
            transient.append(p)
        else:
            left.append(p)
    return base, transient, left


def rel(p: Path) -> str:
    return str(p.relative_to(REPO))


def remove(p: Path, dry: bool):
    if dry:
        return
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    else:
        p.unlink()


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--dry-run", action="store_true", help="list only; delete nothing")
    args = ap.parse_args(argv)
    dry = args.dry_run

    base_all, transient_all, left_all = [], [], []
    if MODULES.is_dir():
        for mod_dir in sorted(MODULES.glob("*/")):
            if not (mod_dir / "board.yaml").exists():
                continue
            b, t, l = classify(mod_dir)
            base_all += b; transient_all += t; left_all += l

    # Base output + transients always go.
    for p in base_all + transient_all:
        remove(p, dry)
    if BUILD.is_dir():
        remove(BUILD, dry)

    # Summary.
    verb = "Would remove" if dry else "Removed"
    print(f"{verb}: {len(base_all)} base artifact(s), {len(transient_all)} transient(s)"
          + (", build/" if BUILD.is_dir() or dry else ""))
    if left_all:
        print(f"Left alone ({len(left_all)} non-pipeline file(s) — yours to keep):")
        for p in left_all:
            print(f"  {rel(p)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
