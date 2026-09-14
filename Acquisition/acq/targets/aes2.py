"""AES2 hardware core adapter (L2.3). Identical to AES1 (same last-round datapath,
same wide Start_AES2 trigger); only the core name differs."""
from .aes1 import AES1Target


class AES2Target(AES1Target):
    core = "aes2"
    out_len = 16
