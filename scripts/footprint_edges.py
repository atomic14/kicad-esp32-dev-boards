#!/usr/bin/env python3
"""Derive each module pin's PHYSICAL edge (left/right/top/bottom) and position
from its footprint pad geometry, joined with the signal names from pinout.json.

The schematic SYMBOL's left/right sides are a drawing convention (most signals
are drawn on the right), so they don't reflect the real package layout. The
FOOTPRINT pads do — pad numbers match symbol pin numbers. This module is the
basis for laying out break-out headers that mirror the physical module.

Usage:
  footprint_edges.py "ESP32-C3-MINI-1" [more...]   # print edge tables
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import sexpdata
from sexpdata import Symbol

REPO = Path(__file__).resolve().parent.parent
LIBRARY_JSON = REPO / "library.json"
EDGE_TOL_MM = 0.3


def tag(n):
    return n[0].value() if isinstance(n, list) and n and isinstance(n[0], Symbol) else None


def collect(n, name, out):
    if isinstance(n, list):
        if tag(n) == name:
            out.append(n)
        else:
            for c in n:
                collect(c, name, out)
    return out


def pad_positions(mod_path: Path) -> dict:
    """{pad_number: (x, y)} using each pad number's first occurrence."""
    data = sexpdata.loads(mod_path.read_text())
    xy = {}
    for p in collect(data, "pad", []):
        num = str(p[1])
        at = next((c for c in p if tag(c) == "at"), None)
        if at and num not in xy:
            xy[num] = (float(at[1]), float(at[2]))
    return xy


def find_footprint(fp_dir: Path, symbol: str) -> Path:
    """Pick the plain footprint (not HandSoldering / U variants) for a symbol."""
    base = symbol.replace("/", "_").replace("-MINI-1_U", "-MINI-1")
    cand = fp_dir / f"{base}.kicad_mod"
    if cand.exists():
        return cand
    # fall back to any file starting with the base name, preferring the shortest
    matches = sorted(fp_dir.glob(f"{base}*.kicad_mod"), key=lambda p: len(p.name))
    if not matches:
        raise FileNotFoundError(f"No footprint for {symbol} (base {base}) in {fp_dir}")
    return matches[0]


def classify_edges(module: str) -> dict:
    """Return {edge: [ {number, name, gpio, is_nc, is_power, is_gnd, pos} ... ]}
    ordered along each edge, for the perimeter pads that map to real pins."""
    lib = json.loads(LIBRARY_JSON.read_text())
    fp_dir = Path(lib["footprint_lib"])
    safe = module.replace("/", "_")
    pins = {p["number"]: p for p in json.loads(
        (REPO / "modules" / safe / "pinout.json").read_text())["pins"]}

    xy = pad_positions(find_footprint(fp_dir, module))
    real = {n: c for n, c in xy.items() if n in pins}
    xs = [c[0] for c in real.values()]
    ys = [c[1] for c in real.values()]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)

    edges = {"left": [], "right": [], "top": [], "bottom": []}
    for n, (x, y) in real.items():
        if abs(x - minx) < EDGE_TOL_MM:
            edge, pos = "left", y
        elif abs(x - maxx) < EDGE_TOL_MM:
            edge, pos = "right", y
        elif abs(y - miny) < EDGE_TOL_MM:
            edge, pos = "top", x
        elif abs(y - maxy) < EDGE_TOL_MM:
            edge, pos = "bottom", x
        else:
            continue  # interior pad (thermal), skip
        p = pins[n]
        edges[edge].append({**p, "pos": pos})
    for e in edges:
        edges[e].sort(key=lambda p: p["pos"])
    return edges


def _short(p) -> str:
    if p["is_nc"]:
        return "NC"
    if p["name"] == "GND":
        return "GND"
    return p["name"].split("/")[0]


def main(argv):
    if not argv:
        print(__doc__)
        return 0
    for module in argv:
        edges = classify_edges(module)
        print(f"\n### {module}")
        for e in ("left", "bottom", "right", "top"):
            sig = [_short(p) for p in edges[e]]
            usable = [s for s in sig if s not in ("NC", "GND")]
            print(f"  {e:<6} ({len(sig):>2} pads, {len(usable):>2} usable): {' '.join(sig)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
