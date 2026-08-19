# JUMPER-GRAPHICS agent notes (durable memory)

Maintained by the jumper-cards agent. Update whenever a decision or calibration changes.

## Sources of truth
- Netlist: `/tmp/netlist_final.txt` (verified 2026-08-06: all jumper facts in the task brief match; nets are listed across MULTIPLE lines with the same net name, e.g. `RX: J7.8` + `RX: JP3.1` are one net).
- Board: `/home/abish/Downloads/PCB/PROACT_DOC/PCB_finalx/PCB_Projectcxfinal/PCB1.PcbDoc` (NEWEST, 76 components; gen_jumpers.py repointed 2026-08-06 — the older `PCB_final/PCB1.PcbDoc` carries byte-identical component XY/ROTATION/outline data, so no coordinates changed).
- Extraction recipe: same as `docs/tools/gen_v2_view.py` (Components6/Data, u32-length records, `|key=value` fields, mil -> mm via *0.0254).

## PcbDoc extraction calibration (final board)
- Board outline from `Board6/Data` VX/VY vertices: **X 319.40..373.25 mm (53.85 wide), Y 196.72..290.07 mm (93.35 tall)**, 1.27 mm corner chamfers (drawn as rounded rect).
- Component X range 322.2..370.5, Y 198.4..286.4 (larger than the "Y 195-270" hint in the brief; outline stream is authoritative).
- **Pads6/Data parser** (gives per-pin XY + owner component — used for pin-1 dots on mini-maps):
  record = byte `0x02`, u32 len, pascal-string pad name; then u32-length blocks until the first block >= 100 bytes = geometry block; after it, if next byte != 0x02 there is one extra u32-length trailing block to skip.
  Geometry block offsets: net u16 @3 (0xFFFF none), component index u16 @7 (index into Components6 record order), x i32 @13, y i32 @17 (units 1/10000 mil; *0.0254/10000 -> mm). Verified against known placements (J11, JP7).

## Physical jumper orientations (from pads, board coords, Y up = board top edge)
- JP1 horizontal, pin1 EAST (right), pin2 west. JP2 horizontal, pin1 EAST. JP3/JP5 horizontal, pin1 WEST (toward J7/CW side). JP4 horizontal pin1 EAST (rot 180); JP6 horizontal pin1 WEST. JP7 vertical, pin1 NORTH (top), pin3 south.
- J10/J12 physically 3 columns x 2 rows: odd pins on TOP row, even on BOTTOM row (cols 1/2, 3/4, 5/6 left->right). So the physical J12 link positions are three side-by-side VERTICAL shunts.
- J6 (rot 270) physically 2 columns x 3 rows, odd column EAST (pin1 top-right), even column west.
- DECISION: position diagrams use the brief's logical convention (2x3 = two columns of three, odd left / even right, rows 1-2 / 3-4 / 5-6). The mini-map draws the TRUE physical pads with a white pin-1 dot, so users can orient the shunt on the real board. 1xN diagrams always drawn horizontally 1..N left->right even where the part is vertical on the board (JP7).

## mm -> px transform (gen_jumpers.py)
- Card mini-map: scale **S = 4.7 px/mm**, board drawn inside left panel; `px = panel_x + pad + (x - 319.40)*S`, `py = panel_y + pad + (290.07 - y)*S` (Y flip, top view). Board = 253x439 px.
- Overview map: same formulas with **S = 8.0** -> 431x747 px.
- Footprint sizes: same SIZE dict as gen_v2_view.py — **w,h in the footprint-library 0° frame** — plus the same `placed_size()` (swap w/h when ROTATION rounds into 90/270); minimap draws axis-aligned rects from placed extents (no SVG rotate).
- **BUG FIXED 2026-08-06 (orientation)**: gen_jumpers.py had SOCKET_IC_DIP_28 and DIPSW254S_7P entered PRE-transposed ((36,15.5)/(18.5,10) instead of the true 0° frames (15.5,36)/(10,18.5)) AND applied `rotate(-ROTATION)` on top, so U1 (rot 270) and S1 (rot 90) drew VERTICAL on every mini-map/overview while the real board has them HORIZONTAL. Fixed by restoring the true 0° frames + placed_size(); verified against `new/ff/PCB1.png` (U1/S1 horizontal, J2–J5 & J7/J8 & JP7 vertical, J9 horizontal). Per-pin pads from Pads6 are absolute and were never affected.

## Style contract (locked)
- bg #0b1220, panels #0e1729 / #111827, strokes #475569 (light #1e293b for panel borders), text #f1f5f9, muted #94a3b8, font "Inter, Segoe UI, Arial, sans-serif". Board green #0e4b34 / #1f7a54 stroke, faint footprints fill #0e4b34 stroke #2f6f52 (matches board_v2_placement / silkscreen mockups).
- Category colors: UART #14b8a6, SPI #3b82f6, reset #f87171, trigger #a78bfa, power #f59e0b, clock #22d3ee, module #14b8a6, CW308 #38bdf8.
- DEVIATION: JP2 (probe header) has no assigned category color in the brief -> uses neutral **#e2e8f0** highlight so it cannot be mistaken for a functional jumper family.
- J6 card carries two families: S-Sel links use SPI blue, the 5-6 trigger-in link uses trigger purple; title accent = blue.
- Cards 1000x560. Title bar ~58 px with 6 px category accent strip. Left mini-map panel x16 y70 w304 h474. Right area x332..984.
- 2-position cards: two side-by-side position panels. 3-position cards (J6, J10, J12): three stacked rows, grid on the left of the row, caption right.
- Shunt drawn as rounded rect over the two linked pads, category fill ~28% opacity + bright category stroke; NC pins dashed gray "NC".
- Check mark for recommended tags: "✓" (U+2713) + tag pill.

## Recommended-position tags (as rendered)
- JP1: CLOSED = "on CW308" (CW can reset), OPEN = standalone (SW1 only).
- JP3+JP5 and JP4+JP6: move BOTH jumpers of the pair together; 1-2 = CW, 2-3 = MODULE.
- JP7: 1-2 FILTERED ✓ SCA capture (mounted on CW308; rail exits J8.8 FILTIN, through CW L-C filter, back via J8.5/6), 2-3 DIRECT = bench/bypass.
- J12 (MODEL RE-CORRECTED by the board designer 2026-08-06 — **supersedes** the earlier "5-6 = feedback add-on" model below): J12 = clock **SOURCE select, EXACTLY ONE link fitted** (board silk: "Clk select — SMA / Osc / Cw"). 1-2 = external SMA J11; 3-4 = Y1 50 MHz via R22 20 Ω (✓ standalone default); **5-6 = ChipWhisperer clock IS the source**: CW clock arrives on J7.3 (CW308 CLKFB line) → R8 100 Ω → J12.5, link 5-6 puts it on the chip clock. **J7.5 = PERMANENT CLOCK ECHO OUT** — chip clock hard-wired to J7.5 so the CW can always observe/sample it; not a jumper, not a source. Caution (exact wording, used on cards): "Leave the CW308's J3 clock jumper unpopulated — it drives J7.5 (the clock-echo pin) and would fight the selected source." Sync-capture recipe: "J12 → 3-4; the chip clock is echoed on J7.5 — sample from it; CW308 J3 empty." Netlist unchanged, only the signal DIRECTION re-interpreted: net CLK = J12.5 + R8.2; R8.1 + J7.3 (unnamed); net CLK_pin = J7.5 + J12.2/4/6 + U1.9 + CLK test point. NOTE: coordinator calls J11 "SMA" although the footprint lib is CONN_RF_BNC_RA — cards say SMA per coordinator.
  - SUPERSEDED (do NOT reuse): the 2026-08-06 morning model that called 5-6 a "clock FEEDBACK OUT fitted on top of 1-2 or 3-4" and J7.5 a "CLKIN driven when the CW308's J3 is fitted". gen_jumpers.py (J12 card + overview), gen_clock.py and gen_arch.py were all realigned to the corrected model on 2026-08-06 (J12 card: no add-on badge, row 3 = "5-6 · CW SOURCE"; clock tree: 3 real source lanes + J7.5 echo tap OUT of the bus, CW308 J3 only in a caution badge, H now 580; arch: third J12 mux input "link 5-6", echo arrow relabeled "J7.5: clock echo to CW (always)").
- J10: 2-4 normal trigger_Out ✓ default; 3-4 config trigger out[1]; 4-6 reserve out[8].
- J6: 1-3 module (MCP2210 GPIO4) drives S-Sel ✓ with USB module; 3-5 CW GPIO3 drives S-Sel; 5-6 CW GPIO3 -> chip trigger_in (horizontal link, combinable with 1-3 but NOT with 3-5 - pin 5 shared).
- JP2: probe only - "do not fit a jumper"; pins 1/2/3 = out_pins[2]/[3]/[4] (U1.26/25/24).

## Final layout numbers (as shipped)
- Cards 1000x560. Title: accent bar x16 y14 6x34, title 21px @ (34,34), subtitle 11px @ (34,54).
- Mini-map panel x16 y70 w304 **h458**; right area x332 w652. 2-position cards: two 320-wide panels at x332/x664, h458. 3-position cards: three rows y=70/224/378, h=146 (grid center cx=RX0+235, cy=y+84; caption column cx=RX0+505). Footnote baseline = MAPY+MAPH+14 (=542); J12 has two footnote lines at +12/+26.
- Overview 1190x900: map panel (16,76,500,808) at scale 8.0; legend table x534 w640 (rows y=140+60i); presets panel y616 h268.
- Overview map label offsets (mm, rel. jumper center): JP1 (4.2,0,start), JP3 (-6.2,0.6,end), JP5 (-0.6,-3.6,middle — below ring; left position collided with J12 ring), JP4/JP6 (0,-3.4,middle), JP7 (0,-4.4,middle), J6 (0,4.7,middle), J10 (0,3.6,middle), J12 (0,-4.2,middle), JP2 (4.4,0,start).
- XML gotcha: raw `&` in SVG text (inkscape tolerates, browsers do not) — always `&amp;`. All 9 SVGs validate with xml.dom.minidom.

## Toolchain
- PNG conversion: **inkscape 1.3.2** (`inkscape in.svg -o out.png -w <2x width>`); cairosvg NOT installed; rsvg-convert absent. Render at 2x.
- Deliverables dir: `docs/img/jumpers/` (JP1, JP3_JP5, JP4_JP6, JP7, J6, J10, J12, JP2, overview) x {svg,png}.
- Generator: `docs/tools/gen_jumpers.py`, stdlib + olefile only.
