#!/usr/bin/env python3
"""PROACT 28-pin DIP pinout diagram (verified from netlist + user notes)."""

# pin -> (name, category, note)
# categories: pwr, gnd, io_uart, io_spi, reset, trig, dbg, clk, ctrl, misc
PINS = {
    1:  ('B_RST_N',       'reset', 'Button reset (SW1 / JP1→CW nRST)'),
    2:  ('UART_TX',       'io_uart', 'To MCP2200 RX or CW308 (via JP3)'),
    3:  ('UART_RX',       'io_uart', 'From MCP2200 TX or CW308 (via JP5)'),
    4:  ('GRST',          'reset', 'Global reset (MCP2210 GPIO2)'),
    5:  ('CRST',          'reset', 'Controller reset (MCP2210 GPIO5)'),
    6:  ('Mem[23]',       'dbg', 'Debug → LED D2 / J1 / CW'),
    7:  ('VDDIO',         'pwr', 'I/O supply (3.3 V)'),
    8:  ('GND',           'gnd', 'Ground'),
    9:  ('CLK',           'clk', 'Clock (test point CLK)'),
    10: ('Spare_In',      'dbg', 'Debug in → LED D3 / J1 / CW'),
    11: ('UART_Rvalid',   'dbg', 'Debug → LED D4 / J1 / CW'),
    12: ('Mem_Req',       'dbg', 'Debug → LED D5 / J1 / CW'),
    13: ('Trig_CFG',      'trig', 'Trigger config (J10 3-4 → CW TRIG)'),
    14: ('Trigger',       'trig', 'Normal trigger (J10 2-4 → CW TRIG)'),
    15: ('SPI_RST',       'reset', 'SPI reset (MCP2210 GPIO1)'),
    16: ('ALIVE',         'misc', 'Heartbeat → LED D1 (blinks = alive)'),
    17: ('SPI_MOSI',      'io_spi', 'MOSI (via JP6: module or CW)'),
    18: ('S_Sel',         'io_spi', 'Slave select (via J6: module or CW)'),
    19: ('SPI_SCK',       'io_spi', 'SCK (via JP4: module or CW)'),
    20: ('VCORE',         'pwr', 'Core supply via R7 shunt'),
    21: ('CoProc_Req',    'dbg', 'Debug → LED D6 / J1 / CW'),
    22: ('VDDIO',         'pwr', 'I/O supply (3.3 V)'),
    23: ('Trig_In',       'trig', 'Trigger input (J6 ↔ CW GPIO3)'),
    24: ('PC2',           'ctrl', 'IBEX program-counter bit (JP2)'),
    25: ('PC3',           'ctrl', 'IBEX program-counter bit (JP2)'),
    26: ('PC4',           'ctrl', 'IBEX program-counter bit (JP2)'),
    27: ('Rsv_Trig',      'trig', 'Reserve trigger (J10 4-6 → CW TRIG)'),
    28: ('VCORE',         'pwr', 'Core supply via R7 shunt'),
}

CAT_COL = {
    'pwr':     '#b45309', 'gnd': '#334155', 'io_uart': '#0f766e', 'io_spi': '#1d4ed8',
    'reset':   '#dc2626', 'trig': '#7c3aed', 'dbg': '#c2410c', 'clk': '#0891b2',
    'ctrl':    '#4d7c0f', 'misc': '#64748b',
}
CAT_NAME = {
    'pwr': 'Power', 'gnd': 'Ground', 'io_uart': 'UART', 'io_spi': 'SPI',
    'reset': 'Reset', 'trig': 'Trigger', 'dbg': 'Debug monitor', 'clk': 'Clock',
    'ctrl': 'Core control', 'misc': 'Status',
}

# layout: DIP-28, pins 1-14 left (top→bottom), 15-28 right (bottom→top)
ROWS = 14
PIN_DY = 34
TOP = 96
BODY_X = 300
BODY_W = 150
LEAD = 16
PAD_W = 250   # label pad width
FS = 12.5

W = 1020
H = TOP + ROWS * PIN_DY + 150

def pin_y(i):
    if i <= 14:
        return TOP + (i - 1) * PIN_DY + PIN_DY / 2
    return TOP + (28 - i) * PIN_DY + PIN_DY / 2

svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
       f'font-family="Inter, Segoe UI, Arial, sans-serif">']
svg.append(f'<rect width="{W}" height="{H}" fill="#0b1220"/>')

# title
svg.append(f'<text x="{W/2}" y="40" font-size="22" font-weight="800" text-anchor="middle" '
           f'fill="#f8fafc">PROACT ASIC — DIP-28 Pinout</text>')
svg.append(f'<text x="{W/2}" y="64" font-size="12" font-weight="600" text-anchor="middle" '
           f'fill="#94a3b8">socket U1 · pin 1 at top-left (notch up) · verified against board netlist</text>')

# chip body
bx0 = BODY_X
by0 = TOP
by1 = TOP + ROWS * PIN_DY
svg.append(f'<rect x="{bx0}" y="{by0}" width="{BODY_W}" height="{by1-by0}" rx="10" '
           f'fill="#111827" stroke="#475569" stroke-width="2"/>')
# notch
svg.append(f'<path d="M {bx0+BODY_W/2-16},{by0} a16,16 0 0 0 32,0" fill="#0b1220" '
           f'stroke="#475569" stroke-width="2"/>')
svg.append(f'<text x="{bx0+BODY_W/2}" y="{(by0+by1)/2-6}" font-size="17" font-weight="800" '
           f'text-anchor="middle" fill="#e2e8f0">PROACT</text>')
svg.append(f'<text x="{bx0+BODY_W/2}" y="{(by0+by1)/2+14}" font-size="11" font-weight="600" '
           f'text-anchor="middle" fill="#64748b">U1 · DIP-28</text>')

def draw_pin(i, side):
    y = pin_y(i)
    name, cat, note = PINS[i]
    col = CAT_COL[cat]
    if side == 'L':
        px = bx0
        # lead
        svg.append(f'<line x1="{px-LEAD}" y1="{y}" x2="{px}" y2="{y}" stroke="#64748b" stroke-width="2"/>')
        svg.append(f'<circle cx="{px-LEAD}" cy="{y}" r="4" fill="#94a3b8"/>')
        # pin number box
        svg.append(f'<rect x="{px+4}" y="{y-9}" width="20" height="18" rx="3" fill="#1e293b"/>')
        svg.append(f'<text x="{px+14}" y="{y+4}" font-size="11" font-weight="700" '
                   f'text-anchor="middle" fill="#cbd5e1">{i}</text>')
        # label pad to the left
        lx1 = px - LEAD - 6
        lx0 = lx1 - PAD_W
        svg.append(f'<rect x="{lx0}" y="{y-13}" width="{PAD_W}" height="26" rx="5" '
                   f'fill="{col}" fill-opacity="0.16" stroke="{col}" stroke-width="1.6"/>')
        svg.append(f'<text x="{lx0+10}" y="{y-1}" font-size="{FS}" font-weight="800" '
                   f'fill="#f1f5f9">{name}</text>')
        svg.append(f'<text x="{lx0+10}" y="{y+11}" font-size="8.5" font-weight="500" '
                   f'fill="#94a3b8">{note}</text>')
    else:
        px = bx0 + BODY_W
        svg.append(f'<line x1="{px}" y1="{y}" x2="{px+LEAD}" y2="{y}" stroke="#64748b" stroke-width="2"/>')
        svg.append(f'<circle cx="{px+LEAD}" cy="{y}" r="4" fill="#94a3b8"/>')
        svg.append(f'<rect x="{px-24}" y="{y-9}" width="20" height="18" rx="3" fill="#1e293b"/>')
        svg.append(f'<text x="{px-14}" y="{y+4}" font-size="11" font-weight="700" '
                   f'text-anchor="middle" fill="#cbd5e1">{i}</text>')
        lx0 = px + LEAD + 6
        svg.append(f'<rect x="{lx0}" y="{y-13}" width="{PAD_W}" height="26" rx="5" '
                   f'fill="{col}" fill-opacity="0.16" stroke="{col}" stroke-width="1.6"/>')
        svg.append(f'<text x="{lx0+PAD_W-10}" y="{y-1}" font-size="{FS}" font-weight="800" '
                   f'text-anchor="end" fill="#f1f5f9">{name}</text>')
        svg.append(f'<text x="{lx0+PAD_W-10}" y="{y+11}" font-size="8.5" font-weight="500" '
                   f'text-anchor="end" fill="#94a3b8">{note}</text>')

for i in range(1, 15):
    draw_pin(i, 'L')
for i in range(15, 29):
    draw_pin(i, 'R')

# legend
cats = ['pwr', 'gnd', 'clk', 'io_uart', 'io_spi', 'reset', 'trig', 'dbg', 'ctrl', 'misc']
ly = H - 60
svg.append(f'<rect x="30" y="{ly-18}" width="{W-60}" height="66" rx="8" fill="#0e1729" '
           f'stroke="#1e293b" stroke-width="1"/>')
cols = 5
cw = (W - 90) / cols
for idx, c in enumerate(cats):
    r, cc = divmod(idx, cols)
    x = 46 + cc * cw
    y = ly + r * 26
    svg.append(f'<rect x="{x}" y="{y-10}" width="15" height="15" rx="3" fill="{CAT_COL[c]}"/>')
    svg.append(f'<text x="{x+21}" y="{y+2}" font-size="11" font-weight="600" '
               f'fill="#cbd5e1">{CAT_NAME[c]}</text>')

svg.append('</svg>')
open('/home/abish/Downloads/PCB/PROACT_DOC/docs/img/proact_pinout.svg', 'w').write('\n'.join(svg))
print('wrote proact_pinout.svg', W, H)
