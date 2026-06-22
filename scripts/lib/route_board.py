#!/usr/bin/env python3
"""Auto-route a generated board with KiCadRoutingTools (external sibling repo).

2-layer boards: module / USB-C / passives on F.Cu, break-out headers on B.Cu,
so most nets cross layers (a via per net). Routed in incremental passes:

  diff pair (opt-in)  D+/D- as a coupled pair. Off by default (--diff): the run
                      is ~2mm — too short to benefit — and a coupled pair shorts
                      in the 0.5mm-pitch USB-C escape.
  USB-C fine neck     D+/D-/CC at 0.15mm; the board's 0.2mm rule can't escape the
                      0.5mm-pitch USB-C pads. A USB net class + baseline.kicad_dru
                      custom rule keep that DRC-legal (rest of the board is 0.2mm).
  signals             everything else at the board rule (0.2mm track / 0.2mm
                      clearance, 0.5/0.3 vias), free to use either layer.
  GND plane pour      GND poured on F.Cu + B.Cu, vias stitched to its pads.

Routes in place: writes the result back to modules/<M>/<M>.kicad_pcb (the
project's existing .kicad_pro/.kicad_dru carry the net classes + USB rule DRC
reads). Routing runs in a temp working file that's moved over the board only on
success, so a failed pass leaves the original untouched. Expects a freshly built
board — re-routing without an intervening build would route over existing tracks.

Requires KiCadRoutingTools as a sibling dir (../KiCadRoutingTools), or --tool /
$KICAD_ROUTING_TOOLS, with its Rust router built (python build_router.py) and
numpy/scipy/shapely available to `python3`.

Usage:
  route_board.py "ESP32-C3-MINI-1" [--tool DIR] [--diff] [--no-gnd-pour]
"""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Board design rules (from baseline.kicad_pro): the routing defaults.
TRACK_WIDTH = 0.2
CLEARANCE = 0.2
VIA_SIZE = 0.5
VIA_DRILL = 0.3
BOARD_EDGE_CLEARANCE = 0.5  # = baseline min_copper_edge_clearance
HOLE_TO_HOLE = 0.25         # = baseline min_hole_to_hole
LAYERS = ["F.Cu", "B.Cu"]
# Equal layer costs: the router defaults to penalising B.Cu 3x (it assumes a
# bottom ground plane), which crowds signals onto the top and fails the dense
# MINI boards. Our headers are on B.Cu, so both layers are first-class.
LAYER_COSTS = ["1.0", "1.0"]
# Finer neck for escaping the 0.5mm-pitch USB-C pads (below the board's 0.2mm
# rule — a local exception the USB-C footprint forces; flagged in the report).
USB_TRACK_WIDTH = 0.15
USB_CLEARANCE = 0.15
USB_GRID_STEP = 0.05
DIFF_PAIR_GAP = 0.15  # P-to-N gap for the USB D+/D- pair
POWER_WIDTH = 0.4     # +3V3/+5V routed wider than signals, and routed FIRST so
                      # they claim clean paths (incl. the empty bottom) before
                      # the GPIO fan-out fills it. Routing power first lets it use
                      # the bottom freely at the default via cost — lowering the
                      # via cost only added vias to the power path without helping.
POWER_NETS = ["+3V3", "+5V"]
GND_VIA_PAD_CLEARANCE = 0.1  # place GND stitching vias BESIDE pads (not in them),
                             # this far from the pad edge; via-in-pad is normally
                             # discouraged on SMD pads
# USB-C / CC nets that need the fine-neck pass (constant across boards — all
# share the baseline J1 USB-C connector).
USB_NETS = ["D+", "D-", "Net-(J1-CC1)", "Net-(J1-CC2)"]


def find_tool(explicit: str | None) -> Path:
    for cand in [explicit, os.environ.get("KICAD_ROUTING_TOOLS"),
                 REPO.parent / "KiCadRoutingTools"]:
        if cand and (Path(cand) / "route.py").exists():
            return Path(cand)
    raise SystemExit("KiCadRoutingTools not found — pass --tool DIR or set "
                     "$KICAD_ROUTING_TOOLS (sibling ../KiCadRoutingTools expected).")


def kicad_python() -> str:
    """KiCad's bundled python (has pcbnew, for zone filling). Derived from the
    kicad-cli path in library.json; falls back to `python3` (Linux distros put
    pcbnew in the system python)."""
    lib = REPO / "library.json"
    if lib.exists():
        cli = Path(json.loads(lib.read_text())["kicad_cli"])
        cand = cli.parents[1] / "Frameworks/Python.framework/Versions/Current/bin/python3"
        if cand.exists():
            return str(cand)
    return "python3"


def board_nets(pcb: Path) -> set[str]:
    """Every net name referenced on a pad in the board."""
    text = pcb.read_text()
    return set(re.findall(r'\(net "([^"]+)"\)', text))


def run_pass(tool: Path, label: str, inp: Path, out: Path, nets: list[str],
             track: float, clearance: float, grid_step: float | None,
             via_cost: int | None = None) -> dict:
    """Run one route.py pass over `nets`; return its JSON summary."""
    cmd = ["python3", "route.py", str(inp), str(out), *nets,
           "--layers", *LAYERS, "--layer-costs", *LAYER_COSTS,
           "--ordering", "mps", "--max-ripup", "8",
           "--track-width", str(track), "--clearance", str(clearance),
           "--via-size", str(VIA_SIZE), "--via-drill", str(VIA_DRILL),
           "--board-edge-clearance", str(BOARD_EDGE_CLEARANCE),
           "--hole-to-hole-clearance", str(HOLE_TO_HOLE)]
    if via_cost is not None:
        cmd += ["--via-cost", str(via_cost)]
    if grid_step:
        cmd += ["--grid-step", str(grid_step)]
    print(f"\n=== pass: {label} ({len(nets)} nets, {track}mm/{clearance}mm) ===")
    proc = subprocess.run(cmd, cwd=tool, capture_output=True, text=True)
    summary = {}
    for line in proc.stdout.splitlines():
        if line.startswith("JSON_SUMMARY:"):
            summary = json.loads(line[len("JSON_SUMMARY:"):])
    if proc.returncode != 0 and not summary:
        sys.stderr.write(proc.stdout[-2000:] + proc.stderr[-1000:])
        raise SystemExit(f"route.py failed in pass '{label}'")
    se = f"{summary.get('successful', '?')}/{summary.get('successful', 0) + summary.get('failed', 0)}"
    print(f"  single-ended {se} routed, {summary.get('total_vias', '?')} vias"
          + (f", FAILED {summary['failed_single']}" if summary.get("failed_single") else ""))
    return summary


def run_diff(tool: Path, inp: Path, out: Path, nets: list[str]) -> dict:
    """Route `nets` as differential pair(s) (D+/D- auto-paired by suffix). The
    coupled run here is only ~2mm (connector sits next to the module), so the
    router routes what coupling is worthwhile and defers the short legs to the
    single-ended fine-neck pass that follows."""
    cmd = ["python3", "route_diff.py", str(inp), str(out), "--nets", *nets,
           "--layers", *LAYERS, "--track-width", str(USB_TRACK_WIDTH),
           "--clearance", str(USB_CLEARANCE), "--diff-pair-gap", str(DIFF_PAIR_GAP),
           "--via-size", str(VIA_SIZE), "--via-drill", str(VIA_DRILL),
           "--board-edge-clearance", str(BOARD_EDGE_CLEARANCE),
           "--hole-to-hole-clearance", str(HOLE_TO_HOLE)]
    print(f"\n=== pass: differential pair {nets} ===")
    proc = subprocess.run(cmd, cwd=tool, capture_output=True, text=True)
    summary = {}
    for line in proc.stdout.splitlines():
        if line.startswith("JSON_SUMMARY:"):
            summary = json.loads(line[len("JSON_SUMMARY:"):])
    if proc.returncode != 0 and not summary:
        sys.stderr.write(proc.stdout[-2000:] + proc.stderr[-1000:])
        raise SystemExit("route_diff.py failed")
    print(f"  diff pairs {summary.get('successful', '?')} routed, "
          f"{summary.get('total_vias', '?')} vias"
          + (f", polarity-swapped {summary['polarity_swapped_pairs']}"
             if summary.get("polarity_swapped_pairs") else "")
          + (f", deferred to single-ended {summary['single_ended_followup_nets']}"
             if summary.get("single_ended_followup_nets") else ""))
    return summary


def run_planes(tool: Path, inp: Path, out: Path, net: str) -> str:
    """Pour `net` as a copper plane on both layers, stitching vias to its pads."""
    cmd = ["python3", "route_planes.py", str(inp), str(out),
           "--nets", *([net] * len(LAYERS)), "--plane-layers", *LAYERS,
           "--via-size", str(VIA_SIZE), "--via-drill", str(VIA_DRILL),
           "--track-width", str(TRACK_WIDTH),
           "--same-net-pad-clearance", str(GND_VIA_PAD_CLEARANCE)]
    print(f"\n=== pass: {net} plane pour (F.Cu + B.Cu) ===")
    proc = subprocess.run(cmd, cwd=tool, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout[-2000:] + proc.stderr[-1000:])
        raise SystemExit(f"route_planes.py failed for {net}")
    tail = [l for l in proc.stdout.splitlines()
            if any(k in l.lower() for k in ("via", "zone", "connect", "plane"))]
    print("  " + "\n  ".join(tail[-4:]))
    return proc.stdout


def connectivity(tool: Path, pcb: Path) -> str:
    proc = subprocess.run(["python3", "check_connected.py", str(pcb)],
                          cwd=tool, capture_output=True, text=True)
    return proc.stdout


def unrouted_nets(report: str) -> set[str]:
    """Net names check_connected reports as not fully routed — both the
    'Unrouted nets' list ("NAME (n pads)") and per-net disconnections
    ("NAME (net n):")."""
    nets = set()
    for line in report.splitlines():
        m = re.match(r"\s+(\S+) \(\d+ pads\)", line) or re.match(r"\s+(\S+) \(net \d+\):", line)
        if m:
            nets.add(m.group(1))
    return nets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("module")
    ap.add_argument("--tool", default=None, help="KiCadRoutingTools dir")
    ap.add_argument("--no-gnd-pour", action="store_true",
                    help="skip the GND copper-plane pour (leave GND for KiCad)")
    ap.add_argument("--diff", action="store_true",
                    help="route D+/D- as a differential pair. Default is single-"
                         "ended: the connector-to-module run is ~2mm (too short "
                         "to benefit from coupling) and forcing a coupled pair "
                         "through the 0.5mm-pitch USB-C escape creates shorts.")
    args = ap.parse_args()

    tool = find_tool(args.tool)
    safe = args.module.replace("/", "_")
    src = REPO / "modules" / safe / f"{safe}.kicad_pcb"
    if not src.exists():
        raise SystemExit(f"{src} not found — run build_board.py first.")
    # Route into a temp working file beside the board, then atomically move it
    # over <M>.kicad_pcb only once every pass has succeeded. Keeps the original
    # intact on failure, and (being a distinct file) lets the router read the
    # bare board while it writes the routed one.
    out = REPO / "modules" / safe / f"{safe}.routing.kicad_pcb"

    nets = board_nets(src)
    usb = [n for n in USB_NETS if n in nets]
    diff = [n for n in ("D+", "D-") if n in nets]
    gnd = [n for n in ("GND",) if n in nets]
    power = [n for n in POWER_NETS if n in nets]
    rest = sorted(nets - set(usb) - set(gnd) - set(power))

    try:
        # Pass 0 (opt-in): route D+/D- as a differential pair. Off by default — see
        # the --diff help: the run is too short to benefit and the coupled pair
        # shorts in the tight USB-C escape; single-ended is cleaner here.
        if args.diff and len(diff) == 2:
            run_diff(tool, src, out, diff)
            usb_in = out
        else:
            usb_in = src
        # Pass 1: USB/CC fine neck — routes (or completes) D± + CC at the fine neck
        run_pass(tool, "USB-C fine neck", usb_in, out, usb,
                 USB_TRACK_WIDTH, USB_CLEARANCE, USB_GRID_STEP)
        # Pass 2: power rails first (wider, and before the GPIO fan-out fills the
        # bottom) so they get clean paths and freely use the empty bottom layer
        if power:
            run_pass(tool, f"power rails @ {POWER_WIDTH}mm", out, out, power,
                     POWER_WIDTH, CLEARANCE, None)
        # Pass 3: every other signal at the board rule, incremental
        run_pass(tool, "signals (board rule)", out, out, rest,
                 TRACK_WIDTH, CLEARANCE, None)
        # Self-heal: retry any net the bulk pass couldn't finish, on its own so it
        # can rip up specific blockers — at the 0.15mm neck (legal: min width 0.15).
        stragglers = sorted(unrouted_nets(connectivity(tool, out)) - {"GND"})
        if stragglers:
            run_pass(tool, f"retry stragglers @ {USB_TRACK_WIDTH}mm", out, out, stragglers,
                     USB_TRACK_WIDTH, USB_CLEARANCE, USB_GRID_STEP)
        # GND: pour as a copper plane on both layers (stitched to its pads), then
        # fill the zones and stitch any unconnected GND island down to the pour
        # (pcbnew via KiCad's python — kicad-cli can't fill zones).
        if gnd and not args.no_gnd_pour:
            run_planes(tool, out, out, "GND")
            print("\n=== pass: GND fill + island stitch ===")
            proc = subprocess.run([kicad_python(), str(REPO / "scripts" / "lib" / "gnd_finish.py"), str(out)],
                                  capture_output=True, text=True)
            print("  " + (proc.stdout.strip().splitlines() or ["(no output)"])[-1])
            if proc.returncode not in (0, 2):
                sys.stderr.write(proc.stdout[-1500:] + proc.stderr[-500:])

        print("\n=== connectivity ===")
        report = connectivity(tool, out)
        print(report.split("Checking")[-1].strip()[:800] if "Checking" in report else report[-800:])

        # Every pass succeeded — move the routed working file over the board.
        # The project's existing .kicad_pro/.kicad_dru already sit beside it, so
        # DRC/KiCad still see the net classes + USB rule.
        os.replace(out, src)
        print(f"\nWrote {src}")
    finally:
        # Drop the temp working file AND any companion project files KiCad spawns
        # for it (<M>.routing.kicad_pro/.kicad_prl). On success the .kicad_pcb was
        # already moved over the board; the companions would otherwise be left
        # orphaned beside every routed board.
        for f in src.parent.glob(f"{safe}.routing.*"):
            f.unlink()


if __name__ == "__main__":
    main()
