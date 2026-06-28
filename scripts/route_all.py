#!/usr/bin/env python3
"""Route every generated board with route_board.py and DRC each one.

For each out/<M>/<M>.kicad_pcb it runs the routing pipeline, then a
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
    """Module dir names that have a generated PCB to route (under out/)."""
    out = []
    for d in sorted((REPO / "out").glob("*/")):
        if (d / f"{d.name}.kicad_pcb").exists():
            out.append(d.name)
    return out


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-diff", dest="diff", action="store_false",
                    help="route D+/D- single-ended (diff-pair is the default)")
    ap.set_defaults(diff=True)
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
    total = len(mods)
    for i, m in enumerate(mods, 1):
        print(f"[{i:2}/{total}] {m:22} routing…", end="", flush=True)
        cmd = PY + [str(REPO / "scripts" / "lib" / "route_board.py"), m]
        if not args.diff:
            cmd.append("--no-diff")
        r = subprocess.run(cmd, capture_output=True, text=True)
        connected = "ALL NETS FULLY CONNECTED" in r.stdout
        if r.returncode != 0 and not connected:
            print(" ROUTE-FAIL", flush=True)
            rows.append((m, "ROUTE-FAIL", "-"))
            sys.stderr.write(f"[{m}] route_board failed:\n{r.stdout[-1500:]}{r.stderr[-500:]}\n")
            continue
        # DRC the routed board (routing wrote it back in place) and count every
        # error — INCLUDING unconnected_items. The GND pour + fill (gnd_finish)
        # should leave nothing unconnected, so any unconnected item is a real
        # defect to surface, not a benign zone-fill artifact to hide.
        print(" DRC…", end="", flush=True)
        board = REPO / "out" / m / f"{m}.kicad_pcb"
        rpt = tempfile.mktemp(suffix=".rpt")
        subprocess.run([cli, "pcb", "drc", "--severity-error", str(board), "-o", rpt],
                       capture_output=True)
        try:
            lines = Path(rpt).read_text().splitlines()
        except OSError:
            lines = []
        real = sum(1 for l in lines if l.startswith("["))
        Path(rpt).unlink(missing_ok=True)
        rows.append((m, "connected" if connected else "UNROUTED", real))
        print(f" {'connected' if connected else 'UNROUTED'} (real-DRC={real})", flush=True)

    print(f"\n{'MODULE':22} {'CONNECTIVITY':12} DRC ERRORS")
    for m, conn, real in rows:
        bad = conn != "connected" or (isinstance(real, int) and real > 0)
        print(f"{m:22} {conn:12} {real}{'   <-- FAIL' if bad else ''}")
    ok = sum(1 for _, c, r in rows if c == "connected" and isinstance(r, int) and r == 0)
    print(f"\n{ok}/{len(rows)} boards fully connected and DRC-clean.")
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
