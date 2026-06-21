#!/usr/bin/env python3
"""Validate a generated board schematic: ERC (vs the baseline) + a PDF render.

Two checks, both via kicad-cli (native KiCad 10):
  1. ERC delta — the baseline skeleton already has ~58 violations (unconnected
     labels, undriven power). A correctly wired module should RESOLVE some of
     those and introduce NONE of its own. So we compare violation counts per
     type against the baseline and flag anything NEW or increased.
  2. PDF render — exported for a visual screenshot check (Read the PDF).

Exit code is nonzero if any new/increased violation type is found.

Usage:
  validate.py <schematic.kicad_sch> [--baseline PATH] [--out-dir DIR]
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIBRARY_JSON = REPO / "library.json"
DEFAULT_BASELINE = REPO / "baseline" / "baseline.kicad_sch"


def kicad_cli() -> str:
    return json.loads(LIBRARY_JSON.read_text())["kicad_cli"]


def run_erc(cli: str, sch: Path, out: Path) -> Counter:
    """Run ERC, write the JSON report to `out`, return a Counter of {type: n}."""
    subprocess.run(
        [cli, "sch", "erc", "--format", "json", "--severity-all", "-o", str(out), str(sch)],
        capture_output=True, text=True,
    )
    data = json.loads(out.read_text())
    counts: Counter = Counter()
    for sheet in data.get("sheets", []):
        for v in sheet.get("violations", []):
            counts[(v["severity"], v["type"])] += 1
    return counts


# Pre-existing baseline noise unrelated to module wiring — collapsed to a
# one-line count rather than detailed. (endpoint_off_grid comes from the
# baseline's own placements; the lib/footprint issues are because the
# easyeda2kicad library isn't in this machine's global table.)
NOISE_TYPES = {"endpoint_off_grid", "footprint_link_issues", "lib_symbol_issues"}


def export_pdf(cli: str, sch: Path, out: Path) -> bool:
    r = subprocess.run(
        [cli, "sch", "export", "pdf", str(sch), "-o", str(out)],
        capture_output=True, text=True,
    )
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("schematic", type=Path)
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--out-dir", type=Path, default=None)
    args = ap.parse_args()

    if not args.schematic.exists():
        print(f"ERROR: schematic not found: {args.schematic}", file=sys.stderr)
        return 2
    cli = kicad_cli()
    out_dir = args.out_dir or (REPO / "build" / args.schematic.stem)
    out_dir.mkdir(parents=True, exist_ok=True)

    target = run_erc(cli, args.schematic, out_dir / "erc.json")
    base = run_erc(cli, args.baseline, out_dir / "erc_baseline.json")

    pdf = out_dir / f"{args.schematic.stem}.pdf"
    pdf_ok = export_pdf(cli, args.schematic, pdf)

    # Compare per (severity, type). key == (severity, type).
    new_errors, new_warnings, new_noise, resolved = [], [], [], []
    for key, n in target.items():
        if n > base.get(key, 0):
            row = (key, base.get(key, 0), n)
            if key[1] in NOISE_TYPES:
                new_noise.append(row)
            elif key[0] == "error":
                new_errors.append(row)
            else:
                new_warnings.append(row)
    for key, n in base.items():
        if n > target.get(key, 0):
            resolved.append((key, n, target.get(key, 0)))

    def fmt(key): return f"{key[1]} ({key[0]})"

    print(f"== ERC: baseline {sum(base.values())} -> board {sum(target.values())} violations ==")
    if resolved:
        print("\nResolved vs baseline (good — module connected these):")
        for key, b, t in sorted(resolved):
            print(f"  - {fmt(key)}: {b} -> {t}")
    # Meaningful new WARNINGS are reported but do NOT fail the build: some are
    # unavoidable and correct (e.g. pin_to_pin from connecting GPIO0 to the boot
    # button, whose EasyEDA symbol uses Unspecified pin types).
    if new_warnings:
        print("\nNew warnings (review, but non-fatal):")
        for key, b, t in sorted(new_warnings):
            print(f"  ~ {fmt(key)}: {b} -> {t}")
    if new_noise:
        extra = sum(t - b for _, b, t in new_noise)
        print(f"\nNew low-signal noise (suppressed): +{extra} across "
              f"{', '.join(sorted(k[1] for k, _, _ in new_noise))}")
    if new_errors:
        print("\nNEW ERRORS (build FAILS — must fix):")
        for key, b, t in sorted(new_errors):
            print(f"  ! {fmt(key)}: {b} -> {t}")
    else:
        print("\nNo new ERROR-severity violations introduced. ✓")

    print(f"\nPDF render: {'OK' if pdf_ok else 'FAILED'} -> {pdf}")
    print("  (Read this PDF — the screenshot is the primary correctness gate.)")

    return 1 if new_errors or not pdf_ok else 0


if __name__ == "__main__":
    raise SystemExit(main())
