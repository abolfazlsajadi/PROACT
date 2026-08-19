#!/usr/bin/env python3
"""Clock-tree diagram for the PROACT eval board.

Facts from the final extracted netlist (/tmp/netlist_final.txt) with the
designer-corrected J12 semantics (2026-08-06):
  CLK_pin net = U1.9 (SYSCLK_P) + J12.2/4/6 + J7.5 + CLK test point.
  J12 = clock SOURCE select, EXACTLY ONE link fitted (silk: SMA / Osc / Cw):
    Source 1: J11 SMA jack (silk mistakenly reads BNC)      -> J12.1  (link 1-2)
    Source 2: Y1 50 MHz active osc -> R22 20R               -> J12.3  (link 3-4)
    Source 3: ChipWhisperer clock, arrives on J7.3 (CW308 CLKFB line)
              -> R8 100R -> J12.5                           (link 5-6)
  J7.5 = PERMANENT CLOCK ECHO OUT: chip clock hard-wired to J7.5 so the
  ChipWhisperer can always observe it / sample synchronously. Not a jumper,
  not a source. Leave the CW308's own J3 clock jumper unpopulated -- it
  drives J7.5 and would fight the selected source.

SVG helpers (box/arrow/label) copied from gen_arch.py to match the house style.
"""

W, H = 1400, 580
S = []
def add(x): S.append(x)

add(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
    f'font-family="Inter, Segoe UI, Arial, sans-serif">')
add(f'<rect width="{W}" height="{H}" fill="#0b1220"/>')
# defs: arrowheads (ar = neutral, arb = CW308 blue, arc = clock cyan)
add('<defs>'
    '<marker id="ar" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">'
    '<path d="M0,0 L7,3 L0,6 Z" fill="#94a3b8"/></marker>'
    '<marker id="arb" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">'
    '<path d="M0,0 L7,3 L0,6 Z" fill="#38bdf8"/></marker>'
    '<marker id="arc" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">'
    '<path d="M0,0 L7,3 L0,6 Z" fill="#22d3ee"/></marker>'
    '</defs>')

def box(x, y, w, h, title, sub='', fill='#111827', stroke='#475569', tcol='#f1f5f9',
        scol='#94a3b8', rx=10, tfs=14, bold=800):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="2"/>')
    if sub:
        add(f'<text x="{x+w/2}" y="{y+h/2-4}" font-size="{tfs}" font-weight="{bold}" '
            f'text-anchor="middle" fill="{tcol}">{title}</text>')
        add(f'<text x="{x+w/2}" y="{y+h/2+13}" font-size="10" font-weight="600" '
            f'text-anchor="middle" fill="{scol}">{sub}</text>')
    else:
        add(f'<text x="{x+w/2}" y="{y+h/2+5}" font-size="{tfs}" font-weight="{bold}" '
            f'text-anchor="middle" fill="{tcol}">{title}</text>')

def arrow(x1, y1, x2, y2, col='#94a3b8', mk='ar', w=2, dash=''):
    d = f' stroke-dasharray="{dash}"' if dash else ''
    add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{col}" stroke-width="{w}" '
        f'marker-end="url(#{mk})"{d}/>')

def label(x, y, t, col='#cbd5e1', fs=10, anchor='middle', bold=600):
    add(f'<text x="{x}" y="{y}" font-size="{fs}" font-weight="{bold}" text-anchor="{anchor}" '
        f'fill="{col}">{t}</text>')

def badge_num(cx, cy, n, col='#22d3ee'):
    add(f'<circle cx="{cx}" cy="{cy}" r="9" fill="{col}" fill-opacity="0.15" '
        f'stroke="{col}" stroke-width="1.6"/>')
    label(cx, cy+3.5, str(n), col, 10, 'middle', 800)

def caution(x, y, txt):
    w = len(txt) * 5.7 + 52
    add(f'<rect x="{x}" y="{y}" width="{w:.0f}" height="36" rx="18" fill="#3f1d0b" '
        f'stroke="#f59e0b" stroke-width="1.6"/>')
    label(x+18, y+23.5, '&#9888;', '#fbbf24', 14, 'start', 700)
    label(x+40, y+22.5, txt, '#fdba74', 11, 'start', 700)
    return w

def chip(x, y, txt, col):
    w = len(txt) * 6.1 + 24
    add(f'<rect x="{x}" y="{y-13}" width="{w:.0f}" height="26" rx="13" fill="none" '
        f'stroke="{col}" stroke-width="1.5"/>')
    label(x+w/2, y+4, txt, col, 11, 'middle', 700)
    return w

# ---------- title ----------
label(W/2, 36, 'PROACT Evaluation Board — Clock Tree (J12 clock-source select)', '#f8fafc', 21, 'middle', 800)
label(W/2, 58, 'One net — CLK_pin — feeds SYSCLK_P. Fit exactly ONE J12 source link: 1-2 SMA · '
      '3-4 on-board 50 MHz · 5-6 ChipWhisperer. The running clock is always echoed to the CW on J7.5.',
      '#94a3b8', 11.5, 'middle', 600)

# ---------- geometry ----------
ROWS = (146, 230, 314)            # pin-row centres (lanes align to these)
HX, HW = 400, 136                 # J12 header body
LCX, RCX = 432, 504               # pin-column centres
BUSX = 634                        # bus bar left edge (w 12)

# ---------- source lanes (left) ----------
label(40, 96, 'CLOCK SOURCES — exactly ONE link · silk SMA / Osc / Cw', '#67e8f9', 11, 'start', 800)

# lane 1: external SMA
add('<rect x="32" y="106" width="330" height="72" rx="8" fill="#0e1729" '
    'stroke="#1e293b" stroke-width="1.2"/>')
badge_num(50, 118, 1)
label(66, 122, 'EXTERNAL — SMA · link 1-2', '#94a3b8', 9.5, 'start', 700)
box(64, 128, 180, 42, 'J11 — SMA jack', 'board silk reads BNC — it is SMA',
    '#083344', '#22d3ee', tfs=12.5)
arrow(244, ROWS[0]+3, LCX-14, ROWS[0], '#22d3ee', 'arc', 2.2)

# lane 2: on-board oscillator
add('<rect x="32" y="190" width="330" height="72" rx="8" fill="#0e1729" '
    'stroke="#1e293b" stroke-width="1.2"/>')
badge_num(50, 202, 2)
label(66, 206, 'ON-BOARD OSC — 50 MHz · link 3-4', '#94a3b8', 9.5, 'start', 700)
box(64, 212, 158, 42, 'Y1 — 50 MHz osc', 'VDDIO · C13 1µF + C16 100n',
    '#083344', '#22d3ee', tfs=12)
box(248, 216, 74, 32, 'R22 · 20Ω', '', '#111827', '#64748b', tfs=11)
arrow(222, ROWS[1]+1, 248, ROWS[1]+1, '#22d3ee', 'arc', 2.2)
arrow(322, ROWS[1]+1, LCX-14, ROWS[1], '#22d3ee', 'arc', 2.2)

# lane 3: ChipWhisperer clock (arrives on J7.3 CLKFB, through R8 to J12.5)
add('<rect x="32" y="274" width="330" height="72" rx="8" fill="#0e1729" '
    'stroke="#1e293b" stroke-width="1.2"/>')
badge_num(50, 286, 3, '#38bdf8')
label(66, 290, 'CHIPWHISPERER CLOCK — link 5-6', '#7dd3fc', 9.5, 'start', 700)
box(64, 296, 158, 42, 'J7.3 — CLKFB', 'CW308 drives its clock here',
    '#082f49', '#38bdf8', tfs=12)
box(248, 300, 74, 32, 'R8 · 100Ω', '', '#111827', '#64748b', tfs=11)
arrow(222, ROWS[2]+3, 248, ROWS[2]+2, '#38bdf8', 'arb', 2.2)
arrow(322, ROWS[2]+2, LCX-14, ROWS[2], '#38bdf8', 'arb', 2.2)

# ---------- J12 header (real 2x3) ----------
label(HX+HW/2, 96, 'J12 — CLKSEL (2×3 header)', '#f1f5f9', 12, 'middle', 800)
add(f'<rect x="{HX}" y="104" width="{HW}" height="256" rx="10" fill="#111827" '
    f'stroke="#475569" stroke-width="2"/>')
add('<circle cx="410" cy="134" r="3" fill="#a78bfa"/>')          # pin-1 marker
label(LCX, 118, 'src', '#64748b', 8.5, 'middle', 700)
label(RCX, 118, 'bus', '#64748b', 8.5, 'middle', 700)
for i, ry in enumerate(ROWS):
    for col, cx in ((0, LCX), (1, RCX)):
        pin = 2*i + 1 + col
        rx = 3 if pin == 1 else 6
        add(f'<rect x="{cx-12}" y="{ry-12}" width="24" height="24" rx="{rx}" '
            f'fill="#1e293b" stroke="{"#22d3ee" if col else "#64748b"}" stroke-width="1.6"/>')
        label(cx, ry+4, str(pin), '#e2e8f0', 11, 'middle', 800)
# jumper links: rounded caps across each pin pair
for ry, txt, lx in ((ROWS[0], '1-2  EXT (SMA)', 468),
                    (ROWS[1], '3-4  OSC 50M', 468),
                    (ROWS[2], '5-6  CW clock', 468)):
    add(f'<rect x="414" y="{ry-15}" width="108" height="30" rx="15" fill="#a78bfa" '
        f'fill-opacity="0.28" stroke="#a78bfa" stroke-width="2"/>')
    label(lx, ry+30, txt, '#c4b5fd', 9.5, 'middle', 700)

# ---------- CLK_pin bus bar ----------
for ry in ROWS:                                   # pins 2/4/6 commoned to the bus
    add(f'<line x1="516" y1="{ry}" x2="{BUSX}" y2="{ry}" stroke="#22d3ee" stroke-width="2.2"/>')
    add(f'<circle cx="{BUSX+6}" cy="{ry}" r="3.6" fill="#22d3ee"/>')
add(f'<rect x="{BUSX-5}" y="125" width="22" height="222" rx="9" fill="#22d3ee" opacity="0.12"/>')
add(f'<rect x="{BUSX}" y="130" width="12" height="212" rx="6" fill="#22d3ee" opacity="0.9"/>')
label(BUSX+6, 120, 'CLK_pin', '#67e8f9', 11.5, 'middle', 800)

# ---------- right side: loads & echo ----------
label(860, 108, 'CLK_pin LOADS &amp; ECHO', '#67e8f9', 11, 'start', 800)
# chip pin
box(860, 120, 330, 64, 'SYSCLK_P — U1 pin 9', 'PROACT ASIC · DIP-28 socket U1',
    '#083344', '#22d3ee', tfs=15)
arrow(BUSX+12, 152, 856, 152, '#22d3ee', 'arc', 2.4)
# test point
box(860, 204, 210, 44, 'CLK — test point', 'scope probe pad', '#111827', '#e2e8f0', tfs=12.5)
arrow(BUSX+12, 226, 856, 226, '#94a3b8', 'ar', 2)
# J7.5 clock-echo tap: OUT of the bus, permanently connected
box(860, 268, 330, 64, 'J7.5 — clock echo out',
    'ChipWhisperer observes/samples the running clock', '#082f49', '#38bdf8', tfs=13.5)
arrow(BUSX+12, 300, 856, 300, '#38bdf8', 'arb', 2.4)
label(750, 290, 'clock echo out (always connected)', '#7dd3fc', 9.5, 'middle', 700)

# ---------- caution badges ----------
c1w = caution(96, 392, 'fit exactly ONE J12 source link')
caution(96 + c1w + 26, 392,
        "Leave the CW308's J3 clock jumper unpopulated — it drives J7.5 (the clock-echo pin) and would fight the selected source.")

# ---------- sync-capture recipe strip ----------
add('<rect x="32" y="456" width="1336" height="58" rx="10" fill="#0e1729" '
    'stroke="#334155" stroke-width="1.4"/>')
add('<rect x="32" y="456" width="6" height="58" rx="3" fill="#22d3ee"/>')
label(60, 491, 'Sync capture recipe:', '#f1f5f9', 13, 'start', 800)
cx = 220
cx += chip(cx, 485, 'J12 → 3-4', '#a78bfa') + 18
cx += chip(cx, 485, 'chip clock echoed on J7.5 — sample from it', '#38bdf8') + 18
cx += chip(cx, 485, 'CW308 J3 empty', '#f59e0b') + 18
label(cx + 8, 489, '&#8594;  capture is synchronous to the 50 MHz device clock',
      '#94a3b8', 10.5, 'start', 600)

# ---------- footer ----------
label(W/2, 552, 'CLK_pin net (final netlist):  U1.9 SYSCLK_P · J12.2/4/6 · J7.5 (clock echo) · CLK test point'
      '   —   CW clock in:  J7.3 CLKFB → R8 100Ω → J12.5',
      '#64748b', 9.5, 'middle', 600)

add('</svg>')
SVG = '/home/abish/Downloads/PCB/PROACT_DOC/docs/img/clock_tree.svg'
PNG = '/home/abish/Downloads/PCB/PROACT_DOC/docs/img/clock_tree.png'
open(SVG, 'w').write('\n'.join(S))
print('wrote clock_tree.svg')

# ---------- rasterize (~1600 px wide): rsvg-convert -> inkscape -> cairosvg ----
import subprocess
def try_run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True).returncode == 0
    except FileNotFoundError:
        return False

if try_run(['rsvg-convert', '-w', '1600', '-o', PNG, SVG]):
    print('wrote clock_tree.png (rsvg-convert)')
elif try_run(['inkscape', SVG, f'--export-filename={PNG}', '--export-width=1600']):
    print('wrote clock_tree.png (inkscape)')
else:
    try:
        import cairosvg
    except ImportError:
        subprocess.run(['python3', '-m', 'pip', 'install', 'cairosvg'], check=True)
        import cairosvg
    cairosvg.svg2png(url=SVG, write_to=PNG, output_width=1600)
    print('wrote clock_tree.png (cairosvg)')
