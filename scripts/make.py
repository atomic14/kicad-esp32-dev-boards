#!/usr/bin/env python3
"""One front door for the whole pipeline: clean -> build -> route -> render.

This is THE command to run. It chains the per-stage orchestrators (each of
which still works standalone):

  clean.py          remove prior generated output      (opt-in: --clean)
  build_all.py      schematic + PCB + ERC, every module (default ON)
  route_all.py      autoroute + DRC, every board        (default ON)
  render_boards.py  3D montages into build/             (opt-in: --render)

Run resolve_library.py once per machine first (creates library.json).

Usage:
  make.py                 # build + route every curated module
  make.py --clean         # wipe prior output first
  make.py --render        # also render the 3D montages at the end
  make.py --all           # clean + build + route + render
  make.py --no-route      # build only (stop before routing)
  make.py --diff          # forward diff-pair routing to route_all
"""
from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
PY = [sys.executable]


def stage(title, script, *args):
    """Run a stage script live (inherit stdio). Return its exit code."""
    print(f"\n{'=' * 60}\n== {title}\n{'=' * 60}", flush=True)
    return subprocess.run(PY + [str(SCRIPTS / script), *args]).returncode


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true", help="wipe prior output first")
    ap.add_argument("--no-route", action="store_true", help="build only; stop before routing")
    ap.add_argument("--render", action="store_true", help="also render 3D montages at the end")
    ap.add_argument("--all", action="store_true", help="clean + build + route + render")
    ap.add_argument("--no-diff", dest="diff", action="store_false",
                    help="route D+/D- single-ended (diff-pair is the default)")
    ap.set_defaults(diff=True)
    args = ap.parse_args(argv)

    if not (REPO / "library.json").exists():
        print("ERROR: library.json missing — run scripts/resolve_library.py first.", file=sys.stderr)
        return 1

    do_clean = args.clean or args.all
    do_route = not args.no_route or args.all
    do_render = args.render or args.all

    if do_clean:
        if stage("CLEAN", "clean.py"):
            return 1

    # build_all already cleans when asked; we cleaned above, so don't double up.
    if stage("BUILD + VALIDATE", "build_all.py"):
        print("\nBuild failed — stopping before routing.", file=sys.stderr)
        return 1

    if do_route:
        route_args = [] if args.diff else ["--no-diff"]
        if stage("ROUTE + DRC", "route_all.py", *route_args):
            print("\nRouting reported failures (see summary above).", file=sys.stderr)
            # keep going to render if requested — a partial route is still worth seeing
            rc = 1
        else:
            rc = 0
    else:
        rc = 0

    if do_render:
        if stage("RENDER", "render_boards.py"):
            return 1

    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
