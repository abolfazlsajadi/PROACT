#!/usr/bin/env python3
"""System architecture & signal-routing diagram for the PROACT eval board."""

W, H = 1400, 1060
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
    '<marker id="arr" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">'
    '<path d="M0,0 L7,3 L0,6 Z" fill="#f87171"/></marker>'
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

def mux(x, y, w, h, name, out_lbl):
    """2:1 selector trapezoid: wide (2 inputs) on left, narrow (1 out) on right."""
    add(f'<path d="M{x},{y} L{x},{y+h} L{x+w},{y+h*0.72} L{x+w},{y+h*0.28} Z" '
        f'fill="#2e1065" fill-opacity="0.55" stroke="#a78bfa" stroke-width="2"/>')
    add(f'<text x="{x+w*0.42}" y="{y+h/2+4}" font-size="12" font-weight="800" '
        f'text-anchor="middle" fill="#ede9fe">{name}</text>')
    return (x, y + h*0.30, x, y + h*0.70, x+w, y+h/2)  # in_top, in_bot, out

# ---------- title ----------
label(W/2, 34, 'PROACT Evaluation Board — System Architecture &amp; Signal Routing', '#f8fafc', 21, 'middle', 800)
label(W/2, 56, 'Each interface jumper is a 2:1 selector: drive the PROACT pin from the on-board USB bridge module OR from the ChipWhisperer CW308.',
      '#94a3b8', 11.5, 'middle', 600)

# ---------- USB host ----------
box(24, 360, 80, 90, 'USB', 'Host PC', '#0e1729', '#334155', tfs=13)

# ---------- bridge modules ----------
box(150, 150, 170, 78, 'MCP2200', 'USB ↔ UART bridge', '#052e2b', '#14b8a6', tfs=15)
box(150, 330, 170, 118, 'MCP2210', 'USB ↔ SPI + GPIO bridge', '#0b213f', '#3b82f6', tfs=15)

arrow(104, 388, 150, 210, '#64748b')   # usb -> mcp2200
arrow(104, 420, 150, 388, '#64748b')   # usb -> mcp2210

# ---------- mux column ----------
mu = mux(400, 150, 96, 70, 'JP3/JP5', 'UART')
ms = mux(400, 300, 96, 70, 'JP4/JP6', 'SPI')
mss = mux(400, 400, 96, 62, 'J6', 'S-Sel')

# module -> mux (top input)
arrow(320, 180, 400, mu[1], '#14b8a6', 'ar')
arrow(320, 372, 400, ms[1], '#3b82f6', 'ar')
arrow(320, 410, 400, mss[1], '#3b82f6', 'ar')
# CW308 alt input (bottom input) - short stubs labeled
for m, ytag in ((mu, 'CW308'), (ms, 'CW308'), (mss, 'CW GPIO3')):
    add(f'<line x1="{m[2]-34}" y1="{m[3]+22}" x2="{m[2]}" y2="{m[3]}" stroke="#38bdf8" '
        f'stroke-width="2" marker-end="url(#arb)" stroke-dasharray="4 3"/>')
    label(m[2]-40, m[3]+30, ytag, '#7dd3fc', 8.5, 'end', 700)

# ---------- PROACT ----------
PX, PY, PW, PH = 560, 110, 230, 522
box(PX, PY, PW, PH, '', '', '#111827', '#64748b')
label(PX+PW/2, PY+30, 'PROACT ASIC', '#f8fafc', 17, 'middle', 800)
label(PX+PW/2, PY+48, 'DIP-28 · socket U1', '#94a3b8', 10.5, 'middle', 600)
# signal group rows inside PROACT (v2 final pin map)
groups = [
    ('UART',       ['2 TX · 3 RX'],                                '#14b8a6'),
    ('SPI',        ['SIn 17 · sck 19'],                            '#3b82f6'),
    ('S-Sel',      ['SSel_n 18'],                                  '#3b82f6'),
    ('Resets',     ['1 B_RST_N (SW1/JP1) · 4 spi_global_RST_N',
                    '5 C_RST_N · 20 spi_c_RST_N'],                 '#f87171'),
    ('Debug taps', ['out[0]/[5]/[6]/[11] → pins 6/11/12/21',
                    'spare_io pin 10 (input)'],                    '#fb923c'),
    ('Triggers',   ['trigger_Out 14 · out[1] 13 · out[8] 27',
                    'trigger_in 23 ← CW · via J10/J6'],            '#a78bfa'),
    ('Core probe', ['out_pins[2:4] → pins 24–26 · JP2'],           '#84cc16'),
    ('Power',      ['VDDIO 7,22 · VDD core 0.8 V 15,28'],          '#f59e0b'),
    ('Clock',      ['SYSCLK_P pin 9 ← J12 · echo → J7.5'],         '#22d3ee'),
]
gy = PY + 66
rowc = []
y = gy
for n, subs, c in groups:
    rh = 38 if len(subs) == 1 else 50
    add(f'<rect x="{PX+12}" y="{y}" width="{PW-24}" height="{rh}" rx="6" '
        f'fill="{c}" fill-opacity="0.13" stroke="{c}" stroke-width="1.3"/>')
    label(PX+22, y+15, n, '#f1f5f9', 11.5, 'start', 800)
    for j, s in enumerate(subs):
        label(PX+22, y+28+j*11, s, '#94a3b8', 8.4, 'start', 500)
    rowc.append(y + rh/2)
    y += rh + 8

def prow(i):
    return rowc[i]

# mux outputs -> PROACT groups
arrow(496, 185, PX+12, prow(0), '#14b8a6')   # UART
arrow(496, 335, PX+12, prow(1), '#3b82f6')   # SPI
arrow(496, 431, PX+12, prow(2), '#3b82f6')   # S-Sel
# MCP2210 GPIO resets -> PROACT resets (direct)
add(f'<path d="M235,448 C235,555 480,500 {PX+12},{prow(3)+3}" fill="none" '
    f'stroke="#f87171" stroke-width="2" marker-end="url(#arr)"/>')
label(400, 527, 'MCP2210 GPIO2/5/1 → pins 4/5/20 (direct)', '#fca5a5', 9, 'middle', 600)

# ---------- CW308 ----------
CX, CY, CW_, CH = 960, 110, 330, 250
add(f'<rect x="{CX}" y="{CY}" width="{CW_}" height="{CH}" rx="10" fill="#3f1d0b" '
    f'stroke="#f59e0b" stroke-width="2"/>')
label(CX+CW_/2, CY+28, 'ChipWhisperer CW308', '#fef3c7', 17, 'middle', 800)
label(CX+CW_/2, CY+46, 'side-channel capture / glitch platform', '#fdba74', 10, 'middle', 600)
label(CX+CW_/2, CY+68, 'Edge connectors  J7 · J8 · J9', '#fed7aa', 11, 'middle', 700)
add(f'<rect x="{CX+20}" y="{CY+80}" width="{CW_-40}" height="72" rx="6" fill="#0b1220" '
    f'fill-opacity="0.5" stroke="#b45309" stroke-width="1.2"/>')
label(CX+CW_/2, CY+100, 'UART · SPI/GPIO · nRST · CW clk J7.3 · clk echo J7.5', '#fdba74', 10, 'middle', 600)
label(CX+CW_/2, CY+118, '3.3 V (J8.14) → VDDIO · VREF J7.20', '#fdba74', 10, 'middle', 600)
label(CX+CW_/2, CY+136, 'shunt J8.2/J8.3 · filter J8.5/6/8 · TRIG in', '#fdba74', 10, 'middle', 600)
add(f'<rect x="{CX+20}" y="{CY+168}" width="{CW_-40}" height="60" rx="6" fill="#450a0a" '
    f'fill-opacity="0.4" stroke="#dc2626" stroke-width="1"/>')
label(CX+CW_/2, CY+188, 'CW308 status LEDs (driven by resets):', '#fca5a5', 9.5, 'middle', 700)
label(CX+CW_/2, CY+206, 'LED1 = SPI_RST · LED2 = CRST · LED3 = GRST', '#fecaca', 9.5, 'middle', 600)

# CW308 feeds muxes (dashed bus from CW308 left edge to mux column)
add(f'<path d="M{CX},200 C620,60 300,70 {mu[2]-2},{mu[3]-2}" fill="none" stroke="#38bdf8" '
    f'stroke-width="1.6" stroke-dasharray="5 4" opacity="0.7"/>')
label(515, 86, 'CW308 shared-signal bus (alt source)', '#7dd3fc', 9, 'middle', 600)

# ---------- J10 trigger ----------
box(960, 412, 210, 74, 'J10 · Trigger select', 'out 14/13/27 → CW TRIG · in 23', '#2e1065', '#a78bfa', tfs=13.5)
add(f'<path d="M{PX+PW},{prow(5)} C860,{prow(5)} 900,450 960,449" fill="none" '
    f'stroke="#a78bfa" stroke-width="2" marker-end="url(#ar)"/>')
arrow(1065, 412, 1075, 364, '#a78bfa')  # J10 -> CW308

# ---------- debug taps -> LEDs/J1 -> readback ----------
box(570, 660, 330, 110, '', '', '#7f1d1d', '#f87171')
label(735, 684, 'LEDs + J1 (SPI_DBG) — live monitor', '#fecaca', 12.5, 'middle', 800)
label(735, 704, 'D1 alive heartbeat — yellow-green', '#fca5a5', 9, 'middle', 600)
label(735, 719, 'D2 · D4–D6 debug taps (yellow) · D3 spare_io (orange)', '#fca5a5', 9, 'middle', 600)
label(735, 734, 'D7–D10 reset LEDs — red, VDDIO-sourced,', '#fca5a5', 9, 'middle', 600)
label(735, 749, 'lit while reset asserted (LOW)', '#fca5a5', 9, 'middle', 600)
add(f'<path d="M{PX+PW},{prow(4)} C856,415 858,600 826,656" fill="none" '
    f'stroke="#fb923c" stroke-width="2" marker-end="url(#ar)"/>')
# readback loop J1/X1 -> S1 -> MCP2210
box(380, 660, 140, 66, 'S1  DIP×7', 'read-back enables', '#052e2b', '#14b8a6', tfs=13)
arrow(570, 700, 522, 700, '#fb923c')          # LED/J1 -> S1
add(f'<path d="M420,660 C380,600 235,555 235,452" fill="none" stroke="#14b8a6" '
    f'stroke-width="1.8" marker-end="url(#ar)" stroke-dasharray="5 3"/>')
label(300, 592, 'X1/GPIO read-back', '#5eead4', 9, 'middle', 600)

# ---------- clock-select block (compact; full tree in the clock diagram) ----------
KX, KY, KW, KH = 40, 770, 500, 246
add(f'<rect x="{KX}" y="{KY}" width="{KW}" height="{KH}" rx="10" fill="#0e1729" '
    f'stroke="#22d3ee" stroke-width="2"/>')
label(KX+KW/2, KY+22, 'Clock Select — J12 → SYSCLK_P (pin 9)', '#a5f3fc', 13, 'middle', 800)
label(KX+KW/2, KY+35, '(compact view — see the clock-tree diagram for details)', '#67e8f9', 7.5, 'middle', 500)
box(60, 812, 170, 44, 'J11 SMA jack', 'external clock (silk: BNC)', '#083344', '#22d3ee', tfs=11.5)
box(60, 864, 170, 44, 'Y1 50 MHz osc', 'VDDIO · via R22 20 Ω', '#083344', '#22d3ee', tfs=11.5)
box(60, 916, 170, 44, 'CW clock', 'via J7.3 CLKFB + R8 100 Ω', '#0b213f', '#38bdf8', tfs=11.5)
mk = mux(300, 812, 90, 72, 'J12', 'CLK')
arrow(230, 834, mk[0], mk[1], '#22d3ee', 'arc')
arrow(230, 886, mk[0], mk[3], '#22d3ee', 'arc')
label(263, 826, 'link 1-2', '#c4b5fd', 8, 'middle', 700)
label(259, 897, 'link 3-4', '#c4b5fd', 8, 'middle', 700)
# CW clock is the third J12 mux input (link 5-6)
add('<path d="M230,938 C272,938 290,908 300,880" fill="none" stroke="#38bdf8" '
    'stroke-width="2" marker-end="url(#arb)"/>')
label(238, 956, 'link 5-6 · CW clock via J7.3 + R8', '#7dd3fc', 7.5, 'start', 600)
# J12 out -> node -> chip clock row
add(f'<line x1="{mk[4]}" y1="{mk[5]}" x2="460" y2="848" stroke="#22d3ee" stroke-width="2"/>')
add(f'<path d="M460,848 C505,848 535,700 571,{prow(8)+5}" fill="none" stroke="#22d3ee" '
    f'stroke-width="2" marker-end="url(#arc)"/>')
# permanent clock echo out to the CW (J7.5 — not a jumper, not a source)
add('<line x1="462" y1="852" x2="374" y2="958" stroke="#22d3ee" stroke-width="1.6" '
    'stroke-dasharray="4 3" marker-end="url(#arc)"/>')
label(374, 974, 'J7.5: clock echo to CW (always)', '#a5f3fc', 9, 'middle', 700)
label(60, 990, '• fit exactly ONE J12 source link — 1-2 · 3-4 · 5-6', '#fbbf24', 8.6, 'start', 600)
label(60, 1004, '• leave CW308 J3 unpopulated — it drives J7.5 (clock echo) and would fight the source', '#fbbf24', 8.6, 'start', 600)

# ---------- power block ----------
box(900, 540, 480, 316, '', '', '#1c1917', '#f59e0b')
label(1140, 566, 'Vcore Power &amp; Current-Sense Path', '#fdba74', 13, 'middle', 800)
label(991, 584, 'VDDIO in', '#fdba74', 8, 'middle', 600)
box(916, 590, 150, 60, 'U2 TPS74801', 'trim R20/R21: 0.80–0.90 V', '#3f1d0b', '#b45309', tfs=12)
box(1102, 590, 74, 60, 'JP7', 'route select', '#3f1d0b', '#b45309', tfs=12)
box(1204, 590, 74, 60, 'R7 0.01 Ω', 'shunt', '#3f1d0b', '#b45309', tfs=11)
box(1302, 590, 66, 60, 'Vcore', 'pins 15/28', '#0e1729', '#0891b2', tfs=11)
arrow(1066, 608, 1102, 608, '#f59e0b', 'aro')
label(1084, 583, 'link 2-3 · direct', '#fdba74', 8, 'middle', 700)
arrow(1176, 620, 1204, 620, '#f59e0b', 'aro')
arrow(1278, 620, 1302, 620, '#f59e0b', 'aro')
box(960, 682, 220, 46, 'CW308 L-C filter', 'J8.8 FILTIN → return J8.5/J8.6', '#3f1d0b', '#b45309', tfs=11.5)
arrow(990, 650, 990, 682, '#f59e0b', 'aro')
arrow(1140, 682, 1140, 650, '#f59e0b', 'aro')
label(1065, 670, 'link 1-2 · filtered', '#fdba74', 8, 'middle', 700)
label(1140, 752, 'CW308 reads ΔV across R7 (J8.3 SHUNTH / J8.2 SHUNTL) → power trace', '#fdba74', 9, 'middle', 600)
label(1140, 768, 'die-side Vcore net is capacitor-free → shunt sees instantaneous die current', '#fdba74', 9, 'middle', 600)
add('<rect x="920" y="786" width="440" height="54" rx="9" fill="#3f1d0b" stroke="#fbbf24" '
    'stroke-width="2"/>')
label(1140, 808, 'SET 0.8 V BEFORE INSERTING THE CHIP', '#fde68a', 12, 'middle', 800)
label(1140, 826, 'trim R20 · measure at the Vcore test point · JP7 at 2-3', '#fdba74', 9.5, 'middle', 600)
# 3.3V -> VDDIO
add(f'<path d="M1030,360 C930,420 806,466 792,{prow(7)-5}" fill="none" '
    f'stroke="#f59e0b" stroke-width="2" marker-end="url(#aro)"/>')
label(940, 506, 'CW308 J8.14 · 3.3 V', '#fdba74', 9, 'middle', 600)
label(940, 520, '→ VDDIO 7/22 · bridges · U2', '#fdba74', 9, 'middle', 600)
# Vcore -> PROACT power group
add(f'<path d="M1335,586 C1150,470 900,560 794,{prow(7)+7}" fill="none" '
    f'stroke="#f59e0b" stroke-width="1.8" marker-end="url(#aro)" stroke-dasharray="4 3"/>')
label(1100, 512, 'Vcore 0.8 V → pins 15/28', '#fdba74', 9, 'middle', 600)

# ---------- legend ----------
ly = H - 20
items = [('#14b8a6', 'UART'), ('#3b82f6', 'SPI'), ('#f87171', 'Reset'),
         ('#fb923c', 'Debug'), ('#a78bfa', 'Trigger / jumper'), ('#f59e0b', 'Power'),
         ('#22d3ee', 'Clock'), ('#38bdf8', 'CW308 bus')]
lx = 30
for c, n in items:
    add(f'<rect x="{lx}" y="{ly-11}" width="14" height="14" rx="3" fill="{c}"/>')
    label(lx+20, ly, n, '#cbd5e1', 10, 'start', 600)
    lx += 90 + len(n) * 5

add('</svg>')
open('/home/abish/Downloads/PCB/PROACT_DOC/docs/img/architecture.svg', 'w').write('\n'.join(S))
print('wrote architecture.svg')
