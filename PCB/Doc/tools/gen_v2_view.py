#!/usr/bin/env python3
"""Render a board placement view directly from the PcbDoc (Components6)."""
import olefile, os

PCB = '/home/abish/Downloads/PCB/PROACT_DOC/PCB_finalx/PCB_Projectcxfinal/PCB1.PcbDoc'
OUT = '/home/abish/Downloads/PCB/PROACT_DOC/docs/img/board_v2_placement.svg'
ole = olefile.OleFileIO(PCB)

def records(stream):
    d = ole.openstream(stream).read(); off = 0; out = []
    while off + 4 <= len(d):
        ln = int.from_bytes(d[off:off+4], 'little'); off += 4
        if ln <= 0 or off + ln > len(d): break
        out.append(d[off:off+ln]); off += ln
    return out

comps = []
for r in records('Components6/Data'):
    f = dict(p.split('=', 1) for p in r.decode('latin1', 'replace').split('|') if '=' in p)
    mil = lambda v: float(v.replace('mil', ''))
    comps.append(dict(des=f.get('SOURCEDESIGNATOR'), x=mil(f.get('X', '0mil')) * 0.0254,
                      y=mil(f.get('Y', '0mil')) * 0.0254, rot=float(f.get('ROTATION', '0')),
                      layer=f.get('LAYER'), lib=f.get('SOURCELIBREFERENCE')))

NEW = {'Y1','J11','J12','JP7','D7','D8','D9','D10','R6','R16','R17','R18','R20','R21','R22','C13','C16'}

SIZE = {  # w,h mm in the FOOTPRINT LIBRARY 0-deg frame (w = X extent at ROTATION=0).
 # Frames verified against Pads6 pad bounding boxes un-rotated by each part's ROTATION.
 'OSC_4_50MHz_3225E':(3.2,2.5),'CONN_RF_BNC_RA':(9,13),'ResPot':(6.8,4.8),
 'HDR_F_2X3':(7.9,5.6),'JUMPER_HDR_3P':(7.6,2.6),'HDR_M_1X7':(17.8,2.6),
 'HDR_F_1X20':(50.8,2.6),'HDR_M_2X5':(12.7,5.1),'JUMPER_HDR_2P':(5.1,2.6),
 'DIPSW254S_7P':(10.0,18.5),'SOCKET_IC_DIP_28':(15.5,36.0),'TPS74801DRCR':(3.2,3.2),
 'SWPBS_SPDT_5X5X1_SKQGAFE010':(6,6),'LED_SMD_0805_RED':(2.0,1.3),'cap_SMD':(2.0,1.3),
 'Res_SMD':(2.0,1.3),'TESTPOINT_BIG_BLK1':(3.4,3.4)}

def placed_size(lib, rot):
    """Drawn extents after applying the component ROTATION: 90/270 swap w/h."""
    w, h = SIZE.get(lib, (2, 2))
    if 45 <= round(rot) % 180 < 135: w, h = h, w
    return w, h

def cat(d, lib):
    if d=='U1': return 'chip'
    if d=='U2' or lib=='ResPot': return 'power'
    if lib=='OSC_4_50MHz_3225E' or lib=='CONN_RF_BNC_RA': return 'clock'
    if d.startswith('JP'): return 'jumper'
    if d.startswith('J'): return 'conn'
    if d.startswith('S1') or d.startswith('SW'): return 'switch'
    if d.startswith('D'): return 'led'
    if d in ('CLK','GND','VDDIO','Vcore'): return 'tp'
    if d.startswith('R'): return 'res'
    if d.startswith('C'): return 'cap'
    return 'other'

COL = {'chip':('#1e293b','#f8fafc'),'power':('#b45309','#fff7ed'),'clock':('#0d9488','#ecfeff'),
 'conn':('#1d4ed8','#eff6ff'),'jumper':('#7c3aed','#f5f3ff'),'switch':('#0f766e','#f0fdfa'),
 'led':('#dc2626','#fef2f2'),'tp':('#0891b2','#ecfeff'),'res':('#64748b','#f1f5f9'),
 'cap':('#94a3b8','#f8fafc'),'other':('#94a3b8','#f8fafc')}

LABEL = {d:d for d in [c['des'] for c in comps]}
# nicer labels for key parts
LABEL.update({'U1':'U1 PROACT','U2':'U2 LDO','S1':'S1 DIP×7','SW1':'SW1 B_RST','J1':'J1 DBG',
 'J7':'J7 (CW-W)','J8':'J8 (CW-E)','J9':'J9 (CW-S)','J11':'J11 BNC','J12':'J12 CLK-sel',
 'JP7':'JP7 Vcore','Y1':'Y1 50MHz','R20':'R20 pot','R7':'R7 shunt','J6':'J6 S-Sel','J10':'J10 TRIG'})
SHOWLBL = set(LABEL) - {c['des'] for c in comps if cat(c['des'],c['lib']) in ('res','cap')} | \
          {'R7','R20'}

xs=[c['x'] for c in comps]; ys=[c['y'] for c in comps]
xmin,xmax,ymin,ymax=min(xs)-4,max(xs)+4,min(ys)-4,max(ys)+5
SCALE=10; TOP=60; BOT=150
W=(xmax-xmin)*SCALE
H=TOP+(ymax-ymin)*SCALE+BOT
def tx(x): return (x-xmin)*SCALE
def ty(y): return TOP+(ymax-y)*SCALE

S=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" font-family="Inter,Segoe UI,Arial,sans-serif">']
S.append(f'<rect width="{W:.0f}" height="{H:.0f}" fill="#0b1220"/>')
S.append(f'<text x="{W/2:.0f}" y="30" font-size="22" font-weight="800" text-anchor="middle" fill="#f8fafc">PROACT Board — Placement View</text>')
S.append(f'<text x="{W/2:.0f}" y="50" font-size="12" font-weight="600" text-anchor="middle" fill="#94a3b8">from PCB1.PcbDoc · top view · ★ = clock / Vcore / debug additions</text>')
# board outline
bx0,by0,bx1,by1=tx(xmin+2),ty(ymax-1),tx(xmax-2),ty(ymin+3)
S.append(f'<rect x="{bx0:.0f}" y="{by0:.0f}" width="{bx1-bx0:.0f}" height="{by1-by0:.0f}" rx="14" fill="#0e4b34" stroke="#1f7a54" stroke-width="2"/>')

def draw(c, key=False):
    d=c['des']; k=cat(d,c['lib']); stroke,fill=COL[k]
    w,h=placed_size(c['lib'],c['rot'])
    isnew=d in NEW
    cx,cy=tx(c['x']),ty(c['y']); wp,hp=w*SCALE,h*SCALE
    sw = 2.4 if isnew else 1.4
    stk = '#f59e0b' if isnew else stroke
    S.append(f'<rect x="{cx-wp/2:.1f}" y="{cy-hp/2:.1f}" width="{wp:.1f}" height="{hp:.1f}" rx="2" fill="{fill}" stroke="{stk}" stroke-width="{sw}" opacity="0.97"/>')

order=['cap','res','tp','led','clock','power','switch','jumper','conn','chip']
for k in order:
    for c in comps:
        if cat(c['des'],c['lib'])==k and c['des'] not in SHOWLBL: draw(c)
for k in order:
    for c in comps:
        if cat(c['des'],c['lib'])==k and c['des'] in SHOWLBL: draw(c)

# labels (rotated 90° when the part is drawn tall+narrow and the text would overflow it)
for c in comps:
    d=c['des']
    if d not in SHOWLBL: continue
    cx,cy=tx(c['x']),ty(c['y'])
    isnew=d in NEW
    fs=11 if d=='U1' else 8.5
    star='★ ' if isnew else ''
    col='#fde68a' if isnew else '#f8fafc'
    txt=f'{star}{LABEL[d]}'
    w,h=placed_size(c['lib'],c['rot']); wp,hp=w*SCALE,h*SCALE
    est=len(txt)*fs*0.62
    attrs=(f'transform="translate({cx:.0f},{cy:.0f}) rotate(-90)" x="0" y="3"'
           if hp>wp*1.4 and est>wp else f'x="{cx:.0f}" y="{cy+3:.0f}"')
    S.append(f'<text {attrs} font-size="{fs}" font-weight="700" text-anchor="middle" fill="{col}" stroke="#0b1220" stroke-width="2.6" style="paint-order:stroke">{txt}</text>')

# legend
leg=[('chip','PROACT'),('conn','Connector'),('jumper','Jumper'),('clock','Clock'),
     ('power','Power/LDO/pot'),('switch','Switch'),('led','LED'),('tp','Test point')]
ly=H-BOT+40
S.append(f'<rect x="20" y="{ly-24:.0f}" width="{W-40:.0f}" height="130" rx="10" fill="#0e1729" stroke="#1e293b"/>')
S.append(f'<text x="40" y="{ly-4:.0f}" font-size="13" font-weight="800" fill="#e2e8f0">Legend</text>')
per=(W-80)/4
for i,(k,n) in enumerate(leg):
    st,fl=COL[k]; x=40+(i%4)*per; y=ly+18+(i//4)*30
    S.append(f'<rect x="{x:.0f}" y="{y-11:.0f}" width="15" height="15" rx="3" fill="{fl}" stroke="{st}" stroke-width="2"/>')
    S.append(f'<text x="{x+21:.0f}" y="{y+1:.0f}" font-size="11" font-weight="600" fill="#cbd5e1">{n}</text>')
# star marker on its own third row
ynew=ly+18+2*30
S.append(f'<rect x="40" y="{ynew-11:.0f}" width="15" height="15" rx="3" fill="#0e4b34" stroke="#f59e0b" stroke-width="2.4"/>')
S.append(f'<text x="61" y="{ynew+1:.0f}" font-size="10" font-weight="700" fill="#fde68a">★ Clock / Vcore / debug additions  (clock select · BNC · Vcore jumper · pot · LEDs)</text>')

S.append('</svg>')
open(OUT,'w').write('\n'.join(S))
print('wrote', OUT, int(W),int(H))
