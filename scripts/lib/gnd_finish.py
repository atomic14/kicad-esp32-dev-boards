#!/usr/bin/env python3
"""Finish the GND pour: fill the zones (dropping empty isolated islands), in place.

Runs under KiCad's bundled python (has pcbnew) — kicad-cli can't fill zones, so
this is the scriptable equivalent. Run after the routing pass so the B.Cu plane
is re-carved around the routed tracks.

Usage:
  <kicad-python> gnd_finish.py board.kicad_pcb
"""
import sys
import pcbnew


def main():
    path = sys.argv[1]
    board = pcbnew.LoadBoard(path)
    # Drop empty isolated GND slivers on fill (good practice for a pour). An island
    # that still holds a stitching via is kept by KiCad — harmless orphan copper
    # (every GND pad reaches the pour anyway).
    for z in board.Zones():
        if z.GetNetname() == "GND":
            z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(path, board)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
