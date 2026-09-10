"""
Experiment input generation. Each variable (key, plaintext, nonce, ad) can be
FIXED to a value or generated RANDOMLY per run; alternatively a whole run list
can be read from a text file. Used by the crypto-experiment and capture panels.
"""
import secrets
from typing import Dict, Iterator, List, Optional

VARS = ("key", "pt", "nonce", "ad")


class Variable:
    """One input variable: fixed to `value`, or random each run."""
    def __init__(self, random: bool = True, value: Optional[bytes] = None,
                 nbytes: int = 16):
        self.random = random
        self.value = value if value is not None else bytes(nbytes)
        self.nbytes = nbytes

    def next(self) -> bytes:
        return secrets.token_bytes(self.nbytes) if self.random else self.value


class InputPlan:
    """Produce `n` runs of {key,pt,nonce,ad}. Either from per-variable specs, or
    (if `runs` is given) replayed from a parsed text file."""
    def __init__(self, variables: Dict[str, Variable] = None, n: int = 1,
                 runs: Optional[List[Dict[str, bytes]]] = None):
        self.vars = variables or {v: Variable() for v in VARS}
        self.n = n if runs is None else len(runs)
        self.runs = runs

    def __iter__(self) -> Iterator[Dict[str, bytes]]:
        if self.runs is not None:
            for r in self.runs:
                yield {v: r.get(v, bytes(16)) for v in VARS}
            return
        for _ in range(self.n):
            yield {v: self.vars[v].next() for v in VARS}

    def validate_for_hardware(self, core: str):
        """Validate the fixed-block UART contract without consuming randomness.

        The generic plan still accepts variable-length values for offline use.
        Call this method before a GUI job opens or writes to a device.
        Successful validation owns byte copies of fixed/file inputs.
        """
        if core not in ("aes1", "aes2", "swrv", "ascon", "xoodyak"):
            raise ValueError(f"unknown core {core!r}")
        if not isinstance(self.n, int) or isinstance(self.n, bool) or self.n <= 0:
            raise ValueError("number of runs must be a positive integer")
        fields = VARS if core in ("ascon", "xoodyak") else ("key", "pt")
        def checked_value(value, label, required):
            if not isinstance(value, (bytes, bytearray, memoryview)):
                raise ValueError(f"{label} must be bytes-like")
            value = bytes(value)
            if required and len(value) != 16:
                raise ValueError(f"{label} must contain exactly 16 bytes")
            return value

        if self.runs is not None:
            normalized = []
            for i, row in enumerate(self.runs, 1):
                if not isinstance(row, dict):
                    raise ValueError(f"row {i}: expected a field dictionary")
                normalized.append({field: checked_value(row.get(field, bytes(16)),
                                   f"row {i}: {field}", field in fields)
                                   for field in VARS})
            self.runs = normalized
        else:
            normalized = {}
            # Iteration generates every variable, including AES-unused nonce/ad.
            for field in VARS:
                var = self.vars.get(field)
                if not isinstance(var, Variable):
                    raise ValueError(f"missing or invalid variable {field!r}")
                if not isinstance(var.random, bool):
                    raise ValueError(f"{field}: random must be a boolean")
                if var.random:
                    width = var.nbytes
                    if not isinstance(width, int) or isinstance(width, bool) or width < 0:
                        raise ValueError(f"{field}: random byte length must be a nonnegative integer")
                    if field in fields and width != 16:
                        raise ValueError(f"{field} must contain exactly 16 bytes (got {width})")
                    normalized[field] = Variable(random=True, value=b"", nbytes=width)
                else:
                    value = checked_value(var.value, field, field in fields)
                    normalized[field] = Variable(random=False, value=value, nbytes=len(value))
            self.vars = normalized
        return self


def parse_input_file(path: str) -> List[Dict[str, bytes]]:
    """Parse a `key=.. pt=.. nonce=.. ad=..` per-line file (hex values).
    Blank lines and lines starting with '#' are ignored. Missing fields = 0."""
    runs = []
    with open(path, encoding="utf-8-sig") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            row: Dict[str, bytes] = {}
            for tok in line.replace(",", " ").split():
                if "=" not in tok:
                    raise ValueError(f"line {lineno}: expected key=hex, got '{tok}'")
                k, v = tok.split("=", 1)
                k = k.strip().lower()
                if k not in VARS:
                    raise ValueError(f"line {lineno}: unknown field '{k}'")
                if k in row:
                    raise ValueError(f"line {lineno}: duplicate field '{k}'")
                try:
                    row[k] = bytes.fromhex(v.strip())
                except ValueError:
                    raise ValueError(f"line {lineno}: invalid hex for '{k}'") from None
            runs.append(row)
    if not runs:
        raise ValueError("no input rows found in %s" % path)
    return runs
