#!/usr/bin/env python3
"""Re-insert layer-transition vias the autorouter dropped.

route.py occasionally writes a net split across layers with no via at the
transition: its internal connectivity assumed a via that its post-route cleanup
dropped (orphan-copper handling, its issue #8), so it reports the net routed
while the written board has it broken. This walks every still-unconnected net,
finds where two of its segment ends meet on OPPOSITE layers within a via's reach
but with no via there, and drops a through-via — keeping only the vias that
actually reduce the unconnected count (so it never adds spurious copper).

Runs under KiCad's bundled python (has pcbnew).
  <kicad-python> via_repair.py board.kicad_pcb
"""
import math
import sys
from collections import defaultdict

import pcbnew

VIA_SIZE = pcbnew.FromMM(0.5)
VIA_DRILL = pcbnew.FromMM(0.3)
TOL = pcbnew.FromMM(0.3)   # max opposite-layer end gap a single via can bridge


def dist(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def candidate_vias(board):
    """(net_code, VECTOR2I) for every spot where two same-net segment ends sit on
    opposite layers within TOL of each other and have no via already nearby."""
    ends = defaultdict(list)   # net -> [(pos, layer_id)]
    vias = defaultdict(list)   # net -> [pos]
    for t in board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA):
            vias[t.GetNetCode()].append(t.GetPosition())
        elif isinstance(t, pcbnew.PCB_TRACK):
            n = t.GetNetCode()
            ends[n].append((t.GetStart(), t.GetLayer()))
            ends[n].append((t.GetEnd(), t.GetLayer()))
    out = []
    for net, es in ends.items():
        for i in range(len(es)):
            pi, li = es[i]
            for j in range(i + 1, len(es)):
                pj, lj = es[j]
                if li == lj or dist(pi, pj) > TOL:
                    continue
                mid = pcbnew.VECTOR2I((pi.x + pj.x) // 2, (pi.y + pj.y) // 2)
                if any(dist(mid, vp) <= TOL for vp in vias[net]):
                    continue
                out.append((net, mid))
    return out


def add_via(board, pos, net):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pos)
    v.SetWidth(VIA_SIZE)
    v.SetDrill(VIA_DRILL)
    v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    v.SetNetCode(net)
    board.Add(v)
    return v


def main():
    path = sys.argv[1]
    board = pcbnew.LoadBoard(path)
    board.BuildConnectivity()
    added = 0
    for _ in range(20):   # backstop; real boards need 0-2
        before = board.GetConnectivity().GetUnconnectedCount(False)
        if before == 0:
            break
        progressed = False
        for net, pos in candidate_vias(board):
            v = add_via(board, pos, net)
            board.BuildConnectivity()
            if board.GetConnectivity().GetUnconnectedCount(False) < before:
                added += 1
                progressed = True
                break
            board.Remove(v)   # didn't help connectivity -> undo (no spurious vias)
        if not progressed:
            break
    if added:
        pcbnew.SaveBoard(path, board)
    left = board.GetConnectivity().GetUnconnectedCount(False)
    print(f"via repair: added {added} via(s); unconnected {left}")
    return 0 if left == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
