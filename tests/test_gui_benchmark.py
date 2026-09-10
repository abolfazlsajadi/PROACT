"""Manual offline responsiveness benchmark; not a hardware benchmark.

Run explicitly from this repository:
    PYTHONPATH=Software/Python .venv/bin/python tests/test_gui_benchmark.py --baseline COMMIT

Compares this GUI with a trusted, explicitly selected local commit. Both use a fake
UART and a lock released after 150 ms. The measured interval is the GUI event
handler's return time, not the end-to-end disconnect duration. Nothing connects,
enumerates devices, captures traces or starts an analysis subprocess.
"""

def main():
    import argparse
    import json
    import hashlib
    import os
    import platform
    import statistics
    import subprocess
    import sys
    import threading
    import time
    import types
    from datetime import datetime, timezone
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True,
                        help="trusted local Git commit/ref containing Software/GUI/proact_gui.py")
    args = parser.parse_args()
    try:
        baseline_commit = subprocess.check_output(
            ["git", "rev-parse", "--verify", "--end-of-options", args.baseline + "^{commit}"],
            cwd=repo, text=True, stderr=subprocess.PIPE).strip()
        source = subprocess.check_output(
            ["git", "show", baseline_commit + ":Software/GUI/proact_gui.py"],
            cwd=repo, text=True, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError:
        parser.error("--baseline must resolve to a local commit containing Software/GUI/proact_gui.py")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path[:0] = [str(repo / "Software/GUI"), str(repo / "Software/Python")]
    from PyQt6.QtCore import QT_VERSION_STR
    from PyQt6.QtWidgets import QApplication
    import proact_gui as current
    app = QApplication([]); current._apply_theme(app)
    baseline = types.ModuleType("proact_gui_baseline")
    baseline.__file__ = str(repo / "Software/GUI/proact_gui.py")
    exec(compile(source, baseline.__file__, "exec"), baseline.__dict__)
    results = {}
    for name, module in [("baseline", baseline), ("updated", current)]:
        values = []
        for _ in range(5):
            win = module.MainWindow(); win.timer.stop()
            if hasattr(win, "ui_timer"):
                win.ui_timer.stop()
            lock = threading.Lock(); lock.acquire()
            win.uart = types.SimpleNamespace(lock=lock, close=lambda: None)
            win.target = object()
            release = threading.Timer(.15, lock.release); release.start()
            start = time.perf_counter(); win.on_disconnect()
            values.append((time.perf_counter() - start) * 1000)
            release.join()
            deadline = time.monotonic() + 3
            while getattr(win, "_busy", False):
                app.processEvents(); time.sleep(.001)
                if time.monotonic() > deadline:
                    raise RuntimeError("Fake disconnect worker did not finish")
            app.processEvents(); win.close()
        results[name] = {"callback_ms": values, "median_ms": statistics.median(values)}
    print(json.dumps({"python": platform.python_version(), "qt": QT_VERSION_STR,
                      "platform": platform.platform(), "baseline_commit": baseline_commit,
                      "measured_utc": datetime.now(timezone.utc).isoformat(),
                      "python_executable": sys.executable,
                      "gui_sha256": hashlib.sha256((repo / "Software/GUI/proact_gui.py").read_bytes()).hexdigest(),
                      "baseline_gui_sha256": hashlib.sha256(source.encode()).hexdigest(),
                      "fake_external_lock_hold_ms": 150, "trials": 5, "results": results}, indent=2))


if __name__ == "__main__":
    main()
