#!/usr/bin/env python3
"""Extract netlist from Altium binary SchDoc (Sheet1.SchDoc)."""
import olefile, re, sys
from collections import defaultdict

SCH = '/home/abish/Downloads/PCB/PROACT_DOC/PCB/Sheet1.SchDoc'

ole = olefile.OleFileIO(SCH)
stream = ole.openstream('FileHeader').read()

# --- parse records: 2-byte LE length, 1 pad byte, 1 type byte, payload ---
records = []
off = 0
while off + 4 <= len(stream):
    ln = int.from_bytes(stream[off:off+2], 'little')
    rtype = stream[off+3]
    payload = stream[off+4:off+4+ln]
    off += 4 + ln
    txt = payload.rstrip(b'\x00').decode('latin1')
    fields = {}
    for part in txt.split('|'):
        if '=' in part:
            k, v = part.split('=', 1)
            fields[k] = v
    records.append(fields)

# first record is HEADER; records after that are indexed from 0
hdr = records[0]
assert 'HEADER' in hdr
recs = records[1:]
print(f"# parsed {len(recs)} records", file=sys.stderr)

def gi(f, k, d=0):
    try:
        return int(f.get(k, d))
    except ValueError:
        return d

# --- components and designators ---
comp_desig = {}   # record index -> designator text
components = {}   # record index -> fields
for i, f in enumerate(recs):
    if f.get('RECORD') == '1':
        components[i] = f
for i, f in enumerate(recs):
    if f.get('RECORD') == '34':
        oi = gi(f, 'OwnerIndex', -1)
        if oi in components:
            comp_desig[oi] = f.get('Text', '?')

# --- pins ---
DIRS = {0: (1, 0), 1: (0, 1), 2: (-1, 0), 3: (0, -1)}
pins = []  # (point, comp_desig, pin_designator, pin_name)
for i, f in enumerate(recs):
    if f.get('RECORD') != '2':
        continue
    oi = gi(f, 'OwnerIndex', -1)
    if oi not in components:
        continue
    comp = components[oi]
    cur_part = gi(comp, 'CurrentPartId', 1)
    part_id = gi(f, 'OwnerPartId', -1)
    if part_id not in (-1, cur_part) and gi(comp, 'PartCount', 1) > 2:
        continue
    x, y = gi(f, 'Location.X'), gi(f, 'Location.Y')
    length = gi(f, 'PinLength')
    conglom = gi(f, 'PinConglomerate')
    o = conglom & 3
    dx, dy = DIRS[o]
    pt = (x + dx * length, y + dy * length)
    pins.append((pt, comp_desig.get(oi, '?'), f.get('Designator', '?'), f.get('Name', ''), oi))

# --- wires ---
wires = []  # list of vertex lists
for f in recs:
    if f.get('RECORD') == '27':
        n = gi(f, 'LocationCount')
        pts = [(gi(f, f'X{k}'), gi(f, f'Y{k}')) for k in range(1, n + 1)]
        wires.append(pts)

# --- net labels, power ports, junctions ---
labels = [((gi(f, 'Location.X'), gi(f, 'Location.Y')), f.get('Text', '?'))
          for f in recs if f.get('RECORD') == '25']
pports = [((gi(f, 'Location.X'), gi(f, 'Location.Y')), f.get('Text', '?'))
          for f in recs if f.get('RECORD') == '17']
junctions = [(gi(f, 'Location.X'), gi(f, 'Location.Y'))
             for f in recs if f.get('RECORD') == '29']

# --- geometry helpers ---
def on_segment(p, a, b):
    (px, py), (ax, ay), (bx, by) = p, a, b
    if (bx - ax) * (py - ay) != (by - ay) * (px - ax):
        return False
    return min(ax, bx) <= px <= max(ax, bx) and min(ay, by) <= py <= max(ay, by)

def point_on_wire(p, w):
    return any(on_segment(p, w[i], w[i + 1]) for i in range(len(w) - 1))

# --- union-find ---
parent = {}
def find(x):
    parent.setdefault(x, x)
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x
def union(a, b):
    ra, rb = find(a), find(b)
    if ra != rb:
        parent[ra] = rb

W = lambda i: ('W', i)
P = lambda i: ('P', i)
PP = lambda i: ('PP', i)

# wire-wire: endpoint/vertex of one on segment of other
for i, wi in enumerate(wires):
    for j in range(i + 1, len(wires)):
        wj = wires[j]
        connected = any(point_on_wire(v, wj) for v in (wi[0], wi[-1])) or \
                    any(point_on_wire(v, wi) for v in (wj[0], wj[-1]))
        if connected:
            union(W(i), W(j))

# junctions connect all wires crossing that point
for jp in junctions:
    touching = [W(i) for i, w in enumerate(wires) if point_on_wire(jp, w)]
    for a in touching[1:]:
        union(touching[0], a)

# pins
for k, (pt, cdes, pdes, pname, oi) in enumerate(pins):
    for i, w in enumerate(wires):
        if point_on_wire(pt, w):
            union(P(k), W(i))
    # pin-to-pin direct
    for k2 in range(k + 1, len(pins)):
        if pins[k2][0] == pt:
            union(P(k), P(k2))

# power ports
for k, (pt, name) in enumerate(pports):
    for i, w in enumerate(wires):
        if point_on_wire(pt, w):
            union(PP(k), W(i))
    for k2, (ppt, cdes, pdes, pname, oi) in enumerate(pins):
        if ppt == pt:
            union(PP(k), P(k2))

# --- collect nets ---
nets = defaultdict(lambda: {'pins': [], 'names': set(), 'power': set()})
for k, (pt, cdes, pdes, pname, oi) in enumerate(pins):
    nets[find(P(k))]['pins'].append(f"{cdes}.{pdes}")
for k, (pt, name) in enumerate(pports):
    nets[find(PP(k))]['power'].add(name)
for pt, name in labels:
    for i, w in enumerate(wires):
        if point_on_wire(pt, w):
            nets[find(W(i))]['names'].add(name)
            break

# name and print
out = []
for root, d in nets.items():
    if not d['pins']:
        continue
    name = '/'.join(sorted(d['power'])) or '/'.join(sorted(d['names'])) or '(unnamed)'
    if d['names'] and d['power']:
        name = '/'.join(sorted(d['power'])) + ' [' + '/'.join(sorted(d['names'])) + ']'
    out.append((name, sorted(d['pins'])))

out.sort(key=lambda x: x[0])
for name, plist in out:
    print(f"{name:28s} : {', '.join(plist)}")
