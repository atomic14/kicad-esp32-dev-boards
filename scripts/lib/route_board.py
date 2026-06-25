#!/usr/bin/env python3
"""Auto-route a generated board with KiCadRoutingTools (external sibling repo).

2-layer boards: module / USB-C / passives on F.Cu, break-out headers on B.Cu,
so most nets cross layers (a via per net). The WHOLE board routes in a single
route.py pass:

  - every net at 0.15mm track / 0.15mm clearance (the Default net class) — fine
    enough to escape the 0.5mm-pitch USB-C pads, so no separate fine-neck pass.
  - +3V3 / +5V / GND widened to 0.4mm via --power-nets-widths. GND is just a
    wide net (tracks), NOT a copper pour.
  - --diff (opt-in) routes D+/D- as a coupled pair first; otherwise single-ended.
  - --max-ripup lets a blocked net rip up & reroute others. Doing this in ONE
    invocation is safe: the router's rip-up + orphan-copper cleanup cover every
    net. (Rip-up ACROSS separate per-pass invocations left shorting fragments —
    see git history — which is why the staged USB/power/signal passes are gone.)

Routes in place: writes the result back to modules/<M>/<M>.kicad_pcb (the
project's existing .kicad_pro/.kicad_dru carry the net classes + custom DRC
rules). Routing runs in a temp working file that's moved over the board only on
success, so a failure leaves the original untouched. Expects a freshly built
board — re-routing without an intervening build would route over existing tracks.

Requires KiCadRoutingTools as a sibling dir (../KiCadRoutingTools), or --tool /
$KICAD_ROUTING_TOOLS, with its Rust router built (python build_router.py) and
numpy/scipy/shapely available to `python3`.

Usage:
  route_board.py "ESP32-C3-MINI-1" [--tool DIR] [--diff]
"""
from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Board design rules (from baseline.kicad_pro): the routing defaults. Everything
# routes at 0.15mm track / 0.15mm clearance (the Default net class) — fine enough
# to escape the 0.5mm-pitch USB-C pads, so the whole board routes in ONE pass
# (where the router's rip-up + cleanup safely cover every net).
TRACK_WIDTH = 0.15
CLEARANCE = 0.15
VIA_SIZE = 0.5
VIA_DRILL = 0.3
BOARD_EDGE_CLEARANCE = 0.5  # = baseline min_copper_edge_clearance
HOLE_TO_HOLE = 0.27         # = baseline min_hole_to_hole
GRID_STEP = 0.05            # fine grid board-wide (needed for the USB-C escape)
LAYERS = ["F.Cu", "B.Cu"]
# Penalise B.Cu 3x (the router default): it assumes a bottom ground plane, which
# is what we pour, so signals prefer F.Cu and B.Cu is kept clear for the GND
# pour and the break-out headers.
LAYER_COSTS = ["1.0", "3.0"]
DIFF_PAIR_GAP = 0.15  # P-to-N gap for the USB D+/D- pair
POWER_WIDTH = 0.2     # +3V3/+5V/GND routed as wide tracks (low impedance) via
                      # --power-nets-widths in the single routing pass.
POWER_NETS = ["+3V3", "+5V", "GND"]   # widened to 0.4mm in the routing pass
GND_VIA_PAD_CLEARANCE = 0.25  # GND stitching vias sit BESIDE pads (not in them),
                             # this far from the pad edge (via-in-pad discouraged)
KEEPOUT_LAYER = "User.2"  # route.py reads keepout polygons from this user layer;
                          # hole_keepouts.py draws NPTH-clearance rings there.


def find_tool(explicit: str | None) -> Path:
    for cand in [explicit, os.environ.get("KICAD_ROUTING_TOOLS"),
                 REPO.parent / "KiCadRoutingTools"]:
        if cand and (Path(cand) / "route.py").exists():
            return Path(cand)
    raise SystemExit("KiCadRoutingTools not found — pass --tool DIR or set "
                     "$KICAD_ROUTING_TOOLS (sibling ../KiCadRoutingTools expected).")


def board_nets(pcb: Path) -> set[str]:
    """Every net name referenced on a pad in the board."""
    text = pcb.read_text()
    return set(re.findall(r'\(net "([^"]+)"\)', text))


def kicad_python() -> str:
    """KiCad's bundled python (has pcbnew, for zone filling). Derived from the
    kicad-cli path in library.json; falls back to `python3`."""
    lib = REPO / "library.json"
    if lib.exists():
        cli = Path(json.loads(lib.read_text())["kicad_cli"])
        cand = cli.parents[1] / "Frameworks/Python.framework/Versions/Current/bin/python3"
        if cand.exists():
            return str(cand)
    return "python3"

def fill_zones(out):
    """Fill the poured zones (pcbnew) so the routing pass sees the GND copper as
    real obstacles/connectivity rather than a bare zone outline. route_planes
    only lays the zone geometry; without this fill route.py routes blind to it."""
    print("\n=== pass: fill zones (before routing) ===")
    proc = subprocess.run([kicad_python(), str(REPO / "scripts" / "lib" / "fill_zones.py"), str(out)],
                          capture_output=True, text=True)
    print("  " + (proc.stdout.strip().splitlines() or ["(no output)"])[-1])
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout[-1500:] + proc.stderr[-500:])


def add_hole_keepouts(out):
    """Draw keepout rings around NPTH (mechanical) holes on KEEPOUT_LAYER. route.py
    doesn't read the .kicad_dru NPTH-to-track rule, so without this it clears the
    mounting holes by only the general track clearance; the rings (passed via
    --keepout below) keep tracks the required distance off the holes."""
    print("\n=== pass: NPTH hole keepout rings ===")
    proc = subprocess.run([kicad_python(), str(REPO / "scripts" / "lib" / "hole_keepouts.py"),
                           str(out), KEEPOUT_LAYER], capture_output=True, text=True)
    print("  " + (proc.stdout.strip().splitlines() or ["(no output)"])[-1])
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout[-1500:] + proc.stderr[-500:])


def finish_gnd(out):
    print("\n=== pass: GND fill + island stitch ===")
    proc = subprocess.run([kicad_python(), str(REPO / "scripts" / "lib" / "gnd_finish.py"), str(out)],
                            capture_output=True, text=True)
    print("  " + (proc.stdout.strip().splitlines() or ["(no output)"])[-1])
    if proc.returncode not in (0, 2):
        sys.stderr.write(proc.stdout[-1500:] + proc.stderr[-500:])

def run_pass(tool: Path, label: str, inp: Path, out: Path, nets: list[str],
             track: float, clearance: float, grid_step: float | None,
             via_cost: int | None = None,
             power_nets: list[str] | None = None,
             power_widths: list[float] | None = None,
             ordering: str = "mps", direction: str | None = None,
             keepout_layer: str | None = None) -> dict:
    """Run one route.py pass over `nets`; return its JSON summary. `power_nets`
    (with matching `power_widths`) route at their own wider width in the same
    pass — used for +3V3/+5V/GND. `ordering`/`direction` pick the net-ordering
    and per-net sweep strategy (varied across retry attempts). `keepout_layer`
    (when set) tells route.py to honour the keepout polygons on that user layer."""
    cmd = ["python3", "route.py", str(inp), str(out), *nets,
           "--layers", *LAYERS, "--layer-costs", *LAYER_COSTS,
           "--ordering", ordering, "--max-ripup", "10",
           "--track-width", str(track), "--clearance", str(clearance),
           "--via-size", str(VIA_SIZE), "--via-drill", str(VIA_DRILL),
           "--board-edge-clearance", str(BOARD_EDGE_CLEARANCE),
           "--hole-to-hole-clearance", str(HOLE_TO_HOLE),
           "--no-fix-drc-settings"]
    if direction:
        cmd += ["--direction", direction]
    if keepout_layer:
        cmd += ["--keepout", "--keepout-layer", keepout_layer]
    if power_nets:
        cmd += ["--power-nets", *power_nets,
                "--power-nets-widths", *[str(w) for w in power_widths]]
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
           "--layers", *LAYERS, "--track-width", str(TRACK_WIDTH),
           "--clearance", str(CLEARANCE), "--diff-pair-gap", str(DIFF_PAIR_GAP),
           "--via-size", str(VIA_SIZE), "--via-drill", str(VIA_DRILL),
           "--board-edge-clearance", str(BOARD_EDGE_CLEARANCE),
           "--hole-to-hole-clearance", str(HOLE_TO_HOLE),
           "--no-fix-drc-settings",
           # route_diff drops GND shield vias beside the pair's signal vias by
           # default; on our boards GND is the poured plane, so those just crowd
           # the pair (sub-clearance to D+/D- tracks). Disable them.
           "--no-gnd-vias"]
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


def run_planes(tool: Path, inp: Path, out: Path, net: str, layers: list[str]) -> str:
    """Pour `net` as a copper plane on BOTH layers, stitching a via from each pad
    down to it. Run as a completion step after the single routing pass: it fills
    the open copper and connects any GND the wide-track pass couldn't reach."""
    # --no-fix-drc-settings: route_planes/route.py otherwise auto-rewrite the
    # output .kicad_pro to pin DRC floors to the routing clearance (0.15) — issue
    # #160. That lowers min_hole_clearance below our baseline 0.25, so the GND
    # fill then hugs the NPTH mounting holes (0.15mm) and trips the 0.2mm "NPTH
    # with copper around" rule. We keep our curated baseline rules; opt out.
    cmd = ["python3", "route_planes.py", str(inp), str(out),
           "--nets", *([net] * len(layers)), "--plane-layers", *layers,
           "--via-size", str(VIA_SIZE), "--via-drill", str(VIA_DRILL),
           "--track-width", str(TRACK_WIDTH),
           "--same-net-pad-clearance", str(GND_VIA_PAD_CLEARANCE),
           "--no-fix-drc-settings"]
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
    """Net names check_connected reports as not fully routed."""
    nets = set()
    for line in report.splitlines():
        m = re.match(r"\s+(\S+) \(\d+ pads\)", line) or re.match(r"\s+(\S+) \(net \d+\):", line)
        if m:
            nets.add(m.group(1))
    return nets


def prerouted_nets(tool: Path, pcb: Path, all_nets: set[str]) -> set[str]:
    """Nets already fully connected by existing copper in the freshly-built board
    — e.g. hand-routed in the baseline, which build_board clones verbatim into
    every module. The autoroute pass skips these: route.py still sees their tracks
    as obstacles, it just won't re-route them on top (which would duplicate/fight
    the manual routing). GND is never treated as pre-routed — it's the pour's job."""
    routed = {n for n in all_nets if n not in unrouted_nets(connectivity(tool, pcb))}
    routed.discard("GND")
    return routed


def route_attempt(tool: Path, src: Path, out: Path, do_diff: bool,
                  diff: list[str], wide: list[str], route_nets: list[str],
                  ordering: str, direction: str | None,
                  debug_dir: Path) -> set[str]:
    """Run the full routing pipeline once, writing the routed board to `out`.
    Snapshots the board after every mutating pass into `debug_dir` (numbered in
    execution order) so a failed route can be inspected stage by stage. Returns
    the set of nets the connectivity check still reports unconnected (empty ==
    fully routed)."""
    step = [0]

    def snap(name: str):
        step[0] += 1
        shutil.copyfile(out, debug_dir / f"{step[0]:02d}-{name}.kicad_pcb")

    shutil.copyfile(src, debug_dir / "00-built.kicad_pcb")
    # Optional pre-step: route D+/D- as a coupled pair (off by default). CC and
    # every other net have no priority — they all route together below.
    if do_diff and len(diff) == 2:
        run_diff(tool, src, out, diff)
        snap("diff-pair")
        inp = out
    else:
        inp = src
    # Do the GND pour on the bottom copper
    run_planes(tool, inp, out, "GND", ["B.Cu"])
    snap("gnd-pour")

    # Fill the poured zone NOW, before routing: route_planes only writes the zone
    # outline, so route.py would otherwise route blind to the GND copper. Filling
    # first lets it treat the plane as a real obstacle / connectivity source.
    fill_zones(out)
    snap("gnd-fill")

    # Ring off the NPTH mounting holes so the routing pass keeps the required
    # clearance from them (route.py can't see the .kicad_dru NPTH-to-track rule).
    add_hole_keepouts(out)
    snap("hole-keepout")

    # ONE whole-board pass: every signal at 0.15mm track / 0.15mm clearance,
    # with +3V3/+5V widened. A single route.py invocation means its
    # rip-up-and-retry + orphan-copper cleanup safely cover ALL nets at once
    # (rip-up ACROSS separate per-pass invocations created shorts — see git
    # history). GND is excluded here and handled by the pour below.
    run_pass(tool, "route all (0.15mm; +3V3/+5V/GND 0.2mm)", out, out, route_nets,
             TRACK_WIDTH, CLEARANCE, GRID_STEP,
             power_nets=wide, power_widths=[POWER_WIDTH] * len(wide),
             ordering=ordering, direction=direction, keepout_layer=KEEPOUT_LAYER)
    snap("routed")

    # Re-insert any layer-transition via route.py dropped (it can write a net
    # split across layers with no via — its issue #8). Surgical: fixes split
    # nets before the straggler pass bothers re-routing them.
    print("\n=== pass: via repair (dropped layer-transition vias) ===")
    subprocess.run([kicad_python(), str(REPO / "scripts" / "lib" / "via_repair.py"), str(out)],
                   capture_output=True, text=True)
    snap("via-repair")

    # Re-fill the GND pour: the fill above went stale the moment route.py laid
    # B.Cu tracks through the plane, so this re-carves the copper around the
    # routed tracks (and drops empty islands). This is the authoritative fill.
    finish_gnd(out)
    snap("gnd-finish")

    print("\n=== connectivity ===")
    report = connectivity(tool, out)
    print(report.split("Checking")[-1].strip()[:800] if "Checking" in report else report[-800:])
    return unrouted_nets(report)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("module")
    ap.add_argument("--tool", default=None, help="KiCadRoutingTools dir")
    ap.add_argument("--no-diff", dest="diff", action="store_false",
                    help="route D+/D- single-ended instead of as a coupled "
                         "differential pair (the diff-pair pre-step is the default "
                         "— it routes the USB pair more reliably).")
    ap.set_defaults(diff=True)
    args = ap.parse_args()

    tool = find_tool(args.tool)
    safe = args.module.replace("/", "_")
    src = REPO / "modules" / safe / f"{safe}.kicad_pcb"
    if not src.exists():
        raise SystemExit(f"{src} not found — run build_board.py first.")
    # Route into a temp working file beside the board, then atomically move it
    # over <M>.kicad_pcb only once routing has succeeded. Keeps the original
    # intact on failure, and (being a distinct file) lets the router read the
    # bare board while it writes the routed one.
    out = REPO / "modules" / safe / f"{safe}.routing.kicad_pcb"
    # Per-stage snapshots land here (00-built, gnd-pour, gnd-fill, routed, …) so
    # a route that comes out wrong can be inspected stage by stage. Cleared at
    # the start of each run so it always reflects the latest route.
    debug_dir = REPO / "modules" / safe / "route_debug"
    if debug_dir.exists():
        shutil.rmtree(debug_dir)
    debug_dir.mkdir(parents=True)

    nets = board_nets(src)
    # Skip nets already fully routed in the (baseline-derived) board — only the
    # un-routed module signals get autorouted; hand-routed baseline nets are left
    # intact (route.py still treats their tracks as obstacles).
    prerouted = prerouted_nets(tool, src, nets)
    if prerouted:
        print(f"Skipping {len(prerouted)} already-routed net(s): "
              f"{', '.join(sorted(prerouted))}")
    to_route = nets - prerouted
    # GND is the poured plane (run_planes stitches every GND pad, gnd_finish
    # fills) — NOT a routed signal. Leaving it in the route pass made route.py lay
    # redundant wide GND tracks + vias all over the board, crowding signals (e.g.
    # GND vias landing < clearance from the D+/D- pair). Exclude it.
    to_route.discard("GND")
    diff = [n for n in ("D+", "D-") if n in to_route]
    wide = [n for n in POWER_NETS if n in to_route]   # +3V3/+5V -> wide tracks
    route_nets = sorted(to_route)

    try:
        # One routing attempt (route.py's default mps ordering). If it doesn't
        # fully connect we give up — the result is still written so it (and the
        # route_debug/ snapshots) can be inspected. route.py is deterministic, so
        # a bare re-run wouldn't change anything anyway.
        print("\n########## routing (ordering=mps) ##########")
        ur = route_attempt(tool, src, out, args.diff, diff, wide, route_nets,
                           "mps", None, debug_dir)

        # Move the routed working file over the board. The project's existing
        # .kicad_pro/.kicad_dru sit beside it, so DRC/KiCad still see the net
        # classes + custom rules.
        os.replace(out, src)
        if ur:
            print(f"\nWrote {src} — {len(ur)} net(s) still unconnected: "
                  f"{', '.join(sorted(ur))}")
        else:
            print(f"\nWrote {src} — fully connected")
    finally:
        # Drop the temp working file AND any companion project files KiCad spawns
        # for it (<M>.routing.kicad_pro/.kicad_prl) — otherwise they're orphaned.
        # The route_debug/ snapshots are intentionally NOT swept (they live in a
        # subdir and are kept for inspection).
        for f in src.parent.glob(f"{safe}.routing.*"):
            f.unlink()


if __name__ == "__main__":
    main()
