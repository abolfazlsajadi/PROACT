#!/usr/bin/env python3
"""Render disconnected GUI pages offscreen, without hardware access.

Examples (from the repository root)::

    python tools/gen_gui_screenshots.py reports/gui_after --size 1400x900 --size 1280x800
    python tools/gen_gui_screenshots.py reports/gui_after_small --size 1280x720

The default single-size invocation remains compatible with docs/images consumers.
Multiple sizes use a separate WIDTHxHEIGHT subfolder per size. Both Qt timers are
stopped; no Connect, self-check, capture or analysis action is invoked.
"""
import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "Software" / "GUI"))
sys.path.insert(0, str(REPO / "Software" / "Python"))

import proact_gui  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

TABS = {
    "Crypto experiment": "gui_overview",
    "ChipWhisperer": "gui_chipwhisperer",
    "CPA analysis": "gui_cpa",
    "Registers": "gui_registers",
    "Memory / Sw-RV": "gui_memory",
    "Self-Check (A–Z)": "gui_selfcheck",
    "UART monitor": "gui_monitor",
}


def dimensions(value):
    try:
        width, height = (int(v) for v in value.lower().split("x"))
        if min(width, height) < 320:
            raise ValueError
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError("size must be WIDTHxHEIGHT, at least 320x320") from None
    return width, height


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("outdir", nargs="?", type=Path, default=REPO / "docs" / "images")
    parser.add_argument("--size", type=dimensions, action="append", help="repeat for multiple sizes")
    args = parser.parse_args(argv)
    sizes = args.size or [(1400, 900)]
    args.outdir.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    proact_gui._apply_theme(app)
    win = proact_gui.MainWindow()
    win.timer.stop(); win.ui_timer.stop()
    win.show()
    titles = {win.tabs.tabText(i): i for i in range(win.tabs.count())}
    if set(titles) != set(TABS):
        parser.error(f"tab inventory changed: found {sorted(titles)}, expected {sorted(TABS)}")
    manifest = {"hardware_accessed": False, "state": "disconnected", "images": []}
    try:
        for width, height in sizes:
            outdir = args.outdir / f"{width}x{height}" if len(sizes) > 1 else args.outdir
            outdir.mkdir(parents=True, exist_ok=True)
            win.resize(width, height)
            for title, name in TABS.items():
                win.tabs.setCurrentIndex(titles[title])
                for _ in range(8):
                    app.processEvents()
                path = outdir / f"{name}.png"
                if not win.grab().save(str(path)):
                    raise RuntimeError(f"Could not save {path}")
                manifest["images"].append({"tab": title, "file": str(path),
                                           "width": win.width(), "height": win.height()})
                print("saved", path)
    finally:
        win.close(); app.processEvents()
    (args.outdir / "screenshots.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
