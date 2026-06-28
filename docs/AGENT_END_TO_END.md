# Agent Task: Curate board definitions, then pilot-verify the pipeline

The deterministic generator **and** router already exist — you RUN them, you do
not reinvent them. Your real job is the **judgement layer**: populate every
module's `board.yaml` (the per-module decisions the generator can't derive). Then
prove the recipe still works with **one end-to-end pilot** before the bulk
build + route, which is pure mechanics handled by our scripts.

Flow: **curate every `board.yaml` → pilot-verify one module → bulk build/route.**

## Read first
1. `DECISIONS.md` — the project spec (toolchain, header policy, validation, the
   `board.yaml` schema). Authoritative; follow it.
2. `baseline-left-en/baseline.kicad_sch` — the skeleton (for reference only).

## Hard rules
- Python only via `uv run python`. Env has kicad-skip, sexpdata, pyyaml.
- Use the scripts in `scripts/` — never hand-edit a schematic or PCB.
- `kicad-cli` is not on PATH; its path is in `library.json` (after Phase 0).
- Never modify `baseline-left-en/` or `baseline-right-en/` (the two skeleton
  variants; the generator auto-picks one per module by EN-pin side).

## Phase 0 — setup (once per machine)
- `uv run python scripts/resolve_library.py`   → writes `library.json`

## Phase 1 — curate every module's `board.yaml`  ← YOUR MAIN JOB

For **each** module dir under `modules/<M>/`:

1. Ensure pin data: `uv run python scripts/lib/extract_pinout.py "<M>"`
   → writes `modules/<M>/pinout.json`.
2. Fetch the module's atomic14 reference page
   (`https://www.atomic14.com/esp32/modules/<slug>/`, trailing slash) and use its
   **"do not use" / reserved / strapping / special-role** pin info.
3. Write `modules/<M>/board.yaml` per the DECISIONS.md schema:
   - **`do_not_break_out`** — pins that must NOT be exposed (internal flash/PSRAM,
     genuinely reserved). Strapping / JTAG / UART-console / USB pins are STILL
     broken out (normal for a dev board) — note them, don't exclude them. USB
     D+/D- are auto-routed by the generator and never broken out.
   - **`strapping` / `input_only`** — the SoC's GPIO numbers (drive LED safety).
   - **`builtin_led`** — the safe (I/O-capable, non-strapping) GPIO whose pad sits
     physically nearest the on-board LED. Get the suggestion from
     `build_board.pick_builtin_led()`; the build hard-errors on an unsafe choice.
   - **`boot`** — the download/boot strapping GPIO the BOOT button pulls low:
     GPIO0 on Xtensa (ESP32 / -S2 / -S3), GPIO9 on most RISC-V (-C3 / -C6 / -H2),
     GPIO28 on -C5. Confirm from the datasheet / atomic14 page.
   - **`notes`** — record the data source and any caveats.

   You are **only authoring definitions** here — not building or routing.

## Phase 2 — pilot-verify ONE module end-to-end

Pick a representative module (a dense MINI is the best stress test, e.g.
`ESP32-S3-MINI-1`) and run the full pipeline to confirm the recipe + your data:

- `uv run python scripts/lib/build_board.py "<M>"`
- `uv run python scripts/lib/validate.py out/<M>/<M>.kicad_sch`
  — then **Read the printed PDF** and check the module, power/USB/boot wiring, and
  both headers. Gate: **no new ERROR-severity violations** (script exits 0). New
  *warnings* are expected (e.g. the `multiple_net_names` from the LED/BOOT alias);
  **do NOT loop trying to drive warnings to zero.**
- `uv run python scripts/lib/route_board.py "<M>"` — expect
  **ALL NETS FULLY CONNECTED** and a clean DRC.

If a failure is a generator/router bug rather than a data issue, **report it** —
do not hack the output by hand.

## Phase 3 — bulk build + route (mechanical; scripts, not the agent)

Once the definitions are curated and the pilot is clean, every module is built and
routed by our scripts — no agent judgement involved:

- `uv run python scripts/make.py`   — clean → `build_all` (build + ERC) →
  `route_all` (autoroute + DRC); add `--render` for the 3D montages.

Run it yourself / let CI run it. Per-board details:
`build_all.py` (build + validate all), `route_all.py` (route + DRC all).

## Deliverables (final message)
1. Every module's `board.yaml`, with a one-line `do_not_break_out` justification
   per module (citing the atomic14 page).
2. Pilot result: the module verified, `validate.py` exit 0, route fully connected.
3. ERC delta summary for the pilot (resolved / new warnings).
4. **Process friction report**: anything in the recipe that was unclear, missing,
   or wrong first time — this is what we refine before trusting the bulk run. If
   it all worked cleanly, say so.
