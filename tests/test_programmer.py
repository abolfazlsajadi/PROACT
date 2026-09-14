"""Offline tests for Mcp2210Programmer setup and verify_running (BUG-011).

The SPI code-loader is write-only, so program() cannot tell whether the load
produced a running chip. verify_running() reboots the controller with the UART
listening and waits for the firmware boot banner. Both paths are exercised here
with in-memory fakes -- no MCP2210, no serial port, no board.
"""
import sys
import types
from pathlib import Path

import pytest

import proact_host.programmer as programmer_module
from proact_host.programmer import Mcp2210Programmer, _LockedMcp
from proact_host.resets import ResetController


class _FakeMcp:
    """Records GPIO writes; verify_running only needs set_gpio_output_value."""
    def __init__(self):
        self.writes = []

    def set_gpio_output_value(self, pin, value):
        self.writes.append((pin, value))


class _FakeUart:
    """Serves `feed` bytes on the first read after a reset, then nothing."""
    def __init__(self, feed=b""):
        self._feed = feed
        self._served = False
        self.flushed = False

    def reset_input_buffer(self):
        self.flushed = True

    def read_available(self):
        if self._served:
            return b""
        self._served = True
        return self._feed


def _programmer(fake_mcp):
    p = Mcp2210Programmer.__new__(Mcp2210Programmer)   # skip open()/hardware
    from proact_host import config
    p.pins = config.PINS
    p.mcp = fake_mcp
    p.lock = None
    return p


def _install_fake_sdk(monkeypatch, mcp_class):
    module = types.ModuleType("mcp2210")
    module.Mcp2210 = mcp_class
    module.Mcp2210GpioDesignation = type("Designation", (), {"GPIO": "gpio"})
    module.Mcp2210GpioDirection = type(
        "Direction", (), {"INPUT": "input", "OUTPUT": "output"})
    monkeypatch.setitem(sys.modules, "mcp2210", module)


def test_open_rejects_unvalidated_sdk_before_detection(monkeypatch):
    class _ShouldNotConstruct:
        def __init__(self, *args, **kwargs):
            pytest.fail("unsupported SDK reached the device constructor")

    _install_fake_sdk(monkeypatch, _ShouldNotConstruct)
    monkeypatch.setattr(programmer_module, "distribution_version", lambda _: "1.0.8")
    prog = Mcp2210Programmer()
    monkeypatch.setattr(prog, "_detect", lambda: pytest.fail("hardware detection started"))

    with pytest.raises(RuntimeError, match=r"validated only with 1\.0\.4"):
        prog.open()


def test_open_rejects_missing_sdk_metadata_before_detection(monkeypatch):
    class _ShouldNotConstruct:
        def __init__(self, *args, **kwargs):
            pytest.fail("unverified SDK reached the device constructor")

    _install_fake_sdk(monkeypatch, _ShouldNotConstruct)
    def missing(_distribution):
        raise programmer_module.PackageNotFoundError
    monkeypatch.setattr(programmer_module, "distribution_version", missing)
    prog = Mcp2210Programmer()
    monkeypatch.setattr(prog, "_detect", lambda: pytest.fail("hardware detection started"))

    with pytest.raises(RuntimeError, match=r"Cannot verify.*mcp2210-python==1\.0\.4"):
        prog.open()


def test_open_uses_public_batched_constructor_option(monkeypatch):
    calls = []

    class _Sdk:
        def __init__(self, serial, immediate_gpio_update=True):
            calls.append((serial, immediate_gpio_update))
            self._immediate_gpio_update = immediate_gpio_update

    _install_fake_sdk(monkeypatch, _Sdk)
    monkeypatch.setattr(programmer_module, "distribution_version", lambda _: "1.0.4")
    prog = Mcp2210Programmer()
    monkeypatch.setattr(prog, "_detect", lambda: "TEST-MCP2210-001")
    monkeypatch.setattr(prog, "_setup", lambda: None)

    assert prog.open() is prog
    assert calls == [("TEST-MCP2210-001", False)]


def test_dependency_manifests_pin_the_validated_sdk():
    root = Path(__file__).resolve().parents[1]
    expected = "mcp2210-python==1.0.4"
    assert expected in (root / "requirements.txt").read_text()
    assert expected in (root / "Software/Python/pyproject.toml").read_text()


def test_setup_defines_all_gpio_directions_and_keeps_x1_debug_as_input():
    class _Settings:
        # Documented run state: global/controller high, SPI/select low.
        gpio_input_level = (1 << 2) | (1 << 5) | (1 << 6) | (1 << 8)

    class _SetupMcp:
        def __init__(self):
            self._immediate_gpio_update = False
            self._gpio_output_needs_update = False
            self._gpio_settings = _Settings()
            self.designations = []
            self.directions = []
            self.outputs = []
            self.updates = 0
            self.publications = []

        def configure_spi_timing(self, **kwargs):
            pass

        def set_spi_mode(self, mode):
            pass

        def set_gpio_designation(self, pin, designation):
            self.designations.append((pin, designation))

        def set_gpio_direction(self, pin, direction):
            self.directions.append((pin, direction))

        def set_gpio_output_value(self, pin, value):
            self.outputs.append((pin, value))
            self._gpio_output_needs_update = True

        def gpio_update(self):
            self.updates += 1
            self.publications.append({
                "directions": dict(self.directions),
                "outputs": dict(self.outputs),
            })

        def get_gpio_value(self, pin):
            if self._gpio_output_needs_update:
                pytest.fail("status read would rewrite the GPIO output mask")
            return bool(self._gpio_settings.gpio_input_level & (1 << pin))

    class _Designation:
        GPIO = "gpio"

    class _Direction:
        INPUT = "input"
        OUTPUT = "output"

    fake = _SetupMcp()
    prog = _programmer(fake)
    prog._Desig = _Designation
    prog._Dir = _Direction
    prog._setup()

    assert fake.designations == [(pin, "gpio") for pin in range(9)]
    assert dict(fake.directions) == {
        0: "input",
        1: "output",
        2: "output",
        3: "input",
        4: "output",
        5: "output",
        6: "input",
        7: "input",
        8: "input",
    }
    assert dict(fake.outputs) == {1: False, 2: True, 4: False, 5: True}
    assert fake.updates == 1
    assert fake.publications == [{
        "directions": {
            0: "input", 1: "output", 2: "output",
            3: "input", 4: "output", 5: "output",
            6: "input", 7: "input", 8: "input",
        },
        "outputs": {1: False, 2: True, 4: False, 5: True},
    }]
    assert fake._gpio_output_needs_update is False
    assert ResetController(prog).status() == {
        "controller": True,
        "global": True,
        "spi": False,
        "spi_select": False,
    }


def test_setup_refuses_immediate_gpio_updates_before_any_gpio_write():
    class _Settings:
        gpio_input_level = (1 << 2) | (1 << 5)

    class _UnsafeMcp:
        _immediate_gpio_update = True
        _gpio_settings = _Settings()

        def configure_spi_timing(self, **kwargs):
            pytest.fail("unsafe setup started changing the device")

    prog = _programmer(_UnsafeMcp())
    with pytest.raises(RuntimeError, match="safe batched mode"):
        prog._setup()


@pytest.mark.parametrize("missing", ["levels", "dirty_flag"])
def test_setup_refuses_missing_sdk_cache_before_any_gpio_write(missing):
    class _Settings:
        if missing != "levels":
            gpio_input_level = (1 << 2) | (1 << 5)

    class _UnsafeMcp:
        _immediate_gpio_update = False
        _gpio_settings = _Settings()

        if missing != "dirty_flag":
            _gpio_output_needs_update = False

        def configure_spi_timing(self, **kwargs):
            pytest.fail("unsafe setup started changing the device")

    prog = _programmer(_UnsafeMcp())
    with pytest.raises(RuntimeError, match="refusing unsafe setup"):
        prog._setup()


def test_locked_mcp_clears_sdk_output_dirty_flag_after_immediate_write():
    class _Sdk:
        _immediate_gpio_update = True
        _gpio_output_needs_update = False

        def __init__(self):
            self.commands = []

        def set_gpio_output_value(self, pin, value):
            self.commands.append(("set", pin, value))
            self._gpio_output_needs_update = True

        def get_gpio_value(self, pin):
            if self._gpio_output_needs_update:
                self.commands.append(("unexpected-repeat-set",))
            self.commands.append(("get", pin))
            return True

    sdk = _Sdk()
    mcp = _LockedMcp(sdk)
    mcp.set_gpio_output_value(5, True)
    assert sdk._gpio_output_needs_update is False
    assert mcp.get_gpio_value(6) is True
    assert sdk.commands == [("set", 5, True), ("get", 6)]


def test_verify_running_true_when_the_banner_arrives():
    prog = _programmer(_FakeMcp())
    uart = _FakeUart(Mcp2210Programmer.BOOT_BANNER + b"\n")
    assert prog.verify_running(uart, timeout=1.0) is True
    assert uart.flushed is True          # it drained stale bytes first


def test_verify_running_true_even_if_banner_is_split_and_surrounded_by_noise():
    prog = _programmer(_FakeMcp())
    uart = _FakeUart(b"\x00\xffPROACT controller ready.\nrubbish")
    assert prog.verify_running(uart, timeout=1.0) is True


def test_verify_running_false_on_silence():
    prog = _programmer(_FakeMcp())
    uart = _FakeUart(b"")                 # nothing ever answers
    assert prog.verify_running(uart, timeout=0.2) is False


def test_verify_running_false_on_garbage_without_the_banner():
    prog = _programmer(_FakeMcp())
    uart = _FakeUart(b"\x01\x02 not the banner \xaa\xbb")
    assert prog.verify_running(uart, timeout=0.2) is False


def test_verify_running_actually_reboots_the_controller():
    fake = _FakeMcp()
    prog = _programmer(fake)
    prog.verify_running(_FakeUart(b""), timeout=0.1)
    # restart_controller pulses controller_reset low then high.
    ctrl = prog.pins.controller_reset
    seq = [v for (pin, v) in fake.writes if pin == ctrl]
    assert seq[-2:] == [False, True], "controller_reset must be pulsed low->high"


def test_verify_running_falls_back_when_no_reset_input_buffer():
    # A transport without reset_input_buffer() must still work: verify_running
    # drains the stale bytes via read_available() instead.
    class _Bare:
        def __init__(self, feed):
            self._feed, self._calls = feed, 0

        def read_available(self):
            self._calls += 1
            if self._calls == 1:      # the pre-reboot drain sees no stale bytes
                return b""
            if self._calls == 2:      # first post-reboot read delivers the banner
                return self._feed
            return b""

    prog = _programmer(_FakeMcp())
    assert not hasattr(_Bare(b""), "reset_input_buffer")
    uart = _Bare(Mcp2210Programmer.BOOT_BANNER)
    assert prog.verify_running(uart, timeout=1.0) is True
