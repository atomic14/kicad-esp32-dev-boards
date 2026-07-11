#!/usr/bin/env python3
"""Render 3D views of every generated board into 6x2 montages.

Produces six montages in build/ (+ the per-board build/<MODULE>/render_*.png
they're stitched from):
  montage_top.png       top, with components (the assembled board)
  montage_top_bare.png  top, NO components — the bare PCB artwork (copper/silk,
                        incl. the under-module silk marker), via a render of a
                        model-stripped copy of the board
  montage_bottom.png    bottom, with components
  montage_*_scale.png   the same three views TO SCALE: normally each render
                        auto-fits its board to the frame (losing relative
                        size), so the *_scale set re-renders with each board's
                        zoom cut by its true size vs the biggest board —
                        kicad-cli's --zoom is linear, so equal mm-per-pixel
                        across tiles. Labels carry the board's WxH mm.
  montage_hero.png      a perspective three-quarter "product shot" per board
                        (floor + shadows, HERO_ROTATE angle), stitched 4x3.
                        The per-board originals are build/<M>/render_hero.png.

Uses kicad-cli (path from library.json) for the 3D renders and ImageMagick
(`magick`) for the montage. The pin-header STEP models live next to KiCad's
bundled footprints, so KICAD10_3DMODEL_DIR is pointed there for the render.

Renders out/<M>/<M>.kicad_pcb (routing writes back in place, so this is the
routed board once it's been routed). Note: the GND copper pour is unfilled in
headless renders (it fills when opened in KiCad), so the montage shows the
tracks/vias but not the GND fill.

Usage:
  render_boards.py             # every board -> three montages
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import sexpdata
from sexpdata import Symbol

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
TILE = "6x2"  # 12 boards -> 6 columns x 2 rows
RENDER_W, RENDER_H = 600, 850  # per-board render frame (px)
BASE_ZOOM = 0.8                # zoom of the auto-fit views (and the biggest board)

# Hero shot: perspective three-quarter view with floor shadows. The angle was
# picked by eye — tilted back and turned so the module, buttons, USB-C and the
# under-board header pins all read clearly.
HERO_W, HERO_H = 1400, 1000
HERO_ROTATE = "-45,0,25"
HERO_ZOOM = 0.8   # 0.9 clipped the front (USB) edge of the board
HERO_TILE = "4x3"              # landscape tiles pack better 4 wide

# Montages to produce: (side, with_components, name). The bare-board view strips
# the 3D component models so the PCB artwork (copper/silk, incl. the under-module
# silk marker) is visible — what the board looks like before assembly.
VIEWS = [
    ("top", True, "top"),
    ("top", False, "top_bare"),
    ("bottom", True, "bottom"),
]

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


def board_dims(pcb_path: Path) -> tuple[float, float]:
    """(width, height) in mm of the board's Edge.Cuts gr_rect outline."""
    tree = sexpdata.loads(pcb_path.read_text())

    def sub(node, tag):
        return next((c for c in node if isinstance(c, list) and c
                     and isinstance(c[0], Symbol) and c[0].value() == tag), None)

    for node in tree:
        if not (isinstance(node, list) and node and isinstance(node[0], Symbol)
                and node[0].value() == "gr_rect"):
            continue
        layer = sub(node, "layer")
        if not layer or "Edge.Cuts" not in str(layer[1]):
            continue
        s, e = sub(node, "start"), sub(node, "end")
        return abs(float(e[1]) - float(s[1])), abs(float(e[2]) - float(s[2]))
    raise RuntimeError(f"no Edge.Cuts gr_rect outline in {pcb_path}")


def scale_zooms(dims: dict) -> dict[str, float]:
    """Per-board zoom that puts every render at the same mm-per-pixel.

    kicad-cli auto-fits each board to the frame, so at a fixed zoom the
    px-per-mm is proportional to min(W/w, H/h) — undo that ratio so the
    biggest board keeps BASE_ZOOM and the rest shrink to true relative size."""
    fit = {name: min(RENDER_W / w, RENDER_H / h) for name, (w, h) in dims.items()}
    biggest = min(fit.values())
    return {name: BASE_ZOOM * biggest / f for name, f in fit.items()}


def strip_models(pcb_path: Path, dest: Path) -> Path:
    """Write a copy of the board with every (model ...) node removed, so a render
    of it shows the bare PCB (copper/silk/mask/pads) with no 3D component bodies.
    Round-tripping through sexpdata is fine here — it's a throwaway render copy."""
    tree = sexpdata.loads(pcb_path.read_text())

    def strip(node):
        if not isinstance(node, list):
            return node
        return [strip(c) for c in node
                if not (isinstance(c, list) and c and isinstance(c[0], Symbol)
                        and c[0].value() == "model")]

    dest.write_text(sexpdata.dumps(strip(tree)))
    return dest


def render(cli, model_dir, pcb, side, out, zoom=BASE_ZOOM):
    env = {**os.environ, "KICAD10_3DMODEL_DIR": str(model_dir)}
    subprocess.run(
        [cli, "pcb", "render", "--side", side, "--background", "opaque",
         "--quality", "high", "--zoom", f"{zoom:.4f}",
         "-w", str(RENDER_W), "-h", str(RENDER_H),
         "-o", str(out), str(pcb)],
        check=True, env=env, capture_output=True)


def render_hero(cli, model_dir, pcb, out):
    env = {**os.environ, "KICAD10_3DMODEL_DIR": str(model_dir)}
    subprocess.run(
        [cli, "pcb", "render", "--background", "opaque", "--quality", "high",
         "--floor", "--perspective", "--rotate", HERO_ROTATE,
         "--zoom", str(HERO_ZOOM), "-w", str(HERO_W), "-h", str(HERO_H),
         "-o", str(out), str(pcb)],
        check=True, env=env, capture_output=True)


def montage(images, labels, out, font, tile=TILE, geometry="300x430+6+10"):
    # ImageMagick 7 is `magick montage`; IM6 (e.g. Ubuntu 24.04) only has the
    # standalone `montage` binary.
    args = ["magick", "montage"] if shutil.which("magick") else ["montage"]
    for img, lab in zip(images, labels):
        if font:
            args += ["-label", lab]
        args.append(str(img))
    if font:
        args += ["-font", font]
    args += ["-tile", tile, "-geometry", geometry,
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
    if not (shutil.which("magick") or shutil.which("montage")):
        print("ERROR: ImageMagick (`magick` or `montage`) not found on PATH.",
              file=sys.stderr)
        return 1
    lib = json.loads(lib_path.read_text())
    cli = lib["kicad_cli"]
    # bundled 3D models sit beside the bundled footprints: <SharedSupport>/3dmodels
    model_dir = Path(lib["kicad_symbols_dir"]).parent / "3dmodels"

    bds = boards()
    if not bds:
        print("No generated boards found — run build_all.py first.", file=sys.stderr)
        return 1
    dims = {name: board_dims(pcb) for name, pcb in bds}
    zooms = scale_zooms(dims)
    print(f"Rendering {len(bds)} boards")

    font = find_font()
    for side, with_comps, tag in VIEWS:
        imgs, scale_imgs, labels, scale_labels = [], [], [], []
        for name, pcb in bds:
            src, tmp = pcb, None
            if not with_comps:
                tmp = pcb.parent / f"{name}.nocomp.kicad_pcb"
                src = strip_models(pcb, tmp)
            out = BUILD / name / f"render_{tag}.png"
            out_scale = BUILD / name / f"render_{tag}_scale.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            print(f"  render {tag:8} {name}")
            render(cli, model_dir, src, side, out)
            render(cli, model_dir, src, side, out_scale, zooms[name])
            if tmp:
                tmp.unlink()
            imgs.append(out)
            scale_imgs.append(out_scale)
            labels.append(name)
            w, h = dims[name]
            scale_labels.append(f"{name}  {w:.0f}x{h:.0f}mm")
        for images, labs, suffix in ((imgs, labels, ""),
                                     (scale_imgs, scale_labels, "_scale")):
            dest = BUILD / f"montage_{tag}{suffix}.png"
            montage(images, labs, dest, font)
            print(f"Wrote {dest}  ({len(images)} boards, {TILE})")

    hero_imgs, hero_labels = [], []
    for name, pcb in bds:
        out = BUILD / name / "render_hero.png"
        print(f"  render hero     {name}")
        render_hero(cli, model_dir, pcb, out)
        hero_imgs.append(out)
        hero_labels.append(name)
    dest = BUILD / "montage_hero.png"
    montage(hero_imgs, hero_labels, dest, font,
            tile=HERO_TILE, geometry="420x300+6+10")
    print(f"Wrote {dest}  ({len(hero_imgs)} boards, {HERO_TILE})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
