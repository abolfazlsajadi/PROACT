"""
Regression tests for proact_host.transport -- the exact byte stream that goes
down the UART to the silicon, and the 0xA5 reply-frame parser.

OFFLINE ONLY. `UartTransport.open()` / `_detect_port()` are device-bound and are
deliberately NOT exercised here; `ProactTarget` is constructed directly on an
in-memory fake link, which is complete because ProactTarget only ever calls
``transport.write(bytes)`` and ``transport.read(n)``.

Why this file exists: a wrong command byte or a wrong payload width silently
drives the wrong core or corrupts a key, and neither shows up as an exception --
only as garbage traces. cli.cmd_test already asserts a three-byte slice of this
protocol, so the maintainers already treat it as regression-worthy; this widens
that to the whole surface.
"""
import threading

import pytest

from proact_host import regs
from proact_host.transport import (
    ProactTarget,
    block_to_words,
    words_to_block,
)


# --------------------------------------------------------------- fake link
class FakeLink:
    """Minimal stand-in for UartTransport: a tx sink and a preloaded rx buffer.

    `read(n)` returns *up to* n bytes, exactly like a pyserial read that times
    out mid-transfer -- that short-read behaviour is what read_frame's
    TimeoutError paths are built on.
    """

    def __init__(self, rx=b"", with_lock=True):
        self.tx = bytearray()
        self.rx = bytearray(rx)
        if with_lock:
            self.lock = threading.RLock()

    def write(self, data):
        self.tx += bytes(data)

    def read(self, n):
        chunk = bytes(self.rx[:n])
        del self.rx[:n]
        return chunk


class NeverSyncsLink(FakeLink):
    """Endless stream that never contains the 0xA5 frame marker."""

    def __init__(self):
        super().__init__()
        self.reads = 0

    def read(self, n):
        self.reads += 1
        return b"\x78" * n


def mk(rx=b"", with_lock=True):
    link = FakeLink(rx, with_lock=with_lock)
    return link, ProactTarget(link)


def frame(mode, payload):
    return bytes([regs.FRAME_MARKER, mode, len(payload)]) + bytes(payload)


K16 = bytes(range(16))


# ------------------------------------------------- block_to_words / inverse
def test_block_to_words_is_big_endian():
    assert block_to_words(bytes(range(16))) == [
        0x00010203, 0x04050607, 0x08090A0B, 0x0C0D0E0F]


def test_block_to_words_high_bit_words_stay_unsigned():
    words = block_to_words(bytes.fromhex("ffffffff8000000000000001deadbeef"))
    assert words == [0xFFFFFFFF, 0x80000000, 0x00000001, 0xDEADBEEF]


def test_words_to_block_emits_four_big_endian_words():
    assert words_to_block([0x00010203, 0x04050607, 0x08090A0B, 0x0C0D0E0F]) \
        == bytes(range(16))


@pytest.mark.parametrize("block", [
    bytes(16),
    bytes(range(16)),
    b"\xff" * 16,
    bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
    bytes.fromhex("00112233445566778899aabbccddeeff"),
    bytes.fromhex("deadbeefcafebabe0123456789abcdef"),
    bytes((i * 37 + 11) & 0xFF for i in range(16)),
])
def test_block_words_roundtrip(block):
    assert words_to_block(block_to_words(block)) == block


@pytest.mark.parametrize("bad", [b"", bytes(15), bytes(17), bytes(32)])
def test_block_to_words_rejects_wrong_length(bad):
    with pytest.raises(ValueError, match="block must be 16 bytes"):
        block_to_words(bad)


def test_words_to_block_is_not_length_checked():
    # Documented reality: words_to_block has no arity check, so it happily
    # produces a non-16-byte block. Only block_to_words guards the width.
    assert words_to_block([1, 2]) == bytes.fromhex("0000000100000002")


# ------------------------------------------------------- command encoding
def test_select_key_run_is_the_documented_byte_stream():
    link, tgt = mk()
    tgt.select("aes1")
    tgt.set_key(K16)
    tgt.run()
    assert bytes(link.tx).hex() == "09" + "01" + K16.hex() + "05"
    # and the same thing spelled out against regs, so a regs renumbering
    # cannot make the hex literal above quietly wrong
    assert link.tx[0] == regs.CMD_AES1
    assert link.tx[1] == regs.CMD_KEY
    assert link.tx[2:18] == K16
    assert link.tx[18] == regs.CMD_RDY
    assert len(link.tx) == 19


@pytest.mark.parametrize("method,cmd", [
    ("set_key", regs.CMD_KEY),
    ("set_plaintext", regs.CMD_PT),
    ("set_nonce", regs.CMD_NONCE),
    ("set_ad", regs.CMD_AD),
])
@pytest.mark.parametrize("block", [
    bytes(16), bytes(range(16)), b"\xff" * 16,
    bytes.fromhex("deadbeefcafebabe0123456789abcdef"),
])
def test_16_byte_payload_commands_send_block_unchanged(method, cmd, block):
    link, tgt = mk()
    getattr(tgt, method)(block)
    assert bytes(link.tx) == bytes([cmd]) + block


@pytest.mark.parametrize("method", ["set_key", "set_plaintext", "set_nonce", "set_ad"])
@pytest.mark.parametrize("bad", [b"", bytes(15), bytes(17)])
def test_16_byte_payload_commands_reject_wrong_width(method, bad):
    link, tgt = mk()
    with pytest.raises(ValueError, match="block must be 16 bytes"):
        getattr(tgt, method)(bad)
    assert bytes(link.tx) == b"", "nothing may reach the wire on a rejected block"


@pytest.mark.parametrize("name,cmd", [
    ("aes1", regs.CMD_AES1), ("aes2", regs.CMD_AES2),
    ("xoodyak", regs.CMD_XOO), ("ascon", regs.CMD_ASC),
    ("swrv", regs.CMD_SWRV),
])
def test_select_emits_one_command_byte_per_core(name, cmd):
    link, tgt = mk()
    tgt.select(name)
    assert bytes(link.tx) == bytes([cmd])


@pytest.mark.parametrize("spelling", ["AES1", "Aes1", "aEs1"])
def test_select_is_case_insensitive(spelling):
    link, tgt = mk()
    tgt.select(spelling)
    assert bytes(link.tx) == bytes([regs.CMD_AES1])


@pytest.mark.parametrize("bogus", ["aes3", "", "present", "aes 1"])
def test_select_rejects_unknown_core(bogus):
    link, tgt = mk()
    with pytest.raises(KeyError):
        tgt.select(bogus)
    assert bytes(link.tx) == b""


def test_select_command_bytes_are_not_in_numeric_core_order():
    # Guards the easy-to-"fix" trap: AES2/XOO/ASC come *before* AES1 in the
    # firmware's opcode numbering (0x06/0x07/0x08 then 0x09).
    assert (regs.CMD_AES2, regs.CMD_XOO, regs.CMD_ASC, regs.CMD_AES1) == \
        (0x06, 0x07, 0x08, 0x09)


@pytest.mark.parametrize("method,cmd", [
    ("enable_debug", regs.CMD_DBG),
    ("enable_sendback", regs.CMD_SB),
    ("toggle_arm", regs.CMD_ARM),
    ("self_test", regs.CMD_TEST),
])
def test_bare_commands_send_exactly_one_byte(method, cmd):
    link, tgt = mk()
    getattr(tgt, method)()
    assert bytes(link.tx) == bytes([cmd])


# ------------------------------------------------------- scalar arguments
@pytest.mark.parametrize("cfg,wire", [
    (0x00, 0x00), (0x12, 0x12), (0x7F, 0x7F),
    (0x80, 0x00), (0xFF, 0x7F), (0x1FF, 0x7F), (0xAA, 0x2A),
])
def test_set_trigger_cfg_masks_to_seven_bits(cfg, wire):
    link, tgt = mk()
    tgt.set_trigger_cfg(cfg)
    assert bytes(link.tx) == bytes([regs.CMD_TRIG, wire])


@pytest.mark.parametrize("value,wire", [
    (True, 1), (False, 0), (1, 1), (0, 0), (None, 0), ("yes", 1), ("", 0),
])
def test_set_decrypt_normalises_to_a_single_0_or_1(value, wire):
    link, tgt = mk()
    tgt.set_decrypt(value)
    assert bytes(link.tx) == bytes([regs.CMD_DEC, wire])


@pytest.mark.parametrize("seed,hexpayload", [
    (0, "00000000"), (1, "00000001"), (0xDEADBEEF, "deadbeef"),
    (0xFFFFFFFF, "ffffffff"), (0x12345678, "12345678"),
])
def test_seed_rng_sends_four_big_endian_bytes(seed, hexpayload):
    link, tgt = mk()
    tgt.seed_rng(seed)
    assert bytes(link.tx).hex() == "%02x" % regs.CMD_SEED + hexpayload


@pytest.mark.parametrize("value,hexpayload", [
    (0, "00000000"),
    (regs.CTRL_ENABLE_TARGET, "00000001"),
    (regs.CTRL_TRIGGER, "40000000"),
    (0xFFFFFFFF, "ffffffff"),
    (0x1_0000_0001, "00000001"),  # masked to 32 bits by write_control
])
def test_write_control_sends_four_big_endian_bytes(value, hexpayload):
    link, tgt = mk()
    tgt.write_control(value)
    assert bytes(link.tx).hex() == "17" + hexpayload
    assert link.tx[0] == regs.CMD_WRCTRL


@pytest.mark.parametrize("source,code", [
    (None, 0xFF), ("software", 0), ("ascon", 1), ("aes1", 2),
    ("aes2", 3), ("xoodyak", 4), ("swrv", 5),
])
def test_set_cfgsel_mux_encoding(source, code):
    link, tgt = mk()
    tgt.set_cfgsel(source)
    assert bytes(link.tx) == bytes([regs.CMD_CFGSEL, code])


def test_set_cfgsel_codes_match_the_control_register_field():
    # The byte the controller gets must be the CTRL_CFGSEL field value, or the
    # scope triggers off the wrong core.
    link, tgt = mk()
    for name, ctrl in [("software", regs.CFGSEL_SOFTWARE), ("ascon", regs.CFGSEL_ASCON),
                       ("aes1", regs.CFGSEL_AES1), ("aes2", regs.CFGSEL_AES2),
                       ("xoodyak", regs.CFGSEL_XOODYAK), ("swrv", regs.CFGSEL_SWRV)]:
        link.tx.clear()
        tgt.set_cfgsel(name)
        assert link.tx[1] == ctrl >> regs.CTRL_CFGSEL_SHIFT, name


@pytest.mark.parametrize("bogus", ["auto", "AES1", "none", "aes3", ""])
def test_set_cfgsel_rejects_unknown_source(bogus):
    # NB: unlike select(), set_cfgsel is case-SENSITIVE and does not accept
    # the CLI's "auto" spelling -- callers must translate "auto" -> None.
    link, tgt = mk()
    with pytest.raises(KeyError):
        tgt.set_cfgsel(bogus)
    assert bytes(link.tx) == b""


# ------------------------------------------------------------- read_frame
def test_read_frame_returns_mode_and_payload():
    link, tgt = mk(frame(regs.MODE_AES1, bytes.fromhex("deadbeef")))
    assert tgt.read_frame() == (0, bytes.fromhex("deadbeef"))
    assert bytes(link.rx) == b"", "frame must be consumed exactly"


def test_read_frame_skips_leading_debug_text():
    _, tgt = mk(b"debug" + frame(0, bytes.fromhex("deadbeef")))
    assert tgt.read_frame() == (0, bytes.fromhex("deadbeef"))


def test_read_frame_skips_non_ascii_noise_too():
    # The docstring says "ASCII debug bytes (all < 0x80)", but the code skips
    # ANY byte that is not 0xA5. Pin the real behaviour.
    _, tgt = mk(b"\x00\xff\x7f\x80" + frame(regs.MODE_ASC, b"\x01\x02"))
    assert tgt.read_frame() == (regs.MODE_ASC, b"\x01\x02")


def test_read_frame_reads_full_16_byte_ciphertext_payload():
    ct = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")
    _, tgt = mk(frame(regs.MODE_AES2, ct))
    assert tgt.read_frame() == (regs.MODE_AES2, ct)


def test_read_frame_accepts_zero_length_payload():
    _, tgt = mk(frame(regs.MODE_SWRV, b""))
    assert tgt.read_frame() == (regs.MODE_SWRV, b"")


def test_read_frame_leaves_trailing_bytes_for_the_next_call():
    link, tgt = mk(frame(1, b"\xaa") + frame(2, b"\xbb\xcc"))
    assert tgt.read_frame() == (1, b"\xaa")
    assert tgt.read_frame() == (2, b"\xbb\xcc")
    assert bytes(link.rx) == b""


def test_read_frame_empty_stream_times_out():
    _, tgt = mk(b"")
    with pytest.raises(TimeoutError, match="no frame marker"):
        tgt.read_frame()


def test_read_frame_junk_then_silence_times_out():
    _, tgt = mk(b"boot ok\r\n")
    with pytest.raises(TimeoutError, match="no frame marker"):
        tgt.read_frame()


@pytest.mark.parametrize("hdr", [b"", b"\x00"])
def test_read_frame_truncated_header_times_out(hdr):
    _, tgt = mk(bytes([regs.FRAME_MARKER]) + hdr)
    with pytest.raises(TimeoutError, match="short frame header"):
        tgt.read_frame()


@pytest.mark.parametrize("got", [0, 1, 15])
def test_read_frame_truncated_payload_times_out(got):
    _, tgt = mk(bytes([regs.FRAME_MARKER, 0, 16]) + bytes(got))
    with pytest.raises(TimeoutError, match="short frame payload"):
        tgt.read_frame()


def test_read_frame_gives_up_after_8192_skipped_bytes():
    link = NeverSyncsLink()
    tgt = ProactTarget(link)
    with pytest.raises(RuntimeError, match="too much junk"):
        tgt.read_frame()
    # the guard trips on the byte *after* 8192 skips
    assert link.reads == 8193


# --------------------------------------------- commands with a reply frame
def test_run_and_read_writes_ready_then_parses_the_reply():
    ct = bytes.fromhex("00112233445566778899aabbccddeeff")
    link, tgt = mk(frame(regs.MODE_AES1, ct))
    assert tgt.run_and_read() == (regs.MODE_AES1, ct)
    assert bytes(link.tx) == bytes([regs.CMD_RDY])


@pytest.mark.parametrize("payload,value", [
    ("00000000", 0), ("00000001", 1), ("deadbeef", 0xDEADBEEF),
    ("ffffffff", 0xFFFFFFFF), ("80000000", 0x80000000),
])
def test_get_timer_parses_a_big_endian_word(payload, value):
    link, tgt = mk(frame(regs.MODE_TIMER, bytes.fromhex(payload)))
    assert tgt.get_timer() == value
    assert bytes(link.tx) == bytes([regs.CMD_TIME])


def test_read_status_parses_a_big_endian_word():
    status = regs.STAT_TARGET_DONE | regs.STAT_DONE_AES1
    link, tgt = mk(frame(regs.MODE_STATUS, status.to_bytes(4, "big")))
    assert tgt.read_status() == status
    assert bytes(link.tx) == bytes([regs.CMD_RDSTAT])


def test_status_word_keeps_bit31_unsigned():
    link, tgt = mk(frame(regs.MODE_STATUS, b"\xff\xff\xff\xff"))
    assert tgt.read_status() == 0xFFFFFFFF


@pytest.mark.parametrize("res,expected", [
    (0b00, (False, False)),
    (0b01, (True, False)),   # bit0 = xoodyak
    (0b10, (False, True)),   # bit1 = ascon
    (0b11, (True, True)),
])
def test_aead_kat_decodes_result_bits(res, expected):
    link, tgt = mk(frame(0, res.to_bytes(4, "big")))
    assert tgt.aead_kat() == expected
    assert bytes(link.tx) == b"\x1a"  # CMD_AEADKAT, not present in regs.py


def test_short_reply_frames_raise_instead_of_inventing_a_value():
    """A truncated reply must be an error, not a plausible register value.

    `int.from_bytes(payload[:4])` used to turn a 2-byte payload into 1 and an
    empty payload into a perfectly innocent-looking 0, so a desynced stream --
    the exact failure the module docstring says the GUI poller used to cause --
    was read back as a valid status/timer value. Both now raise.
    """
    _, tgt = mk(frame(regs.MODE_STATUS, b"\x00\x01"))
    with pytest.raises(TimeoutError, match="short status reply"):
        tgt.read_status()
    _, tgt = mk(frame(regs.MODE_STATUS, b""))
    with pytest.raises(TimeoutError, match="short status reply"):
        tgt.read_status()
    _, tgt = mk(frame(regs.MODE_TIMER, b"\x07"))
    with pytest.raises(TimeoutError, match="short timer reply"):
        tgt.get_timer()


def test_a_full_width_payload_of_the_wrong_mode_is_still_accepted():
    """Documents a REMAINING gap: the firmware tags every reply (MODE_TIMER
    0xF1, MODE_STATUS 0xF2, ...) but the host still discards `mode`, so a
    full-width frame of the wrong kind is taken at face value. Length is now
    checked; mode is not. Pinned so a future mode check is a visible change."""
    ct = bytes.fromhex("00112233445566778899aabbccddeeff")
    _, tgt = mk(frame(regs.MODE_AES1, ct))
    assert tgt.get_timer() == 0x00112233


def test_raw_bus_opcodes_match_the_generated_constants():
    """BUG-008 regression: CMD_POKE/PEEK/AEADKAT are now in the single source of
    truth (config/hardware.json -> regs.py), so transport.py's getattr()
    fallbacks resolve to the generated constants, which must agree with
    Software/Controller/main.c (0x18/0x19/0x1A)."""
    assert regs.CMD_POKE == 0x18
    assert regs.CMD_PEEK == 0x19
    assert regs.CMD_AEADKAT == 0x1A
    link, tgt = mk(frame(0, b"\x00\x00\x00\x00"))
    tgt.poke(0, 0)
    tgt.peek(0)
    assert link.tx[0] == 0x18 and link.tx[9] == 0x19


# ------------------------------------------------------- raw bus access
def test_poke_frames_addr_then_value():
    link, tgt = mk()
    tgt.poke(regs.MBOX_KEY, 0xDEADBEEF)
    assert bytes(link.tx).hex() == "18" + "08003f00" + "deadbeef"


def test_peek_frames_addr_and_parses_the_reply_word():
    link, tgt = mk(frame(0, bytes.fromhex("cafebabe")))
    assert tgt.peek(regs.SCREG_BASE) == 0xCAFEBABE
    assert bytes(link.tx).hex() == "19" + "20000000"


def test_poke_words_walks_the_address_by_stride():
    link, tgt = mk()
    tgt.poke_words(0x08003F10, [0x11111111, 0x22222222], stride=4)
    assert bytes(link.tx).hex() == \
        "18" + "08003f10" + "11111111" + "18" + "08003f14" + "22222222"


def test_poke_words_honours_a_non_default_stride():
    link, tgt = mk()
    tgt.poke_words(0x02000000, [1, 2, 3], stride=16)
    addrs = [bytes(link.tx[i * 9 + 1:i * 9 + 5]).hex() for i in range(3)]
    assert addrs == ["02000000", "02000010", "02000020"]


def test_peek_words_issues_one_peek_per_word_and_orders_results():
    rx = frame(0, b"\x00\x00\x00\x0a") + frame(0, b"\x00\x00\x00\x0b") \
        + frame(0, b"\x00\x00\x00\x0c")
    link, tgt = mk(rx)
    assert tgt.peek_words(0x08003F40, 3) == [0x0A, 0x0B, 0x0C]
    assert bytes(link.tx).hex() == \
        "19" + "08003f40" + "19" + "08003f44" + "19" + "08003f48"


# ----------------------------------------------------- Sw-RV program load
def test_load_target_imem_frames_count_then_words():
    link, tgt = mk()
    tgt.load_target_imem([0x00000013, 0xDEADBEEF])
    assert bytes(link.tx).hex() == "12" + "00000002" + "00000013" + "deadbeef"


def test_load_target_dmem_frames_count_base_then_words():
    link, tgt = mk()
    tgt.load_target_dmem([1, 2], regs.SWRV_DMEM_LOAD_BASE)
    assert bytes(link.tx).hex() == \
        "13" + "00000002" + "08100000" + "00000001" + "00000002"


@pytest.mark.parametrize("cmd,method,args", [
    (regs.CMD_LDI, "load_target_imem", ([],)),
    (regs.CMD_LDD, "load_target_dmem", ([], 0x08100000)),
])
def test_empty_memory_load_still_sends_a_zero_count(cmd, method, args):
    link, tgt = mk()
    getattr(tgt, method)(*args)
    assert link.tx[0] == cmd
    assert bytes(link.tx[1:5]) == b"\x00\x00\x00\x00"


def test_load_swrv_program_order_is_imem_dmem_then_select():
    # The load order is load-bearing: CMD_LDI holds the target in reset, and
    # select('swrv') releases it so the 0->1 edge boots the fresh image.
    link, tgt = mk()
    tgt.load_swrv_program([0xAA], [0xBB], dmem_base=0x08100000, boot_delay=0.0)
    assert bytes(link.tx).hex() == (
        "12" + "00000001" + "000000aa"
        + "13" + "00000001" + "08100000" + "000000bb"
        + "14"
    )
    assert link.tx[-1] == regs.CMD_SWRV


def test_load_swrv_program_accepts_any_iterable():
    link, tgt = mk()
    tgt.load_swrv_program(iter([0x01]), (0x02 for _ in range(1)), boot_delay=0.0)
    assert bytes(link.tx).hex() == (
        "12" + "00000001" + "00000001"
        + "13" + "00000001" + "08100000" + "00000002"
        + "14"
    )


# ------------------------------------------------------------- plumbing
def test_target_reuses_the_transport_transaction_lock():
    link, tgt = mk()
    assert tgt.lock is link.lock, \
        "command+reply must be atomic against the GUI monitor thread"


def test_target_builds_its_own_lock_for_a_bare_link():
    link, tgt = mk(with_lock=False)
    assert not hasattr(link, "lock")
    tgt.run()  # must not blow up on the missing attribute
    assert bytes(link.tx) == bytes([regs.CMD_RDY])


def test_lock_is_reentrant_so_nested_transactions_do_not_deadlock():
    # get_timer takes self.lock and then calls _cmd/read_frame which take it
    # again; a plain Lock here would hang the whole host library.
    link, tgt = mk(frame(regs.MODE_TIMER, b"\x00\x00\x00\x07"))
    with tgt.lock:
        assert tgt.get_timer() == 7


def test_a_full_aead_setup_produces_one_contiguous_stream():
    key = bytes(range(16))
    nonce = bytes(range(16, 32))
    ad = bytes(16)
    pt = b"\xa5" * 16  # payload bytes equal to the frame marker must survive
    ct = bytes.fromhex("0102030405060708090a0b0c0d0e0f10")
    link, tgt = mk(frame(regs.MODE_ASC, ct))
    tgt.select("ascon")
    tgt.set_cfgsel("ascon")
    tgt.set_trigger_cfg(0x12)
    tgt.set_decrypt(False)
    tgt.set_key(key)
    tgt.set_nonce(nonce)
    tgt.set_ad(ad)
    tgt.set_plaintext(pt)
    assert tgt.run_and_read() == (regs.MODE_ASC, ct)
    assert bytes(link.tx).hex() == (
        "08"
        + "1501"
        + "0f12"
        + "0d00"
        + "01" + key.hex()
        + "0b" + nonce.hex()
        + "0c" + ad.hex()
        + "02" + pt.hex()
        + "05"
    )
