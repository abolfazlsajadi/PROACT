#!/usr/bin/env python3
"""Core power & current-sense path diagram for the PROACT eval board.

VDDIO 3.3 V (CW308 J8.14) -> U2 TPS74801 (trim R20/R21 -> 0.8 V) -> JP7 route
select (direct 2-3 / CW308-filtered 1-2) -> Vcore_shunt_1 -> R7 0.01R shunt ->
Vcore -> PROACT pins 15 & 28.  ChipWhisperer measures dV across R7.

Outputs docs/img/power_path.svg (+ .png via rsvg-convert/inkscape/cairosvg).
"""

W, H = 1400, 760
S = []
def add(x): S.append(x)

add(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
    f'font-family="Inter, Segoe UI, Arial, sans-serif">')
add(f'<rect width="{W}" height="{H}" fill="#0b1220"/>')
# defs: arrowheads
add('<defs>'
    '<marker id="ar" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">'
    '<path d="M0,0 L7,3 L0,6 Z" fill="#94a3b8"/></marker>'
    '<marker id="arb" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">'
    '<path d="M0,0 L7,3 L0,6 Z" fill="#38bdf8"/></marker>'
    '<marker id="aro" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">'
    '<path d="M0,0 L7,3 L0,6 Z" fill="#f59e0b"/></marker>'
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
    elif title:
        add(f'<text x="{x+w/2}" y="{y+h/2+5}" font-size="{tfs}" font-weight="{bold}" '
            f'text-anchor="middle" fill="{tcol}">{title}</text>')

def arrow(x1, y1, x2, y2, col='#94a3b8', mk='ar', w=2, dash=''):
    d = f' stroke-dasharray="{dash}"' if dash else ''
    add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{col}" stroke-width="{w}" '
        f'marker-end="url(#{mk})"{d}/>')

def line(x1, y1, x2, y2, col='#94a3b8', w=2, dash=''):
    d = f' stroke-dasharray="{dash}"' if dash else ''
    add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{col}" stroke-width="{w}"{d}/>')

def label(x, y, t, col='#cbd5e1', fs=10, anchor='middle', bold=600):
    add(f'<text x="{x}" y="{y}" font-size="{fs}" font-weight="{bold}" text-anchor="{anchor}" '
        f'fill="{col}">{t}</text>')

def node(x, y, r=5):
    add(f'<circle cx="{x}" cy="{y}" r="{r}" fill="#f59e0b" stroke="#0b1220" stroke-width="1.5"/>')

def pill(cx, cy, t, col='#38bdf8', tcol='#7dd3fc', fs=8.5):
    w = len(t) * 5.6 + 16
    add(f'<rect x="{cx-w/2:.1f}" y="{cy-9}" width="{w:.1f}" height="18" rx="9" '
        f'fill="#0b1220" stroke="{col}" stroke-width="1.3"/>')
    label(cx, cy + 3, t, tcol, fs, 'middle', 700)

def gnd(x, y):
    line(x-10, y, x+10, y, '#64748b', 1.6)
    line(x-6.5, y+4, x+6.5, y+4, '#64748b', 1.6)
    line(x-3, y+8, x+3, y+8, '#64748b', 1.6)

def cap(x, ytop, ybot):
    """Capacitor symbol: wire, two plates, wire to GND."""
    line(x, ytop, x, ybot-18, '#94a3b8', 1.6)
    line(x-12, ybot-18, x+12, ybot-18, '#cbd5e1', 2)
    line(x-12, ybot-12, x+12, ybot-12, '#cbd5e1', 2)
    line(x, ybot-12, x, ybot, '#94a3b8', 1.6)
    gnd(x, ybot)

RAIL = 430  # main rail y

# ---------- title ----------
label(W/2, 34, 'PROACT Evaluation Board — Core Power &amp; Current-Sense Path', '#f8fafc', 21, 'middle', 800)
label(W/2, 56, 'VDDIO 3.3 V (CW308 J8.14) → U2 TPS74801 trimmed to 0.8 V → JP7 route select (direct or CW308-filtered) → '
      'R7 0.01 Ω shunt → PROACT die (pins 15 &amp; 28) — the CW308 captures the power trace across R7.',
      '#94a3b8', 11.5, 'middle', 600)

# ---------- VDDIO entry ----------
box(30, 385, 150, 100, '', '', '#1c1917', '#f59e0b')
label(105, 414, 'VDDIO · 3.3 V', '#f1f5f9', 14, 'middle', 800)
label(105, 436, 'from CW308 J8.14', '#fdba74', 9.5)
label(105, 452, 'also J7.20 VREF · J9.12', '#94a3b8', 8.5)
label(105, 468, 'CW308 supplies the board', '#94a3b8', 8)
# fan-out note
arrow(105, 383, 105, 347, '#94a3b8', 'ar', 1.6, '4 3')
label(105, 320, 'also feeds: chip VDDIO pins 7/22,', '#94a3b8', 9)
label(105, 334, 'USB bridges (DIP S1-6/7) · pull-ups', '#94a3b8', 9)

# ---------- U2 LDO ----------
box(230, 385, 180, 100, '', '', '#3f1d0b', '#b45309')
label(320, 414, 'U2 — TPS74801', '#f1f5f9', 15, 'middle', 800)
label(320, 434, 'LDO · IN / BIAS / EN = VDDIO', '#94a3b8', 9.5)
label(320, 450, 'soft-start: C18 10 nF · PG: n/c', '#94a3b8', 9.5)
label(320, 470, 'FB ← trim network (below)', '#fdba74', 9, 'middle', 700)

arrow(180, RAIL, 226, RAIL, '#f59e0b', 'aro', 2.5)
label(203, 420, '3.3 V', '#fdba74', 9, 'middle', 700)
arrow(410, RAIL, 462, RAIL, '#f59e0b', 'aro', 2.5)
label(436, 420, '0.8 V', '#fdba74', 9, 'middle', 700)

# ---------- trim bubble (attached to U2) ----------
line(320, 485, 320, 535, '#f59e0b', 1.4, '4 3')
add('<rect x="200" y="535" width="280" height="160" rx="12" fill="#0e1729" '
    'stroke="#f59e0b" stroke-width="1.6"/>')
label(340, 556, 'Output trim — FB divider', '#fdba74', 11, 'middle', 800)
# mini schematic (left column)
label(248, 566, '0.8 V', '#f59e0b', 8, 'middle', 700)
line(248, 570, 248, 574, '#94a3b8', 1.6)
add('<rect x="238" y="574" width="20" height="22" rx="3" fill="#1e293b" '
    'stroke="#cbd5e1" stroke-width="1.4"/>')
label(264, 589, 'R20', '#e2e8f0', 8.5, 'start', 700)
# wiper: arrow into R20 body, strapped down to FB node
arrow(224, 585, 236, 585, '#fdba74', 'aro', 1.4)
line(224, 585, 224, 602, '#fdba74', 1.4)
line(224, 602, 248, 602, '#fdba74', 1.4)
label(256, 599, 'FB', '#fdba74', 8, 'start', 700)
line(248, 596, 248, 608, '#94a3b8', 1.6)
add('<circle cx="248" cy="602" r="2.5" fill="#fdba74"/>')
add('<rect x="238" y="608" width="20" height="22" rx="3" fill="#1e293b" '
    'stroke="#cbd5e1" stroke-width="1.4"/>')
label(264, 623, 'R21', '#e2e8f0', 8.5, 'start', 700)
line(248, 630, 248, 638, '#94a3b8', 1.6)
gnd(248, 638)
# text (right column)
label(300, 576, 'R20 1 k multi-turn trimmer', '#e2e8f0', 9.5, 'start', 700)
label(300, 590, '(wiper = FB, strapped to 3rd terminal)', '#94a3b8', 8.5, 'start')
label(300, 604, 'R21 8.2 k → GND', '#e2e8f0', 9.5, 'start', 700)
label(300, 622, 'VOUT = 0.8 × (1 + R20 / 8.2 k)', '#94a3b8', 9, 'start')
label(300, 636, '= 0.80 – 0.90 V adjustable', '#94a3b8', 9, 'start')
# warning band
add('<rect x="212" y="652" width="256" height="36" rx="8" fill="#450a0a" '
    'fill-opacity="0.6" stroke="#f87171" stroke-width="1.2"/>')
label(340, 666, 'SET 0.8 V BEFORE INSERTING THE CHIP', '#fecaca', 9.5, 'middle', 800)
label(340, 681, 'trim R20 · measure Vcore TP · JP7 at 2-3', '#fca5a5', 8.5)

# ---------- 0.8 V rail node + two routes ----------
node(470, RAIL)
label(478, 421, '0.8 V rail', '#fdba74', 9, 'start', 700)

# lower route: link 2-3 DIRECT
line(470, 435, 470, 490, '#f59e0b', 2.5)
arrow(470, 490, 594, 490, '#f59e0b', 'aro', 2.5)
label(530, 481, 'link 2-3 · DIRECT (default)', '#fdba74', 9, 'middle', 700)

# upper route: link 1-2 FILTERED through the CW308
line(470, 425, 470, 150, '#38bdf8', 2, '5 4')
arrow(470, 150, 501, 150, '#38bdf8', 'arb', 2, '5 4')
pill(470, 240, 'J8.8 · FILTIN')
label(482, 300, 'link 1-2 = FILTERED route', '#7dd3fc', 9, 'start', 700)

# CW308 L-C filter box
box(505, 110, 330, 80, '', '', '#082f49', '#38bdf8')
label(670, 138, 'ChipWhisperer CW308 — L-C low-pass filter', '#bae6fd', 13, 'middle', 800)
label(670, 156, 'on the CW308 baseboard', '#7dd3fc', 9.5)
label(670, 174, '0.8 V out on J8.8 → filter → back on J8.5 / J8.6', '#7dd3fc', 9)
# return path down into JP7.1
arrow(620, 190, 620, 344, '#38bdf8', 'arb', 2, '5 4')
pill(620, 262, 'J8.5 / J8.6 · Vcore_back')

# ---------- JP7 3-pin header ----------
label(592, 342, 'JP7', '#ede9fe', 13, 'end', 800)
add('<rect x="600" y="350" width="40" height="160" rx="6" fill="#2e1065" '
    'fill-opacity="0.55" stroke="#a78bfa" stroke-width="2"/>')
# default shunt position highlight (2-3)
add('<rect x="606" y="416" width="28" height="88" rx="6" fill="#f59e0b" '
    'fill-opacity="0.12" stroke="#f59e0b" stroke-width="1" stroke-dasharray="3 3"/>')
for i, py in enumerate((370, 430, 490)):
    add(f'<circle cx="620" cy="{py}" r="6" fill="#1e293b" stroke="#cbd5e1" stroke-width="1.5"/>')
    label(607, py + 3.5, str(i + 1), '#ede9fe', 8.5, 'middle', 700)
label(620, 530, 'shunt 1-2 = filtered · shunt 2-3 = direct', '#c4b5fd', 9, 'middle', 600)

# ---------- JP7.2 -> Vcore_shunt_1 ----------
arrow(627, RAIL, 792, RAIL, '#f59e0b', 'aro', 2.5)
node(800, RAIL)
label(788, 415, 'Vcore_shunt_1', '#fdba74', 9, 'end', 700)
# SHUNTH tap
line(800, 424, 800, 369, '#38bdf8', 1.6, '4 3')
pill(800, 360, 'J8.3 · SHUNTH')
# decoupling C5 / C10
line(800, 435, 800, 492, '#94a3b8', 1.6)
line(762, 492, 838, 492, '#94a3b8', 1.6)
cap(762, 492, 524)
cap(838, 492, 524)
label(744, 512, 'C5 100 n', '#cbd5e1', 8.5, 'end', 700)
label(856, 512, 'C10 10 µ', '#cbd5e1', 8.5, 'start', 700)
label(800, 556, 'board-side decoupling', '#94a3b8', 8.5)

# ---------- R7 shunt ----------
arrow(808, RAIL, 876, RAIL, '#f59e0b', 'aro', 2.5)
box(880, 402, 100, 56, 'R7', '0.01 Ω shunt', '#3f1d0b', '#b45309', tfs=14)
label(874, 422, '2', '#94a3b8', 8.5, 'end', 700)
label(986, 422, '1', '#94a3b8', 8.5, 'start', 700)

# ---------- Vcore node ----------
arrow(980, RAIL, 1052, RAIL, '#f59e0b', 'aro', 2.5)
node(1060, RAIL)
label(1072, 415, 'Vcore', '#fdba74', 9, 'start', 700)
# SHUNTL tap
line(1060, 424, 1060, 369, '#38bdf8', 1.6, '4 3')
pill(1060, 360, 'J8.2 · SHUNTL')
# Vcore test point
line(1060, 435, 1060, 483, '#94a3b8', 1.6)
add('<circle cx="1060" cy="491" r="7" fill="#0b1220" stroke="#e2e8f0" stroke-width="2"/>')
add('<circle cx="1060" cy="491" r="2" fill="#e2e8f0"/>')
label(1060, 515, 'Vcore test point', '#cbd5e1', 8.5, 'middle', 700)
# die-side badge
add('<rect x="956" y="534" width="228" height="22" rx="11" fill="#1a2e05" '
    'stroke="#84cc16" stroke-width="1.4"/>')
label(1070, 549, 'die-side: NO capacitors — SCA fidelity', '#d9f99d', 9, 'middle', 700)

# ---------- chip ----------
arrow(1067, RAIL, 1176, RAIL, '#f59e0b', 'aro', 2.5)
box(1180, 380, 190, 100, '', '', '#111827', '#64748b')
label(1275, 412, 'PROACT ASIC', '#f8fafc', 15, 'middle', 800)
label(1275, 434, 'VCORE → pins 15 &amp; 28', '#fdba74', 10, 'middle', 700)
label(1275, 452, 'DIP-28 · socket U1', '#94a3b8', 9)
label(1275, 468, 'VDDIO pins 7/22 ← 3.3 V', '#94a3b8', 8.5)

# ---------- bottom annotation ----------
arrow(930, 462, 930, 684, '#38bdf8', 'arb', 1.6, '4 3')
label(938, 575, 'ΔV sense', '#7dd3fc', 8, 'start', 700)
add('<rect x="520" y="688" width="850" height="54" rx="10" fill="#1c1917" '
    'stroke="#f59e0b" stroke-width="2"/>')
label(945, 710, 'ChipWhisperer measures ΔV across R7 (SHUNTH − SHUNTL) → power trace', '#fdba74', 12, 'middle', 800)
label(945, 730, 'R7 = 0.01 Ω → 1 mA of core current = 10 µV of signal · die-side rail is deliberately '
      'capacitor-free so the shunt sees the instantaneous die current', '#94a3b8', 9.5)

# ---------- legend ----------
ly = 752
items = [('#f59e0b', 'core power rail (0.8 V)'), ('#38bdf8', 'CW308 loop / sense'),
         ('#a78bfa', 'JP7 route select')]
lx = 30
for c, n in items:
    add(f'<rect x="{lx}" y="{ly-11}" width="14" height="14" rx="3" fill="{c}"/>')
    label(lx + 20, ly, n, '#cbd5e1', 10, 'start', 600)
    lx += 90 + len(n) * 5

add('</svg>')
open('/home/abish/Downloads/PCB/PROACT_DOC/docs/img/power_path.svg', 'w').write('\n'.join(S))
print('wrote power_path.svg')
