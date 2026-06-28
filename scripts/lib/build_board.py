#!/usr/bin/env python3
"""Generate a canonical dev-board schematic for one Espressif module.

Approach A (deterministic): clone the baseline skeleton, embed the module +
connector symbol definitions into lib_symbols, place the module, attach net
labels / power symbols to its pins (wiring via labels — no routing), and break
out the usable GPIO across two headers using the perimeter-walk split.

Construction is done with sexpdata (full control over node creation); validity
is proven by scripts/lib/validate.py (kicad-cli ERC + render).

Usage:
  build_board.py "ESP32-C3-MINI-1" [--out PATH]

Reads modules/<m>/board.yaml if present (do_not_break_out, overrides).
"""
from __future__ import annotations
import argparse
import itertools
import json
import re
import shutil
import uuid as uuidlib
from pathlib import Path

import sexpdata
from sexpdata import Symbol

import footprint_edges  # sibling script
import place_pcb  # sibling script

REPO = Path(__file__).resolve().parents[2]
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


def resolve_led_pin(spec, pins, do_not, unsafe):
    """Resolve a board.yaml ``builtin_led`` value to its pin dict, or None if
    unset. The spec is a GPIO number (``9`` / ``"GPIO9"``) or a pin's primary
    name. Hard-errors if it names no pin, a pin that isn't broken out (NC /
    power / excluded), or a pin that is unsafe to drive an LED — input-only or a
    strapping pin (``unsafe`` = the GPIO numbers from board.yaml's
    ``strapping`` + ``input_only``). An on-board LED must land on a usable,
    I/O-capable, non-strapping signal pin."""
    if spec in (None, ""):
        return None
    s = str(spec).strip()
    cand = None
    m = re.match(r"(?:GPIO|IO)?(\d+)$", s, re.I)
    if m:
        g = int(m.group(1))
        cand = next((p for p in pins if p["gpio"] == g), None)
    if cand is None:
        cand = next((p for p in pins if primary_name(p["name"]).lower() == s.lower()), None)
    if cand is None:
        raise ValueError(f"builtin_led: no pin matches {spec!r}")
    if not is_breakout(cand, do_not):
        raise ValueError(
            f"builtin_led: pin {cand['name']} (#{cand['number']}) is not a "
            f"broken-out signal (NC / power / excluded) — pick a usable GPIO")
    if cand["etype"] == "input" or cand["gpio"] in unsafe:
        why = "input-only" if cand["etype"] == "input" else "a strapping pin"
        raise ValueError(
            f"builtin_led: GPIO{cand['gpio']} (pin {cand['name']}, #{cand['number']}) "
            f"is {why} — unsafe to drive an LED; pick an I/O-capable, "
            f"non-strapping pin (see this module's strapping/input_only)")
    return cand


def resolve_boot_pin(spec, pins, do_not):
    """Resolve a board.yaml ``boot`` value — the download/boot strapping GPIO the
    on-board BOOT button pulls low — to its pin dict, or None if unset. Differs
    per module (GPIO0 on the Xtensa parts, GPIO9 on most RISC-V parts, …), which
    is why it can't be a fixed net name. Unlike the LED, the boot pin IS a
    strapping pin by definition, so strapping is allowed; it only has to be a
    real, broken-out I/O pin."""
    if spec in (None, ""):
        return None
    s = str(spec).strip()
    cand = None
    m = re.match(r"(?:GPIO|IO)?(\d+)$", s, re.I)
    if m:
        cand = next((p for p in pins if p["gpio"] == int(m.group(1))), None)
    if cand is None:
        cand = next((p for p in pins if primary_name(p["name"]).lower() == s.lower()), None)
    if cand is None:
        raise ValueError(f"boot: no pin matches {spec!r}")
    if not is_breakout(cand, do_not):
        raise ValueError(
            f"boot: pin {cand['name']} (#{cand['number']}) is not a broken-out "
            f"signal (NC / power / excluded) — pick the module's boot GPIO")
    return cand


def breakout_split(module, do_not):
    """Split the broken-out signal pins between the two headers BY PHYSICAL SIDE:
    left-edge pins to the left header, right-edge pins to the right header, and
    bottom-edge pins to whichever side of the module's horizontal centre they sit
    on. Order within each header follows the perimeter walk (left edge top→bottom
    then bottom-left; bottom-right then right edge bottom→top) so header pins line
    up with the module pads without crossing.

    Previously this cut the perimeter ring at the COUNT midpoint, which — when the
    left and right edges had unequal pin counts (e.g. C5/C6-WROOM, no bottom pins)
    — pushed right-edge pins into the left header, so they routed clear across the
    board. The caller pads the shorter header with GND to keep the two symmetric."""
    edges = footprint_edges.classify_edges(module)
    bo = lambda e: [p for p in edges[e] if is_breakout(p, do_not)]
    left_e, right_e = bo("left"), bo("right")
    horiz = bo("bottom") + bo("top")          # pos == x on these edges
    cx = (min(p["pos"] for p in horiz) + max(p["pos"] for p in horiz)) / 2 if horiz else 0.0
    near_left = lambda e: [p for p in bo(e) if p["pos"] < cx]
    near_right = lambda e: [p for p in bo(e) if p["pos"] >= cx]
    left_pins = near_left("top") + left_e + near_left("bottom")
    right_pins = near_right("bottom") + list(reversed(right_e)) + near_right("top")
    return left_pins, right_pins


def baseline_dir(module: str) -> Path:
    """Pick the baseline variant whose button placement matches this module: if EN
    lands on the module's RIGHT edge, use the EN-right baseline (its EN button is
    on the right, BOOT on the left); otherwise the default EN-left baseline.
    Aligning each button with the edge its pin sits on avoids long cross-board
    button routes — the S2 family + S3-MINI (EN-right/BOOT-left) need the mirror."""
    edges = footprint_edges.classify_edges(module)
    en_right = any(net_for(p["name"]) == "EN" for p in edges["right"])
    return REPO / ("baseline-right-en" if en_right else "baseline-left-en")


SKELETON_LED_NET = "BUILTIN_LED"  # the skeleton's on-board LED net (baseline PCB)
SKELETON_BOOT_NET = "BOOT"        # the skeleton's BOOT-button net (baseline PCB)


def pick_builtin_led(module, pins, do_not, unsafe):
    """Suggest the on-board LED pin during board.yaml curation: the SAFE module
    pad physically CLOSEST to the skeleton's on-board LED on the laid-out PCB
    (shortest trace). 'Safe' = a broken-out, I/O-capable, non-strapping GPIO.
    Returns a pin dict, or None only if the module exposes no safe GPIO at all.
    Geometry mirrors place_pcb: the module footprint is placed at (mx, my) and
    the LED's pad sits at a fixed spot among the baseline components."""
    fp_dir = Path(LIBRARY["footprint_lib"])
    fp_path = footprint_edges.find_footprint(fp_dir, module)
    # header sizes drive the module's vertical placement -> mirror the build
    left_pins, right_pins = breakout_split(module, do_not)
    headers = [{"side": "left", "nets": [None] * (len(left_pins) + 2)},
               {"side": "right", "nets": [None] * (len(right_pins) + 2)}]
    tree = sexpdata.loads((baseline_dir(module) / "baseline.kicad_pcb").read_text())
    layout = place_pcb.compute_layout(tree, fp_path, headers)
    led = place_pcb.net_pad_global(tree, SKELETON_LED_NET)

    local = footprint_edges.pad_positions(fp_path)   # {pad: (x, y)} footprint-local
    by_num = {p["number"]: p for p in pins}
    best, best_d = None, None
    for pad, (lx, ly) in local.items():
        p = by_num.get(pad)
        if (p is None or p.get("gpio") is None or not is_breakout(p, do_not)
                or p["etype"] == "input" or p["gpio"] in unsafe):
            continue
        gx, gy = layout["mx"] + lx, layout["my"] + ly
        # no LED pad found -> fall back to "rightmost, lowest" (max x, then y)
        d = ((gx - led[0]) ** 2 + (gy - led[1]) ** 2) if led else (-gx, -gy)
        if best_d is None or d < best_d:
            best, best_d = p, d
    return best


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
    # Deterministic UUIDs: uuid5 over a per-process counter, so a rebuild is
    # byte-identical. Random uuid4s otherwise reshuffle the net/item order
    # route.py keys off, flipping marginal nets (e.g. D-) between builds.
    return str(uuidlib.uuid5(_UUID_NS, str(next(_uuid_seq))))


_UUID_NS = uuidlib.UUID("b1d4e7a2-0000-5000-8000-000000000001")
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


def wire(x1, y1, x2, y2):
    return parse(f'(wire (pts (xy {x1} {y1}) (xy {x2} {y2})) (stroke (width 0) (type default)) (uuid "{newid()}"))')


# Unit vector pointing AWAY from the module body, per symbol side. Used to push
# an extra net-alias label clear of the pin's primary label. (Schematic +y is
# down, so "top" is -y and "bottom" is +y.)
OUTWARD_VEC = {"left": (-1, 0), "right": (1, 0), "top": (0, -1), "bottom": (0, 1)}


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
    mod_dir = REPO / "modules" / safe        # curated SOURCE: board.yaml, pinout.json
    out_dir = REPO / "out" / safe            # GENERATED board (disposable; see clean.py)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = baseline_dir(module)   # EN-left or EN-right baseline variant

    pinout = json.loads((mod_dir / "pinout.json").read_text())
    pins_by_num = {p["number"]: p for p in pinout["pins"]}

    # board.yaml (optional)
    do_not, overrides, led_spec, boot_spec, unsafe = set(), {}, None, None, set()
    yml = mod_dir / "board.yaml"
    if yml.exists():
        import yaml
        cfg = yaml.safe_load(yml.read_text()) or {}
        do_not = {str(x) for x in (cfg.get("do_not_break_out") or [])}
        overrides = {str(k): v for k, v in (cfg.get("overrides") or {}).items()}
        led_spec = cfg.get("builtin_led")
        boot_spec = cfg.get("boot")
        # GPIO numbers unsafe to drive an LED: strapping + input-only (curated
        # per module from the atomic14 / datasheet pin data — see notes).
        unsafe = {int(x) for x in (cfg.get("strapping") or [])} | \
                 {int(x) for x in (cfg.get("input_only") or [])}

    # The pin the on-board LED (skeleton's BUILTIN_LED net) attaches to, if any.
    led_pin = resolve_led_pin(led_spec, pinout["pins"], do_not, unsafe)
    led_pin_num = led_pin["number"] if led_pin else None

    # The pin the BOOT button (skeleton's BOOT net) attaches to — per-module
    # strapping GPIO; was hard-wired to "GPIO0" before, wrong on the RISC-V parts.
    boot_pin = resolve_boot_pin(boot_spec, pinout["pins"], do_not)
    boot_pin_num = boot_pin["number"] if boot_pin else None

    # --- load baseline tree ---
    tree = sexpdata.loads((base / "baseline.kicad_sch").read_text())
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
            # On-board LED / BOOT-button pins: keep the GPIOxx label and add a
            # short outward stub carrying the skeleton net's name, so that net
            # (from the baseline LED resistor / BOOT button) joins this pin.
            for alias_net, alias_num in ((SKELETON_LED_NET, led_pin_num),
                                         (SKELETON_BOOT_NET, boot_pin_num)):
                if alias_num is not None and p["number"] == alias_num:
                    dx, dy = OUTWARD_VEC[side]
                    ax, ay = ep[0] + dx * 10.16, ep[1] + dy * 10.16
                    new_elements.append(wire(ep[0], ep[1], ax, ay))
                    new_elements.append(global_label(alias_net, ax, ay, rot, just))

    # --- perimeter-walk split of break-out pins ---
    left_pins, right_pins = breakout_split(module, do_not)

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
    # Build each header's signal list, then PAD the shorter side with GND so both
    # headers have the same pin count (symmetric board, aligned headers). Padding
    # GND sits at the bottom, next to the existing GND cap.
    sig_left = [label_net(p) for p in left_pins]
    sig_right = [label_net(p) for p in right_pins]
    npad = max(len(sig_left), len(sig_right))
    sig_left += ["GND"] * (npad - len(sig_left))
    sig_right += ["GND"] * (npad - len(sig_right))
    rows_left = ["+3V3"] + sig_left + ["GND"]
    rows_right = list(reversed(["+5V"] + sig_right + ["GND"]))
    # On the PCB, the LED pin's copper net must match the skeleton's BUILTIN_LED
    # pad (module pad + its header pad + the LED resistor = one net). The
    # schematic header label stays GPIOxx; the net just canonicalises to
    # BUILTIN_LED via the alias added at the module pin above.
    if led_pin_num is not None:
        chosen_lbl = breakout_label(led_pin)
        rows_left = ["BUILTIN_LED" if r == chosen_lbl else r for r in rows_left]
        rows_right = ["BUILTIN_LED" if r == chosen_lbl else r for r in rows_right]
    # Likewise the BOOT pin: its header/module copper joins the skeleton's BOOT
    # button net (canonicalised to BOOT via the alias added at the module pin).
    if boot_pin_num is not None:
        boot_lbl = breakout_label(boot_pin)
        rows_left = ["BOOT" if r == boot_lbl else r for r in rows_left]
        rows_right = ["BOOT" if r == boot_lbl else r for r in rows_right]
    j2_uuid = child(left_hdr[0], "uuid")[1]
    j3_uuid = child(right_hdr[0], "uuid")[1]

    # --- insert new elements before sheet_instances, then write ---
    idx = next(i for i, n in enumerate(tree) if head(n) == "sheet_instances")
    tree[idx:idx] = new_elements

    out = args.out or (out_dir / f"{safe}.kicad_sch")
    # Rebrand the project name on every symbol instance (cloned baseline + new)
    # so the board is a coherent standalone project. "baseline" appears in the
    # schematic only as the instance project name, so this is safe.
    sch_text = sexpdata.dumps(tree).replace('"baseline"', f'"{safe}"')
    out.write_text(sch_text)

    # Emit a project file + blank board copied from the baseline project, with
    # the project basename rebranded so each module opens standalone in KiCad.
    for ext in ("kicad_pro", "kicad_pcb", "kicad_dru"):
        src = base / f"baseline.{ext}"
        if not src.exists():
            continue
        text = src.read_text().replace("baseline", safe)
        (out_dir / f"{safe}.{ext}").write_text(text)

    # --- lay out the PCB: place module + headers, assign nets, draw outline ---
    fp_dir = Path(LIBRARY["footprint_lib"])
    module_pad_nets = {
        str(p["number"]): n
        for p in pinout["pins"]
        if (n := module_pad_net(p, overrides, do_not))
    }
    if led_pin_num is not None:
        module_pad_nets[str(led_pin_num)] = "BUILTIN_LED"
    if boot_pin_num is not None:
        module_pad_nets[str(boot_pin_num)] = "BOOT"
    headers = [
        {"ref": "J2", "side": "left", "nets": rows_left, "uuid": j2_uuid},
        {"ref": "J3", "side": "right", "nets": rows_right, "uuid": j3_uuid},
    ]
    geo = place_pcb.build_pcb(
        out_dir / f"{safe}.kicad_pcb", module,
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
            (out_dir / tbl).write_text(src.read_text())
    assets = [d for d in base.iterdir() if d.is_dir()
              and not d.name.startswith(".") and not d.name.endswith("-backups")]
    for d in assets:
        shutil.copytree(d, out_dir / d.name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".DS_Store"))

    names = ", ".join(sorted(d.name for d in assets)) or "none"
    print(f"Wrote {out}  (+ .kicad_pro/.kicad_pcb/lib-tables; assets: {names}; "
          f"module pins={len(mod_pin_xy)}, breakout L={len(left_pins)} R={len(right_pins)})")
    print(f"  PCB: board={geo['board']} module@{geo['module_at']} "
          f"center_x={geo['center_x']} (header_top={geo['header_top']}, "
          f"comp_top={geo['comp_top']}, tht_bottom={geo['tht_bottom']})")


if __name__ == "__main__":
    main()
