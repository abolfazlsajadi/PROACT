"""
Parse the byte-swapped `@address / data words` .vmem files produced by the
firmware Makefiles (srec_cat ... -byte-swap 4 -vmem).

Returns a list of (word_address, value) pairs. `word_address` is the address
field from the @ line incremented per word (as the SPI loader expects).
"""
from typing import List, Tuple
import re


def parse_vmem(path: str) -> List[Tuple[int, int]]:
    words: List[Tuple[int, int]] = []
    addr = 0
    with open(path, "r", encoding="utf-8-sig") as f:
        text = f.read()
    # Preserve line numbers while removing both inline and multiline comments.
    text = re.sub(r"/\*.*?\*/|//[^\n]*", lambda m: " " + "\n" * m[0].count("\n"),
                  text, flags=re.DOTALL)
    if "/*" in text:
        line = text[:text.index("/*")].count("\n") + 1
        raise ValueError(f"{path}:{line}: unterminated block comment")
    for line_number, line in enumerate(text.splitlines(), 1):
        for token in line.split():
            address_token = token.startswith("@")
            number = token[1:] if address_token else token
            try:
                value = int(number, 16)
            except ValueError:
                raise ValueError(f"{path}:{line_number}: invalid hexadecimal token {token!r}") from None
            if not 0 <= value <= 0xFFFFFFFF:
                raise ValueError(f"{path}:{line_number}: {token!r} exceeds an unsigned 32-bit word")
            if address_token:
                addr = value
            else:
                if addr > 0xFFFFFFFF:
                    raise ValueError(f"{path}:{line_number}: word address exceeds 0xffffffff")
                words.append((addr, value))
                addr += 1
    return words


def vmem_values(path: str) -> List[int]:
    """Just the data words, in order (for streaming to a mem write port)."""
    return [v for _, v in parse_vmem(path)]
