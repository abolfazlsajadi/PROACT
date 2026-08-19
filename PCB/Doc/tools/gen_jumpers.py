#!/usr/bin/env python3
"""Jumper cards for the PROACT evaluation board.

For every configuration jumper: WHERE it sits (mini board map, true pad positions,
pin-1 dot) and WHAT each shunt position does (per-position diagram + effect line).
Connectivity is taken from the final extracted netlist (see jumper_agent_notes.md)
and is NOT derived here -- only placements/pads come from the PcbDoc.

Outputs docs/img/jumpers/{JP1,JP3_JP5,JP4_JP6,JP7,J6,J10,J12,JP2,overview}.{svg,png}
"""
import olefile, os, subprocess

PCB = '/home/abish/Downloads/PCB/PROACT_DOC/PCB_finalx/PCB_Projectcxfinal/PCB1.PcbDoc'
OUT = '/home/abish/Downloads/PCB/PROACT_DOC/docs/img/jumpers'
os.makedirs(OUT, exist_ok=True)

# ---------------------------------------------------------------- extraction --
ole = olefile.OleFileIO(PCB)

def u32(d, o): return int.from_bytes(d[o:o+4], 'little')

# components (order matters: Pads6 references components by record index)
d = ole.openstream('Components6/Data').read(); off = 0; COMPS = []
while off + 4 <= len(d):
    ln = u32(d, off); off += 4
    if ln <= 0 or off + ln > len(d): break
    f = dict(p.split('=', 1) for p in d[off:off+ln].decode('latin1', 'replace').split('|') if '=' in p)
    off += ln
    mil = lambda v: float(v.replace('mil', '')) * 0.0254
    COMPS.append(dict(des=f.get('SOURCEDESIGNATOR'), x=mil(f.get('X', '0mil')),
                      y=mil(f.get('Y', '0mil')), rot=float(f.get('ROTATION', '0')),
                      lib=f.get('SOURCELIBREFERENCE')))
CBY = {c['des']: c for c in COMPS}

# pads: name / owner component / x / y  (geometry block: comp u16@7, x i32@13, y i32@17)
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
    if comp < len(COMPS):
        PADS.setdefault(COMPS[comp]['des'], {})[name] = (x, y)
    if off < len(d) and d[off] != 2:            # optional trailing block
        bl = u32(d, off)
        if 0 <= bl < len(d) - off: off += 4 + bl

# board outline (Board6 vertices)
BX0, BX1, BY0, BY1 = 319.40, 373.25, 196.72, 290.07

# ---------------------------------------------------------------- style -------
BG, PANEL, PANEL2 = '#0b1220', '#0e1729', '#111827'
PBORD, TXT, MUT, DIM = '#1e293b', '#f1f5f9', '#94a3b8', '#64748b'
FONT = 'Inter,Segoe UI,Arial,sans-serif'
CAT = dict(uart='#14b8a6', spi='#3b82f6', reset='#f87171', trigger='#a78bfa',
           power='#f59e0b', clock='#22d3ee', module='#14b8a6', cw='#38bdf8',
           probe='#e2e8f0')
CHIPC = '#f1f5f9'

SIZE = {  # w,h mm in the FOOTPRINT LIBRARY 0-deg frame (w = X extent at ROTATION=0),
          # same dict as gen_v2_view.py -- verified against Pads6 pad bounding boxes.
        'OSC_4_50MHz_3225E': (3.2, 2.5), 'CONN_RF_BNC_RA': (9, 13), 'ResPot': (6.8, 4.8),
        'HDR_F_2X3': (7.9, 5.6), 'JUMPER_HDR_3P': (7.6, 2.6), 'HDR_M_1X7': (17.8, 2.6),
        'HDR_F_1X20': (50.8, 2.6), 'HDR_M_2X5': (12.7, 5.1), 'JUMPER_HDR_2P': (5.1, 2.6),
        'DIPSW254S_7P': (10.0, 18.5), 'SOCKET_IC_DIP_28': (15.5, 36.0),
        'TPS74801DRCR': (3.2, 3.2), 'SWPBS_SPDT_5X5X1_SKQGAFE010': (6, 6),
        'LED_SMD_0805_RED': (2.0, 1.3), 'cap_SMD': (2.0, 1.3), 'Res_SMD': (2.0, 1.3),
        'TESTPOINT_BIG_BLK1': (3.4, 3.4)}

def placed_size(lib, rot):
    """Drawn extents after applying the component ROTATION: 90/270 swap w/h."""
    w, h = SIZE.get(lib, (2, 2))
    if 45 <= round(rot) % 180 < 135: w, h = h, w
    return w, h

# ---------------------------------------------------------------- helpers -----
def R(S, x, y, w, h, rx, fill, stroke=None, sw=1.2, opac=None, dash=None):
    s = f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx:.1f}" fill="{fill}"'
    if stroke: s += f' stroke="{stroke}" stroke-width="{sw}"'
    if opac is not None: s += f' opacity="{opac}"'
    if dash: s += f' stroke-dasharray="{dash}"'
    S.append(s + '/>')

def L(S, x1, y1, x2, y2, stroke, sw=1.5, dash=None, opac=None):
    s = f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}" stroke-width="{sw}"'
    if dash: s += f' stroke-dasharray="{dash}"'
    if opac is not None: s += f' opacity="{opac}"'
    S.append(s + '/>')

def T(S, x, y, txt, size, fill, weight=600, anchor='middle', halo=None, rot=None):
    pos = (f'transform="translate({x:.1f},{y:.1f}) rotate({rot})"' if rot is not None
           else f'x="{x:.1f}" y="{y:.1f}"')
    h = (f' stroke="{halo}" stroke-width="{max(2.2, size*0.30):.1f}" style="paint-order:stroke"'
         if halo else '')
    S.append(f'<text {pos} font-size="{size}" font-weight="{weight}" '
             f'text-anchor="{anchor}" fill="{fill}"{h}>{txt}</text>')

def arrow(S, x1, x2, y, color, sw=2.2):
    L(S, x1, y, x2 - 7, y, color, sw)
    S.append(f'<polygon points="{x2-9:.1f},{y-4.5:.1f} {x2:.1f},{y:.1f} {x2-9:.1f},{y+4.5:.1f}" fill="{color}"/>')

def flow(S, cx, y, ltxt, lcol, rtxt, rcol, acol):
    T(S, cx - 42, y + 4, ltxt, 11, lcol, 700, 'end')
    arrow(S, cx - 34, cx + 34, y, acol)
    T(S, cx + 42, y + 4, rtxt, 11, rcol, 700, 'start')

def pill(S, cx, y, txt, color, solid=False):
    w = len(txt) * 6.1 + 20
    if solid:
        R(S, cx - w/2, y - 11, w, 22, 11, color)
        T(S, cx, y + 4.5, txt, 10.5, '#0b1220', 800)
    else:
        R(S, cx - w/2, y - 11, w, 22, 11, 'none', color, 1.4)
        T(S, cx, y + 4.5, txt, 10.5, color, 700)

# ---------------------------------------------------------------- mini map ----
LMK = [('U1 PROACT', 346.07, 224.28, None, 9.0, MUT),
       ('J7 CW-W', 322.20, 227.58, -90, 7.5, DIM),
       ('J8 CW-E', 370.46, 227.58, -90, 7.5, DIM),
       ('J9 CW-S', 346.20, 199.6, None, 7.5, DIM),
       ('MCP2200', 335.98, 279.5, -90, 7.0, DIM),
       ('MCP2210', 357.85, 279.5, -90, 7.0, DIM)]

def minimap(S, px, py, pw, ph, focus, ctx=(), scale=4.7):
    """focus: [(ref,color,(dx,dy,anchor))]  ctx: [(ref,label,color,(dx,dy,anchor))]"""
    k = scale / 4.7
    R(S, px, py, pw, ph, 10, PANEL, PBORD, 1.2)
    bw, bh = (BX1 - BX0) * scale, (BY1 - BY0) * scale
    ox, oy = px + (pw - bw) / 2, py + 12 + (ph - 40 - bh) / 2
    mx = lambda x: ox + (x - BX0) * scale
    my = lambda y: oy + (BY1 - y) * scale
    R(S, ox, oy, bw, bh, 5 * k, '#0e4b34', '#1f7a54', 1.6)
    for c in COMPS:                                     # faint footprints (rotation-corrected)
        w, h = placed_size(c['lib'], c['rot'])
        wp, hp = w * scale, h * scale
        S.append(f'<rect x="{mx(c["x"])-wp/2:.1f}" y="{my(c["y"])-hp/2:.1f}" '
                 f'width="{wp:.1f}" height="{hp:.1f}" rx="1.5"'
                 f' fill="#10573c" stroke="#2f6f52" stroke-width="1"/>')
    for txt, x, y, rot, fs, col in LMK:
        T(S, mx(x), my(y) + (0 if rot else fs * k * 0.35), txt, fs * k, col, 700,
          'middle', '#0e4b34', rot)
    for ref, label, color, (dx, dy, anc) in ctx:        # secondary highlights
        c = CBY[ref]; w, h = placed_size(c['lib'], c['rot'])
        R(S, mx(c['x']) - w*scale/2 - 2, my(c['y']) - h*scale/2 - 2,
          w*scale + 4, h*scale + 4, 3, 'none', color, 1.5, opac=0.65)
        if label:
            T(S, mx(c['x'] + dx), my(c['y'] + dy) + 3, label, 7.5 * k, color, 700, anc, BG)
    for ref, color, (dx, dy, anc) in focus:             # focused jumper(s)
        pts = PADS[ref].values()
        x0 = min(p[0] for p in pts) - 1.7; x1 = max(p[0] for p in pts) + 1.7
        y0 = min(p[1] for p in pts) - 1.7; y1 = max(p[1] for p in pts) + 1.7
        rx0, ry0 = mx(x0), my(y1)
        R(S, rx0, ry0, (x1-x0)*scale, (y1-y0)*scale, 4, 'none', color, 7, opac=0.28)
        R(S, rx0, ry0, (x1-x0)*scale, (y1-y0)*scale, 4, 'none', color, 2.2)
        for pn, (x, y) in PADS[ref].items():
            one = (pn == '1')
            S.append(f'<circle cx="{mx(x):.1f}" cy="{my(y):.1f}" r="{2.6*k:.1f}" '
                     f'fill="{"#ffffff" if one else "#0b1220"}" stroke="{"#ffffff" if one else "#cbd5e1"}" stroke-width="1"/>')
        cxm = (x0 + x1) / 2 + dx; cym = (y0 + y1) / 2 + dy
        T(S, mx(cxm), my(cym) + 3.5, ref, 10.5 * k, color, 800, anc, BG)
    T(S, px + pw/2, py + ph - 9, 'top view · white dot = pin 1', 8.5 * k, DIM, 600)

# ---------------------------------------------------------------- diagrams ----
def row1xN(S, cx, cy, pitch, pins, link, color, pad=34):
    """pins: [(line1,line2)] ; link: (a,b) pin numbers or None"""
    n = len(pins)
    xs = [cx + (i - (n - 1) / 2) * pitch for i in range(n)]
    for i, x in enumerate(xs):
        on = link is not None and (i + 1) in link
        R(S, x - pad/2, cy - pad/2, pad, pad, 6, '#1f2937',
          color if on else DIM, 2 if on else 1.3)
    if link:
        a, b = link
        x0 = xs[a-1] - pad/2 - 5; x1 = xs[b-1] + pad/2 + 5
        R(S, x0, cy - pad/2 - 6, x1 - x0, pad + 12, 8, color, color, 2.2, opac=0.30)
        R(S, x0, cy - pad/2 - 6, x1 - x0, pad + 12, 8, 'none', color, 2.2)
    for i, x in enumerate(xs):
        on = link is not None and (i + 1) in link
        T(S, x, cy + 5, str(i + 1), 13, TXT if on else MUT, 800)
        l1, l2 = pins[i]
        T(S, x, cy + pad/2 + 17, l1, 9.5, TXT if on else DIM, 700)
        T(S, x, cy + pad/2 + 30, l2, 8.5, MUT if on else DIM, 600)
    return xs

def grid2x3(S, cx, cy, labels, links, nc=(), gx=46, gy=33, pad=26):
    """labels: {pin:(text,side_color|None,on)}  links: [((a,b),color)]  nc: dim pins"""
    P = {1: (cx - gx/2, cy - gy), 2: (cx + gx/2, cy - gy),
         3: (cx - gx/2, cy),      4: (cx + gx/2, cy),
         5: (cx - gx/2, cy + gy), 6: (cx + gx/2, cy + gy)}
    onpins = set()
    for (a, b), c in links: onpins |= {a, b}
    for pn, (x, y) in P.items():
        isnc = pn in nc
        R(S, x - pad/2, y - pad/2, pad, pad, 5, '#1f2937',
          DIM if not isnc else '#3f4a5c', 1.3, dash='3,3' if isnc else None)
    for (a, b), c in links:
        xa, ya = P[a]; xb, yb = P[b]
        x0, y0 = min(xa, xb) - pad/2 - 5, min(ya, yb) - pad/2 - 5
        w = abs(xb - xa) + pad + 10; h = abs(yb - ya) + pad + 10
        R(S, x0, y0, w, h, 8, c, c, 2.2, opac=0.30)
        R(S, x0, y0, w, h, 8, 'none', c, 2.2)
    for pn, (x, y) in P.items():
        on = pn in onpins; isnc = pn in nc
        T(S, x, y + 4.5, str(pn), 12, '#3f4a5c' if isnc else (TXT if on else MUT), 800)
        txt, col, _ = labels.get(pn, ('', None, False))
        if txt:
            side = -1 if pn % 2 else 1
            T(S, x + side * (pad/2 + 9), y + 3.5, txt, 9,
              (col or (TXT if on else DIM)) if not isnc else '#3f4a5c', 700,
              'end' if pn % 2 else 'start')
    return P

# ---------------------------------------------------------------- scaffold ----
CARD_W, CARD_H = 1000, 560
FILES = []

def new_card(color, title, sub, w=CARD_W, h=CARD_H):
    S = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" font-family="{FONT}">']
    R(S, 0, 0, w, h, 0, BG)
    R(S, 16, 14, 6, 34, 3, color)
    T(S, 34, 34, title, 21, TXT, 800, 'start')
    T(S, 34, 54, sub, 11, MUT, 600, 'start')
    T(S, w - 18, 30, 'PROACT eval board · jumper card', 9, DIM, 600, 'end')
    return S

def save(S, name, w=CARD_W, h=CARD_H):
    S.append('</svg>')
    p = os.path.join(OUT, name + '.svg')
    open(p, 'w').write('\n'.join(S))
    FILES.append((name, w, h))
    print('wrote', p)

MAPX, MAPY, MAPW, MAPH = 16, 70, 304, 458          # left mini-map panel
RX0, RW = 332, 652                                  # right area

def pos_panel(S, x, w, header, color):
    R(S, x, MAPY, w, MAPH, 10, PANEL2, PBORD, 1.2)
    T(S, x + w/2, MAPY + 30, header, 15, color, 800)

def caption(S, cx, y, lines, size=10.5, col='#cbd5e1'):
    for i, ln in enumerate(lines):
        T(S, cx, y + i * 15, ln, size, col, 600)

# ============================================================== JP1 ==========
c = CAT['reset']
S = new_card(c, 'JP1 — Reset from ChipWhisperer',
             '1×2 header · closed = CW308 nRST line reaches the chip reset')
minimap(S, MAPX, MAPY, MAPW, MAPH,
        focus=[('JP1', c, (0, 3.4, 'middle'))],
        ctx=[('SW1', 'SW1', '#fca5a5', (0, -4.4, 'middle'))])
PINS_JP1 = [('B_RST_N', 'chip 1 · SW1 · R9↑'), ('CW nRST', 'J7.11 / J9.15')]
for x, w, hdr, link in [(RX0, 320, 'CLOSED', (1, 2)), (RX0 + 332, 320, 'OPEN', None)]:
    cx = x + w/2
    pos_panel(S, x, w, hdr, c)
    row1xN(S, cx, 150, 104, PINS_JP1, link, c)
    if link is None:
        R(S, cx + 118, 132, 26, 40, 7, 'none', DIM, 1.4, dash='4,3')
        T(S, cx + 131, 190, 'shunt', 8, DIM, 600)
        T(S, cx + 131, 201, 'parked', 8, DIM, 600)
    if link:
        flow(S, cx, 300, 'CW308 nRST', CAT['cw'], 'chip B_RST_N', CHIPC, c)
        caption(S, cx, 350, ['ChipWhisperer (J7.11 / J9.15) can reset', 'the chip. SW1 push-button still works.'])
        pill(S, cx, 440, '✓ board mounted on CW308', c, solid=True)
    else:
        flow(S, cx, 300, 'SW1 only', '#fca5a5', 'chip B_RST_N', CHIPC, DIM)
        caption(S, cx, 350, ['Reset only from push-button SW1.', 'R9 10 k holds B_RST_N high to VDDIO.'])
        pill(S, cx, 440, 'standalone bench use', c)
save(S, 'JP1')

# ============================================================== JP3 + JP5 ====
c = CAT['uart']
S = new_card(c, 'JP3 + JP5 — UART routing',
             'two 1×3 headers · center pin = chip UART · 1-2 = ChipWhisperer · 2-3 = MCP2200 USB module')
minimap(S, MAPX, MAPY, MAPW, MAPH,
        focus=[('JP3', c, (0, 3.1, 'middle')), ('JP5', c, (0, -3.3, 'middle'))],
        ctx=[('J2', None, c, (0, 0, 'middle')), ('J3', None, c, (0, 0, 'middle'))])
JP3P = [('CW TIO2', 'J7.8'), ('chip TX', 'U1.2'), ('MCP2200 RX', 'J2.7')]
JP5P = [('CW TIO1', 'J7.7'), ('chip RX', 'U1.3'), ('MCP2200 TX', 'J2.6')]
for x, w, hdr, link in [(RX0, 320, '1-2 · CW', (1, 2)), (RX0 + 332, 320, '2-3 · MODULE', (2, 3))]:
    cx = x + w/2
    pos_panel(S, x, w, hdr, c)
    T(S, cx, 122, 'JP3 · UART-TX', 10.5, MUT, 700)
    row1xN(S, cx, 165, 92, JP3P, link, c, pad=30)
    flow(S, cx, 242, 'chip TX', CHIPC,
         'CW308 TIO2' if link == (1, 2) else 'module RX',
         CAT['cw'] if link == (1, 2) else CAT['module'], c)
    T(S, cx, 285, 'JP5 · UART-RX', 10.5, MUT, 700)
    row1xN(S, cx, 328, 92, JP5P, link, c, pad=30)
    flow(S, cx, 405, 'CW308 TIO1' if link == (1, 2) else 'module TX',
         CAT['cw'] if link == (1, 2) else CAT['module'], 'chip RX', CHIPC, c)
    if link == (1, 2):
        caption(S, cx, 448, ['Chip UART talks to the ChipWhisperer', 'capture board (SimpleSerial).'])
        pill(S, cx, 505, 'on CW308', c)
    else:
        caption(S, cx, 448, ['Chip UART talks to the MCP2200', 'USB-UART module (J2/J3 socket).'])
        pill(S, cx, 505, 'standalone · USB serial', c)
T(S, RX0 + RW/2, MAPY + MAPH + 14, 'Move JP3 and JP5 TOGETHER — always the same side.', 10, '#fbbf24', 700)
save(S, 'JP3_JP5')

# ============================================================== JP4 + JP6 ====
c = CAT['spi']
S = new_card(c, 'JP4 + JP6 — SPI SCK / MOSI routing',
             'two 1×3 headers · center pin = chip SPI input · 1-2 = ChipWhisperer · 2-3 = MCP2210 USB module')
minimap(S, MAPX, MAPY, MAPW, MAPH,
        focus=[('JP4', c, (0, 3.1, 'middle')), ('JP6', c, (0, 3.1, 'middle'))],
        ctx=[('J4', None, c, (0, 0, 'middle')), ('J5', None, c, (0, 0, 'middle'))])
JP4P = [('CW SCK', 'J7.12 · J9.13'), ('chip SCK', 'U1.19'), ('MCP2210 SCK', 'J4.7')]
JP6P = [('CW MOSI', 'J7.14 · J9.14'), ('chip MOSI', 'U1.17'), ('MCP2210 MOSI', 'J4.6')]
for x, w, hdr, link in [(RX0, 320, '1-2 · CW', (1, 2)), (RX0 + 332, 320, '2-3 · MODULE', (2, 3))]:
    cx = x + w/2
    pos_panel(S, x, w, hdr, c)
    T(S, cx, 122, 'JP4 · SPI clock', 10.5, MUT, 700)
    row1xN(S, cx, 165, 92, JP4P, link, c, pad=30)
    flow(S, cx, 242, 'CW308' if link == (1, 2) else 'MCP2210',
         CAT['cw'] if link == (1, 2) else CAT['module'], 'chip SCK', CHIPC, c)
    T(S, cx, 285, 'JP6 · SPI data in', 10.5, MUT, 700)
    row1xN(S, cx, 328, 92, JP6P, link, c, pad=30)
    flow(S, cx, 405, 'CW308' if link == (1, 2) else 'MCP2210',
         CAT['cw'] if link == (1, 2) else CAT['module'], 'chip MOSI', CHIPC, c)
    if link == (1, 2):
        caption(S, cx, 448, ['SPI master = ChipWhisperer', '(loads keys / configuration).'])
        pill(S, cx, 505, 'on CW308', c)
    else:
        caption(S, cx, 448, ['SPI master = MCP2210 USB-SPI', 'module (J4/J5 socket).'])
        pill(S, cx, 505, 'standalone · USB SPI', c)
T(S, RX0 + RW/2, MAPY + MAPH + 14, 'Move JP4 and JP6 TOGETHER · the S-Sel driver is chosen on J6.', 10, '#fbbf24', 700)
save(S, 'JP4_JP6')

# ============================================================== JP7 ==========
c = CAT['power']
S = new_card(c, 'JP7 — Vcore routing',
             '1×3 header · 1-2 = core rail through the CW308 L-C filter · 2-3 = direct from on-board LDO')
minimap(S, MAPX, MAPY, MAPW, MAPH,
        focus=[('JP7', c, (-4.6, 0, 'end'))],
        ctx=[('U2', 'U2 LDO', c, (0, -3.4, 'middle')),
             ('R7', 'R7 shunt', c, (0, 2.8, 'middle')),
             ('J8', None, CAT['cw'], (0, 0, 'middle'))])
JP7P = [('CW filter', 'J8.5 / J8.6'), ('R7 shunt', '→ core 15/28'), ('U2 LDO', '0.8 V')]
for x, w, hdr, link in [(RX0, 320, '1-2 · FILTERED', (1, 2)), (RX0 + 332, 320, '2-3 · DIRECT', (2, 3))]:
    cx = x + w/2
    pos_panel(S, x, w, hdr, c)
    row1xN(S, cx, 150, 100, JP7P, link, c)
    if link == (1, 2):
        flow(S, cx, 262, 'CW308 filter', CAT['cw'], 'R7 → core', CHIPC, c)
        caption(S, cx, 310, ['0.8 V leaves the board on J8.8 (FILTIN),',
                             'passes the CW308 L-C low-pass filter,',
                             'returns on J8.5/6 and feeds the core',
                             'through the 0.01 Ω measurement shunt R7.'])
        pill(S, cx, 430, '✓ SCA capture — board on CW308', c, solid=True)
    else:
        flow(S, cx, 262, 'U2 LDO 0.8 V', c, 'R7 → core', CHIPC, c)
        caption(S, cx, 310, ['On-board LDO U2 feeds the core directly',
                             'through shunt R7 — CW308 filter bypassed.',
                             'Set 0.8 V on R20 before inserting the chip.'])
        pill(S, cx, 430, 'bench / standalone use', c)
save(S, 'JP7')

# ============================================================== J6 ===========
c = CAT['spi']; ct = CAT['trigger']
S = new_card(c, 'J6 — S-Sel source &amp; trigger-in',
             '2×3 header · vertical link on pins 1-3-5 picks the S-Sel driver · horizontal link 5-6 routes the CW trigger-in')
minimap(S, MAPX, MAPY, MAPW, MAPH,
        focus=[('J6', c, (0, 4.9, 'middle'))],
        ctx=[('J4', None, CAT['module'], (0, 0, 'middle'))])
J6L = {1: ('MCP2210 GPIO4 · J4.5', None, 0), 2: ('NC', None, 0),
       3: ('chip S-Sel_n · U1.18', None, 0), 4: ('NC', None, 0),
       5: ('CW GPIO3 · J7.9', None, 0), 6: ('chip trig-in · U1.23', None, 0)}
ROWS = [(70,  '1-3 · MODULE drives S-Sel', c, [((1, 3), c)],
         ('MCP2210 GPIO4', CAT['module'], 'chip S-Sel_n', CHIPC),
         ['USB-SPI module selects the chip.'], '✓ with USB module', True),
        (224, '3-5 · CW drives S-Sel', c, [((3, 5), c)],
         ('CW308 GPIO3', CAT['cw'], 'chip S-Sel_n', CHIPC),
         ['ChipWhisperer selects the chip.'], None, False),
        (378, '5-6 · CW trigger-in', ct, [((5, 6), ct)],
         ('CW308 GPIO3', CAT['cw'], 'chip trig-in', CHIPC),
         ['CW pulses the chip trigger input.', 'Combinable with 1-3 (not 3-5).'], None, False)]
for y, hdr, cc, links, (lt, lc, rt, rc), cap, tag, solid in ROWS:
    R(S, RX0, y, RW, 146, 10, PANEL2, PBORD, 1.2)
    T(S, RX0 + 14, y + 24, hdr, 13, cc, 800, 'start')
    grid2x3(S, RX0 + 235, y + 84, J6L, links, nc=(2, 4))
    flow(S, RX0 + 505, y + 62, lt, lc, rt, rc, cc)
    caption(S, RX0 + 505, y + 92, cap, 10)
    if tag: pill(S, RX0 + 505, y + 126, tag, cc, solid=solid)
T(S, RX0 + RW/2, MAPY + MAPH + 14, 'Pins 2 and 4 are not connected.', 10, MUT, 600)
save(S, 'J6')

# ============================================================== J10 ==========
c = CAT['trigger']
S = new_card(c, 'J10 — Trigger-out select',
             '2×3 header · choose which chip trigger reaches the CW308 TRIG / GPIO4 line (J7.10)')
minimap(S, MAPX, MAPY, MAPW, MAPH,
        focus=[('J10', c, (0, 3.4, 'middle'))], ctx=[])
J10L = {1: ('NC', None, 0), 2: ('trigger_Out · U1.14', None, 0),
        3: ('out[1] config · U1.13', None, 0), 4: ('CW TRIG · J7.10', None, 0),
        5: ('NC', None, 0), 6: ('out[8] reserve · U1.27', None, 0)}
ROWS = [(70,  '2-4 · NORMAL', [((2, 4), c)],
         ('trigger_Out', CHIPC, 'CW TRIG', CAT['cw']),
         ['Main encryption trigger (U1.14).'], '✓ default', True),
        (224, '3-4 · CONFIG', [((3, 4), c)],
         ('out_pins[1]', CHIPC, 'CW TRIG', CAT['cw']),
         ['Configuration-phase trigger (U1.13).'], None, False),
        (378, '4-6 · RESERVE', [((4, 6), c)],
         ('out_pins[8]', CHIPC, 'CW TRIG', CAT['cw']),
         ['Reserve trigger output (U1.27).'], None, False)]
for y, hdr, links, (lt, lc, rt, rc), cap, tag, solid in ROWS:
    R(S, RX0, y, RW, 146, 10, PANEL2, PBORD, 1.2)
    T(S, RX0 + 14, y + 24, hdr, 13, c, 800, 'start')
    grid2x3(S, RX0 + 235, y + 84, J10L, links, nc=(1, 5))
    flow(S, RX0 + 505, y + 62, lt, lc, rt, rc, c)
    caption(S, RX0 + 505, y + 92, cap, 10)
    if tag: pill(S, RX0 + 505, y + 126, tag, c, solid=solid)
T(S, RX0 + RW/2, MAPY + MAPH + 14, 'Pins 1 and 5 are not connected · fit exactly one link.', 10, MUT, 600)
save(S, 'J10')

# ============================================================== J12 ==========
c = CAT['clock']
S = new_card(c, 'J12 — Clock source select',
             '2×3 header · exactly ONE link · silk: SMA / Osc / Cw')
minimap(S, MAPX, MAPY, MAPW, MAPH,
        focus=[('J12', c, (0, -4.2, 'middle'))],
        ctx=[('Y1', 'Y1 50MHz', c, (0, 2.8, 'middle')),
             ('J11', 'SMA', c, (0, -8.0, 'middle'))])
J12L = {1: ('EXT clk · J11 SMA', None, 0), 2: ('CLK_pin', None, 0),
        3: ('Y1 50 MHz · R22 20Ω', None, 0), 4: ('CLK_pin', None, 0),
        5: ('CW clk · J7.3 → R8', None, 0), 6: ('CLK_pin', None, 0)}
ROWS = [(70,  '1-2 · EXT SOURCE', [((1, 2), c)],
         ('J11 SMA', c, 'CLK_pin', CHIPC),
         ['External bench clock drives the chip.'], None, False),
        (224, '3-4 · OSC 50M SOURCE', [((3, 4), c)],
         ('Y1 50 MHz', c, 'CLK_pin', CHIPC),
         ['On-board oscillator (via R22 20 Ω).'], '✓ standalone default', True),
        (378, '5-6 · CW SOURCE', [((5, 6), c)],
         ('CW clock', CAT['cw'], 'CLK_pin', CHIPC),
         ['ChipWhisperer clock: J7.3 CLKFB', '→ R8 100 Ω → chip clock.'], None, False)]
for y, hdr, links, (lt, lc, rt, rc), cap, tag, solid in ROWS:
    R(S, RX0, y, RW, 146, 10, PANEL2, PBORD, 1.2)
    T(S, RX0 + 14, y + 24, hdr, 13, c, 800, 'start')
    grid2x3(S, RX0 + 235, y + 84, J12L, links)
    flow(S, RX0 + 505, y + 52, lt, lc, rt, rc, c)
    caption(S, RX0 + 505, y + 82, cap, 10)
    if tag: pill(S, RX0 + 505, y + 126, tag, c, solid=solid)
T(S, RX0 + RW/2, MAPY + MAPH + 12,
  'Leave the CW308&#8217;s J3 clock jumper unpopulated — it drives J7.5 (the clock-echo pin) and would fight the selected source.',
  9.5, '#fbbf24', 700)
T(S, RX0 + RW/2, MAPY + MAPH + 26,
  'The chip clock is always echoed to the CW on J7.5 (chip pin 9 · CLK TP).',
  9.5, MUT, 600)
save(S, 'J12')

# ============================================================== JP2 ==========
c = CAT['probe']
S = new_card(c, 'JP2 — Probe header (no jumper)',
             '1×3 header · core debug bits out_pins[2..4] · probe only — there is no jumper logic here')
minimap(S, MAPX, MAPY, MAPW, MAPH, focus=[('JP2', c, (0, 3.1, 'middle'))], ctx=[])
x, w = RX0, RW; cx = x + w/2
R(S, x, MAPY, w, MAPH, 10, PANEL2, PBORD, 1.2)
R(S, cx - 205, MAPY + 24, 410, 30, 8, '#f59e0b', opac=0.95)
T(S, cx, MAPY + 44, 'PROBE HEADER — DO NOT FIT A JUMPER', 13, '#0b1220', 800)
row1xN(S, cx, MAPY + 140, 120, [('out[2]', 'U1.26'), ('out[3]', 'U1.25'), ('out[4]', 'U1.24')],
       None, c)
flow(S, cx, MAPY + 250, 'chip out_pins[2..4]', CHIPC, 'scope / logic analyzer', c, c)
caption(S, cx, MAPY + 300,
        ['Clip an oscilloscope or logic-analyzer probe here to watch the',
         'core debug/PC bits during operation.',
         '',
         'Fitting a shunt would short two chip outputs together.'], 10.5)
save(S, 'JP2')

# ============================================================== overview =====
OW, OH = 1190, 900
S = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {OW} {OH}" font-family="{FONT}">']
R(S, 0, 0, OW, OH, 0, BG)
T(S, 24, 38, 'PROACT — configuration jumpers', 24, TXT, 800, 'start')
T(S, 24, 60, 'where every jumper sits and what its positions mean · details on the per-jumper cards', 11.5, MUT, 600, 'start')
OSC = 8.0
minimap(S, 16, 76, 500, 808,
        focus=[('JP1', CAT['reset'],  (4.2, 0, 'start')),
               ('JP3', CAT['uart'],   (-6.2, 0.6, 'end')),
               ('JP5', CAT['uart'],   (-0.6, -3.6, 'middle')),
               ('JP4', CAT['spi'],    (0, -3.4, 'middle')),
               ('JP6', CAT['spi'],    (0, -3.4, 'middle')),
               ('JP7', CAT['power'],  (0, -4.4, 'middle')),
               ('J6',  CAT['spi'],    (0, 4.7, 'middle')),
               ('J10', CAT['trigger'],(0, 3.6, 'middle')),
               ('J12', CAT['clock'],  (0, -4.2, 'middle')),
               ('JP2', CAT['probe'],  (4.4, 0, 'start'))],
        ctx=[('Y1', None, CAT['clock'], (0, 0, 'middle')),
             ('J11', 'SMA', CAT['clock'], (0, -8.2, 'middle')),
             ('SW1', 'SW1', '#fca5a5', (0, -4.6, 'middle')),
             ('U2', 'U2 LDO', CAT['power'], (0, -3.2, 'middle')),
             ('R7', 'R7', CAT['power'], (2.8, 0, 'start'))],
        scale=OSC)
# ---- legend table ----
TX0, TW = 534, 640
R(S, TX0, 76, TW, 520, 10, PANEL, PBORD, 1.2)
T(S, TX0 + 18, 102, 'Jumper', 11, MUT, 800, 'start')
T(S, TX0 + 118, 102, 'Role', 11, MUT, 800, 'start')
T(S, TX0 + 320, 102, 'Positions', 11, MUT, 800, 'start')
L(S, TX0 + 12, 112, TX0 + TW - 12, 112, PBORD, 1.2)
ROWS = [('JP1', 'reset', 'Reset from CW308', 'closed = CW resets chip · open = SW1 only'),
        ('JP3 / JP5', 'uart', 'UART TX / RX routing', '1-2 CW308 TIO2/TIO1 · 2-3 MCP2200 module'),
        ('JP4 / JP6', 'spi', 'SPI SCK / MOSI source', '1-2 CW308 · 2-3 MCP2210 module'),
        ('JP7', 'power', 'Vcore rail routing', '1-2 CW308 filter (✓ SCA) · 2-3 U2 LDO direct'),
        ('J6', 'spi', 'S-Sel source + trig-in', '1-3 module · 3-5 CW · 5-6 CW → trigger_in'),
        ('J10', 'trigger', 'Trigger out → CW TRIG', '2-4 normal (✓) · 3-4 config · 4-6 reserve'),
        ('J12', 'clock', 'Clock source select', '1-2 EXT SMA · 3-4 OSC 50M (✓) · 5-6 CW'),
        ('JP2', 'probe', 'out_pins[2..4] probe', 'no jumper — probe pins only')]
for i, (ref, cat, role, pos) in enumerate(ROWS):
    y = 140 + i * 60
    if i: L(S, TX0 + 12, y - 30, TX0 + TW - 12, y - 30, PBORD, 1)
    col = CAT[cat]
    R(S, TX0 + 18, y - 13, 10, 10, 2, col)
    T(S, TX0 + 34, y - 4, ref, 12, col, 800, 'start')
    T(S, TX0 + 118, y - 4, role, 11, TXT, 700, 'start')
    T(S, TX0 + 320, y - 4, pos, 10, MUT, 600, 'start')
# ---- quick presets ----
PY = 616
R(S, TX0, PY, TW, 268, 10, PANEL, PBORD, 1.2)
T(S, TX0 + 18, PY + 28, 'Quick presets', 13, TXT, 800, 'start')
T(S, TX0 + 18, PY + 56, '▸ SCA capture — board mounted on CW308', 11.5, CAT['cw'], 800, 'start')
for i, ln in enumerate(['JP1 closed · JP3/JP5 → 1-2 · JP4/JP6 → 1-2 · JP7 → 1-2 FILTERED',
                        'J6 → 3-5 (CW drives S-Sel) · J10 → 2-4 · J12 → 3-4 (clock echoed on J7.5)']):
    T(S, TX0 + 34, PY + 76 + i * 17, ln, 10.5, '#cbd5e1', 600, 'start')
T(S, TX0 + 18, PY + 130, '▸ Standalone bench — USB modules fitted', 11.5, CAT['module'], 800, 'start')
for i, ln in enumerate(['JP1 open · JP3/JP5 → 2-3 · JP4/JP6 → 2-3 · JP7 → 2-3 DIRECT',
                        'J6 → 1-3 (module drives S-Sel) · J10 → 2-4 · J12 → 3-4 OSC 50M']):
    T(S, TX0 + 34, PY + 150 + i * 17, ln, 10.5, '#cbd5e1', 600, 'start')
T(S, TX0 + 18, PY + 202, 'Always: set R20 for 0.8 V core BEFORE inserting the chip · white dot = pin 1.', 10, MUT, 600, 'start')
T(S, TX0 + 18, PY + 219, 'J12: exactly one source link · CW308 J3 stays empty — J7.5 carries the clock echo.', 10, MUT, 600, 'start')
T(S, TX0 + 18, PY + 250, 'Netlist-verified · generated by docs/tools/gen_jumpers.py', 9, DIM, 600, 'start')
save(S, 'overview', OW, OH)

# ---------------------------------------------------------------- render PNG --
unknown = {c['lib'] for c in COMPS if c['lib'] not in SIZE}
if unknown: print('NOTE: default 2x2 mm footprint used for libs:', sorted(unknown))
for name, w, h in FILES:
    svg = os.path.join(OUT, name + '.svg'); png = os.path.join(OUT, name + '.png')
    subprocess.run(['inkscape', svg, '-o', png, '-w', str(w * 2)],
                   check=True, capture_output=True)
    print('rendered', png)
