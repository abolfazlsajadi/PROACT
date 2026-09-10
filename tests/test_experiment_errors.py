"""Acquisition API error handling with in-memory devices; no trace acquisition."""
import pytest
from proact_host import experiment
from proact_host import capture as capture_module


@pytest.fixture
def fake_devices(monkeypatch):
    events = []
    class Uart:
        def __init__(self, **kwargs): pass
        def open(self): events.append("uart open"); return self
        def close(self): events.append("uart close")
    class Chip:
        def __init__(self, uart): pass
        def __getattr__(self, name): return lambda *a, **k: None
    class Scope:
        def __init__(self, **kwargs): pass
        def connect(self, **kwargs):
            events.append("scope open")
            raise RuntimeError("scope unavailable")
        def disconnect(self): events.append("scope close")
    monkeypatch.setattr(experiment, "UartTransport", Uart)
    monkeypatch.setattr(experiment, "ProactTarget", Chip)
    monkeypatch.setattr(capture_module, "ChipWhispererCapture", Scope)
    return events


def test_requested_scope_failure_aborts_and_closes_all_handles(fake_devices, tmp_path):
    output = tmp_path / "must-not-exist.npz"
    exp = experiment.PROACTExperiment(output=str(output))
    with pytest.raises(RuntimeError, match="scope unavailable"):
        exp.prepare()
    assert fake_devices == ["uart open", "scope open", "scope close", "uart close"]
    assert not output.exists() and exp.store is None
    assert exp.uart is None and exp.scope is None and exp.chip is None
    exp.close()
    assert len(fake_devices) == 4


def test_functional_only_is_explicit_and_never_opens_scope(fake_devices, tmp_path):
    exp = experiment.PROACTExperiment(capture=False, output=str(tmp_path / "functional.npz"))
    exp.prepare(); exp.close()
    assert fake_devices == ["uart open", "uart close"]


def test_uart_closes_even_if_scope_cleanup_fails(fake_devices):
    exp = experiment.PROACTExperiment()
    class Scope:
        def disconnect(self): raise OSError("close failed")
    class Uart:
        def close(self): fake_devices.append("closed")
    exp.scope = Scope(); exp.uart = Uart()
    with pytest.raises(OSError): exp.close()
    assert fake_devices == ["closed"]


@pytest.mark.parametrize("kwargs", [
    {"traces": 0}, {"samples": -1}, {"clock_hz": float("nan")},
    {"clock_hz": 0}, {"key": b""}, {"fixed_input": b"wrong"},
    {"target": "unknown"}, {"target": "ascon", "decrypt": True},
    {"platform": "unknown"},
])
def test_invalid_api_options_fail_before_device_access(fake_devices, kwargs):
    with pytest.raises(ValueError): experiment.PROACTExperiment(**kwargs)
    assert fake_devices == []
