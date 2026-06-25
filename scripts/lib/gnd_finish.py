#!/usr/bin/env python3
"""Finish the GND pour: refill the zones, then delete any orphan GND stub, in place.

Runs under KiCad's bundled python (has pcbnew) — kicad-cli can't fill zones, so
this is the scriptable equivalent. Run after the routing pass so the B.Cu plane
is re-carved around the routed tracks (the pre-route fill goes stale once route.py
lays tracks through the plane).

Once the plane is correctly carved around the routed tracks, the odd `route_planes`
stitching via+track can be left electrically isolated from the main pour — a real
`unconnected_items` DRC error. Every GND *pad* still reaches the pour, so such a
stub is dead copper: we delete any GND track/via not in the main GND cluster.

Usage:
  <kicad-python> gnd_finish.py board.kicad_pcb
"""
import sys
import pcbnew


def cleanup_orphans(board):
    """Delete GND tracks/vias not in the main GND cluster (the pour + pads).
    Returns the number removed."""
    anchor = next((p for fp in board.GetFootprints() for p in fp.Pads()
                   if p.GetNetname() == "GND"), None)
    if anchor is None:
        return 0
    net = anchor.GetNetCode()
    # GetConnectedItems traverses the filled zone, so this is the whole main
    # cluster — anything on GND not in it is a stranded stub.
    main = {i.m_Uuid.AsString() for i in board.GetConnectivity().GetConnectedItems(anchor)}
    orphans = [t for t in board.GetTracks()
               if t.GetNetCode() == net and t.m_Uuid.AsString() not in main]
    for t in orphans:
        board.Remove(t)
    return len(orphans)


def main():
    path = sys.argv[1]
    board = pcbnew.LoadBoard(path)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.BuildConnectivity()
    removed = cleanup_orphans(board)
    pcbnew.SaveBoard(path, board)
    print(f"gnd_finish: refilled; removed {removed} orphan GND stub(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
