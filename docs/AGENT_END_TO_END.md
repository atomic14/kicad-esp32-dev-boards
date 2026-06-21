# Agent Task: Clean-Slate Pilot — full pipeline for one module

You are running the **entire documented pipeline from scratch** for one Espressif
module, to prove the process is reproducible and agent-runnable before we fan out
to all modules. A deterministic generator already exists — you RUN it, you do not
reinvent it. The valuable output is a validated board **plus a short report of any
friction** in the documented process.

**Target module for this pilot: `ESP32-S2-MINI-1`.**

## Read first
1. `DECISIONS.md` — the full project spec (toolchain, header policy, validation,
   the `board.yaml` schema). This is authoritative; follow it.
2. `baseline/baseline.kicad_sch` — the skeleton (never edit it).

## Hard rules
- Python only via `uv run python`. Env has kicad-skip, sexpdata, pyyaml.
- Use the existing scripts in `scripts/` — do not hand-edit schematics.
- `kicad-cli` is not on PATH; its path is in `library.json` (after step 1).
- Never modify `baseline/`.

## Steps

1. **Regenerate machine state** (it was wiped for this clean-slate run):
   - `uv run python scripts/resolve_library.py`        → writes `library.json`
   - `uv run python scripts/extract_pinout.py "ESP32-S2-MINI-1"` → writes
     `modules/ESP32-S2-MINI-1/pinout.json`

2. **Curate `modules/ESP32-S2-MINI-1/board.yaml`** using the module reference
   data at **https://www.atomic14.com/esp32/modules/esp32-s2-mini-1/** (note the
   trailing slash). Fetch that page and use it — especially the **"do not use" /
   reserved / strapping / special-role pins** — to decide `do_not_break_out`:
   - `do_not_break_out` = pins that must NOT be exposed: internal flash/PSRAM
     pins and any pin the page marks genuinely reserved / do-not-use. (For
     S2-MINI-1 specifically the SPI pins are usable and there are likely NO hard
     flash exclusions — if so, leave the list empty and say why.)
   - Strapping / JTAG / UART-console / USB pins are **still broken out** (that's
     normal for a dev board) — capture them in `notes`, don't exclude them.
   - USB D+/D- pins are auto-routed by the generator; they aren't broken out
     regardless.
   Follow the `board.yaml` schema in DECISIONS.md (module, symbol,
   do_not_break_out, overrides, notes). Record the data source in `notes`.

3. **Generate**: `uv run python scripts/build_board.py "ESP32-S2-MINI-1"`

4. **Validate + screenshot** (iterate until good):
   `uv run python scripts/validate.py modules/ESP32-S2-MINI-1/ESP32-S2-MINI-1.kicad_sch`
   then **Read the printed PDF** and check the module, power/USB/boot wiring, and
   both headers visually.
   - Gate: **no new ERROR-severity violations** (script exits 0). New *warnings*
     are expected and acceptable (e.g. the `pin_to_pin` from GPIO0→boot button).
     **Do NOT loop trying to drive warnings to zero.**
   - If something is genuinely wrong (errors, or the render looks broken), and
     it's a generator bug rather than a data issue, report it — do not hack the
     output by hand.

## Deliverables (final message)
1. Path to the generated schematic + confirmation `validate.py` exits 0.
2. The `board.yaml` you wrote and a one-line justification of `do_not_break_out`
   (citing the atomic14 page).
3. ERC delta summary (resolved / new warnings).
4. **Process friction report**: anything in the documented recipe that was
   unclear, missing, or didn't work first time — this is what we refine before
   fanning out to the other modules. If it all worked cleanly, say so.
