"""
Regression tests for proact_host.monitor.

The UART monitor's entire contract is: NEVER raise on bad bytes, and never
desync permanently. The chip deliberately emits 0xA5 binary result frames into
the same stream as ASCII debug text, and a marginal clock adds noise on top of
that. All of this is pure byte -> string logic with no I/O whatsoever, so it can
be tested exhaustively over all 256 byte values with no board attached.

Offline only: nothing here opens a port, imports a hardware backend, or sleeps.
"""
import random

from proact_host.monitor import (
    MonitorDecoder,
    hex_str,
    is_frame_start,
    safe_text,
)

ALL_BYTES = [bytes([b]) for b in range(256)]

# 0x20..0x7E ("space".."~") plus tab are the bytes that survive as themselves.
PRINTABLE = set(range(0x20, 0x7F)) | {0x09}


def unescape(text):
    """Inverse of safe_text: turn the escaped display string back into bytes.

    Used to prove safe_text is lossless (i.e. a human reading the text column
    can always recover the exact bytes the chip sent), for any data at all --
    a payload backslash arrives here doubled.
    """
    out = bytearray()
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            kind = text[i + 1]
            if kind == "n":
                out.append(0x0A)
                i += 2
            elif kind == "r":
                out.append(0x0D)
                i += 2
            elif kind == "\\":
                out.append(0x5C)
                i += 2
            elif kind == "x":
                out.append(int(text[i + 2:i + 4], 16))
                i += 4
            else:
                raise AssertionError("unknown escape %r" % text[i:i + 2])
        else:
            out.append(ord(ch))
            i += 1
    return bytes(out)


# --------------------------------------------------------------------------
# safe_text
# --------------------------------------------------------------------------

def test_safe_text_is_total_over_all_256_byte_values():
    """It must never raise, whatever the line noise looks like."""
    for one in ALL_BYTES:
        result = safe_text(one)
        assert isinstance(result, str)
        assert result != ""


def test_safe_text_handles_every_byte_value_in_one_call():
    """It is a pure per-byte map: no state carried between bytes, so a bad byte
    can never corrupt the rendering of the bytes around it."""
    blob = bytes(range(256))
    assert safe_text(blob) == "".join(safe_text(bytes([b])) for b in blob)


def test_safe_text_passes_printable_ascii_through_unchanged():
    # 0x5C is the one exception: it introduces every escape, so it is doubled
    # rather than passed through -- see test_safe_text_doubles_a_backslash.
    for value in sorted(PRINTABLE - {0x5C}):
        assert safe_text(bytes([value])) == chr(value)


def test_safe_text_doubles_a_backslash():
    assert safe_text(b"\\") == "\\\\"
    assert safe_text(b"C:\\dir") == "C:\\\\dir"


def test_safe_text_passes_tab_through_as_a_real_tab():
    assert safe_text(b"\t") == "\t"
    assert safe_text(b"col\tcol") == "col\tcol"


def test_safe_text_newline_and_cr_become_two_char_literal_escapes():
    # Literal backslash + letter, NOT a real control character -- otherwise the
    # monitor's own output would gain lines the chip never sent.
    assert safe_text(b"\n") == "\\n"
    assert safe_text(b"\r") == "\\r"
    assert len(safe_text(b"\n")) == 2
    assert "\n" not in safe_text(b"\n")
    assert "\r" not in safe_text(b"\r")


def test_safe_text_non_printable_becomes_uppercase_hex_escape():
    for value in range(256):
        if value in PRINTABLE or value in (0x0A, 0x0D):
            continue
        expected = "\\x%02X" % value
        assert safe_text(bytes([value])) == expected


def test_safe_text_hex_escapes_are_uppercase():
    assert safe_text(b"\xff") == "\\xFF"
    assert safe_text(b"\xab") == "\\xAB"
    assert safe_text(b"\x0b") == "\\x0B"
    assert safe_text(b"\x7f") == "\\x7F"  # DEL is not in 0x20..0x7E


def test_safe_text_docstring_example():
    assert safe_text(b"a\n\r\x00\xff") == "a\\n\\r\\x00\\xFF"


def test_safe_text_of_empty_is_empty():
    assert safe_text(b"") == ""


def test_safe_text_accepts_a_bytearray_too():
    # The decoder holds a bytearray internally; make sure the helper is not
    # accidentally str-only.
    assert safe_text(bytearray(b"hi\xa5")) == "hi\\xA5"


def test_safe_text_is_lossless_for_arbitrary_bytes():
    rng = random.Random(1234)  # fixed seed: deterministic
    for _ in range(200):
        blob = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 40)))
        assert unescape(safe_text(blob)) == blob


def test_safe_text_is_injective_even_when_data_contains_a_backslash():
    assert safe_text(b"\\n") != safe_text(b"\n")


def test_safe_text_is_injective_over_every_two_byte_input():
    """Exhaustive: no two distinct byte pairs may share a rendering, or the
    text column would silently mean two different things."""
    seen = {}
    for hi in range(256):
        for lo in range(256):
            blob = bytes([hi, lo])
            text = safe_text(blob)
            assert seen.setdefault(text, blob) == blob


# --------------------------------------------------------------------------
# hex_str
# --------------------------------------------------------------------------

def test_hex_str_is_uppercase_space_separated_two_digits():
    assert hex_str(b"Hel") == "48 65 6C"
    assert hex_str(b"\x00\x0f\xa5\xff") == "00 0F A5 FF"


def test_hex_str_of_empty_is_empty_string():
    assert hex_str(b"") == ""


def test_hex_str_single_byte_has_no_separator():
    assert hex_str(b"\x05") == "05"


def test_hex_str_every_byte_is_exactly_two_uppercase_hex_digits():
    for value in range(256):
        cell = hex_str(bytes([value]))
        assert len(cell) == 2
        assert cell == cell.upper()
        assert int(cell, 16) == value


def test_hex_str_column_count_matches_byte_count():
    blob = bytes(range(256))
    assert hex_str(blob).split(" ") == ["%02X" % b for b in blob]


# --------------------------------------------------------------------------
# is_frame_start
# --------------------------------------------------------------------------

def test_is_frame_start_is_true_exactly_for_high_bytes():
    for value in range(256):
        assert is_frame_start(bytes([value])) is (value >= 0x80)


def test_is_frame_start_boundary_and_the_real_frame_marker():
    assert is_frame_start(b"\x7f") is False
    assert is_frame_start(b"\x80") is True
    assert is_frame_start(b"\xa5") is True


def test_is_frame_start_false_for_empty_and_plain_ascii():
    assert is_frame_start(b"") is False
    assert is_frame_start(b"PROACT ready") is False


def test_is_frame_start_true_if_any_byte_is_high():
    assert is_frame_start(b"noise\xa5here") is True
    assert is_frame_start(b"........\x80") is True


# --------------------------------------------------------------------------
# MonitorDecoder.feed -- line splitting
# --------------------------------------------------------------------------

def test_feed_emits_one_tuple_per_complete_line_with_newline_consumed():
    dec = MonitorDecoder()
    out = dec.feed(b"hello\n")
    assert out == [("hello", "68 65 6C 6C 6F")]
    text, hexcol = out[0]
    assert "\\n" not in text and "\n" not in text
    assert "0A" not in hexcol


def test_feed_returns_several_tuples_for_several_lines_in_one_chunk():
    dec = MonitorDecoder()
    out = dec.feed(b"one\ntwo\nthree\n")
    assert [t for t, _ in out] == ["one", "two", "three"]
    assert [h for _, h in out] == ["6F 6E 65", "74 77 6F", "74 68 72 65 65"]


def test_feed_emits_empty_tuple_for_a_bare_newline():
    dec = MonitorDecoder()
    assert dec.feed(b"\n") == [("", "")]
    assert dec.feed(b"a\n\nb\n") == [("a", "61"), ("", ""), ("b", "62")]


def test_feed_keeps_carriage_return_visible_in_crlf_lines():
    # The chip sends CRLF; only the LF is the delimiter, so the CR stays in the
    # payload and must show up as an escape (and as 0D in the hex column).
    dec = MonitorDecoder()
    out = dec.feed(b"hi\r\n")
    assert out == [("hi\\r", "68 69 0D")]


def test_feed_buffers_a_partial_line_until_a_later_chunk_supplies_the_newline():
    dec = MonitorDecoder()
    assert dec.feed(b"part") == []
    assert dec.feed(b"ial ") == []
    assert dec.feed(b"line") == []
    assert dec.feed(b"\n") == [("partial line", hex_str(b"partial line"))]
    assert list(dec.flush()) == []


def test_feed_trailing_partial_after_complete_lines_stays_pending():
    dec = MonitorDecoder()
    out = dec.feed(b"done\nnot yet")
    assert out == [("done", "64 6F 6E 65")]
    assert list(dec.flush()) == [("not yet", hex_str(b"not yet"))]


def test_chunk_boundaries_never_change_the_decoded_lines():
    """Byte-for-byte the same stream must decode identically however the USB
    reads happen to split it."""
    stream = b"boot ok\nkey=\xa5\x01\x02\nstatus 0\r\nnoise\xff\xfe\ndone\n"
    whole = MonitorDecoder().feed(stream)

    rng = random.Random(20240607)  # fixed seed: deterministic
    for _ in range(50):
        dec = MonitorDecoder()
        pieces = []
        pos = 0
        while pos < len(stream):
            step = rng.randrange(1, 5)
            pieces.append(stream[pos:pos + step])
            pos += step
        got = []
        for piece in pieces:
            got.extend(dec.feed(piece))
        assert got == whole
        assert list(dec.flush()) == []


def test_feed_never_raises_on_any_single_byte():
    for one in ALL_BYTES:
        dec = MonitorDecoder()
        dec.feed(one)
        list(dec.flush())


def test_feed_never_raises_on_random_noise():
    rng = random.Random(99)  # fixed seed: deterministic
    dec = MonitorDecoder()
    for _ in range(500):
        blob = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 32)))
        for text, hexcol in dec.feed(blob):
            assert isinstance(text, str) and isinstance(hexcol, str)
    list(dec.flush())


# --------------------------------------------------------------------------
# MonitorDecoder -- binary frames must not desync the stream
# --------------------------------------------------------------------------

def test_binary_frame_bytes_survive_intact_in_both_columns():
    dec = MonitorDecoder()
    out = dec.feed(b"hi\xa5\x01there\n")
    assert out == [("hi\\xA5\\x01there", "68 69 A5 01 74 68 65 72 65")]


def test_a_frame_does_not_desync_the_following_ascii_lines():
    dec = MonitorDecoder()
    out = dec.feed(b"\xa5\x10\x00\x00\nnext line\nthird\n")
    assert out[0] == ("\\xA5\\x10\\x00\\x00", "A5 10 00 00")
    assert out[1] == ("next line", hex_str(b"next line"))
    assert out[2] == ("third", hex_str(b"third"))
    assert list(dec.flush()) == []


def test_a_frame_containing_0x0a_is_split_there_but_loses_no_bytes():
    # 0x0A inside a binary frame is indistinguishable from a newline; the
    # contract is only that no byte is silently dropped or duplicated.
    dec = MonitorDecoder()
    out = dec.feed(b"\xa5\x0a\xff\n")
    assert out == [("\\xA5", "A5"), ("\\xFF", "FF")]
    assert list(dec.flush()) == []


# --------------------------------------------------------------------------
# MonitorDecoder -- idle flush
# --------------------------------------------------------------------------

def test_idle_flush_emits_the_pending_partial_after_enough_empty_feeds():
    dec = MonitorDecoder(idle_flush=2)
    assert dec.feed(b"partial") == []
    assert dec.feed(b"") == []
    assert dec.feed(b"") == [("partial", hex_str(b"partial"))]
    assert list(dec.flush()) == []


def test_idle_counter_resets_on_real_data():
    dec = MonitorDecoder(idle_flush=2)
    dec.feed(b"par")
    assert dec.feed(b"") == []          # idle 1 of 2
    assert dec.feed(b"tial") == []      # real data -> counter back to 0
    assert dec.feed(b"") == []          # idle 1 of 2 again, not 2
    assert dec.feed(b"") == [("partial", hex_str(b"partial"))]


def test_idle_counter_resets_after_an_idle_flush():
    dec = MonitorDecoder(idle_flush=2)
    dec.feed(b"one")
    dec.feed(b"")
    assert dec.feed(b"") == [("one", hex_str(b"one"))]
    dec.feed(b"two")
    assert dec.feed(b"") == []          # counter restarted, so one empty is not enough
    assert dec.feed(b"") == [("two", hex_str(b"two"))]


def test_idle_feed_on_empty_buffer_returns_empty_and_never_raises():
    dec = MonitorDecoder(idle_flush=2)
    for _ in range(50):
        assert dec.feed(b"") == []
    assert list(dec.flush()) == []


def test_default_idle_flush_is_twenty_feeds():
    dec = MonitorDecoder()
    dec.feed(b"slow")
    for _ in range(19):
        assert dec.feed(b"") == []
    assert dec.feed(b"") == [("slow", hex_str(b"slow"))]


def test_idle_flush_does_not_fire_while_lines_keep_arriving():
    dec = MonitorDecoder(idle_flush=3)
    for _ in range(10):
        assert dec.feed(b"tick\n") == [("tick", hex_str(b"tick"))]
    assert list(dec.flush()) == []


# --------------------------------------------------------------------------
# MonitorDecoder -- runaway buffer cap
# --------------------------------------------------------------------------

def test_runaway_buffer_is_emitted_once_the_cap_is_passed():
    dec = MonitorDecoder()
    blob = b"x" * 5000
    out = dec.feed(blob)
    assert len(out) == 1
    text, hexcol = out[0]
    assert text == "x" * 5000
    assert len(hexcol.split(" ")) == 5000
    # buffer must be empty afterwards, not merely trimmed
    assert list(dec.flush()) == []


def test_runaway_cap_boundary_is_strictly_greater_than_4096():
    dec = MonitorDecoder()
    assert dec.feed(b"y" * 4096) == []          # exactly at the cap: still buffered
    out = dec.feed(b"z")                        # 4097 bytes: over the cap
    assert len(out) == 1
    assert out[0][0] == "y" * 4096 + "z"
    assert list(dec.flush()) == []


def test_runaway_cap_emits_completed_lines_before_the_garbage_tail():
    dec = MonitorDecoder()
    out = dec.feed(b"real line\n" + b"g" * 5000)
    assert out[0] == ("real line", hex_str(b"real line"))
    assert len(out) == 2
    assert out[1][0] == "g" * 5000
    assert list(dec.flush()) == []


def test_runaway_cap_of_binary_garbage_does_not_raise():
    dec = MonitorDecoder()
    out = dec.feed(bytes(b for b in range(256) if b != 0x0A) * 40)
    assert len(out) == 1
    assert list(dec.flush()) == []


# --------------------------------------------------------------------------
# MonitorDecoder.flush
# --------------------------------------------------------------------------

def test_flush_yields_the_pending_partial_exactly_once():
    dec = MonitorDecoder()
    dec.feed(b"tail")
    assert list(dec.flush()) == [("tail", hex_str(b"tail"))]
    assert list(dec.flush()) == []


def test_flush_yields_nothing_when_the_buffer_is_empty():
    dec = MonitorDecoder()
    assert list(dec.flush()) == []
    dec.feed(b"complete\n")
    assert list(dec.flush()) == []


def test_flush_is_a_generator_and_does_not_consume_the_buffer_until_iterated():
    dec = MonitorDecoder()
    dec.feed(b"held")
    gen = dec.flush()
    assert dec.feed(b" more") == []      # still buffered: flush was not iterated
    assert list(gen) == [("held more", hex_str(b"held more"))]
    assert list(dec.flush()) == []


def test_no_bytes_are_lost_across_feeds_and_a_final_flush():
    """End-to-end: everything fed comes back out (minus the LF delimiters)."""
    rng = random.Random(7)  # fixed seed: deterministic
    # Kept under the 4096-byte runaway cap so every emitted tuple corresponds
    # to a real LF delimiter and the reconstruction is exact.
    stream = bytes(rng.randrange(256) for _ in range(3000))
    dec = MonitorDecoder()
    recovered = bytearray()
    pos = 0
    while pos < len(stream):
        step = rng.randrange(1, 64)
        for _, hexcol in dec.feed(stream[pos:pos + step]):
            if hexcol:
                recovered += bytes(int(c, 16) for c in hexcol.split(" "))
            recovered += b"\n"
        pos += step
    for _, hexcol in dec.flush():
        if hexcol:
            recovered += bytes(int(c, 16) for c in hexcol.split(" "))

    # Every LF the decoder swallowed is put back exactly where it was, so the
    # reconstruction must be byte-identical to what was fed in.
    assert bytes(recovered) == stream
