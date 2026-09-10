#!/usr/bin/env python3
"""Verify that every GUI page remains reachable at standard window sizes.

    python tools/check_gui_layout.py
    python tools/check_gui_layout.py 1280 800

Measures actual scrollbars as well as page size hints, sidebar and tab overflow.
A nonzero exit means a normal desktop page needs scrolling or Qt enlarged the
requested window. Expanded advanced Reset controls may scroll deliberately;
they are not part of the default-layout fit contract. No hardware is accessed.
"""
import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "Software" / "GUI"))
sys.path.insert(0, str(REPO / "Software" / "Python"))

import proact_gui  # noqa: E402
from PyQt6.QtWidgets import QApplication, QScrollArea  # noqa: E402

SIZES = [(1280, 720), (1280, 800), (1366, 768), (1600, 900), (1920, 1040)]


def measure(win, app):
    """Return page overflow rows plus sidebar and tab-bar overflow in pixels."""
    rows = []
    for i in range(win.tabs.count()):
        win.tabs.setCurrentIndex(i)
        for _ in range(6):
            app.processEvents()
        page = win.tabs.widget(i)
        overflow = 0
        if isinstance(page, QScrollArea):
            overflow = max(0, page.widget().sizeHint().height() - page.viewport().height(),
                           page.verticalScrollBar().maximum(), page.horizontalScrollBar().maximum())
        rows.append((win.tabs.tabText(i), overflow))
    side = win.centralWidget().layout().itemAt(0).widget()
    side_over = max(0, side.widget().sizeHint().height() - side.viewport().height(),
                    side.verticalScrollBar().maximum())
    bar = win.tabs.tabBar()
    needed = sum(bar.tabRect(i).width() for i in range(bar.count()))
    tab_over = max(0, needed - win.tabs.width())
    return rows, side_over, tab_over


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dimensions", type=int, nargs="*", help="optional WIDTH HEIGHT")
    args = parser.parse_args(argv)
    if len(args.dimensions) not in (0, 2) or any(v < 320 for v in args.dimensions):
        parser.error("supply WIDTH HEIGHT, each at least 320, or no dimensions")
    sizes = [tuple(args.dimensions)] if args.dimensions else SIZES
    app = QApplication.instance() or QApplication(sys.argv[:1])
    proact_gui._apply_theme(app)
    win = proact_gui.MainWindow(); win.timer.stop(); win.ui_timer.stop(); win.show()
    failures = []
    try:
        for width, height in sizes:
            win.resize(width, height)
            for _ in range(6):
                app.processEvents()
            rows, side_over, tab_over = measure(win, app)
            resized = (win.width(), win.height()) != (width, height)
            total = sum(over for _, over in rows) + side_over + tab_over + int(resized)
            print(f"\n{width}x{height}: {'PASS' if total == 0 else 'FAIL'} "
                  f"(actual {win.width()}x{win.height()})")
            for name, overflow in [("Tab bar", tab_over), ("Sidebar", side_over), *rows]:
                print(f"  {name:<22} overflow {overflow:>4} px")
            if total:
                failures.append(f"{width}x{height}")
    finally:
        win.close(); app.processEvents()
    if failures:
        print("\nFAIL: overflow or forced resizing at " + ", ".join(failures))
        return 1
    print(f"\nPASS: all 7 pages fit at all {len(sizes)} tested sizes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
