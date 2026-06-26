#!/usr/bin/env python3
"""Deterministic PCB layout for a generated dev board.

`build_board.py` produces the schematic and copies the baseline PCB (which has
the *default* components — USB-C, LDO, buttons, LEDs — already placed). This
module adds the three pieces that vary per module — the ESP module footprint,
the two break-out pin headers, and the board outline — and assigns the same
nets the schematic uses (KiCad 10 PCBs carry nets by name on each pad, so this
is just `(net "<name>")` per pad — no net table to maintain).

Placement rules (all deterministic, +y is DOWN in KiCad):

  * USB socket + module are horizontally centred on the board (board centre x =
    USB centre x).
  * Left header on the left, right header on the right, symmetric about centre.
  * The board's bottom edge sits 0.5 mm below the USB socket's lowest plated
    through-hole pad (the board-edge copper clearance), so the socket body still
    overhangs the edge while its solder pads clear it.
  * Module sits above the existing components, antenna at the top, with a
    >= 5 mm gap between the module and the components.
  * Each header's TOP aligns with the module body top, so its pins overlap the
    module's pin rows for short break-out traces — UNLESS the header is longer
    than the module->USB span, in which case it stays bottom-aligned to the USB
    edge and the board grows up to fit it (a header longer than the board height).
  * Board outline spans the module body top (or a tall header's top) to the lower
    of the USB edge / header bottoms. The antenna overhangs the top edge; the USB
    socket overhangs the bottom edge.

`build_pcb()` is called by build_board.py with the already-computed net mapping
and the schematic symbol UUIDs (so each footprint links back to its symbol).
"""
from __future__ import annotations

import itertools
import json
import math
import uuid as uuidlib
from pathlib import Path

import sexpdata
from sexpdata import Symbol

REPO = Path(__file__).resolve().parents[2]

# --- placement constants (mm) ----------------------------------------------
EDGE_CLEAR = 0.5        # board-edge copper clearance below the USB THT pads
COMP_GAP = 2.0          # minimum module-to-components clearance
HDR_INNER_CLEAR = 2.0   # gap from the widest obstacle to a header courtyard
BOARD_MARGIN = 0.0      # gap from a header's outer courtyard to the board edge
HDR_PITCH = 2.54        # pin header pitch
HDR_CRT_HALF = 1.77     # pin header courtyard half-width
HDR_CRT_TOP = -1.77     # courtyard top edge, local (above pad 1 at y=0)
EDGE_WIDTH = 0.1        # Edge.Cuts line width
LABEL_FONT = 1.0        # pin-label silk text size
LABEL_OFFSET = HDR_CRT_HALF + 0.3  # inward offset from a pad to its label anchor


# ---- sexp helpers ----------------------------------------------------------

def parse(s: str):
    return sexpdata.loads(s)


def newid() -> str:
    # Deterministic UUIDs (see build_board.newid); distinct namespace so the two
    # generators never collide within one board.
    return str(uuidlib.uuid5(_UUID_NS, str(next(_uuid_seq))))


_UUID_NS = uuidlib.UUID("b1d4e7a2-0000-5000-8000-000000000002")
_uuid_seq = itertools.count()


def head(node) -> str | None:
    if isinstance(node, list) and node and isinstance(node[0], Symbol):
        return node[0].value()
    return None


def child(node, name):
    for c in node:
        if head(c) == name:
            return c
    return None


def num(x) -> float:
    return float(x.value()) if isinstance(x, Symbol) else float(x)


def collect(n, name, out):
    if isinstance(n, list):
        if head(n) == name:
            out.append(n)
        else:
            for c in n:
                collect(c, name, out)
    return out


# ---- footprint geometry ----------------------------------------------------

def courtyard_bbox_local(fp_node):
    """(minx, maxx, miny, maxy) of the F.CrtYd outline in footprint-local
    coords, or None if the footprint has no courtyard."""
    xs, ys = [], []
    for el in collect(fp_node, "fp_line", []) + collect(fp_node, "fp_rect", []):
        layer = child(el, "layer")
        if not layer or "CrtYd" not in str(layer[1]):
            continue
        for key in ("start", "end"):
            c = child(el, key)
            if c:
                xs.append(num(c[1])); ys.append(num(c[2]))
    if not xs:
        return None
    return (min(xs), max(xs), min(ys), max(ys))


def body_bbox_local(fp_node):
    """Fallback bbox from pads + silk/fab when there's no courtyard."""
    xs, ys = [], []
    for p in collect(fp_node, "pad", []):
        at = child(p, "at")
        if at:
            xs.append(num(at[1])); ys.append(num(at[2]))
    for el in collect(fp_node, "fp_line", []) + collect(fp_node, "fp_rect", []):
        layer = child(el, "layer")
        if not layer or ("SilkS" not in str(layer[1]) and "Fab" not in str(layer[1])):
            continue
        for key in ("start", "end"):
            c = child(el, key)
            if c:
                xs.append(num(c[1])); ys.append(num(c[2]))
    return (min(xs), max(xs), min(ys), max(ys))


def antenna_divider_local(fp_node, pad_min_y: float) -> float:
    """Local y of the module body top (the antenna/body boundary).

    The antenna sits on the no-pad side (smaller y). Every ESP module footprint
    draws a full-width silk/fab line dividing the antenna area from the body;
    it's the horizontal line with the largest y that's still above the topmost
    pad. Falls back to the topmost pad y if no such line exists."""
    candidates = []
    for el in collect(fp_node, "fp_line", []):
        layer = child(el, "layer")
        if not layer or ("SilkS" not in str(layer[1]) and "Fab" not in str(layer[1])):
            continue
        s, e = child(el, "start"), child(el, "end")
        if s and e and abs(num(s[2]) - num(e[2])) < 0.01:  # horizontal
            y = num(s[2])
            if y < pad_min_y - 0.05:
                candidates.append(y)
    return max(candidates) if candidates else pad_min_y


def pad_y_range(fp_node):
    ys = [num(child(p, "at")[2]) for p in collect(fp_node, "pad", []) if child(p, "at")]
    return min(ys), max(ys)


def pad_copper_top_local(fp_node) -> float:
    """Local y of the topmost (smallest-y) edge of any pad's copper. The board
    top edge must stay >= 0.5 mm above this — relevant for the MINI modules,
    whose castellated top pad row sits right against the antenna boundary."""
    top = None
    for p in collect(fp_node, "pad", []):
        pat, size = child(p, "at"), child(p, "size")
        px, py = num(pat[1]), num(pat[2])
        pth = math.radians(num(pat[3]) if len(pat) > 3 else 0.0)
        hw, hh = num(size[1]) / 2.0, num(size[2]) / 2.0
        for cx, cy in ((hw, hh), (-hw, hh), (hw, -hh), (-hw, -hh)):
            y = cx * math.sin(pth) + cy * math.cos(pth) + py
            top = y if top is None else min(top, y)
    return top


def tht_copper_bottom(fp_node):
    """Global max y reached by the copper of any *plated* through-hole pad in a
    placed footprint (non-plated mounting holes carry no copper, so they're
    excluded). Accounts for footprint + pad rotation."""
    at = child(fp_node, "at")
    ox, oy = num(at[1]), num(at[2])
    fth = math.radians(num(at[3]) if len(at) > 3 else 0.0)
    bottom = None
    for p in collect(fp_node, "pad", []):
        typ = p[2].value() if isinstance(p[2], Symbol) else str(p[2])
        if typ != "thru_hole":
            continue
        pat, size = child(p, "at"), child(p, "size")
        px, py = num(pat[1]), num(pat[2])
        pth = math.radians(num(pat[3]) if len(pat) > 3 else 0.0)
        hw, hh = num(size[1]) / 2.0, num(size[2]) / 2.0
        for cx, cy in ((hw, hh), (-hw, hh), (hw, -hh), (-hw, -hh)):
            # rotate corner by pad rot, offset to pad centre, rotate by fp rot
            rx = cx * math.cos(pth) - cy * math.sin(pth) + px
            ry = cx * math.sin(pth) + cy * math.cos(pth) + py
            gy = oy + rx * math.sin(fth) + ry * math.cos(fth)
            bottom = gy if bottom is None else max(bottom, gy)
    return bottom


# ---- placed-footprint builder ----------------------------------------------

def transform_xy_points(node, ox, oy, th):
    """Rewrite every (xy ...) point under `node` from footprint-local to board
    coords. Footprint *zones* are stored in absolute coords in a .kicad_pcb
    (unlike fp_lines/pads, which stay local), so a zone copied verbatim from a
    .kicad_mod must be transformed by the footprint's placement."""
    if not isinstance(node, list):
        return node
    if head(node) == "xy":
        lx, ly = num(node[1]), num(node[2])
        gx = ox + lx * math.cos(th) - ly * math.sin(th)
        gy = oy + lx * math.sin(th) + ly * math.cos(th)
        return [Symbol("xy"), round(gx, 4), round(gy, 4)]
    return [transform_xy_points(c, ox, oy, th) for c in node]


def _swap_side(layer: str) -> str:
    if layer.startswith("F."):
        return "B." + layer[2:]
    if layer.startswith("B."):
        return "F." + layer[2:]
    return layer  # "*.Cu", "*.Mask" etc. are side-agnostic


def flip_to_back(node):
    """Move a footprint node (and all its graphics/pads) to the bottom side by
    swapping every F.* layer to its B.* counterpart. The footprint's own
    (layer "F.Cu") flips to (layer "B.Cu"), which is what marks it bottom-side.
    Coordinates are left as-is — these single-column headers are symmetric about
    x=0, so the pad positions, pin order, and net mapping are unchanged.

    The 3D STEP model needs a tweak though: KiCad mirrors a flipped footprint's
    model in Y, which throws the pin-header body off the board (it extends from
    pin 1 *away* from the pads). Adding 180° about Z to the model rotation brings
    the body back over its pads, pins still pointing down."""
    if not isinstance(node, list):
        return node
    if head(node) == "layer":
        return [Symbol("layer")] + [_swap_side(v) if isinstance(v, str) else v
                                    for v in node[1:]]
    if head(node) == "rotate":  # only occurs inside (model ...)
        xyz = child(node, "xyz")
        if xyz:
            return [Symbol("rotate"),
                    [Symbol("xyz"), num(xyz[1]), num(xyz[2]), (num(xyz[3]) + 180) % 360]]
        return node
    return [flip_to_back(c) for c in node]


def place_footprint(fp_path: Path, libid: str, ref: str, value: str,
                    x: float, y: float, rot: float, pad_nets: dict,
                    sym_uuid: str, sheetfile: str, flip: bool = False):
    """Read a .kicad_mod and return a PCB `(footprint ...)` node placed at
    (x, y, rot), with `ref`/`value`, a schematic link, and a net on each pad
    whose number appears in `pad_nets` (pads absent from the map stay netless).
    `flip=True` places it on the bottom side (pins pointing down)."""
    data = parse(fp_path.read_text())  # (footprint "<name>" ...)
    th = math.radians(rot)

    out = [Symbol("footprint"), libid,
           parse('(layer "F.Cu")'),
           parse(f'(uuid "{newid()}")'),
           parse(f'(at {x} {y} {rot})')]

    for c in data[2:]:
        tag = head(c)
        if tag in ("version", "generator", "layer"):
            continue
        if tag == "property" and len(c) >= 3 and c[1] in ("Reference", "Value"):
            node = list(c)
            node[2] = ref if c[1] == "Reference" else value
            if not child(node, "uuid"):
                node.append(parse(f'(uuid "{newid()}")'))
            out.append(node)
        elif tag == "fp_text" and len(c) >= 3 and isinstance(c[1], Symbol) \
                and c[1].value() in ("reference", "value"):
            # Older footprints (e.g. the Espressif modules) carry their ref/value
            # as (fp_text reference ...) rather than (property ...).
            node = list(c)
            node[2] = ref if c[1].value() == "reference" else value
            out.append(node)
        elif tag == "pad":
            node = list(c)
            net = pad_nets.get(str(c[1]))
            if net:
                node.append([Symbol("net"), net])
            out.append(node)
        elif tag == "zone":
            out.append(transform_xy_points(c, x, y, th))
        else:
            out.append(c)

    out.append(parse(f'(path "/{sym_uuid}")'))
    out.append(parse('(sheetname "/")'))
    out.append(parse(f'(sheetfile "{sheetfile}")'))
    return flip_to_back(out) if flip else out


def gr_rect(left, top, right, bottom):
    return parse(
        f'(gr_rect (start {left} {top}) (end {right} {bottom}) '
        f'(stroke (width {EDGE_WIDTH}) (type solid)) (fill none) '
        f'(layer "Edge.Cuts") (uuid "{newid()}"))')


def pin_label(text, x, y, justify):
    """A bottom-silk (B.SilkS) net-name label for a header pin. `justify` is
    'left'/'right' for the inward growth direction; 'mirror' makes it read
    correctly when the board is viewed from the bottom."""
    return parse(
        f'(gr_text "{text}" (at {x} {y} 0) (layer "B.SilkS") (uuid "{newid()}") '
        f'(effects (font (size {LABEL_FONT} {LABEL_FONT}) (thickness 0.15)) '
        f'(justify {justify} mirror)))')


# ---- main entry ------------------------------------------------------------

def existing_footprints_bbox(pcb_tree):
    """Per-ref global courtyard bbox for every footprint already in the PCB."""
    out = {}
    for fp in collect(pcb_tree, "footprint", []):
        at = child(fp, "at")
        ox, oy = num(at[1]), num(at[2])
        rot = num(at[3]) if len(at) > 3 else 0.0
        th = math.radians(rot)
        ref = None
        for pr in collect(fp, "property", []):
            if len(pr) >= 3 and pr[1] == "Reference":
                ref = pr[2]
        bb = courtyard_bbox_local(fp)
        if not bb:
            continue
        xs, ys = [], []
        for lx, ly in ((bb[0], bb[2]), (bb[1], bb[2]), (bb[0], bb[3]), (bb[1], bb[3])):
            xs.append(ox + lx * math.cos(th) - ly * math.sin(th))
            ys.append(oy + lx * math.sin(th) + ly * math.cos(th))
        out[ref] = (min(xs), max(xs), min(ys), max(ys))
    return out


def build_pcb(pcb_path: Path, module: str, fp_path: Path, fp_libid: str,
              module_pad_nets: dict, module_uuid: str, headers: list,
              sheetfile: str):
    """Add the module + headers + board outline to an existing PCB file.

    headers: list of dicts {ref, side('left'|'right'), nets[top..bottom], uuid}.
    module_pad_nets: {pad_number(str): net_name}. module_uuid + each header's
    uuid link the footprint back to its schematic symbol.
    """
    lib = json.loads((REPO / "library.json").read_text())
    hdr_dir = Path(lib["kicad_symbols_dir"]).parent / "footprints" / "Connector_PinHeader_2.54mm.pretty"

    tree = sexpdata.loads(pcb_path.read_text())
    L = compute_layout(tree, fp_path, headers)   # also sets h["n"]/["y"] on each header
    center_x, mx, my = L["center_x"], L["mx"], L["my"]
    board_left, board_top = L["board_left"], L["board_top"]
    board_right, board_bottom = L["board_right"], L["board_bottom"]
    hdr_offset = L["hdr_offset"]

    def r(v):  # keep the file tidy
        return round(v, 4)

    new_nodes = []

    # module
    new_nodes.append(place_footprint(
        fp_path, fp_libid, "U1", module, r(mx), r(my), 0,
        module_pad_nets, module_uuid, sheetfile))

    # headers + bottom-silk pin labels (net name beside each pin, growing
    # inward toward the board centre — the outer side is flush with the edge)
    for h in headers:
        hx = center_x - hdr_offset if h["side"] == "left" else center_x + hdr_offset
        n = h["n"]
        hfp = hdr_dir / f"PinHeader_1x{n:02d}_P2.54mm_Vertical.kicad_mod"
        libid = f"Connector_PinHeader_2.54mm:PinHeader_1x{n:02d}_P2.54mm_Vertical"
        pad_nets = {str(i + 1): net for i, net in enumerate(h["nets"])}
        new_nodes.append(place_footprint(
            hfp, libid, h["ref"], f"Conn_01x{n:02d}", r(hx), r(h["y"]), 0,
            pad_nets, h["uuid"], sheetfile, flip=True))
        inward = 1 if h["side"] == "left" else -1
        # B.SilkS text is mirrored, which flips horizontal justify — so to make
        # the label grow *inward* (away from the pad), the justify is the
        # opposite of the inward screen direction.
        justify = "right" if h["side"] == "left" else "left"
        for i, net in enumerate(h["nets"]):
            lx = hx + inward * LABEL_OFFSET
            ly = h["y"] + i * HDR_PITCH
            new_nodes.append(pin_label(net, r(lx), r(ly), justify))

    # board outline
    new_nodes.append(gr_rect(r(board_left), r(board_top), r(board_right), r(board_bottom)))

    # insert before the final ")" of the (kicad_pcb ...) node
    text = pcb_path.read_text().rstrip()
    assert text.endswith(")"), "unexpected PCB file ending"
    blocks = "\n".join("\t" + sexpdata.dumps(n) for n in new_nodes)
    pcb_path.write_text(text[:-1] + blocks + "\n)\n")

    return {
        "center_x": r(center_x), "board": (r(board_left), r(board_top),
                                            r(board_right), r(board_bottom)),
        "module_at": (r(mx), r(my)), "header_top": r(L["header_top"]),
        "comp_top": r(L["comp_top"]), "tht_bottom": r(L["tht_bottom"]),
    }


def compute_layout(tree, fp_path: Path, headers: list) -> dict:
    """Deterministic placement geometry for a board: where the module + headers
    + outline go, given the skeleton PCB (`tree`, before the module is added),
    the module footprint, and the headers (each {side, nets}). Mutates each
    header dict with n/y/top. Pure geometry — no file writes — so the
    builtin-LED picker can reuse it to find the module's placed pad positions.
    build_pcb() calls this and then emits the footprints/outline."""
    boxes = existing_footprints_bbox(tree)
    if "J1" not in boxes:
        raise RuntimeError("USB socket J1 not found in PCB — cannot centre the board")
    j1_node = next(fp for fp in collect(tree, "footprint", [])
                   if any(len(pr) >= 3 and pr[1] == "Reference" and pr[2] == "J1"
                          for pr in collect(fp, "property", [])))

    usb = boxes["J1"]
    center_x = (usb[0] + usb[1]) / 2.0
    comp_top = min(b[2] for b in boxes.values())

    # bottom board edge: 0.5 mm below the USB socket's lowest THT pad copper, so
    # the pads clear the edge while the socket body overhangs it. Headers' bottoms
    # sit on this edge.
    tht_bottom = tht_copper_bottom(j1_node)
    board_bottom = tht_bottom + EDGE_CLEAR

    usb_edge = board_bottom
    def hdr_height(n):
        return (n - 1) * HDR_PITCH + HDR_CRT_HALF - HDR_CRT_TOP   # courtyard height

    # --- module geometry + placement (UNCHANGED from the original: the module is
    # placed against the taller header's bottom-aligned top, pushed up to clear the
    # baseline components — moving it shifts the antenna keepout onto the pour). ---
    for h in headers:
        h["n"] = len(h["nets"])
    header_top0 = min(usb_edge - hdr_height(h["n"]) for h in headers)  # bottom-aligned top

    fp_node = parse(fp_path.read_text())
    crt = courtyard_bbox_local(fp_node) or body_bbox_local(fp_node)
    crt_minx, crt_maxx, crt_miny, crt_maxy = crt
    pad_min_y, _ = pad_y_range(fp_node)
    divider = antenna_divider_local(fp_node, pad_min_y)  # body top, local
    crt_cx = (crt_minx + crt_maxx) / 2.0
    pad_top = pad_copper_top_local(fp_node)

    mx = center_x - crt_cx                  # centre the module courtyard
    my = header_top0 - divider              # align body top with the header tops
    if my + crt_maxy > comp_top - COMP_GAP:  # would break the component gap -> push up
        my = (comp_top - COMP_GAP) - crt_maxy
    module_top = my + min(divider, pad_top - EDGE_CLEAR)

    # --- header origins: align each header's courtyard TOP with the module top so
    # its pins overlap the module's pin rows (short break-out traces). BUT never
    # drag the header bottom past the USB edge — a header LONGER than the
    # module->USB span stays bottom-aligned to the USB and the board grows UP to
    # fit it (so a long header isn't stranded far below everything). ---
    for h in headers:
        ctop = min(module_top, usb_edge - hdr_height(h["n"]))   # courtyard top, global
        h["y"] = ctop - HDR_CRT_TOP                             # pad-1 origin
        h["top"] = ctop
    header_top = min(h["top"] for h in headers)
    board_top = min(module_top, header_top)                # grow up for a long header
    board_bottom = max(usb_edge,
                       max(h["y"] + (h["n"] - 1) * HDR_PITCH + HDR_CRT_HALF for h in headers))

    # --- header x: clear the widest obstacle (components or module), centred ---
    mod_minx, mod_maxx = mx + crt_minx, mx + crt_maxx
    comp_minx = min(b[0] for b in boxes.values())
    comp_maxx = max(b[1] for b in boxes.values())
    obstacle_half = max(center_x - min(comp_minx, mod_minx),
                        max(comp_maxx, mod_maxx) - center_x)
    hdr_offset = obstacle_half + HDR_INNER_CLEAR + HDR_CRT_HALF
    board_half = hdr_offset + HDR_CRT_HALF + BOARD_MARGIN
    board_left, board_right = center_x - board_half, center_x + board_half

    return {
        "center_x": center_x, "mx": mx, "my": my, "hdr_offset": hdr_offset,
        "board_left": board_left, "board_top": board_top,
        "board_right": board_right, "board_bottom": board_bottom,
        "header_top": header_top, "comp_top": comp_top, "tht_bottom": tht_bottom,
    }


def net_pad_global(tree, net_name: str):
    """Global (x, y) of the first pad carrying `net_name` in a PCB tree (pad
    local coords transformed by its footprint's placement), or None. Used to
    locate the on-board LED's pad so the builtin-LED picker can route it to the
    nearest module pad."""
    for fp in collect(tree, "footprint", []):
        at = child(fp, "at")
        ox, oy = num(at[1]), num(at[2])
        fth = math.radians(num(at[3]) if len(at) > 3 else 0.0)
        for p in collect(fp, "pad", []):
            netc = child(p, "net")
            if netc and str(netc[-1]) == net_name:
                pat = child(p, "at")
                px, py = num(pat[1]), num(pat[2])
                return (ox + px * math.cos(fth) - py * math.sin(fth),
                        oy + px * math.sin(fth) + py * math.cos(fth))
    return None
