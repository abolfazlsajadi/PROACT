"""Input, firmware, authentication and resource regressions, entirely offline."""
import sys
import threading
from types import SimpleNamespace
import pytest
from proact_host import aead_soft, inputs
from proact_host.programmer import Mcp2210Programmer, _LockedMcp
from proact_host.vmem import parse_vmem


@pytest.mark.parametrize("name", ["ascon128", "xoodyak"])
def test_extended_authentication_tag_is_rejected(name):
    enc = getattr(aead_soft, name + "_encrypt")
    dec = getattr(aead_soft, name + "_decrypt")
    key = bytes(range(16)); nonce = bytes(16); pt = b"a complete block"
    ct, tag = enc(key, nonce, b"", pt)
    assert dec(key, nonce, b"", ct, tag) == pt
    assert dec(key, nonce, b"", ct, tag + b"extra") is None
    assert dec(key, nonce, b"", ct, tag[:-1]) is None


def test_multiline_and_inline_vmem_comments_preserve_words(tmp_path):
    path = tmp_path / "program.vmem"
    path.write_text("\ufeff/* a\n header */ @10 1 /* middle */ 2 // tail\n3\n")
    assert parse_vmem(path) == [(16, 1), (17, 2), (18, 3)]


@pytest.mark.parametrize("text, message", [
    ("@0\nGARBAGE", ":2: invalid hexadecimal"),
    ("@0\n100000000", ":2:.*32-bit"),
    ("@100000000 1", ":1:.*32-bit"),
    ("@ffffffff 1 2", "word address exceeds"),
    ("/* broken", "unterminated block comment"),
    ("@0 -1", "32-bit"),
])
def test_vmem_errors_have_location_and_do_not_truncate(tmp_path, text, message):
    path = tmp_path / "program.vmem"; path.write_text(text)
    with pytest.raises(ValueError, match=message): parse_vmem(path)


def test_invalid_program_does_not_reset_gpio(tmp_path, monkeypatch):
    p = Mcp2210Programmer()
    called = []
    monkeypatch.setattr(p, "_reset_low", lambda: called.append("reset"))
    bad = tmp_path / "bad.vmem"; bad.write_text("@0 wrong")
    with pytest.raises(ValueError): p.program(bad)
    assert called == []


def test_empty_program_does_not_reset_gpio(tmp_path, monkeypatch):
    p = Mcp2210Programmer()
    monkeypatch.setattr(p, "_reset_low", lambda: pytest.fail("reset attempted"))
    path = tmp_path / "empty.vmem"; path.write_text("")
    with pytest.raises(ValueError, match="no data words"): p.program(path)


def test_programmer_close_is_idempotent_and_closes_hid_only():
    p = Mcp2210Programmer(); calls = []
    p.mcp = _LockedMcp(SimpleNamespace(_hid=SimpleNamespace(close=lambda: calls.append("closed"))))
    p.lock = p.mcp.lock
    p.close(); p.close()
    assert calls == ["closed"]
    assert p.mcp is None and p.lock is None


def test_setup_failure_closes_opened_hid(monkeypatch):
    calls = []
    fake = SimpleNamespace(_hid=SimpleNamespace(close=lambda: calls.append("closed")))
    def construct(serial, immediate_gpio_update=True):
        assert serial == "1234567890"
        assert immediate_gpio_update is False
        return fake
    monkeypatch.setitem(sys.modules, "mcp2210", SimpleNamespace(
        Mcp2210=construct, Mcp2210GpioDesignation=object(), Mcp2210GpioDirection=object()))
    p = Mcp2210Programmer()
    monkeypatch.setattr(p, "_detect", lambda: "1234567890")
    def fail(): raise RuntimeError("setup failed")
    monkeypatch.setattr(p, "_setup", fail)
    with pytest.raises(RuntimeError, match="setup failed"): p.open()
    assert calls == ["closed"] and p.mcp is None


def test_input_plan_validates_without_drawing_random_values(monkeypatch):
    monkeypatch.setattr(inputs.secrets, "token_bytes", lambda n: pytest.fail("randomness consumed"))
    p = inputs.InputPlan(n=2)
    assert p.validate_for_hardware("ascon") is p


def test_input_plan_finds_late_invalid_row_before_iteration():
    p = inputs.InputPlan(runs=[{"pt": bytes(16)}, {"pt": b"short"}])
    with pytest.raises(ValueError, match="row 2: pt.*16 bytes"):
        p.validate_for_hardware("aes1")


def test_input_plan_checks_generated_width_without_changing_generic_api():
    variables = {v: inputs.Variable() for v in inputs.VARS}
    variables["key"] = inputs.Variable(random=False, value=bytes(24))
    with pytest.raises(ValueError, match="key.*16 bytes"):
        inputs.InputPlan(variables=variables).validate_for_hardware("aes1")
    assert len(variables["key"].next()) == 24


def test_duplicate_input_field_is_not_silently_overwritten(tmp_path):
    path = tmp_path / "inputs.txt"; path.write_text("key=00 key=11\n")
    with pytest.raises(ValueError, match="line 1: duplicate field 'key'"):
        inputs.parse_input_file(path)
