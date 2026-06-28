#!/usr/bin/env python3
"""Route a real GND track to cut-off GND pads — the islands a via can't bridge.

Some GND pads end up on a copper island with NO plane on the opposite layer to
drop a stitching via to (e.g. an F.Cu pad fenced off by signal tracks, with the
B.Cu plane carved away beneath it). gnd_finish's via-stitch can't help; the pad
needs an actual routed GND track. The autorouter (route.py) won't route GND — it
treats the filled GND zone as already connecting every GND pad, so it reports the
net done and skips it. We work around that with a temp net:

  prepare  — find each still-stranded GND cluster, put its pad AND the nearest
             main-plane GND pad on a fresh net "GNDSTRAGGLER<n>" (a net with no
             zone, so route.py WILL route it), unfill the zones (so the island
             copper isn't an obstacle around the temp pad), and write the result.
             Prints "STRAGGLER_NETS=<comma-list>" for the orchestrator to route.
  restore  — after route.py has routed those temp nets, rename their tracks/vias
             and pads back to GND and delete the temp nets. gnd_finish then refills
             and the island merges into the plane through the new track.

Runs under KiCad's bundled python (has pcbnew).
  <kicad-python> gnd_stragglers.py prepare  in.kicad_pcb out.kicad_pcb
  <kicad-python> gnd_stragglers.py restore  board.kicad_pcb
"""
import sys

import pcbnew

from gnd_finish import gnd_clusters, pad_count

TEMP_PREFIX = "GNDSTRAGGLER"


def nearest_pad(pos, pads, used):
    """The unused pad nearest `pos` (squared distance)."""
    best, best_d = None, None
    for p in pads:
        if p.m_Uuid.AsString() in used:
            continue
        pp = p.GetPosition()
        d = (pp.x - pos.x) ** 2 + (pp.y - pos.y) ** 2
        if best_d is None or d < best_d:
            best, best_d = p, d
    return best


def prepare(inp, outp):
    board = pcbnew.LoadBoard(inp)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.BuildConnectivity()
    clusters = gnd_clusters(board)
    if len(clusters) <= 1:
        print("STRAGGLER_NETS=")
        return 0
    main = max(clusters, key=pad_count)
    main_pads = [it for it in main if isinstance(it, pcbnew.PAD)]
    used, names = set(), []
    for cluster in clusters:
        if cluster is main:
            continue
        strand = next((it for it in cluster if isinstance(it, pcbnew.PAD)), None)
        if strand is None:
            continue
        target = nearest_pad(strand.GetPosition(), main_pads, used)
        if target is None:
            continue
        name = f"{TEMP_PREFIX}{len(names)}"
        net = pcbnew.NETINFO_ITEM(board, name)
        board.Add(net)
        strand.SetNet(net)
        target.SetNet(net)
        used.add(target.m_Uuid.AsString())
        names.append(name)
    # Unfill so the island's GND copper isn't an other-net obstacle hugging the
    # temp pad; gnd_finish re-pours after restore.
    for z in board.Zones():
        z.UnFill()
    pcbnew.SaveBoard(outp, board)
    print("STRAGGLER_NETS=" + ",".join(names))
    return 0


def restore(path):
    board = pcbnew.LoadBoard(path)
    gnd = board.FindNet("GND")
    names = set()
    for t in board.GetTracks():
        if t.GetNetname().startswith(TEMP_PREFIX):
            names.add(t.GetNetname())
            t.SetNet(gnd)
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.GetNetname().startswith(TEMP_PREFIX):
                names.add(p.GetNetname())
                p.SetNet(gnd)
    for nm in names:
        net = board.FindNet(nm)
        if net:
            board.RemoveNative(net)
    pcbnew.SaveBoard(path, board)
    print(f"gnd_stragglers: restored {len(names)} straggler net(s) to GND")
    return 0


def main():
    mode = sys.argv[1]
    if mode == "prepare":
        return prepare(sys.argv[2], sys.argv[3])
    if mode == "restore":
        return restore(sys.argv[2])
    sys.stderr.write(f"unknown mode {mode!r}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
