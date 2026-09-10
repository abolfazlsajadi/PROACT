"""GUI lifecycle regressions. Offscreen Qt and fake devices only; no enumeration."""
import csv
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
GUI_DIR = Path(__file__).resolve().parents[1] / "Software" / "GUI"
sys.path.insert(0, str(GUI_DIR))
pytest.importorskip("PyQt6")
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
import proact_gui as gui


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    gui._apply_theme(instance)
    return instance


def pump_until(app, condition, timeout=3):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.002)
    assert condition(), "Qt worker completion was not delivered"
    app.processEvents()


@pytest.fixture
def win(app, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline GUI tests must not open or enumerate hardware")
    from proact_host.programmer import Mcp2210Programmer
    monkeypatch.setattr(gui.UartTransport, "open", forbidden)
    monkeypatch.setattr(Mcp2210Programmer, "open", forbidden)
    window = gui.MainWindow()
    window.timer.stop()
    window.resize(1280, 800)
    window.show(); app.processEvents()
    yield window
    assert not window._busy, "Test left a worker running"
    assert not window._poll_active, "Test left a polling worker running"
    window.uart = window.target = window.programmer = window.resets = window.scope = None
    window.close(); app.processEvents()


def test_import_and_startup_are_copy_local_and_disconnected(win):
    import proact_host
    assert Path(proact_host.__file__).resolve().is_relative_to(GUI_DIR.parent / "Python")
    assert win.target is win.scope is win.programmer is None
    assert win.activity_label.text() == "Disconnected · no task running"
    assert win.tabs.count() == 7


def test_one_task_remains_responsive_and_restores_input_states(win, app):
    release = threading.Event()
    entered = threading.Event()
    def slow():
        entered.set()
        assert release.wait(2)
    try:
        assert win._run(slow, "Fake slow operation")
        assert entered.wait(1)
        assert not win.exp_run_btn.isEnabled()
        assert not win.reset_section.isEnabled()
        assert win.tabs.isEnabled()
        assert not win._run(lambda: pytest.fail("Second operation ran"), "Second task")
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        app.processEvents()
        assert ticks
        assert "Fake slow operation" in win.activity_label.text()
        # Existing completion callbacks must not unlock a control mid-task.
        win._btn_enabled("experiment", True)
        assert not win.exp_run_btn.isEnabled()
    finally:
        release.set()
        pump_until(app, lambda: not win._busy)
    assert win.exp_run_btn.isEnabled()
    assert win.reset_section.isEnabled()
    assert not win.rows["nonce"].isEnabled()
    assert not win.bit_prog.isEnabled()  # ASIC-specific state survives restoration.
    win.reset_section.setChecked(True)
    assert win.line_toggles["controller"].isEnabled()


def test_unhandled_worker_exception_is_visible_and_controls_recover(win, app):
    def broken():
        raise RuntimeError("simulated transport disconnected")
    assert win._run(broken, "Reset test")
    pump_until(app, lambda: not win._busy)
    win._flush_logs(limit=5000)
    assert "Failed" in win.activity_label.text()
    assert "simulated transport disconnected" in win.status_lbl.text()
    assert "RuntimeError" in win.table.item(0, 2).text()
    assert win.exp_run_btn.isEnabled()


def test_disconnect_waits_off_the_qt_thread(win, app):
    lock = threading.Lock(); lock.acquire()
    closed = []
    gui_thread = threading.get_ident()
    uart = SimpleNamespace(lock=lock, close=lambda: closed.append(threading.get_ident()))
    programmer = SimpleNamespace(close=lambda: closed.append("programmer"))
    win.uart, win.target, win.programmer = uart, object(), programmer
    start = time.monotonic()
    try:
        win.on_disconnect()
        assert time.monotonic() - start < .2
        ticks = []; QTimer.singleShot(0, lambda: ticks.append(True)); app.processEvents()
        assert ticks and not closed and win._busy
    finally:
        lock.release()
        pump_until(app, lambda: not win._busy)
    assert closed[0] != gui_thread
    assert closed[1] == "programmer"
    assert win.uart is win.target is win.programmer is None


def test_repeated_poll_ticks_do_not_accumulate_threads(win, app):
    release, entered = threading.Event(), threading.Event()
    calls = []
    def status():
        calls.append(True); entered.set(); assert release.wait(2)
        return {"controller": True}
    win.resets = SimpleNamespace(try_status=status)
    try:
        win._poll(); assert entered.wait(1)
        for _ in range(30):
            win._poll()
        assert len(calls) == 1
    finally:
        release.set(); pump_until(app, lambda: not win._poll_active)
    assert win.line_toggles["controller"].isChecked()


def test_foreground_task_suppresses_background_poll(win, app):
    release = threading.Event()
    win.resets = SimpleNamespace(try_status=lambda: pytest.fail("Polled during operation"))
    try:
        win._run(lambda: release.wait(2), "Fake task")
        win._poll()
        assert not win._poll_active
    finally:
        release.set(); pump_until(app, lambda: not win._busy)


def test_close_does_not_terminate_active_worker(win, app):
    release = threading.Event()
    try:
        win._run(lambda: release.wait(2), "Fake write")
        assert not win.close()
        assert win.isVisible()
        assert "Wait for it to finish" in win.status_lbl.text()
    finally:
        release.set(); pump_until(app, lambda: not win._busy)


def test_close_cleans_up_all_connections_without_blocking(win, app):
    closed = []
    win.uart = SimpleNamespace(lock=threading.Lock(), close=lambda: closed.append("uart"))
    win.target = object()
    win.programmer = SimpleNamespace(close=lambda: closed.append("spi"))
    win.scope = SimpleNamespace(disconnect=lambda: closed.append("scope"))
    win.close()
    pump_until(app, lambda: not win._busy and not win.isVisible())
    assert closed == ["uart", "spi", "scope"]


def test_display_log_is_bounded_and_reports_discarded_rows(win):
    for i in range(win.LOG_LIMIT + 31):
        win._log("Fake", f"message {i}")
    win._flush_logs(limit=len(win._pending_logs))
    assert win.table.rowCount() == win.LOG_LIMIT
    assert win.table.item(0, 2).text() == "message 31"
    assert "31 older messages discarded" in win.log_count.text()
    win._log("Fake", "latest")
    win._flush_logs()
    assert win.table.rowCount() == win.LOG_LIMIT
    assert win.table.item(win.LOG_LIMIT-1, 2).text() == "latest"
    assert "32 older messages discarded" in win.log_count.text()
    win._clear_logs()
    assert not win._pending_logs and not win.table.rowCount()


def test_new_log_rows_do_not_interrupt_reading_older_rows(win, app):
    win.tabs.setCurrentIndex(6)
    for i in range(100):
        win._log("Fake", str(i))
    win._flush_logs(); app.processEvents()
    bar = win.table.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(0)
    win._log("Fake", "later"); win._flush_logs(); app.processEvents()
    assert bar.value() == 0


def test_csv_export_includes_pending_unicode_rows(win, monkeypatch, tmp_path):
    path = tmp_path / "messages.csv"
    monkeypatch.setattr(gui.QFileDialog, "getSaveFileName", lambda *a, **k: (str(path), ""))
    win._log("Test", 'temperature μ, "quoted"')
    assert win.table.rowCount() == 0
    win.on_export_csv()
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    assert rows[1][2] == 'temperature μ, "quoted"'


def test_csv_export_failure_is_reported_in_the_window(win, monkeypatch, tmp_path):
    monkeypatch.setattr(gui.QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path), ""))
    win.on_export_csv()  # A directory is not a writable CSV file.
    assert "Export failed" in win.status_lbl.text()


def test_selected_input_file_cannot_silently_fall_back_to_manual_values(win):
    win.use_file.setChecked(True)
    win.file_edit.clear()
    with pytest.raises(ValueError, match="Choose an input file"):
        win._build_plan(win.rows, "aes1", 3)


def test_relative_input_file_resolves_against_the_copy(win, monkeypatch, tmp_path):
    folder = tmp_path / "repo"; folder.mkdir()
    (folder / "input.txt").write_text("pt=" + "ab" * 16 + "\n")
    monkeypatch.setattr(win, "_repo_root", lambda: str(folder))
    monkeypatch.chdir(tmp_path)
    win.use_file.setChecked(True); win.file_edit.setText("input.txt")
    plan = win._build_plan(win.rows, "aes1", 4)
    assert plan.n == 1
    assert next(iter(plan))["pt"] == bytes.fromhex("ab" * 16)


class FakeExperiment:
    """Only the host's formatting/error path is exercised; no crypto computation."""
    def __init__(self, setup_error=False):
        self.setup_error = setup_error
        self.calls = 0
    def enable_sendback(self):
        if self.setup_error:
            raise RuntimeError("fake setup failure")
    def select(self, *args): pass
    def set_cfgsel(self, *args): pass
    def set_decrypt(self, *args): pass
    def set_key(self, *args): pass
    def set_plaintext(self, *args): pass
    def run_and_read(self):
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("fake missing reply")
        return 0, b"\xab" * 16


@pytest.mark.parametrize("save", [True, False])
def test_experiment_error_rows_and_summary_are_not_lost(win, monkeypatch, tmp_path, save):
    monkeypatch.setattr(win, "_repo_root", lambda: str(tmp_path))
    win.target = FakeExperiment()
    plan = gui.InputPlan(n=3)
    win._experiment_body(plan, "aes1", False, "auto", 0x12, False, False, save)
    text = win.exp_out.toPlainText()
    assert "fake missing reply" in text
    assert "2 responses, 1 communication errors" in text
    logs = list(tmp_path.glob("experiments/*.log"))
    assert len(logs) == int(save)
    if save:
        assert "fake missing reply" in logs[0].read_text()
        assert "2 responses, 1 communication errors" in logs[0].read_text()


def test_setup_failure_flushes_the_partial_experiment_log(win, monkeypatch, tmp_path):
    monkeypatch.setattr(win, "_repo_root", lambda: str(tmp_path))
    win.target = FakeExperiment(setup_error=True)
    with pytest.raises(RuntimeError, match="fake setup failure"):
        win._experiment_body(gui.InputPlan(n=1), "aes1", False, "auto", 0x12, False, False, True)
    logs = list(tmp_path.glob("experiments/*.log"))
    assert len(logs) == 1
    assert "# stopped: RuntimeError: fake setup failure" in logs[0].read_text()


def test_experiment_display_is_bounded(win):
    win.exp_out.setPlainText("\n".join(str(i) for i in range(5500)))
    assert win.exp_out.blockCount() == win.OUTPUT_BLOCK_LIMIT
    assert win.exp_out.toPlainText().splitlines()[0] == "500"


def test_unimplemented_options_are_not_selectable(win):
    from PyQt6.QtCore import Qt
    assert not win.cap_timer.isEnabled()
    assert not win.cap_timer.isChecked()
    assert "does not record" in win.cap_timer.toolTip()
    assert not (win.transport.model().item(1).flags() & Qt.ItemFlag.ItemIsEnabled)


def test_bad_hardware_input_is_rejected_before_starting_any_job(win, monkeypatch):
    win.target = object()
    win.rows["key"].value.setText("00")
    monkeypatch.setattr(win, "_run", lambda *a, **k: pytest.fail("Invalid input started a worker"))
    win.on_experiment()
    assert "input error" in win.exp_out.toPlainText()
    assert "16" in win.exp_out.toPlainText()


def test_missing_connection_is_visible_on_the_active_page(win):
    assert not win._need()
    assert "Connect the board UART" in win.status_lbl.text()


@pytest.mark.parametrize("dimensions", [(1280, 720), (1280, 800), (1366, 768), (1600, 900), (1920, 1040)])
def test_all_pages_fit_standard_workspaces(win, app, dimensions):
    import importlib.util
    path = GUI_DIR.parents[1] / "tools" / "check_gui_layout.py"
    spec = importlib.util.spec_from_file_location("gui_layout_check", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    win.resize(*dimensions)
    for _ in range(6): app.processEvents()
    rows, side, tabs = module.measure(win, app)
    assert (win.width(), win.height()) == dimensions
    assert rows and all(overflow == 0 for _, overflow in rows)
    assert side == tabs == 0
    for panel in win.findChildren(gui.HelpPanel):
        assert panel.rect().contains(panel.help.geometry())


def test_encryption_only_cores_do_not_offer_hardware_decryption(win):
    win.dec_rb.setChecked(True)
    win.exp_core.setCurrentText("ascon")
    assert win.enc_rb.isChecked() and not win.dec_rb.isEnabled()
    win.exp_core.setCurrentText("xoodyak")
    assert win.enc_rb.isChecked() and not win.dec_rb.isEnabled()
    win.exp_core.setCurrentText("aes2")
    assert win.dec_rb.isEnabled()


def test_inactive_aead_manual_inputs_do_not_break_an_aes_run(win):
    win.rows["nonce"].value.setText("not valid hex")
    win.rows["ad"].value.setText("ff")
    plan = win._build_plan(win.rows, "aes1", 1)
    assert plan.n == 1
    assert next(iter(plan))["nonce"] == bytes(16)


def test_experiment_progress_is_visible_across_tabs(win, app):
    release = threading.Event()
    try:
        win._run(lambda: release.wait(2), "Running experiment")
        win.bus.taskprogress.emit(25, 100)
        win.tabs.setCurrentIndex(6)
        assert "25/100 operations" in win.activity_label.text()
        assert win.activity_bar.maximum() == 100
        assert win.activity_bar.value() == 25
    finally:
        release.set(); pump_until(app, lambda: not win._busy)


@pytest.mark.parametrize("scope", [None, SimpleNamespace(is_connected=False)])
def test_capture_requires_a_connected_scope_before_any_worker(win, monkeypatch, scope):
    win.target = object(); win.scope = scope
    monkeypatch.setattr(win, "_run", lambda *a, **k: pytest.fail("Capture started without a scope"))
    win.on_capture()
    assert "Connect the scope first" in win.status_lbl.text()
    assert win.cap_btn.isEnabled()


@pytest.mark.parametrize("field,text,error", [
    ("cap_key", "00", "Fixed key must contain exactly 16 bytes"),
    ("cap_key", "zz" * 16, "Fixed key must contain exactly 16 bytes"),
    ("cap_pt", "", "Fixed plaintext must contain exactly 16 bytes"),
    ("cap_samples", "NaN", "Samples must be an integer"),
    ("cap_samples", "0", "Samples must be between"),
    ("cap_samples", "-1", "Samples must be between"),
    ("cap_samples", "4.5", "Samples must be an integer"),
    ("cap_inttrig", "0x80", "Internal trigger must be between"),
    ("cap_inttrig", "-1", "Internal trigger must be between"),
    ("cap_inttrig", "invalid", "Internal trigger must be an integer"),
])
def test_capture_rejects_bad_fields_without_defaults_or_device_access(win, monkeypatch, field, text, error):
    win.target = object(); win.scope = SimpleNamespace(is_connected=True)
    if field == "cap_inttrig":
        win.cap_core.setCurrentText("ascon")
    win.cap_input.setCurrentText("fixed")
    getattr(win, field).setText(text)
    monkeypatch.setattr(win, "_run", lambda *a, **k: pytest.fail("Invalid capture field started a worker"))
    win.on_capture()
    assert error in win.status_lbl.text()
    assert win.cap_btn.isEnabled()


def test_random_capture_modes_ignore_inactive_fixed_boxes_without_running_capture(win, monkeypatch):
    win.target = object(); win.scope = SimpleNamespace(is_connected=True)
    win.cap_key_mode.setCurrentText("random"); win.cap_input.setCurrentText("random")
    win.cap_key.setText("unused"); win.cap_pt.setText("unused")
    jobs = []
    monkeypatch.setattr(win, "_run", lambda fn, label: jobs.append(label))
    win.on_capture()
    assert jobs == ["Capturing traces"]  # The callable is deliberately not executed.


@pytest.mark.parametrize("handler", ["on_connect", "on_cw_connect", "on_program_fpga"])
@pytest.mark.parametrize("frequency", ["nan", "inf", "-inf", "1e999", "0", "-2", "bad"])
def test_all_clock_actions_reject_nonfinite_or_nonpositive_mhz(win, monkeypatch, handler, frequency):
    win.target_sel.setCurrentText("FPGA (CW305)")
    win.bit_edit.setText("unused-test-file.bit")
    win.freq.setText(frequency)
    monkeypatch.setattr(win, "_run", lambda *a, **k: pytest.fail("Invalid clock started a worker"))
    getattr(win, handler)()
    assert "Frequency must be a finite, positive number in MHz" in win.status_lbl.text()


@pytest.mark.parametrize("handler", ["on_mem_read", "on_mem_write"])
@pytest.mark.parametrize("address,error", [
    ("bad", "Address must be an integer"),
    ("-4", "Address must be between"),
    ("0x100000000", "Address must be between"),
    ("0x1001", "Address must be 4-byte aligned"),
    ("0xFFFFFFFC", "range exceeds the 32-bit address space"),
])
def test_memory_address_or_span_is_checked_before_worker(win, monkeypatch, handler, address, error):
    win.target = object(); win.mem_addr.setText(address); win.mem_len.setValue(2)
    win.mem_data.setText("00000001 00000002")
    monkeypatch.setattr(win, "_run", lambda *a, **k: pytest.fail("Invalid address started a worker"))
    getattr(win, handler)()
    assert error in win.mem_out.toPlainText()


@pytest.mark.parametrize("text,error", [("100000000", "Data word 1 must be between"),
                                       ("-1", "Data word 1 must be between"),
                                       ("xyz", "Data word 1 must be an integer"),
                                       ("", "Enter one or more")])
def test_memory_words_are_not_silently_truncated(win, monkeypatch, text, error):
    win.target = object(); win.mem_data.setText(text)
    monkeypatch.setattr(win, "_run", lambda *a, **k: pytest.fail("Invalid data started a worker"))
    win.on_mem_write()
    assert error in win.mem_out.toPlainText()


@pytest.mark.parametrize("base", ["-4", "0x100000000", "0x08100001", "invalid"])
def test_swrv_base_is_checked_before_worker_or_file_parsing(win, monkeypatch, base):
    win.target = object(); win.swrv_base.setText(base)
    monkeypatch.setattr(win, "_run", lambda *a, **k: pytest.fail("Invalid Sw-RV base started a worker"))
    win.on_load_swrv()
    assert "Sw-RV data-memory base" in win.status_lbl.text()


def test_aead_experiment_internal_trigger_is_not_masked(win, monkeypatch):
    win.target = object(); win.exp_core.setCurrentText("ascon"); win.int_trig.setText("0x180")
    monkeypatch.setattr(win, "_run", lambda *a, **k: pytest.fail("Invalid trigger started an experiment"))
    win.on_experiment()
    assert "Internal trigger must be between 0 and 127" in win.exp_out.toPlainText()


@pytest.mark.parametrize("entry", ["scope", "program", "fpga_connect"])
def test_failed_new_scope_connection_releases_partial_resource(win, monkeypatch, app, entry):
    from proact_host import capture
    events = []
    class PartialScope:
        def __init__(self, **kwargs):
            events.append(("create", kwargs))
        def connect(self, **kwargs):
            events.append(("connect", kwargs)); raise RuntimeError("fake partial setup failure")
        def disconnect(self):
            events.append(("disconnect", None))
    monkeypatch.setattr(capture, "ChipWhispererCapture", PartialScope)
    win.bus.info.disconnect()  # No modal dialogs during a fake failure path.
    if entry == "scope":
        win.on_cw_connect()
        pump_until(app, lambda: not win._busy)
    elif entry == "program":
        win.target_sel.setCurrentText("FPGA (CW305)"); win.bit_edit.setText("unused.bit")
        win.on_program_fpga()
        pump_until(app, lambda: not win._busy)
    else:
        win._fpga_connect_and_program("unused.bit", 25e6)
    assert [event[0] for event in events] == ["create", "connect", "disconnect"]
    assert win.scope is None
    assert "fake partial setup failure" in win.cw_lbl.text()


def test_scope_setup_failure_remains_primary_if_cleanup_also_fails(win, monkeypatch):
    from proact_host import capture
    class PartialScope:
        def __init__(self, **kwargs): pass
        def connect(self, **kwargs): raise RuntimeError("first setup failure")
        def disconnect(self): raise OSError("cleanup failure")
    monkeypatch.setattr(capture, "ChipWhispererCapture", PartialScope)
    with pytest.raises(RuntimeError, match="first setup failure"):
        win._connect_new_scope(50e6, "asic")
    win._flush_logs(limit=5000)
    assert "cleanup failure" in win.table.item(0, 2).text()


def test_scope_rejecting_sample_count_stops_before_target_commands(win, monkeypatch, app):
    class RejectingADC:
        @property
        def samples(self): return 5000
        @samples.setter
        def samples(self, value): raise ValueError("fake unsupported sample count")
    class UntouchedTarget:
        def __getattr__(self, name):
            pytest.fail("Target was accessed after scope rejected samples: " + name)
    win.target = UntouchedTarget()
    win.scope = SimpleNamespace(is_connected=True, scope=SimpleNamespace(adc=RejectingADC()))
    win.on_capture()
    pump_until(app, lambda: not win._busy)
    win._flush_logs(limit=5000)
    assert any("fake unsupported sample count" in win.table.item(row, 2).text()
               for row in range(win.table.rowCount()))
