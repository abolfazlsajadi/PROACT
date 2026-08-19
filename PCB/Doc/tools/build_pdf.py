#!/usr/bin/env python3
"""Build a print-styled, fully self-contained HTML from README.md, then Chrome renders it to PDF."""
import base64, re, os, markdown

ROOT = '/home/abish/Downloads/PCB/PROACT_DOC'
IMG = os.path.join(ROOT, 'docs/img')

# diagrams we want as crisp inline vector in the PDF (map png ref -> svg file)
SVG_MAP = {
    'docs/img/architecture.png': 'architecture.svg',
    'docs/img/proact_pinout_v2.png': 'proact_pinout_v2.svg',
    'docs/img/clock_tree.png': 'clock_tree.svg',
    'docs/img/power_path.png': 'power_path.svg',
    'docs/img/board_v2_placement.png': 'board_v2_placement.svg',
    'docs/img/board_v2_silkscreen.png': 'board_v2_silkscreen.svg',
    'docs/img/jumpers/overview.png': 'jumpers/overview.svg',
    'docs/img/jumpers/JP1.png': 'jumpers/JP1.svg',
    'docs/img/jumpers/JP2.png': 'jumpers/JP2.svg',
    'docs/img/jumpers/JP3_JP5.png': 'jumpers/JP3_JP5.svg',
    'docs/img/jumpers/JP4_JP6.png': 'jumpers/JP4_JP6.svg',
    'docs/img/jumpers/JP7.png': 'jumpers/JP7.svg',
    'docs/img/jumpers/J6.png': 'jumpers/J6.svg',
    'docs/img/jumpers/J10.png': 'jumpers/J10.svg',
    'docs/img/jumpers/J12.png': 'jumpers/J12.svg',
}

def data_uri(relpath):
    path = os.path.join(ROOT, relpath)
    ext = os.path.splitext(path)[1].lower().lstrip('.')
    mime = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg'}[ext]
    with open(path, 'rb') as f:
        b = base64.b64encode(f.read()).decode()
    return f'data:{mime};base64,{b}'

def inline_svg(svgfile, cls='fig-svg'):
    with open(os.path.join(IMG, svgfile)) as f:
        s = f.read()
    s = re.sub(r'<\?xml.*?\?>', '', s, flags=re.S)
    # ensure it scales to container width
    s = s.replace('<svg ', f'<svg class="{cls}" preserveAspectRatio="xMidYMid meet" ', 1)
    return s

md_text = open(os.path.join(ROOT, 'README.md')).read()

# drop the ToC block from the PDF (page numbers make it redundant) - keep everything else
md_text = re.sub(r'## Table of contents.*?\n---\n', '', md_text, count=1, flags=re.S)
# drop the collapsible vector-locator (redundant with the annotated layout in print)
md_text = re.sub(r'<details>.*?</details>\n?', '', md_text, flags=re.S)

html_body = markdown.markdown(
    md_text,
    extensions=['tables', 'fenced_code', 'attr_list', 'md_in_html', 'sane_lists'],
)

# --- replace <img src="docs/img/..."> occurrences ---
def repl_img(m):
    src = m.group('src')
    if src in SVG_MAP:
        return f'<div class="figure">{inline_svg(SVG_MAP[src])}</div>'
    # photo -> base64 img, preserve width attr if present
    width = m.group('rest') or ''
    wm = re.search(r'width="([^"]+)"', width)
    style = f' style="max-width:{wm.group(1)}"' if wm else ''
    return f'<img class="photo" src="{data_uri(src)}"{style}>'

html_body = re.sub(
    r'<img\b(?P<pre>[^>]*?)src="(?P<src>docs/img/[^"]+)"(?P<rest>[^>]*?)>',
    repl_img, html_body)

# the repo-root logo is outside docs/img — inline it too (cover shows it as well; keep body copy small)
html_body = html_body.replace('src="logo.png"', f'src="{data_uri("logo.png")}"')

# also handle markdown-produced <img> where attrs come before/after already handled;
# strip <em> caption duplication left in tables is fine.

# --- cover images (base64) ---
cover_img = data_uri('docs/img/board_final_top.png')
cover_3d = data_uri('docs/img/board_final_bottom.png')
cover_logo = data_uri('logo.png')

CSS = """
@page { size: A4; margin: 14mm 13mm 16mm 13mm;
        @bottom-center { content: "PROACT Evaluation Board — Reference"; font-size: 8pt; color:#94a3b8; }
        @bottom-right  { content: counter(page) " / " counter(pages); font-size: 8pt; color:#94a3b8; } }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { font-family: "Inter","Segoe UI",Helvetica,Arial,sans-serif; color:#0f172a;
       font-size: 10.2px; line-height: 1.5; margin:0; }
h1,h2,h3 { color:#0b1220; line-height:1.2; font-weight:800; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 15.5px; margin: 22px 0 8px; padding-bottom:5px;
     border-bottom: 2px solid #e2e8f0; break-after: avoid; }
h3 { font-size: 12.5px; margin: 14px 0 6px; break-after: avoid; }
p { margin: 6px 0; }
a { color:#1d4ed8; text-decoration:none; }
sub, sup { color:#64748b; }
code { background:#f1f5f9; border-radius:4px; padding:0.5px 4px; font-size: 9.2px;
       font-family: "SFMono-Regular",Consolas,"Liberation Mono",monospace; color:#0f172a; }
pre { background:#0e1729; color:#e2e8f0; border-radius:8px; padding:10px 12px; overflow:hidden;
      font-size: 7.6px; line-height:1.35; break-inside: avoid;
      font-family: "SFMono-Regular",Consolas,"Liberation Mono",monospace; }
pre code { background:none; color:inherit; padding:0; font-size: inherit; }
blockquote { margin: 8px 0; padding: 7px 12px; background:#f8fafc; border-left: 3px solid #94a3b8;
             color:#334155; border-radius: 0 6px 6px 0; font-size: 9.6px; break-inside: avoid; }
table { border-collapse: collapse; width:100%; margin: 8px 0 12px; font-size: 9.1px;
        break-inside: auto; }
thead { display: table-header-group; }
th, td { border: 1px solid #e2e8f0; padding: 3.5px 6px; text-align: left; vertical-align: top; }
th { background:#f1f5f9; color:#0b1220; font-weight:700; }
tr { break-inside: avoid; }
tbody tr:nth-child(even) { background:#fafcff; }
td code, th code { font-size: 8.7px; }
.figure { margin: 10px 0 14px; text-align:center; break-inside: avoid; }
.fig-svg { width: 100%; height: auto; max-height: 235mm; border:1px solid #e2e8f0; border-radius:8px; background:#0b1220; }
img.photo { max-width: 100%; height:auto; border-radius:8px; border:1px solid #e2e8f0; }
/* gallery tables that hold photos: kill borders, keep captions */
table:has(img.photo) { border:none; }
table:has(img.photo) td { border:none; padding:5px; text-align:center; }
table:has(img.photo) em { display:block; color:#64748b; font-size:8.6px; margin-top:3px; }
hr { border:none; border-top:1px solid #e2e8f0; margin: 14px 0; }
/* cover */
.cover { text-align:center; padding: 4mm 0 0; break-after: page; }
.cover-logo { width: 26mm; height:auto; margin: 0 auto 6px; display:block; }
.cover .kicker { color:#1d4ed8; font-weight:800; letter-spacing:.14em; font-size:10px; text-transform:uppercase; }
.cover h1 { font-size: 30px; margin: 8px 0 6px; }
.cover .sub { color:#475569; font-size: 12.5px; max-width: 150mm; margin: 0 auto 14px; }
.cover .imgs { display:flex; gap:8px; justify-content:center; margin: 6px 0 14px; }
.cover .imgs img { width: 47%; border-radius:10px; border:1px solid #e2e8f0; }
.cover .meta { color:#64748b; font-size:9.5px; margin-top: 10px; }
.badges { display:flex; gap:6px; justify-content:center; flex-wrap:wrap; margin: 10px 0; }
.badge { background:#eff6ff; border:1px solid #bfdbfe; color:#1e40af; border-radius:999px;
         padding:3px 11px; font-size:9px; font-weight:700; }
h2 { break-inside: avoid; }
ul,ol { margin:6px 0; padding-left: 18px; }
li { margin: 2px 0; }
"""

# Chrome's Blink doesn't do @page named margin boxes reliably; keep header/footer off but leave CSS.
cover = f"""
<div class="cover">
  <img class="cover-logo" src="{cover_logo}">
  <div class="kicker">ChipWhisperer CW308 Target</div>
  <h1>PROACT ASIC<br>Evaluation &amp; Side‑Channel Target Board</h1>
  <div class="sub">Hardware reference — chip pinout, signal routing, jumpers, switches,
  connectors, clock system, Vcore trim, power &amp; current‑sense architecture.</div>
  <div class="badges">
    <span class="badge">DIP‑28 · socket U1</span>
    <span class="badge">Vcore 0.80–0.90 V trim</span>
    <span class="badge">3‑source clock select</span>
    <span class="badge">R7 0.01 Ω shunt</span>
    <span class="badge">CW308 J7 / J8 / J9</span>
  </div>
  <div class="imgs">
    <img src="{cover_img}"><img src="{cover_3d}">
  </div>
  <div class="meta">Generated from the board's Altium netlist, placement data and BOM · verified against the design.</div>
</div>
"""

html = f"""<!doctype html><html><head><meta charset="utf-8">
<style>{CSS}</style></head><body>
{cover}
{html_body}
</body></html>"""

out_html = os.path.join(ROOT, 'docs/PROACT_Board_Reference.html')
open(out_html, 'w').write(html)
print('wrote', out_html, f'({len(html)//1024} KB)')
