"""Target registry. make_target(cfg) returns the adapter for cfg.target."""


def make_target(cfg):
    tgt = cfg.target
    if tgt == "aes1":
        from .aes1 import AES1Target
        return AES1Target(cfg)
    if tgt == "aes2":
        from .aes2 import AES2Target
        return AES2Target(cfg)
    if tgt == "sw_rv":
        from .sw_rv import SwRVTarget
        return SwRVTarget(cfg)
    if tgt == "sw_rv_masked":
        from .sw_rv_masked import MaskedSwRVTarget
        return MaskedSwRVTarget(cfg)
    if tgt == "xoodyak":
        from .aead import XoodyakTarget
        return XoodyakTarget(cfg)
    if tgt == "ascon":
        from .aead import AsconTarget
        return AsconTarget(cfg)
    raise SystemExit(f"no adapter for target '{tgt}'")
