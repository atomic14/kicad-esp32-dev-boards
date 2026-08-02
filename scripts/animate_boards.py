#!/usr/bin/env python3
"""Render an animated 3D turntable montage of every generated board.

Produces build/montage_spin.mp4 — a 4x3 grid of all 12 boards, each spinning a
full 360 deg about its vertical axis at a fixed three-quarter tilt, at
2560x1440. The turn wraps exactly, so the clip loops seamlessly. Each board
starts at a different angle (staggered phase) so the grid reads as a wave
rather than 12 boards in lockstep.

Two things make this more than "render N frames":

* **Orthographic, not perspective.** kicad-cli's --zoom is linear in
  pixels-per-mm only under orthogonal projection (the same property
  render_boards.py's *_scale montages rely on); under --perspective it is not,
  so the scale correction below could not be solved in one pass.

* **De-breathing.** kicad-cli re-fits the camera to the board's rotated
  bounding box on every frame, so at a fixed --zoom a spinning board pulses in
  size through the turn (~5% orthogonal, ~22% under perspective, measured on
  ESP32-S3-WROOM-1). Left alone that reads as a bug — and the fixed zoom has to
  be timid enough for the worst angle, wasting tile. So each board first gets a
  cheap MEASURE pass (small,
  quality=basic, no floor, transparent background) over a coarse ring of
  angles; the opaque-pixel *area* of a flat board under orthogonal projection
  is invariant under yaw, which makes sqrt(area) a shape-independent proxy for
  the scale kicad-cli picked. The per-frame --zoom then divides that back out,
  normalised so the worst-case (widest) angle still fits inside FILL of the
  frame. Result: constant mm-per-pixel through the whole turn (measured spread
  0.35%, down from ~5%), no clipping, and a bigger board in the tile.

Renders out/<M>/<M>.kicad_pcb, same as render_boards.py — so it shows the
routed board once routing has run, and the GND pour is unfilled in headless
renders. Needs kicad-cli (path from library.json), ImageMagick and ffmpeg.

Usage:
  animate_boards.py                    # 120 frames @30fps -> build/montage_spin.mp4
  animate_boards.py --frames 180       # smoother/longer turn (more render time)
  animate_boards.py --supersample 1    # render at tile size; ~4x faster, softer
  animate_boards.py --boards ESP32-C3-MINI-1 --grid 1x1 --out spin_c3.mp4
"""
from __future__ import annotations
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from render_boards import boards, find_font

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"

OUT_W, OUT_H = 2560, 1440   # final video resolution
GRID = (4, 3)               # 12 boards -> 4 columns x 3 rows (tiles are 640x480)
FRAMES, FPS = 120, 30       # 120/30 = a 4 s seamless loop
TILT = -45                  # X rotation: the three-quarter tilt held all turn
SUPERSAMPLE = 2             # render tiles at Nx then downscale (antialiasing)
FILL = 0.90                 # board may occupy at most this much of its tile
MEASURE_STEP = 12           # degrees between scale-measurement samples
MEASURE_LONG = 320          # long edge of the measurement frame (aspect follows
                            # the tile: the fit kicad-cli picks is aspect-dependent)
MEASURE_ZOOM = 0.8          # reference zoom the measurements are taken at

MAGICK = ["magick"] if shutil.which("magick") else ["convert"]
MONTAGE = ["magick", "montage"] if shutil.which("magick") else ["montage"]


def render(cli, model_dir, pcb, out, yaw, zoom, w, h, *, final, tilt=TILT):
    """One turntable frame. final=True is the pretty pass (floor, shadows, high
    quality, opaque); final=False is the cheap measurement pass, which must keep
    the same projection and frame aspect so the fit it reports still applies."""
    cmd = [cli, "pcb", "render", "--rotate", f"{tilt},0,{yaw:.4f}",
           "--zoom", f"{zoom:.5f}", "-w", str(w), "-h", str(h), "-o", str(out)]
    if final:
        cmd += ["--background", "opaque", "--quality", "high", "--floor"]
    else:
        cmd += ["--background", "transparent", "--quality", "basic"]
    subprocess.run(cmd + [str(pcb)], check=True, capture_output=True,
                   env={**os.environ, "KICAD10_3DMODEL_DIR": str(model_dir)})


def measure(png: Path, mw: int, mh: int) -> tuple[float, float, float]:
    """(sigma, frac_w, frac_h) for a transparent-background render.

    sigma = sqrt(opaque area fraction), a linear scale proxy that does not
    depend on the silhouette's shape; frac_* are the alpha bounding box as a
    fraction of the frame, used to keep the widest angle inside FILL."""
    r = subprocess.run(MAGICK + [str(png), "-alpha", "extract",
                                 "-format", "%[fx:mean] %@", "info:"],
                       check=True, capture_output=True, text=True)
    mean, bbox = r.stdout.split()
    bw, bh = (int(v) for v in bbox.split("+")[0].split("x"))
    return math.sqrt(float(mean)), bw / mw, bh / mh


def zoom_curve(samples: dict[float, tuple[float, float, float]]):
    """Turn the measurement ring into yaw -> zoom, holding scale constant.

    At zoom z every measured quantity scales linearly, so pinning sigma to a
    constant sigma* gives zoom(a) = MEASURE_ZOOM * sigma* / sigma(a). sigma* is
    the largest value for which *every* sampled angle still fits within FILL."""
    angles = sorted(samples)
    sigma_star = FILL * min(samples[a][0] / max(samples[a][1], samples[a][2])
                            for a in angles)

    def zoom_at(yaw: float) -> float:
        yaw %= 360.0
        # linear interpolation round the ring (the curve is smooth in yaw)
        hi = next((i for i, a in enumerate(angles) if a >= yaw), len(angles))
        a0, a1 = angles[hi - 1], angles[hi % len(angles)]
        span = (a1 - a0) % 360.0 or 360.0
        t = ((yaw - a0) % 360.0) / span
        sigma = samples[a0][0] * (1 - t) + samples[a1][0] * t
        return MEASURE_ZOOM * sigma_star / sigma

    return zoom_at


def label_overlay(names, font, dest: Path, cols, rows, tile_w, tile_h):
    """One transparent 2560x1440 PNG carrying all the tile captions, composited
    onto every frame — cheaper and better-centred than annotating per frame."""
    if not font:
        return None
    tiles = []
    tmp = dest.parent / "_labels"
    tmp.mkdir(parents=True, exist_ok=True)
    for i, name in enumerate(names):
        t = tmp / f"{i:02d}.png"
        subprocess.run(MAGICK + [
            "-size", f"{tile_w}x{tile_h}", "xc:none", "-font", font,
            "-pointsize", str(max(14, tile_h // 22)), "-fill", "white",
            "-undercolor", "#00000059", "-gravity", "south",
            "-annotate", "+0+10", f" {name} ", str(t)], check=True)
        tiles.append(str(t))
    subprocess.run(MONTAGE + tiles + [
        "-font", font, "-tile", f"{cols}x{rows}",
        "-geometry", f"{tile_w}x{tile_h}+0+0",
        "-background", "none", str(dest)], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    return dest


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=FRAMES, help="frames per full turn")
    ap.add_argument("--fps", type=int, default=FPS)
    ap.add_argument("--supersample", type=int, default=SUPERSAMPLE,
                    help="render tiles at Nx the final size, then downscale")
    ap.add_argument("--jobs", type=int, default=2, help="concurrent kicad-cli renders")
    ap.add_argument("--tilt", type=float, default=TILT, help="X rotation (degrees)")
    ap.add_argument("--no-stagger", dest="stagger", action="store_false",
                    help="start every board at the same angle")
    ap.add_argument("--loops", type=int, default=1,
                    help="repeat the turn N times in the mp4 (free: stream copy)")
    ap.add_argument("--grid", default=f"{GRID[0]}x{GRID[1]}", help="COLSxROWS")
    ap.add_argument("--boards", nargs="*", help="only these modules (default: all)")
    ap.add_argument("--out", default="montage_spin.mp4", help="filename under build/")
    ap.add_argument("--keep-frames", action="store_true",
                    help="keep the PNG frames (build/spin/) after encoding")
    args = ap.parse_args(argv)

    lib_path = REPO / "library.json"
    if not lib_path.exists():
        print("ERROR: library.json missing — run resolve_library.py first.", file=sys.stderr)
        return 1
    for tool, why in ((MAGICK[0], "ImageMagick"), ("ffmpeg", "ffmpeg")):
        if not shutil.which(tool):
            print(f"ERROR: {why} (`{tool}`) not found on PATH.", file=sys.stderr)
            return 1
    lib = json.loads(lib_path.read_text())
    cli = lib["kicad_cli"]
    model_dir = Path(lib["kicad_symbols_dir"]).parent / "3dmodels"

    bds = boards()
    if args.boards:
        want = set(args.boards)
        bds = [b for b in bds if b[0] in want]
        missing = want - {n for n, _ in bds}
        if missing:
            print(f"ERROR: no generated board for {', '.join(sorted(missing))}",
                  file=sys.stderr)
            return 1
    if not bds:
        print("No generated boards found — run build_all.py first.", file=sys.stderr)
        return 1

    cols, rows = (int(v) for v in args.grid.lower().split("x"))
    if cols * rows < len(bds):
        print(f"ERROR: --grid {args.grid} holds {cols * rows} tiles, "
              f"but there are {len(bds)} boards.", file=sys.stderr)
        return 1
    tile_w, tile_h = OUT_W // cols, OUT_H // rows
    rw, rh = tile_w * args.supersample, tile_h * args.supersample
    # measurement frame keeps the tile's aspect (kicad-cli fits to the frame, so
    # a different aspect would report a fit that does not apply to the tile)
    if tile_w >= tile_h:
        mw, mh = MEASURE_LONG, max(1, round(MEASURE_LONG * tile_h / tile_w))
    else:
        mw, mh = max(1, round(MEASURE_LONG * tile_w / tile_h)), MEASURE_LONG

    spin = BUILD / "spin"
    frames_dir = spin / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    # ---- pass 1: measure how kicad-cli's auto-fit varies with yaw ----------
    print(f"Measuring turntable fit for {len(bds)} boards "
          f"({360 // MEASURE_STEP} angles each)")
    zooms = {}
    with tempfile.TemporaryDirectory() as td:
        def measure_board(item):
            name, pcb = item
            samples = {}
            for deg in range(0, 360, MEASURE_STEP):
                png = Path(td) / f"{name}_{deg}.png"
                render(cli, model_dir, pcb, png, deg, MEASURE_ZOOM,
                       mw, mh, final=False, tilt=args.tilt)
                samples[float(deg)] = measure(png, mw, mh)
                png.unlink()
            return name, zoom_curve(samples)

        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            for name, curve in pool.map(measure_board, bds):
                zooms[name] = curve
                print(f"  measured {name}")

    # ---- pass 2: render every frame of every board ------------------------
    jobs = []
    for b, (name, pcb) in enumerate(bds):
        phase = (360.0 * b / len(bds)) if args.stagger else 0.0
        out_dir = spin / name
        out_dir.mkdir(parents=True, exist_ok=True)
        for i in range(args.frames):
            yaw = (360.0 * i / args.frames + phase) % 360.0
            jobs.append((name, pcb, out_dir / f"{i:04d}.png", yaw,
                         zooms[name](yaw)))

    print(f"Rendering {len(jobs)} frames at {rw}x{rh} ({args.jobs} at a time)")
    done = 0

    def run_job(j):
        name, pcb, out, yaw, zoom = j
        render(cli, model_dir, pcb, out, yaw, zoom, rw, rh, final=True,
               tilt=args.tilt)
        return name

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for _ in pool.map(run_job, jobs):
            done += 1
            if done % 20 == 0 or done == len(jobs):
                print(f"  {done}/{len(jobs)} frames", flush=True)

    # ---- pass 3: stitch each frame into the grid, then encode -------------
    font = find_font()
    overlay = label_overlay([n for n, _ in bds], font,
                            spin / "labels.png", cols, rows, tile_w, tile_h)
    # montage insists on a usable font even with labels off
    font_args = ["-font", font] if font else []
    print(f"Stitching {args.frames} montages at {OUT_W}x{OUT_H}")
    for i in range(args.frames):
        tiles = [str(spin / name / f"{i:04d}.png") for name, _ in bds]
        dest = frames_dir / f"{i:04d}.png"
        subprocess.run(MONTAGE + tiles + font_args + [
            "-tile", f"{cols}x{rows}", "-geometry", f"{tile_w}x{tile_h}+0+0",
            "-background", "black", str(dest)], check=True)
        if overlay:
            subprocess.run(MAGICK + [str(dest), str(overlay), "-composite",
                                     str(dest)], check=True)

    mp4 = BUILD / args.out
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(args.fps),
                    "-i", str(frames_dir / "%04d.png"), "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "slow",
                    "-movflags", "+faststart", str(mp4)], check=True)
    if args.loops > 1:
        # repeating the encoded clip is a stream copy — no re-render, no re-encode
        listing = spin / "loops.txt"
        listing.write_text(f"file '{mp4}'\n" * args.loops)
        looped = mp4.with_name(f"{mp4.stem}_x{args.loops}{mp4.suffix}")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat",
                        "-safe", "0", "-i", str(listing), "-c", "copy",
                        str(looped)], check=True)
        print(f"Wrote {looped}")

    if not args.keep_frames:
        shutil.rmtree(spin, ignore_errors=True)

    secs = args.frames / args.fps
    print(f"Wrote {mp4}  ({len(bds)} boards, {cols}x{rows}, {OUT_W}x{OUT_H}, "
          f"{args.frames} frames, {secs:.1f}s seamless loop)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
