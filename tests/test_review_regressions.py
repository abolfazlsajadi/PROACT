"""Independent regressions for reviewed preflight and UART cleanup fixes.

Every UART/lock endpoint here is an in-memory fake. Neither serial enumeration
nor the real open()/flock() functions can run in these tests.
"""
import io
import sys
from types import SimpleNamespace

import pytest

from proact_host import experiment, inputs, transport


@pytest.fixture(autouse=True)
def block_device_and_lock_access(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("review regression attempted real device or lock access")
    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(Serial=forbidden))
    monkeypatch.setattr(transport, "open", forbidden, raising=False)
    monkeypatch.setattr(transport, "fcntl", SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=forbidden))
    monkeypatch.setattr(transport.UartTransport, "_detect_port", forbidden)


def fixed_variables():
    return {field: inputs.Variable(random=False, value=bytes(16)) for field in inputs.VARS}


@pytest.mark.parametrize("missing", inputs.VARS)
def test_preflight_rejects_missing_generated_variables_even_for_aes(monkeypatch, missing):
    variables = fixed_variables()
    del variables[missing]
    plan = inputs.InputPlan(variables=variables)
    monkeypatch.setattr(inputs.secrets, "token_bytes", lambda _: pytest.fail("randomness consumed"))
    with pytest.raises(ValueError, match=missing):
        plan.validate_for_hardware("aes1")
    # Failed preflight must not partially replace caller-supplied variables.
    assert plan.vars is variables


@pytest.mark.parametrize("width", [16.0, True, -1, "16", 0, 15, 17])
def test_preflight_rejects_invalid_required_random_lengths_without_drawing(monkeypatch, width):
    variables = fixed_variables()
    variables["key"] = inputs.Variable(random=True, value=b"", nbytes=width)
    monkeypatch.setattr(inputs.secrets, "token_bytes", lambda _: pytest.fail("randomness consumed"))
    with pytest.raises(ValueError, match="key"):
        inputs.InputPlan(variables=variables).validate_for_hardware("aes1")


@pytest.mark.parametrize("width", [16.0, True, -1, "16"])
def test_preflight_checks_generation_validity_for_unused_aes_fields(width):
    variables = fixed_variables()
    variables["nonce"] = inputs.Variable(random=True, value=b"", nbytes=width)
    with pytest.raises(ValueError, match="nonce"):
        inputs.InputPlan(variables=variables).validate_for_hardware("aes1")


@pytest.mark.parametrize("field", inputs.VARS)
@pytest.mark.parametrize("mode", ["variables", "rows"])
def test_preflight_rejects_fixed_strings_instead_of_counting_characters(field, mode):
    if mode == "variables":
        variables = fixed_variables()
        variables[field] = inputs.Variable(random=False, value="x" * 16)
        plan = inputs.InputPlan(variables=variables)
    else:
        plan = inputs.InputPlan(runs=[{field: "x" * 16}])
    with pytest.raises(ValueError, match=field + ".*bytes-like"):
        plan.validate_for_hardware("ascon")


@pytest.mark.parametrize("mode", ["variables", "rows"])
def test_preflight_rejects_64_byte_memoryview_with_sixteen_elements(mode):
    view = memoryview(bytearray(64)).cast("I")
    assert len(view) == 16 and view.nbytes == 64
    if mode == "variables":
        variables = fixed_variables()
        variables["key"] = inputs.Variable(random=False, value=view)
        plan = inputs.InputPlan(variables=variables)
    else:
        plan = inputs.InputPlan(runs=[{"key": view}])
    with pytest.raises(ValueError, match="key.*exactly 16 bytes"):
        plan.validate_for_hardware("aes1")


@pytest.mark.parametrize("field", ["key", "fixed_input"])
def test_experiment_rejects_64_byte_memoryview_before_opening_devices(field):
    view = memoryview(bytearray(64)).cast("I")
    assert len(view) == 16 and view.nbytes == 64
    with pytest.raises(ValueError, match=field + ".*exactly 16 bytes"):
        experiment.PROACTExperiment(**{field: view})


@pytest.mark.parametrize("mode", ["variables", "rows"])
def test_16_byte_word_format_view_is_canonicalized_to_owned_bytes(mode):
    backing = bytearray(range(16))
    view = memoryview(backing).cast("I")
    assert len(view) == 4 and view.nbytes == 16
    if mode == "variables":
        variables = fixed_variables()
        variables["key"] = inputs.Variable(random=False, value=view)
        plan = inputs.InputPlan(variables=variables)
    else:
        plan = inputs.InputPlan(runs=[{"key": view}])
    plan.validate_for_hardware("aes1")
    backing[:] = bytes([255]) * 16
    row = next(iter(plan))
    assert type(row["key"]) is bytes
    assert row["key"] == bytes(range(16))


@pytest.mark.parametrize("field", ["key", "fixed_input"])
def test_experiment_accepts_and_copies_exact_16_byte_word_format_view(field):
    backing = bytearray(range(16))
    view = memoryview(backing).cast("I")
    exp = experiment.PROACTExperiment(**{field: view})
    backing[:] = bytes([255]) * 16
    assert type(getattr(exp, field)) is bytes
    assert getattr(exp, field) == bytes(range(16))
    assert exp.uart is None and exp.scope is None


def test_validated_variables_own_copies_of_mutable_values_and_specs():
    payloads = {field: bytearray([index]) * 16 for index, field in enumerate(inputs.VARS)}
    variables = {field: inputs.Variable(random=False, value=value) for field, value in payloads.items()}
    plan = inputs.InputPlan(variables=variables).validate_for_hardware("xoodyak")
    for field in inputs.VARS:
        payloads[field][:] = bytes([255]) * 16
        variables[field].value = b"changed"
        variables[field].random = True
    assert next(iter(plan)) == {field: bytes([index]) * 16 for index, field in enumerate(inputs.VARS)}


def test_validated_file_rows_own_copies_of_values_and_row_containers():
    key = bytearray(range(16))
    row = {"key": key}
    supplied = [row]
    plan = inputs.InputPlan(runs=supplied).validate_for_hardware("aes1")
    key[:] = bytes([255]) * 16
    row["key"] = b"changed"
    supplied.clear()
    result = list(plan)
    assert len(result) == 1 and plan.n == 1
    assert result[0]["key"] == bytes(range(16))
    assert all(type(value) is bytes for value in result[0].values())


def test_experiment_copies_mutable_key_and_fixed_input():
    key = bytearray(range(16))
    plaintext = bytearray(range(16, 32))
    exp = experiment.PROACTExperiment(key=key, fixed_input=plaintext)
    key[:] = bytes(16)
    plaintext[:] = bytes(16)
    assert exp.key == bytes(range(16)) and type(exp.key) is bytes
    assert exp.fixed_input == bytes(range(16, 32)) and type(exp.fixed_input) is bytes
    assert exp.randomize is False


@pytest.fixture
def fake_uart(monkeypatch):
    state = SimpleNamespace(lock_handles=[], serial_handles=[], flock_calls=[],
                            constructor_error=None, serial_close_error=None)
    class LockHandle(io.StringIO):
        def __init__(self):
            super().__init__()
            self.close_calls = 0
        def close(self):
            self.close_calls += 1
            super().close()
    class SerialHandle:
        def __init__(self):
            self.close_calls = 0
        def close(self):
            self.close_calls += 1
            if state.serial_close_error is not None:
                raise state.serial_close_error
    def lock_open(path, mode):
        assert path == "/tmp/proact_uart_OFFLINE_FAKE.lock"
        assert mode == "a"
        handle = LockHandle()
        state.lock_handles.append(handle)
        return handle
    def serial_open(**kwargs):
        assert kwargs == {"port": "/dev/OFFLINE_FAKE", "baudrate": 115200, "timeout": 2.0}
        if state.constructor_error is not None:
            raise state.constructor_error
        handle = SerialHandle()
        state.serial_handles.append(handle)
        return handle
    def flock(handle, operation):
        assert handle in state.lock_handles
        state.flock_calls.append((handle, operation))
    monkeypatch.setattr(transport, "open", lock_open)
    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(Serial=serial_open))
    monkeypatch.setattr(transport, "fcntl", SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=flock))
    return state


@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt])
def test_serial_constructor_failure_releases_lock_and_allows_retry(fake_uart, error_type):
    uart = transport.UartTransport(port="/dev/OFFLINE_FAKE")
    error = error_type("simulated serial constructor failure")
    fake_uart.constructor_error = error
    with pytest.raises(error_type) as raised:
        uart.open()
    assert raised.value is error
    assert len(fake_uart.lock_handles) == 1
    assert fake_uart.lock_handles[0].closed
    assert fake_uart.lock_handles[0].close_calls == 1
    assert uart._ser is None and uart._lock_fh is None
    uart.close()
    assert fake_uart.lock_handles[0].close_calls == 1
    fake_uart.constructor_error = None
    assert uart.open() is uart
    assert len(fake_uart.lock_handles) == 2 and len(fake_uart.serial_handles) == 1
    uart.close()
    assert fake_uart.lock_handles[1].closed


def test_serial_close_failure_still_releases_lock_and_second_close_is_harmless(fake_uart):
    uart = transport.UartTransport(port="/dev/OFFLINE_FAKE").open()
    fake_uart.serial_close_error = OSError("simulated serial close failure")
    with pytest.raises(OSError, match="serial close failure"):
        uart.close()
    assert fake_uart.serial_handles[0].close_calls == 1
    assert fake_uart.lock_handles[0].closed
    assert fake_uart.lock_handles[0].close_calls == 1
    assert uart._ser is None and uart._lock_fh is None
    uart.close()
    assert fake_uart.serial_handles[0].close_calls == 1
    assert fake_uart.lock_handles[0].close_calls == 1


def test_double_open_and_double_close_do_not_duplicate_or_leak_handles(fake_uart):
    uart = transport.UartTransport(port="/dev/OFFLINE_FAKE")
    assert uart.open() is uart
    assert uart.open() is uart
    assert len(fake_uart.serial_handles) == len(fake_uart.lock_handles) == len(fake_uart.flock_calls) == 1
    uart.close()
    uart.close()
    assert fake_uart.serial_handles[0].close_calls == 1
    assert fake_uart.lock_handles[0].close_calls == 1
    assert fake_uart.lock_handles[0].closed
    assert uart._ser is None and uart._lock_fh is None
