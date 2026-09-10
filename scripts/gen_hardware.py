#!/usr/bin/env python3
"""
gen_hardware.py -- generate the C header and Python register module from the
ONE source of truth, config/hardware.json. Run after editing hardware.json:

    python3 scripts/gen_hardware.py

Writes:
    Software/common/proact_regs.h        (controller + target C)
    Software/Python/proact_host/regs.py  (host Python)

Both are marked "GENERATED -- do not edit". stdlib only (no PyYAML/etc.).
Verify the C output against the frozen RTL with tools/verify_regs_vs_rtl.py.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
SRC = os.path.join(ROOT, "config", "hardware.json")
H_OUT = os.path.join(ROOT, "Software", "common", "proact_regs.h")
PY_OUT = os.path.join(ROOT, "Software", "Python", "proact_host", "regs.py")

BANNER = "GENERATED from config/hardware.json by scripts/gen_hardware.py -- DO NOT EDIT."


def load():
    with open(SRC) as f:
        return json.load(f)


def hx(v):
    return v if isinstance(v, str) else hex(v)


def gen_c(hw):
    L = []
    a = L.append
    a("/* " + BANNER + " */")
    a("/* PROACT canonical register/address map, derived from the frozen ASIC RTL. */")
    a("#ifndef PROACT_REGS_H\n#define PROACT_REGS_H\n\n#include <stdint.h>\n")

    a("/* ---- bus device bases / masks ---- */")
    for d in hw["devices"]:
        nm = "PROACT_%s_BASE" % d["name"] if d["name"] != "CORE_UNIMPLEMENTED" \
            else "PROACT_CORE_BASE_DO_NOT_USE"
        a("#define %-32s %su  /* %s: %s */" % (nm, d["base"], d["size"], d["note"]))
    a("")

    a("/* ---- control register bits (WRITE; 31-bit, data bit31 truncated; trigger=bit30) ---- */")
    for name, bit in hw["control_bits"].items():
        if name.startswith("_"):
            continue
        a("#define CTRL_%-16s (1u << %d)" % (name, bit))
    cf = hw["control_fields"]["CFGSEL"]
    a("#define CTRL_CFGSEL_SHIFT %d" % cf["shift"])
    a("#define CTRL_CFGSEL_MASK  (0x%Xu << CTRL_CFGSEL_SHIFT)" % ((1 << cf["width"]) - 1))
    for vn, vv in cf["values"].items():
        a("#define CTRL_CFGSEL_%-10s (0x%Xu << CTRL_CFGSEL_SHIFT)" % (vn, vv))
    a("")

    a("/* ---- status register bits (READ; true 32-bit; bit31 = Sw-RV target-done) ---- */")
    for name, bit in hw["status_bits"].items():
        if name.startswith("_"):
            continue
        a("#define STAT_%-16s (1u << %d)" % (name, bit))
    a("")

    a("/* ---- AES1/AES2 offsets ---- */")
    for name, off in hw["aes_offsets"].items():
        if name.startswith("_"):
            continue
        a("#define AES_%-10s %su" % (name, off))
    a("")

    a("/* ---- ASCON/Xoodyak offsets ---- */")
    for name, off in hw["aead_offsets"].items():
        if name.startswith("_"):
            continue
        a("#define AEAD_%-8s %su" % (name, off))
    a("#define AEAD_LEN_WORD(triggercfg, pt_len, ad_len) \\")
    a("    ((((uint32_t)(triggercfg) & 0xFFu) << 16) | \\")
    a("     (((uint32_t)(pt_len)     & 0xFFu) <<  8) | \\")
    a("     (( (uint32_t)(ad_len)    & 0xFFu)      ))")
    a("")

    u = hw["uart"]
    a("/* ---- UART ---- */")
    a("#define UART_RXTX %su" % u["RXTX"])
    a("#define UART_BAUD %su" % u["BAUD"])
    a("#define UART_STATUS_RX_EMPTY (1u << %d)" % u["status_rx_empty_bit"])
    a("#define UART_STATUS_TX_FULL  (1u << %d)" % u["status_tx_full_bit"])
    a("#define UART_BAUD_DEFAULT    %du" % u["baud_default_divisor"])
    a("")

    a("static inline void     proact_write32(uint32_t a, uint32_t v){ *(volatile uint32_t*)a = v; }")
    a("static inline uint32_t proact_read32(uint32_t a){ return *(volatile uint32_t*)a; }")
    a("\n#endif /* PROACT_REGS_H */")
    return "\n".join(L) + "\n"


def gen_py(hw):
    L = []
    a = L.append
    a('"""' + BANNER + '"""')
    a("# PROACT canonical map + command protocol, mirrored for the host.\n")
    a("# --- bus device bases ---")
    for d in hw["devices"]:
        nm = "%s_BASE" % d["name"] if d["name"] != "CORE_UNIMPLEMENTED" else "CORE_BASE_DO_NOT_USE"
        a("%-24s = %s  # %s" % (nm, d["base"], d["note"]))
    a("")
    a("# --- control bits (trigger=bit30, NOT bit31) ---")
    for name, bit in hw["control_bits"].items():
        if name.startswith("_"):
            continue
        a("CTRL_%-16s = 1 << %d" % (name, bit))
    cf = hw["control_fields"]["CFGSEL"]
    a("CTRL_CFGSEL_SHIFT = %d" % cf["shift"])
    for vn, vv in cf["values"].items():
        a("CFGSEL_%-10s = %d << CTRL_CFGSEL_SHIFT" % (vn, vv))
    a("")
    a("# --- status bits (true 32-bit; bit31 = Sw-RV target-done) ---")
    for name, bit in hw["status_bits"].items():
        if name.startswith("_"):
            continue
        a("STAT_%-16s = 1 << %d" % (name, bit))
    a("")
    a("# --- AES offsets ---")
    for name, off in hw["aes_offsets"].items():
        if name.startswith("_"):
            continue
        a("AES_%-10s = %s" % (name, off))
    a("")
    a("# --- AEAD offsets ---")
    for name, off in hw["aead_offsets"].items():
        if name.startswith("_"):
            continue
        a("AEAD_%-8s = %s" % (name, off))
    a("")
    a("# --- UART ---")
    u = hw["uart"]
    a("UART_RXTX = %s" % u["RXTX"])
    a("UART_BAUD = %s" % u["BAUD"])
    a("UART_STATUS_RX_EMPTY = 1 << %d" % u["status_rx_empty_bit"])
    a("UART_STATUS_TX_FULL  = 1 << %d" % u["status_tx_full_bit"])
    a("UART_BAUD_DEFAULT = %d" % u["baud_default_divisor"])
    a("")
    a("# --- mailbox ---")
    mb = hw["mailbox"]
    for k in ("KEY", "IN", "OUT", "CMD", "DONE"):
        a("MBOX_%-6s = %s" % (k, mb[k]))
    a("MBOX_CMD_IDLE = %d" % mb["CMD_IDLE"])
    a("MBOX_CMD_ENCRYPT = %d" % mb["CMD_ENCRYPT"])
    a("MBOX_CMD_DECRYPT = %d" % mb["CMD_DECRYPT"])
    a("SWRV_DMEM_LOAD_BASE = %s" % mb["swrv_dmem_load_base"])
    a("")
    a("# --- UART command protocol (matches Software/Controller/main.c) ---")
    p = hw["uart_protocol"]
    for k, v in p.items():
        if k in ("_comment", "modes", "FRAME_MARKER"):
            continue
        a("CMD_%-6s = 0x%02X" % (k, v))
    a("FRAME_MARKER = 0x%02X" % p["FRAME_MARKER"])
    for mn, mv in p["modes"].items():
        a("MODE_%-6s = %d" % (mn, mv))
    return "\n".join(L) + "\n"


def main():
    hw = load()
    with open(H_OUT, "w") as f:
        f.write(gen_c(hw))
    with open(PY_OUT, "w") as f:
        f.write(gen_py(hw))
    print("generated:\n  %s\n  %s" % (H_OUT, PY_OUT))


if __name__ == "__main__":
    main()
