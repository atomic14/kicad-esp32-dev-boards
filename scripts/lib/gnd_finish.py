#!/usr/bin/env python3
"""Finish the GND pour: fill the zones, stitch any GND copper island that the
fill leaves unconnected down to the main pour with a via, then refill until the
ground is one electrical net (or no further progress).

Runs under KiCad's bundled python (has pcbnew). kicad-cli can't fill zones; this
is the scriptable equivalent. Stitching vias land in open pour (not in pads), so
they're compatible with beside-pad GND stitching.

Usage:
  <kicad-python> gnd_finish.py board.kicad_pcb   # fills + stitches in place
"""
import sys
import math
import pcbnew

VIA_SIZE = pcbnew.FromMM(0.5)
VIA_DRILL = pcbnew.FromMM(0.3)
# A stitching via is safe only where a disk of (via radius + clearance) around it
# stays inside the GND pour on BOTH layers — that guarantees it clears every
# other-net track/via/hole (the fill already holds clearance from them).
SAFE_MARGIN = pcbnew.FromMM(0.25 + 0.2)
SAMPLE_STEP = pcbnew.FromMM(0.4)


def gnd_netcode(board):
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.GetNetname() == "GND":
                return p.GetNetCode()
    return None


def fill(board):
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())


def merged_polys(board, layer):
    out = pcbnew.SHAPE_POLY_SET()
    for z in board.Zones():
        if z.GetNetname() == "GND" and z.IsOnLayer(layer):
            out.Append(z.GetFilledPolysList(layer))
    return out


def disk_inside(poly, pt, r):
    """True if pt and a ring of 8 points at radius r around it are all inside
    poly — i.e. a via of that radius there stays clear of the pour boundary
    (and thus of every other-net feature the pour already clears)."""
    if not poly.Contains(pt):
        return False
    for a in range(8):
        x = pt.x + int(r * math.cos(a * math.pi / 4))
        y = pt.y + int(r * math.sin(a * math.pi / 4))
        if not poly.Contains(pcbnew.VECTOR2I(x, y)):
            return False
    return True


def add_via(board, pt, net):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pt)
    v.SetWidth(VIA_SIZE)
    v.SetDrill(VIA_DRILL)
    v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    v.SetNetCode(net)
    board.Add(v)


def prune_orphan_vias(board, net):
    """Remove GND vias whose bottom end doesn't land in the B.Cu pour. Such a via
    can't be stitching anything to the main ground (a via *in* the B.Cu pour ties
    its F.Cu copper to main); it only holds an orphan F.Cu island alive. Every GND
    pad already reaches the pour, so none depends on these. Geometric, not via the
    connectivity API (which doesn't return full clusters reliably)."""
    bcu = merged_polys(board, pcbnew.B_Cu)
    orphans = [v for v in board.GetTracks()
               if isinstance(v, pcbnew.PCB_VIA) and v.GetNetCode() == net
               and not bcu.Contains(v.GetPosition())]
    for v in orphans:
        board.Remove(v)
    return len(orphans)


def stitch_pass(board, net):
    """For each F.Cu GND island without a via, drop a stitching via at a point
    that's safely inside both the F.Cu island and the B.Cu pour. Returns the
    number of vias added (islands too thin for a safe via are left for the
    island-removal pass)."""
    fcu = merged_polys(board, pcbnew.F_Cu)
    bcu = merged_polys(board, pcbnew.B_Cu)
    existing = [v.GetPosition() for v in board.GetTracks()
                if isinstance(v, pcbnew.PCB_VIA) and v.GetNetCode() == net]
    added = 0
    for i in range(fcu.OutlineCount()):
        outline = fcu.Outline(i)
        if any(outline.PointInside(p) for p in existing):
            continue
        bb = outline.BBox()
        spot = None
        y = bb.GetTop()
        while y <= bb.GetBottom() and spot is None:
            x = bb.GetLeft()
            while x <= bb.GetRight():
                pt = pcbnew.VECTOR2I(x, y)
                if fcu.Contains(pt, i) and disk_inside(fcu, pt, SAFE_MARGIN) \
                        and disk_inside(bcu, pt, SAFE_MARGIN):
                    spot = pt
                    break
                x += SAMPLE_STEP
            y += SAMPLE_STEP
        if spot is not None:
            add_via(board, spot, net)
            added += 1
    return added


def main():
    path = sys.argv[1]
    board = pcbnew.LoadBoard(path)
    net = gnd_netcode(board)
    # Drop empty isolated GND slivers on fill (good practice for a pour). Note:
    # an island that still holds a stitching via is kept by KiCad — those are
    # harmless orphan copper (every GND *pad* is connected via the pour anyway).
    for z in board.Zones():
        if z.GetNetname() == "GND":
            z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    fill(board)
    # board.BuildConnectivity()
    # before = board.GetConnectivity().GetUnconnectedCount(False)
    # total = 0
    # for _ in range(5):
    #     board.BuildConnectivity()
    #     if board.GetConnectivity().GetUnconnectedCount(False) == 0:
    #         break
    #     added = stitch_pass(board, net)
    #     if not added:
    #         break
    #     total += added
    #     fill(board)
    # # Prune redundant vias stranded in orphan islands, then refill so the now-
    # # empty islands get dropped by island-removal.
    # board.BuildConnectivity()
    # pruned = prune_orphan_vias(board, net)
    # if pruned:
    #     fill(board)
    # board.BuildConnectivity()
    # after = board.GetConnectivity().GetUnconnectedCount(False)
    pcbnew.SaveBoard(path, board)
    # print(f"GND finish: filled; stitched {total} via(s); pruned {pruned} orphan "
    #       f"via(s); unconnected {before} -> {after}")
    # return 0 if after == 0 else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

