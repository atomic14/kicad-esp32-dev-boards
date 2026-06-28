#!/usr/bin/env python3
"""Finish the GND pour: refill, stitch F.Cu<->B.Cu islands, drop orphan stubs.

Runs under KiCad's bundled python (has pcbnew) — kicad-cli can't fill zones, so
this is the scriptable equivalent. Run after the routing pass. Three steps:

  1. Refill the zones so the planes are re-carved around the routed tracks (the
     pre-route fill goes stale the moment route.py lays tracks through them).

  2. Stitch GND islands. The F.Cu and B.Cu pours are meant to be one net, tied
     together by the per-pad vias route_planes drops. But routing can carve a
     plane region free of every cross-layer via, leaving two electrically
     separate GND clusters — e.g. an F.Cu island sitting over the B.Cu plane
     with no via between them (ESP32-C6-MINI-1_U is the canonical case). For each
     stranded GND cluster we drop a stitching via where its copper on one layer
     overlaps the main plane on the other, keeping only vias that actually merge
     two clusters (so we never add spurious copper).

  3. Delete orphan GND stubs. Once the plane is carved around the routed tracks,
     a leftover route_planes stitch via+track can sit electrically isolated from
     the main pour — a real `unconnected_items` DRC error. Every GND *pad* still
     reaches the pour, so such a stub is dead copper: we delete any GND track/via
     not in the main GND cluster. (Stitching in step 2 first, so a fixable island
     gets a via rather than having its feeder copper deleted here.)

Usage:
  <kicad-python> gnd_finish.py board.kicad_pcb
"""
import math
import sys

import pcbnew

# These mirror the baseline DRC / routing rules (route_board.py) so a stitching
# via lands DRC-clean.
VIA_SIZE = pcbnew.FromMM(0.5)
VIA_DRILL = pcbnew.FromMM(0.3)
CLEARANCE = pcbnew.FromMM(0.15)      # min copper-to-copper clearance
HOLE_TO_HOLE = pcbnew.FromMM(0.45)   # min hole edge-to-edge clearance
GRID = pcbnew.FromMM(0.05)
# The via can short another net only through copper, so we require its annular
# ring (plus a full clearance ring beyond it) to sit inside the GND fill on BOTH
# layers: GND fill out to (via radius + clearance) guarantees the nearest
# other-net copper is at least one clearance from the via's copper edge.
VIA_COPPER_R = VIA_SIZE // 2 + CLEARANCE
# Step for scanning an island's interior for a stitch spot, and the ring of points
# (at the via's copper+clearance radius) used to confirm a spot is clear.
GRID_SCAN = pcbnew.FromMM(0.2)
RING_ANGLES = [i * math.pi / 4 for i in range(8)]


def snap(coord):
    """Snap a nm coordinate to the routing grid."""
    return round(coord / GRID) * GRID


def gnd_zones(board):
    return [z for z in board.Zones() if z.GetNetname() == "GND"]


def gnd_net_code(board):
    return next((p.GetNetCode() for fp in board.GetFootprints() for p in fp.Pads()
                 if p.GetNetname() == "GND"), None)


def gnd_clusters(board):
    """The distinct GND clusters, each the item list GetConnectedItems returns for
    one of its pads. GetConnectedItems traverses the filled zone, so each list is
    a whole electrical cluster. Anchored on pads — the filler drops fill islands
    that carry no pad, so every live GND region holds at least one pad."""
    conn = board.GetConnectivity()
    pads = [p for fp in board.GetFootprints() for p in fp.Pads()
            if p.GetNetname() == "GND"]
    clusters, seen = [], set()
    for pad in pads:
        if pad.m_Uuid.AsString() in seen:
            continue
        items = list(conn.GetConnectedItems(pad))
        seen |= {it.m_Uuid.AsString() for it in items}
        clusters.append(items)
    return clusters


def pad_count(cluster):
    return sum(1 for it in cluster if isinstance(it, pcbnew.PAD))


def on_gnd_fill(zones, layer, pos):
    return any(z.IsOnLayer(layer) and z.HitTestFilledArea(layer, pos) for z in zones)


def via_lands_clean(zones, pos):
    """True if a stitching via at `pos` sits wholly inside GND fill on BOTH layers
    — its centre and two rings out to the via's copper+clearance radius are all on
    GND copper. Such a via bridges the F.Cu and B.Cu GND and keeps a full clearance
    to the nearest other-net copper, so it cannot create a DRC short."""
    for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
        if not on_gnd_fill(zones, layer, pos):
            return False
        for radius in (VIA_COPPER_R // 2, VIA_COPPER_R):
            for a in RING_ANGLES:
                q = pcbnew.VECTOR2I(int(pos.x + radius * math.cos(a)),
                                    int(pos.y + radius * math.sin(a)))
                if not on_gnd_fill(zones, layer, q):
                    return False
    return True


def holes(board):
    """(centre, drill) for every drilled hole — through pads, vias and NPTH
    mounting holes. A stitching via keeps the hole-to-hole clearance off each,
    using its real drill so it clears the big mounting holes by their full radius."""
    out = [(p.GetPosition(), p.GetDrillSize().x) for fp in board.GetFootprints()
           for p in fp.Pads() if p.GetDrillSize().x > 0]
    out += [(t.GetPosition(), t.GetDrill()) for t in board.GetTracks()
            if isinstance(t, pcbnew.PCB_VIA)]
    return out


def hole_clear(pos, hole_list):
    """True if a via drill at `pos` holds HOLE_TO_HOLE off every existing hole."""
    for hpos, hdrill in hole_list:
        need = VIA_DRILL // 2 + hdrill // 2 + HOLE_TO_HOLE
        if (pos.x - hpos.x) ** 2 + (pos.y - hpos.y) ** 2 < need * need:
            return False
    return True


def cluster_islands(board, zones, cluster):
    """The filled-zone islands this cluster's copper occupies, as (layer, polygon)
    pairs. We seed from the cluster's pad/via/track points and keep each zone-fill
    outline that contains one — so we get exactly the island(s) belonging to this
    cluster, on whichever layer(s) it lives."""
    pts = {pcbnew.F_Cu: [], pcbnew.B_Cu: []}
    for it in cluster:
        if isinstance(it, pcbnew.PCB_VIA):
            for layer in pts:
                pts[layer].append(it.GetPosition())
        elif isinstance(it, pcbnew.PAD):
            for layer in pts:
                if it.IsOnLayer(layer):
                    pts[layer].append(it.GetPosition())
        elif isinstance(it, pcbnew.PCB_TRACK):
            if it.GetLayer() in pts:
                pts[it.GetLayer()] += [it.GetStart(), it.GetEnd()]
    islands = []
    for layer, seeds in pts.items():
        if not seeds:
            continue
        for z in zones:
            if not z.IsOnLayer(layer):
                continue
            polys = z.GetFilledPolysList(layer)
            for i in range(polys.OutlineCount()):
                poly = pcbnew.SHAPE_POLY_SET()
                poly.AddOutline(polys.Outline(i))
                if any(poly.Contains(p) for p in seeds):
                    islands.append((layer, poly))
    return islands


def candidate_vias(board, zones, cluster, hole_list):
    """Via spots that tie this cluster's island to the GND copper on the OTHER
    layer: scan the interior of each of the cluster's islands on a grid and keep
    points that land cleanly on GND fill on both layers and clear of every hole.
    Scanning the whole island (not just near a pad) finds the bridge even when the
    overlap with the opposite plane is far down a long, thin island."""
    out, seen = [], set()
    for _layer, poly in cluster_islands(board, zones, cluster):
        bbox = poly.BBox()
        y = bbox.GetTop()
        while y <= bbox.GetBottom():
            x = bbox.GetLeft()
            while x <= bbox.GetRight():
                pos = pcbnew.VECTOR2I(snap(x), snap(y))
                x += GRID_SCAN
                key = (pos.x, pos.y)
                if key in seen:
                    continue
                seen.add(key)
                if (poly.Contains(pos) and hole_clear(pos, hole_list)
                        and via_lands_clean(zones, pos)):
                    out.append(pos)
            y += GRID_SCAN
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


def cluster_of(board, pad):
    """UUIDs of the items electrically connected to `pad` (its current cluster)."""
    return {it.m_Uuid.AsString() for it in board.GetConnectivity().GetConnectedItems(pad)}


def stitch_islands(board):
    """Drop vias to tie stranded GND clusters into the main plane. For each
    stranded cluster we try interior via spots until one merges it into the main
    cluster (confirmed by connectivity), keeping only that via. A cluster with no
    spot overlapping the opposite-layer plane can't be bridged by a via (it needs
    a routed GND track) and is left for the caller to report. Returns (vias_added,
    clusters_remaining)."""
    net = gnd_net_code(board)
    if net is None or len(gnd_zones(board)) < 2:
        return 0, len(gnd_clusters(board))
    zones = gnd_zones(board)
    clusters = gnd_clusters(board)
    if len(clusters) <= 1:
        return 0, len(clusters)
    main = max(clusters, key=pad_count)
    main_uuid = next(it.m_Uuid.AsString() for it in main if isinstance(it, pcbnew.PAD))
    added = 0
    for cluster in clusters:
        if cluster is main:
            continue
        anchor = next((it for it in cluster if isinstance(it, pcbnew.PAD)), None)
        if anchor is None:
            continue
        hole_list = holes(board)
        for pos in candidate_vias(board, zones, cluster, hole_list):
            v = add_via(board, pos, net)
            board.BuildConnectivity()
            if main_uuid in cluster_of(board, anchor):   # merged into the main plane
                added += 1
                break
            board.Remove(v)                              # didn't help -> undo
            board.BuildConnectivity()
    return added, len(gnd_clusters(board))


def cleanup_orphans(board):
    """Delete GND tracks/vias not in the main GND cluster (the pour + pads).
    Returns the number removed."""
    clusters = gnd_clusters(board)
    if not clusters:
        return 0
    main = max(clusters, key=pad_count)
    main_uuids = {it.m_Uuid.AsString() for it in main}
    net = gnd_net_code(board)
    orphans = [t for t in board.GetTracks()
               if t.GetNetCode() == net and t.m_Uuid.AsString() not in main_uuids]
    for t in orphans:
        board.Remove(t)
    return len(orphans)


def main():
    path = sys.argv[1]
    board = pcbnew.LoadBoard(path)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.BuildConnectivity()

    added, remaining = stitch_islands(board)
    if added:
        # Re-carve the planes around the new vias before judging orphans.
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        board.BuildConnectivity()

    removed = cleanup_orphans(board)
    pcbnew.SaveBoard(path, board)

    msg = f"gnd_finish: refilled; stitched {added} GND via(s)"
    if remaining > 1:
        msg += f" (WARNING: {remaining} GND clusters remain — could not bridge)"
    msg += f"; removed {removed} orphan GND stub(s)"
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
