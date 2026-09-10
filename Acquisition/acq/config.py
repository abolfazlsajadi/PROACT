"""Validated instrument requests and dataset identity; no device access."""
from __future__ import annotations

import math
import os
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation

TARGETS = ("aes1", "aes2", "sw_rv", "xoodyak", "ascon")
AEAD = ("xoodyak", "ascon")
POLICIES = ("fixed", "random")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")


def parse_count(value) -> int:
    """Parse a positive integral count, including 1.5k; never truncate a fraction."""
    if isinstance(value, bool):
        raise ValueError("count must be a positive integer")
    text = str(value).strip().replace(",", "")
    match = re.fullmatch(r"([+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))([kKmM]?)", text)
    if not match:
        raise ValueError("count must be a positive integer, optionally followed by k or M")
    try:
        count = Decimal(match.group(1)) * {"": 1, "K": 1000, "M": 1000000}[match.group(2).upper()]
    except InvalidOperation as exc:
        raise ValueError("invalid count") from exc
    if not count.is_finite() or count <= 0 or count != count.to_integral_value():
        raise ValueError("count must resolve to a positive whole number")
    return int(count)


def _integer(value, name, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}")


def _finite(value, name, positive=False):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError(f"{name} must be {'positive and ' if positive else ''}finite")
    return number


@dataclass
class AcqConfig:
    target: str
    traces: int
    key_policy: str = "fixed"
    input_policy: str = "random"
    fixed_key: bytes = bytes(range(16))
    fixed_input: bytes = bytes(16)
    trigger_mode: str = "auto"
    aead_trigger: int = 0x12
    gain_db: float = 25.0
    clock_mhz: float = 50.0
    adc_mul: int = 4
    clock_source: str = ""             # resolved from backend during validation
    firmware_uart_divisor: int = 27    # declaration, not a firmware register write
    baud: int | None = None            # None => nearest integer to declared UART rate
    port: str = ""                    # explicit serial resource required for live use
    samples: int = 0
    offset: int = 0
    backend: str = "husky"
    scope_resource: str = ""
    scope_dialect: str = "auto"
    scope_model: str = ""              # expected model label; identity is queried live
    scope_trig_source: str = "EXTernal"
    scope_trig_level: float = 1.5
    scope_channel: int = 1
    force_reset: bool = False
    allow_nofit: bool = False
    suffix: str = ""
    chunk: int = 20_000
    seed: int = 0

    def validate(self, require_devices=False):
        if self.target not in TARGETS:
            raise ValueError(f"target must be one of {TARGETS}")
        if self.key_policy not in POLICIES or self.input_policy not in POLICIES:
            raise ValueError("key/input policies must be fixed or random")
        _integer(self.traces, "traces", 1)
        _integer(self.chunk, "chunk", 1)
        _integer(self.samples, "samples", 0)
        _integer(self.offset, "offset", 0)
        _integer(self.adc_mul, "ADC multiplier", 1)
        _integer(self.firmware_uart_divisor, "firmware UART divisor", 1, 65535)
        _integer(self.seed, "seed", 0, (1 << 63) - 1)
        self.clock_mhz = _finite(self.clock_mhz, "clock MHz", positive=True)
        self.gain_db = _finite(self.gain_db, "gain dB")
        self.scope_trig_level = _finite(self.scope_trig_level, "scope trigger level")
        if not math.isfinite(self.clock_mhz * 1e6 * self.adc_mul):
            raise ValueError("requested sample rate is not finite")
        if self.baud is not None:
            _integer(self.baud, "host baud", 1)
        if self.uart_host_baud < 1:
            raise ValueError("declared clock/divisor gives a host baud below 1")
        if self.uart_baud_error_percent > 2:
            raise ValueError(
                f"host baud differs from declared firmware UART rate by "
                f"{self.uart_baud_error_percent:.3f}% (maximum 2%); check clock/divisor/baud")
        for field in ("fixed_key", "fixed_input"):
            value = getattr(self, field)
            if not isinstance(value, (bytes, bytearray, memoryview)):
                raise ValueError(f"{field} must be 16 bytes")
            value = bytes(value)
            if len(value) != 16:
                raise ValueError(f"{field} must be 16 bytes")
            setattr(self, field, value)
        if self.trigger_mode == "firmware":
            raise ValueError("firmware trigger mode is not implemented; use an explicit supported mode")
        if self.trigger_mode not in ("auto", "core"):
            raise ValueError("trigger mode must be auto or core")
        _integer(self.aead_trigger, "raw AEAD trigger (7-bit field)", 0, 127)
        if self.backend not in ("husky", "scope"):
            raise ValueError("backend must be husky or scope")
        if not self.clock_source:
            self.clock_source = "husky" if self.backend == "husky" else "external"
        if self.clock_source not in ("husky", "external"):
            raise ValueError("clock source must be husky or external")
        if self.backend == "scope" and self.clock_source != "external":
            raise ValueError("scope backend requires a separately supplied external board clock")
        if self.scope_dialect not in ("auto", "keysight", "tek"):
            raise ValueError("scope dialect must be auto, keysight or tek")
        _integer(self.scope_channel, "scope channel", 1, 8)
        for field in ("port", "scope_resource", "scope_model", "scope_trig_source"):
            value = getattr(self, field)
            if not isinstance(value, str) or any(ord(c) < 32 for c in value):
                raise ValueError(f"{field} must be plain text without control characters")
            setattr(self, field, value.strip())
        if not re.fullmatch(r"(?i:EXT(?:ERNAL)?|AUX|CH(?:AN(?:NEL)?)?[1-8])", self.scope_trig_source):
            raise ValueError("scope trigger source must be EXTernal, AUX or CH1..CH8")
        if not isinstance(self.suffix, str) or not re.fullmatch(r"[A-Za-z0-9_.-]*", self.suffix):
            raise ValueError("suffix may contain only letters, digits, underscore, dot and hyphen")
        if require_devices and not self.port:
            raise ValueError("an explicit UART resource is required: --port /dev/serial/by-id/...")
        if require_devices and self.backend == "scope" and not self.scope_resource:
            raise ValueError("an explicit VISA resource is required: --scope-resource")
        return self

    @property
    def uart_actual_baud(self):
        """Calculated rate from the declared clock/divisor; not a measured rate."""
        return self.clock_mhz * 1e6 / (16 * self.firmware_uart_divisor)

    @property
    def uart_host_baud(self):
        return self.baud if self.baud is not None else round(self.uart_actual_baud)

    @property
    def uart_baud_error_percent(self):
        return abs(self.uart_host_baud - self.uart_actual_baud) / self.uart_actual_baud * 100

    @property
    def is_aead(self):
        return self.target in AEAD

    @property
    def dataset_suffix(self):
        return self.suffix or f"_t{self.traces}"

    @property
    def base(self):
        return os.path.join(DATA, f"acq_{self.target}{self.dataset_suffix}")

    @property
    def input_name(self):
        return "nonce" if self.is_aead else "plaintext"

    def to_meta(self):
        result = asdict(self)
        result["fixed_key"] = list(self.fixed_key)
        result["fixed_input"] = list(self.fixed_input)
        return result
