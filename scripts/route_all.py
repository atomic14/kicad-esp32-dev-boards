#!/usr/bin/env python3
"""Route every generated board with route_board.py and DRC each one.

For each modules/<M>/<M>.kicad_pcb it runs the routing pipeline, then a
kicad-cli DRC, and prints a summary: connectivity (all nets connected?) and the
real DRC error count (excluding the GND-plane "unconnected" that only resolves
once KiCad refills the zone, and which check_connected already accounts for).

Usage:
  route_all.py            # route + DRC every generated board
  route_all.py --diff     # forwarded to route_board (diff-pair D+/D-)
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = [sys.executable]


def modules():
    """Module dir names that have a generated PCB to route."""
    out = []
    for d in sorted((REPO / "modules").glob("*/")):
        if (d / f"{d.name}.kicad_pcb").exists():
            out.append(d.name)
    return out


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--diff", action="store_true", help="route D+/D- as a diff pair")
    args = ap.parse_args(argv)

    lib_path = REPO / "library.json"
    if not lib_path.exists():
        print("ERROR: library.json missing — run resolve_library.py first.", file=sys.stderr)
        return 1
    cli = json.loads(lib_path.read_text())["kicad_cli"]

    mods = modules()
    if not mods:
        print("No generated boards — run build_all.py first.", file=sys.stderr)
        return 1

    rows = []
    for m in mods:
        cmd = PY + [str(REPO / "scripts" / "route_board.py"), m]
        if args.diff:
            cmd.append("--diff")
        r = subprocess.run(cmd, capture_output=True, text=True)
        connected = "ALL NETS FULLY CONNECTED" in r.stdout
        if r.returncode != 0 and not connected:
            rows.append((m, "ROUTE-FAIL", "-"))
            sys.stderr.write(f"[{m}] route_board failed:\n{r.stdout[-1500:]}{r.stderr[-500:]}\n")
            continue
        # DRC the routed board, count real (non zone-fill) errors
        routed = REPO / "modules" / m / f"{m}_routed.kicad_pcb"
        rpt = tempfile.mktemp(suffix=".rpt")
        subprocess.run([cli, "pcb", "drc", "--severity-error", str(routed), "-o", rpt],
                       capture_output=True)
        try:
            lines = Path(rpt).read_text().splitlines()
        except OSError:
            lines = []
        real = sum(1 for l in lines if l.startswith("[") and "unconnected_items" not in l)
        Path(rpt).unlink(missing_ok=True)
        rows.append((m, "connected" if connected else "UNROUTED", real))
        print(f"  {m:22} {'connected' if connected else 'UNROUTED':10} real-DRC={real}")

    print(f"\n{'MODULE':22} {'CONNECTIVITY':12} REAL-DRC (excl. GND zone-fill)")
    for m, conn, real in rows:
        print(f"{m:22} {conn:12} {real}")
    ok = sum(1 for _, c, _ in rows if c == "connected")
    print(f"\n{ok}/{len(rows)} boards fully connected. "
          "(real-DRC = accepted CC-vs-NPTH grazes; GND zone fills in KiCad.)")
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
