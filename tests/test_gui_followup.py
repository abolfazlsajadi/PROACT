"""Further capture/setup usability checks; fake resources and offscreen Qt only."""
import threading
from types import SimpleNamespace

import pytest

from test_gui_workflows import app, win, pump_until, gui


def test_setup_readiness_distinguishes_missing_connections_from_form_validity(win):
    assert win.cap_state.text() == "Not connected"
    assert "board UART" in win.cap_summary.text() and "scope" in win.cap_summary.text()
    win.target = object()
    win._update_capture_state()
    assert "board UART" not in win.cap_summary.text()
    win.scope = SimpleNamespace(is_connected=True)
    win._update_capture_state()
    assert win.cap_state.text() == "Connected"
    assert "verification" in win.cap_summary.text()
    win._set_status("uart", "ok")
    assert win.activity_label.text() == "Board connected · no task running"
    win.cap_samples.setText("oops")
    assert win.cap_state.text() == "Check settings"
    assert "Samples must be an integer" in win.cap_summary.text()
    win.cap_samples.setText("4096")
    assert win.cap_state.text() == "Connected"


def test_offline_review_reads_no_device_status_and_writes_nothing(win, monkeypatch):
    class HandleOnly:
        is_connected = True
        def __getattr__(self, name):
            pytest.fail("Review accessed device property: " + name)
    win.target = HandleOnly(); win.scope = HandleOnly()
    monkeypatch.setattr(win, "_run", lambda *a: pytest.fail("Review started worker"))
    win.cap_out.setText("experiments/relative.tracepack")
    win.on_review_capture()
    text = win.capture_review_text.toPlainText()
    assert "Board UART and scope handles are present" in text
    assert "current CPA page cannot open this format" in text
    assert win._repo_root() in text
    assert "no device commands" in text
    assert "does not compare ciphertexts" in text
    assert "actual ADC rate" in text
    win.capture_review_dialog.hide()


def test_review_reports_invalid_form_and_escapes_user_text(win):
    win.cap_samples.setText("0")
    win.cap_out.setText("<script>capture</script>.npz")
    win.on_review_capture()
    text = win.capture_review_text.toPlainText()
    assert "Correct this field: Samples" in text
    assert "<script>capture</script>.npz" in text
    assert "single-file" in text.lower()
    win.capture_review_dialog.hide()


def test_aead_explanation_and_internal_trigger_match_selected_core(win):
    assert not win.cap_inttrig.isEnabled()
    win.cap_inttrig.setText("unused value")
    assert win._capture_settings()["inttrig"] == 0
    win.cap_core.setCurrentText("ascon")
    assert win.cap_inttrig.isEnabled()
    assert win.cap_state.text() == "Check settings"
    win.cap_inttrig.setText("0x12")
    win.on_review_capture()
    text = win.capture_review_text.toPlainText()
    assert "Nonce and associated data are each 16 zero bytes" in text
    assert "AEAD tags are not retained" in text
    win.capture_review_dialog.hide()


def test_swrv_review_names_program_source_without_loading_it(win):
    win.cap_core.setCurrentText("swrv")
    win.on_review_capture()
    assert "Memory / Sw-RV" in win.capture_review_text.toPlainText()
    assert "loaded when capture starts" in win.capture_review_text.toPlainText()
    win.capture_review_dialog.hide()


def test_review_stays_available_while_worker_holds_session(win, app):
    release = threading.Event()
    try:
        win._run(lambda: release.wait(2), "Fake setup task")
        assert win.cap_state.text() == "Working"
        assert win.cap_review_btn.isEnabled()
        assert not win.cap_btn.isEnabled()
        win.on_review_capture()
        assert win.capture_review_dialog.isVisible()
        assert "no device commands" in win.capture_review_text.toPlainText()
    finally:
        win.capture_review_dialog.hide()
        release.set(); pump_until(app, lambda: not win._busy)
    assert win.cap_state.text() == "Not connected"
    assert win.activity_label.text().startswith("Finished")


def test_scope_connection_error_is_a_failed_task(win, app, monkeypatch):
    def fail(*a, **k):
        raise RuntimeError("simulated scope setup failure")
    monkeypatch.setattr(win, "_connect_new_scope", fail)
    win.on_cw_connect()
    pump_until(app, lambda: not win._busy)
    assert win.activity_label.text().startswith("Failed")
    assert "simulated scope setup failure" in win.status_lbl.text()
    assert win.cap_state.text() == "Not connected"


def test_capture_output_error_is_visible_in_task_and_setup_state(win, app, monkeypatch):
    from proact_host import storage
    win.target = object()
    win.scope = SimpleNamespace(is_connected=True, scope=SimpleNamespace(adc=SimpleNamespace()))
    def fail(*a, **k):
        raise OSError("simulated output write failure")
    monkeypatch.setattr(storage, "TraceStore", fail)
    win.on_capture()
    pump_until(app, lambda: not win._busy)
    assert win.activity_label.text().startswith("Failed")
    assert "simulated output write failure" in win.status_lbl.text()
    assert win.cap_state.text() == "Needs attention"
    assert win.cap_btn.isEnabled()
    win._flush_logs(limit=5000)
    assert any("Requested output:" in win.table.item(row, 2).text()
               and "No output file was confirmed" in win.table.item(row, 2).text()
               for row in range(win.table.rowCount()))


def test_partial_capture_reports_saved_count_as_incomplete(win, app, monkeypatch):
    from proact_host import storage
    failures = []
    class FakeStore:
        def __init__(self, *a, **k): pass
        def record_failure(self, index, reason): failures.append((index, reason))
        def close(self): return "offline-example.npz"
    class FakeTarget:
        lock = threading.RLock()
        def enable_sendback(self): pass
        def select(self, core): pass
        def set_decrypt(self, value): pass
        def set_key(self, value): pass
        def set_plaintext(self, value): pass
        def run_and_read(self): raise TimeoutError("simulated acquisition timeout")
    win.target = FakeTarget()
    win.scope = SimpleNamespace(is_connected=True, scope=SimpleNamespace(adc=SimpleNamespace()),
                                arm=lambda: None)
    win.cap_n.setValue(2)
    monkeypatch.setattr(storage, "TraceStore", FakeStore)
    win.on_capture()
    pump_until(app, lambda: not win._busy)
    assert len(failures) == 2
    assert "Incomplete capture: saved 0/2 traces" in win.status_lbl.text()
    assert win.cap_state.text() == "Needs attention"
    assert "Incomplete / failed" in win.cap_bar.format()


def test_samples_help_makes_no_platform_independent_window_promise(win):
    tip = win.cap_samples.toolTip()
    assert "actual ADC sample rate" in tip and "measured timing" in tip
    assert "800-5000" not in tip and "first ~100" not in tip
