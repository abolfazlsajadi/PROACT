"""GENERATED from config/hardware.json by scripts/gen_hardware.py -- DO NOT EDIT."""
# PROACT canonical map + command protocol, mirrored for the host.

# --- bus device bases ---
RAM_BASE                 = 0x02000000  # controller data RAM (128KB phys)
RII_IMEM_BASE            = 0x04000000  # Sw-RV instruction mem write port
RII_DMEM_BASE            = 0x08000000  # Sw-RV data mem; controller<->target mailbox
SCREG_BASE               = 0x20000000  # WRITE=control, READ=status (same address)
UART_BASE                = 0x10000000  # AHBUART
TIMER_BASE               = 0x40000000  # 32-bit trigger-window counter
RNG_BASE                 = 0x80000000  # write=seed; read routed only to Sw-RV
AES1_BASE                = 0x10001000  # AES-128 hardware core
AES2_BASE                = 0x10002000  # AES-128 hardware core (2nd)
XOODYAK_BASE             = 0x10003000  # Xoodyak AEAD core
ASCON_BASE               = 0x10005000  # ASCON AEAD core
CORE_BASE_DO_NOT_USE     = 0x10007000  # HAZARD: no hardware instance -> access HANGS the CPU. Never touch.

# --- control bits (trigger=bit30, NOT bit31) ---
CTRL_ENABLE_TARGET    = 1 << 0
CTRL_ENABLE_AES1      = 1 << 1
CTRL_START_AES1       = 1 << 2
CTRL_DEC_AES1         = 1 << 3
CTRL_ENABLE_AES2      = 1 << 4
CTRL_START_AES2       = 1 << 5
CTRL_DEC_AES2         = 1 << 6
CTRL_ENABLE_XOODYAK   = 1 << 7
CTRL_START_XOODYAK    = 1 << 8
CTRL_DEC_XOODYAK      = 1 << 9
CTRL_ENABLE_ASCON     = 1 << 10
CTRL_START_ASCON      = 1 << 11
CTRL_DEC_ASCON        = 1 << 12
CTRL_ENABLE_RNG       = 1 << 13
CTRL_ENABLE_TIMER     = 1 << 14
CTRL_RESET_UART       = 1 << 15
CTRL_TRIGGERPC        = 1 << 29
CTRL_TRIGGER          = 1 << 30
CTRL_CFGSEL_SHIFT = 20
CFGSEL_SOFTWARE   = 0 << CTRL_CFGSEL_SHIFT
CFGSEL_ASCON      = 1 << CTRL_CFGSEL_SHIFT
CFGSEL_AES1       = 2 << CTRL_CFGSEL_SHIFT
CFGSEL_AES2       = 3 << CTRL_CFGSEL_SHIFT
CFGSEL_XOODYAK    = 4 << CTRL_CFGSEL_SHIFT
CFGSEL_SWRV       = 5 << CTRL_CFGSEL_SHIFT

# --- status bits (true 32-bit; bit31 = Sw-RV target-done) ---
STAT_UART_RVALID      = 1 << 0
STAT_TRIGGER_IN       = 1 << 1
STAT_DONE_AES1        = 1 << 2
STAT_TRIGGER_AES1     = 1 << 3
STAT_DONE_AES2        = 1 << 4
STAT_TRIGGER_AES2     = 1 << 5
STAT_DONE_XOODYAK     = 1 << 6
STAT_READY_XOODYAK    = 1 << 7
STAT_TRIGGER_XOODYAK  = 1 << 8
STAT_DONE_ASCON       = 1 << 9
STAT_READY_ASCON      = 1 << 10
STAT_TRIGGER_ASCON    = 1 << 11
STAT_TEST_I           = 1 << 12
STAT_AES1_ACTIVE      = 1 << 13
STAT_AES2_ACTIVE      = 1 << 14
STAT_XOODYAK_ACTIVE   = 1 << 15
STAT_ASCON_ACTIVE     = 1 << 16
STAT_TARGET_DONE      = 1 << 31

# --- AES offsets ---
AES_START      = 0x00
AES_DONE       = 0x04
AES_KEY0       = 0x08
AES_KEY1       = 0x0C
AES_KEY2       = 0x10
AES_KEY3       = 0x14
AES_DATA0      = 0x18
AES_DATA1      = 0x1C
AES_DATA2      = 0x20
AES_DATA3      = 0x24
AES_RESULT0    = 0x28
AES_RESULT1    = 0x2C
AES_RESULT2    = 0x30
AES_RESULT3    = 0x34

# --- AEAD offsets ---
AEAD_LEN      = 0x00
AEAD_KEY      = 0x04
AEAD_NPUB     = 0x08
AEAD_AD       = 0x0C
AEAD_PT       = 0x10
AEAD_CT       = 0x14
AEAD_TAG      = 0x18

# --- UART ---
UART_RXTX = 0x00
UART_BAUD = 0x04
UART_STATUS_RX_EMPTY = 1 << 0
UART_STATUS_TX_FULL  = 1 << 1
UART_BAUD_DEFAULT = 27

# --- mailbox ---
MBOX_KEY    = 0x08003F00
MBOX_IN     = 0x08003F10
MBOX_OUT    = 0x08003F40
MBOX_CMD    = 0x08003F80
MBOX_DONE   = 0x08003F84
MBOX_CMD_IDLE = 0
MBOX_CMD_ENCRYPT = 1
MBOX_CMD_DECRYPT = 2
SWRV_DMEM_LOAD_BASE = 0x08100000

# --- UART command protocol (matches Software/Controller/main.c) ---
CMD_KEY    = 0x01
CMD_PT     = 0x02
CMD_SB     = 0x03
CMD_DBG    = 0x04
CMD_RDY    = 0x05
CMD_AES2   = 0x06
CMD_XOO    = 0x07
CMD_ASC    = 0x08
CMD_AES1   = 0x09
CMD_ARM    = 0x0A
CMD_NONCE  = 0x0B
CMD_AD     = 0x0C
CMD_DEC    = 0x0D
CMD_SEED   = 0x0E
CMD_TRIG   = 0x0F
CMD_TIME   = 0x10
CMD_TEST   = 0x11
CMD_LDI    = 0x12
CMD_LDD    = 0x13
CMD_SWRV   = 0x14
CMD_CFGSEL = 0x15
CMD_RDSTAT = 0x16
CMD_WRCTRL = 0x17
CMD_POKE   = 0x18
CMD_PEEK   = 0x19
CMD_AEADKAT = 0x1A
FRAME_MARKER = 0xA5
MODE_AES1   = 0
MODE_AES2   = 1
MODE_XOO    = 2
MODE_ASC    = 3
MODE_SWRV   = 4
MODE_TIMER  = 241
MODE_STATUS = 242
MODE_PEEK   = 243
MODE_AEADKAT = 244
