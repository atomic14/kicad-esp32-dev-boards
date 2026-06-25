#!/usr/bin/env python3
"""Fill every zone on the board in place (pcbnew's ZONE_FILLER).

Run right after the GND pour and BEFORE the routing pass so route.py sees the
poured copper as real obstacles/connectivity instead of a bare zone outline.
kicad-cli can't fill zones, so this runs under KiCad's bundled python — the same
interpreter route_board uses for gnd_finish.

Usage:
  <kicad-python> fill_zones.py board.kicad_pcb   # fills all zones in place
"""
import sys
import pcbnew


def main():
    path = sys.argv[1]
    board = pcbnew.LoadBoard(path)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(path, board)
    print("fill_zones: filled all zones")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
