"""
Regression tests for the PROACT register/command map.

`Software/Python/proact_host/regs.py` and `Software/common/proact_regs.h` are
GENERATED from `config/hardware.json` by `scripts/gen_hardware.py`.  That map is
the contract shared by the C controller firmware, the Python host library and
the GUI, so a hand-edit or a stale regeneration desynchronizes host and silicon
in the most expensive possible way (wrong core enabled, wrong trigger bit, wrong
command byte -- all of which produce plausible-looking but meaningless traces).

These tests are OFFLINE and side-effect free.  The generator is loaded with
importlib and only its pure functions (`load`, `gen_py`, `gen_c`) are called --
`main()`, which WRITES the generated files, is never invoked.

The public host release does not include controller firmware or the C header.
Only cross-checks requiring those files skip when they are absent; the JSON,
generated Python and host invariant checks run in both release trees.

Note on the C header: `gen_c()` does *not* reproduce the committed
`proact_regs.h` textually, because the committed header carries a hand-added
comment block documenting the AEAD_LEN word layout.  The header is therefore
compared SEMANTICALLY, by parsing and evaluating its `#define` values.
"""
import importlib.util
import json
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN_SCRIPT = os.path.join(REPO_ROOT, "scripts", "gen_hardware.py")
HARDWARE_JSON = os.path.join(REPO_ROOT, "config", "hardware.json")
REGS_PY = os.path.join(REPO_ROOT, "Software", "Python", "proact_host", "regs.py")
REGS_H = os.path.join(REPO_ROOT, "Software", "common", "proact_regs.h")
CONTROLLER_MAIN_C = os.path.join(REPO_ROOT, "Software", "Controller", "main.c")

from proact_host import config, regs  # noqa: E402  (needs conftest's sys.path)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def load_generator():
    """Import scripts/gen_hardware.py without running it.

    The script guards its file-writing `main()` behind `if __name__ ==
    "__main__"`, and we load it under a different module name, so executing it
    only defines functions.  Nothing is written to disk.
    """
    spec = importlib.util.spec_from_file_location("_proact_gen_hardware", GEN_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_hardware_json():
    with open(HARDWARE_JSON) as f:
        return json.load(f)


HW = load_hardware_json()
GEN = load_generator()

# constant groups exported by regs.py.  CTRL_CFGSEL_SHIFT is a bit *position*,
# not a mask, so it is excluded from the CTRL_* bit family everywhere.
CTRL_BIT_NAMES = sorted(
    n for n in vars(regs) if n.startswith("CTRL_") and n != "CTRL_CFGSEL_SHIFT"
)
STAT_BIT_NAMES = sorted(n for n in vars(regs) if n.startswith("STAT_"))
CMD_NAMES = sorted(n for n in vars(regs) if n.startswith("CMD_"))
MODE_NAMES = sorted(n for n in vars(regs) if n.startswith("MODE_"))
CFGSEL_NAMES = sorted(n for n in vars(regs) if n.startswith("CFGSEL_"))

_C_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
_C_DEFINE = re.compile(r"^\s*#\s*define\s+([A-Za-z_]\w*)(\()?\s*(.*)$")
_C_INT_SUFFIX = re.compile(r"\b(0[xX][0-9a-fA-F]+|\d+)[uUlL]+\b")


def parse_c_defines(text):
    """Evaluate the object-like `#define`s of a C source into {name: int}.

    Handles line continuations, comments and integer suffixes (`1u << 30`).
    Function-like macros (e.g. AEAD_LEN_WORD) and anything that is not an
    integer expression over previously-defined names are ignored, which is what
    makes this a *semantic* comparison rather than a textual one.
    """
    text = text.replace("\\\n", " ")
    text = _C_COMMENT.sub(" ", text)
    values = {}
    for line in text.splitlines():
        m = _C_DEFINE.match(line)
        if not m:
            continue
        name, is_function_like, body = m.group(1), m.group(2), m.group(3).strip()
        if is_function_like or not body:
            continue
        expr = _C_INT_SUFFIX.sub(r"\1", body)
        try:
            value = eval(expr, {"__builtins__": {}}, dict(values))  # noqa: S307
        except Exception:  # noqa: BLE001 -- non-integer macro, not our business
            continue
        if isinstance(value, int):
            values[name] = value
    return values


def parse_c_enum_members(text, prefix):
    """Return {name: int} for `enum { A = 0, B = 1 };` members with `prefix`."""
    text = _C_COMMENT.sub(" ", text)
    members = {}
    for body in re.findall(r"enum\s*\{([^}]*)\}", text, re.S):
        implicit = 0
        for item in body.split(","):
            item = item.strip()
            if not item:
                continue
            if "=" in item:
                name, raw = (part.strip() for part in item.split("=", 1))
                implicit = int(raw, 0)
            else:
                name = item
            if name.startswith(prefix):
                members[name] = implicit
            implicit += 1
    return members


def controller_constants():
    with open(CONTROLLER_MAIN_C) as f:
        text = f.read()
    consts = parse_c_defines(text)
    consts.update(parse_c_enum_members(text, "MODE_"))
    return consts


@pytest.fixture(scope="module")
def controller():
    if not os.path.isfile(CONTROLLER_MAIN_C):
        pytest.skip("controller source is not included in the public host release")
    return controller_constants()


@pytest.fixture(scope="module")
def c_header():
    if not os.path.isfile(REGS_H):
        pytest.skip("C register header is not included in the public host release")
    with open(REGS_H) as f:
        return parse_c_defines(f.read())


def is_single_bit(value):
    return isinstance(value, int) and value > 0 and value & (value - 1) == 0


# --------------------------------------------------------------------------
# 1. regeneration: the committed files still match the generator
# --------------------------------------------------------------------------
def test_regs_py_is_byte_for_byte_what_the_generator_emits():
    """The committed regs.py must be exactly gen_py(hardware.json).

    A mismatch means someone hand-edited the generated file or edited
    hardware.json without re-running scripts/gen_hardware.py.
    """
    with open(REGS_PY) as f:
        committed = f.read()
    assert GEN.gen_py(GEN.load()) == committed, (
        "regs.py is out of sync with config/hardware.json -- "
        "re-run scripts/gen_hardware.py"
    )


def test_generator_reads_the_same_hardware_json_this_test_reads():
    assert os.path.realpath(GEN.SRC) == os.path.realpath(HARDWARE_JSON)
    assert GEN.load() == HW


def test_committed_c_header_semantically_matches_the_generator(c_header):
    """Compare parsed #define VALUES, not text.

    The committed header intentionally carries an extra hand-written comment
    block for AEAD_LEN, so a textual comparison would be a false alarm; the
    numbers are what the firmware compiles against.
    """
    generated = parse_c_defines(GEN.gen_c(HW))
    assert c_header == generated


def test_python_and_c_maps_agree_on_every_shared_constant(c_header):
    """regs.py and proact_regs.h must not drift apart.

    Device bases are PROACT_*_BASE in C and *_BASE in Python; everything else
    shares a name.
    """
    header = c_header
    shared = 0
    for name, value in vars(regs).items():
        if name.startswith("_") or not isinstance(value, int):
            continue
        c_name = name if name in header else "PROACT_" + name
        if c_name in header:
            shared += 1
            assert header[c_name] == value, "%s: C=%#x Python=%#x" % (
                c_name, header[c_name], value)
    # sanity: the overlap is the whole register map, not an empty intersection
    assert shared >= 60


# --------------------------------------------------------------------------
# 2. no duplicate values inside a namespace
# --------------------------------------------------------------------------
def test_control_bits_are_18_and_pairwise_distinct():
    values = {n: getattr(regs, n) for n in CTRL_BIT_NAMES}
    assert len(values) == 18
    assert len(set(values.values())) == len(values), "duplicate CTRL_* bit: %r" % (
        sorted(values.items(), key=lambda kv: kv[1]),)


def test_status_bits_are_18_and_pairwise_distinct():
    values = {n: getattr(regs, n) for n in STAT_BIT_NAMES}
    assert len(values) == 18
    assert len(set(values.values())) == len(values)


def test_command_bytes_are_pairwise_distinct():
    values = {n: getattr(regs, n) for n in CMD_NAMES}
    assert len(values) == len(CMD_NAMES)   # every name resolves
    assert len(set(values.values())) == len(values), "duplicate CMD_* byte: %r" % (
        sorted(values.items(), key=lambda kv: kv[1]),)


def test_mode_ids_are_pairwise_distinct():
    values = {n: getattr(regs, n) for n in MODE_NAMES}
    assert len(set(values.values())) == len(values)


# --------------------------------------------------------------------------
# 3. control-register bit invariants
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", CTRL_BIT_NAMES)
def test_control_bit_is_a_single_bit_inside_32_bits(name):
    value = getattr(regs, name)
    assert is_single_bit(value), "%s = %#x is not a power of two" % (name, value)
    assert value < 1 << 32


def test_control_trigger_is_bit30_and_nothing_uses_bit31():
    """SCREG's WRITE side is 31 bits wide: data bit31 is truncated by hardware,
    so the capture trigger lives at bit30.  A CTRL_* constant at bit31 would be
    silently dropped by the SoC and never fire the scope."""
    assert regs.CTRL_TRIGGER == 1 << 30
    assert regs.CTRL_TRIGGERPC == 1 << 29
    for name in CTRL_BIT_NAMES:
        assert not getattr(regs, name) & (1 << 31), "%s uses truncated bit31" % name


def test_control_bits_match_hardware_json_entry_for_entry():
    expected = {
        "CTRL_" + k: 1 << v
        for k, v in HW["control_bits"].items()
        if not k.startswith("_")
    }
    got = {n: getattr(regs, n) for n in CTRL_BIT_NAMES}
    assert got == expected


# --------------------------------------------------------------------------
# 4. status-register bit invariants
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", STAT_BIT_NAMES)
def test_status_bit_is_a_single_bit_inside_32_bits(name):
    value = getattr(regs, name)
    assert is_single_bit(value), "%s = %#x is not a power of two" % (name, value)
    assert value < 1 << 32


def test_status_target_done_is_bit31():
    """Unlike the control side, the READ side of SCREG really is 32-bit, and
    bit31 is the Sw-RV target-done handshake / trigger source."""
    assert regs.STAT_TARGET_DONE == 1 << 31
    assert int(HW["swrv_trigger"]["trigger_bit"], 0) == regs.STAT_TARGET_DONE


def test_status_bits_match_hardware_json_entry_for_entry():
    expected = {
        "STAT_" + k: 1 << v
        for k, v in HW["status_bits"].items()
        if not k.startswith("_")
    }
    got = {n: getattr(regs, n) for n in STAT_BIT_NAMES}
    assert got == expected


# --------------------------------------------------------------------------
# 5. CFGSEL trigger-source mux
# --------------------------------------------------------------------------
def test_cfgsel_shift_matches_hardware_json():
    assert regs.CTRL_CFGSEL_SHIFT == HW["control_fields"]["CFGSEL"]["shift"] == 20


@pytest.mark.parametrize(
    "name,raw", sorted(HW["control_fields"]["CFGSEL"]["values"].items())
)
def test_cfgsel_encoding_is_raw_value_shifted(name, raw):
    assert getattr(regs, "CFGSEL_" + name) == raw << regs.CTRL_CFGSEL_SHIFT


def test_cfgsel_values_are_distinct_and_fit_the_declared_field_width():
    field = HW["control_fields"]["CFGSEL"]
    raw_values = [v for v in field["values"].values()]
    assert sorted(raw_values) == [0, 1, 2, 3, 4, 5]
    assert len(set(raw_values)) == len(raw_values)
    mask = (1 << field["width"]) - 1
    for raw in raw_values:
        assert raw & mask == raw, "cfg_sel value %d overflows the %d-bit field" % (
            raw, field["width"])
    encoded = {getattr(regs, n) for n in CFGSEL_NAMES}
    assert len(encoded) == len(CFGSEL_NAMES) == 6


def test_cfgsel_field_does_not_overlap_any_control_bit():
    field = HW["control_fields"]["CFGSEL"]
    field_mask = ((1 << field["width"]) - 1) << field["shift"]
    for name in CTRL_BIT_NAMES:
        assert not getattr(regs, name) & field_mask, (
            "%s collides with the CFGSEL field" % name)


# --------------------------------------------------------------------------
# 6. device bases
# --------------------------------------------------------------------------
def _base_attr(device_name):
    if device_name == "CORE_UNIMPLEMENTED":
        return "CORE_BASE_DO_NOT_USE"
    return device_name + "_BASE"


@pytest.mark.parametrize("device", HW["devices"], ids=lambda d: d["name"])
def test_device_base_matches_hardware_json(device):
    attr = _base_attr(device["name"])
    assert getattr(regs, attr) == int(device["base"], 16)


def test_device_bases_are_distinct_and_word_aligned():
    bases = {_base_attr(d["name"]): int(d["base"], 16) for d in HW["devices"]}
    assert len(set(bases.values())) == len(bases), "two devices share a base address"
    for name, base in bases.items():
        assert base % 4 == 0, "%s = %#x is not 4-byte aligned" % (name, base)
        assert 0 < base < 1 << 32


def test_regs_exports_exactly_the_json_device_bases():
    """No extra and no missing bus device -- SWRV_DMEM_LOAD_BASE is excluded
    because it is a mailbox load address inside RII_DMEM, not a bus device."""
    exported = {
        n for n in vars(regs)
        if (n.endswith("_BASE") or n == "CORE_BASE_DO_NOT_USE")
        and n != "SWRV_DMEM_LOAD_BASE"
    }
    expected = {_base_attr(d["name"]) for d in HW["devices"]}
    assert exported == expected


def test_do_not_use_base_is_the_unimplemented_core_and_aliases_nothing():
    """CORE_BASE_DO_NOT_USE has no hardware instance -- touching it hangs the
    CPU.  It must stay distinct from every usable core base so no code can
    reach it by accident."""
    assert regs.CORE_BASE_DO_NOT_USE == 0x10007000
    unimplemented = [d for d in HW["devices"] if d["name"] == "CORE_UNIMPLEMENTED"]
    assert len(unimplemented) == 1
    assert int(unimplemented[0]["base"], 16) == regs.CORE_BASE_DO_NOT_USE
    usable = [
        getattr(regs, _base_attr(d["name"]))
        for d in HW["devices"]
        if d["name"] != "CORE_UNIMPLEMENTED"
    ]
    assert regs.CORE_BASE_DO_NOT_USE not in usable


# --------------------------------------------------------------------------
# 7. offsets, UART and mailbox
# --------------------------------------------------------------------------
def test_aes_offsets_match_hardware_json_and_are_word_aligned():
    expected = {
        "AES_" + k: int(v, 16)
        for k, v in HW["aes_offsets"].items()
        if not k.startswith("_")
    }
    got = {n: getattr(regs, n) for n in vars(regs) if n.startswith("AES_")}
    assert got == expected
    assert len(set(got.values())) == len(got)
    for name, off in got.items():
        assert off % 4 == 0, "%s = %#x is not word aligned" % (name, off)


def test_aead_offsets_match_hardware_json_and_are_word_aligned():
    expected = {
        "AEAD_" + k: int(v, 16)
        for k, v in HW["aead_offsets"].items()
        if not k.startswith("_")
    }
    got = {n: getattr(regs, n) for n in vars(regs) if n.startswith("AEAD_")}
    assert got == expected
    assert len(set(got.values())) == len(got)
    for name, off in got.items():
        assert off % 4 == 0, "%s = %#x is not word aligned" % (name, off)


def test_uart_constants_match_hardware_json():
    uart = HW["uart"]
    assert regs.UART_RXTX == int(uart["RXTX"], 16)
    assert regs.UART_BAUD == int(uart["BAUD"], 16)
    assert regs.UART_BAUD_DEFAULT == uart["baud_default_divisor"] == 27
    assert regs.UART_STATUS_RX_EMPTY == 1 << uart["status_rx_empty_bit"]
    assert regs.UART_STATUS_TX_FULL == 1 << uart["status_tx_full_bit"]
    assert regs.UART_STATUS_RX_EMPTY != regs.UART_STATUS_TX_FULL


def test_mailbox_addresses_match_hardware_json_and_live_in_the_dmem_window():
    mb = HW["mailbox"]
    addrs = {}
    for key in ("KEY", "IN", "OUT", "CMD", "DONE"):
        value = getattr(regs, "MBOX_" + key)
        assert value == int(mb[key], 16)
        addrs["MBOX_" + key] = value
    assert regs.SWRV_DMEM_LOAD_BASE == int(mb["swrv_dmem_load_base"], 16)
    assert len(set(addrs.values())) == len(addrs)
    dmem_mask = int(
        [d for d in HW["devices"] if d["name"] == "RII_DMEM"][0]["mask"], 16)
    for name, addr in list(addrs.items()) + [
            ("SWRV_DMEM_LOAD_BASE", regs.SWRV_DMEM_LOAD_BASE)]:
        assert addr % 4 == 0, "%s = %#x is not word aligned" % (name, addr)
        assert addr & dmem_mask == regs.RII_DMEM_BASE, (
            "%s = %#x is outside the Sw-RV data-memory window" % (name, addr))
    assert (regs.MBOX_CMD_IDLE, regs.MBOX_CMD_ENCRYPT, regs.MBOX_CMD_DECRYPT) == (
        mb["CMD_IDLE"], mb["CMD_ENCRYPT"], mb["CMD_DECRYPT"])
    assert len({regs.MBOX_CMD_IDLE, regs.MBOX_CMD_ENCRYPT, regs.MBOX_CMD_DECRYPT}) == 3


# --------------------------------------------------------------------------
# 8. UART command protocol
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", CMD_NAMES)
def test_command_byte_is_a_single_nonzero_byte(name):
    value = getattr(regs, name)
    assert 0 < value < 256, "%s = %r is not a usable command byte" % (name, value)


def test_command_bytes_match_hardware_json_entry_for_entry():
    expected = {
        "CMD_" + k: v
        for k, v in HW["uart_protocol"].items()
        if k not in ("_comment", "modes", "FRAME_MARKER")
    }
    got = {n: getattr(regs, n) for n in CMD_NAMES}
    assert got == expected


def test_mode_ids_match_hardware_json_entry_for_entry():
    expected = {"MODE_" + k: v for k, v in HW["uart_protocol"]["modes"].items()}
    got = {n: getattr(regs, n) for n in MODE_NAMES}
    assert got == expected


def test_frame_marker_is_a5_and_collides_with_no_mode_id():
    """The reply framing is <0xA5><mode><len>..., so a MODE_* equal to the
    marker would make the host resynchronize on the wrong byte."""
    assert regs.FRAME_MARKER == 0xA5 == HW["uart_protocol"]["FRAME_MARKER"]
    for name in MODE_NAMES:
        assert getattr(regs, name) != regs.FRAME_MARKER, (
            "%s collides with FRAME_MARKER" % name)
    for name in CMD_NAMES:
        assert getattr(regs, name) != regs.FRAME_MARKER


# --------------------------------------------------------------------------
# 9. cross-check against the controller firmware (Software/Controller/main.c)
# --------------------------------------------------------------------------
def test_controller_main_c_was_actually_parsed(controller):
    """Guard the cross-checks below: an empty parse would make them vacuous."""
    assert controller.get("CMD_KEY") == 0x01
    assert len([k for k in controller if k.startswith("CMD_")]) >= 23
    assert len([k for k in controller if k.startswith("MODE_")]) >= 7


@pytest.mark.parametrize("name", CMD_NAMES)
def test_command_byte_matches_the_controller_firmware(name, controller):
    assert name in controller, "%s is in regs.py but not in main.c" % name
    assert controller[name] == getattr(regs, name), (
        "%s: firmware=%#x host=%#x" % (name, controller[name], getattr(regs, name)))


@pytest.mark.parametrize("name", MODE_NAMES)
def test_mode_id_matches_the_controller_firmware(name, controller):
    assert name in controller, "%s is in regs.py but not in main.c" % name
    assert controller[name] == getattr(regs, name)


def test_frame_marker_matches_the_controller_firmware(controller):
    assert controller["FRAME_MARKER"] == regs.FRAME_MARKER


def test_hardware_json_covers_every_controller_command_byte(controller):
    # BUG-008 regression: every CMD_* in main.c must be in the single source of
    # truth (hardware.json -> regs.py). CMD_POKE/PEEK/AEADKAT were added so
    # transport.py's getattr() fallbacks resolve to the real constants and the
    # next byte allocated from the JSON cannot collide with firmware.
    firmware = {k for k in controller if k.startswith("CMD_")}
    assert firmware == set(CMD_NAMES), "missing from regs.py: %s" % sorted(
        firmware - set(CMD_NAMES))


def test_hardware_json_covers_every_controller_frame_mode(controller):
    # BUG-008 regression: MODE_PEEK 0xF3 / MODE_AEADKAT 0xF4 now named in regs.py.
    firmware = {k for k in controller if k.startswith("MODE_")}
    assert firmware == set(MODE_NAMES), "missing from regs.py: %s" % sorted(
        firmware - set(MODE_NAMES))


# --------------------------------------------------------------------------
# 10. proact_host.config -- same source of truth, pure arithmetic
# --------------------------------------------------------------------------
def test_input_clock_hz_comes_from_hardware_json():
    assert config.INPUT_CLOCK_HZ == HW["clock_hz_default"] == 50_000_000


def test_clock_from_hardware_json_helper_reads_the_real_file():
    """The default argument must never be what is actually returned here --
    otherwise a moved/renamed hardware.json would silently fall back."""
    assert config._clock_from_hardware_json(default=1) == HW["clock_hz_default"]


def test_divisor_for_115200_baud_is_the_reset_default():
    assert config.divisor_for_baud(115200) == 27 == regs.UART_BAUD_DEFAULT


def test_baud_for_reset_divisor_is_within_one_percent_of_115200():
    actual = config.baud_for_divisor(regs.UART_BAUD_DEFAULT)
    assert abs(actual - 115200) / 115200 < 0.01, (
        "divisor %d gives %.1f baud" % (regs.UART_BAUD_DEFAULT, actual))


def test_divisor_and_baud_helpers_are_inverse_within_rounding():
    for baud in (9600, 19200, 38400, 57600, 115200, 230400):
        divisor = config.divisor_for_baud(baud)
        assert abs(config.baud_for_divisor(divisor) - baud) / baud < 0.05


@pytest.mark.parametrize("baud", [10**9, 10**12, config.INPUT_CLOCK_HZ])
def test_divisor_for_baud_clamps_to_one(baud):
    """clock/(16*baud) rounds to 0 for absurd bauds; a 0 divisor would be a
    divide-by-zero on the way back and an illegal UART programming."""
    assert config.divisor_for_baud(baud) == 1


def test_divisor_for_baud_honours_an_explicit_clock():
    assert config.divisor_for_baud(115200, clock_hz=100_000_000) == 54
    assert config.divisor_for_baud(115200, clock_hz=50_000_000) == 27
    # explicit clock must win over the module global, without mutating it
    before = config.INPUT_CLOCK_HZ
    assert config.divisor_for_baud(9600, clock_hz=25_000_000) == round(
        25_000_000 / (16 * 9600))
    assert config.INPUT_CLOCK_HZ == before


def test_baud_for_divisor_honours_an_explicit_clock():
    assert config.baud_for_divisor(27, clock_hz=100_000_000) == pytest.approx(
        100_000_000 / (16 * 27))
    assert config.baud_for_divisor(27) == pytest.approx(
        config.INPUT_CLOCK_HZ / (16 * 27))


# --------------------------------------------------------------------------
# 11. proact_host.config.PINS -- MCP2210 GPIO map
# --------------------------------------------------------------------------
PIN_OUTPUTS = ("controller_reset", "spi_reset", "global_reset", "spi_select")
PIN_READBACKS = (
    "read_controller_reset", "read_spi_reset", "read_global_reset", "read_spi_select")


def test_pins_are_distinct_and_within_the_mcp2210_gpio_range():
    pins = {name: getattr(config.PINS, name) for name in PIN_OUTPUTS + PIN_READBACKS}
    assert len(pins) == 8
    assert len(set(pins.values())) == 8, "two MCP2210 roles share a GPIO: %r" % (pins,)
    for name, gpio in pins.items():
        assert isinstance(gpio, int)
        assert 0 <= gpio <= 8, "%s = %r is not an MCP2210 GPIO" % (name, gpio)


def test_output_pins_are_disjoint_from_readback_pins():
    """Driving a reset line and reading it back must be two different GPIOs --
    an overlap would make the reset self-confirm and hide a stuck board."""
    outputs = {getattr(config.PINS, n) for n in PIN_OUTPUTS}
    readbacks = {getattr(config.PINS, n) for n in PIN_READBACKS}
    assert len(outputs) == len(readbacks) == 4
    assert outputs.isdisjoint(readbacks)


def test_pins_default_instance_matches_a_fresh_dataclass():
    """config.PINS is module state that callers may be tempted to mutate; the
    shipped default must be the CAD- and bench-confirmed map."""
    assert config.PINS == config.Mcp2210Pins()
    assert config.Mcp2210Pins() == config.Mcp2210Pins(
        controller_reset=5,
        read_controller_reset=6,
        spi_reset=1,
        read_spi_reset=0,
        global_reset=2,
        read_global_reset=8,
        spi_select=4,
        read_spi_select=3,
    )
