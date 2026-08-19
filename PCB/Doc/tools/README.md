# Documentation tooling

Scripts that generate the figures and PDF for the PROACT board reference (**board v2 final**).
All read straight from the final design files (`Sheet1.SchDoc`, `PCB1.PcbDoc`), so the docs stay
in sync with the board.

| Script | Produces | Reads |
|--------|----------|-------|
| `extract_netlist.py` | Board netlist (stdout) — pin/wire/junction connectivity | `Sheet1.SchDoc` (Altium, via `olefile`) |
| `gen_pinout_v2.py` | `../img/proact_pinout_v2.svg` — DIP‑28 pinout (v2 names) | (data embedded, verified vs. netlist + `pins.png`) |
| `gen_arch.py` | `../img/architecture.svg` — system routing diagram (v2) | (data embedded) |
| `gen_clock.py` | `../img/clock_tree.svg` — clock sources / feedback tree | (data embedded) |
| `gen_power.py` | `../img/power_path.svg` — Vcore trim / JP7 / shunt chain | (data embedded) |
| `gen_v2_view.py` | `../img/board_v2_placement.svg` — component placement view | `PCB1.PcbDoc` (`Components6`, via `olefile`) |
| `gen_silk.py` | `../img/board_v2_silkscreen.svg` — silkscreen labelling mockup | `PCB1.PcbDoc` + label table |
| `gen_jumpers.py` | `../img/jumpers/*.svg` — per‑jumper cards + overview poster | `PCB1.PcbDoc` (`Components6`/`Pads6`/`Board6`) |
| `build_pdf.py` | `../PROACT_Board_Reference.html` — self‑contained print HTML | `../../README.md`, `../img/*` |
| *(v1 archive)* `gen_pinout.py`, `gen_locator.py`, `gen_callout.py` | v1 figures (kept for the changelog page) | v1 pick‑and‑place CSV |

Durable design notes for the jumper cards live in `jumper_agent_notes.md`.

## Rebuild everything

```bash
pip install olefile markdown            # one-time
cd docs/tools

python3 gen_pinout_v2.py
python3 gen_arch.py
python3 gen_clock.py
python3 gen_power.py
python3 gen_v2_view.py
python3 gen_silk.py
python3 gen_jumpers.py                  # writes ../img/jumpers/*.svg and .png

# rasterize SVG → PNG for the GitHub README (needs Inkscape)
for f in proact_pinout_v2 architecture clock_tree power_path board_v2_placement board_v2_silkscreen; do
  inkscape --export-type=png --export-width=1600 -o ../img/$f.png ../img/$f.svg
done

# build the PDF (needs Google Chrome)
python3 build_pdf.py
google-chrome --headless=new --disable-gpu --no-pdf-header-footer \
  --print-to-pdf=../PROACT_Board_Reference.pdf \
  file://$(cd .. && pwd)/PROACT_Board_Reference.html
```

## Publishing to the repo

The GitHub repo keeps these docs under `PCB/` (`Doc/` = this `docs/` folder). After regenerating
anything: re‑copy the changed files into the repo clone (`PCB/Doc/img/`, `PCB/Doc/wiki/`,
`PCB/Doc/tools/`, `PCB/README.md` with `docs/` → `Doc/` path rewrite) and push.
