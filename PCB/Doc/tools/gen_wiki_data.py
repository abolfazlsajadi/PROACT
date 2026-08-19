#!/usr/bin/env python3
"""Build the self-contained interactive board wiki: docs/PROACT-Board-Wiki-v2.html

- Geometry (component centers, per-pin pads, board outline) parsed from the final
  PCB1.PcbDoc (same recipe as gen_jumpers.py / jumper_agent_notes.md).
- Connectivity from /tmp/netlist_final.txt (same-name lines merge; unnamed lines are
  individual nets; single-pin nets dropped).
- ALL descriptive facts transcribed from README.md (verified v2 reference).
- App shell: wiki_app.css + wiki_app.js are inlined; data as a JSON blob in a
  <script type="application/json"> tag (with '</' escaped).

Run:  python3 docs/tools/gen_wiki_data.py
"""
import olefile, os, json, base64, io, re

ROOT = '/home/abish/Downloads/PCB/PROACT_DOC'
PCB = f'{ROOT}/PCB_finalx/PCB_Projectcxfinal/PCB1.PcbDoc'   # newest PcbDoc (final silk)
IMG = f'{ROOT}/docs/img'
TOOLS = f'{ROOT}/docs/tools'
OUTFILE = f'{ROOT}/docs/PROACT-Board-Wiki-v2.html'
NETLIST = '/tmp/netlist_final.txt'

# ------------------------------------------------------------------ geometry --
ole = olefile.OleFileIO(PCB)
def u32(d, o): return int.from_bytes(d[o:o+4], 'little')

d = ole.openstream('Components6/Data').read(); off = 0; COMPS = []
while off + 4 <= len(d):
    ln = u32(d, off); off += 4
    if ln <= 0 or off + ln > len(d): break
    f = dict(p.split('=', 1) for p in d[off:off+ln].decode('latin1', 'replace').split('|') if '=' in p)
    off += ln
    mil = lambda v: float(v.replace('mil', '')) * 0.0254
    COMPS.append(dict(des=f.get('SOURCEDESIGNATOR'), x=mil(f.get('X', '0mil')),
                      y=mil(f.get('Y', '0mil')), rot=float(f.get('ROTATION', '0')),
                      lib=f.get('SOURCELIBREFERENCE'),
                      layer='B' if f.get('LAYER') == 'BOTTOM' else 'T'))

d = ole.openstream('Pads6/Data').read(); off = 0; PADS = {}
while off < len(d) - 5:
    if d[off] != 2: break
    off += 1
    ln = u32(d, off); off += 4
    name = d[off+1:off+1+d[off]].decode('latin1'); off += ln
    g = None
    for _ in range(8):
        bl = u32(d, off); off += 4
        blob = d[off:off+bl]; off += bl
        if bl >= 100: g = blob; break
    if g is None: break
    comp = int.from_bytes(g[7:9], 'little')
    x = int.from_bytes(g[13:17], 'little', signed=True) / 10000 * 0.0254
    y = int.from_bytes(g[17:21], 'little', signed=True) / 10000 * 0.0254
    sx = int.from_bytes(g[21:25], 'little', signed=True) / 10000 * 0.0254  # top-layer pad size
    sy = int.from_bytes(g[25:29], 'little', signed=True) / 10000 * 0.0254
    size = round(max(sx, sy), 3)                       # e.g. 1.6 for the 2x3 header pads
    if comp < len(COMPS):
        pad = (round(x, 3), round(y, 3)) + ((size,) if 0.2 <= size <= 10 else ())
        PADS.setdefault(COMPS[comp]['des'], {})[name] = pad
    if off < len(d) and d[off] != 2:
        bl = u32(d, off)
        if 0 <= bl < len(d) - off: off += 4 + bl

BOARD = dict(x0=319.40, y0=196.72, x1=373.25, y1=290.07)

SIZE = {  # w,h mm in the FOOTPRINT LIBRARY 0-deg frame (w = X extent at ROTATION=0);
          # the JS rotAABB() swaps w/h when ROTATION rounds into 90/270 — NEVER enter
          # pre-rotated "as placed" extents here (that double-rotates; the U1/S1 bug).
          # Same dict as gen_v2_view.py / gen_jumpers.py, verified against Pads6 bboxes.
        'OSC_4_50MHz_3225E': (3.2, 2.5), 'CONN_RF_BNC_RA': (9, 13), 'ResPot': (6.8, 4.8),
        'HDR_F_2X3': (7.9, 5.6), 'JUMPER_HDR_3P': (7.6, 2.6), 'HDR_M_1X7': (17.8, 2.6),
        'HDR_F_1X20': (50.8, 2.6), 'HDR_M_2X5': (12.7, 5.1), 'JUMPER_HDR_2P': (5.1, 2.6),
        'DIPSW254S_7P': (10.0, 18.5), 'SOCKET_IC_DIP_28': (15.5, 36.0),
        'TPS74801DRCR': (3.2, 3.2), 'SWPBS_SPDT_5X5X1_SKQGAFE010': (6, 6),
        'LED_SMD_0805_RED': (2.0, 1.3), 'cap_SMD': (2.0, 1.3), 'Res_SMD': (2.0, 1.3),
        'TESTPOINT_BIG_BLK1': (3.4, 3.4)}

# ------------------------------------------------------------------- netlist --
named, unnamed = {}, []
for line in open(NETLIST):
    line = line.strip()
    if not line: continue
    nm, mem = line.split(':', 1)
    nm = nm.strip(); mem = [m.strip() for m in mem.split(',') if m.strip()]
    if nm == '(unnamed)': unnamed.append(mem)
    else: named.setdefault(nm, []).extend(mem)

# hand labels for meaningful unnamed nets (keyed by member frozenset)
NETNAMES = {
 frozenset({'J7.3','R8.1'}): ('CW clock in (J7.3)', 'ChipWhisperer clock leg: J7.3 (the CW308 CLKFB line) through R8 100 Ω into J12 pin 5 — selected as the chip clock by J12 5-6.'),
 frozenset({'J6.5','J7.9'}): ('CW GPIO3 (J7.9)', 'ChipWhisperer GPIO3 line into the J6 routing block (S-Sel or trigger_in source).'),
 frozenset({'J10.4','J7.10'}): ('CW TRIG / GPIO4 (J7.10)', 'ChipWhisperer trigger line — J10 picks which chip trigger drives it.'),
 frozenset({'J7.11','J9.15','JP1.2'}): ('CW nRST (J7.11 / J9.15)', 'ChipWhisperer reset line; reaches chip B_RST_N when JP1 is closed.'),
 frozenset({'J7.12','J9.13','JP4.1'}): ('CW SCK (J7.12)', 'SPI clock from the ChipWhisperer — JP4 position 1-2.'),
 frozenset({'J7.14','J9.14','JP6.1'}): ('CW MOSI (J7.14)', 'SPI data from the ChipWhisperer — JP6 position 1-2.'),
 frozenset({'JP3.2','U1.2'}): ('chip TX (pin 2)', 'PROACT UART transmit — centre pin of JP3.'),
 frozenset({'JP5.2','U1.3'}): ('chip RX (pin 3)', 'PROACT UART receive — centre pin of JP5.'),
 frozenset({'J10.3','U1.13'}): ('out_pins[1] — config trigger', 'Configuration-phase trigger (chip pin 13) → J10 link 3-4.'),
 frozenset({'J10.2','U1.14'}): ('trigger_Out — normal trigger', 'Main trigger output (chip pin 14) → J10 link 2-4.'),
 frozenset({'R10.2','U1.16'}): ('out_pins[7] — alive', 'Heartbeat output (chip pin 16) → yellow-green LED D1 via R10.'),
 frozenset({'JP6.2','U1.17'}): ('SIn — SPI MOSI (pin 17)', 'SPI data into the chip — centre pin of JP6.'),
 frozenset({'J6.3','U1.18'}): ('SSel_n (pin 18)', 'SPI select, active low — centre column of J6.'),
 frozenset({'JP4.2','U1.19'}): ('sck — SPI clock (pin 19)', 'SPI clock into the chip — centre pin of JP4.'),
 frozenset({'JP2.3','U1.24'}): ('out_pins[4] — IBEX PC bit 4', 'Program-counter probe bit (chip pin 24) on JP2.3.'),
 frozenset({'JP2.2','U1.25'}): ('out_pins[3] — IBEX PC bit 3', 'Program-counter probe bit (chip pin 25) on JP2.2.'),
 frozenset({'JP2.1','U1.26'}): ('out_pins[2] — IBEX PC bit 2', 'Program-counter probe bit (chip pin 26) on JP2.1.'),
 frozenset({'J10.6','U1.27'}): ('out_pins[8] — reserve trigger', 'Reserve / software trigger (chip pin 27) → J10 link 4-6.'),
 frozenset({'J4.5','J6.1','R3.2'}): ('MCP2210 GPIO4 — S-Sel source', 'Module SPI-select line into J6 (link 1-3); read back through R3 / S1-1.'),
 frozenset({'J4.7','JP4.3'}): ('MCP2210 SCK', 'SPI clock from the USB module — JP4 position 2-3.'),
 frozenset({'J4.6','JP6.3'}): ('MCP2210 MOSI', 'SPI data from the USB module — JP6 position 2-3.'),
 frozenset({'J2.6','JP5.3'}): ('MCP2200 TX', 'UART from the USB module toward chip RX — JP5 position 2-3.'),
 frozenset({'J2.7','JP3.3'}): ('MCP2200 RX', 'UART into the USB module from chip TX — JP3 position 2-3.'),
 frozenset({'J11.1','J12.1'}): ('EXT CLK — SMA J11', 'External clock from the J11 jack into J12 pin 1 (source link 1-2).'),
 frozenset({'R22.1','Y1.3'}): ('Y1 output', '50 MHz oscillator output into series resistor R22.'),
 frozenset({'J12.3','R22.2'}): ('OSC CLK (via R22 20 Ω)', 'On-board 50 MHz into J12 pin 3 (source link 3-4).'),
 frozenset({'C1.2','C2.1','J5.1','S1.8'}): ('MCP2210 VDD (S1-7)', 'SPI-bridge module supply — powered from VDDIO when S1-7 is on; decoupled by C1/C2.'),
 frozenset({'C3.2','C4.1','J3.1','S1.9'}): ('MCP2200 VDD (S1-6)', 'UART-bridge module supply — powered from VDDIO when S1-6 is on; decoupled by C3/C4.'),
 frozenset({'J4.1','S1.11'}): ('MCP2210 GPIO0 (read-back)', 'Reads GPIO1 / spi_c_RST_N when S1-4 is on.'),
 frozenset({'J4.4','S1.14'}): ('MCP2210 GPIO3 (read-back)', 'Reads GPIO4 / SSel_n when S1-1 is on.'),
 frozenset({'J5.3','S1.12'}): ('MCP2210 GPIO8 (read-back)', 'Reads GPIO2 / spi_global_RST_N when S1-3 is on.'),
 frozenset({'J5.4','S1.13'}): ('MCP2210 GPIO7 (read-back)', 'Reads the X1 debug bus (J1) when S1-2 is on.'),
 frozenset({'J5.5','R5.2'}): ('MCP2210 GPIO6 (read-back)', 'Reads GPIO5 / C_RST_N through R5 when S1-5 is on.'),
 frozenset({'R2.1','S1.4'}): ('S1-4 loop leg (via R2)', 'spi_c_RST_N read-back path through 10 k series resistor R2.'),
 frozenset({'R5.1','S1.5'}): ('S1-5 loop leg (via R5)', 'C_RST_N read-back path through 10 k series resistor R5.'),
 frozenset({'R1.1','S1.3'}): ('S1-3 loop leg (via R1)', 'spi_global_RST_N read-back path through 10 k series resistor R1.'),
 frozenset({'R3.1','S1.1'}): ('S1-1 loop leg (via R3)', 'SSel_n read-back path through 10 k series resistor R3.'),
 frozenset({'R4.2','S1.2'}): ('S1-2 loop leg (via R4)', 'X1 debug-bus read-back path through 10 k series resistor R4.'),
 frozenset({'R20.2','R20.3','R21.2','U2.8'}): ('LDO feedback (R20 / R21)', 'TPS74801 feedback node — R20 trimmer against R21 8.2 k sets Vcore = 0.8 V × (1 + R20/8.2 k).'),
 frozenset({'C18.1','U2.7'}): ('LDO soft-start (C18)', 'TPS74801 soft-start capacitor, 10 nF.'),
 frozenset({'D1.1','R10.1'}): ('D1 LED leg', 'Alive LED D1 to its 560 Ω resistor R10.'),
 frozenset({'D2.2','R11.2'}): ('D2 LED leg', 'Debug LED D2 to its 560 Ω resistor R11.'),
 frozenset({'D3.2','R12.2'}): ('D3 LED leg', 'Spare-input LED D3 to its 560 Ω resistor R12.'),
 frozenset({'D4.2','R13.2'}): ('D4 LED leg', 'Debug LED D4 to its 560 Ω resistor R13.'),
 frozenset({'D5.2','R14.2'}): ('D5 LED leg', 'Debug LED D5 to its 560 Ω resistor R14.'),
 frozenset({'D6.2','R15.2'}): ('D6 LED leg', 'Debug LED D6 to its 560 Ω resistor R15.'),
 frozenset({'D7.2','R6.2'}): ('D7 LED leg', 'Reset LED D7 to its 100 Ω resistor R6 (into B_RST_N).'),
 frozenset({'D8.2','R16.2'}): ('D8 LED leg', 'Reset LED D8 to its 100 Ω resistor R16 (into CRST).'),
 frozenset({'D9.2','R17.2'}): ('D9 LED leg', 'Reset LED D9 to its 100 Ω resistor R17 (into GRST).'),
 frozenset({'D10.2','R18.2'}): ('D10 LED leg', 'Reset LED D10 to its 100 Ω resistor R18 (into SPI_RST).'),
}

NETDESC = {
 '0.8V': ('0.8 V — LDO output rail', 'Trimmed core rail from U2, before the JP7 route and the R7 shunt. Also exits to the CW308 filter input on J8.8 (FILTIN).', False),
 'A1': ('trigger_in (pin 23)', 'Chip trigger input — driven from CW308 GPIO3 via J6 link 5-6.', False),
 'B_RST_N': ('B_RST_N — button reset (pin 1)', 'Active-low reset: SW1 button, R9 10 k pull-up, JP1 link to CW nRST, red LED D7.', False),
 'CLK': ('CW clock leg (J12.5)', 'ChipWhisperer clock into the selector: J7.3 (the CW308 CLKFB line) → R8 100 Ω → J12 pin 5. Becomes the chip clock with J12 5-6.', False),
 'CLK_pin': ('chip clock — SYSCLK_P (pin 9)', 'The chip clock net: J12 even pins, the CLK test point, and J7.5 — the permanent clock echo out to the ChipWhisperer.', False),
 'CRST': ('C_RST_N — controller reset (pin 5)', 'Driven by MCP2210 GPIO5; mirrored on CW308 LED2 (J8.19); red LED D8; read back via S1-5.', False),
 'D1': ('out_pins[0] — debug (pin 6)', 'Debug output: yellow LED D2, header J1.9, CW308 south J9.20. (Board net name "D1".)', False),
 'D2': ('spare_io (pin 10)', 'User-drivable spare input: orange LED D3, J1.7, J9.19. (Board net name "D2".)', False),
 'D3': ('out_pins[5] — debug (pin 11)', 'Debug output: yellow LED D4, J1.5, J9.18. (Board net name "D3".)', False),
 'D4': ('out_pins[6] — debug (pin 12)', 'Debug output: yellow LED D5, J1.3, J9.17. (Board net name "D4".)', False),
 'D5': ('out_pins[11] — debug (pin 21)', 'Debug output: yellow LED D6, J1.1, J9.11. (Board net name "D5".)', False),
 'GND': ('GND', 'Ground plane.', True),
 'GRST': ('spi_global_RST_N — global reset (pin 4)', 'Driven by MCP2210 GPIO2; mirrored on CW308 LED3 (J8.20); red LED D9; read back via S1-3.', False),
 'RX': ('RX net — chip TX → CW TIO2', 'Carries chip TX (via JP3 1-2) to CW308 J7.8. Net is named from the receiver’s perspective.', False),
 'SPI_RST': ('spi_c_RST_N — SPI reset (pin 20)', 'Driven by MCP2210 GPIO1; mirrored on CW308 LED1 (J8.18); red LED D10; read back via S1-4.', False),
 'TX': ('TX net — CW TIO1 → chip RX', 'Carries CW308 TIO1 (J7.7) toward chip RX (via JP5 1-2). Net is named from the transmitter’s perspective.', False),
 'VDDIO': ('VDDIO — 3.3 V I/O rail', 'From the CW308 (J8.14) or the VDDIO test point on the bench. Feeds the I/O ring, bridge modules (S1-6/7), pull-ups and the U2 LDO.', True),
 'Vcore': ('Vcore — die side of the shunt', 'Core rail after R7, chip pins 15 & 28, J8.2 (SHUNT-L), Vcore test point. Deliberately capacitor-free.', False),
 'Vcore_back': ('Vcore_back — filtered rail return', '0.8 V returning from the CW308 L-C filter on J8.5/6, into JP7 pin 1 (route 1-2).', False),
 'Vcore_shunt_1': ('Vcore_shunt_1 — supply side of the shunt', 'Rail between JP7 and R7: decoupling C5/C10, J8.3 (SHUNT-H).', False),
 'X1': ('X1 — J1 debug read-back bus', 'Common line of the J1 monitor header; read by MCP2210 GPIO7 via R4 when S1-2 is on.', False),
 '1.2V': ('1.2 V CW308 rail (unused)', 'Present on J8.11 only.', True),
 '1.8V': ('1.8 V CW308 rail (unused)', 'Present on J8.12 only.', True),
 '2.5V': ('2.5 V CW308 rail (unused)', 'Present on J8.13 only.', True),
 '5V': ('5 V CW308 rail (unused)', 'Present on J8.15 only.', True),
}

NETS = {}
for nm, mem in named.items():
    mem = sorted(set(mem))
    label, desc, power = NETDESC.get(nm, (nm, '', False))
    NETS[nm] = dict(label=label, members=mem, desc=desc, power=power)
ui = 0
for mem in unnamed:
    if len(mem) < 2: continue
    key = frozenset(mem)
    label, desc = NETNAMES.get(key, (' ↔ '.join(sorted(mem)), ''))
    NETS['u%02d' % ui] = dict(label=label, members=sorted(mem), desc=desc, power=False)
    ui += 1

# --------------------------------------------------------------- component DB -
C_UART='#14b8a6'; C_SPI='#3b82f6'; C_RST='#f87171'; C_TRG='#a78bfa'; C_PWR='#f59e0b'
C_CLK='#22d3ee'; C_CW='#38bdf8'; C_CHIP='#f1f5f9'; C_NEU='#e2e8f0'; C_DBG='#facc15'
C_ALIVE='#a3e635'; C_SPARE='#fb923c'; C_MUT='#94a3b8'

def E(cat, color, name, role, desc='', routing='', jcard=None, label=1):
    e = dict(cat=cat, color=color, name=name, role=role)
    if desc: e['desc'] = desc
    if routing: e['routing'] = routing
    if jcard: e['jcard'] = jcard
    if label: e['label'] = 1
    return e

DB = {
 'U1': E('chip', C_CHIP, 'PROACT ASIC — DIP-28 socket', 'The target device. Pin 1 top-left with the package notch up.',
        '<p>The PROACT secure ASIC sits in this socket. The 0.8 V core enters on pins <b>15</b> and <b>28</b> through the '
        '<span class="ck" data-comp="R7">R7</span> shunt; the I/O ring runs from 3.3 V <b>VDDIO</b> (pins 7 / 22).</p>'
        '<div class="warn">Pin <b>15</b> is the VDD 0.8 V core supply and pin <b>20</b> is the SPI reset '
        '<code>spi_c_RST_N</code> — double-check both against the pinout tab before wiring anything.</div>',
        '<button class="lk" data-tab="pinout">Open the full DIP-28 pinout →</button>'),
 'J1': E('connector', C_DBG, 'SPI_DBG — debug-signal monitor (2×5)', 'Live debug signals on odd pins; even pins are the common X1 read-back line.',
        '<p>Each <b>odd</b> pin is a live PROACT debug signal; the adjacent <b>even</b> pin is the common <code>X1</code> line. '
        'Fit a jumper across a row to route that signal onto <code>X1</code>, which the MCP2210 reads via <b>GPIO7</b> '
        '(enable <b>S1-2</b>). You can equally probe the odd pins directly.</p>'
        '<table class="t"><tr><th>J1 pin</th><th>Signal</th><th>Chip pin</th><th>LED</th></tr>'
        '<tr><td>9</td><td><code>out_pins[0]</code></td><td>6</td><td class="ck" data-comp="D2">D2</td></tr>'
        '<tr><td>7</td><td><code>spare_io</code></td><td>10</td><td class="ck" data-comp="D3">D3</td></tr>'
        '<tr><td>5</td><td><code>out_pins[5]</code></td><td>11</td><td class="ck" data-comp="D4">D4</td></tr>'
        '<tr><td>3</td><td><code>out_pins[6]</code></td><td>12</td><td class="ck" data-comp="D5">D5</td></tr>'
        '<tr><td>1</td><td><code>out_pins[11]</code></td><td>21</td><td class="ck" data-comp="D6">D6</td></tr>'
        '<tr><td>2,4,6,8,10</td><td><code>X1</code> common</td><td>—</td><td>—</td></tr></table>'),
 'J2': E('connector', C_UART, 'MCP2200 module socket — row A (1×7)', 'Half of the USB-UART bridge socket; carries the module TX / RX.',
        '<p>Together with <span class="ck" data-comp="J3">J3</span> this 1×7 pair hosts the <b>MCP2200</b> USB↔UART module. '
        'Module pin 6 (<b>TX</b>) goes to JP5.3, pin 7 (<b>RX</b>) to JP3.3.</p>'),
 'J3': E('connector', C_UART, 'MCP2200 module socket — row B (1×7)', 'Second row of the MCP2200 socket: module VDD and GND.',
        '<p>Pin 1 is the module <b>VDD</b> — powered from 3.3 V when <b>S1-6</b> is on (decoupled by C3/C4); pin 2 is GND.</p>'),
 'J4': E('connector', C_SPI, 'MCP2210 module socket — row A (1×7)', 'GPIO0–GPIO4, MOSI and SCK of the USB-SPI bridge.',
        '<p>Together with <span class="ck" data-comp="J5">J5</span> this pair hosts the <b>MCP2210</b> USB↔SPI+GPIO module. '
        'Row A = module pins 1–7: GPIO0 (read-back, S1-4) · GPIO1 → <code>spi_c_RST_N</code> · GPIO2 → <code>spi_global_RST_N</code> · '
        'GPIO3 (read-back, S1-1) · GPIO4 → S-Sel source (J6 1-3) · MOSI → JP6.3 · SCK → JP4.3.</p>'
        '<button class="lk" data-tab="conn">Full module pin table →</button>'),
 'J5': E('connector', C_SPI, 'MCP2210 module socket — row B (1×7)', 'VDD, GND, GPIO5–GPIO8 and MISO of the USB-SPI bridge.',
        '<p>Row B = module pins 14…8: VDD (powered when <b>S1-7</b> on, C1/C2) · GND · GPIO8 (read-back, S1-3) · GPIO7 (X1 read-back, S1-2) · '
        'GPIO6 (read-back, S1-5) · GPIO5 → <code>C_RST_N</code> · MISO (not connected).</p>'
        '<button class="lk" data-tab="conn">Full module pin table →</button>'),
 'J6': E('jumper', C_SPI, 'S-Sel &amp; trigger-in routing (2×3)', 'Chooses who drives SSel_n (pin 18); can route CW GPIO3 to trigger_in (pin 23).',
        '<table class="t"><tr><th>Link</th><th>Effect</th></tr>'
        '<tr><td><b>1-3</b></td><td>MCP2210 GPIO4 drives <code>SSel_n</code> (pin 18) — select from the bridge module</td></tr>'
        '<tr><td><b>3-5</b></td><td>CW308 GPIO3 (J7.9) drives <code>SSel_n</code> (pin 18)</td></tr>'
        '<tr><td><b>5-6</b></td><td>CW308 GPIO3 drives <code>trigger_in</code> (pin 23)</td></tr></table>'
        '<p class="mut">Pins 2 and 4 are not connected. 5-6 is combinable with 1-3, not with 3-5 (pin 5 shared).</p>', jcard='J6'),
 'J7': E('connector', C_CW, 'CW308 West edge connector (1×20)', 'Clock · UART · SPI · GPIO · nRST from the ChipWhisperer.',
        '<p>Key pins: 3 <code>CLKFB</code> — ChipWhisperer <b>clock in</b> (selected by J12 5-6, via R8) · 5 <code>CLKIN</code> — chip <b>clock echo out</b> '
        'to the ChipWhisperer (always connected) · 7/8 <code>TIO1/TIO2</code> UART · 9 GPIO3 · 10 GPIO4/TRIG · 11 nRST · 12/14 SCK/MOSI · 20 VREF = VDDIO.</p>'
        '<button class="lk" data-tab="conn">Full pin table →</button>'),
 'J8': E('connector', C_PWR, 'CW308 East edge connector (1×20)', 'Power rails · shunt sense · filter loop · status LEDs.',
        '<p>Key pins: 2 <code>Vcore</code> SHUNT-L · 3 SHUNT-H · 5/6 <code>Vcore_back</code> (filter return) · 8 FILTIN (0.8 V out to filter) · '
        '14 VDDIO 3.3 V in · 18/19/20 reset nets → CW308 status LEDs.</p>'
        '<button class="lk" data-tab="conn">Full pin table →</button>'),
 'J9': E('connector', C_CW, 'CW308 South edge connector (1×20)', 'Debug taps · SPI · nRST.',
        '<p>Carries the debug outputs (J9.11/17/18/20), <code>spare_io</code> (J9.19), SPI taps (J9.13/14), nRST (J9.15) and VDDIO (J9.12).</p>'
        '<button class="lk" data-tab="conn">Full pin table →</button>'),
 'J10': E('jumper', C_TRG, 'Trigger select (2×3)', 'Pin 4 is the CW308 TRIG line — jumper it to one PROACT trigger source.',
        '<table class="t"><tr><th>Link</th><th>Trigger source → CW308 TRIG</th></tr>'
        '<tr><td><b>2-4</b></td><td><code>trigger_Out</code> — normal trigger (pin 14)</td></tr>'
        '<tr><td><b>3-4</b></td><td><code>out_pins[1]</code> — config trigger (pin 13)</td></tr>'
        '<tr><td><b>4-6</b></td><td><code>out_pins[8]</code> — reserve / software trigger (pin 27)</td></tr></table>'
        '<p class="mut">Pins 1 and 5 are not connected — fit exactly one link.</p>', jcard='J10'),
 'J11': E('connector', C_CLK, 'External clock input — coaxial jack', 'Feeds an external bench clock into the J12 selector (link 1-2).',
        '<p class="note">The fitted connector is <b>SMA</b> — the board silk reads “BNC”.</p>'),
 'J12': E('jumper', C_CLK, 'Clock source select (2×3)', 'Three sources, exactly ONE link fitted: 1-2 SMA · 3-4 Y1 50 MHz · 5-6 ChipWhisperer.',
        '<table class="t"><tr><th>Link</th><th>Clock source</th></tr>'
        '<tr><td><b>1-2</b></td><td>External <b>SMA J11</b> drives the chip clock</td></tr>'
        '<tr><td><b>3-4</b></td><td>On-board <b>Y1 50 MHz</b> (via R22 20 Ω) drives the chip clock</td></tr>'
        '<tr><td><b>5-6</b></td><td><b>ChipWhisperer clock</b> — arrives on J7.3 (the CW308 <code>CLKFB</code> line) through R8 100 Ω</td></tr></table>'
        '<p class="mut">Radio behaviour — fit exactly <b>one</b> link. J7.5 is <i>not</i> selectable here: it always carries the chip clock out to the ChipWhisperer.</p>', jcard='J12'),
 'JP1': E('jumper', C_RST, 'Reset link to CW308 (1×2)', 'Close to tie B_RST_N (pin 1) to the CW308 nRST line.',
        '<p>Closed: the ChipWhisperer (J7.11 / J9.15) can reset the chip — SW1 still works. Open: reset only from '
        '<span class="ck" data-comp="SW1">SW1</span>; R9 10 k holds the line high.</p>', jcard='JP1'),
 'JP2': E('jumper', C_NEU, 'IBEX PC probe header (1×3)', 'out_pins[2]/[3]/[4] — probe only, no jumper fitted.',
        '<p>Pins 1/2/3 = <code>out_pins[2]/[3]/[4]</code> (chip pins 26/25/24) — bits 2–4 of the <b>IBEX</b> RISC-V program counter. '
        'The index order is reversed with respect to the pin order, matching the silk <code>out[2:4]</code>.</p>'
        '<div class="warn">Do not fit a shunt — it would short two chip outputs together.</div>', jcard='JP2'),
 'JP3': E('jumper', C_UART, 'UART TX route (1×3)', 'Centre = chip TX (pin 2). 1-2 → CW308 J7.8 · 2-3 → MCP2200 RX.',
        '<table class="t"><tr><th>Link</th><th>Route</th></tr>'
        '<tr><td><b>1-2</b> CW</td><td>chip TX → CW308 <code>TIO2</code> (J7.8)</td></tr>'
        '<tr><td><b>2-3</b> M</td><td>chip TX → MCP2200 <code>RX</code></td></tr></table>'
        '<p class="mut">Move JP3 and <span class="ck" data-comp="JP5">JP5</span> together — always the same side.</p>', jcard='JP3_JP5'),
 'JP5': E('jumper', C_UART, 'UART RX route (1×3)', 'Centre = chip RX (pin 3). 1-2 → CW308 J7.7 · 2-3 → MCP2200 TX.',
        '<table class="t"><tr><th>Link</th><th>Route</th></tr>'
        '<tr><td><b>1-2</b> CW</td><td>CW308 <code>TIO1</code> (J7.7) → chip RX</td></tr>'
        '<tr><td><b>2-3</b> M</td><td>MCP2200 <code>TX</code> → chip RX</td></tr></table>'
        '<p class="mut">Move <span class="ck" data-comp="JP3">JP3</span> and JP5 together — always the same side.</p>', jcard='JP3_JP5'),
 'JP4': E('jumper', C_SPI, 'SPI clock route (1×3)', 'Centre = chip sck (pin 19). 1-2 → CW308 J7.12 · 2-3 → MCP2210 SCK.',
        '<table class="t"><tr><th>Link</th><th>Route</th></tr>'
        '<tr><td><b>1-2</b> CW</td><td>CW308 SCK (J7.12) → chip <code>sck</code></td></tr>'
        '<tr><td><b>2-3</b> M</td><td>MCP2210 SCK → chip <code>sck</code></td></tr></table>'
        '<p class="mut">Move JP4 and <span class="ck" data-comp="JP6">JP6</span> together · the S-Sel driver is chosen on <span class="ck" data-comp="J6">J6</span>.</p>', jcard='JP4_JP6'),
 'JP6': E('jumper', C_SPI, 'SPI MOSI route (1×3)', 'Centre = chip SIn (pin 17). 1-2 → CW308 J7.14 · 2-3 → MCP2210 MOSI.',
        '<table class="t"><tr><th>Link</th><th>Route</th></tr>'
        '<tr><td><b>1-2</b> CW</td><td>CW308 MOSI (J7.14) → chip <code>SIn</code></td></tr>'
        '<tr><td><b>2-3</b> M</td><td>MCP2210 MOSI → chip <code>SIn</code></td></tr></table>'
        '<p class="mut">Move <span class="ck" data-comp="JP4">JP4</span> and JP6 together · the S-Sel driver is chosen on <span class="ck" data-comp="J6">J6</span>.</p>', jcard='JP4_JP6'),
 'JP7': E('jumper', C_PWR, 'Vcore route (1×3)', '1-2 = core rail through the CW308 L-C filter · 2-3 = direct from the LDO.',
        '<table class="t"><tr><th>Link</th><th>Route</th><th>When</th></tr>'
        '<tr><td><b>1-2</b></td><td>0.8 V → J8.8 <code>FILTIN</code> → CW308 L-C filter → back on J8.5/6 → shunt</td><td>Side-channel capture on the CW308</td></tr>'
        '<tr><td><b>2-3</b></td><td>0.8 V → shunt, direct</td><td>Bench use, shortest supply path</td></tr></table>'
        '<div class="warn">Set the rail to <b>0.800 V</b> with R20 <b>before inserting the chip</b> — see the Power tab.</div>', jcard='JP7'),
 'S1': E('switch', C_NEU, '7-position DIP switch', 'Switches 1–5: GPIO read-back loops · 6–7: power the USB bridge modules.',
        '<p>Switches <b>1–5</b> enable GPIO read-back loops (the USB host can read the state of a signal it — or the chip — is driving); '
        '<b>6–7</b> power the two USB bridge modules.</p><button class="lk" data-tab="s1">Open the S1 explorer →</button>'),
 'SW1': E('switch', C_RST, 'Reset push-button (B_RST)', 'Pulls B_RST_N (pin 1) low; red LED D7 lights while pressed.',
        '<p>Tactile push-button to ground on the chip reset. <span class="ck" data-comp="R9">R9</span> 10 k holds the line high; '
        '<span class="ck" data-comp="JP1">JP1</span> optionally ties the same line to the CW308 nRST.</p>'
        '<p class="note">Part number to be confirmed before ordering (5×5 SMD).</p>'),
 'U2': E('power', C_PWR, 'TPS74801 — adjustable 1.5 A LDO', 'Generates the trimmable 0.80–0.90 V core rail from VDDIO.',
        '<p><b>V<sub>core</sub> = 0.8 V × (1 + R20 / 8.2 kΩ)</b> — trimmed with multi-turn pot <span class="ck" data-comp="R20">R20</span> '
        'against <span class="ck" data-comp="R21">R21</span>. Soft-start C18; output decoupling C11/C12; the rail then goes through '
        '<span class="ck" data-comp="JP7">JP7</span> and the <span class="ck" data-comp="R7">R7</span> shunt to chip pins 15/28.</p>'
        '<div class="warn">Set <b>0.800 V</b> at the Vcore test point <b>before inserting the chip</b> — full procedure on the Power tab.</div>'),
 'Y1': E('clock', C_CLK, '50 MHz clock oscillator (3225)', 'On-board clock source — selected with J12 link 3-4, via R22 20 Ω.',
        '<p class="note">Fit an <b>active 3.3 V oscillator</b> — verify the part number before ordering. Decoupled by C13 (1 µF) and C16 (100 nF).</p>'),
 'R7': E('power', C_PWR, 'Current-sense shunt — 0.01 Ω (1206)', 'The measurement heart: all core current flows through it.',
        '<p>1 mA of core current = 10 µV across the shunt. The CW308 measures the drop (J8.2/J8.3) to capture the power trace; two probe '
        'holes take a differential probe directly. The die side (net <code>Vcore</code>, pins 15/28) is deliberately capacitor-free — '
        'decoupling sits before the shunt (C5/C10) so the instantaneous die current appears in the trace.</p>'),
 'R20': E('power', C_PWR, 'Vcore trim potentiometer — 1 k multi-turn', 'Sets the core rail: 0.8 V × (1 + R20/8.2 k) → 0.80–0.90 V.',
        '<p>Turn until the <b>Vcore test point</b> reads <b>0.800 V</b> — with the chip OUT of the socket. Full procedure on the Power tab.</p>'),
 'R21': E('power', C_PWR, 'LDO feedback divider — 8.2 kΩ', 'Bottom leg of the U2 feedback divider (with trimmer R20).'),
 'R22': E('clock', C_CLK, 'Oscillator series resistor — 20 Ω', 'Series damping between Y1 and J12 pin 3.'),
 'R8': E('clock', C_CLK, 'CW-clock series resistor — 100 Ω', 'J7.3 (the CW308 CLKFB line) → R8 → J12.5 — the ChipWhisperer clock into the J12 selector (link 5-6).'),
 'R9': E('passive', C_RST, 'Pull-up on B_RST_N — 10 kΩ', 'Holds the chip reset (pin 1) high to VDDIO; SW1 pulls it low.'),
 'C18': E('power', C_PWR, 'LDO soft-start capacitor — 10 nF', 'TPS74801 soft-start (U2 pin 7).'),
 'CLK': E('testpoint', C_CLK, 'CLK test point', 'Directly on the chip clock net (pin 9) — observe the selected clock.'),
 'GND': E('testpoint', C_MUT, 'GND test point', 'Scope / meter reference.'),
 'VDDIO': E('testpoint', C_PWR, 'VDDIO test point', 'Probe the 3.3 V I/O rail — or feed 3.3 V here for USB-only bench use off the CW308.'),
 'Vcore': E('testpoint', C_PWR, 'Vcore test point', 'Trim target for the 0.8 V procedure. High-impedance probing only — this node is deliberately capacitor-free.'),
}
# read-back series resistors
for r, (sw, sig) in {'R1': (3, 'spi_global_RST_N (GRST)'), 'R2': (4, 'spi_c_RST_N (SPI_RST)'),
                     'R3': (1, 'SSel_n (GPIO4)'), 'R4': (2, 'X1 debug bus'), 'R5': (5, 'C_RST_N (GPIO6 leg)')}.items():
    DB[r] = E('passive', C_NEU, 'Read-back series resistor — 10 kΩ',
              f'S1-{sw} loop: lets the MCP2210 read back {sig}.',
              f'<p>Part of the <b>S1-{sw}</b> read-back loop.</p><button class="lk" data-tab="s1" data-s1="{sw}">Open in the S1 explorer →</button>', label=0)
# LED resistors
for r, (led, val, sig) in {'R10': ('D1', '560 Ω', 'out_pins[7] alive'), 'R11': ('D2', '560 Ω', 'out_pins[0]'),
                           'R12': ('D3', '560 Ω', 'spare_io'), 'R13': ('D4', '560 Ω', 'out_pins[5]'),
                           'R14': ('D5', '560 Ω', 'out_pins[6]'), 'R15': ('D6', '560 Ω', 'out_pins[11]'),
                           'R6': ('D7', '100 Ω', 'B_RST_N'), 'R16': ('D8', '100 Ω', 'C_RST_N'),
                           'R17': ('D9', '100 Ω', 'spi_global_RST_N'), 'R18': ('D10', '100 Ω', 'spi_c_RST_N')}.items():
    DB[r] = E('passive', C_NEU, f'LED resistor — {val}',
              f'Series resistor for <span class="ck" data-comp="{led}">{led}</span> ({sig}).', label=0)
# LEDs
DB['D1'] = E('led', C_ALIVE, 'Alive LED — yellow-green', 'out_pins[7] heartbeat (pin 16) — blinking = chip alive.',
             '<p>Lights when the heartbeat output is high; wired signal → LED → R10 560 Ω → GND.</p>')
for led, (sig, pin, res, j1, j9) in {'D2': ('out_pins[0]', 6, 'R11', 'J1.9', 'J9.20'), 'D4': ('out_pins[5]', 11, 'R13', 'J1.5', 'J9.18'),
                                     'D5': ('out_pins[6]', 12, 'R14', 'J1.3', 'J9.17'), 'D6': ('out_pins[11]', 21, 'R15', 'J1.1', 'J9.11')}.items():
    DB[led] = E('led', C_DBG, 'Debug LED — yellow', f'<code>{sig}</code> (pin {pin}) — lights when the output is high.',
                f'<p>The same signal appears on header {j1} and the CW308 south connector {j9}, and can be read back through S1-2 via J1.</p>')
DB['D3'] = E('led', C_SPARE, 'Spare-input LED — orange', 'spare_io (pin 10) — lights when YOU drive the input high.',
             '<p><code>spare_io</code> is an input: the chip never lights this LED. Also on J1.7 and J9.19.</p>')
for led, (sig, pin) in {'D7': ('B_RST_N', 1), 'D8': ('C_RST_N', 5), 'D9': ('spi_global_RST_N', 4), 'D10': ('spi_c_RST_N', 20)}.items():
    DB[led] = E('led', C_RST, 'Reset LED — red', f'<code>{sig}</code> (pin {pin}) — lights while the reset is asserted (line low).',
                '<p>Wired from VDDIO through the LED into the reset line, so a low (active) reset lights it. '
                'All four red LEDs on at once is normal during reset — it is not a fault.</p>')
# capacitors
for c, (val, role) in {'C1': ('10 µF', 'MCP2210 module VDD decoupling (with C2, via S1-7)'),
                       'C2': ('100 nF', 'MCP2210 module VDD decoupling (with C1, via S1-7)'),
                       'C3': ('10 µF', 'MCP2200 module VDD decoupling (with C4, via S1-6)'),
                       'C4': ('100 nF', 'MCP2200 module VDD decoupling (with C3, via S1-6)'),
                       'C5': ('100 nF', 'Shunt supply-side decoupling (with C10) — before R7, so die current shows in the trace'),
                       'C10': ('10 µF', 'Shunt supply-side decoupling (with C5) — before R7, so die current shows in the trace'),
                       'C6': ('10 µF', 'VDDIO rail decoupling (with C7)'), 'C7': ('100 nF', 'VDDIO rail decoupling (with C6)'),
                       'C8': ('10 µF', 'VDDIO rail decoupling (with C9)'), 'C9': ('100 nF', 'VDDIO rail decoupling (with C8)'),
                       'C11': ('10 µF', 'LDO output decoupling on the 0.8 V rail (with C12)'),
                       'C12': ('100 nF', 'LDO output decoupling on the 0.8 V rail (with C11)'),
                       'C13': ('1 µF', 'Y1 oscillator decoupling (with C16)'), 'C16': ('100 nF', 'Y1 oscillator decoupling (with C13)'),
                       'C14': ('10 µF', 'LDO input decoupling, VDDIO side (with C15)'),
                       'C15': ('100 nF', 'LDO input decoupling, VDDIO side (with C14)')}.items():
    DB[c] = E('passive', C_NEU, f'Capacitor — {val}', role + '.', label=0)

# ------------------------------------------------------------------- S1 data --
S1DATA = [
 dict(n=1, kind='read', title='Read back SSel_n', pins='S1.1 ↔ S1.14',
      purpose='MCP2210 GPIO3 reads GPIO4 — the USB host can verify the SPI-select state it drives.',
      path='MCP2210 GPIO4 (J4.5) → R3 10 k → S1-1 → MCP2210 GPIO3 (J4.4)', loop=['J4', 'R3', 'S1', 'J6']),
 dict(n=2, kind='read', title='Read back the J1 debug signal', pins='S1.2 ↔ S1.13',
      purpose='MCP2210 GPIO7 reads X1 — whichever debug signal is jumpered on J1.',
      path='J1 X1 bus → R4 10 k → S1-2 → MCP2210 GPIO7 (J5.4)', loop=['J1', 'R4', 'S1', 'J5']),
 dict(n=3, kind='read', title='Read back the global reset', pins='S1.3 ↔ S1.12',
      purpose='MCP2210 GPIO8 reads GPIO2 (spi_global_RST_N, board net GRST).',
      path='GRST (J4.3 / chip pin 4) → R1 10 k → S1-3 → MCP2210 GPIO8 (J5.3)', loop=['J4', 'R1', 'S1', 'J5', 'U1']),
 dict(n=4, kind='read', title='Read back the SPI reset', pins='S1.4 ↔ S1.11',
      purpose='MCP2210 GPIO0 reads GPIO1 (spi_c_RST_N, board net SPI_RST).',
      path='SPI_RST (J4.2 / chip pin 20) → R2 10 k → S1-4 → MCP2210 GPIO0 (J4.1)', loop=['J4', 'R2', 'S1', 'U1']),
 dict(n=5, kind='read', title='Read back the controller reset', pins='S1.5 ↔ S1.10',
      purpose='MCP2210 GPIO6 reads GPIO5 (C_RST_N, board net CRST).',
      path='CRST (J5.6 / chip pin 5) → S1-5 → R5 10 k → MCP2210 GPIO6 (J5.5)', loop=['J5', 'R5', 'S1', 'U1']),
 dict(n=6, kind='power', title='Power the UART bridge (MCP2200)', pins='S1.6 ↔ S1.9',
      purpose='Connects the MCP2200 module VDD to 3.3 V.',
      path='VDDIO 3.3 V → S1-6 → MCP2200 VDD (J3.1, decoupled by C3/C4)', loop=['J3', 'S1', 'C3', 'C4']),
 dict(n=7, kind='power', title='Power the SPI bridge (MCP2210)', pins='S1.7 ↔ S1.8',
      purpose='Connects the MCP2210 module VDD to 3.3 V.',
      path='VDDIO 3.3 V → S1-7 → MCP2210 VDD (J5.1, decoupled by C1/C2)', loop=['J5', 'S1', 'C1', 'C2']),
]

# -------------------------------------------------------- silk & LED colours --
# Silk text transcribed from the final renders (docs/img/board_final_top/bottom.png).
SILK = {
 'U1': 'U1', 'J11': 'EXT CLK IN', 'J1': 'X1 Dbug', 'JP2': 'PC: 2 3 4',
 'JP3': 'Rx · UART CW/M', 'JP5': 'Tx · UART CW/M',
 'JP4': 'SCK M/CW', 'JP6': 'MOSI CW/M', 'JP1': 'B_RST→CW',
 'JP7': 'Vcore select · direct / filt',
 'J6': 'S-SEL / TRIG-IN (spi s_sel · pin18 · Pin23)',
 'J10': 'Trig select — Pin13 (trig cfg) · (Trig norm) Pin14 / io4 Cw / Pin27 · (Trig rsv)',
 'J12': 'Clk select — SMA / Osc / Cw',
 'R20': 'Vadj SET 0.8V', 'R7': '0.01Ω shunt', 'SW1': 'B RST',
 'D1': 'Alive?', 'D2': 'Mem[23]', 'D3': 'Spare In', 'D4': 'UART Rvalid',
 'D5': 'Mem req', 'D6': 'Co req',
 'D7': 'B RST', 'D8': 'C RST', 'D9': 'G RST', 'D10': 'SPI RST',
 'S1': 'ON + per-switch labels (SPI_Select read · Dbug · GRST read · SPI_RST read · CRST read · UART VDD Connect · SPI VDD Connect)',
 'J2': 'MCP2200 UART', 'J3': 'MCP2200 UART', 'J4': 'MCP2210 SPI', 'J5': 'MCP2210 SPI',
 'CLK': 'CLK', 'GND': 'GND', 'Vcore': 'Vcore', 'VDDIO': 'VDDIO', 'Y1': 'Y1',
 'J7': 'J7', 'J8': 'J8', 'J9': 'J9 · co-req/mem-req/uart-va/spare-in/Mem[23]',
}
# Mounted LED colours (BOM). The CAD render shows placeholder LED bodies.
LEDCOLOR = {'D1': ('yellow-green', '#a3e635'), 'D2': ('yellow', '#facc15'),
            'D3': ('orange', '#fb923c'), 'D4': ('yellow', '#facc15'),
            'D5': ('yellow', '#facc15'), 'D6': ('yellow', '#facc15'),
            'D7': ('red', '#f87171'), 'D8': ('red', '#f87171'),
            'D9': ('red', '#f87171'), 'D10': ('red', '#f87171')}

# --------------------------------------------------------------- image assets --
def svg_inline(path, ns):
    s = open(path, encoding='utf-8').read()
    s = s.replace('id="ar"', f'id="ar_{ns}"').replace('url(#ar)', f'url(#ar_{ns})')
    return s

def jpg_uri(path):
    return 'data:image/jpeg;base64,' + base64.b64encode(open(path, 'rb').read()).decode()

def logo_uri():
    try:
        from PIL import Image
        im = Image.open(f'{ROOT}/logo.png')
        im.thumbnail((256, 96), Image.LANCZOS)
        buf = io.BytesIO(); im.save(buf, 'PNG', optimize=True)
        return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None

JCARDS = {}
for k in ['overview', 'JP1', 'JP2', 'JP3_JP5', 'JP4_JP6', 'JP7', 'J6', 'J10', 'J12']:
    JCARDS[k] = open(f'{IMG}/jumpers/{k}.svg', encoding='utf-8').read()

def scrub_versions(s):
    """Remove board-version references from diagram text (page policy: the wiki
    describes THE board — no revision history). Exact-string, text-node-only
    replacements; the SVG sources on disk are left untouched."""
    for old, new in [
        ('PROACT Evaluation Board v2 — System Architecture',
         'PROACT Evaluation Board — System Architecture'),
        ('PROACT 22FDX — DIP-28 Pinout (v2)',
         'PROACT 22FDX — DIP-28 Pinout'),
        ('★ = pins 15/20 swapped vs v1 (Vcore ↔ SPI reset)',
         '★ = double-check pins 15/20 (Vcore &amp; SPI reset)'),
        ('★ Vcore (was SPI_RST in v1) · via R7 shunt',
         '★ Vcore (0.8 V core supply) · via R7 shunt'),
        ('★ SPI ctrl reset (board SPI_RST, was Vcore in v1) · GPIO1 · LED D10',
         '★ SPI ctrl reset (board silk SPI RST) · GPIO1 · LED D10'),
    ]:
        s = s.replace(old, new)
    return s

ARCH = scrub_versions(svg_inline(f'{IMG}/architecture.svg', 'arch'))
CLOCK = svg_inline(f'{IMG}/clock_tree.svg', 'clk')
POWER = svg_inline(f'{IMG}/power_path.svg', 'pwr')
PINOUT_SVG = scrub_versions(open(f'{IMG}/proact_pinout_v2.svg', encoding='utf-8').read())
PHOTO_TOP = jpg_uri(f'{IMG}/board_v3_top.jpg')
PHOTO_BOT = jpg_uri(f'{IMG}/board_v3_bottom.jpg')
LOGO = logo_uri()

# Final board renders (full silkscreen) for the interactive board map.
# mm→px calibration: detect the green board region's bbox in each render and
# affine-map the Board6 outline bbox onto it. TOP: Y flips (mm Y up, px Y down).
# BOTTOM: viewed from below — X mirrors as well (verified against bottom silk).
def green_bbox(im):
    from PIL import ImageChops
    r, g, b = im.convert('RGB').split()
    m1 = ImageChops.subtract(g, r).point(lambda v: 255 if v >= 20 else 0)
    m2 = ImageChops.subtract(g, b).point(lambda v: 255 if v >= 10 else 0)
    m3 = g.point(lambda v: 255 if v > 60 else 0)
    mask = ImageChops.multiply(ImageChops.multiply(m1, m2), m3)
    x0, y0, x1, y1 = mask.getbbox()          # x1/y1 exclusive
    return x0, y0, x1 - 1, y1 - 1

def render_view(path, mirror):
    from PIL import Image
    im = Image.open(path)
    gx0, gy0, gx1, gy1 = green_bbox(im)
    uri = 'data:image/png;base64,' + base64.b64encode(open(path, 'rb').read()).decode()
    return dict(img=uri, iw=im.size[0], ih=im.size[1],
                gx0=gx0, gy0=gy0, mir=1 if mirror else 0,
                sx=round((gx1 - gx0) / (BOARD['x1'] - BOARD['x0']), 4),
                sy=round((gy1 - gy0) / (BOARD['y1'] - BOARD['y0']), 4))

VIEWS = {'T': render_view(f'{IMG}/board_final_top.png', mirror=False),
         'B': render_view(f'{IMG}/board_final_bottom.png', mirror=True)}
for k, v in VIEWS.items():
    print(f'view {k}: green origin ({v["gx0"]},{v["gy0"]}) scale ({v["sx"]},{v["sy"]}) px/mm mirror={v["mir"]}')

# ---------------------------------------------------------------- data blob ---
comps_json = {}
missing_db = []
for c in COMPS:
    ref = c['des']
    if not ref: continue
    w, h = SIZE.get(c['lib'], (2, 2))
    e = DB.get(ref)
    if e is None:
        missing_db.append(ref)
        e = E('passive', C_MUT, c['lib'] or 'component', '', label=0)
    comps_json[ref] = dict(x=round(c['x'], 3), y=round(c['y'], 3), rot=c['rot'], w=w, h=h,
                           layer=c['layer'], pads=PADS.get(ref, {}), **e)
    if ref in SILK: comps_json[ref]['silk'] = SILK[ref]
    if ref in LEDCOLOR: comps_json[ref]['led'] = list(LEDCOLOR[ref])

DATA = dict(board=BOARD, comps=comps_json, nets=NETS, jcards=JCARDS, s1=S1DATA, views=VIEWS)
data_json = json.dumps(DATA, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')

# ------------------------------------------------------------ static sections --
PINOUT_ROWS = [
 (1, 'B_RST_N', 'IN·PU', 'Button / external reset (active low)', 'SW1 · R9 10 k pull-up · JP1 → CW308 nRST · red LED D7'),
 (2, 'TX', 'OUT', 'UART transmit', 'JP3 → MCP2200 RX or CW308 (J7.8)'),
 (3, 'RX', 'IN', 'UART receive', 'JP5 → MCP2200 TX or CW308 (J7.7)'),
 (4, 'spi_global_RST_N', 'IN·PU', 'Global reset (board net GRST)', 'MCP2210 GPIO2 · CW308 LED3 · red LED D9'),
 (5, 'C_RST_N', 'IN·PU', 'Controller reset (board net CRST)', 'MCP2210 GPIO5 · CW308 LED2 · red LED D8'),
 (6, 'out_pins[0]', 'OUT', 'Debug output', 'yellow LED D2 · J1.9 · J9.20'),
 (7, 'VDDIO', 'PWR', 'I/O supply', '3.3 V'),
 (8, 'VSS', 'GND', 'Ground', '—'),
 (9, 'SYSCLK_P', 'IN', 'Chip clock', 'from J12 clock select · CLK test point · echoed out on J7.5'),
 (10, 'spare_io', 'IN', 'Spare input (user-driven)', 'orange LED D3 · J1.7 · J9.19'),
 (11, 'out_pins[5]', 'OUT', 'Debug output', 'yellow LED D4 · J1.5 · J9.18'),
 (12, 'out_pins[6]', 'OUT', 'Debug output', 'yellow LED D5 · J1.3 · J9.17'),
 (13, 'out_pins[1]', 'OUT', 'Trigger (config)', 'J10 (3-4) → CW308 TRIG'),
 (14, 'trigger_Out', 'OUT', 'Trigger (normal)', 'J10 (2-4) → CW308 TRIG'),
 (15, 'VDD 0.8 V core', 'PWR', 'Core supply', 'via R7 shunt (with pin 28)'),
 (16, 'out_pins[7]', 'OUT', 'Alive heartbeat', 'yellow-green LED D1 (blinks = alive)'),
 (17, 'SIn', 'IN', 'SPI data in (MOSI)', 'JP6 → MCP2210 or CW308'),
 (18, 'SSel_n', 'IN', 'SPI select (active low)', 'J6 → MCP2210 GPIO4 or CW308 GPIO3'),
 (19, 'sck', 'IN', 'SPI clock', 'JP4 → MCP2210 or CW308'),
 (20, 'spi_c_RST_N', 'IN·PU', 'SPI-controller reset (board net SPI_RST)', 'MCP2210 GPIO1 · CW308 LED1 · red LED D10'),
 (21, 'out_pins[11]', 'OUT', 'Debug output', 'yellow LED D6 · J1.1 · J9.11'),
 (22, 'VDDIO', 'PWR', 'I/O supply', '3.3 V'),
 (23, 'trigger_in', 'IN', 'Trigger input', 'J6.6 ← CW308 GPIO3 (link J6 5-6)'),
 (24, 'out_pins[4]', 'OUT', 'IBEX PC probe (bit 4)', 'JP2.3'),
 (25, 'out_pins[3]', 'OUT', 'IBEX PC probe (bit 3)', 'JP2.2'),
 (26, 'out_pins[2]', 'OUT', 'IBEX PC probe (bit 2)', 'JP2.1'),
 (27, 'out_pins[8]', 'OUT', 'Trigger (reserve / software)', 'J10 (4-6) → CW308 TRIG'),
 (28, 'VDD 0.8 V core', 'PWR', 'Core supply', 'via R7 shunt (with pin 15)'),
]
pinout_html = ['<table class="t pins"><tr><th>Pin</th><th>Signal</th><th>Dir</th><th>Function</th><th>Routing / destination</th></tr>']
for p, sig, dr, fn, rt in PINOUT_ROWS:
    strong = ' class="hot"' if p in (15, 20) else ''
    pinout_html.append(f'<tr{strong} data-pin="{p}" title="Click to highlight on the board map">'
                       f'<td><b>{p}</b></td><td><code>{sig}</code></td><td>{dr}</td><td>{fn}</td><td>{rt}</td></tr>')
pinout_html.append('</table>')
PINOUT_TABLE = '\n'.join(pinout_html)

BOM_ROWS = [
 ('C1, C3, C6, C8, C10, C11, C14', '10 µF', 'Capacitor', '1210', 'C77100', ''),
 ('C2, C4, C5, C7, C9, C12, C15, C16', '100 nF', 'Capacitor', '0805', 'C1711', ''),
 ('C13', '1 µF', 'Capacitor (Y1 decoupling)', '0805', 'C1848', ''),
 ('C18', '10 nF', 'Capacitor (LDO soft-start)', '0805', 'C1710', ''),
 ('D1', 'yellow-green', 'LED — alive heartbeat', '0805', 'C84257', ''),
 ('D2, D4, D5, D6', 'yellow', 'LED — debug outputs', '0805', 'C84261', ''),
 ('D3', 'orange', 'LED — spare_io', '0805', 'C84262', ''),
 ('D7–D10', 'red', 'LED — resets', '0805', 'C84256', ''),
 ('J1', '—', 'Header, male 2×5', '2.54 mm', 'C492422', ''),
 ('J2, J3, J4, J5', '—', 'Header, male 1×7', '2.54 mm', 'C124418', ''),
 ('J6, J10, J12', '—', 'Header, female 2×3', '2.54 mm', 'C65114', ''),
 ('J7, J8, J9', '—', 'Header, female 1×20', '2.54 mm', 'C7434502', ''),
 ('J11', '—', 'Coaxial jack, ext. clock', '—', 'C20415804', 'SMA jack; board silk reads “BNC”'),
 ('JP1', '—', 'Jumper header 2-pin', '2.54 mm', 'C86471', ''),
 ('JP2–JP7', '—', 'Jumper header 3-pin', '2.54 mm', 'C49257', ''),
 ('R1–R5, R9', '10 kΩ', 'Resistor', '0805', 'C17414', ''),
 ('R6, R8, R16, R17, R18', '100 Ω', 'Reset LEDs / CW-clock series (R8)', '0805', 'C17408', ''),
 ('R7', '0.01 Ω', 'Current-sense shunt', '1206', 'C105362', ''),
 ('R10–R15', '560 Ω', 'LED resistors', '0805', 'C25319', ''),
 ('R20', '1 kΩ', 'Multi-turn trimmer (Vcore adjust)', '—', 'C57089', ''),
 ('R21', '8.2 kΩ', 'Vcore feedback divider', '0805', 'C17828', ''),
 ('R22', '20 Ω', 'Oscillator series resistor', '0805', 'C17544', ''),
 ('S1', '—', 'DIP switch, 7-position', 'THT', 'C331508', ''),
 ('SW1', '—', 'Tactile push-button (B_RST)', '5×5 SMD', 'TBC', 'part number to be confirmed before ordering'),
 ('U1', '—', 'DIP-28 IC socket (PROACT)', 'DIP-28', 'C72121', ''),
 ('U2', '—', 'TPS74801DRCR 1.5 A adjustable LDO', 'VSON-10', 'C105263', ''),
 ('Y1', '50 MHz', 'Clock oscillator, 3225', '3225', 'TBC', 'fit an active 3.3 V oscillator — verify part number before ordering'),
]
bom_html = ['<table class="t"><tr><th>Ref(s)</th><th>Value</th><th>Description</th><th>Footprint</th><th>JLCPCB</th><th>Note</th></tr>']
for r in BOM_ROWS:
    ref0 = re.split(r'[,–\s]', r[0])[0]
    bom_html.append('<tr><td class="ck" data-comp="%s">%s</td>' % (ref0, r[0]) +
                    ''.join(f'<td>{v}</td>' for v in r[1:]) + '</tr>')
bom_html.append('</table>')
BOM_TABLE = '\n'.join(bom_html)

# assembled in the template below
SECTIONS = {}

SECTIONS['overview'] = '''
<div class="card">
<p class="lead">A ChipWhisperer <b>CW308 UFO</b> target board for the <b>PROACT</b> secure ASIC. The board hosts the
PROACT chip in a DIP-28 socket and lets you drive its UART / SPI / GPIO interfaces either from two on-board
<b>USB bridge modules</b> (MCP2200 for UART, MCP2210 for SPI + GPIO) <b>or</b> from the ChipWhisperer CW308 platform
for power / EM <b>side-channel analysis</b> and fault injection — the routing is selected entirely with jumpers.
The chip clock can come from an <b>on-board 50 MHz oscillator</b>, an <b>external SMA input</b>, or the
<b>ChipWhisperer</b>, and the 0.8 V core rail is <b>trimmable (0.80–0.90 V)</b> and measured through a 0.01 Ω sense shunt.</p>
<table class="t kv">
<tr><td>Target device</td><td>PROACT ASIC, 28-pin DIP (socketed at <span class="ck" data-comp="U1">U1</span>)</td></tr>
<tr><td>Host platform</td><td>ChipWhisperer <b>CW308 UFO</b> (edge connectors <span class="ck" data-comp="J7">J7</span> / <span class="ck" data-comp="J8">J8</span> / <span class="ck" data-comp="J9">J9</span>)</td></tr>
<tr><td>On-board bridges</td><td><b>MCP2200</b> USB↔UART · <b>MCP2210</b> USB↔SPI+GPIO (plug-in modules)</td></tr>
<tr><td>Interfaces</td><td>UART, SPI (<code>SIn</code>/<code>sck</code>/<code>SSel_n</code>), debug outputs, 4 reset lines, 3 trigger outputs + 1 trigger input</td></tr>
<tr><td>Clock</td><td><span class="ck" data-comp="J12">J12</span> source select (exactly one link): on-board <b>Y1 50 MHz</b> · external <b>SMA (J11)</b> · <b>ChipWhisperer</b>; the chip clock is permanently <b>echoed to the ChipWhisperer on J7.5</b> for synchronous sampling</td></tr>
<tr><td>Core supply</td><td><b>TPS74801</b> LDO, <b>trimmable 0.80–0.90 V</b> (R20) — <i>set 0.8 V before inserting the chip</i>; delivered through <b>R7</b> 0.01 Ω sense shunt, optionally via the CW308 L-C filter (<span class="ck" data-comp="JP7">JP7</span>)</td></tr>
<tr><td>I/O supply</td><td><b>VDDIO</b> 3.3 V from the CW308 rail</td></tr>
<tr><td>Live monitoring</td><td><b>10 LEDs in 4 color groups</b> (alive · debug · spare-in · resets) + <span class="ck" data-comp="J1">J1</span> monitor header + CW308 status LEDs</td></tr>
<tr><td>Board size</td><td>53.9 mm × 93.3 mm</td></tr>
</table>
<p>The unifying idea of the board: <b>every PROACT interface signal can be sourced from the USB bridge module
<i>or</i> from the ChipWhisperer</b>, chosen by a 3-pin jumper whose <b>centre pin is always the PROACT chip</b>.
Silkscreen tags such as <code>Mosi M/CW</code> and <code>SCK M/CW</code> spell this out — <b>M</b> = module, <b>CW</b> = ChipWhisperer.</p>
</div>
<div class="card"><h2>System architecture &amp; signal routing</h2>
<div class="dg">@ARCH@</div>
<ul>
<li><b>Two masters, one target.</b> The PROACT chip in the centre can be talked to by the USB bridge modules (left) or by the ChipWhisperer CW308 (right). The purple jumper “muxes” pick which.</li>
<li><b>UART</b> (JP3 / JP5) routes PROACT <code>TX</code> (pin 2) and <code>RX</code> (pin 3) to the MCP2200 module or the CW308 (J7.8 / J7.7).</li>
<li><b>SPI</b> (JP4 = <code>sck</code> pin 19, JP6 = <code>SIn</code> pin 17) and <b>S-Sel</b> (J6 = <code>SSel_n</code> pin 18) route to the MCP2210 module or the CW308.</li>
<li><b>Resets</b> come from MCP2210 GPIO or the button: <code>B_RST_N</code> (pin 1, SW1/JP1), <code>spi_global_RST_N</code> (pin 4, GPIO2), <code>C_RST_N</code> (pin 5, GPIO5), <code>spi_c_RST_N</code> (pin 20, GPIO1). The three GPIO resets also drive the CW308’s status LEDs, and all four light a red LED (D7–D10) while asserted.</li>
<li><b>Debug outputs</b> <code>out_pins[0]/[5]/[6]/[11]</code> (pins 6, 11, 12, 21) light the yellow LEDs D2/D4/D5/D6, appear on header J1 and the CW308 south header, and can be read back into the MCP2210 through the S1 DIP switch. <code>spare_io</code> (pin 10) is a user-drivable input with the orange LED D3.</li>
<li><b>Triggers</b> (<code>trigger_Out</code> 14, <code>out_pins[1]</code> 13, <code>out_pins[8]</code> 27) are selected on J10 and fed to the CW308 TRIG input; <code>trigger_in</code> (pin 23) can be driven from CW308 GPIO3 via J6.</li>
<li><b>Clock</b>: <code>SYSCLK_P</code> (pin 9) is fed from the J12 clock-source select — on-board 50 MHz Y1 (3-4), external SMA J11 (1-2), or the ChipWhisperer clock (5-6, via J7.3/R8) — and is permanently echoed to the ChipWhisperer on J7.5 for synchronous sampling.</li>
<li><b>Power / measurement</b>: CW308 3.3 V → VDDIO; the U2 LDO makes the trimmable 0.8 V core rail, which reaches the chip through JP7 (direct or via the CW308 filter) and the R7 shunt; the CW308 measures the drop across R7 to capture the power trace.</li>
</ul></div>
<div class="card"><h2>Board photos — fully silkscreened</h2>
<div class="grid2 photos">
<figure><img src="@PHOTO_TOP@" alt="PROACT board — top"><figcaption>Top side — full silkscreen</figcaption></figure>
<figure><img src="@PHOTO_BOT@" alt="PROACT board — bottom"><figcaption>Bottom side — PROACT logo &amp; CW308 edge connectors</figcaption></figure>
</div></div>
<div class="card"><h2>Signal names on the silkscreen</h2>
<p>The board silk prints shorthand names next to the LEDs, headers and jumpers; this wiki uses the RTL signal
names from the chip datasheet (<code>out_pins[x]</code>, <code>SYSCLK_P</code>, <code>spare_io</code>,
<code>SIn</code>, <code>sck</code>, <code>SSel_n</code>, …). Every part&rsquo;s panel on the board map shows its
silk label alongside the signal.</p>
<details><summary>Silkscreen name → signal name map</summary>
<table class="t"><tr><th>name on the silk (chip pin)</th><th>signal</th></tr>
<tr><td>G RST (4)</td><td><code>spi_global_RST_N</code></td></tr><tr><td>C RST (5)</td><td><code>C_RST_N</code></td></tr>
<tr><td>B RST (1)</td><td><code>B_RST_N</code></td></tr><tr><td>SPI RST (20)</td><td><code>spi_c_RST_N</code></td></tr>
<tr><td>Mem[23] (6)</td><td><code>out_pins[0]</code></td></tr><tr><td>CLK (9)</td><td><code>SYSCLK_P</code></td></tr>
<tr><td>Spare In (10)</td><td><code>spare_io</code></td></tr><tr><td>UART Rvalid (11)</td><td><code>out_pins[5]</code></td></tr>
<tr><td>Mem req (12)</td><td><code>out_pins[6]</code></td></tr><tr><td>Trig cfg (13)</td><td><code>out_pins[1]</code></td></tr>
<tr><td>Trig norm (14)</td><td><code>trigger_Out</code></td></tr><tr><td>Alive? (16)</td><td><code>out_pins[7]</code></td></tr>
<tr><td>MOSI (17)</td><td><code>SIn</code></td></tr><tr><td>S-SEL (18)</td><td><code>SSel_n</code></td></tr>
<tr><td>SCK (19)</td><td><code>sck</code></td></tr><tr><td>Co req (21)</td><td><code>out_pins[11]</code></td></tr>
<tr><td>TRIG-IN (23)</td><td><code>trigger_in</code></td></tr><tr><td>PC: 2 3 4 (24/25/26)</td><td><code>out_pins[4]/[3]/[2]</code></td></tr>
<tr><td>Trig rsv (27)</td><td><code>out_pins[8]</code></td></tr></table></details>
</div>'''

SECTIONS['pinout'] = '''
<div class="warn big">Pin <b>15</b> is <b>VDD (0.8 V core)</b> and pin <b>20</b> is the SPI reset
<code>spi_c_RST_N</code> — double-check these two pins before wiring anything.</div>
<div class="card"><h2>PROACT chip pinout (DIP-28)</h2>
<p>Socket <span class="ck" data-comp="U1">U1</span>. Pin 1 is top-left with the package notch up. Colours group pins by
function. <code>IN·PU</code> = input with internal pull-up. <b>Click a row</b> to see the pin’s net on the board map.</p>
<div class="dg">@PINOUT_SVG@</div>
@PINOUT_TABLE@
<p class="mut"><code>out_pins[2]/[3]/[4]</code> on JP2 expose bits 2–4 of the program counter of the <b>IBEX</b> RISC-V
soft-core for probing. Note the index order is <b>reversed</b> with respect to the pin order (pin 24 = bit 4 … pin 26 = bit 2),
matching the board silk <code>out[2:4]</code>.</p></div>'''

def jumper_section(title, comp_refs, table_html, card_key, note=''):
    btns = ' '.join(f'<button class="lk" data-comp="{r}">{r} on board →</button>' for r in comp_refs)
    return (f'<div class="card"><h2>{title}</h2>{table_html}{note}<p>{btns}</p>'
            f'<div class="jcard">@JC_{card_key}@</div></div>')

SECTIONS['jumpers'] = ('''
<div class="card"><p class="lead">Every 3-pin routing jumper follows the same rule — <b>the centre pin is the PROACT
chip</b>; jumper it toward the module (<b>M</b>) to use the USB bridge, or toward <b>CW</b> to use the ChipWhisperer.</p>
<div class="jcard">@JC_overview@</div></div>'''
 + jumper_section('JP3 + JP5 — UART routing', ['JP3', 'JP5'],
    '''<table class="t"><tr><th>Jumper</th><th>Centre = PROACT</th><th>Position M (module)</th><th>Position CW (ChipWhisperer)</th></tr>
<tr><td class="ck" data-comp="JP3"><b>JP3</b></td><td><code>TX</code> (pin 2)</td><td>2-3 → MCP2200 RX</td><td>1-2 → CW308 J7.8</td></tr>
<tr><td class="ck" data-comp="JP5"><b>JP5</b></td><td><code>RX</code> (pin 3)</td><td>2-3 → MCP2200 TX</td><td>1-2 → CW308 J7.7</td></tr></table>''', 'JP3_JP5')
 + jumper_section('JP4 + JP6 — SPI routing', ['JP4', 'JP6'],
    '''<table class="t"><tr><th>Jumper</th><th>Centre = PROACT</th><th>Position M (module)</th><th>Position CW (ChipWhisperer)</th></tr>
<tr><td class="ck" data-comp="JP4"><b>JP4</b></td><td><code>sck</code> (pin 19)</td><td>2-3 → MCP2210 SCK</td><td>1-2 → CW308 J7.12</td></tr>
<tr><td class="ck" data-comp="JP6"><b>JP6</b></td><td><code>SIn</code> (pin 17)</td><td>2-3 → MCP2210 MOSI</td><td>1-2 → CW308 J7.14</td></tr></table>''', 'JP4_JP6')
 + jumper_section('J6 — S-Sel &amp; trigger-input block (2×3)', ['J6'],
    '''<p>Selects the source of PROACT <code>SSel_n</code> (pin 18) and can route the PROACT trigger input (pin 23) to the CW308.</p>
<table class="t"><tr><th>Link on J6</th><th>Effect</th></tr>
<tr><td><b>1-3</b></td><td>MCP2210 GPIO4 drives <code>SSel_n</code> (pin 18) — SPI select from the bridge module</td></tr>
<tr><td><b>3-5</b></td><td>CW308 GPIO3 (J7.9) drives <code>SSel_n</code> (pin 18) — SPI select from the ChipWhisperer</td></tr>
<tr><td><b>5-6</b></td><td>CW308 GPIO3 drives <code>trigger_in</code> (pin 23)</td></tr></table>''', 'J6')
 + jumper_section('J10 — trigger select (2×3)', ['J10'],
    '''<p>Pin 4 is the CW308 GPIO4/TRIG line (J7.10); jumper it to one PROACT trigger source.</p>
<table class="t"><tr><th>Link on J10</th><th>Trigger source → CW308 TRIG</th></tr>
<tr><td><b>2-4</b></td><td><code>trigger_Out</code> — normal trigger (pin 14)</td></tr>
<tr><td><b>3-4</b></td><td><code>out_pins[1]</code> — config trigger (pin 13)</td></tr>
<tr><td><b>4-6</b></td><td><code>out_pins[8]</code> — reserve / software trigger (pin 27)</td></tr></table>''', 'J10')
 + jumper_section('J12 — clock source select (2×3)', ['J12'],
    '''<p>Three clock sources — fit <b>exactly one</b> link (the silk spells it: <i>Clk select — SMA / Osc / Cw</i>).
The chip clock is <b>hard-wired to J7.5</b>: a permanent clock echo out to the ChipWhisperer, whatever the source.</p>
<table class="t"><tr><th>Source</th><th>J12 link</th><th>Path</th></tr>
<tr><td><b>On-board 50 MHz</b></td><td><b>3-4</b></td><td>Y1 oscillator → R22 20 Ω → chip clock</td></tr>
<tr><td><b>External input</b></td><td><b>1-2</b></td><td>J11 coaxial jack → chip clock (SMA fitted; silk reads “BNC”)</td></tr>
<tr><td><b>ChipWhisperer</b></td><td><b>5-6</b></td><td>CW clock on J7.3 (the CW308 CLKFB line) → R8 100 Ω → chip clock</td></tr></table>
<div class="warn">Leave the CW308’s <b>J3</b> clock jumper <b>unpopulated</b> — it drives J7.5 (the clock-echo pin) and would fight the selected source.</div>''',
    'J12', '<p class="mut">Details and rules on the <button class="lk" data-tab="clock">Clock tab</button>.</p>')
 + jumper_section('JP1 — reset from ChipWhisperer (1×2)', ['JP1'],
    '''<p>Close to tie PROACT <code>B_RST_N</code> (pin 1) to the CW308 nRST line. Leave open to reset only from SW1.</p>''', 'JP1')
 + jumper_section('JP7 — Vcore route (1×3)', ['JP7'],
    '''<table class="t"><tr><th>Link</th><th>Route</th><th>When to use</th></tr>
<tr><td><b>1-2</b></td><td>0.8 V → J8.8 (FILTIN) → CW308 L-C low-pass filter → back on J8.5/6 → shunt</td>
<td>Side-channel capture on the CW308 — the filter cleans the rail so the shunt sees the die, not supply noise</td></tr>
<tr><td><b>2-3</b></td><td>0.8 V → shunt, direct</td><td>Bench use off the CW308, or the shortest supply path</td></tr></table>''', 'JP7')
 + jumper_section('JP2 — probe header (1×3, no jumper)', ['JP2'],
    '''<p>Probe header for IBEX PC bits — <code>out_pins[2]/[3]/[4]</code> (chip pins 26/25/24). No jumper fitted.</p>''', 'JP2'))

SECTIONS['clock'] = '''
<div class="card"><h2>Clock system</h2>
<p>The chip clock <code>SYSCLK_P</code> (pin 9) comes from one of <b>three sources</b>, selected on
<span class="ck" data-comp="J12">J12</span> with <b>exactly one link</b> fitted (radio behaviour — the silk spells it:
<i>Clk select — SMA / Osc / Cw</i>). Whatever the source, the chip clock is <b>permanently echoed to the
ChipWhisperer on J7.5</b>, so the scope can always sample synchronously with the target clock.</p>
<div class="dg">@CLOCK_SVG@</div>
<table class="t"><tr><th>Source</th><th>J12 link</th><th>Path</th></tr>
<tr><td><b>On-board 50 MHz</b></td><td><b>3-4</b></td><td><span class="ck" data-comp="Y1">Y1</span> oscillator → R22 20 Ω → chip clock</td></tr>
<tr><td><b>External input</b></td><td><b>1-2</b></td><td><span class="ck" data-comp="J11">J11</span> coaxial jack → chip clock. <i>The fitted connector is <b>SMA</b> (the board silk reads “BNC”).</i></td></tr>
<tr><td><b>ChipWhisperer</b></td><td><b>5-6</b></td><td>CW clock on J7.3 (the CW308 <code>CLKFB</code> line) → <span class="ck" data-comp="R8">R8</span> 100 Ω → chip clock</td></tr></table>
<h3>Clock echo — synchronous sampling</h3>
<p>The chip clock is <b>hard-wired to J7.5</b> — a permanent clock <b>echo out</b> to the ChipWhisperer, not a jumper.
Whatever J12 selects, the running clock is always available there: sample from the echo for phase-locked traces.
E.g. J12 = 3-4 with the scope locked to the J7.5 echo is the flagship synchronous-capture setup (configuration C).</p>
<div class="warn"><b>Rules</b><br>· Fit <b>exactly one source link</b> on J12 (1-2 · 3-4 · 5-6).<br>
· Leave the CW308’s <b>J3</b> clock jumper <b>unpopulated</b> — it drives J7.5 (the clock-echo pin) and would fight the selected source.<br>
· The <span class="ck" data-comp="CLK">CLK</span> test point sits directly on the chip clock net for probing.</div>
<div class="jcard">@JC_J12@</div></div>'''

SECTIONS['power'] = '''
<div class="warn big"><b>Do this before the chip ever meets the board:</b>
<ol>
<li>Leave the PROACT chip <b>out</b> of socket U1.</li>
<li>Set <span class="ck" data-comp="JP7">JP7</span> to <b>2-3</b> (direct).</li>
<li>Apply 3.3 V VDDIO (mount on the CW308, or feed the <span class="ck" data-comp="VDDIO">VDDIO</span> test point on the bench).</li>
<li>Meter between the <span class="ck" data-comp="Vcore">Vcore</span> test point and GND (no load → no shunt drop).</li>
<li>Turn <span class="ck" data-comp="R20">R20</span> (multi-turn) until the meter reads <b>0.800 V</b>.</li>
<li>Power down, insert the chip (notch up), power up and re-check under load.</li>
</ol></div>
<div class="card"><h2>Core voltage (Vcore)</h2>
<p>The 0.8 V core rail is generated on-board by the <span class="ck" data-comp="U2">U2</span> <b>TPS74801</b> LDO and
trimmed with the multi-turn potentiometer <span class="ck" data-comp="R20">R20</span>:</p>
<p class="formula">V<sub>core</sub> = 0.8 V × (1 + R20 / 8.2 kΩ) → adjustable 0.80 – 0.90 V</p>
<div class="dg">@POWER_SVG@</div>
<p>The die side of the shunt (net <code>Vcore</code>, chip pins <b>15</b> and <b>28</b>) is <b>deliberately
capacitor-free</b>: decoupling sits <i>before</i> the shunt (C5/C10), so the instantaneous die current flows through
<span class="ck" data-comp="R7">R7</span> and appears in the power trace.</p>
<div class="jcard">@JC_JP7@</div></div>
<div class="card"><h2>Power &amp; current-sense architecture</h2>
<pre class="tree">
 CW308 3.3 V (J8.14) ─────────────────────► VDDIO ─► PROACT pins 7, 22 · bridge modules · pull-ups
                                              │
                                              ▼
                     U2  TPS74801 LDO — trim R20/R21 → 0.80–0.90 V
                                              │
                          ┌─── JP7 1-2 ───────┤─── JP7 2-3 (direct) ──┐
                          ▼                                            │
              J8.8 FILTIN → CW308 L-C filter → J8.5/6 Vcore_back ──────┤
                                                                       ▼
                                    Vcore_shunt_1 (C5/C10 · J8.3 SHUNT-H)
                                                                       │
                                                            R7 0.01 Ω shunt
                                                                       │
                                      Vcore (die side · J8.2 SHUNT-L · no caps)
                                                                       ▼
                                                        PROACT core — pins 15, 28
</pre>
<ul>
<li><b>VDDIO (3.3 V)</b> comes from the CW308 rail (J8.14) and supplies the PROACT I/O ring, the USB bridge modules (via S1-6/7), the pull-ups and the U2 LDO. For USB-only bench use off the ChipWhisperer, feed 3.3 V into the VDDIO test point instead.</li>
<li><b>Vcore</b> is trimmed with R20 (procedure above) and routed by JP7 — through the CW308 filter for capture, or direct for the bench.</li>
<li><b>Measurement.</b> R7 is <b>0.01 Ω</b>, so 1 mA of core current is 10 µV across the shunt — use the ChipWhisperer low-noise amplifier on the MEAS output, or a differential probe directly in the two R7 probe holes.</li>
<li>Decoupling: 10 µF + 100 nF pairs on VDDIO and on the supply side of the shunt; <b>none</b> on the die side (by design, for side-channel fidelity).</li>
</ul></div>
<div class="card"><h2>Test points</h2>
<table class="t"><tr><th>Test point</th><th>Net</th><th>Use</th></tr>
<tr><td class="ck" data-comp="Vcore"><b>Vcore</b></td><td>Core rail, die side</td><td>Trim target for the 0.8 V procedure; probe <b>high-impedance only</b> — this node is deliberately capacitor-free</td></tr>
<tr><td class="ck" data-comp="VDDIO"><b>VDDIO</b></td><td>3.3 V I/O rail</td><td>Probe / feed the I/O supply</td></tr>
<tr><td class="ck" data-comp="CLK"><b>CLK</b></td><td>Chip clock (pin 9)</td><td>Observe the selected clock</td></tr>
<tr><td class="ck" data-comp="GND"><b>GND</b></td><td>Ground</td><td>Scope / meter reference</td></tr>
<tr><td class="ck" data-comp="R7"><b>R7</b></td><td>Shunt</td><td>0.01 Ω current-sense resistor with two probe holes for a differential probe</td></tr></table></div>'''

SECTIONS['s1'] = '''
<div class="card"><h2>S1 DIP switch — read-back &amp; power enables</h2>
<p><span class="ck" data-comp="S1">S1</span> is a 7-position DIP switch. Switches <b>1–5</b> enable GPIO
<b>read-back</b> loops (so the USB host can read the state of a signal it — or the chip — is driving);
switches <b>6–7</b> power the two USB bridge modules. <b>Click a switch</b> to explore it.</p>
<div id="s1x"></div>
<div id="s1detail"></div>
<table class="t"><tr><th>Switch</th><th>Enables</th><th>Purpose</th></tr>
<tr><td><b>1</b></td><td>MCP2210 GPIO3 reads GPIO4</td><td>Read back the <code>SSel_n</code> state</td></tr>
<tr><td><b>2</b></td><td>MCP2210 GPIO7 reads X1</td><td>Read back the <b>debug signal</b> selected on J1</td></tr>
<tr><td><b>3</b></td><td>MCP2210 GPIO8 reads GPIO2</td><td>Read back the <b>global reset</b> (<code>spi_global_RST_N</code>)</td></tr>
<tr><td><b>4</b></td><td>MCP2210 GPIO0 reads GPIO1</td><td>Read back the <b>SPI reset</b> (<code>spi_c_RST_N</code>)</td></tr>
<tr><td><b>5</b></td><td>MCP2210 GPIO6 reads GPIO5</td><td>Read back the <b>controller reset</b> (<code>C_RST_N</code>)</td></tr>
<tr><td><b>6</b></td><td>MCP2200 VDD → 3.3 V</td><td>Power the <b>UART</b> bridge module</td></tr>
<tr><td><b>7</b></td><td>MCP2210 VDD → 3.3 V</td><td>Power the <b>SPI</b> bridge module</td></tr></table></div>'''

SECTIONS['conn'] = '''
<div class="card"><h2>Connectors &amp; headers</h2>
<table class="t"><tr><th>Ref</th><th>Type</th><th>Role</th></tr>
<tr><td class="ck" data-comp="U1"><b>U1</b></td><td>DIP-28 socket</td><td>PROACT ASIC</td></tr>
<tr><td class="ck" data-comp="J1"><b>J1</b></td><td>2×5 male (SPI_DBG)</td><td>Debug-signal monitor</td></tr>
<tr><td class="ck" data-comp="J2"><b>J2 / J3</b></td><td>1×7 male ×2</td><td>MCP2200 USB-UART module socket</td></tr>
<tr><td class="ck" data-comp="J4"><b>J4 / J5</b></td><td>1×7 male ×2</td><td>MCP2210 USB-SPI module socket</td></tr>
<tr><td class="ck" data-comp="J6"><b>J6</b></td><td>2×3 female</td><td>S-Sel / trigger-input routing</td></tr>
<tr><td class="ck" data-comp="J10"><b>J10</b></td><td>2×3 female</td><td>Trigger select</td></tr>
<tr><td class="ck" data-comp="J11"><b>J11</b></td><td>coaxial jack</td><td>External clock input — SMA fitted (silk reads “BNC”)</td></tr>
<tr><td class="ck" data-comp="J12"><b>J12</b></td><td>2×3 female</td><td>Clock source select (SMA · Y1 · CW)</td></tr>
<tr><td class="ck" data-comp="J7"><b>J7</b></td><td>1×20 female</td><td>CW308 <b>West</b> edge connector (clock · UART · SPI · GPIO · nRST)</td></tr>
<tr><td class="ck" data-comp="J8"><b>J8</b></td><td>1×20 female</td><td>CW308 <b>East</b> edge connector (power rails · shunt sense · filter loop · status LEDs)</td></tr>
<tr><td class="ck" data-comp="J9"><b>J9</b></td><td>1×20 female</td><td>CW308 <b>South</b> edge connector (debug taps · SPI · nRST)</td></tr></table></div>
<div class="grid2">
<div class="card"><h3>J7 — CW308 West (clock &amp; control)</h3>
<table class="t"><tr><th>J7 pin</th><th>Net</th><th>Meaning</th></tr>
<tr><td>3</td><td><code>CLKFB</code></td><td>ChipWhisperer <b>clock in</b> — selected by J12 5-6, via R8</td></tr>
<tr><td>5</td><td><code>CLKIN</code></td><td>Chip <b>clock echo out</b> to the ChipWhisperer (always connected)</td></tr>
<tr><td>7</td><td><code>TIO1</code></td><td>UART toward chip RX (via JP5 1-2)</td></tr>
<tr><td>8</td><td><code>TIO2</code></td><td>Chip TX toward the scope (via JP3 1-2)</td></tr>
<tr><td>9</td><td><code>GPIO3</code></td><td>S-Sel / trigger-in source (via J6)</td></tr>
<tr><td>10</td><td><code>GPIO4 / TRIG</code></td><td>Trigger line (via J10)</td></tr>
<tr><td>11</td><td><code>nRST</code></td><td>Reset line (via JP1)</td></tr>
<tr><td>12 / 14</td><td><code>SCK</code> / <code>MOSI</code></td><td>SPI from the ChipWhisperer (via JP4 / JP6 1-2)</td></tr>
<tr><td>20</td><td><code>VREF</code></td><td>Level reference = board VDDIO</td></tr></table></div>
<div class="card"><h3>J8 — CW308 East (power &amp; measurement)</h3>
<table class="t"><tr><th>J8 pin</th><th>Net</th><th>Meaning</th></tr>
<tr><td>2</td><td><code>Vcore</code> (SHUNT-L)</td><td>Die side of the R7 sense shunt</td></tr>
<tr><td>3</td><td><code>Vcore_shunt_1</code> (SHUNT-H)</td><td>Supply side of the shunt</td></tr>
<tr><td>5, 6</td><td><code>Vcore_back</code></td><td>Filtered 0.8 V <b>returning</b> from the CW308 L-C filter (JP7 = 1-2)</td></tr>
<tr><td>8</td><td>0.8 V (FILTIN)</td><td>Core rail from the U2 LDO <b>into</b> the CW308 filter (JP7 = 1-2)</td></tr>
<tr><td>11</td><td>1.2 V</td><td>CW308 rail (unused)</td></tr>
<tr><td>12</td><td>1.8 V</td><td>CW308 rail (unused)</td></tr>
<tr><td>13</td><td>2.5 V</td><td>CW308 rail (unused)</td></tr>
<tr><td>14</td><td><code>VDDIO</code> (3.3 V)</td><td>I/O supply into the board</td></tr>
<tr><td>15</td><td>5 V</td><td>CW308 rail (unused)</td></tr>
<tr><td>18</td><td><code>SPI_RST</code> → LED1</td><td>CW308 status LED</td></tr>
<tr><td>19</td><td><code>CRST</code> → LED2</td><td>CW308 status LED</td></tr>
<tr><td>20</td><td><code>GRST</code> → LED3</td><td>CW308 status LED</td></tr></table></div>
<div class="card"><h3>J9 — CW308 South (debug taps)</h3>
<table class="t"><tr><th>J9 pin</th><th>Net</th></tr>
<tr><td>11</td><td><code>out_pins[11]</code> (pin 21)</td></tr>
<tr><td>12</td><td><code>VDDIO</code></td></tr>
<tr><td>13</td><td><code>sck</code> — CW side of JP4</td></tr>
<tr><td>14</td><td><code>SIn</code> — CW side of JP6</td></tr>
<tr><td>15</td><td><code>nRST</code> (with JP1)</td></tr>
<tr><td>17</td><td><code>out_pins[6]</code> (pin 12)</td></tr>
<tr><td>18</td><td><code>out_pins[5]</code> (pin 11)</td></tr>
<tr><td>19</td><td><code>spare_io</code> (pin 10)</td></tr>
<tr><td>20</td><td><code>out_pins[0]</code> (pin 6)</td></tr></table></div>
<div class="card"><h3>J1 — SPI_DBG signal monitor (2×5)</h3>
<p>Each <b>odd</b> pin is a live PROACT debug signal; the adjacent <b>even</b> pin is the common <code>X1</code> line.
Fit a jumper across a row to route that signal onto <code>X1</code>, which the MCP2210 reads via <b>GPIO7</b>
(enable S1-2). You can equally probe the odd pins directly.</p>
<table class="t"><tr><th>J1 pin</th><th>Signal</th><th>PROACT pin</th><th>LED</th></tr>
<tr><td>9</td><td><code>out_pins[0]</code></td><td>6</td><td class="ck" data-comp="D2">D2</td></tr>
<tr><td>7</td><td><code>spare_io</code></td><td>10</td><td class="ck" data-comp="D3">D3</td></tr>
<tr><td>5</td><td><code>out_pins[5]</code></td><td>11</td><td class="ck" data-comp="D4">D4</td></tr>
<tr><td>3</td><td><code>out_pins[6]</code></td><td>12</td><td class="ck" data-comp="D5">D5</td></tr>
<tr><td>1</td><td><code>out_pins[11]</code></td><td>21</td><td class="ck" data-comp="D6">D6</td></tr>
<tr><td>2,4,6,8,10</td><td><code>X1</code> common (→ MCP2210 GPIO7)</td><td>—</td><td>—</td></tr></table></div>
</div>
<div class="card"><h2>LEDs</h2>
<p>Ten on-board LEDs in <b>four color groups</b>:</p>
<table class="t"><tr><th>LED(s)</th><th>Color</th><th>Signal (chip pin)</th><th>Lights when</th></tr>
<tr><td class="ck" data-comp="D1"><b>D1</b></td><td><span class="sw" style="background:#a3e635"></span> yellow-green</td><td><code>out_pins[7]</code> — alive (16)</td><td>the heartbeat output is high — <b>blinking = chip alive</b></td></tr>
<tr><td class="ck" data-comp="D2"><b>D2 / D4 / D5 / D6</b></td><td><span class="sw" style="background:#facc15"></span> yellow</td><td><code>out_pins[0]/[5]/[6]/[11]</code> (6 / 11 / 12 / 21)</td><td>the debug output is <b>high</b></td></tr>
<tr><td class="ck" data-comp="D3"><b>D3</b></td><td><span class="sw" style="background:#fb923c"></span> orange</td><td><code>spare_io</code> (10)</td><td><b>you</b> drive the spare input high (it is an input — the chip never lights it)</td></tr>
<tr><td class="ck" data-comp="D7"><b>D7 / D8 / D9 / D10</b></td><td><span class="sw" style="background:#f87171"></span> red</td><td><code>B_RST_N</code> (1) / <code>C_RST_N</code> (5) / <code>spi_global_RST_N</code> (4) / <code>spi_c_RST_N</code> (20)</td><td>the reset is <b>asserted</b> (line low)</td></tr></table>
<p class="note"><b>All four red LEDs on at once is normal during reset — it is not a fault.</b> They are wired from
VDDIO through the LED into the reset line, so a low (active) reset lights them.</p>
<p class="mut">The CAD render on the board map shows placeholder LED bodies — the mounted parts follow the BOM colors
above. The silkscreen prints shorthand names next to the LEDs (<code>Mem[23]</code>, <code>Spare In</code>,
<code>UART Rvalid</code>, <code>Mem req</code>, <code>Co req</code>, <code>B/C/G/SPI RST</code>) — each LED’s panel
on the board map shows its silk label and the RTL signal name.</p>
<h3>CW308 motherboard status LEDs</h3>
<p>Driven by the reset lines through J8.18/19/20:</p>
<table class="t"><tr><th>CW308 LED</th><th>Signal</th></tr>
<tr><td><b>LED1</b></td><td><code>spi_c_RST_N</code> — SPI reset</td></tr>
<tr><td><b>LED2</b></td><td><code>C_RST_N</code> — controller reset</td></tr>
<tr><td><b>LED3</b></td><td><code>spi_global_RST_N</code> — global reset</td></tr></table></div>
<div class="card"><h2>USB bridge modules</h2>
<p>Two Microchip USB-bridge break-out modules plug into the 1×7 header pairs.</p>
<div class="grid2">
<div><h3>MCP2210 — USB ↔ SPI + GPIO (sockets <span class="ck" data-comp="J4">J4</span> / <span class="ck" data-comp="J5">J5</span>)</h3>
<table class="t"><tr><th>Module pin</th><th>Signal</th><th>Connected to</th></tr>
<tr><td>1</td><td>GPIO0</td><td>reads GPIO1 (<code>spi_c_RST_N</code>) when S1-4 on</td></tr>
<tr><td>2</td><td>GPIO1</td><td><code>SPI_RST</code> → PROACT pin <b>20</b> (<code>spi_c_RST_N</code>)</td></tr>
<tr><td>3</td><td>GPIO2</td><td><code>GRST</code> → PROACT pin 4 (<code>spi_global_RST_N</code>)</td></tr>
<tr><td>4</td><td>GPIO3</td><td>reads GPIO4 (<code>SSel_n</code>) when S1-1 on</td></tr>
<tr><td>5</td><td>GPIO4</td><td><code>SSel_n</code> → J6 (1-3) → PROACT pin 18</td></tr>
<tr><td>6</td><td>MOSI</td><td>JP6 (2-3) → PROACT pin 17 (<code>SIn</code>)</td></tr>
<tr><td>7</td><td>SCK</td><td>JP4 (2-3) → PROACT pin 19 (<code>sck</code>)</td></tr>
<tr><td>8</td><td>MISO</td><td>not connected</td></tr>
<tr><td>9</td><td>GPIO5</td><td><code>CRST</code> → PROACT pin 5 (<code>C_RST_N</code>)</td></tr>
<tr><td>10</td><td>GPIO6</td><td>reads GPIO5 (<code>C_RST_N</code>) when S1-5 on</td></tr>
<tr><td>11</td><td>GPIO7</td><td>reads X1 debug bus (J1) when S1-2 on</td></tr>
<tr><td>12</td><td>GPIO8</td><td>reads GPIO2 (<code>spi_global_RST_N</code>) when S1-3 on</td></tr>
<tr><td>13</td><td>GND</td><td>ground</td></tr>
<tr><td>14</td><td>VDD</td><td>3.3 V when S1-7 on</td></tr></table></div>
<div><h3>MCP2200 — USB ↔ UART (sockets <span class="ck" data-comp="J2">J2</span> / <span class="ck" data-comp="J3">J3</span>)</h3>
<table class="t"><tr><th>Module pin</th><th>Signal</th><th>Connected to</th></tr>
<tr><td>6</td><td>TX</td><td>JP5 (2-3) → PROACT RX (pin 3)</td></tr>
<tr><td>7</td><td>RX</td><td>JP3 (2-3) → PROACT TX (pin 2)</td></tr>
<tr><td>14</td><td>VDD</td><td>3.3 V when S1-6 on</td></tr></table></div>
</div></div>'''

SECTIONS['cw'] = '''
<div class="card"><h2>ChipWhisperer CW308 setup</h2>
<h3>CW308 motherboard settings for this board</h3>
<table class="t"><tr><th>CW308 control</th><th>Set to</th><th>Why</th></tr>
<tr><td><b>3.3 V rail</b></td><td>On</td><td>Supplies VDDIO via J8.14.</td></tr>
<tr><td><b>Filter input</b></td><td><b>Victim-supplied (FILTIN)</b></td><td>With JP7 = 1-2 the 0.8 V rail from U2 goes through the CW308 L-C filter and back. Do <b>not</b> drive it from VADJ. With JP7 = 2-3 the filter is out of the loop.</td></tr>
<tr><td><b>J3 clock jumper</b></td><td><b>Unpopulated</b></td><td>Leave the CW308’s J3 clock jumper unpopulated — it drives J7.5 (the clock-echo pin) and would fight the selected source. To clock the chip from the ChipWhisperer, set <b>J12 = 5-6</b> instead (CW clock arrives on J7.3, via R8).</td></tr>
<tr><td><b>VREF</b></td><td>From victim</td><td>Uses this board’s VDDIO (3.3 V, J7.20) as the level reference.</td></tr>
<tr><td><b>MEAS SMA → Capture</b></td><td>Connect</td><td>Feeds the shunt voltage into the ChipWhisperer ADC (or a scope).</td></tr></table>
<h3>ChipWhisperer software notes</h3>
<ul>
<li><b>UART direction is mirrored vs. the CW default:</b> the chip’s TX arrives on <b>TIO2</b> and the chip’s RX is driven from <b>TIO1</b> — configure <code>tio1 = serial_tx</code>, <code>tio2 = serial_rx</code>.</li>
<li><b>Synchronous sampling:</b> the chip clock is always echoed out on <b>J7.5</b> (hard-wired) — sample from it for phase-locked traces, whatever the J12 source.</li>
</ul>
<h3>Board-side settings for capture</h3>
<ul>
<li><span class="ck" data-comp="JP3">JP3</span> <span class="ck" data-comp="JP5">JP5</span> <span class="ck" data-comp="JP4">JP4</span> <span class="ck" data-comp="JP6">JP6</span> → <b>CW</b> (1-2) so UART/SPI come from the ChipWhisperer.</li>
<li><span class="ck" data-comp="J6">J6</span> → 3-5 (CW308 GPIO3 drives <code>SSel_n</code>); add 5-6 to drive <code>trigger_in</code> instead/as needed.</li>
<li><span class="ck" data-comp="J10">J10</span> → pick the trigger fed to TRIG (<b>2-4</b> normal · <b>3-4</b> config · <b>4-6</b> reserve).</li>
<li><span class="ck" data-comp="J12">J12</span> → clock source, exactly one link: <b>5-6</b> (CW clock) or <b>3-4</b> (Y1, sync capture) — see the Clock tab.</li>
<li><span class="ck" data-comp="JP7">JP7</span> → <b>1-2</b> (filtered) for capture.</li>
<li><span class="ck" data-comp="JP1">JP1</span> → closed if you want the CW308 nRST to reset the chip.</li>
<li><span class="ck" data-comp="S1">S1-6</span> / S1-7 → off (the USB bridges stay idle and unpowered during capture).</li>
<li><b>Precondition:</b> Vcore already trimmed to 0.800 V.</li>
</ul></div>'''

SECTIONS['cfg'] = '''
<div class="card"><h2>A. USB bench bring-up</h2><p class="mut">Talk to PROACT from a PC — no ChipWhisperer.</p>
<table class="t kv">
<tr><td><b>Precondition</b></td><td>Vcore trimmed to 0.800 V (<button class="lk" data-tab="power">procedure</button>)</td></tr>
<tr><td>S1-6, S1-7</td><td><b>on</b> (power both bridge modules)</td></tr>
<tr><td>S1-1…5</td><td>on as needed for GPIO read-back</td></tr>
<tr><td>JP3, JP5, JP4, JP6</td><td><b>M</b> (2-3)</td></tr>
<tr><td>J6</td><td><b>1-3</b> (MCP2210 GPIO4 → <code>SSel_n</code>)</td></tr>
<tr><td>J12</td><td><b>3-4</b> (on-board 50 MHz)</td></tr>
<tr><td>JP7</td><td><b>2-3</b> (direct)</td></tr>
<tr><td>JP1</td><td>open (reset via SW1)</td></tr>
<tr><td>VDDIO</td><td>feed 3.3 V into the VDDIO test point</td></tr></table></div>
<div class="card"><h2>B. ChipWhisperer capture, CW-clocked</h2>
<table class="t kv">
<tr><td>Mount board on the <b>CW308</b></td><td>—</td></tr>
<tr><td>JP3, JP5, JP4, JP6</td><td><b>CW</b> (1-2)</td></tr>
<tr><td>J6</td><td><b>3-5</b> (CW GPIO3 → <code>SSel_n</code>)</td></tr>
<tr><td>J10</td><td>choose trigger: <b>2-4</b> normal · <b>3-4</b> config · <b>4-6</b> reserve</td></tr>
<tr><td>J12</td><td><b>5-6</b> — the ChipWhisperer clock (arrives on J7.3, via R8 100 Ω)</td></tr>
<tr><td>CW308 J3</td><td><b>unpopulated</b> — it drives J7.5 (the clock-echo pin) and would fight the selected source</td></tr>
<tr><td>JP7</td><td><b>1-2</b> (through the CW308 filter)</td></tr>
<tr><td>JP1</td><td>closed (reset from CW308 nRST) if desired</td></tr>
<tr><td>S1-6, S1-7</td><td>off</td></tr></table></div>
<div class="card"><h2>C. Synchronous capture from the on-board oscillator <span class="tag">flagship SCA setup</span></h2>
<table class="t kv">
<tr><td>As configuration <b>B</b>, except:</td><td></td></tr>
<tr><td>J12</td><td><b>3-4</b> — Y1 clocks the chip; the clock is echoed to the scope on J7.5 — sample from it for phase-locked traces</td></tr>
<tr><td>CW308 J3</td><td><b>unpopulated</b> — it drives J7.5 (the clock-echo pin) and would fight the selected source</td></tr>
<tr><td>Scope clock</td><td>sample from the <b>J7.5</b> clock echo for phase-locked traces</td></tr></table></div>'''

SECTIONS['bom'] = ('<div class="card"><h2>Bill of materials</h2><p>27 line items (JLCPCB part numbers).</p>'
                   + BOM_TABLE + '</div>')

# ------------------------------------------------------------------ assembly --
CSS = open(f'{TOOLS}/wiki_app.css', encoding='utf-8').read()
JS = open(f'{TOOLS}/wiki_app.js', encoding='utf-8').read()

TABS = [('map', 'Board map'), ('overview', 'Overview'), ('pinout', 'Chip pinout'), ('jumpers', 'Jumpers'),
        ('clock', 'Clock'), ('power', 'Power & Vcore'), ('s1', 'S1 switch'), ('conn', 'Connectors & LEDs'),
        ('cw', 'CW308 setup'), ('cfg', 'Configurations'), ('bom', 'BOM')]

nav = ''.join(f'<button data-tabbtn="{k}"{" class=on" if k == "map" else ""}>{t}</button>' for k, t in TABS)

logo_img = f'<img id="logo" src="{LOGO}" alt="PROACT">' if LOGO else ''

map_section = '''
<div class="maplayout">
 <div id="mapcol">
  <div id="maptools">
   <div id="viewtog"><button data-view="T" class="on">TOP</button><button data-view="B">BOTTOM</button></div>
   <div id="cats"></div>
   <button id="resetview" title="Clear selection and filters">✕ clear</button>
  </div>
  <div id="boardmap"></div>
  <div id="maplegend"></div>
 </div>
 <aside id="panel"></aside>
</div>'''

body_sections = [f'<section class="tab on" id="tab-map">{map_section}</section>']
for k, _ in TABS[1:]:
    body_sections.append(f'<section class="tab" id="tab-{k}">{SECTIONS[k]}</section>')
BODY = '\n'.join(body_sections)

for tok, val in [('@ARCH@', ARCH), ('@CLOCK_SVG@', CLOCK), ('@POWER_SVG@', POWER),
                 ('@PINOUT_SVG@', PINOUT_SVG), ('@PINOUT_TABLE@', PINOUT_TABLE),
                 ('@PHOTO_TOP@', PHOTO_TOP), ('@PHOTO_BOT@', PHOTO_BOT)]:
    BODY = BODY.replace(tok, val)
for k, svg in JCARDS.items():
    BODY = BODY.replace(f'@JC_{k}@', svg)

HTML = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PROACT board — interactive wiki</title>
<style>{CSS}</style>
</head>
<body>
<header>
 <div class="brand">{logo_img}<div><h1>PROACT board <span class="fin">CW308 target</span></h1>
 <div class="sub">interactive board wiki · side-channel evaluation target</div></div></div>
 <div class="hsearch"><input id="q" type="search" placeholder="Search components, signals, nets…  (e.g. JP7, sck, shunt)" autocomplete="off">
 <div id="qdrop"></div></div>
</header>
<nav id="tabs">{nav}</nav>
<div id="vban">⚠ Set the core rail to <b>0.800&nbsp;V</b> with R20 <b>before inserting the chip</b> —
<button class="lk" data-tab="power">procedure</button></div>
<main>
{BODY}
</main>
<footer>PROACT board · every signal name, pin number and routing option cross-checked against the board design
data — netlist extracted from the Altium schematic, placements read from PCB1.PcbDoc, BOM from the production export,
CW308 pin functions from the ChipWhisperer CW308 UFO documentation.</footer>
<div id="tooltip"></div>
<div id="modal"><div id="modalbox"><button id="modalclose">✕ close</button><div id="modalbody"></div></div></div>
<script type="application/json" id="wiki-data">{data_json}</script>
<script>{JS}</script>
</body>
</html>'''

open(OUTFILE, 'w', encoding='utf-8').write(HTML)
print('wrote', OUTFILE, f'{os.path.getsize(OUTFILE)/1e6:.2f} MB')
print('components:', len(comps_json), '· nets kept:', len(NETS))
if missing_db:
    print('NO DB ENTRY (default used):', sorted(missing_db))
unlabeled = [ref for ref, e in comps_json.items() if not PADS.get(ref)]
if unlabeled: print('components with NO pads extracted:', unlabeled)
