#!/usr/bin/env python3
"""Extract authoritative pin tables from the Espressif symbol library into
per-module pinout.json files.

This is the mechanical, deterministic ground truth taken straight from the
KiCad symbol. It records nothing that requires judgement (no strapping-pin,
USB-mapping or do-not-break-out decisions) — that curation lives in board.yaml.

Usage:
  extract_pinout.py --list                 # list every symbol in the library
  extract_pinout.py --candidates           # extract the target module set
  extract_pinout.py "ESP32-C3-MINI-1" ...  # extract specific symbol(s)

Output: modules/<safe-name>/pinout.json   ('/' in a symbol name -> '_')
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

import sexpdata
from sexpdata import Symbol

REPO = Path(__file__).resolve().parent.parent
LIBRARY_JSON = REPO / "library.json"
MODULES_DIR = REPO / "modules"

# Target modules: PCB antenna + native USB, and present in the library.
# (See memory esp-module-candidates / project README for the rationale.)
CANDIDATES = [
    "ESP32-S2-MINI-1", "ESP32-S2-WROOM", "ESP32-S2-WROVER",
    "ESP32-S3-MINI-1", "ESP32-S3-WROOM-1", "ESP32-S3-WROOM-2",
    "ESP32-C3-MINI-1", "ESP32-C3-WROOM-02",
    "ESP32-C5-WROOM-1",
    "ESP32-C6-WROOM-1", "ESP32-C6-MINI-1/U",
    "ESP32-H2-MINI-1",
]

# A pin's (at x y angle): the angle is the direction the pin stub points away
# from the body, so the body — and thus which edge the pin sits on — is the
# opposite side. (KiCad symbol space: +x right, +y up.)
ANGLE_TO_SIDE = {0: "left", 180: "right", 90: "bottom", 270: "top"}


def tag(node) -> str | None:
    if isinstance(node, list) and node and isinstance(node[0], Symbol):
        return node[0].value()
    return None


def find(node, name):
    """First direct child with the given tag."""
    for c in node if isinstance(node, list) else []:
        if tag(c) == name:
            return c
    return None


def collect(node, name, out):
    """All descendant nodes with the given tag (recursive)."""
    if isinstance(node, list):
        if tag(node) == name:
            out.append(node)
        else:
            for c in node:
                collect(c, name, out)
    return out


def load_symbols(path: Path) -> dict:
    data = sexpdata.loads(path.read_text())
    return {s[1]: s for s in data if tag(s) == "symbol" and isinstance(s[1], str)}


def prop(sym, key, default=None):
    for p in sym:
        if tag(p) == "property" and len(p) >= 3 and p[1] == key:
            return p[2]
    return default


def gpio_number(name: str):
    """The pin's canonical GPIO number, taken from its ``GPIOxx`` alt-function
    token. Tokens are slash-separated; we must match a *token* rather than
    search the whole string, or names like ``SPIIO4/GPIO33/...`` wrongly yield 4
    (the ``IO`` inside ``SPIIO4``) instead of 33. A few parts use a bare
    ``IOxx`` token, kept as a fallback."""
    for tok in name.split("/"):
        m = re.match(r"GPIO(\d+)", tok)
        if m:
            return int(m.group(1))
    for tok in name.split("/"):
        m = re.match(r"IO(\d+)", tok)
        if m:
            return int(m.group(1))
    return None


def parse_pin(pin) -> dict:
    etype = pin[1].value()          # power_in / bidirectional / input / passive / no_connect ...
    at = find(pin, "at")
    x, y, angle = (at[1], at[2], int(at[3])) if at else (None, None, None)
    name = find(pin, "name")
    number = find(pin, "number")
    name = name[1] if name else ""
    number = number[1] if number else ""
    return {
        "number": str(number),
        "name": name,
        "etype": etype,
        "functions": name.split("/") if name not in ("", "NC") else [],
        "gpio": gpio_number(name),
        "is_power": etype in ("power_in", "power_out"),
        "is_gnd": name == "GND",
        "is_nc": etype == "no_connect" or name == "NC",
        "x": x, "y": y, "angle": angle,
        "side": ANGLE_TO_SIDE.get(angle),
    }


def extract(sym) -> dict:
    name = sym[1]
    pins_raw = collect(sym, "pin", [])
    pins = [parse_pin(p) for p in pins_raw]
    # Stable, human-friendly order: by edge, then position along that edge.
    side_order = {"left": 0, "right": 1, "top": 2, "bottom": 3, None: 4}
    pins.sort(key=lambda p: (
        side_order[p["side"]],
        -(p["y"] or 0) if p["side"] in ("left", "right") else (p["x"] or 0),
    ))
    gpios = sorted({p["gpio"] for p in pins if p["gpio"] is not None})
    return {
        "symbol": name,
        "footprint": prop(sym, "Footprint"),
        "datasheet": prop(sym, "Datasheet"),
        "description": prop(sym, "Description"),
        "pin_count": len(pins),
        "gpio_numbers": gpios,
        "pins": pins,
    }


def safe_name(symbol_name: str) -> str:
    return symbol_name.replace("/", "_")


def main(argv: list[str]) -> int:
    if not LIBRARY_JSON.exists():
        print("ERROR: library.json missing — run resolve_library.py first.", file=sys.stderr)
        return 1
    lib = json.loads(LIBRARY_JSON.read_text())
    symbols = load_symbols(Path(lib["symbol_lib"]))

    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] == "--list":
        for n in sorted(symbols):
            print(n)
        return 0
    targets = CANDIDATES if argv[0] == "--candidates" else argv

    missing = [t for t in targets if t not in symbols]
    if missing:
        print(f"ERROR: symbols not in library: {missing}", file=sys.stderr)
        return 1

    MODULES_DIR.mkdir(exist_ok=True)
    for name in targets:
        info = extract(symbols[name])
        d = MODULES_DIR / safe_name(name)
        d.mkdir(exist_ok=True)
        (d / "pinout.json").write_text(json.dumps(info, indent=2) + "\n")
        gp = info["gpio_numbers"]
        print(f"{name}: {info['pin_count']} pins, "
              f"{len(gp)} GPIO{f' ({min(gp)}..{max(gp)})' if gp else ''} "
              f"-> {d.relative_to(REPO)}/pinout.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
