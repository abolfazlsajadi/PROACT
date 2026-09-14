"""Acquisition configuration (L1.1).

One dataclass describes a whole campaign. It is the single source of truth that the
CLI fills, the checkpoint persists, and every module reads. Nothing else holds
campaign state.
"""
from __future__ import annotations
import math
import os
import re
from dataclasses import dataclass, asdict
from typing import Optional

TARGETS = ("aes1", "aes2", "sw_rv", "sw_rv_masked", "xoodyak", "ascon")
AEAD = ("xoodyak", "ascon")
POLICIES = ("fixed", "random")
TVLA_ORDERS = ("block", "alternate")

# The fabricated design resets the UART generator to divisor 27 and the supported
# controller firmware never changes it.  The proven host setting at 50 MHz is
# 115200 baud (the divisor-implied wire rate is 115740.74 baud, a 0.4672% error).
UART_DIVISOR = 27
UART_REFERENCE_CLOCK_MHZ = 50.0
UART_REFERENCE_HOST_BAUD = 115_200
UART_MAX_ERROR_PERCENT = 2.0

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")


def _require(condition, message):
    """Runtime validation that remains active under ``python -O``."""
    if not condition:
        raise ValueError(message)


def parse_count(s) -> int:
    """Accept 10000, 10k, 2M."""
    if isinstance(s, int):
        return s
    s = str(s).strip().replace(",", "").upper()
    mult = {"K": 1_000, "M": 1_000_000}
    if s and s[-1] in mult:
        return int(float(s[:-1]) * mult[s[-1]])
    return int(s)


def auto_uart_baud(clock_mhz: float) -> int:
    """Scale the bench-proven 115200-at-50-MHz host rate with target clock."""
    return int(round(UART_REFERENCE_HOST_BAUD *
                     float(clock_mhz) / UART_REFERENCE_CLOCK_MHZ))


@dataclass
class AcqConfig:
    target: str                      # one of TARGETS
    traces: int                      # requested valid traces
    key_policy: str = "fixed"        # fixed | random
    input_policy: str = "random"     # fixed | random  (plaintext for AES, nonce for AEAD)
    fixed_key: bytes = bytes(range(16))
    fixed_input: bytes = bytes(16)   # all-zero default
    # analysis / experimental protocol
    auto_cpa: bool = False           # run the target's exact CPA model after capture
    tvla: bool = False               # fixed-vs-random first-order Welch TVLA
    tvla_per_class: int = 0          # requested rows in EACH TVLA class
    tvla_order: str = "block"        # block (recommended) | alternate (strict F,R)
    tvla_block_size: int = 100       # even, balanced and shuffled within each block
    warmup: int = 0                  # successful target operations discarded per session
    # trigger
    trigger_mode: str = "auto"       # auto | core | firmware   (AES/Sw-RV)
    aead_trigger: int = 0x12         # AEAD in-core triggercfg (only for xoodyak/ascon)
    # scope
    gain_db: float = 25.0
    clock_mhz: float = 50.0
    baud: Optional[int] = None          # None => scale proven 115200 @ 50 MHz
    adc_mul: int = 4
    samples: int = 0                 # 0 => Husky trigger size; scope 2000-point pilot
    offset: int = 0                  # scope capture offset (scope-side windowing)
    # backend / storage
    backend: str = "husky"
    scope_resource: str = ""         # VISA resource for scope backend ("" = auto)
    scope_clock_source: str = "husky" # husky drives HS2 | external already supplied
    scope_dialect: str = "auto"      # auto | keysight | tek
    scope_trig_source: str = "EXTernal"
    scope_trig_level: float = 1.5     # volts; applied and read back before capture
    scope_channel: int = 1
    scope_transfer_timeout_ms: Optional[int] = None  # None => size-scaled timeout
    force_reset: bool = False        # reprogram controller before starting
    allow_nofit: bool = False        # proceed even if the estimate exceeds free disk
    suffix: str = ""                 # dataset suffix; auto = _t<traces> if empty
    chunk: int = 20_000              # checkpoint interval (traces)
    seed: int = 0                    # RNG seed (0 => os.urandom-seeded, recorded)
    port: str = ""                     # blank => identify the MCP2200 at open

    def validate(self):
        _require(self.target in TARGETS, f"target must be one of {TARGETS}")
        _require(self.key_policy in POLICIES, f"key policy must be one of {POLICIES}")
        _require(self.input_policy in POLICIES, f"input policy must be one of {POLICIES}")
        _require(isinstance(self.traces, int) and not isinstance(self.traces, bool) and
                 self.traces > 0,
                 "traces must be a positive integer greater than zero")
        _require(isinstance(self.chunk, int) and not isinstance(self.chunk, bool) and
                 self.chunk > 0, "chunk must be > 0")
        _require(isinstance(self.warmup, int) and not isinstance(self.warmup, bool) and
                 self.warmup >= 0, "warmup must be a non-negative integer")
        _require(math.isfinite(float(self.clock_mhz)) and float(self.clock_mhz) > 0,
                 "clock_mhz must be positive and finite")
        self.clock_mhz = float(self.clock_mhz)
        _require(isinstance(self.adc_mul, int) and not isinstance(self.adc_mul, bool) and
                 self.adc_mul > 0, "adc_mul must be > 0")
        _require(isinstance(self.samples, int) and not isinstance(self.samples, bool),
                 "samples must be an integer")
        _require(self.samples == 0 or self.samples >= 100,
                 "samples must be 0 for auto-size or at least 100")
        _require(isinstance(self.offset, int) and not isinstance(self.offset, bool),
                 "offset must be an integer")
        _require(self.offset >= 0, "offset must be non-negative")
        _require(math.isfinite(float(self.gain_db)), "gain_db must be finite")
        self.gain_db = float(self.gain_db)
        if self.baud is not None:
            _require(isinstance(self.baud, int) and not isinstance(self.baud, bool) and
                     self.baud > 0, "baud must be a positive integer or auto")
        _require(self.uart_host_baud > 0, "clock is too low to derive a UART baud")
        _require(self.uart_baud_error_percent <= UART_MAX_ERROR_PERCENT,
            f"host UART baud differs from the fixed-divisor wire rate by "
            f"{self.uart_baud_error_percent:.3f}% (maximum "
            f"{UART_MAX_ERROR_PERCENT:g}%)")
        _require(isinstance(self.port, str) and
                 not any(ord(c) < 32 for c in self.port),
                 "port must be plain text without control characters")
        self.port = self.port.strip()
        try:
            self.fixed_key = bytes(self.fixed_key)
            self.fixed_input = bytes(self.fixed_input)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("fixed key/input must be byte sequences") from exc
        _require(len(self.fixed_key) == 16, "fixed key must contain exactly 16 bytes")
        _require(len(self.fixed_input) == 16, "fixed input must contain exactly 16 bytes")
        _require(self.trigger_mode in ("auto", "core", "firmware"),
                 "trigger must be auto, core or firmware for AES/Sw-RV targets")
        _require(isinstance(self.aead_trigger, int) and
                 not isinstance(self.aead_trigger, bool) and
                 0 <= self.aead_trigger <= 0x7F,
                 "AEAD triggercfg must fit in seven bits (0x00..0x7f)")
        _require(self.backend in ("husky", "scope"), "backend must be husky or scope")
        _require(self.scope_clock_source in ("husky", "external"),
                 "scope_clock_source must be husky or external")
        _require(self.scope_dialect in ("auto", "keysight", "tek"),
                 "scope_dialect must be auto, keysight or tek")
        _require(isinstance(self.scope_channel, int) and
                 not isinstance(self.scope_channel, bool) and
                 1 <= self.scope_channel <= 8, "scope_channel must be 1..8")
        _require(isinstance(self.scope_resource, str) and not any(
                 ord(c) < 32 for c in self.scope_resource),
                 "scope_resource must not contain control characters")
        _require(isinstance(self.scope_trig_source, str) and
                 self.scope_trig_source and self.scope_trig_source[0].isalpha() and
                 self.scope_trig_source.isalnum(),
                 "scope trigger source must be one SCPI name such as EXTernal or CHANnel2")
        source = self.scope_trig_source.upper()
        _require(source in ("EXT", "EXTERNAL", "AUX", "AUXILIARY") or
                 re.fullmatch(r"(?:CH|CHANNEL)[1-8]", source),
                 "scope trigger source must be EXTernal/AUXiliary or CHANnel1..8")
        trigger_channel = re.fullmatch(r"(?:CH|CHANNEL)([1-8])", source)
        _require(not (self.backend == "scope" and trigger_channel and
                      int(trigger_channel.group(1)) == self.scope_channel),
                 "scope measurement channel and trigger channel must be different")
        _require(math.isfinite(float(self.scope_trig_level)),
                 "scope trigger level must be a finite voltage")
        self.scope_trig_level = float(self.scope_trig_level)
        if self.scope_transfer_timeout_ms is not None:
            _require(isinstance(self.scope_transfer_timeout_ms, int) and
                     not isinstance(self.scope_transfer_timeout_ms, bool) and
                     self.scope_transfer_timeout_ms > 0,
                     "scope transfer timeout must be a positive integer in ms or auto")
        _require(not (self.backend == "scope" and self.offset != 0),
                 "nonzero scope offset is not implemented or query-verified")
        _require(isinstance(self.suffix, str), "suffix must be text")
        _require(len(self.suffix) <= 128, "suffix must be at most 128 characters")
        _require(all(c.isalnum() or c in "._-" for c in self.suffix),
                 "suffix may contain only letters, numbers, dot, underscore and hyphen")
        _require(self.tvla_order in TVLA_ORDERS,
                 f"TVLA order must be one of {TVLA_ORDERS}")
        _require(isinstance(self.tvla_block_size, int) and
                 not isinstance(self.tvla_block_size, bool) and
                 self.tvla_block_size >= 2 and self.tvla_block_size % 2 == 0,
                 "TVLA block size must be an even integer of at least 2")
        _require(isinstance(self.tvla_per_class, int) and
                 not isinstance(self.tvla_per_class, bool) and
                 self.tvla_per_class >= 0,
                 "tvla_per_class must be a non-negative integer")
        _require(isinstance(self.seed, int) and not isinstance(self.seed, bool) and
                 self.seed >= 0, "seed must be a non-negative integer")
        if self.tvla:
            _require(self.key_policy == "fixed", "TVLA requires a fixed key")
            _require(self.traces % 2 == 0, "TVLA capture total must be even")
            if not self.tvla_per_class:
                self.tvla_per_class = self.traces // 2
            _require(self.traces == 2 * self.tvla_per_class,
                     "TVLA total must equal two times tvla_per_class")
        if self.auto_cpa and not self.tvla:
            _require(self.key_policy == "fixed", "automatic CPA requires a fixed key")
            _require(self.input_policy == "random",
                     "automatic CPA requires random plaintexts/nonces")
        return self

    @property
    def is_aead(self) -> bool:
        return self.target in AEAD

    @property
    def dataset_suffix(self) -> str:
        if self.suffix:
            return self.suffix
        if self.tvla:
            return f"_tvla_n{self.tvla_per_class or self.traces // 2}"
        return f"_t{self.traces}"

    @property
    def base(self) -> str:
        root = os.path.abspath(DATA)
        candidate = os.path.abspath(
            os.path.join(root, f"acq_{self.target}{self.dataset_suffix}"))
        if os.path.commonpath((root, candidate)) != root:
            raise ValueError("dataset path escapes the configured data directory")
        return candidate

    @property
    def input_name(self) -> str:
        return "nonce" if self.is_aead else "plaintext"

    @property
    def uart_actual_baud(self) -> float:
        """Wire rate implied by the selected clock and fixed RTL divisor 27."""
        return self.clock_mhz * 1e6 / (16 * UART_DIVISOR)

    @property
    def uart_host_baud(self) -> int:
        return self.baud if self.baud is not None else auto_uart_baud(self.clock_mhz)

    @property
    def uart_baud_error_percent(self) -> float:
        return abs(self.uart_host_baud - self.uart_actual_baud) / self.uart_actual_baud * 100

    def to_meta(self) -> dict:
        d = asdict(self)
        d["fixed_key"] = list(self.fixed_key)
        d["fixed_input"] = list(self.fixed_input)
        d["uart_fixed_divisor"] = UART_DIVISOR
        d["uart_actual_baud"] = self.uart_actual_baud
        d["uart_host_baud"] = self.uart_host_baud
        d["uart_baud_error_percent"] = self.uart_baud_error_percent
        return d
