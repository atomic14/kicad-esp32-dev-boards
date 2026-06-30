#!/usr/bin/env python3
"""Export fabrication files (Gerbers + Excellon drill) for one module, zipped.

Reads the routed board out/<M>/<M>.kicad_pcb and writes, under out/<M>/:
  fab/            the loose Gerber + drill files (kept for inspection)
  <M>-fab.zip     those same files zipped flat — ready to upload to a board house

Standard 2-layer Gerber set + Excellon drill via kicad-cli. Key detail:
`--check-zones` refills the copper pours as part of the export, so the GND plane
is ALWAYS present in the copper Gerbers even if the board on disk was saved with
a stale/empty fill (headless tools don't auto-fill) — shipping a board with no
ground plane would be a silent, expensive fab error.

Coordinates use absolute origin for both Gerbers and drill so they line up.

Usage:
  fab_export.py "<MODULE>"
"""
from __future__ import annotations
import argparse
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LIBRARY_JSON = REPO / "library.json"

# Standard set for a 2-layer board. Explicit (not "all layers") so the routing
# keepout layer (User.2) and any other user/fab layers never leak into the zip.
GERBER_LAYERS = [
    "F.Cu", "B.Cu",
    "F.Paste", "B.Paste",
    "F.Silkscreen", "B.Silkscreen",
    "F.Mask", "B.Mask",
    "Edge.Cuts",
]


def kicad_cli() -> str:
    return json.loads(LIBRARY_JSON.read_text())["kicad_cli"]


def _run(cli, *args) -> bool:
    p = subprocess.run([cli, "pcb", *args], capture_output=True, text=True)
    if p.returncode != 0:
        sys.stderr.write(p.stdout[-1500:] + p.stderr[-500:])
        return False
    return True


def export(module: str) -> Path | None:
    safe = module.replace("/", "_")
    pcb = REPO / "out" / safe / f"{safe}.kicad_pcb"
    if not pcb.exists():
        print(f"ERROR: {pcb} not found — build + route it first.", file=sys.stderr)
        return None
    cli = kicad_cli()

    fab = pcb.parent / "fab"
    if fab.exists():
        shutil.rmtree(fab)          # never ship stale files from a prior export
    fab.mkdir(parents=True)
    out = str(fab) + "/"            # trailing slash: kicad-cli treats it as a dir

    if not _run(cli, "export", "gerbers", "-o", out,
                "--layers", ",".join(GERBER_LAYERS),
                "--check-zones",            # refill copper pours before plotting
                "--subtract-soldermask",    # don't print silk over mask openings
                str(pcb)):
        return None
    if not _run(cli, "export", "drill", "-o", out,
                "--format", "excellon",
                "--drill-origin", "absolute",
                "--excellon-units", "mm",
                "--generate-map", "--map-format", "gerberx2",
                str(pcb)):
        return None

    files = sorted(p for p in fab.iterdir() if p.is_file())
    zip_path = pcb.parent / f"{safe}-fab.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.name)      # flat archive (no nested dir)
    print(f"{safe}: {len(files)} fab file(s) -> {zip_path.relative_to(REPO)}")
    return zip_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("module")
    args = ap.parse_args()
    return 0 if export(args.module) else 1


if __name__ == "__main__":
    raise SystemExit(main())
