#!/usr/bin/env python3
"""Draw keepout rings around non-plated (mechanical) holes on a user layer.

The external router (route.py) doesn't read KiCad's custom .kicad_dru rules, so
its clearance to an NPTH mounting hole is only the general track clearance
(~0.15mm) — which violates the project's 0.254mm "NPTH to Track" rule. route.py
DOES honour closed keepout polygons drawn on a user layer (--keepout
--keepout-layer); it hard-blocks a track's CENTERLINE (and vias) from entering
them. This drops a ring around each NPTH hole big enough that a track kept out of
it still clears the hole by >0.254mm.

Because only the centerline is blocked, copper can reach (ring_radius -
half_width) from the hole centre, so the radius gap beyond the hole must exceed
0.254 + max track half-width (~0.1) + grid slack. GAP=0.45 -> ~0.3mm clearance.

Runs under KiCad's bundled python (pcbnew).

Usage:
  <kicad-python> hole_keepouts.py board.kicad_pcb [layer]
"""
import sys
import math
import pcbnew

LAYER = "User.2"
GAP = 0.6        # mm added to each hole's radius for the keepout disk. Must cover
                 # a VIA's radius (0.3) + the NPTH clearance (~0.25), not just a
                 # track half-width — route.py keeps a via CENTER out of the ring,
                 # so ring_radius >= hole_r + clearance + via_r for via copper to clear.
SEGMENTS = 24    # polygon approximation of the ring


def main():
    path = sys.argv[1]
    layer_name = sys.argv[2] if len(sys.argv) > 2 else LAYER
    board = pcbnew.LoadBoard(path)
    layer = board.GetLayerID(layer_name)
    gap = pcbnew.FromMM(GAP)
    added = 0
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetAttribute() != pcbnew.PAD_ATTRIB_NPTH:
                continue
            c = pad.GetPosition()
            r = pad.GetDrillSizeX() // 2 + gap
            poly = pcbnew.SHAPE_POLY_SET()
            poly.NewOutline()
            for i in range(SEGMENTS):
                a = 2 * math.pi * i / SEGMENTS
                poly.Append(int(c.x + r * math.cos(a)), int(c.y + r * math.sin(a)))
            shape = pcbnew.PCB_SHAPE(board)
            shape.SetShape(pcbnew.SHAPE_T_POLY)
            shape.SetPolyShape(poly)
            shape.SetLayer(layer)
            shape.SetFilled(False)
            shape.SetWidth(pcbnew.FromMM(0.05))
            board.Add(shape)
            added += 1
    pcbnew.SaveBoard(path, board)
    print(f"hole_keepouts: added {added} keepout ring(s) on {layer_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
