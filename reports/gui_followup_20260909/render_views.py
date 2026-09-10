"""Generate actual Qt views with fake local handles; never access bench devices."""
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "Software/GUI"))
sys.path.insert(0, str(ROOT / "Software/Python"))
import proact_gui as gui
from PyQt6.QtWidgets import QApplication


def forbidden(*a, **k):
    raise AssertionError("Screenshot generation must not access hardware")


gui.UartTransport.open = forbidden
from proact_host.programmer import Mcp2210Programmer
Mcp2210Programmer.open = forbidden
from proact_host.capture import ChipWhispererCapture
ChipWhispererCapture.connect = forbidden

app = QApplication.instance() or QApplication([])
gui._apply_theme(app)
win = gui.MainWindow()
win.timer.stop(); win.ui_timer.stop()
win.resize(1280, 800); win.show()
records = []


def pump():
    for _ in range(8):
        app.processEvents()


def save(name, widget=win, note="Disconnected application; no devices"):
    pump()
    path = OUT / (name + ".png")
    assert widget.grab().save(str(path))
    records.append({"file": path.name, "width": widget.width(), "height": widget.height(),
                    "state": note, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})


def wait_finished():
    deadline = time.monotonic()+3
    while win._busy and time.monotonic() < deadline:
        pump(); time.sleep(.002)
    assert not win._busy


try:
    save("overview_1280x800")
    win.tabs.setCurrentIndex(1)
    save("default_capture_1280x800")
    win.resize(1280, 720)
    save("default_capture_1280x720")
    win.resize(1280, 800)
    win.target = object()
    win.scope = SimpleNamespace(is_connected=True)
    win._set_status("uart", "ok")
    win._set_status("cw", "OFFLINE DEMO · simulated scope connection; no hardware was accessed.")
    win._set_status("status", "OFFLINE DEMO · local fake connection handles.")
    win.cap_out.setText("/work/PROACT/experiments/example_run01.tracepack")
    win._update_capture_state()
    save("ready_capture_1280x800", note="Offline demonstration: fake handles, form valid; no hardware validation")
    win.cap_core.setCurrentText("ascon")
    win.on_review_capture()
    save("help_capture_780x650", win.capture_review_dialog,
         "Actual offline review dialog; fake connected handles, ASCON settings")
    win.capture_review_dialog.hide()
    win.cap_core.setCurrentText("aes1")
    release = threading.Event()
    try:
        win._run(lambda: release.wait(3), "Capturing traces (offline demo)")
        win._set_job_progress(250, 1000)
        win.cap_bar.setValue(25)
        save("busy_capture_1280x800", note="Offline fake worker waiting on an Event; no capture")
    finally:
        release.set(); wait_finished()
    def failed():
        raise OSError("OFFLINE DEMO: output location is not writable")
    win.cap_bar.setValue(0)
    win._run(failed, "Capturing traces")
    wait_finished()
    save("error_capture_1280x800", note="Offline fake worker exception; no file/device writes")
finally:
    if win.capture_review_dialog:
        win.capture_review_dialog.hide()
    win.uart = win.target = win.scope = win.programmer = win.resets = None
    win.close(); pump()

manifest = {"hardware_accessed": False, "generated_utc": datetime.now(timezone.utc).isoformat(),
            "gui_sha256": hashlib.sha256((ROOT / "Software/GUI/proact_gui.py").read_bytes()).hexdigest(),
            "images": records}
(OUT / "screenshots.json").write_text(json.dumps(manifest, indent=2)+"\n")
print(json.dumps(manifest, indent=2))
