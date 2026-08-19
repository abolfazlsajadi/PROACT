#!/usr/bin/env python3
"""Generate an accurate SVG assembly/locator drawing from Altium pick-and-place data."""
import csv, math

PNP = '/home/abish/Downloads/PCB/PROACT_DOC/PCB/Project Outputs for PCB_Project/Pick Place for PCB1.csv'

# ---- read pick & place ----
rows = []
with open(PNP) as f:
    for line in f:
        if not line.startswith('"'):
            continue
        p = [c.strip().strip('"') for c in line.rstrip('\n').split('","')]
        p = [c.strip('"') for c in p]
        if p[0] == 'Designator':
            continue
        try:
            des, cmt, layer, fp = p[0], p[1], p[2], p[3]
            cx = float(p[4].replace(',', '.'))
            cy = float(p[5].replace(',', '.'))
            rot = float(p[6].replace(',', '.'))
        except (ValueError, IndexError):
            continue
        rows.append(dict(des=des, cmt=cmt, layer=layer, fp=fp, x=cx, y=cy, rot=rot))

# ---- world bounds (board outline from GM13 mechanical) ----
xmin, xmax = -1.9, 50.3
ymin, ymax = -2.2, 72.7
PAD = 4
TOP = 5.0    # extra top space (mm) for title
BOT = 8.5    # extra bottom space (mm) for legend
SCALE = 11   # px per mm
W = (xmax - xmin + 2 * PAD) * SCALE
H = (ymax - ymin + 2 * PAD + TOP + BOT) * SCALE

def tx(x):
    return (x - xmin + PAD) * SCALE
def ty(y):
    return (ymax - y + PAD + TOP) * SCALE  # flip Y, offset for title band

# footprint nominal sizes in mm (w = along pin row / body-x before rotation, h)
SIZES = {
    'DIP28_sckt': (16.0, 36.0),
    'HDR_M_1X7': (2.8, 17.8),
    'HDR_F_1X20': (2.8, 50.8),
    'HDR_F_2X3': (5.6, 7.9),
    'HDR_M_2X5': (5.6, 13.0),
    'DIPSW254S_7': (10.0, 18.5),
    'JP_3': (2.8, 8.0),
    'JP_2_BLK': (2.8, 5.4),
    'SWPBS_SPDT_5X5X1': (6.0, 6.0),
    'DRC0010A': (3.2, 3.2),
    'C1210': (3.2, 1.8),
    'C0805': (2.0, 1.3),
    'R0805': (2.0, 1.3),
    'R1206': (3.2, 1.8),
    'LED0805RED': (2.0, 1.3),
    'TPTHD_C_BLK1': (3.4, 3.4),
}

# functional classification & colors
def klass(r):
    d = r['des']
    if d == 'U1':
        return 'chip'
    if d == 'U2':
        return 'power'
    if d.startswith('J') and not d.startswith('JP'):
        return 'conn'
    if d.startswith('JP'):
        return 'jumper'
    if d.startswith('S1') or d.startswith('SW'):
        return 'switch'
    if d.startswith('D'):
        return 'led'
    if d in ('Vcore', 'VDDIO', 'GND', 'CLK'):
        return 'tp'
    if d.startswith('R'):
        return 'res'
    if d.startswith('C'):
        return 'cap'
    return 'other'

COL = {
    'chip':   ('#1e293b', '#f8fafc'),
    'power':  ('#b45309', '#fff7ed'),
    'conn':   ('#1d4ed8', '#eff6ff'),
    'jumper': ('#7c3aed', '#f5f3ff'),
    'switch': ('#0f766e', '#f0fdfa'),
    'led':    ('#dc2626', '#fef2f2'),
    'tp':     ('#0891b2', '#ecfeff'),
    'res':    ('#64748b', '#f1f5f9'),
    'cap':    ('#94a3b8', '#f8fafc'),
    'other':  ('#94a3b8', '#f8fafc'),
}

# human labels for key parts
LABELS = {
    'U1': 'PROACT\n(DIP-28)', 'U2': 'U2 LDO', 'S1': 'S1 DIP×7',
    'SW1': 'SW1\nB_RST', 'J1': 'J1 SPI_DBG', 'J2': 'J2', 'J3': 'J3',
    'J4': 'J4', 'J5': 'J5', 'J6': 'J6\nS-Sel', 'J10': 'J10\nTRIG',
    'J7': 'J7 (CW-W)', 'J8': 'J8 (CW-E)', 'J9': 'J9 (CW-S)',
    'JP1': 'JP1', 'JP2': 'JP2', 'JP3': 'JP3', 'JP4': 'JP4', 'JP5': 'JP5', 'JP6': 'JP6',
    'R7': 'R7 shunt', 'Vcore': 'Vcore', 'VDDIO': 'VDDIO', 'GND': 'GND', 'CLK': 'CLK',
    'D1': 'D1', 'D2': 'D2', 'D3': 'D3', 'D4': 'D4', 'D5': 'D5', 'D6': 'D6',
}

DRAW_ORDER = ['cap', 'res', 'led', 'tp', 'power', 'switch', 'jumper', 'conn', 'chip']

svg = []
svg.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" '
           f'font-family="Inter, Segoe UI, Arial, sans-serif">')
svg.append(f'<rect x="0" y="0" width="{W:.0f}" height="{H:.0f}" fill="#0b1220"/>')

# board outline (rounded rect w/ mounting ears simplified)
bx0, by0 = tx(xmin), ty(ymax)
bx1, by1 = tx(xmax), ty(ymin)
svg.append(f'<rect x="{bx0:.1f}" y="{by0:.1f}" width="{bx1-bx0:.1f}" height="{by1-by0:.1f}" '
           f'rx="14" fill="#0e4b34" stroke="#1f7a54" stroke-width="2"/>')
# subtle silk grid frame
svg.append(f'<rect x="{bx0+6:.1f}" y="{by0+6:.1f}" width="{bx1-bx0-12:.1f}" height="{by1-by0-12:.1f}" '
           f'rx="10" fill="none" stroke="#12603f" stroke-width="1"/>')

# ---- USB bridge module footprint regions (span each 1x7 header pair) ----
def module_box(desA, desB, title, sub):
    a = next(r for r in rows if r['des'] == desA)
    b = next(r for r in rows if r['des'] == desB)
    x0 = min(tx(a['x']), tx(b['x'])) - 8
    x1 = max(tx(a['x']), tx(b['x'])) + 8
    ytop = ty(max(a['y'], b['y']) + 9.5)
    ybot = ty(min(a['y'], b['y']) - 9.5)
    svg.append(f'<rect x="{x0:.1f}" y="{ytop:.1f}" width="{x1-x0:.1f}" height="{ybot-ytop:.1f}" '
               f'rx="4" fill="#0b1220" fill-opacity="0.28" stroke="#38bdf8" '
               f'stroke-width="1.4" stroke-dasharray="5 3"/>')
    xm = (x0 + x1) / 2
    svg.append(f'<text x="{xm:.1f}" y="{ytop+13:.1f}" font-size="10.5" font-weight="800" '
               f'text-anchor="middle" fill="#e0f2fe" stroke="#0b1220" stroke-width="3" '
               f'style="paint-order:stroke">{title}</text>')
    svg.append(f'<text x="{xm:.1f}" y="{ytop+25:.1f}" font-size="8" font-weight="600" '
               f'text-anchor="middle" fill="#7dd3fc" stroke="#0b1220" stroke-width="2.5" '
               f'style="paint-order:stroke">{sub}</text>')

module_box('J2', 'J3', 'MCP2200', 'USB↔UART')
module_box('J4', 'J5', 'MCP2210', 'USB↔SPI/GPIO')

def emit_part(r):
    k = klass(r)
    stroke, fill = COL[k]
    w, h = SIZES.get(r['fp'], (2.0, 2.0))
    # rotation: 90/270 swaps w/h visually
    rot = r['rot'] % 360
    cxp, cyp = tx(r['x']), ty(r['y'])
    wp, hp = w * SCALE, h * SCALE
    g = f'<g transform="translate({cxp:.1f},{cyp:.1f}) rotate({-rot:.0f})">'
    body = (f'<rect x="{-wp/2:.1f}" y="{-hp/2:.1f}" width="{wp:.1f}" height="{hp:.1f}" '
            f'rx="2" fill="{fill}" stroke="{stroke}" stroke-width="1.5" opacity="0.96"/>')
    svg.append(g + body + '</g>')

# draw non-labeled small parts first (faint), then key parts
key = set(LABELS.keys())
for k in DRAW_ORDER:
    for r in rows:
        if klass(r) != k:
            continue
        if r['des'] in key:
            continue
        emit_part(r)

for k in DRAW_ORDER:
    for r in rows:
        if klass(r) != k or r['des'] not in key:
            continue
        emit_part(r)

# labels (drawn last, on top, with halo)
LBL_DY = {'J3': -10, 'J4': 10}  # nudge overlapping inner headers apart

def label(r):
    txt = LABELS.get(r['des'])
    if not txt:
        return
    cxp, cyp = tx(r['x']), ty(r['y']) + LBL_DY.get(r['des'], 0)
    lines = txt.split('\n')
    fs = 11 if r['des'] == 'U1' else 9
    dy0 = -(len(lines) - 1) * (fs + 1) / 2
    for i, ln in enumerate(lines):
        yy = cyp + dy0 + i * (fs + 1) + fs / 2 - 1
        svg.append(f'<text x="{cxp:.1f}" y="{yy:.1f}" font-size="{fs}" font-weight="700" '
                   f'text-anchor="middle" fill="#f8fafc" '
                   f'stroke="#0b1220" stroke-width="3" paint-order="stroke" '
                   f'style="paint-order:stroke">{ln}</text>')

for r in rows:
    label(r)

# ---- title band ----
svg.append(f'<text x="{W/2:.1f}" y="30" font-size="20" font-weight="800" text-anchor="middle" '
           f'fill="#f8fafc">PROACT Evaluation Board — Component Locator</text>')
svg.append(f'<text x="{W/2:.1f}" y="48" font-size="11.5" font-weight="600" text-anchor="middle" '
           f'fill="#94a3b8">Top view · generated from assembly (pick-and-place) coordinates · '
           f'CW = ChipWhisperer CW308 edge connector</text>')

# ---- legend ----
legend = [('chip', 'PROACT'), ('conn', 'Connector'), ('jumper', 'Jumper'),
          ('switch', 'Switch'), ('led', 'LED'), ('power', 'Power'), ('tp', 'Test pt')]
lx = 34
ly = H - 52
svg.append(f'<rect x="20" y="{ly-20:.1f}" width="{W-40:.1f}" height="60" rx="8" '
           f'fill="#0e1729" stroke="#1e293b" stroke-width="1"/>')
per = (W - 60) / len(legend)
for i, (k, name) in enumerate(legend):
    stroke, fill = COL[k]
    x = lx + i * per
    svg.append(f'<rect x="{x:.1f}" y="{ly-6:.1f}" width="15" height="15" rx="3" '
               f'fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
    svg.append(f'<text x="{x+20:.1f}" y="{ly+5:.1f}" font-size="10" font-weight="600" '
               f'fill="#cbd5e1">{name}</text>')

svg.append('</svg>')
open('/home/abish/Downloads/PCB/PROACT_DOC/docs/img/board_locator.svg', 'w').write('\n'.join(svg))
print("wrote board_locator.svg", int(W), int(H))
