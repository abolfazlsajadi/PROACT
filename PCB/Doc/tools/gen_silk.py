#!/usr/bin/env python3
"""Proposed silkscreen (white-print) mockup for the PROACT board, from PcbDoc placements."""
import olefile, os

PCB = '/home/abish/Downloads/PCB/PROACT_DOC/PCB_finalx/PCB_Projectcxfinal/PCB1.PcbDoc'
OUT = '/home/abish/Downloads/PCB/PROACT_DOC/docs/img/board_v2_silkscreen.svg'
ole = olefile.OleFileIO(PCB)
d = ole.openstream('Components6/Data').read(); off = 0; C = {}
while off + 4 <= len(d):
    ln = int.from_bytes(d[off:off+4], 'little'); off += 4
    if ln <= 0 or off + ln > len(d): break
    f = dict(p.split('=', 1) for p in d[off:off+ln].decode('latin1', 'replace').split('|') if '=' in p); off += ln
    mil = lambda v: float(v.replace('mil', '')) * 0.0254
    C[f.get('SOURCEDESIGNATOR')] = dict(x=mil(f.get('X', '0mil')), y=mil(f.get('Y', '0mil')),
                                        rot=float(f.get('ROTATION', '0')), lib=f.get('SOURCELIBREFERENCE'))

xs = [c['x'] for c in C.values()]; ys = [c['y'] for c in C.values()]
xmin, xmax, ymin, ymax = min(xs)-4, max(xs)+4, min(ys)-4, max(ys)+5
SCALE = 11; TOP = 64; BOT = 60
W = (xmax-xmin)*SCALE; H = TOP + (ymax-ymin)*SCALE + BOT
def tx(x): return (x-xmin)*SCALE
def ty(y): return TOP + (ymax-y)*SCALE
def P(des): return C[des]['x'], C[des]['y']

# ---- proposed silk: (anchor_des or (x,y), dx_mm, dy_mm, text, size, weight, anchor) ----
# component-anchored functional labels (added ABOVE existing designators)
LAB = [
  # --- clock block ---
  ('J12', 0, 5.6, 'CLOCK SELECT', 10, 800, 'middle'),
  ('J12', 0, -5.2, '1-2 BNC · 3-4 OSC50M · 5-6 CW', 7.5, 600, 'middle'),
  ('Y1', 0, 3.0, '50 MHz', 8, 700, 'middle'),
  ('J11', 0, 7.5, 'EXT CLK IN (BNC)', 9, 700, 'middle'),
  # --- Vcore / power block ---
  ('R20', -1, -4.3, 'Vadj — SET 0.8V BEFORE CHIP', 8, 800, 'middle'),
  ('U2', -0.5, -3.2, 'CORE LDO 0.8V', 8, 700, 'middle'),
  ('R7', 0, 3.0, 'SHUNT 0.01Ω', 7.5, 700, 'middle'),
  ('JP7', 0, -3.8, 'Vcore 2-3 direct/2-1 filt', 7, 700, 'middle'),
  # --- reset / status LEDs (labels to the LEFT, they sit on the right edge) ---
  ('D7', -3.0, 0, 'B_RST', 7.5, 700, 'end'),
  ('D8', -3.0, 0, 'C_RST', 7.5, 700, 'end'),
  ('D9', -3.0, 0, 'G_RST', 7.5, 700, 'end'),
  ('D10', -3.0, 0, 'SPI_RST', 7.5, 700, 'end'),
  ('D1', 3.0, 0, 'ALIVE', 7.5, 700, 'start'),
  # --- config jumpers ---
  ('JP1', -3.2, 0, 'B_RST→CW', 7, 700, 'end'),
  ('JP2', 0, 3.0, 'out[2:4]', 7, 700, 'middle'),
  ('JP3', -3.0, 0, 'UART M/CW', 7, 700, 'end'),
  ('JP5', -3.0, 0, 'UART M/CW', 7, 700, 'end'),
  ('JP4', 5.5, 0, 'SCK M/CW', 7, 700, 'start'),
  ('JP6', 5.5, 0, 'MOSI M/CW', 7, 700, 'start'),
  ('J6', 0, 4.5, 'S-SEL / TRIG-IN', 7.5, 700, 'middle'),
  ('J10', 0, -4.5, 'TRIG 2-4 norm/3-4 cfg/4-6 rsv', 7, 700, 'middle'),
  ('J1', 0, -4.0, 'DEBUG out[0/5/6/11]+spare', 7, 700, 'middle'),
  # --- DIP switch functions ---
  ('S1', 0, 10.5, 'S1: 1 Ssel  2 dbg  3 Grst  4 SPIrst  5 Crst  rb', 7.5, 700, 'middle'),
  ('S1', 0, -10.5, '6 = UART VDD    7 = SPI VDD', 7.5, 700, 'middle'),
  # --- CW connectors ---
  ('J7', 1.8, 0, 'CW WEST', 8, 700, 'start'),
  ('J8', -1.8, 0, 'CW EAST', 8, 700, 'end'),
  ('J9', 0, -2.2, 'CW SOUTH', 8, 700, 'middle'),
  # --- module regions ---
  ('J2', 6, -12, 'MCP2200  USB-UART', 9, 800, 'middle'),
  ('J5', -6, -12, 'MCP2210  USB-SPI/GPIO', 9, 800, 'middle'),
]

svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" font-family="Inter,Segoe UI,Arial,sans-serif">']
svg.append(f'<rect width="{W:.0f}" height="{H:.0f}" fill="#0b1220"/>')
svg.append(f'<text x="{W/2:.0f}" y="28" font-size="20" font-weight="800" text-anchor="middle" fill="#f8fafc">PROACT — Proposed Silkscreen (white print)</text>')
svg.append(f'<text x="{W/2:.0f}" y="48" font-size="11" font-weight="600" text-anchor="middle" fill="#94a3b8">functional labels to ADD on the overlay layer · positions from PcbDoc · does not change routing/placement</text>')
# board
bx0,by0,bx1,by1=tx(xmin+2),ty(ymax-1),tx(xmax-2),ty(ymin+3)
svg.append(f'<rect x="{bx0:.0f}" y="{by0:.0f}" width="{bx1-bx0:.0f}" height="{by1-by0:.0f}" rx="14" fill="#0e4b34" stroke="#1f7a54" stroke-width="2"/>')

# faint component footprints for context — w,h in the footprint LIBRARY 0-deg frame
# (verified against Pads6 pad bboxes); ROTATION 90/270 swaps the drawn extents.
SZ={'SOCKET_IC_DIP_28':(15.5,36),'HDR_F_1X20':(50.8,2.6),'HDR_M_1X7':(17.8,2.6),'DIPSW254S_7P':(10,18.5),
    'HDR_F_2X3':(7.9,5.6),'HDR_M_2X5':(12.7,5.1),'JUMPER_HDR_3P':(7.6,2.6),'JUMPER_HDR_2P':(5.1,2.6),
    'CONN_RF_BNC_RA':(9,13),'ResPot':(6.8,4.8),'TPS74801DRCR':(3.2,3.2),'OSC_4_50MHz_3225E':(3.2,2.5),
    'SWPBS_SPDT_5X5X1_SKQGAFE010':(6,6),'LED_SMD_0805_RED':(2,1.3),'cap_SMD':(2,1.3),'Res_SMD':(2,1.3),
    'TESTPOINT_BIG_BLK1':(3.4,3.4)}
for des,c in C.items():
    w,h=SZ.get(c['lib'],(2,2))
    if 45 <= round(c['rot']) % 180 < 135: w,h = h,w
    cx,cy=tx(c['x']),ty(c['y']); wp,hp=w*SCALE,h*SCALE
    svg.append(f'<rect x="{cx-wp/2:.1f}" y="{cy-hp/2:.1f}" width="{wp:.1f}" height="{hp:.1f}" rx="1.5" fill="#0e4b34" stroke="#2f6f52" stroke-width="1"/>')

# proposed silk (white text)
for des, dx, dy, text, size, weight, anchor in LAB:
    if des not in C: continue
    x,y = P(des); px,py = tx(x+dx), ty(y+dy)
    svg.append(f'<text x="{px:.1f}" y="{py:.1f}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" '
               f'fill="#ffffff" stroke="#0b1220" stroke-width="2.4" style="paint-order:stroke">{text}</text>')

svg.append('</svg>')
open(OUT,'w').write('\n'.join(svg))
print('wrote', OUT, int(W), int(H))
