"""Offline tests for Mcp2210Programmer.verify_running (BUG-011).

The SPI code-loader is write-only, so program() cannot tell whether the load
produced a running chip. verify_running() reboots the controller with the UART
listening and waits for the firmware boot banner. Both paths are exercised here
with in-memory fakes -- no MCP2210, no serial port, no board.
"""
from proact_host.programmer import Mcp2210Programmer


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
