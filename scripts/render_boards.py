#!/usr/bin/env python3
"""Render top + bottom 3D views of every generated board into two 6x2 montages.

Produces build/montage_top.png and build/montage_bottom.png (and the per-board
build/<MODULE>/render_{top,bottom}.png it stitches them from).

Uses kicad-cli (path from library.json) for the 3D renders and ImageMagick
(`magick`) for the montage. The pin-header STEP models live next to KiCad's
bundled footprints, so KICAD10_3DMODEL_DIR is pointed there for the render.

Renders out/<M>/<M>.kicad_pcb (routing writes back in place, so this is the
routed board once it's been routed). Note: the GND copper pour is unfilled in
headless renders (it fills when opened in KiCad), so the montage shows the
tracks/vias but not the GND fill.

Usage:
  render_boards.py             # every board -> two montages
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
TILE = "6x2"  # 12 boards -> 6 columns x 2 rows

# Common font locations (macOS / Linux) for the montage labels; labels are
# dropped if none is found rather than failing the whole render.
FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def find_font():
    return next((f for f in FONT_CANDIDATES if Path(f).exists()), None)


def boards():
    """(module_name, pcb_path) for every module with a generated PCB."""
    out = []
    for d in sorted((REPO / "out").glob("*/")):
        pcb = d / f"{d.name}.kicad_pcb"
        if pcb.exists():
            out.append((d.name, pcb))
    return out


def render(cli, model_dir, pcb, side, out):
    env = {**os.environ, "KICAD10_3DMODEL_DIR": str(model_dir)}
    subprocess.run(
        [cli, "pcb", "render", "--side", side, "--background", "opaque",
         "--quality", "high", "--zoom", "0.8", "-w", "600", "-h", "850",
         "-o", str(out), str(pcb)],
        check=True, env=env, capture_output=True)


def montage(images, labels, out, font):
    args = ["magick", "montage"]
    for img, lab in zip(images, labels):
        if font:
            args += ["-label", lab]
        args.append(str(img))
    if font:
        args += ["-font", font]
    args += ["-tile", TILE, "-geometry", "300x430+6+10",
             "-background", "white", "-fill", "black", "-pointsize", "14",
             str(out)]
    subprocess.run(args, check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.parse_args()

    lib_path = REPO / "library.json"
    if not lib_path.exists():
        print("ERROR: library.json missing — run resolve_library.py first.", file=sys.stderr)
        return 1
    if not shutil.which("magick"):
        print("ERROR: ImageMagick `magick` not found on PATH.", file=sys.stderr)
        return 1
    lib = json.loads(lib_path.read_text())
    cli = lib["kicad_cli"]
    # bundled 3D models sit beside the bundled footprints: <SharedSupport>/3dmodels
    model_dir = Path(lib["kicad_symbols_dir"]).parent / "3dmodels"

    bds = boards()
    if not bds:
        print("No generated boards found — run build_all.py first.", file=sys.stderr)
        return 1
    print(f"Rendering {len(bds)} boards")

    for side in ("top", "bottom"):
        imgs, labels = [], []
        for name, pcb in bds:
            out = BUILD / name / f"render_{side}.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            print(f"  render {side:6} {name}")
            render(cli, model_dir, pcb, side, out)
            imgs.append(out)
            labels.append(name)
        dest = BUILD / f"montage_{side}.png"
        montage(imgs, labels, dest, find_font())
        print(f"Wrote {dest}  ({len(imgs)} boards, {TILE})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
