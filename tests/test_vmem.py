"""
Regression tests for proact_host/vmem.py and Mcp2210Programmer._frame.

`parse_vmem` turns the byte-swapped `.vmem` produced by
`srec_cat ... -byte-swap 4 -vmem` into the (word_address, value) list that the
SPI code loader streams into controller memory, and that `cmd_load_swrv` /
fullcheck stream into the Sw-RV target. The address bookkeeping is the whole
point: the address advances by one per data word and is re-based by every `@`
line. A fencepost error here writes firmware to the wrong memory.

`Mcp2210Programmer._frame` is a @staticmethod that packs the
{addr[31:0], data[31:0]} 64-bit frame MSB-first, so it can be exercised with
no MCP2210 present -- it is the direct consumer of `parse_vmem` output, which
makes the two one coherent, offline area.

OFFLINE ONLY: nothing here opens a HID/serial device. `proact_host.programmer`
imports mcp2210/hid lazily inside open()/program(), so importing the module is
safe; open()/program()/restart_controller() are never called.
"""
import os

import pytest

from proact_host.programmer import Mcp2210Programmer
from proact_host.vmem import parse_vmem, vmem_values


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def write_vmem(tmp_path, text, name="fw.vmem"):
    """Write `text` to a temp .vmem and return its absolute path."""
    p = tmp_path / name
    p.write_text(text)
    return str(p)


GOOD_VMEM = (
    "/* header */\n"
    "\n"
    "@00000000 DEADBEEF 12345678\n"
    "AABBCCDD\n"
    "@00000010\n"
    "0000FFFF\n"
)


# --------------------------------------------------------------------------
# parse_vmem: the canonical file
# --------------------------------------------------------------------------

def test_parse_vmem_good_file(tmp_path):
    """Header, blank line, @-line with data, bare continuation, bare @-line."""
    words = parse_vmem(write_vmem(tmp_path, GOOD_VMEM))
    assert words == [
        (0x00000000, 0xDEADBEEF),
        (0x00000001, 0x12345678),
        (0x00000002, 0xAABBCCDD),   # continuation keeps counting from the @ base
        (0x00000010, 0x0000FFFF),   # bare @ line re-bases the address
    ]


def test_address_increments_across_continuation_lines(tmp_path):
    """One word == one address step, regardless of how lines are broken up."""
    text = (
        "@00000100 00000001 00000002 00000003\n"
        "00000004 00000005\n"
        "00000006\n"
    )
    words = parse_vmem(write_vmem(tmp_path, text))
    assert [a for a, _ in words] == [0x100, 0x101, 0x102, 0x103, 0x104, 0x105]
    assert [v for _, v in words] == [1, 2, 3, 4, 5, 6]


def test_at_line_rebases_address_downwards_too(tmp_path):
    """An @ line is an absolute re-base, not an offset -- it may go backwards."""
    text = (
        "@00000020 AAAAAAAA BBBBBBBB\n"
        "@00000005 CCCCCCCC\n"
        "DDDDDDDD\n"
    )
    words = parse_vmem(write_vmem(tmp_path, text))
    assert words == [
        (0x20, 0xAAAAAAAA),
        (0x21, 0xBBBBBBBB),
        (0x05, 0xCCCCCCCC),
        (0x06, 0xDDDDDDDD),
    ]


def test_comments_and_blank_lines_skipped_everywhere(tmp_path):
    """Comments/blank lines between data must not perturb the address counter."""
    text = (
        "/* http://srecord.sourceforge.net/ */\n"
        "\n"
        "   \n"
        "@00000000 00000011\n"
        "// a slash-slash comment mid-file\n"
        "\t\n"
        "/* a block-comment line mid-file */\n"
        "00000022\n"
        "\n"
        "00000033\n"
    )
    words = parse_vmem(write_vmem(tmp_path, text))
    assert words == [(0, 0x11), (1, 0x22), (2, 0x33)]


def test_leading_and_trailing_whitespace_tolerated(tmp_path):
    """Lines are stripped, so indented @ lines and data lines still parse."""
    text = (
        "   @00000000 00000001 00000002   \n"
        "\t00000003\t\n"
    )
    words = parse_vmem(write_vmem(tmp_path, text))
    assert words == [(0, 1), (1, 2), (2, 3)]


def test_hex_parsing_is_case_insensitive_and_accepts_short_tokens(tmp_path):
    """Data words go through int(tok, 16): any width, either case."""
    lower = parse_vmem(write_vmem(tmp_path, "@0000000a deadbeef\n", "lower.vmem"))
    upper = parse_vmem(write_vmem(tmp_path, "@0000000A DEADBEEF\n", "upper.vmem"))
    assert lower == upper == [(0x0A, 0xDEADBEEF)]

    short = parse_vmem(write_vmem(tmp_path, "@0 1 F 10\n", "short.vmem"))
    assert short == [(0, 0x1), (1, 0xF), (2, 0x10)]


def test_address_defaults_to_zero_without_an_at_line(tmp_path):
    """Data before any @ line starts at word address 0."""
    words = parse_vmem(write_vmem(tmp_path, "00000001 00000002\n"))
    assert words == [(0, 1), (1, 2)]


# --------------------------------------------------------------------------
# parse_vmem: degenerate and malformed input
# --------------------------------------------------------------------------

def test_empty_file_yields_empty_list(tmp_path):
    """Pinned behaviour: an empty file parses to [] rather than raising."""
    assert parse_vmem(write_vmem(tmp_path, "")) == []


def test_comments_only_file_yields_empty_list(tmp_path):
    """Pinned behaviour: a header-only file parses to [] rather than raising."""
    text = "/* http://srecord.sourceforge.net/ */\n\n// nothing here\n"
    assert parse_vmem(write_vmem(tmp_path, text)) == []


def test_bare_at_line_with_no_data_contributes_nothing(tmp_path):
    """A lone @ line only moves the cursor; it emits no words."""
    assert parse_vmem(write_vmem(tmp_path, "@0000ABCD\n")) == []


def test_malformed_data_token_raises(tmp_path):
    """A non-hex data word must blow up, not be silently skipped/mis-parsed."""
    with pytest.raises(ValueError):
        parse_vmem(write_vmem(tmp_path, "@0 ZZZZ\n"))


def test_malformed_address_token_raises(tmp_path):
    """A non-hex @ address must blow up rather than silently re-base to junk."""
    with pytest.raises(ValueError):
        parse_vmem(write_vmem(tmp_path, "@GG 1\n"))


def test_missing_file_raises_oserror(tmp_path):
    with pytest.raises(OSError):
        parse_vmem(str(tmp_path / "does_not_exist.vmem"))


# --------------------------------------------------------------------------
# vmem_values
# --------------------------------------------------------------------------

def test_vmem_values_is_the_value_column_in_order(tmp_path):
    path = write_vmem(tmp_path, GOOD_VMEM)
    assert vmem_values(path) == [v for _, v in parse_vmem(path)]
    assert vmem_values(path) == [0xDEADBEEF, 0x12345678, 0xAABBCCDD, 0x0000FFFF]


def test_vmem_values_keeps_duplicates_and_order(tmp_path):
    """Streaming order matters: no dedup, no sort."""
    text = "@00000000 00000005 00000001 00000005 00000001\n"
    path = write_vmem(tmp_path, text)
    assert vmem_values(path) == [5, 1, 5, 1]


def test_vmem_values_empty_file(tmp_path):
    assert vmem_values(write_vmem(tmp_path, "")) == []


# --------------------------------------------------------------------------
# Mcp2210Programmer._frame
# --------------------------------------------------------------------------

def test_frame_packs_address_in_the_upper_word_big_endian():
    assert Mcp2210Programmer._frame(0x08100000, 0xDEADBEEF) == bytes.fromhex(
        "08100000deadbeef"
    )


def test_frame_is_always_eight_bytes():
    for addr, data in ((0, 0), (1, 2), (0xFFFFFFFF, 0xFFFFFFFF), (0x1234, 0xABCD)):
        assert len(Mcp2210Programmer._frame(addr, data)) == 8


def test_frame_masks_each_half_to_32_bits():
    """Negative and over-wide inputs are masked, not allowed to bleed across."""
    assert Mcp2210Programmer._frame(-1, 0x1_0000_0002) == bytes.fromhex(
        "ffffffff00000002"
    )
    assert Mcp2210Programmer._frame(0x1_0000_0001, -2) == bytes.fromhex(
        "00000001fffffffe"
    )


def test_frame_zero_and_all_ones():
    assert Mcp2210Programmer._frame(0, 0) == b"\x00" * 8
    assert Mcp2210Programmer._frame(0xFFFFFFFF, 0xFFFFFFFF) == b"\xff" * 8


def test_frame_halves_are_independent():
    """The data word must never disturb the address word and vice versa."""
    frame = Mcp2210Programmer._frame(0x0000_0001, 0xFFFF_FFFF)
    assert frame[:4] == bytes.fromhex("00000001")
    assert frame[4:] == bytes.fromhex("ffffffff")


def test_frame_callable_without_an_instance():
    """It is a @staticmethod: usable with no MCP2210 hardware anywhere."""
    assert isinstance(
        Mcp2210Programmer.__dict__["_frame"], staticmethod
    ), "_frame must stay a staticmethod so it is testable/usable offline"


# --------------------------------------------------------------------------
# end-to-end, still entirely offline
# --------------------------------------------------------------------------

def test_parse_then_frame_round_trip(tmp_path):
    """Every parsed row frames to 8 bytes whose upper word is that address."""
    text = (
        "/* http://srecord.sourceforge.net/ */\n"
        "@00000000 0840006F 0800006F 07C0006F\n"
        "0780006F\n"
        "@00000100 00000093\n"
    )
    words = parse_vmem(write_vmem(tmp_path, text))
    assert len(words) == 5

    for addr, data in words:
        frame = Mcp2210Programmer._frame(addr, data)
        assert len(frame) == 8
        assert int.from_bytes(frame[:4], "big") == addr
        assert int.from_bytes(frame[4:], "big") == data


def test_real_repo_vmem_parses_contiguously(repo_root):
    """The checked-in controller image must parse to a gap-free word stream.

    This is the file the SPI loader actually streams, so a regression in the
    address bookkeeping would show up here as a gap or a duplicate address.
    """
    path = os.path.join(repo_root, "Software", "Controller", "main_imem.vmem")
    if not os.path.exists(path):
        pytest.skip("Software/Controller/main_imem.vmem is not checked in")

    words = parse_vmem(path)
    assert words, "the checked-in image should not parse to zero words"

    addresses = [a for a, _ in words]
    assert addresses == list(range(len(words))), "word addresses must be 0..N-1"
    assert all(0 <= v <= 0xFFFFFFFF for _, v in words), "values must be 32-bit"
    assert vmem_values(path) == [v for _, v in words]
