# ESP Dev-Board Generator

Generates canonical "basic dev board" KiCad schematics for the Espressif modules
that have a **PCB antenna + native USB** (12 modules across S2/S3/C3/C5/C6/H2).
Each board takes a shared skeleton (USB-C, 3V3 LDO, BOOT/EN buttons, LEDs) and
adds the module symbol + two break-out pin headers, wired entirely with net
labels (no routing). Output is a complete, openable KiCad project per module.

> **Why / design rationale:** see [`DECISIONS.md`](DECISIONS.md). This file is
> the *operational* guide; DECISIONS.md is the *why*.

## Prerequisites (per machine)

1. **KiCad 10.x** installed (the schematics use the v10 file format).
2. **Espressif KiCad libraries** installed via KiCad's **Plug-in & Content
   Manager** (Manage Plugins → install "Espressif"). This provides the
   `PCM_Espressif` symbol/footprint libraries. *This is the one manual install
   step and must be done before running anything.*
3. **[uv](https://docs.astral.sh/uv/)** (Python package manager).
4. Internet access to `https://www.atomic14.com/esp32/` (only needed when
   curating a new module's `board.yaml`).

`resolve_library.py` auto-locates KiCad on macOS / Linux / Windows. If it can't,
set `ESPRESSIF_3RDPARTY` (the `<kicad>/3rdparty` dir) and/or `KICAD_CLI`
(path to `kicad-cli`) and re-run it.

## Quick start

```bash
uv sync                                   # restore the Python env (pinned in uv.lock)
uv run python scripts/resolve_library.py  # detect KiCad/libraries -> library.json (once/machine)
uv run python scripts/make.py             # build + route every board (THE command)
```

`make.py` is the one front door — it chains clean → build → route → render:

```bash
uv run python scripts/make.py            # build + route all modules
uv run python scripts/make.py --clean    # wipe prior output first
uv run python scripts/make.py --render    # also render the 3D montages
uv run python scripts/make.py --all       # clean + build + route + render
uv run python scripts/make.py --no-route  # build only
```

Generated boards land in `modules/<MODULE>/<MODULE>.kicad_sch` (+ `.kicad_pro`,
`.kicad_pcb`); validation artifacts (ERC JSON, PDF render) in `build/<MODULE>/`.
Open any `modules/<MODULE>/<MODULE>.kicad_pro` in KiCad.

## Repository layout

```
DECISIONS.md            design decisions + rationale (read for the "why")
library.json            GENERATED per machine by resolve_library.py (git-ignore-able)
pyproject.toml/uv.lock  pinned Python env (kicad-skip, sexpdata, pyyaml)
baseline-left-en/       skeleton KiCad project (INPUT — never edit); EN button on the left
baseline-right-en/      same skeleton mirrored, EN on the right — generator auto-picks per module
  BasicEsp32Footprints.pretty/, 3d-models/, fp-lib-table   project-local footprints + 3D models
scripts/                orchestrators (run these); scripts/lib/ holds the per-module primitives
modules/<MODULE>/
  board.yaml            CURATED input — the only hand-authored file (see below)
  pinout.json           GENERATED from the symbol (never hand-edit)
  <MODULE>.kicad_sch/pro/pcb   GENERATED board
  fp-lib-table + *.pretty/ + asset dirs   GENERATED (copied from baseline)
build/<MODULE>/         GENERATED validation artifacts
docs/AGENT_END_TO_END.md  agent spec: curate every board.yaml, then pilot-verify one module
```

## The pipeline (`scripts/`)

**Orchestrators** — the top-level commands you run:

| Script | Role | Deterministic? |
|---|---|---|
| `make.py` | **THE command** — chains clean → build → route → render (flags toggle stages) | ✅ |
| `resolve_library.py` | Find KiCad libs + `kicad-cli` → `library.json` | ✅ run once/machine |
| `build_all.py [--clean]` | extract → build → validate every curated module | ✅ |
| `route_all.py [--no-diff]` | autoroute every board (diff-pair with single-ended fallback; writes back in place) + DRC | ✅ |
| `render_boards.py` | 3D montages of every board → `build/` | ✅ |
| `clean.py` | remove generated output for a fresh run (keeps `board.yaml`/`pinout.json`/`library.json`; leaves any non-pipeline files, e.g. manual `*_routed.*`, alone) | ✅ |

**Primitives** (`scripts/lib/`) — operate on one module; called by the orchestrators, rarely run directly:

| Script | Role |
|---|---|
| `extract_pinout.py "<MODULE>"` | Parse the symbol → `modules/<m>/pinout.json` |
| `footprint_edges.py "<MODULE>"` | Physical pin edges from the footprint |
| `build_board.py "<MODULE>"` | **The generator**: module + perimeter-split headers + labels + footprints + project files (copies the baseline's `fp-lib-table` and all asset dirs — `*.pretty` footprint libs, `3d-models/`, etc.) |
| `route_board.py "<MODULE>"` | Autoroute one board (USB-C fine neck, signals, GND pour) |
| `place_pcb.py` / `gnd_finish.py` | PCB placement + GND-pour fill/stitch helpers |
| `validate.py <sch>` | ERC delta vs baseline + PDF render |

**~95% of the work is fully scripted.** The only step needing human/agent
judgement is curating each module's `board.yaml`.

## Adding / re-curating a module

`board.yaml` is the one hand-authored file. Most wiring is auto-derived (power,
GND, EN, USB→D±, GPIO→`GPIOxx` labels). You decide:

- **`do_not_break_out`**: pins that must NOT be exposed — internal flash/PSRAM
  and any pin the [atomic14 module page](https://www.atomic14.com/esp32/modules/)
  marks as a hard "do not use". Strapping / JTAG / UART / USB pins are still
  broken out (documented in `notes`, not excluded).
- **`strapping` / `input_only`**: GPIO numbers (curated from the atomic14 page /
  datasheet) that are sampled at reset or have no output driver. They're *still*
  broken out — these lists only steer the on-board LED away from unsafe pins.
- **`builtin_led`**: the GPIO the skeleton's on-board LED attaches to. The
  generator routes it to the **safe pad physically nearest the LED** on the laid-out
  PCB (shortest trace); `build_board.py pick_builtin_led()` computes exactly that,
  and the build **hard-errors** if `builtin_led` names a strapping / input-only /
  non-broken-out pin. "Safe" = I/O-capable, non-strapping, broken-out GPIO.
- **`boot`**: the download/boot strapping GPIO the on-board BOOT button pulls low —
  GPIO0 on Xtensa (ESP32/-S2/-S3), GPIO9 on most RISC-V (-C3/-C6/-H2), GPIO28 on
  -C5. Varies per module, so the generator aliases the skeleton's `BOOT` net onto it.

```yaml
module: ESP32-S3-WROOM-1
symbol: PCM_Espressif:ESP32-S3-WROOM-1
do_not_break_out: [SPIDQS, SPIIO6, SPIIO7]   # octal flash/PSRAM, must not expose
overrides: {}
strapping: [0, 3, 45, 46]   # sampled at reset — unsafe for the LED (GPIO45 = VDD_SPI)
input_only: []              # no output driver
builtin_led: GPIO48         # safe GPIO pad nearest the on-board LED (NOT GPIO45, a strap)
boot: GPIO0                 # download/boot strapping pin the BOOT button pulls low
notes: >
  Source: https://www.atomic14.com/esp32/modules/esp32-s3-wroom-1/ ...
```


> ⚠️ Curation is **symbol-specific, not name-based**: e.g. `SPIIO6/7`/`SPIDQS`
> are reserved on the octal S3-WROOM-1, but are *freely-usable GPIO* on the quad
> S3-MINI-1 (and are NC, so absent, on the WROOM-2). Check the atomic14 page;
> don't blindly exclude by name.

Then: `uv run python scripts/lib/build_board.py "<MODULE>"` and
`uv run python scripts/lib/validate.py modules/<m>/<m>.kicad_sch`, and **open the
rendered PDF** — the visual check catches things ERC can't (it once passed a
board whose USB pins were unconnected).

## Validation

`validate.py` gate = **no new ERROR-severity ERC violations vs the baseline**.
New *warnings* are expected and fine (notably the `pin_to_pin` from connecting
`GPIO0` to the boot button — the EasyEDA button symbol uses `Unspecified` pin
types). The baseline itself has ~58 pre-existing violations, so the bar is the
*delta*, not zero. Always also eyeball the PDF render.

## Handoff notes

To pass this on: ship the whole directory **except `.venv/`** (regenerated by
`uv sync`) and `library.json`/`build/` (regenerated per machine). The recipient
runs the three Quick-start commands. The curated `board.yaml` files are the
valuable hand-work and are included.
