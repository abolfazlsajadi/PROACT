# INTERACTIVE-WIKI agent notes (durable memory)

Maintained by the interactive-wiki agent. Update whenever a decision changes.
Deliverable: `docs/PROACT-Board-Wiki-v2.html` (single file, self-contained, file:// safe).

## Build pipeline
- Generator: `docs/tools/gen_wiki_data.py` (stdlib + olefile + PIL). Run:
  `python3 docs/tools/gen_wiki_data.py` -> writes the HTML.
- It reads app sources `docs/tools/wiki_app.css` and `docs/tools/wiki_app.js` and splices them
  inline with a JSON data blob (`<script type="application/json" id="wiki-data">`), JSON is
  `.replace('</','<\\/')`-escaped so it is safe inside the script tag.
- PcbDoc extraction recipe copied from `gen_jumpers.py` (see `jumper_agent_notes.md`):
  Components6 (des/x/y/rot/lib + LAYER TOP/BOTTOM, mil*0.0254), Pads6 (geometry block:
  comp u16@7, x i32@13, y i32@17, 1/10000 mil), board X 319.40-373.25, Y 196.72-290.07 mm.
- GEOMETRY SOURCE since 2026-08-06: `PCB_finalx/PCB_Projectcxfinal/PCB1.PcbDoc` (final silk;
  76 comps — 56 top, 20 bottom: C5-C10, J7/J8/J9, R6, R9-R18). Netlist unchanged.
- Facts: ALL text sourced from `/home/abish/Downloads/PCB/PROACT_DOC/README.md` (v2 verified);
  connectivity from `/tmp/netlist_final.txt`. Never invent.

## Netlist model (verified 2026-08-06)
- Lines `name : members`; SAME-NAME lines merge into one net; `(unnamed)` lines are each their
  own net; single-member nets = unconnected pins (dropped from the data blob).
- Hand-assigned labels for key unnamed nets live in `NETNAMES` in the generator (keyed by
  frozenset of members). Leftover unnamed multi-pin nets get auto label "A ↔ B".
- Net-name traps: nets `D1..D5` are DEBUG SIGNAL nets (out_pins[0], spare_io, out[5], out[6],
  out[11]) — NOT the LEDs D1-D5. Net `RX` = chip-TX side (JP3.1 ↔ J7.8 TIO2); net `TX` =
  chip-RX side (JP5.1 ↔ J7.7 TIO1). Net `A1` = trigger_in (U1.23 ↔ J6.6).
- Highlight graph EXCLUDES power nets GND + VDDIO (would connect everything); they are still
  listed in the side panel with a "power net" badge. 0.8V/Vcore/Vcore_back/Vcore_shunt_1 stay
  in the graph (small membership, meaningful power path).
- S1 (7-pos DIP, pins k & 15-k): 1=S1.1(R3->GPIO4)–S1.14(J4.4 GPIO3) · 2=S1.2(R4->X1)–S1.13
  (J5.4 GPIO7) · 3=S1.3(R1->GRST)–S1.12(J5.3 GPIO8) · 4=S1.4(R2->SPI_RST)–S1.11(J4.1 GPIO0)
  · 5=S1.5(R5->J5.5 GPIO6)–S1.10(CRST) · 6=S1.6(VDDIO)–S1.9(J3.1 MCP2200 VDD) ·
  7=S1.7(VDDIO)–S1.8(J5.1 MCP2210 VDD). All verified against netlist.
- J4 pins 1-7 = MCP2210 module pins 1-7 (GPIO0,GPIO1,GPIO2,GPIO3,GPIO4,MOSI,SCK);
  J5 pins 1-7 = module pins 14..8 (VDD,GND,GPIO8,GPIO7,GPIO6,GPIO5,MISO(nc)).
- LED wiring: debug/alive/spare = signal->LED->R(560/R10-R15)->GND (light on HIGH);
  resets = VDDIO->LED(D7-D10)->R(100: R6,R16,R17,R18)->reset net (light on LOW).
  R map: R10=D1 R11=D2 R12=D3 R13=D4 R14=D5 R15=D6 · R6=D7(B_RST) R16=D8(CRST)
  R17=D9(GRST) R18=D10(SPI_RST).

## Component extents / highlight geometry (BUG FIXED 2026-08-06)
- Pipeline: gen_wiki_data.py `SIZE` dict = w,h in the FOOTPRINT-LIBRARY 0° frame → emitted
  per comp as `w`/`h` + `rot`; wiki_app.js `rotAABB()` (line ~151) swaps w/h when
  `rot % 180 === 90`, `ringGeom()` builds the axis-aligned rect used by ALL of: selection /
  green rings, #dimmask holes, ref-label anchors. Marker radius & hover hit are CIRCLES from
  min(w,h) — rotation-proof. NEVER enter pre-rotated "as placed" extents in SIZE (they get
  swapped AGAIN by rotAABB = the bug).
- BUG (same defect family as gen_jumpers.py / gen_v2_view.py, fixed there earlier same day):
  SIZE had SOCKET_IC_DIP_28 = (36,15.5) and DIPSW254S_7P = (18.5,10) — pre-transposed. With
  U1 rot 270 and S1 rot 90 the double swap drew their highlight windows VERTICAL while both
  parts are HORIZONTAL. Fixed to true 0° frames (15.5,36) / (10,18.5), matching gen_jumpers.py.
- AUDIT 2026-08-06 (scratchpad audit_extents.py): all 76 comps, placed rect long-axis vs
  Pads6 pad-cloud bbox long-axis (pads = absolute ground truth; threshold aspect 1.4) —
  0 mismatches after the fix; the ONLY two rects changed by the fix are U1 and S1. All
  ROTATION values are right angles. Screenshot-verified: #U1 wide horizontal window over the
  socket, S1 wide horizontal (via selectComp — see below), #J2 still vertical (no regression).
- Deep-link gotcha: `#S1` opens the S1-explorer TAB (case-insensitive tab match wins over
  component refs in applyHash), so it can NOT screenshot the S1 map highlight. Recipe: copy
  the page to scratchpad and append `<script>window.addEventListener("load",()=>
  selectComp("S1"))</script>` before `</body>` (app JS is top-level, selectComp is global).

## Design decisions (locked unless coordinator overrides)
- Dark theme identical to diagram family: bg #0b1220, panels #0e1729/#111827, border #1e293b,
  text #f1f5f9, muted #94a3b8, font Inter/system. Accents: UART #14b8a6, SPI #3b82f6, reset
  #f87171, trigger #a78bfa, power #f59e0b, clock #22d3ee, CW #38bdf8, chip #f1f5f9, probe/
  neutral #e2e8f0, debug-LED yellow #facc15, alive #a3e635, spare #fb923c.
- Per-component accent color = FUNCTION (not category). Category is a separate filter facet:
  chip / connector / jumper / led / switch / power / clock / passive / testpoint.
- Tabs: Board map (default) · Overview · Pinout · Jumpers · Clock · Power & Vcore · S1 switch
  · Connectors & LEDs · CW308 setup · Configs · BOM.
- Board map (REBUILT 2026-08-06, user rejected the abstract SVG): the REAL board renders
  (`docs/img/board_final_top/bottom.png`, copies of `new/ff/PCB1/PCB2.png`, 1272x2205) are
  embedded as data URIs inside `<svg viewBox="0 0 1272 2205"><image …>`; category-colored
  circular markers (r = clamp(min(w,h)*px_mm*0.42, 11..16) render px) sit over every part of
  the shown side; TOP / BOTTOM toggle (`#viewtog`, `setView()`); LEDs' marker fill = MOUNTED
  color. Selection: accent pulsing ring on the pick, GREEN (#4ade80) rings on net-connected
  parts (v1 behaviour), rest of the render dimmed by a masked overlay (#dimover + #dimmask
  holes at the active parts) + inactive markers at opacity .25; ref labels drawn above active
  rings (≤45). Parts of the active set on the hidden side → `#flipnote` button ("n parts on
  the BOTTOM side — flip view"); `selectComp` auto-switches to the part's side; presets force
  TOP. Shunt caps / dashed open outlines drawn on the real pad positions (pads ±15 px,
  rx 9; open outline ±19 dashed) — top view only (all stateful jumpers are top).
- mm→render-px calibration (computed at BUILD time in gen_wiki_data.py `render_view()`:
  green-region bbox of each PNG via PIL ImageChops, affine-mapped to the Board6 outline bbox):
  TOP  px = 17 + (x-319.40)*22.9712 · py = 29 + (290.07-y)*22.9888  (y flips)
  BOT  px = 11 + (373.25-x)*23.1941 · py = 20 + (290.07-y)*23.1816  (viewed from below → X
  mirrors too). Verified with a PIL debug overlay (scratchpad) + headless screenshots at
  J11/S1/U1/R7/D1/JP7 (top) and R9/C6-C9/R6-R18 blocks/J7-J9 (bottom): misalignment ≲3 px.
- Hidden-pin headers (2026-08-06): the CAD render shows J6 / J10 / J12 as featureless BLACK
  bodies (the model hides their pins; every other top header shows real gold pins — checked
  J1/J2/J3/J4/J5/JP1-7 crops; S1's nubs are visible enough, left alone). Fix:
  `HIDDENPIN = ['J6','J10','J12']` in wiki_app.js — always-on gold pin dots on the true Pads6
  pad centers (group `<g id="gpins" fill="#e8c840" stroke="#8a6d1a" stroke-width="2">`),
  r = pad_mm × PXMM × 0.25 (these pads are 1.6 mm → r≈9 px). Pads tuples now carry an
  OPTIONAL 3rd element = max pad dimension in mm (gen_wiki_data.py parses topsize at
  g[21:25]/g[25:29] of the Pads6 geometry blob, kept only if 0.2–10 mm; JS falls back to 1.6).
  Layer order: #gpins sits BETWEEN the render <image> and #dimover, i.e. it is part of the
  base render — dims with the board on selection, and always UNDER the shunt caps (dots ghost
  through the 45 %-opacity cap so a fitted link still reads on top). Pin 1 keeps the existing
  white-dot convention (#gdots layer above shunts): J6 pin 1 = top-RIGHT column (1/3/5 right,
  2/4/6 left, rows top→bottom); J10/J12 pin 1 = top-LEFT (odd pins top row). All verified on
  headless screenshots (default preset A, #preset=B J12 5-6 cap over the dotted pins,
  #J6 selected dim/mask behaviour).
- Silk↔designator pairs (read off the renders, stored in `SILK` in the generator, shown as a
  "silk label" chip in the side panel + tooltip + searchable): D1 "Alive?" · D2 "Mem[23]" ·
  D3 "Spare In" · D4 "UART Rvalid" · D5 "Mem req" · D6 "Co req" (v1-era names) · reset column
  top→bottom D7 "B RST", D10 "SPI RST", D8 "C RST", D9 "G RST" · JP3 "Rx · UART CW/M" ·
  JP5 "Tx · UART CW/M" · JP4 "SCK M/CW" · JP6 "MOSI CW/M" · JP1 "B_RST→CW" · JP2 "PC: 2 3 4" ·
  JP7 "Vcore select · direct / filt" · J6 "S-SEL / TRIG-IN" (+ spi s_sel / pin18 / Pin23; text
  under the header body partly hidden in the render) · J10 "Trig select — Pin13 (trig cfg) ·
  (Trig norm) Pin14 / io4 Cw / Pin27 · (Trig rsv)" · J12 "Clk select — SMA / Osc / Cw" ·
  R20 "Vadj SET 0.8V" · R7 "0.01Ω shunt" · SW1 "B RST" · J1 "X1 Dbug" · J11 "EXT CLK IN" ·
  J2/J3 "MCP2200 UART" · J4/J5 "MCP2210 SPI" · S1 "ON" + 7 per-switch labels · testpoints
  CLK/GND/Vcore/VDDIO print their names · J9 edge "co-req/mem-req/uart-va/spare-in/Mem[23]".
- LED mounted colors (explicit user req; `LEDCOLOR` in generator; swatch in panel + tooltip,
  marker fill, note that the CAD render shows placeholder LED bodies): D1 yellow-green
  #a3e635 · D2/D4/D5/D6 yellow #facc15 · D3 orange #fb923c · D7–D10 red #f87171. Same
  swatches already in the LEDs table on the Connectors & LEDs tab (+ placeholder-body note).
- Jumper cards: inlined from docs/img/jumpers/*.svg via DATA.jcards; side panel opens them in
  a modal (1000x560 too wide for the 420px panel); Jumpers tab shows them full width.
- Big diagrams inlined raw (architecture/clock_tree/power_path/proact_pinout_v2.svg) with the
  duplicate `id="ar"` marker namespaced per file (id + url(#) rewritten).
- Photos board_v3_top/bottom.jpg embedded as base64 JPEG (as-is, ~240 KB total); logo.png
  downscaled via PIL to h=96 PNG for the header.
- POLICY (from coordinator): no errata/known-issues framing. Neutral notes only: J11 is SMA
  though silk reads "BNC"; Y1 must be an active 3.3 V oscillator (verify PN); SW1 PN TBC.
  Never mention any pre-fab review.
- POLICY (USER, 2026-08-06 — supersedes the old "PROACT board v2 (final)" brand): ZERO board
  version-history on the visible page. No "v1"/"v2", no "changed/was swapped/new in", no
  "final", no previous-revision talk — the page describes THE board, full stop. Brand is
  "PROACT board" + badge "CW308 target"; <title> "PROACT board — interactive wiki"; sub
  "interactive board wiki · side-channel evaluation target". The deliverable FILENAME
  PROACT-Board-Wiki-v2.html stays (internal), as do file paths/net names/data URIs.
  · The Overview "What changed from board v1" card was REPLACED by "Signal names on the
    silkscreen" (+ details table "Silkscreen name → signal name map" built from the SILK
    shorthand: G/C/B/SPI RST, Mem[23], CLK, Spare In, UART Rvalid, Mem req, Trig cfg/norm/rsv,
    Alive?, MOSI, S-SEL, SCK, Co req, TRIG-IN, PC: 2 3 4 — the 15/20 swap-history rows are
    gone; SPI RST maps to pin 20). Pin-15/20 warnings (U1 panel + Pinout tab) are now neutral
    "double-check before wiring" cautions. LED silk note says "shorthand names", not "v1-era".
  · gen_wiki_data.py `scrub_versions()` neutralizes version text in the INLINED SVGs at build
    time (architecture title "…Board v2 —" → "…Board —"; pinout title "(v2)" dropped; the ★
    legend/pin-15/pin-20 notes reworded). The SVG files on disk (docs/img/architecture.svg,
    proact_pinout_v2.svg) still carry v1/v2 text for other deliverables — scrub is
    EXACT-STRING replace, so if gen_arch.py / gen_pinout_v2.py rewording changes, the scrub
    silently no-ops: ALWAYS re-run the verifier after a rebuild.
  · wiki_app.js: "final-board render" → "CAD render of the board" (map caption + default
    panel); footer/BOM/photos-card "final …" wording neutralized in the generator.
  · Verifier: scratchpad verify_versions.py — strips tags/base64, checks static DOM text,
    alt/title/placeholder/aria-label attrs, AND every string in the #wiki-data JSON blob for
    \bv[12]\b (case-insensitive). Current build: PASS, zero hits (1.81 MB page).
- Vcore 0.8 V-before-insert warning: global slim banner under header + full procedure on the
  Power tab.
- Pinout table rows clickable -> select the pin's net on the map; GND/VDDIO rows show the net
  panel but suppress map highlight (power nets).

## Jumper state model (coordinator feature request 2026-08-06; J12 revised same day)
- Live state on the board map; shunt caps drawn on the true pads; side panel updates live.
- `JSTATE = {JP1:'open'|'closed', JP3/JP5/JP4/JP6:'cw'|'m', JP7:'filt'|'direct',
   J6:{ssel:'m'|'cw', trig:bool}, J10:'normal'|'config'|'reserve',
   J12:'ext'|'osc'|'cw'}`. JP2 stateless (probe header, "do not fit").
- Links: JP1 closed=[1,2] · JP3-6 cw=[1,2] m=[2,3] · JP7 filt=[1,2] direct=[2,3] ·
  J6 ssel m=[1,3] cw=[3,5] + trig=[5,6] · J10 normal=[2,4] config=[3,4] reserve=[4,6] ·
  J12 ext=[1,2] osc=[3,4] cw=[5,6] (RADIO — exactly one link, never zero).
- Caution surfaced when J6 ssel='cw' AND trig=true (3-5 and 5-6 share pin 5).
- J12 MODEL (designer-authoritative 2026-08-06, matches silk "Clk select — SMA / Osc / Cw");
  SUPERSEDES the earlier "5-6 = clock feedback out / src none = CW via CW308 J3 → J7.5" model
  everywhere — do NOT reintroduce it:
  · J12 = clock SOURCE select, three positions, exactly ONE link (radio): 1-2 ext SMA J11 ·
    3-4 on-board Y1 50 MHz via R22 · 5-6 ChipWhisperer clock, arriving on J7.3 (CW308 CLKFB
    line) through R8 100 Ω.
  · J7.5 = PERMANENT CLOCK ECHO OUT: chip clock hard-wired to J7.5 so the CW can always
    observe/sample synchronously, whatever the source. Not a jumper. Stated in the J12 panel
    (jlab "clock echo out"), Clock tab, J7 rows (J7.3 "ChipWhisperer clock in — selected by
    J12 5-6, via R8" · J7.5 "chip clock echo out to the ChipWhisperer (always connected)").
  · Standard caution EVERYWHERE, no exception clause (JS const J3NOTE, also in generator
    sections): "Leave the CW308's J3 clock jumper unpopulated — it drives J7.5 (the
    clock-echo pin) and would fight the selected source." Shown as i.caution for every J12
    position; also in Clock tab rules, Jumpers tab, CW308 setup J3 row, cfg B/C, preset B row.
- Interaction: click on map selects; clicking an ALREADY-SELECTED stateful jumper cycles its
  primary position (JP1/JP3-7 toggle, J10 3-cycle, J12 3-cycle ext→osc→cw, J6 ssel toggle).
  Secondary toggle J6 5-6 only via panel chips. J12 has no secondary toggle any more.
- Presets A/B/C (README "Typical configurations") as buttons in the map toolbar. Each has a
  full `set` state + a `match` predicate over only the fields the README specifies
  (A: J10 free, J12='osc'; B: JP1 & J10 free, J12='cw'; C: like B but J12='osc' — B and C are
  now distinguished purely by the J12 radio position).
  Match is re-checked on every state change and shown in the toolbar; default page state =
  preset A (so the page loads "matching configuration A"). Preset side-panel summary lists
  jumper settings + reminders (S1-6/7, VDDIO feed, Vcore 0.800 V precondition, CW308 J3).
  C's J12 row wording (fixed text): "3-4 — Y1 clocks the chip; the clock is echoed to the
  scope on J7.5 — sample from it for phase-locked traces".
- S1 switches are NOT part of JSTATE (explorer stays separate); preset summaries mention the
  S1-6/7 expectation as a reminder line only.

## S1 explorer (fixed 2026-08-06)
- Selection ≠ toggle. `selectSwitch(n)` only sets the `.active` ring + detail card; the drawn
  nub ALWAYS stays OFF/down. The old code had `selectSwitch(n, toggle)` with a cosmetic
  `s1on{}` map + `.onpos` CSS (knob up): plain clicks AND `#S1-n` deep links passed
  toggle=true, so merely viewing a switch flipped its nub (user report: "switch 6 up") while
  the others stayed down. `s1on`/`.onpos` are deleted — never reintroduce a visual toggle
  here; the explorer is documentation, not a state model.

## Deep links (implemented)
- `#<tab>` (map/overview/pinout/jumpers/clock/power/s1/conn/cw/cfg/bom) · `#<REF>` selects a
  component (e.g. `#JP7`) · `#<netid>` selects a net (e.g. `#CLK_pin`) · `#S1-3` opens the S1
  explorer on switch 3 (select only — does NOT flip the nub) · `#preset=A|B|C` applies a
  preset. Also used for headless screenshot self-review
  (google-chrome --headless=new --screenshot ... "file://...#JP7").

## Status — SHIPPED 2026-08-06 (render-map rebuild + S1/J12 fixes + hidden-pin dots same day)
- Latest rebuild (U1/S1 extents fix, 16:02): 1.73 MB, 76 comps / 75 nets, node --check OK,
  version verifier PASS. Only data-blob w/h for U1+S1 changed vs the previous build.
- Latest rebuild (hidden-pin dots): **1.73 MB**. The 80 KB drop vs the previous build is NOT
  the pin-dot change — the jumper-card SVGs (docs/img/jumpers, owned by the jumper agent) were
  regenerated 14:26 (~4 KB smaller each, inlined twice: jcards blob + Jumpers tab) and
  architecture/pinout SVGs were regenerated 13:38, all AFTER the previous 13:36 build; this
  rebuild picked them up. Version verifier (scratchpad verify_versions.py) re-run: PASS.
- `docs/PROACT-Board-Wiki-v2.html` built: **1.81 MB** (two PNG renders embedded), zero
  external references (only SVG xmlns URIs), 76 components (all with authored DB entries),
  75 nets, works from file://.
- Screenshot-verified after the render-map rebuild: map default (preset A shunts on the real
  pads: J6 1-3, J10 2-4, J12 3-4, JP3/5/4/6 at 2-3, JP7 2-3, JP1 dashed open; match "A"),
  #JP7 selected (dim overlay w/ mask holes, green rings + labels on R7/R20/C11…, silk chip,
  top-side pill), #D1 (yellow-green swatch + placeholder-body note + "Alive?" silk chip),
  #R6 (auto-switch to BOTTOM view, ring on the real R6, green ring R9, bottom-side pill),
  #CLK_pin net (green thin rings CLK/J12/U1 + flipnote). node --check passes.
- Screenshot-verified after the 2026-08-06 S1/J12 fixes: #S1-6 (all seven nubs DOWN, switch 6
  only ringed amber), #J12 (three radio buttons, echo-out note, red J3 caution, "matches
  configuration A" on load), #preset=B (J12 shunt on the REAL 5-6 pads — right column under
  the "Cw" silk — match "B", panel rows incl. CW308 J3 unpopulated), #preset=C (single 3-4
  shunt, match "C", designer wording), #clock and #cfg tabs coherent.
- Diagram deps: gen_clock.py and gen_jumpers.py already carried the new J12 model but their
  outputs were STALE — re-ran both (clock_tree.svg/png + all jumper cards + overview card
  regenerated) BEFORE gen_wiki_data.py, since the wiki inlines those SVGs at build time.
  README.md already documents the new model (facts consistent).
- Rebuild after any source edit: `python3 docs/tools/gen_wiki_data.py` (reads wiki_app.css,
  wiki_app.js, README-derived tables inside the generator, the PCB_finalx PcbDoc, netlist,
  docs/img images incl. board_final_top/bottom.png). If gen_clock.py / gen_jumpers.py change,
  run them FIRST. NOTE: repo copies (index.html etc.) update via docs/tools/sync_to_repo.sh —
  not run automatically; coordinator decides when to sync.
- Known cosmetics (acceptable, revisit if asked): BOM first-column shows the whole ref list
  underlined but clicking selects the first ref only; the dashed "open/parked" outline is
  drawn only for JP1 open now (J12 always has exactly one link); jumper markers partially
  overlap their shunt caps (marker is the click-to-cycle target); board photos on the
  Overview tab are still the v3 photos (board_v3_top/bottom.jpg) — renders live on the map.
