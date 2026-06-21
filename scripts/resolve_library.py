#!/usr/bin/env python3
"""Locate the installed PCM_Espressif KiCad library and the kicad-cli binary on
this machine, and record the resolved absolute paths to library.json.

Run-once-per-machine. Re-run if KiCad is upgraded or reinstalled. Everything
downstream reads library.json rather than hard-coding paths.

Cross-platform: searches the usual PCM 3rd-party locations on macOS / Linux /
Windows. If auto-detection fails, set explicit overrides:
  ESPRESSIF_3RDPARTY=/path/to/<kicad>/3rdparty   (dir containing symbols/, footprints/)
  KICAD_CLI=/path/to/kicad-cli
"""
from __future__ import annotations
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "library.json"

ESPRESSIF_PKG = "com_github_espressif_kicad-libraries"

# Where the PCM installs 3rd-party addons, across OSes (newest KiCad preferred).
HOME = Path.home()
KICAD_DOC_GLOBS = [
    str(HOME / "Documents/KiCad/*/3rdparty"),                       # macOS / Windows
    str(HOME / ".local/share/kicad/*/3rdparty"),                    # Linux
    str(HOME / ".var/app/org.kicad.KiCad/data/kicad/*/3rdparty"),   # Linux flatpak
]

# kicad-cli is on PATH on Linux/Windows but NOT on macOS (it's in the bundle).
KICAD_CLI_CANDIDATES = [
    "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",       # macOS
    "/usr/bin/kicad-cli", "/usr/local/bin/kicad-cli",               # Linux
    r"C:\Program Files\KiCad\bin\kicad-cli.exe",                    # Windows
]


def _die(msg: str) -> "None":
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _version_key(path: str) -> tuple:
    """Sort key from the KiCad version dir name, e.g. .../kicad/10.0/3rdparty."""
    m = re.search(r"[/\\](?:KiCad|kicad)[/\\]([0-9.]+)[/\\]", path)
    return tuple(int(x) for x in m.group(1).split(".")) if m else (0,)


def find_3rdparty() -> Path:
    override = os.environ.get("ESPRESSIF_3RDPARTY")
    if override:
        if (Path(override) / "symbols" / ESPRESSIF_PKG).is_dir():
            return Path(override)
        _die(f"ESPRESSIF_3RDPARTY={override} has no symbols/{ESPRESSIF_PKG}")
    matches = sorted(
        (m for g in KICAD_DOC_GLOBS for m in glob.glob(g)),
        key=_version_key, reverse=True,
    )
    for base in matches:
        if (Path(base) / "symbols" / ESPRESSIF_PKG).is_dir():
            return Path(base)
    _die(
        "Could not find the PCM_Espressif library in any known location "
        f"({', '.join(KICAD_DOC_GLOBS)}). Install the Espressif KiCad addon via "
        "KiCad's Plugin & Content Manager, or set ESPRESSIF_3RDPARTY."
    )


def find_kicad_cli() -> str:
    override = os.environ.get("KICAD_CLI")
    if override:
        if os.path.isfile(override) and os.access(override, os.X_OK):
            return override
        _die(f"KICAD_CLI={override} is not an executable file")
    cli = shutil.which("kicad-cli")
    if cli:
        return cli
    for c in KICAD_CLI_CANDIDATES:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    _die("kicad-cli not found on PATH or in known install locations. Set KICAD_CLI.")


def find_kicad_symbols(cli: str) -> Path:
    """Stock KiCad symbol library dir (holds Connector_Generic.kicad_sym), needed
    to embed pin-header symbols. Layout differs per OS; derive from kicad-cli or
    search the usual spots."""
    cli_root = Path(cli).resolve().parents[1]
    candidates = [
        cli_root / "SharedSupport" / "symbols",   # macOS bundle
        cli_root / "share" / "kicad" / "symbols", # Windows
        Path("/usr/share/kicad/symbols"),         # Linux
        Path("/usr/local/share/kicad/symbols"),
    ]
    for c in candidates:
        if (c / "Connector_Generic.kicad_sym").is_file():
            return c
    _die("Could not find KiCad's stock symbol dir (Connector_Generic.kicad_sym).")


def main() -> None:
    base = find_3rdparty()
    sym = base / "symbols" / ESPRESSIF_PKG / "Espressif.kicad_sym"
    fp = base / "footprints" / ESPRESSIF_PKG / "Espressif.pretty"
    d3 = base / "3dmodels" / ESPRESSIF_PKG / "espressif.3dshapes"
    for label, p in [("symbol library", sym), ("footprint library", fp)]:
        if not p.exists():
            _die(f"Espressif {label} missing at expected path: {p}")

    cli = find_kicad_cli()
    try:
        ver = subprocess.run(
            [cli, "version"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except subprocess.CalledProcessError as e:  # pragma: no cover
        _die(f"kicad-cli version failed: {e}")

    info = {
        "kicad_version": ver,
        "kicad_cli": cli,
        "espressif_3rdparty_root": str(base),
        "symbol_lib": str(sym),
        "footprint_lib": str(fp),
        "model3d_dir": str(d3) if d3.exists() else None,
        "kicad_symbols_dir": str(find_kicad_symbols(cli)),
        # PCM exposes these libraries to the symbol/footprint editors under
        # this nickname; placed symbols use lib_id "PCM_Espressif:<name>".
        "lib_nickname": "PCM_Espressif",
    }
    OUT.write_text(json.dumps(info, indent=2) + "\n")
    print(f"Wrote {OUT}")
    for k, v in info.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
