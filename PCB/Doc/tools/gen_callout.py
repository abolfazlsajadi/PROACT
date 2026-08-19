#!/usr/bin/env python3
"""Annotated board render with numbered callouts. Calibratable mm->px transform."""
import base64, csv, sys, os

ROOT = '/home/abish/Downloads/PCB/PROACT_DOC'
REND = os.path.join(ROOT, 'docs/img/board_top_render.png')
IMGW, IMGH = 713, 739

# ---- transform: px = A*mm + B  (Y flipped) ----
AX, BX = float(sys.argv[1]) if len(sys.argv) > 1 else 10.72, \
         float(sys.argv[2]) if len(sys.argv) > 2 else 101.5
AY, BY = float(sys.argv[3]) if len(sys.argv) > 3 else -10.72, \
         float(sys.argv[4]) if len(sys.argv) > 4 else 696.0
MODE = sys.argv[5] if len(sys.argv) > 5 else 'calib'

def px(mx, my):
    return AX * mx + BX, AY * my + BY

# pick-and-place centers
PP = {}
for line in open(os.path.join(ROOT, 'PCB/Project Outputs for PCB_Project/Pick Place for PCB1.csv')):
    if not line.startswith('"'):
        continue
    p = [c.strip('"') for c in line.rstrip('\n').split('","')]
    p = [c.strip('"') for c in p]
    if p[0] == 'Designator':
        continue
    try:
        PP[p[0]] = (float(p[4].replace(',', '.')), float(p[5].replace(',', '.')))
    except (ValueError, IndexError):
        continue

CAL = ['U1', 'S1', 'SW1', 'J1', 'J2', 'J5', 'JP6', 'JP4', 'D1', 'CLK', 'Vcore', 'GND', 'VDDIO', 'J10', 'J6', 'JP1']

b64 = base64.b64encode(open(REND, 'rb').read()).decode()

SC = 2
IMW, IMH = IMGW * SC, IMGH * SC
TITLE_H = 84
LEG_H = 430
W = IMW
H = TITLE_H + IMH + LEG_H
S = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="Inter,Segoe UI,Arial,sans-serif">']
S.append(f'<rect width="{W}" height="{H}" fill="#0b1220"/>')
# title
S.append(f'<text x="{W/2}" y="40" font-size="30" font-weight="800" text-anchor="middle" fill="#f8fafc">PROACT Evaluation Board — Annotated Layout</text>')
S.append(f'<text x="{W/2}" y="68" font-size="16" font-weight="600" text-anchor="middle" fill="#94a3b8">Top view · numbered callouts registered to the assembly render · see index below</text>')
# image (shifted down by title)
S.append(f'<g transform="translate(0,{TITLE_H})">')
S.append(f'<image href="data:image/png;base64,{b64}" x="0" y="0" width="{IMW}" height="{IMH}"/>')

def mid(*ds):
    xs = [PP[d][0] for d in ds]; ys = [PP[d][1] for d in ds]
    return sum(xs)/len(xs), sum(ys)/len(ys)

# category colors (match locator legend)
CC = {'chip':'#f8fafc','conn':'#3b82f6','jumper':'#a78bfa','switch':'#2dd4bf',
      'led':'#f87171','power':'#fbbf24','tp':'#22d3ee'}

# callouts: (num, mm-point, label, category, badge dx, badge dy)  dx/dy in render px (pre-scale)
CALL = [
    (1,  PP['U1'],            'U1 — PROACT DIP-28 socket',        'chip',   70, -6),
    (2,  PP['J1'],            'J1 — SPI_DBG monitor (2×5)',       'conn',   -6, 44),
    (3,  mid('J2','J3'),      'J2/J3 — MCP2200 UART module',      'conn',  -70, -30),
    (4,  mid('J4','J5'),      'J4/J5 — MCP2210 SPI module',       'conn',   58, -34),
    (5,  PP['J6'],            'J6 — S-Sel / trigger-in route',    'conn',   -2, -34),
    (6,  PP['J10'],           'J10 — trigger select',             'conn',  -46, -8),
    (7,  PP['JP1'],           'JP1 — reset ↔ CW nRST',            'jumper', -44, 4),
    (8,  PP['JP2'],           'JP2 — IBEX PC[2:4] probe',         'jumper', -44, 8),
    (9,  PP['JP3'],           'JP3 — UART select (silk CW/M RX)', 'jumper', -50, -2),
    (10, PP['JP5'],           'JP5 — UART select (silk CW/M TX)', 'jumper',  4, -34),
    (11, PP['JP6'],           'JP6 — SPI MOSI select (M/CW)',     'jumper', 66, -2),
    (12, PP['JP4'],           'JP4 — SPI SCK select (M/CW)',      'jumper', 66, 4),
    (13, PP['S1'],            'S1 — DIP×7 read-back / power',     'switch', 40, -30),
    (14, PP['SW1'],           'SW1 — B_RST push-button',          'switch', 44, 6),
    (15, PP['D1'],            'D1 — “alive” heartbeat LED',       'led',    52, 6),
    (16, mid('D2','D6'),      'D2–D6 — debug LEDs',               'led',    56, 0),
    (17, PP['U2'],            'U2 — TPS74801 core LDO (0.8 V)',   'power', -60, 28),
    (18, PP['R7'],            'R7 — 0.01 Ω current-sense shunt',  'power', -46, 8),
    (19, PP['Vcore'],         'Vcore — core test point',          'tp',    -50, 6),
    (20, PP['VDDIO'],         'VDDIO — 3.3 V test point',         'tp',     50, 4),
    (21, PP['GND'],           'GND — ground test point',          'tp',     46, 4),
    (22, PP['CLK'],           'CLK — clock test point',           'tp',     -6, 40),
]

if MODE == 'calib':
    for d in CAL:
        if d not in PP:
            continue
        x, y = px(*PP[d]); x*=SC; y*=SC
        S.append(f'<line x1="{x-10}" y1="{y}" x2="{x+10}" y2="{y}" stroke="red" stroke-width="1.5"/>')
        S.append(f'<line x1="{x}" y1="{y-10}" x2="{x}" y2="{y+10}" stroke="red" stroke-width="1.5"/>')
        S.append(f'<text x="{x+8}" y="{y-6}" font-size="15" font-weight="700" fill="yellow" '
                 f'stroke="black" stroke-width="3" style="paint-order:stroke">{d}</text>')
    S.append('</svg>')
    open(os.path.join(ROOT, 'docs/img/_callout_calib.svg'), 'w').write('\n'.join(S))
    print('calib', AX, BX, AY, BY); sys.exit()

# ---- FINAL callout render ----
for num, mm, lbl, cat, dx, dy in CALL:
    cxr, cyr = px(*mm)
    cx, cy = cxr*SC, cyr*SC
    bx, by = (cxr+dx)*SC, (cyr+dy)*SC
    col = CC[cat]
    # leader line badge->component
    S.append(f'<line x1="{bx}" y1="{by}" x2="{cx}" y2="{cy}" stroke="#0b1220" stroke-width="4.5"/>')
    S.append(f'<line x1="{bx}" y1="{by}" x2="{cx}" y2="{cy}" stroke="{col}" stroke-width="2"/>')
    S.append(f'<circle cx="{cx}" cy="{cy}" r="4.5" fill="{col}" stroke="#0b1220" stroke-width="2"/>')
    # badge
    S.append(f'<circle cx="{bx}" cy="{by}" r="15" fill="{col}" stroke="#0b1220" stroke-width="2.5"/>')
    tcol = '#0b1220'
    S.append(f'<text x="{bx}" y="{by+5.5}" font-size="16" font-weight="800" text-anchor="middle" '
             f'fill="{tcol}">{num}</text>')

S.append('</g>')  # close image group

# ---- legend index panel (grouped, 3 columns) ----
ly0 = TITLE_H + IMH + 8
S.append(f'<rect x="24" y="{ly0}" width="{W-48}" height="{LEG_H-24}" rx="12" fill="#0e1729" stroke="#1e293b" stroke-width="1.5"/>')
S.append(f'<text x="44" y="{ly0+34}" font-size="17" font-weight="800" fill="#e2e8f0">Callout index</text>')

by_cat = {}
for num, mm, lbl, cat, dx, dy in CALL:
    by_cat.setdefault(cat, []).append((num, lbl))

# arrange into 3 columns
cats_order = ['chip', 'conn', 'jumper', 'switch', 'led', 'power', 'tp']
cat_title = {'chip':'Target', 'conn':'Connectors / headers', 'jumper':'Configuration jumpers',
             'switch':'Switches', 'led':'LEDs', 'power':'Power / sense', 'tp':'Test points'}
# build flat list of (kind, payload)
seq = []
for c in cats_order:
    if c not in by_cat:
        continue
    seq.append(('hdr', c))
    for it in by_cat[c]:
        seq.append(('item', (c, it)))

ncol = 3
per = (len(seq) + ncol - 1) // ncol
colx = [44, 44 + (W-88)/3, 44 + 2*(W-88)/3]
rowh = 30
ytop = ly0 + 58
for i, (kind, pay) in enumerate(seq):
    col = i // per
    row = i % per
    x = colx[col]
    y = ytop + row * rowh
    if kind == 'hdr':
        S.append(f'<text x="{x}" y="{y}" font-size="12.5" font-weight="800" fill="#64748b" '
                 f'letter-spacing="0.06em">{cat_title[pay].upper()}</text>')
    else:
        cat, (num, lbl) = pay
        col_c = CC[cat]
        S.append(f'<circle cx="{x+11}" cy="{y-5}" r="12" fill="{col_c}" stroke="#0b1220" stroke-width="2"/>')
        S.append(f'<text x="{x+11}" y="{y-0.5}" font-size="13" font-weight="800" text-anchor="middle" fill="#0b1220">{num}</text>')
        S.append(f'<text x="{x+30}" y="{y}" font-size="13.5" font-weight="600" fill="#cbd5e1">{lbl}</text>')

S.append('</svg>')
open(os.path.join(ROOT, 'docs/img/board_annotated.svg'), 'w').write('\n'.join(S))
print('final', len(CALL), 'callouts -> docs/img/board_annotated.svg')
