#!/usr/bin/env python3
"""Generate a canonical dev-board schematic for one Espressif module.

Approach A (deterministic): clone the baseline skeleton, embed the module +
connector symbol definitions into lib_symbols, place the module, attach net
labels / power symbols to its pins (wiring via labels — no routing), and break
out the usable GPIO across two headers using the perimeter-walk split.

Construction is done with sexpdata (full control over node creation); validity
is proven by scripts/validate.py (kicad-cli ERC + render).

Usage:
  build_board.py "ESP32-C3-MINI-1" [--out PATH]

Reads modules/<m>/board.yaml if present (do_not_break_out, overrides).
"""
from __future__ import annotations
import argparse
import json
import re
import shutil
import uuid as uuidlib
from pathlib import Path

import sexpdata
from sexpdata import Symbol

import footprint_edges  # sibling script
import place_pcb  # sibling script

REPO = Path(__file__).resolve().parent.parent
LIBRARY = json.loads((REPO / "library.json").read_text())
ROOT_UUID = "b23045b8-0ca8-49c8-a4de-e20269e6d669"  # baseline schematic root uuid
PROJECT = "baseline"
GRID = 1.27  # KiCad schematic connection grid (mm)
HEADER_OFFSET = 76.2  # x-distance from module centre to each header (well clear)


def snap(v: float) -> float:
    return round(round(v / GRID) * GRID, 4)

# ---- net mapping (auto-derived from pin names) -----------------------------

def net_for(name: str):
    """Return the fixed baseline net a pin connects to, or None if it's a
    plain break-out signal (whose net is its own primary name)."""
    if name == "3V3":
        return "+3V3"
    if name == "GND":
        return "GND"
    # USB_D± can appear anywhere in the alt-function name (start on S2, end on
    # C3/S3), so match as a substring, not endswith.
    if "USB_D+" in name:
        return "D+"
    if "USB_D-" in name:
        return "D-"
    primary = name.split("/")[0]
    if primary in ("EN", "CHIP_PU", "CHIP", "CHIP_EN") or name.startswith("EN/"):
        return "EN"
    return None  # plain GPIO/function pin -> break out under its primary name


def primary_name(name: str) -> str:
    return name.split("/")[0]


def breakout_label(p) -> str:
    """Net label for a broken-out signal pin. Use the GPIO number (GPIOxx) when
    the pin has one — that's the canonical name users wire to — falling back to
    the primary alt-function name for the rare pin with no GPIO number."""
    if p["gpio"] is not None:
        return f"GPIO{p['gpio']}"
    return primary_name(p["name"])


def is_breakout(p, do_not: set) -> bool:
    """A usable signal pin that should appear on a header."""
    if p["is_nc"] or p["name"] in ("GND", "3V3"):
        return False
    if "USB_D" in p["name"]:           # USB pins are dedicated, not broken out
        return False
    if str(p["number"]) in do_not or primary_name(p["name"]) in do_not:
        return False
    return True


def module_pad_net(p, overrides, do_not):
    """Net for a module footprint pad — same mapping the schematic uses: power /
    USB / EN via net_for(), broken-out signals via their GPIO label, and None
    (left netless, matching the schematic's no-connect) for NC and excluded
    pins."""
    if p["is_nc"]:
        return None
    name = p["name"]
    net = overrides.get(str(p["number"])) or overrides.get(primary_name(name)) or net_for(name)
    if net:
        return net
    if is_breakout(p, do_not):
        return breakout_label(p)
    return None


# ---- sexp helpers ----------------------------------------------------------

def parse(s: str):
    return sexpdata.loads(s)


def newid() -> str:
    return str(uuidlib.uuid4())


def head(node) -> str | None:
    if isinstance(node, list) and node and isinstance(node[0], Symbol):
        return node[0].value()
    return None


def child(node, name):
    for c in node:
        if head(c) == name:
            return c
    return None


def load_lib_symbol(lib_path: Path, sym_name: str, lib_nick: str):
    """Pull a top-level (symbol "<name>" ...) out of a .kicad_sym and rename its
    head to "<nick>:<name>" for embedding in a schematic's lib_symbols."""
    data = sexpdata.loads(lib_path.read_text())
    for n in data:
        if head(n) == "symbol" and isinstance(n[1], str) and n[1] == sym_name:
            node = list(n)
            node[1] = f"{lib_nick}:{sym_name}"
            return node
    raise KeyError(f"{sym_name} not found in {lib_path}")


def sym_pins(lib_symbol_node):
    """{number: (x, y, angle)} for a lib_symbol node (pins live in sub-symbols)."""
    out = {}
    def walk(n):
        if isinstance(n, list):
            if head(n) == "pin":
                at = child(n, "at")
                num = child(n, "number")
                if at and num:
                    out[str(num[1])] = (float(at[1]), float(at[2]), int(at[3]))
            else:
                for c in n:
                    walk(c)
    walk(lib_symbol_node)
    return out


def endpoint(origin, local, rot):
    """Schematic coord of a pin given symbol origin, local pin (x,y) and symbol
    rotation. Symbol +y is up, schematic +y is down (hence the y inversion)."""
    ox, oy = origin
    px, py = local
    if rot == 0:
        return (ox + px, oy - py)
    if rot == 180:
        return (ox - px, oy + py)
    if rot == 90:
        return (ox - py, oy - px)
    if rot == 270:
        return (ox + py, oy + px)
    raise ValueError(rot)


# ---- element builders (templated strings -> sexp) --------------------------

def placed_symbol(lib_id, x, y, rot, ref, value, pins, *, hide_fields=False,
                  ref_at=None, val_at=None, footprint=""):
    rx, ry, rr = ref_at or (x, y - 5.08, 0)
    vx, vy, vr = val_at or (x, y + 5.08, 0)
    hide = "(hide yes)" if hide_fields else ""
    pin_s = " ".join(f'(pin "{n}" (uuid "{newid()}"))' for n in pins)
    return parse(f'''(symbol (lib_id "{lib_id}") (at {x} {y} {rot}) (unit 1)
        (body_style 1) (exclude_from_sim no) (in_bom yes) (on_board yes)
        (in_pos_files yes) (dnp no) (uuid "{newid()}")
        (property "Reference" "{ref}" (at {rx} {ry} {rr}) {hide} (show_name no) (do_not_autoplace no) (effects (font (size 1.27 1.27)) (justify left)))
        (property "Value" "{value}" (at {vx} {vy} {vr}) {hide} (show_name no) (do_not_autoplace no) (effects (font (size 1.27 1.27)) (justify left)))
        (property "Footprint" "{footprint}" (at {x} {y} 0) (hide yes) (show_name no) (do_not_autoplace no) (effects (font (size 1.27 1.27))))
        {pin_s}
        (instances (project "{PROJECT}" (path "/{ROOT_UUID}" (reference "{ref}") (unit 1)))))''')


def lib_prop(node, key, default=""):
    """Value of a (property "key" "value" ...) in a lib_symbol node."""
    for c in node:
        if head(c) == "property" and len(c) >= 3 and c[1] == key:
            return c[2]
    return default


def power_symbol(net, x, y, rot, ref):
    lib = {"+3V3": "power:+3V3", "+5V": "power:+5V", "GND": "power:GND"}[net]
    return parse(f'''(symbol (lib_id "{lib}") (at {x} {y} {rot}) (unit 1)
        (body_style 1) (exclude_from_sim no) (in_bom yes) (on_board yes)
        (in_pos_files yes) (dnp no) (fields_autoplaced yes) (uuid "{newid()}")
        (property "Reference" "{ref}" (at {x} {y} 0) (hide yes) (show_name no) (do_not_autoplace no) (effects (font (size 1.27 1.27))))
        (property "Value" "{net}" (at {x} {y} 0) (show_name no) (do_not_autoplace no) (effects (font (size 1.27 1.27))))
        (pin "1" (uuid "{newid()}"))
        (instances (project "{PROJECT}" (path "/{ROOT_UUID}" (reference "{ref}") (unit 1)))))''')


def global_label(text, x, y, rot, justify):
    return parse(f'''(global_label "{text}" (shape input) (at {x} {y} {rot})
        (fields_autoplaced yes) (effects (font (size 1.27 1.27)) (justify {justify})) (uuid "{newid()}")
        (property "Intersheetrefs" "${{INTERSHEET_REFS}}" (at {x} {y} {rot}) (hide yes) (show_name no) (do_not_autoplace no) (effects (font (size 1.27 1.27)) (justify {justify}))))''')


def no_connect(x, y):
    return parse(f'(no_connect (at {x} {y}) (uuid "{newid()}"))')


# electrical types that MUST be connected (else ERC pin_not_connected error)
SIGNAL_ETYPES = {"bidirectional", "input", "output", "tri_state", "open_collector"}


# Module-side label orientation (symbol side -> rotation, justify).
LABEL_ORIENT = {"left": (180, "right"), "right": (0, "left"),
                "top": (270, "left"), "bottom": (90, "left")}

# Power-symbol rotation so the symbol points OUTWARD (away from the module).
# Net-aware because +V graphics default to pointing up (rot 0) while GND
# defaults to pointing down (rot 0).
_OUTWARD = {"top": "up", "bottom": "down", "left": "left", "right": "right"}


def power_rot(net: str, side: str) -> int:
    outward = _OUTWARD[side]
    if net == "GND":   # base graphic points down at rot 0
        return {"down": 0, "right": 90, "up": 180, "left": 270}[outward]
    return {"up": 0, "left": 90, "down": 180, "right": 270}[outward]  # +V


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("module")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    module = args.module
    safe = module.replace("/", "_")
    mod_dir = REPO / "modules" / safe

    pinout = json.loads((mod_dir / "pinout.json").read_text())
    pins_by_num = {p["number"]: p for p in pinout["pins"]}

    # board.yaml (optional)
    do_not, overrides = set(), {}
    yml = mod_dir / "board.yaml"
    if yml.exists():
        import yaml
        cfg = yaml.safe_load(yml.read_text()) or {}
        do_not = {str(x) for x in (cfg.get("do_not_break_out") or [])}
        overrides = {str(k): v for k, v in (cfg.get("overrides") or {}).items()}

    # --- load baseline tree ---
    tree = sexpdata.loads((REPO / "baseline" / "baseline.kicad_sch").read_text())
    libsyms = child(tree, "lib_symbols")

    # --- embed lib_symbols: module + connector ---
    nick = LIBRARY["lib_nickname"]
    mod_libsym = load_lib_symbol(Path(LIBRARY["symbol_lib"]), module, nick)
    libsyms.append(mod_libsym)
    mod_pin_xy = sym_pins(mod_libsym)

    # connector lib (KiCad stock symbols, located by resolve_library.py)
    conn_lib = Path(LIBRARY["kicad_symbols_dir"]) / "Connector_Generic.kicad_sym"

    new_elements = []

    # --- place module symbol ---
    # Placed right-of-centre so the LEFT header clears the baseline blocks
    # (which occupy roughly x=19..86 on the A4 sheet).
    MX, MY = 184.15, 99.06
    mod_sym = placed_symbol(
        f"{nick}:{module}", MX, MY, 0, "U1", module,
        list(mod_pin_xy.keys()),
        ref_at=(MX - 35, MY - 2.54, 0), val_at=(MX - 35, MY, 0),
        footprint=lib_prop(mod_libsym, "Footprint"))
    new_elements.append(mod_sym)
    u1_uuid = child(mod_sym, "uuid")[1]

    # --- module-side: net label / power symbol per pin ---
    pwr_ref = 100
    for p in pinout["pins"]:
        if p["is_nc"]:
            continue
        name = p["name"]
        side = p["side"] or "right"
        ep = endpoint((MX, MY), (p["x"], p["y"]), 0)
        net = overrides.get(str(p["number"])) or overrides.get(primary_name(name)) or net_for(name)
        if net in ("+3V3", "GND"):
            # one power symbol on the power_in pins; skip passive thermal GNDs
            if p["etype"] != "power_in":
                continue
            new_elements.append(power_symbol(net, ep[0], ep[1], power_rot(net, side), f"#PWR{pwr_ref}"))
            pwr_ref += 1
        else:
            if net is None and not is_breakout(p, do_not):
                # deliberately-unconnected signal pin (e.g. excluded flash/PSRAM):
                # flag no-connect so ERC doesn't report it as unconnected.
                if p["etype"] in SIGNAL_ETYPES:
                    new_elements.append(no_connect(ep[0], ep[1]))
                continue
            text = net or breakout_label(p)
            rot, just = LABEL_ORIENT[side]
            new_elements.append(global_label(text, ep[0], ep[1], rot, just))

    # --- perimeter-walk split of break-out pins ---
    edges = footprint_edges.classify_edges(module)
    ring = edges["left"] + edges["bottom"] + list(reversed(edges["right"]))
    ring = [p for p in ring if is_breakout(p, do_not)]
    # de-dup (a pin can't be on two edges, but guard anyway), keep order
    seen, ordered = set(), []
    for p in ring:
        if p["number"] not in seen:
            seen.add(p["number"]); ordered.append(p)
    half = (len(ordered) + 1) // 2
    left_pins, right_pins = ordered[:half], ordered[half:]

    # --- build the two headers ---
    def label_net(p):
        return net_for(p["name"]) or breakout_label(p)

    def build_header(signals, header_x, ref, rail, rot, flip=False):
        # Conventional dev-board header: positive rail at the TOP (points up),
        # GPIO in the middle, GND at the BOTTOM (points down). `flip` reverses
        # the net->pin mapping top-to-bottom — used on the right header so its
        # pins line up with the module's right-edge pad order and route without
        # crossing (the perimeter walk visits that edge bottom-to-top).
        rows = [rail] + [label_net(p) for p in signals] + ["GND"]
        n = len(rows)
        conn_name = f"Conn_01x{n:02d}"
        conn_node = load_lib_symbol(conn_lib, conn_name, "Connector_Generic")
        if not any(head(s) == "symbol" and s[1] == f"Connector_Generic:{conn_name}"
                   for s in libsyms):
            libsyms.append(conn_node)
        cpins = sym_pins(conn_node)
        cx, hy = snap(header_x), snap(MY - (n - 1) * 2.54 / 2)
        fp = f"Connector_PinHeader_2.54mm:PinHeader_1x{n:02d}_P2.54mm_Vertical"
        sym = placed_symbol(f"Connector_Generic:{conn_name}", cx, hy, rot,
                            ref, conn_name, list(cpins.keys()), footprint=fp)
        out = [sym]
        # order connector pins by endpoint Y (top -> bottom), regardless of rot
        eps = {num: endpoint((cx, hy), (px, py), rot)
               for num, (px, py, _) in cpins.items()}
        order = sorted(eps, key=lambda num: eps[num][1])
        for net, num in zip(reversed(rows) if flip else rows, order):
            ex, ey = eps[num]
            if net in ("+3V3", "+5V", "GND"):
                nonlocal_ref[0] += 1
                # rot 0: KiCad draws +V pointing up, GND pointing down (convention)
                out.append(power_symbol(net, ex, ey, 0, f"#PWR{nonlocal_ref[0]}"))
            else:
                out.append(global_label(net, ex, ey, 0,
                                         "left" if ex >= cx else "right"))
        return out

    nonlocal_ref = [200]
    # Headers spread well clear of the module body + its pin labels. The right
    # header is flipped vertically (flip=True) so its pins line up with the
    # module's right-edge pad order — routes 1:1 without crossing.
    left_hdr = build_header(left_pins, MX - HEADER_OFFSET, "J2", "+3V3", rot=0)
    right_hdr = build_header(right_pins, MX + HEADER_OFFSET, "J3", "+5V", rot=180,
                             flip=True)
    new_elements += left_hdr + right_hdr

    # Header net order, top -> bottom (matches the schematic rows and the PCB
    # footprint pad order, where pad 1 is at the top): rail, signals, GND. J3 is
    # reversed to match its flipped schematic header.
    rows_left = ["+3V3"] + [label_net(p) for p in left_pins] + ["GND"]
    rows_right = list(reversed(["+5V"] + [label_net(p) for p in right_pins] + ["GND"]))
    j2_uuid = child(left_hdr[0], "uuid")[1]
    j3_uuid = child(right_hdr[0], "uuid")[1]

    # --- insert new elements before sheet_instances, then write ---
    idx = next(i for i, n in enumerate(tree) if head(n) == "sheet_instances")
    tree[idx:idx] = new_elements

    out = args.out or (mod_dir / f"{safe}.kicad_sch")
    # Rebrand the project name on every symbol instance (cloned baseline + new)
    # so the board is a coherent standalone project. "baseline" appears in the
    # schematic only as the instance project name, so this is safe.
    sch_text = sexpdata.dumps(tree).replace('"baseline"', f'"{safe}"')
    out.write_text(sch_text)

    # Emit a project file + blank board copied from the baseline project, with
    # the project basename rebranded so each module opens standalone in KiCad.
    base = REPO / "baseline"
    for ext in ("kicad_pro", "kicad_pcb", "kicad_dru"):
        src = base / f"baseline.{ext}"
        if not src.exists():
            continue
        text = src.read_text().replace("baseline", safe)
        (mod_dir / f"{safe}.{ext}").write_text(text)

    # --- lay out the PCB: place module + headers, assign nets, draw outline ---
    fp_dir = Path(LIBRARY["footprint_lib"])
    module_pad_nets = {
        str(p["number"]): n
        for p in pinout["pins"]
        if (n := module_pad_net(p, overrides, do_not))
    }
    headers = [
        {"ref": "J2", "side": "left", "nets": rows_left, "uuid": j2_uuid},
        {"ref": "J3", "side": "right", "nets": rows_right, "uuid": j3_uuid},
    ]
    geo = place_pcb.build_pcb(
        mod_dir / f"{safe}.kicad_pcb", module,
        footprint_edges.find_footprint(fp_dir, module),
        lib_prop(mod_libsym, "Footprint"),
        module_pad_nets, u1_uuid, headers, f"{safe}.kicad_sch")

    # Carry over project-local library tables + asset directories (footprint
    # libs, 3D models, ...) so the baseline's parts fully resolve. The tables
    # and footprint model paths use ${KIPRJMOD}, so they're portable to each
    # module dir verbatim. Copy every non-hidden, non-backup subdir of baseline/
    # so newly-added asset dirs are picked up automatically.
    for tbl in ("fp-lib-table", "sym-lib-table"):
        src = base / tbl
        if src.exists():
            (mod_dir / tbl).write_text(src.read_text())
    assets = [d for d in base.iterdir() if d.is_dir()
              and not d.name.startswith(".") and not d.name.endswith("-backups")]
    for d in assets:
        shutil.copytree(d, mod_dir / d.name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".DS_Store"))

    names = ", ".join(sorted(d.name for d in assets)) or "none"
    print(f"Wrote {out}  (+ .kicad_pro/.kicad_pcb/lib-tables; assets: {names}; "
          f"module pins={len(mod_pin_xy)}, breakout L={len(left_pins)} R={len(right_pins)})")
    print(f"  PCB: board={geo['board']} module@{geo['module_at']} "
          f"center_x={geo['center_x']} (header_top={geo['header_top']}, "
          f"comp_top={geo['comp_top']}, tht_bottom={geo['tht_bottom']})")


if __name__ == "__main__":
    main()
