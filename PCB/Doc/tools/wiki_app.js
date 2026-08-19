'use strict';
/* PROACT board wiki app — inlined by gen_wiki_data.py */
const $ = s => document.querySelector(s), $$ = s => Array.from(document.querySelectorAll(s));
const DATA = JSON.parse($('#wiki-data').textContent);
const B = DATA.board, CMP = DATA.comps, NETS = DATA.nets, VIEWS = DATA.views;
/* mm → render-pixel transforms, per view. TOP: y flips (mm Y up, px Y down).
   BOTTOM: viewed from below — x mirrors as well. Calibrated by the generator
   against the green board region of each render. */
let VIEW = 'T';
const vw = () => VIEWS[VIEW];
const mx = x => { const v = vw(); return v.mir ? v.gx0 + (B.x1 - x) * v.sx : v.gx0 + (x - B.x0) * v.sx; };
const my = y => { const v = vw(); return v.gy0 + (B.y1 - y) * v.sy; };
const PXMM = VIEWS.T.sx;                       // ≈23 px per mm (marker/ring sizing)
const plain = h => (h || '').replace(/<[^>]*>/g, '');
const CATS = ['chip', 'connector', 'jumper', 'led', 'switch', 'power', 'clock', 'passive', 'testpoint'];

/* ---------------- lookups ---------------- */
const netComps = {}, compNets = {};
for (const [id, n] of Object.entries(NETS)) {
  netComps[id] = new Set();
  for (const m of n.members) {
    const ref = m.split('.')[0];
    if (CMP[ref]) { netComps[id].add(ref); (compNets[ref] = compNets[ref] || new Set()).add(id); }
  }
}
const related = {};
for (const [id, n] of Object.entries(NETS)) {
  if (n.power) continue;
  const refs = [...netComps[id]];
  for (const r of refs) { related[r] = related[r] || new Set(); refs.forEach(o => { if (o !== r) related[r].add(o); }); }
}
const pinNet = p => Object.keys(NETS).find(id => NETS[id].members.includes('U1.' + p)) || null;

/* ---------------- jumper state ---------------- */
const JSTATE = { JP1: 'open', JP3: 'm', JP5: 'm', JP4: 'm', JP6: 'm', JP7: 'direct',
                 J6: { ssel: 'm', trig: false }, J10: 'normal', J12: 'osc' };
const STATEFUL = ['JP1', 'JP3', 'JP5', 'JP4', 'JP6', 'JP7', 'J6', 'J10', 'J12'];
const HIDDENPIN = ['J6', 'J10', 'J12'];   // 2x3 headers whose CAD body hides the pins — draw them ourselves

function jumperLinks(ref) {
  switch (ref) {
    case 'JP1': return JSTATE.JP1 === 'closed' ? [[1, 2]] : [];
    case 'JP3': case 'JP5': case 'JP4': case 'JP6': return JSTATE[ref] === 'cw' ? [[1, 2]] : [[2, 3]];
    case 'JP7': return JSTATE.JP7 === 'filt' ? [[1, 2]] : [[2, 3]];
    case 'J6': { const l = [JSTATE.J6.ssel === 'm' ? [1, 3] : [3, 5]]; if (JSTATE.J6.trig) l.push([5, 6]); return l; }
    case 'J10': return [{ normal: [2, 4], config: [3, 4], reserve: [4, 6] }[JSTATE.J10]];
    case 'J12': return [{ ext: [1, 2], osc: [3, 4], cw: [5, 6] }[JSTATE.J12]];
  }
  return null;
}

const J3NOTE = 'Leave the CW308’s <b>J3</b> clock jumper <b>unpopulated</b> — it drives J7.5 (the clock-echo pin) and would fight the selected source.';
function jumperStateInfo(ref) {
  const i = { pos: '', lines: [], caution: '' };
  switch (ref) {
    case 'JP1':
      if (JSTATE.JP1 === 'closed') { i.pos = 'closed'; i.lines = ['The CW308 <b>nRST</b> line (J7.11 / J9.15) reaches chip <code>B_RST_N</code> (pin 1) — the ChipWhisperer can reset the chip. SW1 still works.']; }
      else { i.pos = 'open'; i.lines = ['Reset only from push-button <b>SW1</b> — R9 10 k holds <code>B_RST_N</code> high to VDDIO.']; }
      break;
    case 'JP3':
      i.pos = JSTATE.JP3 === 'cw' ? '1-2 · CW' : '2-3 · M';
      i.lines = [JSTATE.JP3 === 'cw' ? 'chip <code>TX</code> (pin 2) → CW308 <b>TIO2</b> (J7.8) — UART to the ChipWhisperer.'
                                     : 'chip <code>TX</code> (pin 2) → MCP2200 <b>RX</b> — USB serial to the PC.',
                 'Move JP3 and JP5 together — always the same side.'];
      break;
    case 'JP5':
      i.pos = JSTATE.JP5 === 'cw' ? '1-2 · CW' : '2-3 · M';
      i.lines = [JSTATE.JP5 === 'cw' ? 'CW308 <b>TIO1</b> (J7.7) → chip <code>RX</code> (pin 3).'
                                     : 'MCP2200 <b>TX</b> → chip <code>RX</code> (pin 3).',
                 'Move JP3 and JP5 together — always the same side.'];
      break;
    case 'JP4':
      i.pos = JSTATE.JP4 === 'cw' ? '1-2 · CW' : '2-3 · M';
      i.lines = [JSTATE.JP4 === 'cw' ? 'CW308 <b>SCK</b> (J7.12) → chip <code>sck</code> (pin 19).'
                                     : 'MCP2210 <b>SCK</b> → chip <code>sck</code> (pin 19).',
                 'Move JP4 and JP6 together — the S-Sel driver is chosen on J6.'];
      break;
    case 'JP6':
      i.pos = JSTATE.JP6 === 'cw' ? '1-2 · CW' : '2-3 · M';
      i.lines = [JSTATE.JP6 === 'cw' ? 'CW308 <b>MOSI</b> (J7.14) → chip <code>SIn</code> (pin 17).'
                                     : 'MCP2210 <b>MOSI</b> → chip <code>SIn</code> (pin 17).',
                 'Move JP4 and JP6 together — the S-Sel driver is chosen on J6.'];
      break;
    case 'JP7':
      if (JSTATE.JP7 === 'filt') { i.pos = '1-2 · filtered'; i.lines = ['0.8 V → J8.8 <b>FILTIN</b> → CW308 L-C low-pass filter → back on J8.5/6 → R7 shunt. Side-channel capture on the CW308 — the filter cleans the rail so the shunt sees the die, not supply noise.']; }
      else { i.pos = '2-3 · direct'; i.lines = ['0.8 V → R7 shunt, <b>direct</b>. Bench use off the CW308, or the shortest supply path.']; }
      break;
    case 'J6':
      i.pos = (JSTATE.J6.ssel === 'm' ? '1-3' : '3-5') + (JSTATE.J6.trig ? ' + 5-6' : '');
      i.lines = [JSTATE.J6.ssel === 'm' ? '<b>1-3</b> — MCP2210 GPIO4 drives <code>SSel_n</code> (pin 18): SPI select from the bridge module.'
                                        : '<b>3-5</b> — CW308 GPIO3 (J7.9) drives <code>SSel_n</code> (pin 18): SPI select from the ChipWhisperer.'];
      if (JSTATE.J6.trig) i.lines.push('<b>5-6</b> — CW308 GPIO3 drives <code>trigger_in</code> (pin 23).');
      if (JSTATE.J6.ssel === 'cw' && JSTATE.J6.trig)
        i.caution = '3-5 and 5-6 share pin 5 — GPIO3 would drive <code>SSel_n</code> and <code>trigger_in</code> at once. Fit one of the two.';
      break;
    case 'J10': {
      const m = { normal: ['2-4', '<code>trigger_Out</code> — normal trigger (pin 14) → CW308 TRIG.'],
                  config: ['3-4', '<code>out_pins[1]</code> — config trigger (pin 13) → CW308 TRIG.'],
                  reserve: ['4-6', '<code>out_pins[8]</code> — reserve / software trigger (pin 27) → CW308 TRIG.'] }[JSTATE.J10];
      i.pos = m[0]; i.lines = [m[1], 'Pins 1 and 5 are not connected — fit exactly one link.'];
      break; }
    case 'J12': {
      const m = { ext: ['1-2 · SMA J11', '<b>1-2</b> — external <b>SMA J11</b> drives the chip clock.'],
                  osc: ['3-4 · Y1 50 MHz', '<b>3-4</b> — on-board <b>Y1 50 MHz</b> (via R22 20 Ω) drives the chip clock.'],
                  cw: ['5-6 · CW clock', '<b>5-6</b> — the <b>ChipWhisperer clock</b> drives the chip: it arrives on J7.3 (the CW308 <code>CLKFB</code> line) through R8 100 Ω.'] }[JSTATE.J12];
      i.pos = m[0];
      i.lines = [m[1],
                 'Exactly <b>one</b> link fitted (silk: <i>Clk select — SMA / Osc / Cw</i>). The chip clock is <b>hard-wired to J7.5</b> — a permanent clock <b>echo out</b>, so the ChipWhisperer can always observe / sample it synchronously, whatever the source.'];
      i.caution = J3NOTE;
      break; }
  }
  return i;
}

/* ---------------- presets ---------------- */
const PRESETS = {
  A: { name: 'A — USB bench bring-up', sub: 'talk to PROACT from a PC · no ChipWhisperer',
    set: { JP1: 'open', JP3: 'm', JP5: 'm', JP4: 'm', JP6: 'm', JP7: 'direct', J6: { ssel: 'm', trig: false }, J10: 'normal', J12: 'osc' },
    match: s => s.JP1 === 'open' && s.JP3 === 'm' && s.JP5 === 'm' && s.JP4 === 'm' && s.JP6 === 'm' && s.JP7 === 'direct' && s.J6.ssel === 'm' && !s.J6.trig && s.J12 === 'osc',
    rows: [['JP3 · JP5 · JP4 · JP6', 'M (2-3)'], ['J6', '1-3 — MCP2210 GPIO4 → SSel_n'], ['J12', '3-4 — on-board 50 MHz'], ['JP7', '2-3 — direct'], ['JP1', 'open (reset via SW1)']],
    notes: ['S1-6 & S1-7 <b>on</b> (power both bridge modules); S1-1…5 on as needed for read-back', 'Feed 3.3 V into the VDDIO test point', 'Precondition: Vcore trimmed to 0.800 V'] },
  B: { name: 'B — ChipWhisperer capture, CW-clocked', sub: 'board mounted on the CW308',
    set: { JP1: 'closed', JP3: 'cw', JP5: 'cw', JP4: 'cw', JP6: 'cw', JP7: 'filt', J6: { ssel: 'cw', trig: false }, J10: 'normal', J12: 'cw' },
    match: s => s.JP3 === 'cw' && s.JP5 === 'cw' && s.JP4 === 'cw' && s.JP6 === 'cw' && s.JP7 === 'filt' && s.J6.ssel === 'cw' && !s.J6.trig && s.J12 === 'cw',
    rows: [['JP3 · JP5 · JP4 · JP6', 'CW (1-2)'], ['J6', '3-5 — CW GPIO3 → SSel_n'], ['J10', '2-4 normal · 3-4 config · 4-6 reserve'], ['J12', '5-6 — the ChipWhisperer clock (arrives on J7.3, via R8 100 Ω)'], ['CW308 J3', 'unpopulated — it drives J7.5 (the clock-echo pin) and would fight the selected source'], ['JP7', '1-2 — through the CW308 filter'], ['JP1', 'closed (reset from CW308 nRST) if desired']],
    notes: ['S1-6 / S1-7 <b>off</b> — the USB bridges stay idle and unpowered during capture', 'Precondition: Vcore trimmed to 0.800 V'] },
  C: { name: 'C — Synchronous capture from the on-board oscillator', sub: 'flagship SCA setup',
    set: { JP1: 'closed', JP3: 'cw', JP5: 'cw', JP4: 'cw', JP6: 'cw', JP7: 'filt', J6: { ssel: 'cw', trig: false }, J10: 'normal', J12: 'osc' },
    match: s => s.JP3 === 'cw' && s.JP5 === 'cw' && s.JP4 === 'cw' && s.JP6 === 'cw' && s.JP7 === 'filt' && s.J6.ssel === 'cw' && !s.J6.trig && s.J12 === 'osc',
    rows: [['As configuration B, except:', ''], ['J12', '3-4 — Y1 clocks the chip; the clock is echoed to the scope on J7.5 — sample from it for phase-locked traces'], ['CW308 J3', 'unpopulated']],
    notes: ['S1-6 / S1-7 off', 'Precondition: Vcore trimmed to 0.800 V'] },
};
const matchPreset = () => ['C', 'B', 'A'].find(k => PRESETS[k].match(JSTATE)) || null;

function applyPreset(k) {
  const p = JSON.parse(JSON.stringify(PRESETS[k].set));
  Object.assign(JSTATE, p);
  state.sel = null; state.net = null; state.multi = null;
  if (VIEW !== 'T') setView('T', true);          // the jumpers are on the top side
  redrawShunts(); applyMapState(); updatePresetUI();
  panelPreset(k);
}

function updatePresetUI() {
  const m = matchPreset();
  $$('#presetbar .pbtn').forEach(b => b.classList.toggle('on', b.dataset.preset === m));
  $('#presetmatch').innerHTML = m ? `matches configuration <b>${m}</b>` : '';
}

/* ---------------- board map (real render + markers) ---------------- */
const rotAABB = c => (c.rot % 180 === 90) ? [c.h, c.w] : [c.w, c.h];
const markR = c => Math.min(Math.max(Math.min(c.w, c.h) * PXMM * 0.42, 11), 16);

function buildMap() {
  const v = vw();
  const markers = [], dots = [], pins = [];
  const comps = Object.entries(CMP).sort((a, b) => (b[1].w * b[1].h) - (a[1].w * a[1].h));
  for (const [ref, c] of comps) {
    if (c.layer !== VIEW) continue;
    const px = mx(c.x), py = my(c.y), r = markR(c);
    const fill = c.led ? c.led[1] : c.color;
    markers.push(`<g class="comp" data-ref="${ref}" transform="translate(${px.toFixed(1)},${py.toFixed(1)})">` +
      `<circle class="hit" r="${(r + 10).toFixed(1)}"/><circle class="mk" r="${r.toFixed(1)}" fill="${fill}"/></g>`);
    if (VIEW === 'T' && HIDDENPIN.includes(ref) && c.pads)   // always-on gold pin dots on the true pads
      for (const p of Object.values(c.pads))
        pins.push(`<circle cx="${mx(p[0]).toFixed(1)}" cy="${my(p[1]).toFixed(1)}" r="${((p[2] || 1.6) * PXMM * 0.25).toFixed(1)}"/>`);
    if (VIEW === 'T' && STATEFUL.includes(ref) && c.pads && c.pads['1'])   // pin-1 dot to orient the links
      dots.push(`<circle cx="${mx(c.pads['1'][0]).toFixed(1)}" cy="${my(c.pads['1'][1]).toFixed(1)}" r="5" fill="#ffffff" stroke="#0b1220" stroke-width="2"/>`);
  }
  $('#boardmap').innerHTML =
    `<svg viewBox="0 0 ${v.iw} ${v.ih}" font-family="Inter,'Segoe UI',Arial,sans-serif">` +
    `<defs><mask id="dimmask"><rect width="${v.iw}" height="${v.ih}" fill="#fff"/><g id="dimholes"></g></mask></defs>` +
    `<image href="${v.img}" x="0" y="0" width="${v.iw}" height="${v.ih}"/>` +
    `<g id="gpins" fill="#e8c840" stroke="#8a6d1a" stroke-width="2">${pins.join('')}</g>` +
    `<rect id="dimover" width="${v.iw}" height="${v.ih}" fill="#0b1220" fill-opacity="0.55" mask="url(#dimmask)" style="display:none"/>` +
    `<g id="gshunts" class="shl"></g>` +
    `<g id="gdots" class="padl">${dots.join('')}</g>` +
    `<g id="gcomps">${markers.join('')}</g>` +
    `<g id="grings" class="ringl"></g>` +
    `<g id="glabels" class="lbl"></g></svg>` +
    `<div id="mapcap">${VIEW === 'T'
      ? 'top view · CAD render of the board · white dot = pin 1 of a routing jumper · click a selected jumper again to cycle its position'
      : 'bottom view — mirrored left-right (the board seen from below) · jumpers &amp; LEDs live on the top side'}</div>` +
    `<button id="flipnote" style="display:none"></button>`;
}

function setView(k, keepSel) {
  if (VIEW === k || !VIEWS[k]) return;
  VIEW = k;
  $$('#viewtog button').forEach(b => b.classList.toggle('on', b.dataset.view === k));
  buildMap(); redrawShunts(); applyMapState();
  if (!keepSel) renderPanel();
}

function redrawShunts() {
  const g = $('#gshunts'); if (!g) return;
  if (VIEW !== 'T') { g.innerHTML = ''; return; }        // all stateful jumpers are top-side
  const out = [];
  for (const ref of STATEFUL) {
    const c = CMP[ref]; if (!c || !c.pads) continue;
    const links = jumperLinks(ref) || [];
    if (!links.length) {  // open — dashed outline around the pad field
      const xs = Object.values(c.pads).map(p => p[0]), ys = Object.values(c.pads).map(p => p[1]);
      out.push(`<rect x="${(mx(Math.min(...xs)) - 19).toFixed(1)}" y="${(my(Math.max(...ys)) - 19).toFixed(1)}" width="${(mx(Math.max(...xs)) - mx(Math.min(...xs)) + 38).toFixed(1)}" height="${(my(Math.min(...ys)) - my(Math.max(...ys)) + 38).toFixed(1)}" rx="10" fill="none" stroke="#e2e8f0" stroke-opacity="0.9" stroke-width="3" stroke-dasharray="9,7"/>`);
      continue;
    }
    for (const [a, b] of links) {
      const pa = c.pads[a], pb = c.pads[b];
      if (!pa || !pb) continue;
      const x0 = Math.min(mx(pa[0]), mx(pb[0])) - 15, x1 = Math.max(mx(pa[0]), mx(pb[0])) + 15;
      const y0 = Math.min(my(pa[1]), my(pb[1])) - 15, y1 = Math.max(my(pa[1]), my(pb[1])) + 15;
      out.push(`<rect x="${x0.toFixed(1)}" y="${y0.toFixed(1)}" width="${(x1 - x0).toFixed(1)}" height="${(y1 - y0).toFixed(1)}" rx="9" fill="${c.color}" fill-opacity="0.45" stroke="${c.color}" stroke-width="4"/>`);
    }
  }
  g.innerHTML = out.join('');
}

/* ---------------- map state ---------------- */
const state = { tab: 'map', sel: null, net: null, multi: null, q: '', cat: 'all' };

const matchComp = (ref, q) => {
  const c = CMP[ref];
  return (ref + ' ' + plain(c.name) + ' ' + plain(c.role || '') + ' ' + (c.silk || '') + ' ' + c.cat).toLowerCase().includes(q);
};

function ringGeom(ref) {           // rounded-rect ring around the part, in render px
  const c = CMP[ref], [aw, ah] = rotAABB(c);
  const wp = Math.max(aw * PXMM, 30) + 22, hp = Math.max(ah * PXMM, 30) + 22;
  return { x: mx(c.x) - wp / 2, y: my(c.y) - hp / 2, w: wp, h: hp };
}
function ringRect(ref, color, thin) {
  const g = ringGeom(ref);
  return `<rect class="ring${thin ? ' thin' : ''}" x="${g.x.toFixed(1)}" y="${g.y.toFixed(1)}" width="${g.w.toFixed(1)}" height="${g.h.toFixed(1)}" rx="12" stroke="${color}"/>`;
}
const GREEN = '#4ade80';           // v1 behaviour: net-connected parts get green rings

function applyMapState() {
  let active = null, rel = new Set(), rings = [], labeled = null;
  if (state.multi) {
    active = new Set(state.multi.refs);
    rings = state.multi.refs.filter(r => CMP[r].layer === VIEW).map(r => ringRect(r, GREEN));
    labeled = active;
  } else if (state.net) {
    const n = NETS[state.net];
    if (!n.power) {
      active = netComps[state.net];
      rings = [...active].filter(r => CMP[r].layer === VIEW).map(r => ringRect(r, GREEN, true));
      labeled = active;
    }
  } else if (state.sel) {
    rel = related[state.sel] || new Set();
    active = new Set([state.sel, ...rel]);
    rings = [...rel].filter(r => CMP[r].layer === VIEW).map(r => ringRect(r, GREEN, true));
    if (CMP[state.sel].layer === VIEW) rings.push(ringRect(state.sel, CMP[state.sel].color));
    labeled = active;
  } else if (state.q || state.cat !== 'all') {
    active = new Set(Object.keys(CMP).filter(r =>
      (state.cat === 'all' || CMP[r].cat === state.cat) && (!state.q || matchComp(r, state.q))));
    labeled = active;
  }
  $$('#gcomps .comp').forEach(g => {
    const r = g.dataset.ref;
    g.classList.toggle('dim', !!(active && !active.has(r)));
    g.classList.toggle('rel', rel.has(r));
  });
  /* dim the render outside the active parts (mask holes keep them lit) */
  const over = $('#dimover'), holes = $('#dimholes');
  const onView = active ? [...active].filter(r => CMP[r].layer === VIEW) : [];
  if (over && holes) {
    if (active) {
      holes.innerHTML = onView.map(r => {
        const g = ringGeom(r);
        return `<rect x="${(g.x - 4).toFixed(1)}" y="${(g.y - 4).toFixed(1)}" width="${(g.w + 8).toFixed(1)}" height="${(g.h + 8).toFixed(1)}" rx="14" fill="#000"/>`;
      }).join('');
      over.style.display = '';
    } else { over.style.display = 'none'; holes.innerHTML = ''; }
  }
  /* ref labels above the active parts */
  const lg = $('#glabels');
  if (lg) lg.innerHTML = (labeled && onView.length && onView.length <= 45)
    ? onView.map(r => {
        const g = ringGeom(r);
        return `<text x="${(g.x + g.w / 2).toFixed(1)}" y="${(g.y - 10).toFixed(1)}" text-anchor="middle">${r}</text>`;
      }).join('')
    : '';
  $('#grings').innerHTML = rings.join('');
  /* parts of the active set living on the hidden side → offer a view flip */
  const fn = $('#flipnote');
  if (fn) {
    const hid = active ? [...active].filter(r => CMP[r].layer !== VIEW).length : 0;
    if (hid) {
      fn.textContent = `${hid} highlighted part${hid > 1 ? 's' : ''} on the ${VIEW === 'T' ? 'BOTTOM' : 'TOP'} side — flip view`;
      fn.style.display = '';
      fn.onclick = () => setView(VIEW === 'T' ? 'B' : 'T', true);
    } else fn.style.display = 'none';
  }
}

/* ---------------- side panel ---------------- */
const panel = () => $('#panel');
const chipC = r => `<button class="chip" data-comp="${r}" title="${plain(CMP[r].name)}">${r}</button>`;
const chipN = id => {
  const n = NETS[id];
  return `<button class="chip${n.power ? ' pw' : ''}" data-net="${id}">${n.label} <span class="n">· ${n.members.length} pins</span></button>`;
};

function jumperControls(ref) {
  const col = CMP[ref].color;
  const bt = (grp, val, lab, on) =>
    `<button data-jset="${ref}|${grp}|${val}"${on ? ` class="on" style="background:${col};border-color:${col}"` : ''}>${lab}</button>`;
  let h = '<div class="jctl">';
  if (['JP3', 'JP5', 'JP4', 'JP6'].includes(ref))
    h += `<div class="jopt">${bt('pos', 'cw', '1-2 · CW', JSTATE[ref] === 'cw')}${bt('pos', 'm', '2-3 · M module', JSTATE[ref] === 'm')}</div>`;
  else if (ref === 'JP1')
    h += `<div class="jopt">${bt('pos', 'open', 'open', JSTATE.JP1 === 'open')}${bt('pos', 'closed', 'closed', JSTATE.JP1 === 'closed')}</div>`;
  else if (ref === 'JP7')
    h += `<div class="jopt">${bt('pos', 'filt', '1-2 · filtered', JSTATE.JP7 === 'filt')}${bt('pos', 'direct', '2-3 · direct', JSTATE.JP7 === 'direct')}</div>`;
  else if (ref === 'J10')
    h += `<div class="jopt">${bt('pos', 'normal', '2-4 · normal', JSTATE.J10 === 'normal')}${bt('pos', 'config', '3-4 · config', JSTATE.J10 === 'config')}${bt('pos', 'reserve', '4-6 · reserve', JSTATE.J10 === 'reserve')}</div>`;
  else if (ref === 'J6')
    h += `<div class="jlab">S-Sel source</div><div class="jopt">${bt('ssel', 'm', '1-3 · module', JSTATE.J6.ssel === 'm')}${bt('ssel', 'cw', '3-5 · CW GPIO3', JSTATE.J6.ssel === 'cw')}</div>` +
         `<div class="jlab">trigger-in link</div><div class="jopt">${bt('trig', '0', '5-6 off', !JSTATE.J6.trig)}${bt('trig', '1', '5-6 on · CW → trigger_in', JSTATE.J6.trig)}</div>`;
  else if (ref === 'J12')
    h += `<div class="jlab">clock source — exactly one link</div><div class="jopt">${bt('src', 'ext', '1-2 · SMA J11', JSTATE.J12 === 'ext')}${bt('src', 'osc', '3-4 · Y1 50 MHz', JSTATE.J12 === 'osc')}${bt('src', 'cw', '5-6 · ChipWhisperer', JSTATE.J12 === 'cw')}</div>` +
         `<div class="jlab">clock echo out</div><div class="jopt"><span class="phint">J7.5 always carries the chip clock to the ChipWhisperer — hard-wired, not a jumper.</span></div>`;
  h += '</div>';
  const i = jumperStateInfo(ref);
  h += `<div class="jstate"><div class="jpos" style="color:${col}">current: ${i.pos}</div>${i.lines.map(l => `<div>${l}</div>`).join('')}</div>`;
  if (i.caution) h += `<div class="jcaution">⚠ ${i.caution}</div>`;
  return h;
}

function panelComp(ref) {
  const c = CMP[ref];
  let h = `<button class="pback" data-clear="1">← board map</button>` +
    `<div class="ph"><span class="dot" style="background:${c.color}"></span><h2>${ref}</h2><span class="pill">${c.cat}</span>` +
    `<span class="pill">${c.layer === 'B' ? 'bottom side' : 'top side'}</span></div>` +
    `<div class="pname">${c.name}</div>` + (c.role ? `<p class="prole">${c.role}</p>` : '');
  if (c.led) h += `<div class="ledline"><span class="ledsw" style="background:${c.led[1]}"></span>mounted color: <b>${c.led[0]}</b>` +
    `<span class="phint"> — the CAD render shows a placeholder LED body; mounted parts follow the BOM</span></div>`;
  if (c.silk) h += `<div class="silkline">silk label: <span class="silktx">${c.silk}</span></div>`;
  if (STATEFUL.includes(ref)) { h += `<h4>Position — set it here or click the jumper on the map</h4>` + jumperControls(ref); }
  if (c.desc) h += c.desc;
  if (c.routing) h += c.routing;
  if (c.jcard) h += `<p><button class="lk" data-jcard="${c.jcard}">Open the jumper card ⤢</button> <button class="lk" data-tab="jumpers">Jumpers tab</button></p>`;
  if (ref === 'S1') {
    h += '<h4>Switches</h4><div class="chips">' +
      DATA.s1.map(s => `<button class="chip" data-s1="${s.n}">S1-${s.n} <span class="n">· ${plain(s.title)}</span></button>`).join('') + '</div>';
  }
  const nets = [...(compNets[ref] || [])].sort((a, b) => NETS[a].label.localeCompare(NETS[b].label));
  if (nets.length) h += `<h4>Nets</h4><div class="chips">${nets.map(chipN).join('')}</div>`;
  const rel = [...(related[ref] || [])].sort();
  if (rel.length) h += `<h4>Connected components <span class="phint">(shared signal nets — highlighted on the map)</span></h4><div class="chips">${rel.map(chipC).join('')}</div>`;
  panel().innerHTML = h;
}

function panelNet(id) {
  const n = NETS[id];
  let h = `<button class="pback" data-clear="1">← board map</button>` +
    `<div class="ph"><span class="dot" style="background:#e2e8f0"></span><h2 style="font-size:17px">${n.label}</h2>${n.power ? '<span class="pill">power net</span>' : ''}</div>`;
  if (n.desc) h += `<p class="prole">${n.desc}</p>`;
  if (n.power) h += `<div class="note">Power / ground net — map highlighting is suppressed (it would light most of the board).</div>`;
  h += `<h4>Pins on this net (${n.members.length})</h4><div class="chips">` +
    n.members.map(m => { const r = m.split('.')[0]; return `<button class="chip" data-comp="${r}" title="${CMP[r] ? plain(CMP[r].name) : ''}">${m}</button>`; }).join('') + '</div>';
  const refs = [...(netComps[id] || [])].sort();
  h += `<h4>Components</h4><div class="chips">${refs.map(chipC).join('')}</div>`;
  panel().innerHTML = h;
}

function panelMulti() {
  const m = state.multi;
  panel().innerHTML = `<button class="pback" data-clear="1">← board map</button>` +
    `<div class="ph"><span class="dot" style="background:#fbbf24"></span><h2 style="font-size:17px">${m.title}</h2></div>` +
    (m.desc || '') +
    `<h4>Highlighted components</h4><div class="chips">${m.refs.map(chipC).join('')}</div>`;
}

function panelPreset(k) {
  const p = PRESETS[k];
  panel().innerHTML = `<button class="pback" data-clear="1">← board map</button>` +
    `<div class="ph"><span class="dot" style="background:#fbbf24"></span><h2 style="font-size:17px">Configuration ${p.name}</h2></div>` +
    `<p class="prole">${p.sub} — all jumpers on the map are now set accordingly.</p>` +
    `<ul class="psum">${p.rows.map(r => `<li><span class="pv">${r[0]}</span>${r[1] ? ' — ' + r[1] : ''}</li>`).join('')}</ul>` +
    `<h4>Also required (not jumpers)</h4><ul class="psum">${p.notes.map(n => `<li>${n}</li>`).join('')}</ul>` +
    `<p><button class="lk" data-tab="cfg">Configurations tab →</button></p>`;
}

function panelDefault() {
  const m = matchPreset();
  panel().innerHTML =
    `<div class="ph"><h2 style="font-size:17px">Board map</h2></div>
     <p class="prole">The map is a <b>CAD render of the board</b> — click any marker for the part’s role, routing and nets:
     net-connected components get <b style="color:#4ade80">green rings</b> while the rest of the board dims.
     Click a <b>net</b> chip to trace it across the board. <b>TOP / BOTTOM</b> switches the side
     (decoupling, LED resistors and the CW308 edge connectors live on the bottom).</p>
     <div class="note"><b>Jumpers are live:</b> click a jumper to select it, click it again to cycle its position —
     the shunt cap moves on the map and the description follows. Or use the position buttons in this panel.
     ${m ? `Current jumper state matches configuration <b>${m}</b>.` : ''}</div>
     <h4>Quick picks</h4><div class="chips">${['U1', 'JP7', 'J12', 'R7', 'S1', 'J7', 'J8'].map(chipC).join('')}</div>
     <h4>Search examples</h4><p class="phint">“sck”, “shunt”, “reset”, “clock”, “LED”, “JP”, “trigger_in”…</p>
     <h4>Board facts</h4>
     <p class="phint">${Object.keys(CMP).length} components · ${Object.keys(NETS).length} nets · 53.9 × 93.3 mm ·
     PROACT ASIC in DIP-28 socket U1 · CW308 UFO target</p>`;
}

function renderPanel() {
  if (state.multi) panelMulti();
  else if (state.net) panelNet(state.net);
  else if (state.sel) panelComp(state.sel);
  else panelDefault();
}

/* ---------------- selection ---------------- */
function showTab(k) {
  state.tab = k;
  $$('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.tabbtn === k));
  $$('.tab').forEach(s => s.classList.toggle('on', s.id === 'tab-' + k));
  window.scrollTo({ top: 0 });
}
function selectComp(ref) {
  if (!CMP[ref]) return;
  showTab('map');
  if (CMP[ref].layer !== VIEW) setView(CMP[ref].layer, true);   // part lives on the other side
  state.sel = ref; state.net = null; state.multi = null;
  applyMapState(); renderPanel();
}
function selectNet(id) {
  if (!NETS[id]) return;
  showTab('map');
  state.net = id; state.sel = null; state.multi = null;
  applyMapState(); renderPanel();
  if (NETS[id].power) toast('Power net — map highlighting suppressed');
}
function selectMulti(title, refs, desc) {
  showTab('map');
  state.multi = { title, refs: refs.filter(r => CMP[r]), desc: desc || '' };
  state.sel = null; state.net = null;
  applyMapState(); renderPanel();
}
function clearSel() {
  state.sel = null; state.net = null; state.multi = null; state.q = ''; state.cat = 'all';
  $('#q').value = '';
  $$('#cats .catchip').forEach(b => b.classList.toggle('on', b.dataset.cat === 'all'));
  applyMapState(); renderPanel();
}

function cycleJumper(ref) {
  if (ref === 'JP1') JSTATE.JP1 = JSTATE.JP1 === 'open' ? 'closed' : 'open';
  else if (['JP3', 'JP5', 'JP4', 'JP6'].includes(ref)) JSTATE[ref] = JSTATE[ref] === 'cw' ? 'm' : 'cw';
  else if (ref === 'JP7') JSTATE.JP7 = JSTATE.JP7 === 'filt' ? 'direct' : 'filt';
  else if (ref === 'J10') JSTATE.J10 = { normal: 'config', config: 'reserve', reserve: 'normal' }[JSTATE.J10];
  else if (ref === 'J12') JSTATE.J12 = { ext: 'osc', osc: 'cw', cw: 'ext' }[JSTATE.J12];
  else if (ref === 'J6') JSTATE.J6.ssel = JSTATE.J6.ssel === 'm' ? 'cw' : 'm';
  else return;
  redrawShunts(); renderPanel(); updatePresetUI();
  toast(`${ref} → ${jumperStateInfo(ref).pos}`);
}

/* ---------------- S1 explorer ---------------- */
const S1FN = { 1: 'SSel read', 2: 'J1 dbg read', 3: 'GRST read', 4: 'SPI_RST read', 5: 'CRST read', 6: 'MCP2200 pwr', 7: 'MCP2210 pwr' };
let s1sel = null;
function buildS1() {
  const x = $('#s1x'); if (!x) return;
  x.innerHTML = '<div class="onoff" style="align-self:flex-start">ON ▲<br><br><br>OFF ▼</div>' +
    DATA.s1.map(s =>
      `<button class="swt" data-swn="${s.n}"><span class="k">${s.n}</span><span class="well"><span class="knob"></span></span><span class="fn">${S1FN[s.n]}</span></button>`).join('');
  s1detail(null);
}
function s1detail(n) {
  const d = $('#s1detail'); if (!d) return;
  if (!n) { d.innerHTML = '<p class="mut">Select a switch above to see its loop.</p>'; return; }
  const s = DATA.s1[n - 1];
  const kcol = s.kind === 'power' ? '#f59e0b' : '#38bdf8';
  d.innerHTML = `<div class="card" style="border-color:${kcol}55">
    <div class="ph"><h3 style="margin:0">S1-${s.n} — ${s.title}</h3>
    <span class="kindpill" style="background:${kcol}">${s.kind === 'power' ? 'power enable' : 'read-back loop'}</span>
    <span class="pill">${s.pins}</span></div>
    <p>${s.purpose}</p>
    <div class="path">${s.path}</div>
    <div class="chips">${s.loop.map(chipC).join('')}</div>
    <p style="margin-top:10px"><button class="lk" data-s1map="${s.n}">Highlight the loop on the board map →</button></p></div>`;
}
function selectSwitch(n) {
  /* selection only — the drawn nub always stays OFF (down); the explorer is
     documentation, not a state model, so viewing a switch must not flip it */
  s1sel = n;
  $$('#s1x .swt').forEach(b => b.classList.toggle('active', +b.dataset.swn === n));
  s1detail(n);
}

/* ---------------- search ---------------- */
function searchResults(q) {
  q = q.toLowerCase();
  const comps = Object.keys(CMP).filter(r => matchComp(r, q))
    .sort((a, b) => (b.toLowerCase().startsWith(q)) - (a.toLowerCase().startsWith(q)) || a.localeCompare(b)).slice(0, 9);
  const nets = Object.keys(NETS).filter(id => (NETS[id].label + ' ' + id).toLowerCase().includes(q)).slice(0, 5);
  return { comps, nets };
}
function renderDrop() {
  const d = $('#qdrop'), q = state.q;
  if (!q) { d.classList.remove('on'); return; }
  const { comps, nets } = searchResults(q);
  if (!comps.length && !nets.length) { d.innerHTML = '<div class="qh">no matches</div>'; d.classList.add('on'); return; }
  let h = '';
  if (comps.length) h += '<div class="qh">Components</div>' + comps.map(r =>
    `<button class="qi" data-comp="${r}"><span class="qr" style="color:${CMP[r].color}">${r}</span><span class="qn">${plain(CMP[r].name)}</span></button>`).join('');
  if (nets.length) h += '<div class="qh">Nets</div>' + nets.map(id =>
    `<button class="qi" data-net="${id}"><span class="qr">⌁</span><span class="qn">${NETS[id].label}</span></button>`).join('');
  d.innerHTML = h; d.classList.add('on');
}

/* ---------------- misc ui ---------------- */
let toastT = null;
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('on');
  clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove('on'), 2200);
}
function openModal(key) {
  $('#modalbody').innerHTML = DATA.jcards[key] || '';
  $('#modal').classList.add('on');
}
function buildCats() {
  $('#cats').innerHTML = ['all', ...CATS].map(c =>
    `<button class="catchip${c === 'all' ? ' on' : ''}" data-cat="${c}">${c}</button>`).join('');
}
function buildLegend() {
  const L = [['UART', '#14b8a6'], ['SPI', '#3b82f6'], ['reset', '#f87171'], ['trigger', '#a78bfa'],
             ['power', '#f59e0b'], ['clock', '#22d3ee'], ['CW308', '#38bdf8'], ['debug', '#facc15']];
  $('#maplegend').innerHTML = L.map(([t, c]) => `<span class="li"><span class="dot" style="background:${c}"></span>${t}</span>`).join('');
}
function buildPresets() {
  const bar = document.createElement('div');
  bar.id = 'presetbar';
  bar.innerHTML = '<span class="phint">Presets:</span>' +
    ['A', 'B', 'C'].map(k => `<button class="pbtn" data-preset="${k}" title="${plain(PRESETS[k].name)}">${k} · ${k === 'A' ? 'USB bench' : k === 'B' ? 'CW capture' : 'sync capture'}</button>`).join('') +
    '<span id="presetmatch"></span>';
  $('#mapcol').insertBefore(bar, $('#boardmap'));
}

/* ---------------- events ---------------- */
document.addEventListener('click', e => {
  const t = e.target;
  const el = sel => t.closest(sel);
  let m;
  if ((m = el('[data-tabbtn]'))) { showTab(m.dataset.tabbtn); return; }
  if ((m = el('#viewtog button'))) { setView(m.dataset.view); return; }
  if ((m = el('[data-jset]'))) {
    const [ref, grp, val] = m.dataset.jset.split('|');
    if (grp === 'pos') JSTATE[ref] = val;
    else if (grp === 'ssel') JSTATE.J6.ssel = val;
    else if (grp === 'trig') JSTATE.J6.trig = val === '1';
    else if (grp === 'src') JSTATE.J12 = val;
    redrawShunts(); renderPanel(); updatePresetUI();
    return;
  }
  if ((m = el('[data-preset]'))) { applyPreset(m.dataset.preset); return; }
  if ((m = el('[data-jcard]'))) { openModal(m.dataset.jcard); return; }
  if ((m = el('[data-s1map]'))) {
    const s = DATA.s1[+m.dataset.s1map - 1];
    selectMulti(`S1-${s.n} — ${s.title}`, s.loop, `<div class="path" style="font-size:12px">${s.path}</div>`);
    return;
  }
  if ((m = el('[data-s1]'))) { showTab('s1'); selectSwitch(+m.dataset.s1); return; }
  if ((m = el('#s1x .swt'))) { selectSwitch(+m.dataset.swn); return; }
  if ((m = el('[data-pin]'))) {
    const id = pinNet(+m.dataset.pin);
    if (id) selectNet(id);
    return;
  }
  if ((m = el('[data-net]'))) { selectNet(m.dataset.net); $('#qdrop').classList.remove('on'); return; }
  if ((m = el('g.comp'))) {
    const ref = m.dataset.ref;
    if (state.sel === ref && STATEFUL.includes(ref)) cycleJumper(ref);
    else selectComp(ref);
    return;
  }
  if ((m = el('[data-comp]'))) { selectComp(m.dataset.comp); $('#qdrop').classList.remove('on'); return; }
  if ((m = el('[data-tab]'))) {
    showTab(m.dataset.tab);
    if (m.dataset.s1) selectSwitch(+m.dataset.s1);
    return;
  }
  if ((m = el('[data-cat]'))) {
    state.cat = m.dataset.cat;
    $$('#cats .catchip').forEach(b => b.classList.toggle('on', b === m));
    state.sel = null; state.net = null; state.multi = null;
    applyMapState(); renderPanel();
    return;
  }
  if (el('[data-clear]') || el('#resetview')) { clearSel(); return; }
  if (el('#modalclose') || (t.id === 'modal')) { $('#modal').classList.remove('on'); return; }
  if (!el('.hsearch')) $('#qdrop').classList.remove('on');
});

$('#q').addEventListener('input', e => {
  state.q = e.target.value.trim();
  state.sel = null; state.net = null; state.multi = null;
  renderDrop();
  if (state.tab === 'map') { applyMapState(); if (!state.q) renderPanel(); }
});
$('#q').addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    const { comps, nets } = searchResults(state.q || '');
    if (comps.length) selectComp(comps[0]);
    else if (nets.length) selectNet(nets[0]);
    $('#qdrop').classList.remove('on');
  }
  if (e.key === 'Escape') { $('#q').value = ''; state.q = ''; renderDrop(); applyMapState(); }
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    if ($('#modal').classList.contains('on')) $('#modal').classList.remove('on');
    else if (state.sel || state.net || state.multi) { clearSel(); }
  }
});

/* tooltip */
document.addEventListener('mousemove', e => {
  const tip = $('#tooltip');
  const g = e.target.closest && e.target.closest('#boardmap g.comp');
  if (!g) { tip.style.display = 'none'; return; }
  const c = CMP[g.dataset.ref];
  tip.innerHTML = `<span class="tr" style="color:${c.color}">${g.dataset.ref}</span> <span class="tn">${plain(c.name)}</span>` +
    (c.led ? `<div class="tn"><span class="ledsw sm" style="background:${c.led[1]}"></span>mounted: ${c.led[0]}</div>` : '') +
    (c.silk ? `<div class="tn">silk: ${plain(c.silk)}</div>` : '') +
    (STATEFUL.includes(g.dataset.ref) ? `<div class="tn">position: ${jumperStateInfo(g.dataset.ref).pos}${state.sel === g.dataset.ref ? ' · click to cycle' : ''}</div>` : '');
  tip.style.display = 'block';
  const vw = window.innerWidth;
  tip.style.left = Math.min(e.clientX + 14, vw - 300) + 'px';
  tip.style.top = (e.clientY + 16) + 'px';
});

/* ---------------- init ---------------- */
buildMap(); buildCats(); buildLegend(); buildPresets(); buildS1();
redrawShunts(); applyMapState(); renderPanel(); updatePresetUI();

/* deep links: #tab (map/overview/…), #S1-3 (switch), #JP7 (component), #CLK_pin (net) */
function applyHash() {
  const h = decodeURIComponent((location.hash || '').slice(1));
  if (!h) return;
  const mS1 = h.match(/^s1-([1-7])$/i);
  const mPre = h.match(/^preset=([ABC])$/i);
  if (mPre) { applyPreset(mPre[1].toUpperCase()); return; }
  if (document.getElementById('tab-' + h.toLowerCase())) showTab(h.toLowerCase());
  else if (mS1) { showTab('s1'); selectSwitch(+mS1[1]); }
  else if (CMP[h]) selectComp(h);
  else if (NETS[h]) selectNet(h);
}
window.addEventListener('hashchange', applyHash);
applyHash();
