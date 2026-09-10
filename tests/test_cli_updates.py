"""CLI boundary regressions: all device operations are replaced with fakes."""
import json
from types import SimpleNamespace
import pytest
from proact_host import cli


@pytest.fixture(autouse=True)
def no_hardware(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected device access")
    monkeypatch.setattr(cli, "_target", forbidden)
    from proact_host.programmer import Mcp2210Programmer
    monkeypatch.setattr(Mcp2210Programmer, "open", forbidden)


@pytest.mark.parametrize("argv", [
    ["run", "--core", "aes1", "--runs", "0"],
    ["run", "--core", "aes1", "--runs", "-1"],
    ["run", "--core", "aes1", "--inttrig", "128"],
    ["run", "--core", "ascon", "--decrypt"],
    ["run", "--core", "xoodyak", "--decrypt"],
    ["capture", "--core", "aes1", "--traces", "0"],
    ["capture", "--core", "aes1", "--samples", "-1"],
    ["capture", "--core", "aes1", "--clock", "nan"],
    ["capture", "--core", "aes1", "--clock", "0"],
    ["capture", "--core", "aes1", "--gain", "inf"],
    ["selfcheck", "--clock", "-5"],
    ["status", "--watch", "0"],
    ["status", "--watch", "nan"],
    ["monitor", "--secs", "-1"],
    ["peek", "--addr", "3"],
    ["peek", "--addr", "0", "--count", "0"],
    ["peek", "--addr", "0xfffffffc", "--count", "2"],
    ["poke", "--addr", "0xfffffffc", "--data", "1", "2"],
    ["poke", "--addr", "0", "--data", "0x100000000"],
    ["seed", "--value", "-1"],
])
def test_invalid_arguments_fail_before_device_access(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 2
    assert "error:" in capsys.readouterr().err


@pytest.mark.parametrize("contents", [None, "", "@0 GARBAGE", "@0 100000000"])
def test_bad_firmware_is_rejected_before_spi_open(tmp_path, contents, capsys):
    path = tmp_path / "bad.vmem"
    if contents is not None:
        path.write_text(contents)
    with pytest.raises(SystemExit) as exc:
        cli.main(["program", "--vmem", str(path)])
    assert exc.value.code == 2
    assert "firmware" in capsys.readouterr().err


@pytest.mark.parametrize("contents", [None, "@0 GARBAGE", "@0 100000000"])
def test_swrv_validates_both_files_before_uart(tmp_path, contents):
    path = tmp_path / "ok.vmem"
    path.write_text("@0 1")
    dmem = tmp_path / "dmem.vmem"
    if contents is not None:
        dmem.write_text(contents)
    with pytest.raises(SystemExit) as exc:
        cli.main(["load-swrv", "--imem", str(path), "--dmem", str(dmem)])
    assert exc.value.code == 2


def test_swrv_rejects_empty_instruction_image_before_uart(tmp_path):
    imem = tmp_path / "imem.vmem"
    imem.write_text("/* no instructions */\n")
    dmem = tmp_path / "dmem.vmem"
    dmem.write_text("@0 1")
    with pytest.raises(SystemExit) as exc:
        cli.main(["load-swrv", "--imem", str(imem), "--dmem", str(dmem)])
    assert exc.value.code == 2


@pytest.mark.parametrize("contents", ["", "/* no initialized data */\n"])
def test_swrv_accepts_empty_data_image_and_closes_uart(tmp_path, monkeypatch, contents):
    imem = tmp_path / "imem.vmem"
    imem.write_text("@0 00000013")
    dmem = tmp_path / "dmem.vmem"
    dmem.write_text(contents)
    events = []
    uart = SimpleNamespace(close=lambda: events.append("closed"))
    target = SimpleNamespace(
        load_swrv_program=lambda instructions, data, base:
            events.append((instructions, data, base)),
        set_key=lambda key: None,
        set_plaintext=lambda plaintext: None,
        run_and_read=lambda: (0, bytes(16)),
    )
    monkeypatch.setattr(cli, "_target", lambda args: (uart, target))
    cli.main(["load-swrv", "--imem", str(imem), "--dmem", str(dmem)])
    assert events == [([0x13], [], cli.regs.SWRV_DMEM_LOAD_BASE), "closed"]


def test_doctor_json_is_clean_and_identifies_this_copy(capsys):
    from proact_host import diagnostics
    cli.main(["doctor", "--json"])
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert not output.err
    assert report["hardware_accessed"] is False
    assert report["package"] == str(__import__("pathlib").Path(diagnostics.__file__).parent)
    assert {d["module"] for d in report["dependencies"]} >= {"numpy", "hid", "h5py", "PyQt6"}


def test_interrupt_has_conventional_exit_status(monkeypatch, capsys):
    def interrupted(_args):
        raise KeyboardInterrupt
    monkeypatch.setattr(cli, "cmd_status", interrupted)
    with pytest.raises(SystemExit) as exc:
        cli.main(["status"])
    assert exc.value.code == 130
    assert "Interrupted" in capsys.readouterr().err


def test_program_closes_both_handles_after_verification_failure(monkeypatch, tmp_path, capsys):
    from proact_host import programmer, transport
    closed = []
    class FakeProgrammer:
        def __init__(self, **kwargs): pass
        def open(self): return self
        def program(self, *args, **kwargs): pass
        def verify_running(self, uart): raise OSError("read failed")
        def close(self): closed.append("spi")
    class FakeUart:
        def __init__(self, **kwargs): pass
        def open(self): return self
        def close(self): closed.append("uart")
    monkeypatch.setattr(programmer, "Mcp2210Programmer", FakeProgrammer)
    monkeypatch.setattr(transport, "UartTransport", FakeUart)
    path = tmp_path / "ok.vmem"; path.write_text("@0 1")
    cli.main(["program", "--vmem", str(path)])
    assert closed == ["uart", "spi"]
    assert "could not be verified" in capsys.readouterr().out


def test_program_closes_spi_when_transfer_fails(monkeypatch, tmp_path):
    from proact_host import programmer
    closed = []
    class FakeProgrammer:
        def __init__(self, **kwargs): pass
        def open(self): return self
        def program(self, *args, **kwargs): raise OSError("transfer failed")
        def close(self): closed.append("spi")
    monkeypatch.setattr(programmer, "Mcp2210Programmer", FakeProgrammer)
    path = tmp_path / "ok.vmem"; path.write_text("@0 1")
    with pytest.raises(SystemExit) as exc:
        cli.main(["program", "--vmem", str(path)])
    assert exc.value.code == 1
    assert closed == ["spi"]


def test_partial_acquisition_reports_failure_after_saving(monkeypatch, capsys):
    from proact_host import experiment
    events = []
    class FakeExperiment:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): events.append("closed")
        def prepare(self): events.append("prepared")
        def capture(self): return 2
        def save(self): events.append("saved")
    monkeypatch.setattr(experiment, "PROACTExperiment", FakeExperiment)
    with pytest.raises(SystemExit) as exc:
        cli.main(["capture", "--core", "aes1", "--traces", "5"])
    assert exc.value.code == 1
    assert events == ["prepared", "saved", "closed"]
    assert "2 of 5" in capsys.readouterr().err
