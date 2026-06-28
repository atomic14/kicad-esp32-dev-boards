#!/usr/bin/env python3
"""Pre-route GND protection: stitch a via from each top-side GND pad down to the
intact B.Cu plane BEFORE signal routing.

The problem this fixes: run_planes ties the F.Cu and B.Cu GND pours together with
vias scattered through the plane field — NOT at the pads. Every GND pad reaches
the plane only through fill-copper continuity. So when route.py later lays signal
tracks that carve the fill around a top-side GND pad (and carve the B.Cu plane out
from under it), the pad is left on a stranded F.Cu island with no via to the
bottom plane — and post-route there's often no DRC-clean spot left to add one
(a signal track now sits where the via would go). The canonical victim is a lone
module GND pin boxed in by break-out routing (ESP32-S3-MINI-1 U1.64).

Doing it here, while the plane is still whole, a clean via fits right at the pad;
route.py then treats that via as an obstacle and flows the signals around it, so
the pad stays tied to the plane through routing. gnd_finish's post-route island
stitch + straggler routing remain the safety net for anything this misses.

Only top-side (F.Cu-only) GND pads need this — a pad already on B.Cu touches the
bottom plane directly. Pads that already have a GND via right beside them (e.g.
the exposed-pad thermal vias) are left alone.

Runs under KiCad's bundled python (has pcbnew).
  <kicad-python> gnd_prestitch.py board.kicad_pcb
"""
import sys

import pcbnew

from gnd_finish import (gnd_zones, gnd_net_code, on_gnd_fill, holes, hole_clear,
                        add_via, snap, VIA_SIZE, CLEARANCE, GRID, GRID_SCAN)

# Other-net copper must clear the via centre by at least the via copper radius
# plus a full clearance (same rule gnd_finish uses for its stitching vias).
NEED = VIA_SIZE // 2 + CLEARANCE
# Don't add a via beside a pad that already has a GND via this close.
DEDUP_R = pcbnew.FromMM(0.5)
# How far from the pad centre we'll look for a clean spot (the via should land on
# or right next to the pad so its copper overlaps the pad's).
SEARCH_R = pcbnew.FromMM(0.6)


def other_net_shapes(board, net):
    """Effective copper shapes of every non-GND track/pad, per layer — used to
    keep a stitching via a full clearance off other nets."""
    shapes = {pcbnew.F_Cu: [], pcbnew.B_Cu: []}
    for t in board.GetTracks():
        if t.GetNetCode() == net:
            continue
        for layer in shapes:
            if t.IsOnLayer(layer):
                shapes[layer].append(t.GetEffectiveShape(layer))
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.GetNetCode() == net:
                continue
            for layer in shapes:
                if p.IsOnLayer(layer):
                    shapes[layer].append(p.GetEffectiveShape(layer))
    return shapes


def clean_spot(zones, shapes, hole_list, pos):
    """True if a via at `pos` connects to the B.Cu GND plane (centre on B.Cu GND
    fill) and keeps a full clearance off all other-net copper on both layers and
    off every hole. Correct DRC rule — no other-net copper within NEED — rather
    than requiring GND fill to blanket the whole via-copper disk."""
    if not on_gnd_fill(zones, pcbnew.B_Cu, pos):
        return False
    for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
        for sh in shapes[layer]:
            if sh.Collide(pos, NEED):
                return False
    return hole_clear(pos, hole_list)


def find_spot(zones, shapes, hole_list, pad):
    """Nearest clean via spot at/around the pad centre, or None. Scans outward on
    the routing grid so we land the via on the pad itself where possible."""
    centre = pad.GetPosition()
    best, best_d = None, None
    y = centre.y - SEARCH_R
    while y <= centre.y + SEARCH_R:
        x = centre.x - SEARCH_R
        while x <= centre.x + SEARCH_R:
            pos = pcbnew.VECTOR2I(snap(x), snap(y))
            x += GRID
            d = (pos.x - centre.x) ** 2 + (pos.y - centre.y) ** 2
            if d > SEARCH_R * SEARCH_R:
                continue
            if (best_d is None or d < best_d) and clean_spot(zones, shapes, hole_list, pos):
                best, best_d = pos, d
        y += GRID
    return best


def prestitch(path):
    board = pcbnew.LoadBoard(path)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.BuildConnectivity()
    net = gnd_net_code(board)
    zones = gnd_zones(board)
    if net is None or not any(z.IsOnLayer(pcbnew.B_Cu) for z in zones):
        print("gnd_prestitch: no GND net / no B.Cu plane — nothing to do")
        return 0
    shapes = other_net_shapes(board, net)
    hole_list = holes(board)
    gnd_vias = [v for v in board.GetTracks()
                if isinstance(v, pcbnew.PCB_VIA) and v.GetNetCode() == net]
    added = 0
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetCode() != net or pad.IsOnLayer(pcbnew.B_Cu):
                continue  # bottom-side GND pads already touch the plane
            pos = pad.GetPosition()
            if any((v.GetPosition().x - pos.x) ** 2 + (v.GetPosition().y - pos.y) ** 2
                   < DEDUP_R * DEDUP_R for v in gnd_vias):
                continue  # already has a GND via beside it
            spot = find_spot(zones, shapes, hole_list, pad)
            if spot is not None:
                gnd_vias.append(add_via(board, spot, net))
                added += 1
    pcbnew.SaveBoard(path, board)
    print(f"gnd_prestitch: added {added} protective GND via(s) at top-side GND pads")
    return 0


def main():
    return prestitch(sys.argv[1])


if __name__ == "__main__":
    raise SystemExit(main())
