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
uv run python scripts/resolve_library.py  # detect KiCad/libraries -> library.json
uv run python scripts/build_all.py        # build + validate all 12 boards
```

Generated boards land in `modules/<MODULE>/<MODULE>.kicad_sch` (+ `.kicad_pro`,
`.kicad_pcb`); validation artifacts (ERC JSON, PDF render) in `build/<MODULE>/`.
Open any `modules/<MODULE>/<MODULE>.kicad_pro` in KiCad.

## Repository layout

```
DECISIONS.md            design decisions + rationale (read for the "why")
library.json            GENERATED per machine by resolve_library.py (git-ignore-able)
pyproject.toml/uv.lock  pinned Python env (kicad-skip, sexpdata, pyyaml)
baseline/               the skeleton KiCad project (INPUT — never edit)
  BasicEsp32Footprints.pretty/, 3d-models/, fp-lib-table   project-local footprints + 3D models
scripts/                the pipeline (see below)
modules/<MODULE>/
  board.yaml            CURATED input — the only hand-authored file (see below)
  pinout.json           GENERATED from the symbol (never hand-edit)
  <MODULE>.kicad_sch/pro/pcb   GENERATED board
  fp-lib-table + *.pretty/ + asset dirs   GENERATED (copied from baseline)
build/<MODULE>/         GENERATED validation artifacts
docs/AGENT_END_TO_END.md  spec for running one module end-to-end (incl. via an agent)
```

## The pipeline (`scripts/`)

| Script | Role | Deterministic? |
|---|---|---|
| `resolve_library.py` | Find KiCad libs + `kicad-cli` → `library.json` | ✅ run once/machine |
| `extract_pinout.py "<MODULE>"` | Parse the symbol → `modules/<m>/pinout.json` | ✅ |
| `footprint_edges.py "<MODULE>"` | Physical pin edges from the footprint (used by the generator) | ✅ |
| `build_board.py "<MODULE>"` | **The generator**: module + perimeter-split headers + labels + footprints + project files (copies the baseline's `fp-lib-table` and all asset dirs — `*.pretty` footprint libs, `3d-models/`, etc.) | ✅ |
| `validate.py <sch>` | ERC delta vs baseline + PDF render | ✅ |
| `build_all.py` | Run extract→build→validate for every curated module | ✅ |

**~95% of the work is fully scripted.** The only step needing human/agent
judgement is curating each module's `board.yaml`.

## Adding / re-curating a module

`board.yaml` is the one hand-authored file. Most wiring is auto-derived (power,
GND, EN, USB→D±, GPIO→`GPIOxx` labels). You only decide **`do_not_break_out`**:
pins that must NOT be exposed — internal flash/PSRAM and any pin the
[atomic14 module page](https://www.atomic14.com/esp32/modules/) marks as a hard
"do not use". Strapping / JTAG / UART / USB pins are still broken out (documented
in `notes`, not excluded).

```yaml
module: ESP32-S3-WROOM-1
symbol: PCM_Espressif:ESP32-S3-WROOM-1
do_not_break_out: [SPIDQS, SPIIO6, SPIIO7]   # octal flash/PSRAM, must not expose
overrides: {}
notes: >
  Source: https://www.atomic14.com/esp32/modules/esp32-s3-wroom-1/ ...
```

> ⚠️ Curation is **symbol-specific, not name-based**: e.g. `SPIIO6/7`/`SPIDQS`
> are reserved on the octal S3-WROOM-1, but are *freely-usable GPIO* on the quad
> S3-MINI-1 (and are NC, so absent, on the WROOM-2). Check the atomic14 page;
> don't blindly exclude by name.

Then: `uv run python scripts/build_board.py "<MODULE>"` and
`uv run python scripts/validate.py modules/<m>/<m>.kicad_sch`, and **open the
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
