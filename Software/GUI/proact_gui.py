#!/usr/bin/env python3
"""
PROACT Chip GUI -- graphical control panel for the PROACT bench, built entirely
on the shared `proact_host` backend (no forked serial/SPI/capture code).

Layout: a fixed sidebar (Connection + Reset control + Programming, with live
LED indicators) beside seven tabbed pages, one per workflow step:
  * Crypto experiment: per-variable fixed/random or file inputs, #runs,
    encrypt/decrypt, trigger + internal (ASCON/Xoodyak) trigger config,
    optional cycle-count timer, compare-with-reference, log window + file
  * ChipWhisperer: connect/disconnect Husky, target clock (default 50 MHz on
    HS2), transport selection (MCP or Husky SPI via GPIO3), trace capture,
    and a jump to the unified self-check
  * CPA analysis: offline correlation-power-analysis attack on a local capture
    with the separately supplied companion analysis script (no board required)
  * Registers: control-register bit editor + graphical status-register view
  * Memory / Sw-RV: raw bus peek/poke + Sw-RV target program loader
  * Self-Check (A–Z): the single unified pass/fail health check for the chip
  * UART monitor: noise-tolerant (non-ASCII shown as hex), CSV export

Every panel has a (?) help button linking to the documentation.
Run:  python3 proact_gui.py
"""
import os
import sys
import csv
import time
import threading
import math
from collections import deque
from contextlib import nullcontext
from datetime import datetime
from html import escape

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Python"))

from PyQt6.QtCore import Qt, QObject, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import QAction, QFont, QColor, QPalette, QImageReader
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QLineEdit,
    QComboBox, QRadioButton, QButtonGroup, QGroupBox, QGridLayout, QVBoxLayout,
    QHBoxLayout, QTabWidget, QTableWidget, QTableWidgetItem,
    QProgressBar, QFileDialog, QPlainTextEdit, QCheckBox, QHeaderView, QSpinBox,
    QMessageBox, QScrollArea, QAbstractItemView, QDialog, QTextBrowser,
)

from proact_host import regs
from proact_host.transport import ProactTarget, UartTransport
from proact_host.inputs import InputPlan, Variable, parse_input_file, VARS
from proact_host.monitor import MonitorDecoder
from proact_host.validation import validate_aes, validate_aead

STYLE = """
/* ---- PROACT dark theme --------------------------------------------------
   Layered surfaces: window #14181f  <  card #1a1f29  <  raised #252c3a,
   inset (inputs/consoles) #10141c.  Accent: blue #3b82f6 / #2563eb.       */
QWidget { color: #e6eaf2; font-size: 13px;
          font-family: "Inter", "SF Pro Text", "Segoe UI", "Ubuntu", "Cantarell", sans-serif; }
QWidget:disabled { color: #5d6675; }
QMainWindow, QDialog, QMessageBox { background: #14181f; }

/* Sidebar brand header */
QWidget#brand { background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                            stop:0 #1b2a4a, stop:1 #172033);
                border: 1px solid #2b3854; border-radius: 10px; }
QLabel#brandTitle { font-size: 19px; font-weight: 800; color: #f2f5fa; background: transparent; }
QLabel#brandSub { font-size: 11px; color: #8fa3c8; background: transparent; }

/* Panels as cards */
QGroupBox { background: #1a1f29; border: 1px solid #262e3d; border-radius: 10px;
            margin-top: 12px; padding: 8px 10px 6px 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left;
                   left: 10px; top: 3px; padding: 0 6px; color: #8ab4f8;
                   background: #14181f; border-radius: 4px; }
/* Collapsible Section: the expand/collapse affordance is a plain-text triangle
   in the title (Section._apply), so it survives a missing SVG plugin; hide the
   checkable indicator box, and shrink a collapsed section to a slim clickable
   header (no empty card). */
QGroupBox::indicator { width: 0px; height: 0px; }
QGroupBox:!checked { background: transparent; border-color: #232a37;
                     padding: 0 10px 0 10px; }

/* Buttons */
QPushButton { background: #252c3a; border: 1px solid #313b4d; border-radius: 7px;
              padding: 6px 14px; font-weight: 500; }
QPushButton:hover { background: #2c3444; border-color: #3e4a61; }
QPushButton:pressed { background: #1f2531; }
QPushButton:disabled { background: #1d222c; color: #5d6675; border-color: #262e3d; }
QPushButton#primary { background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                                  stop:0 #3b82f6, stop:1 #2b6be0);
                      border: 1px solid #2f6ee0; color: white; font-weight: 600; }
QPushButton#primary:hover { background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                                        stop:0 #4f8ff9, stop:1 #3577ec); }
QPushButton#primary:pressed { background: #2360cf; }
QPushButton#primary:disabled { background: #23334d; color: #7b8db0; border-color: #2a3a57; }
QPushButton#help { background: transparent; border: 1px solid #38445a; border-radius: 9px;
                   max-width: 18px; min-width: 18px; max-height: 18px; min-height: 18px;
                   padding: 0; color: #8ab4f8; font-size: 11px; font-weight: 700; }
QPushButton#help:hover { border-color: #3b82f6; background: rgba(59, 130, 246, 0.12); }

/* Inputs */
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {
    background: #10141c; border: 1px solid #2a3242; border-radius: 7px;
    padding: 5px 8px; selection-background-color: #2563eb; selection-color: white; }
QLineEdit:hover, QComboBox:hover, QSpinBox:hover { border-color: #344054; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus { border-color: #3b82f6; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {
    color: #4d5563; background: #151a22; border-color: #232a37; }
QComboBox::drop-down { subcontrol-origin: padding; width: 20px; border: 0; }
QComboBox::down-arrow { image: url(@ASSETS@/chevron-down.svg); width: 12px; height: 12px; }
QComboBox QAbstractItemView { background: #1a2029; border: 1px solid #2c3547;
    border-radius: 6px; padding: 4px;
    selection-background-color: #2563eb; selection-color: white; }
QSpinBox::up-button, QSpinBox::down-button { width: 18px; background: transparent; border: 0; }
QSpinBox::up-arrow { image: url(@ASSETS@/chevron-up.svg); width: 10px; height: 10px; }
QSpinBox::down-arrow { image: url(@ASSETS@/chevron-down.svg); width: 10px; height: 10px; }

/* Check / radio indicators (explicit, so they stay visible on the dark theme).
   Content 14px + 1px border = 16px box; the radio radius must be exactly half
   the box (8px) or Qt falls back to square corners. */
QCheckBox, QRadioButton { spacing: 6px; background: transparent; }
QCheckBox::indicator, QRadioButton::indicator { width: 14px; height: 14px; }
QCheckBox::indicator { border: 1px solid #3e4a61; border-radius: 4px; background: #10141c; }
QCheckBox::indicator:hover { border-color: #4d5c78; }
QCheckBox::indicator:checked { background: #2563eb; border-color: #2f6ee0;
                               image: url(@ASSETS@/check.svg); }
QCheckBox::indicator:disabled { background: #151a22; border-color: #232a37; }
QCheckBox::indicator:checked:disabled { background: #23334d; }
QRadioButton::indicator { border: 1px solid #3e4a61; border-radius: 8px; background: #10141c; }
QRadioButton::indicator:hover { border-color: #4d5c78; }
QRadioButton::indicator:checked { background: #2563eb; border-color: #2f6ee0;
                                  image: url(@ASSETS@/radio-dot.svg); }
QRadioButton::indicator:disabled { border-color: #232a37; background: #151a22; }
QRadioButton::indicator:checked:disabled { background: #23334d; }

/* Tabs */
QTabWidget::pane { border: 1px solid #262e3d; border-radius: 8px; top: -1px; }
/* Tab padding is deliberately tight: seven tabs must fit a 1280px-wide window
   without the tab bar sprouting scroll arrows (tools/check_gui_layout.py). */
QTabBar::tab { background: transparent; color: #97a1b3; padding: 8px 12px; margin-right: 2px;
               border-top-left-radius: 8px; border-top-right-radius: 8px;
               border-bottom: 2px solid transparent; font-weight: 500; }
QTabBar::tab:hover { color: #cdd6e4; background: #1d2330; }
QTabBar::tab:selected { color: #eaf0f9; background: #222939; border-bottom: 2px solid #3b82f6; }

/* Progress */
QProgressBar { border: 1px solid #262e3d; border-radius: 7px; text-align: center;
               background: #10141c; color: #cdd6e4; max-height: 14px; }
QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                                  stop:0 #2563eb, stop:1 #3b82f6);
                      border-radius: 6px; }

/* Tables */
QTableWidget { background: #10141c; alternate-background-color: #151b26;
               gridline-color: #232b39; border: 1px solid #262e3d; border-radius: 8px; }
QTableWidget::item { padding: 2px 6px; }
QTableWidget::item:selected { background: #2563eb; color: white; }
QHeaderView::section { background: #1c222e; color: #aeb9cb; padding: 6px 8px; border: 0;
                       border-bottom: 1px solid #262e3d; font-weight: 600; }
QTableCornerButton::section { background: #1c222e; border: 0; }

/* Menus */
QMenuBar { background: #14181f; border-bottom: 1px solid #232a37; padding: 2px 6px; }
QMenuBar::item { padding: 4px 10px; border-radius: 6px; background: transparent; }
QMenuBar::item:selected { background: #232b3b; color: #eaf0f9; }
QMenu { background: #1a2029; border: 1px solid #2c3547; border-radius: 8px; padding: 6px; }
QMenu::item { padding: 6px 22px 6px 14px; border-radius: 6px; }
QMenu::item:selected { background: #2563eb; color: white; }
QMenu::separator { height: 1px; background: #2a3242; margin: 4px 8px; }

/* Consoles + status chip.  The generic QWidget font-family above overrides any
   programmatic setFont(), so the monospace intent MUST live here in the QSS. */
QPlainTextEdit#console { background: #0b0f16; border: 1px solid #222937; color: #c7d2e0;
    font-family: "JetBrains Mono", "DejaVu Sans Mono", "Menlo", "Consolas", monospace;
    font-size: 12px; }
QLabel#monoValue { font-family: "JetBrains Mono", "DejaVu Sans Mono", "Menlo", "Consolas", monospace;
    font-size: 13px; }
QLabel#monoSmall { font-family: "JetBrains Mono", "DejaVu Sans Mono", "Menlo", "Consolas", monospace;
    font-size: 11px; }
QLabel#statusLabel { background: #10141c; border: 1px solid #232a37; border-radius: 8px;
                     padding: 8px 10px; color: #97a1b3; }
QToolTip { background-color: #1d2430; color: #dbe3ee; border: 1px solid #364358;
           padding: 6px 8px; }
QLabel#pageTitle { color: #f2f5fa; font-size: 21px; font-weight: 700; }
QLabel#pageDescription { color: #aeb9cb; font-size: 12px; }
QLabel#pageTag { color: #9fc1ff; background: #1b2a43; border: 1px solid #304568;
                border-radius: 6px; padding: 4px 8px; font-size: 11px; }
QStatusBar { background: #10141c; border-top: 1px solid #2a3242; color: #aeb9cb; }
QStatusBar::item { border: 0; }
QPushButton:focus, QCheckBox:focus, QRadioButton:focus { border: 1px solid #93baff; }

/* Scrollbars */
QScrollArea { border: 0; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #333d4e; border-radius: 4px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #41506a; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #333d4e; border-radius: 4px; min-width: 24px; }
QScrollBar::handle:horizontal:hover { background: #41506a; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
"""
GREEN, RED, GRAY, AMBER, BLUE = "#22c55e", "#ef4444", "#4b5563", "#f59e0b", "#2563eb"

HELP = {
    "connection": "Auto-detect the MCP2210 (SPI loader) and MCP2200 (UART). Leave Port blank to auto-detect; set it to override. See docs/wiki/GUI-Guide.md and Getting-Started.md.",
    "reset": "Reset presets drive the MCP2210 GPIO reset lines. Every preset sets ALL four lines to a safe, deterministic state:\n- Run = the verified running state (CPU+crypto active, SPI loader held).\n- Reset all = everything held (baseline).\n- Controller = hold only the CPU.\n- Global = hold Sw-RV+crypto -- the CPU is held too, because resetting the crypto under a running CPU wedges the bus (hardware fact). Recover with Run.\n- SPI loader released = programming-style state (CPU+crypto held).\nThe checkboxes below mirror the actual read-back pins (synced every second); green LED = released/active, red = held, gray = disconnected. See docs/hardware_hazards.md.",
    "program": "Stream a controller .vmem into the chip over SPI (64-bit {addr,data} frames) with the reset handshake. Supply a matching image separately, or build it from the companion design package. See docs/bringup_guide.md.",
    "core": "The hardware/software target to run. Nonce and AD apply only to the AEAD cores (ASCON, Xoodyak); they are disabled for AES1/AES2/Sw-RV.",
    "encdec": "AES1, AES2 and the software AES support encrypt/decrypt. ASCON and Xoodyak hardware support encryption only, so Decrypt is unavailable for those cores. Their self-check uses host-side decryption for the reference round-trip.",
    "inputs": "Each input can be Fixed (enter hex) or Random (new value per run), or read a whole run list from a text file (see experiments/inputs_example.txt).",
    "trigcfg": "Trigger source (cfg_sel): which core drives the scope trigger. 'auto' follows the selected core. See docs/hardware_hazards.md (H6).",
    "inttrig": "Internal trigger config (triggercfg) selects WHICH PHASE of an ASCON/Xoodyak operation the trigger brackets (key/nonce/AD/PT). Only ASCON & Xoodyak. See docs/hardware_hazards.md (H6).",
    "timer": "Read the trigger-window cycle count from the on-chip timer after each run (the timer counts only while the trigger is high).",
    "compare": "Compare the returned data with the shared software reference for the selected operation. This does not add a hardware decryption path for the encryption-only ASCON/Xoodyak cores.",
    "targetplat": "Which PROACT you are using:\n- ASIC: the fabricated chip. The Husky generates its clock on HS2 (set the frequency in the ChipWhisperer tab, default 50 MHz).\n- FPGA (CW305): the design running on a CW305 Artix-7 board. HS2 is disabled and the CW305's own PLL provides the clock; you must upload the PROACT bitstream (ChipWhisperer tab). Both then load the controller firmware over SPI and talk over UART the same way.",
    "cw": "ChipWhisperer Husky + the target platform's clock. ASIC: clock generated on HS2 (default 50 MHz). FPGA (CW305): upload the PROACT bitstream; the CW305 PLL provides 50 MHz and HS2 is disabled. See docs/wiki/ChipWhisperer.md.",
    "companion": "The GUI works with a specific on-chip program -- the controller command-server firmware. It understands every command this GUI sends (select core, key/plaintext, run, read result, registers, Sw-RV load). Supply the matching main.vmem separately, or build it from the companion design package, then Program it.",
    "transport": "The GUI connects through the MCP2210 SPI loader and MCP2200 UART. The Husky SPI/UART transport entry is unavailable because this GUI does not implement that route.",
    "selfcheck": "The ONE full self-check (A-Z): UART link+baud, AES1/AES2 encrypt KAT + decrypt round-trip, ASCON/Xoodyak reference-vector encrypt KAT + software decrypt round-trip, timer, control write, PRNG, Sw-RV, and -- with the scope connected -- clock lock and a real trace capture. Every step reports PASS/FAIL; export as CSV. Use it to screen ASIC chips.",
    "registers": "Control register: tick bits and write them. Status register: read the live value; each set bit lights up with its meaning.",
    "memory": "Raw bus peek/poke via the controller (CMD_PEEK/CMD_POKE). Enter a hex address and a word count to Read, or a list of hex words to Write. Words are 32-bit and step by 4 bytes. Use it to inspect any peripheral/memory or to push custom data into the Sw-RV data memory (e.g. base 0x08100000 region). Bring-up/debug tool -- there is no bus watchdog, so only touch addresses you know are mapped.",
    "cpa": "Analyze a local capture without connecting a board. This public checkout bundles neither trace data nor the legacy examples/cpa_*.py helpers; supply both from the companion package. With Capture empty, the GUI checks the conventional datasets/<core>_reference.npz path. The Acquisition folder contains the packaged automatic analysis workflow.",
    "swrv": "Load a program into the Sw-RV target core: pick its instruction and data .vmem images (build them under Software/SW_RV/, or use your own) and a data-memory base, then Load. Afterwards choose core 'swrv' in the Crypto experiment tab to run it. This is how you run different software on the target for measurement.",
    "monitor": "Raw UART log. Non-printable/noise bytes are shown as \\xNN (never dropped or crashed on). Export the log to CSV.",
    "capture": "Review setup checks the form without accessing devices. Capture needs both the board UART and the scope connection. Connection handles alone do not verify firmware, clock lock, wiring or waveform quality. Capture saves acquired rows; it does not run a ciphertext reference check or determine how many traces an analysis needs.",
}


def help_button(key):
    b = QPushButton("?")
    b.setObjectName("help")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setToolTip(HELP.get(key, ""))
    b.setAccessibleName("Help: " + key)
    b.clicked.connect(lambda: QMessageBox.information(None, "Help", HELP.get(key, "")))
    return b


class HelpPanel(QGroupBox):
    """Keep help beside the panel heading instead of spending an entire form row."""
    def __init__(self, title, key):
        super().__init__(title)
        self.help = help_button(key)
        self.help.setParent(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.help.move(max(0, self.width() - 30), 1)


class ResultConsole(QPlainTextEdit):
    """Offer a modest preferred height, while still using spare desktop space."""
    def sizeHint(self):
        return QSize(super().sizeHint().width(), 120)


class Led(QWidget):
    def __init__(self, label=""):
        super().__init__()
        self._dot = QLabel(); self._dot.setFixedSize(16, 16); self.set_color(GRAY)
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._dot)
        if label:
            lay.addWidget(QLabel(label))
        lay.addStretch()

    def set_color(self, color):
        # Radius from the actual dot size: a radius larger than half the box
        # makes Qt fall back to square corners (the old hardcoded 8px turned
        # the 12px line LEDs into squares).
        r = max(2, self._dot.width() // 2)
        self._dot.setStyleSheet(
            f"background:{color}; border-radius:{r}px; border:1px solid rgba(0,0,0,0.45);")


class Section(QGroupBox):
    """A panel whose body collapses, so setup-time controls do not push the
    controls you use every run off the bottom of the window. The title carries
    a plain-text ▸/▾ triangle so the expand/collapse affordance is visible even
    without the Qt SVG image plugin."""
    def __init__(self, title, collapsed=False):
        self._title = title
        super().__init__(title)
        self.setCheckable(True)
        self.setToolTip("Click the header to expand or collapse this panel.")
        self._body = QWidget()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._body)
        self.toggled.connect(self._apply)
        self.setChecked(not collapsed)
        # Apply unconditionally: setChecked() only emits `toggled` on an actual
        # change, so on the collapsed=True path the signal may not fire.
        self._apply(not collapsed)

    def _apply(self, on):
        # A checkable group box hides its body when collapsed and lets Qt
        # disable the (now hidden) child controls; expanding re-enables them.
        # The controls are never both hidden and reachable, so this is safe.
        self._body.setVisible(on)
        self.setTitle(("▾  " if on else "▸  ") + self._title)

    def body(self):
        return self._body


class InputRow(QWidget):
    """A per-variable input: label + Fixed/Random radios + hex field."""
    def __init__(self, name, default_hex=""):
        super().__init__()
        self.name = name
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        title = {"key": "Key", "pt": "Plaintext", "nonce": "Nonce", "ad": "Associated data"}.get(name, name)
        label = QLabel(title); label.setFixedWidth(112)
        lay.addWidget(label)
        self.random = QRadioButton("Random")
        self.fixed = QRadioButton("Fixed")
        self.fixed.setChecked(True)
        grp = QButtonGroup(self); grp.addButton(self.random); grp.addButton(self.fixed)
        lay.addWidget(self.fixed); lay.addWidget(self.random)
        self.value = QLineEdit(default_hex); self.value.setPlaceholderText("16-byte hex")
        self.value.setAccessibleName(title + " hexadecimal value")
        self.fixed.setAccessibleName(title + ": fixed")
        self.random.setAccessibleName(title + ": random")
        label.setBuddy(self.value)
        lay.addWidget(self.value, 1)
        self.random.toggled.connect(lambda on: self.value.setDisabled(on))

    def variable(self):
        if self.random.isChecked():
            return Variable(random=True)
        return Variable(random=False, value=bytes.fromhex(self.value.text().replace(" ", "") or "00" * 16))

    def set_enabled(self, on):
        self.setEnabled(on)


class StatusRegisterView(QWidget):
    """Graphical status register: one labelled cell per bit, lit when set."""
    def __init__(self):
        super().__init__()
        self.bits = {}  # bitpos -> (dot, name)
        names = {getattr(regs, n): n[5:] for n in dir(regs)
                 if n.startswith("STAT_") and isinstance(getattr(regs, n), int)}
        grid = QGridLayout(self)
        for i, pos in enumerate(range(32)):
            mask = 1 << pos
            label = names.get(mask, "-")
            dot = QLabel(); dot.setFixedSize(12, 12)
            dot.setStyleSheet(f"background:{GRAY}; border-radius:6px;")
            cell = QWidget(); cl = QHBoxLayout(cell); cl.setContentsMargins(2, 1, 2, 1)
            cl.addWidget(dot)
            lbl = QLabel(f"{pos:2d} {label}")
            lbl.setObjectName("monoSmall")       # QSS monospace (setFont alone is
            lbl.setFont(QFont("monospace", 9))   # overridden by the stylesheet)
            if label == "-":
                lbl.setStyleSheet("color:#5d6675;")
            cl.addWidget(lbl); cl.addStretch()
            self.bits[pos] = dot
            grid.addWidget(cell, i % 8, i // 8)   # 8 rows x 4 columns

    def update_value(self, value):
        for pos, dot in self.bits.items():
            on = (value >> pos) & 1
            dot.setStyleSheet(f"background:{GREEN if on else GRAY}; border-radius:6px;")


class Bus(QObject):
    log = pyqtSignal(str, str, str)   # source, text, hex
    status = pyqtSignal(str, str)
    progress = pyqtSignal(int)
    leds = pyqtSignal(dict)
    out = pyqtSignal(str)
    statusreg = pyqtSignal(int)
    azrow = pyqtSignal(tuple)         # one A-Z check result: (category,name,status,detail)
    azdone = pyqtSignal(int, int, int)  # A-Z summary: pass, fail, skip
    info = pyqtSignal(str, str)         # (title, text) -> popup message box
    btn = pyqtSignal(str, bool)         # (button name, enabled) from worker threads
    capprogress = pyqtSignal(int)       # trace-capture progress (own bar)
    linestate = pyqtSignal(dict)        # sync reset per-line toggle checkboxes
    memout = pyqtSignal(str)            # raw bus read/write output
    cpaout = pyqtSignal(str)            # CPA attack output
    jobdone = pyqtSignal(str)          # empty on return; exception text on failure
    polldone = pyqtSignal()
    taskprogress = pyqtSignal(int, int)  # completed attempts, planned attempts


class MainWindow(QMainWindow):
    # These limits apply to the live display, not to optional experiment files.
    LOG_LIMIT = 5000
    OUTPUT_BLOCK_LIMIT = 5000
    LOG_BATCH_SIZE = 200

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PROACT Chip GUI — not connected")
        # Clamp the initial size to the actual screen (logical units), so the
        # window always fits -- including scaled/HiDPI and small displays.
        scr = QApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            self.resize(min(1500, g.width() - 60), min(1000, g.height() - 60))
            self.move(g.left() + (g.width() - self.width()) // 2,
                      g.top() + (g.height() - self.height()) // 2)
        else:
            self.resize(1400, 950)
        assets = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
        self.setStyleSheet(STYLE.replace("@ASSETS@", assets.replace(os.sep, "/")))
        self.bus = Bus()
        self._busy = False
        self._poll_active = False
        self._closing = False
        self._close_requested = False
        self._job_label = ""
        self._job_started = 0.0
        self._job_progress = None
        self._busy_controls = {}
        self._capture_outcome = ""
        self.capture_review_dialog = None
        self._pending_logs = deque(maxlen=self.LOG_LIMIT)
        self._log_discarded = 0
        self.bus.log.connect(self._log)
        self.bus.status.connect(self._set_status)
        self.bus.progress.connect(lambda v: self.prog_bar.setValue(v))
        self.bus.leds.connect(self._update_leds)
        self.bus.out.connect(lambda s: self.exp_out.appendPlainText(s))
        self.bus.statusreg.connect(self._show_statusreg)
        self.bus.info.connect(lambda t, m: QMessageBox.information(self, t, m))
        self.bus.btn.connect(self._btn_enabled)
        self.bus.capprogress.connect(lambda v: self.cap_bar.setValue(v))
        self.bus.linestate.connect(self._sync_line_toggles)
        self.bus.memout.connect(lambda s: self.mem_out.setPlainText(s))
        self.bus.cpaout.connect(lambda s: self.cpa_out.setPlainText(s))
        self.bus.jobdone.connect(self._finish_job)
        self.bus.polldone.connect(self._finish_poll)
        self.bus.taskprogress.connect(self._set_job_progress)

        self.uart = self.target = self.programmer = self.resets = self.scope = None
        self.mon = MonitorDecoder()

        central = QWidget(); self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10); root.setSpacing(10)
        # Sidebar scrolls on short screens instead of dictating the window's
        # minimum height.
        side_scroll = QScrollArea(); side_scroll.setWidget(self._sidebar())
        side_scroll.setWidgetResizable(True)
        side_scroll.setFixedWidth(378)  # sidebar 360 + scrollbar
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(side_scroll, 0)
        root.addWidget(self._center(), 1)
        self._menu()
        for layout in self.findChildren(QGridLayout):
            layout.setVerticalSpacing(4)
        self.activity_label = QLabel("Disconnected · no task running")
        self.activity_label.setAccessibleName("Current task and elapsed time")
        self.statusBar().addWidget(self.activity_label, 1)
        self.activity_bar = QProgressBar()
        self.activity_bar.setFixedWidth(140); self.activity_bar.setRange(0, 0)
        self.activity_bar.setAccessibleName("Task is running")
        self.activity_bar.hide(); self.statusBar().addPermanentWidget(self.activity_bar)
        self.ui_timer = QTimer(self); self.ui_timer.timeout.connect(self._refresh_activity)
        self.ui_timer.start(100)
        self._target_changed(self.target_sel.currentText())  # set ASIC/FPGA initial state
        self._update_capture_state()
        self.timer = QTimer(self); self.timer.timeout.connect(self._poll)
        self.timer.start(1000)

    # ------------------------------------------------------------- sidebar
    def _titled(self, title, help_key):
        box = HelpPanel(title, help_key)
        h = QHBoxLayout(); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(0)
        return box, h

    def _sidebar(self):
        w = QWidget(); w.setFixedWidth(360); v = QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6); v.setSpacing(10)

        brand = QWidget(); brand.setObjectName("brand")
        bl = QVBoxLayout(brand); bl.setContentsMargins(14, 10, 14, 10); bl.setSpacing(1)
        b_title = QLabel("PROACT"); b_title.setObjectName("brandTitle")
        b_sub = QLabel("Chip control panel"); b_sub.setObjectName("brandSub")
        bl.addWidget(b_title); bl.addWidget(b_sub)
        v.addWidget(brand)

        conn, hh = self._titled("Connection", "connection")
        cg = QGridLayout(conn)
        self.spi_led = Led("SPI (MCP2210)"); self.uart_led = Led("UART (MCP2200)")
        cg.addLayout(hh, 0, 1)
        cg.addWidget(self.spi_led, 1, 0, 1, 2); cg.addWidget(self.uart_led, 2, 0, 1, 2)
        cg.addWidget(QLabel("Target"), 3, 0); self.target_sel = QComboBox()
        self.target_sel.addItems(["ASIC", "FPGA (CW305)"])
        self.target_sel.currentTextChanged.connect(self._target_changed)
        cg.addWidget(self.target_sel, 3, 1); cg.addWidget(help_button("targetplat"), 3, 2)
        cg.addWidget(QLabel("Port"), 4, 0); self.port_edit = QLineEdit(placeholderText="auto")
        cg.addWidget(self.port_edit, 4, 1)
        cg.addWidget(QLabel("Baud"), 5, 0); self.baud = QComboBox(); self.baud.addItems(["115200","38400","19200","9600"])
        cg.addWidget(self.baud, 5, 1)
        bc = QPushButton("Connect"); bc.setObjectName("primary"); bc.clicked.connect(self.on_connect)
        bd = QPushButton("Disconnect"); bd.clicked.connect(self.on_disconnect)
        cg.addWidget(bc, 6, 0); cg.addWidget(bd, 6, 1)
        v.addWidget(conn)

        # Reset control is a bring-up tool, not a per-run control: collapsed by
        # default so Connection and Programming stay visible without scrolling.
        rst = Section("Reset control", collapsed=True)
        rl = QVBoxLayout(rst.body()); rl.setContentsMargins(0, 0, 0, 0)
        hh = QHBoxLayout(); hh.addStretch(); hh.addWidget(help_button("reset"))
        rl.addLayout(hh)
        rl.addWidget(QLabel("Presets (each sets ALL four lines, safely):"))
        self.reset_group = QButtonGroup(self)
        for i, (t, k) in enumerate([("Run (return to running)", "run"),
                ("Reset all (baseline, all held)", "reset_all"),
                ("Controller reset (hold CPU)", "controller"),
                ("Global reset (Sw-RV + crypto; CPU held too)", "global"),
                ("SPI loader released (CPU+crypto held)", "spi")]):
            rb = QRadioButton(t); rb.setProperty("mode", k)
            if k == "run":
                rb.setChecked(True)
            self.reset_group.addButton(rb, i); rl.addWidget(rb)
        ba = QPushButton("Apply preset"); ba.clicked.connect(self.on_apply_reset); rl.addWidget(ba)
        # Per-line view + toggles. The checkbox state mirrors the actual
        # read-back pin (synced on connect and every poll). Toggling routes
        # through the SAFE preset sequences where needed -- a bare crypto
        # reset under a running CPU would wedge the bus for good.
        rl.addWidget(QLabel("Lines (checked = released / active):"))
        self.line_leds = {}
        self.line_toggles = {}
        for name in ("controller", "global", "spi", "spi_select"):
            row = QHBoxLayout()
            led = Led(); led._dot.setFixedSize(12, 12); led.set_color(GRAY)
            led.setFixedWidth(20)   # bare dot: don't let the row stretch it
            self.line_leds[name] = led
            cb = QCheckBox(name); cb.setChecked(False); self.line_toggles[name] = cb
            cb.clicked.connect(lambda checked, n=name: self.on_toggle_line(n, checked))
            row.addWidget(led); row.addWidget(cb); row.addStretch()
            rl.addLayout(row)
        v.addWidget(rst)
        self.reset_section = rst

        prog, hh = self._titled("Programming", "program")
        pg = QGridLayout(prog); pg.addLayout(hh, 0, 2)
        # one-click preset: the controller command-server firmware the GUI drives
        bgui = QPushButton("Use GUI companion firmware")
        bgui.setToolTip("Select the controller command-server firmware that works with this GUI "
                        "(Software/Controller/main.vmem when supplied). Obtain it separately or "
                        "build it from the companion design package.")
        bgui.clicked.connect(self.on_pick_companion)
        pg.addWidget(bgui, 1, 0, 1, 3)
        self.vmem_edit = QLineEdit(placeholderText="controller .vmem  (or the GUI companion firmware)")
        pg.addWidget(self.vmem_edit, 2, 0, 1, 2)
        bb = QPushButton("Browse"); bb.clicked.connect(self.on_browse); pg.addWidget(bb, 2, 2)
        self.prog_btn = QPushButton("Program"); self.prog_btn.setObjectName("primary")
        self.prog_btn.clicked.connect(self.on_program); pg.addWidget(self.prog_btn, 3, 0, 1, 2)
        br = QPushButton("Restart ctrl"); br.clicked.connect(self.on_restart); pg.addWidget(br, 3, 2)
        self.prog_bar = QProgressBar(); pg.addWidget(self.prog_bar, 4, 0, 1, 3)
        v.addWidget(prog)
        v.addStretch()
        self.status_lbl = QLabel("Offline · choose settings, or connect the board."); self.status_lbl.setObjectName("statusLabel")
        self.status_lbl.setWordWrap(True); v.addWidget(self.status_lbl)
        return w

    # -------------------------------------------------------------- center
    @staticmethod
    def _scrolled(w):
        """Wrap a tab page in a scroll area so no page dictates a minimum
        window size -- on small/scaled screens the page scrolls instead."""
        sa = QScrollArea(); sa.setWidget(w); sa.setWidgetResizable(True)
        return sa

    def _center(self):
        self.tabs = QTabWidget()
        # One workflow step per page. Panels that used to be stacked into a
        # single scrolling tab (CPA, raw bus + Sw-RV loader) now have their own
        # page, so no page overflows a normal desktop window.
        self.tabs.addTab(self._scrolled(self._tab_experiment()), "Crypto experiment")
        self.tabs.addTab(self._scrolled(self._tab_capture()), "ChipWhisperer")
        self.tabs.addTab(self._scrolled(self._tab_cpa()), "CPA analysis")
        self.tabs.addTab(self._scrolled(self._tab_registers()), "Registers")
        # No "&" in tab text: Qt would eat it as a mnemonic marker.
        self.tabs.addTab(self._scrolled(self._tab_memory()), "Memory / Sw-RV")
        self.az_tab = self._scrolled(self._tab_selfcheck())
        self.tabs.addTab(self.az_tab, "Self-Check (A–Z)")
        self.tabs.addTab(self._scrolled(self._tab_monitor()), "UART monitor")
        return self.tabs

    def _page(self, title, description, tag="Board required"):
        """Consistent orientation on every page, without hiding the task controls."""
        w = QWidget(); v = QVBoxLayout(w)
        v.setContentsMargins(10, 8, 10, 8); v.setSpacing(8)
        row = QHBoxLayout()
        heading = QLabel(title); heading.setObjectName("pageTitle")
        row.addWidget(heading); row.addStretch()
        badge = QLabel(tag); badge.setObjectName("pageTag"); row.addWidget(badge)
        v.addLayout(row)
        subtitle = QLabel(description); subtitle.setObjectName("pageDescription")
        subtitle.setWordWrap(True); v.addWidget(subtitle)
        return w, v

    def _tab_selfcheck(self):
        """One-click A-Z chip screen: every core, timer, PRNG, control, capture.
        Reusable for ASIC bring-up -- connect over UART and press Run."""
        w, v = self._page("Check the complete system", "Review individual results, then export the check record.")
        box, hh = self._titled("Full self-check (A–Z)", "selfcheck")
        bl = QVBoxLayout(box); bl.addLayout(hh)
        az_desc = QLabel(
            "Checks UART, core reference vectors, timer, control registers, PRNG and "
            "available Sw-RV firmware. Optional scope checks include clock lock and "
            "one trace. Results appear below as each step finishes.")
        az_desc.setWordWrap(True)  # unwrapped long labels force a huge window minimum width
        bl.addWidget(az_desc)
        row = QHBoxLayout()
        self.az_capture = QCheckBox("Also capture a trace (needs the Husky connected)")
        row.addWidget(self.az_capture); row.addStretch()
        self.az_run = QPushButton("Run Full Self-Check (A–Z)"); self.az_run.setObjectName("primary")
        self.az_run.clicked.connect(self.on_fullcheck); row.addWidget(self.az_run)
        self.az_export = QPushButton("Export CSV"); self.az_export.clicked.connect(self.on_export_az)
        row.addWidget(self.az_export)
        bl.addLayout(row)
        self.az_summary = QLabel("Not run yet.")
        f = QFont(); f.setBold(True); self.az_summary.setFont(f)
        bl.addWidget(self.az_summary)
        self.az_table = QTableWidget(0, 4)
        self.az_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.az_table.setAccessibleName("Self-check results")
        self.az_table.setAlternatingRowColors(True)
        self.az_table.setHorizontalHeaderLabels(["Group", "Check", "Result", "Detail"])
        self.az_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.az_table.setColumnWidth(0, 70); self.az_table.setColumnWidth(1, 190)
        self.az_table.setColumnWidth(2, 60)
        bl.addWidget(self.az_table, 1)
        v.addWidget(box)
        self.bus.azrow.connect(self._az_add_row)
        self.bus.azdone.connect(self._az_finish)
        return w

    def _az_add_row(self, item):
        cat, name, status, detail = item
        r = self.az_table.rowCount(); self.az_table.insertRow(r)
        color = {"PASS": GREEN, "FAIL": RED, "SKIP": AMBER}.get(status, GRAY)
        for c, text in enumerate((cat, name, status, detail)):
            it = QTableWidgetItem(text)
            it.setToolTip(text)
            if c == 2:
                it.setForeground(Qt.GlobalColor.white)
                it.setBackground(QColor(color))
            self.az_table.setItem(r, c, it)
        self.az_table.scrollToBottom()

    def _az_finish(self, p, f, s):
        verdict = "ALL PASS ✓" if f == 0 else f"{f} FAILED ✗"
        self.az_summary.setText(f"{verdict}    —    {p} pass, {f} fail, {s} skip")
        self.az_summary.setStyleSheet(f"color:{GREEN if f == 0 else RED};")
        self._btn_enabled("selfcheck", True)

    def on_export_az(self):
        if self.az_table.rowCount() == 0:
            self._set_status("status", "Nothing to export -- run the self-check first."); return
        path, _ = QFileDialog.getSaveFileName(self, "Export self-check", "selfcheck_az.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            self._export_table(path, ["group", "check", "result", "detail"], self.az_table)
        except OSError as exc:
            self._set_status("status", f"Export failed: {exc}")
            self._log("Export", f"Self-check CSV failed: {exc}"); return
        self._set_status("status", f"Self-check exported to {path}")

    def on_fullcheck(self):
        if not self._need():
            return
        self.az_table.setRowCount(0)
        self.az_summary.setText("Running…"); self.az_summary.setStyleSheet(f"color:{AMBER};")
        self.az_run.setEnabled(False)
        do_cap = self.az_capture.isChecked()

        def job():
            try:
                from proact_host.fullcheck import run_full_check, summarize
                # Auto-include the Sw-RV software-AES step if its firmware is
                # built, so the GUI check is as complete as the CLI one.
                swrv = None
                try:
                    from proact_host.vmem import parse_vmem
                    imf = os.path.join(self._repo_root(), "Software", "SW_RV", "sw_rv_imem.vmem")
                    dmf = os.path.join(self._repo_root(), "Software", "SW_RV", "sw_rv_dmem.vmem")
                    if os.path.exists(imf) and os.path.exists(dmf):
                        imem = [v for _, v in parse_vmem(imf)]
                        dmem = [v for _, v in parse_vmem(dmf)]
                        swrv = (imem, dmem, 0x08100000)
                except Exception:  # noqa: BLE001
                    swrv = None
                with self.target.lock:   # own the port for the whole sweep
                    items = run_full_check(
                        self.target, scope=self.scope, clock_hz=50e6, do_capture=do_cap,
                        swrv_words=swrv,
                        on_item=lambda it: self.bus.azrow.emit(it.as_row()))
                p, f, s = summarize(items)
                self.bus.azdone.emit(p, f, s)
            except Exception as e:  # noqa: BLE001
                self.bus.azrow.emit(("error", "self-check", "FAIL", str(e)))
                self.bus.azdone.emit(0, 1, 0)
        self._run(job, "Running self-check")

    def _core_selector(self):
        c = QComboBox(); c.addItems(["aes1", "aes2", "ascon", "xoodyak", "swrv"])
        return c

    def _tab_experiment(self):
        w, v = self._page("Run a crypto experiment", "Choose a core and inputs, then inspect the returned data and reference checks.")
        box, hh = self._titled("Run crypto operations", "inputs")
        f = QVBoxLayout(box); f.addLayout(hh)

        top = QHBoxLayout()
        top.addWidget(QLabel("Core")); self.exp_core = self._core_selector()
        self.exp_core.currentTextChanged.connect(self._core_changed)
        top.addWidget(self.exp_core); top.addWidget(help_button("core"))
        self.enc_rb = QRadioButton("Encrypt"); self.enc_rb.setChecked(True)
        self.dec_rb = QRadioButton("Decrypt")
        g = QButtonGroup(self); g.addButton(self.enc_rb); g.addButton(self.dec_rb)
        top.addWidget(self.enc_rb); top.addWidget(self.dec_rb); top.addWidget(help_button("encdec"))
        top.addStretch()
        f.addLayout(top)

        self.rows = {}
        for name, dflt in (("key", "000102030405060708090a0b0c0d0e0f"),
                           ("pt", "00112233445566778899aabbccddeeff"),
                           ("nonce", ""), ("ad", "")):
            r = InputRow(name, dflt); self.rows[name] = r; f.addWidget(r)

        src = QHBoxLayout()
        self.use_file = QCheckBox("Read runs from file")
        self.file_edit = QLineEdit(placeholderText="experiments/inputs_example.txt")
        self.file_edit.setEnabled(False)
        bf = QPushButton("Browse"); bf.clicked.connect(self.on_pick_inputs)
        self.use_file.toggled.connect(self.file_edit.setEnabled)
        src.addWidget(self.use_file); src.addWidget(self.file_edit); src.addWidget(bf)
        f.addLayout(src)

        opts = QHBoxLayout()
        opts.addWidget(QLabel("Runs")); self.runs = QSpinBox(); self.runs.setRange(1, 1000000); self.runs.setValue(10)
        opts.addWidget(self.runs)
        opts.addWidget(QLabel("Trigger")); self.trig_src = QComboBox()
        self.trig_src.addItems(["auto", "software", "aes1", "aes2", "ascon", "xoodyak", "swrv"])
        opts.addWidget(self.trig_src); opts.addWidget(help_button("trigcfg"))
        opts.addWidget(QLabel("Internal")); self.int_trig = QLineEdit("0x12"); self.int_trig.setMaximumWidth(60)
        opts.addWidget(self.int_trig); opts.addWidget(help_button("inttrig"))
        f.addLayout(opts)

        chk = QHBoxLayout()
        self.timer_chk = QCheckBox("Read cycles (timer)"); chk.addWidget(self.timer_chk); chk.addWidget(help_button("timer"))
        self.compare_chk = QCheckBox("Compare with reference"); self.compare_chk.setChecked(True)
        chk.addWidget(self.compare_chk); chk.addWidget(help_button("compare"))
        self.savelog_chk = QCheckBox("Also save log file"); chk.addWidget(self.savelog_chk)
        chk.addStretch()
        f.addLayout(chk)

        self.exp_run_btn = QPushButton("Run experiment"); self.exp_run_btn.setObjectName("primary")
        self.exp_run_btn.clicked.connect(self.on_experiment)
        f.addWidget(self.exp_run_btn)
        v.addWidget(box)

        self.exp_out = ResultConsole(readOnly=True); self.exp_out.setObjectName("console")
        self.exp_out.setMaximumBlockCount(self.OUTPUT_BLOCK_LIMIT)
        self.exp_out.setPlaceholderText("Experiment results appear here. Connect the board and load the controller firmware to begin.")
        self.exp_out.setAccessibleName("Experiment output")
        self.exp_out.setFont(QFont("monospace", 10))
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Results · latest 5,000 lines"))
        out_row.addStretch()
        clear_btn = QPushButton("Clear logs")
        clear_btn.clicked.connect(lambda: self.exp_out.setPlainText(""))
        out_row.addWidget(clear_btn)
        v.addLayout(out_row); v.addWidget(self.exp_out)
        self._core_changed(self.exp_core.currentText())
        return w

    def _tab_capture(self):
        w, v = self._page("Set up the scope", "Connect the clock and scope before configuring a capture.")
        v.setContentsMargins(8, 6, 8, 6); v.setSpacing(8)
        cw, hh = self._titled("ChipWhisperer scope + target clock", "cw")
        g = QGridLayout(cw); g.addLayout(hh, 0, 3)
        g.addWidget(QLabel("Frequency (MHz)"), 1, 0); self.freq = QLineEdit("50"); g.addWidget(self.freq, 1, 1)
        self.plat_lbl = QLabel("ASIC: clock on HS2"); g.addWidget(self.plat_lbl, 1, 2, 1, 2)
        bconn = QPushButton("Connect scope"); bconn.setObjectName("primary"); bconn.clicked.connect(self.on_cw_connect)
        bdisc = QPushButton("Disconnect"); bdisc.clicked.connect(self.on_cw_disconnect)
        g.addWidget(bconn, 2, 0); g.addWidget(bdisc, 2, 1)
        self.cw_lbl = QLabel("Scope not connected."); self.cw_lbl.setWordWrap(True); g.addWidget(self.cw_lbl, 3, 0, 1, 4)
        # FPGA bitstream (only used when Target = FPGA)
        g.addWidget(QLabel("PROACT bitstream"), 4, 0); self.bit_edit = QLineEdit(placeholderText="PROACT_top.bit (FPGA only)")
        g.addWidget(self.bit_edit, 4, 1, 1, 2)
        self.bit_browse = QPushButton("Browse"); self.bit_browse.clicked.connect(self.on_pick_bitstream)
        g.addWidget(self.bit_browse, 4, 3)
        self.bit_prog = QPushButton("Program FPGA bitstream"); self.bit_prog.clicked.connect(self.on_program_fpga)
        g.addWidget(self.bit_prog, 5, 0, 1, 4)
        g.addWidget(QLabel("Transport"), 6, 0); self.transport = QComboBox()
        self.transport.addItems(["MCP2210/MCP2200", "Husky SPI/UART (not implemented in GUI)"])
        self.transport.model().item(1).setEnabled(False)
        self.transport.setToolTip(HELP["transport"])
        g.addWidget(self.transport, 6, 1, 1, 2)
        g.addWidget(help_button("transport"), 6, 3)
        v.addWidget(cw)

        cap, hh = self._titled("Trace capture", "capture")
        cg = QGridLayout(cap); cg.addLayout(hh, 0, 3)
        cg.addWidget(QLabel("Core"), 1, 0); self.cap_core = self._core_selector(); cg.addWidget(self.cap_core, 1, 1)
        cg.addWidget(QLabel("Traces"), 1, 2); self.cap_n = QSpinBox(); self.cap_n.setRange(1, 1000000); self.cap_n.setValue(1000)
        self.cap_n.setToolTip(
            "Requested number of traces. The completion message reports the number actually saved. "
            "Trace requirements depend on the platform, setup, dataset and analysis protocol; "
            "there is no guaranteed recovery count for this field.")
        cg.addWidget(self.cap_n, 1, 3)

        # Key and Plaintext: each is fixed (use the hex box) or random (fresh per
        # trace). The hex box holds the fixed value / the default seed.
        cg.addWidget(QLabel("Key"), 2, 0)
        self.cap_key_mode = QComboBox(); self.cap_key_mode.addItems(["fixed", "random"])
        cg.addWidget(self.cap_key_mode, 2, 1)
        self.cap_key = QLineEdit("000102030405060708090a0b0c0d0e0f")
        self.cap_key.setToolTip("16-byte key as 32 hex chars.")
        cg.addWidget(self.cap_key, 2, 2, 1, 2)

        cg.addWidget(QLabel("Plaintext"), 3, 0)
        self.cap_input = QComboBox(); self.cap_input.addItems(["random", "fixed"])
        cg.addWidget(self.cap_input, 3, 1)
        self.cap_pt = QLineEdit("00112233445566778899aabbccddeeff")
        self.cap_pt.setToolTip("16-byte plaintext as 32 hex chars (used when Plaintext = fixed).")
        cg.addWidget(self.cap_pt, 3, 2, 1, 2)
        # enable/disable the hex box to match fixed/random
        def _sync_key(m): self.cap_key.setEnabled(m == "fixed")
        def _sync_pt(m):  self.cap_pt.setEnabled(m == "fixed")
        self.cap_key_mode.currentTextChanged.connect(_sync_key)
        self.cap_input.currentTextChanged.connect(_sync_pt)
        _sync_pt(self.cap_input.currentText())

        cg.addWidget(QLabel("Samples"), 4, 0); self.cap_samples = QLineEdit("5000")
        self.cap_samples.setToolTip(
            "ADC samples saved per trace, not target clock cycles or a crop boundary. "
            "Duration = samples / actual ADC sample rate. Establish the required window "
            "from this board's measured timing, including margin. More samples increase "
            "data size; they do not guarantee lower noise or successful analysis.")
        cg.addWidget(self.cap_samples, 4, 1)
        cg.addWidget(QLabel("Internal trig"), 4, 2); self.cap_inttrig = QLineEdit("0x12"); cg.addWidget(self.cap_inttrig, 4, 3)
        self.cap_timer = QCheckBox("Cycle logging unavailable")
        self.cap_timer.setEnabled(False)
        self.cap_timer.setToolTip("The capture workflow does not record timer values. This previously enabled control had no effect.")
        self.cap_timer.hide()  # Retained for compatibility; the review explains the limitation.
        self.cap_state = QLabel("Not connected")
        self.cap_state.setAccessibleName("Capture setup state")
        self.cap_state.setObjectName("pageTag")
        cg.addWidget(self.cap_state, 5, 0)
        self.cap_summary = QLabel(); self.cap_summary.setWordWrap(True)
        self.cap_summary.setAccessibleName("Capture setup guidance")
        self.cap_summary.setObjectName("pageDescription")
        cg.addWidget(self.cap_summary, 5, 1, 1, 3)
        cg.addWidget(QLabel("Output"), 6, 0); self.cap_out = QLineEdit("experiments/capture"); cg.addWidget(self.cap_out, 6, 1, 1, 3)
        self.cap_out.setToolTip(
            "Relative paths resolve inside this workspace. Use a new directory name such as "
            "experiments/run_01.tracepack for incremental chunks, or .npz/.h5 for a single-file "
            "snapshot. An existing .tracepack directory cannot be reused. External analysis "
            "readers must explicitly support the selected format.")
        self.cap_btn = QPushButton("Capture traces"); self.cap_btn.setObjectName("primary")
        self.cap_btn.clicked.connect(self.on_capture); cg.addWidget(self.cap_btn, 7, 0, 1, 3)
        self.cap_review_btn = QPushButton("Review setup…")
        self.cap_review_btn.setProperty("offlineHelp", True)
        self.cap_review_btn.setToolTip("Review settings and workflow limitations without accessing devices.")
        self.cap_review_btn.clicked.connect(self.on_review_capture)
        cg.addWidget(self.cap_review_btn, 7, 3)
        self.cap_bar = QProgressBar(); cg.addWidget(self.cap_bar, 8, 0, 1, 4)
        v.addWidget(cap)
        for widget in (self.cap_key, self.cap_pt, self.cap_samples, self.cap_inttrig, self.cap_out):
            widget.textChanged.connect(self._capture_settings_changed)
        for widget in (self.cap_key_mode, self.cap_input):
            widget.currentTextChanged.connect(self._capture_settings_changed)
        self.cap_n.valueChanged.connect(self._capture_settings_changed)
        self.cap_core.currentTextChanged.connect(self._capture_core_changed)
        self._capture_core_changed(self.cap_core.currentText())

        # The unified self-check lives on its own page; one compact row is
        # enough to get there (a full panel here only wasted vertical space).
        link = QHBoxLayout()
        link_lbl = QLabel("With the scope connected, the self-check also verifies the "
                          "clock lock and captures a real trace.")
        link_lbl.setWordWrap(True)
        bsc = QPushButton("Open Self-Check (A–Z) and run")
        bsc.setObjectName("primary"); bsc.clicked.connect(self.on_goto_fullcheck)
        link.addWidget(link_lbl, 1); link.addWidget(bsc)
        v.addLayout(link)
        v.addStretch()
        return w

    def _capture_core_changed(self, core):
        self.cap_inttrig.setEnabled(core in ("ascon", "xoodyak"))
        self.cap_inttrig.setToolTip(HELP["inttrig"] + " It is unused for AES1, AES2 and Sw-RV.")
        self._capture_settings_changed()

    def _capture_settings_changed(self, *_):
        self._capture_outcome = ""
        self._update_capture_state()

    def _capture_settings(self):
        """Parse only the form. No device access, random draws or file writes."""
        core = self.cap_core.currentText()
        key_random = self.cap_key_mode.currentText() == "random"
        pt_random = self.cap_input.currentText() == "random"
        return dict(core=core, n=self.cap_n.value(),
                    out=self._resolve(self.cap_out.text().strip() or "experiments/capture"),
                    key_random=key_random, pt_random=pt_random,
                    fixed_key=bytes(16) if key_random else self._hex16(self.cap_key.text(), "Fixed key"),
                    fixed_pt=bytes(16) if pt_random else self._hex16(self.cap_pt.text(), "Fixed plaintext"),
                    samples=self._integer(self.cap_samples.text(), "Samples", 1, 0x7FFFFFFF, base=10),
                    inttrig=self._integer(self.cap_inttrig.text(), "Internal trigger", 0, 0x7F)
                            if core in ("ascon", "xoodyak") else 0)

    def _capture_connection_gaps(self):
        # is_connected is the wrapper's local handle flag; never ask clock_status
        # or another device property from a UI refresh or offline review.
        gaps = []
        if self.target is None:
            gaps.append("board UART in the sidebar")
        if self.scope is None or not getattr(self.scope, "is_connected", False):
            gaps.append("scope above")
        return gaps

    def _update_capture_state(self):
        if not hasattr(self, "cap_summary"):
            return
        try:
            settings = self._capture_settings()
            summary = f"{settings['n']:,} traces × {settings['samples']:,} samples. "
            problem = ""
        except ValueError as exc:
            summary, problem = "", str(exc)
        if self._busy:
            state, message = "Working", f"{self._job_label}. Settings are held; Review setup remains available."
        elif self._capture_outcome:
            state, message = "Needs attention", "Last capture failed or was incomplete. See the task status and UART log."
        elif problem:
            state, message = "Check settings", problem
        else:
            gaps = self._capture_connection_gaps()
            if gaps:
                state, message = "Not connected", "Connect " + " and ".join(gaps) + "."
            else:
                state, message = "Connected", "Form valid; firmware and waveform quality still require verification."
        self.cap_state.setText(state)
        colors = {"Needs attention": ("#ffb4ad", "#352127"),
                  "Check settings": ("#f4ca80", "#302a20"),
                  "Working": ("#9fc1ff", "#1b2a43")}
        foreground, background = colors.get(state, ("#b6c6dd", "#1b2433"))
        self.cap_state.setStyleSheet(f"color:{foreground};background:{background};")
        self.cap_summary.setText(message)
        self.cap_summary.setToolTip(summary + message)
        self.cap_state.setToolTip("Local connection/form state only. This summary does not communicate with the board.")

    def on_review_capture(self):
        """Show a copyable, nonmodal review without connecting or starting work."""
        core = self.cap_core.currentText()
        try:
            settings = self._capture_settings()
            state = "Form settings valid"
            values = f"{settings['n']:,} traces × {settings['samples']:,} samples = {settings['n'] * settings['samples']:,} sample values"
        except ValueError as exc:
            state, values = "Correct this field: " + str(exc), "Counts unavailable until the form is valid."
        gaps = self._capture_connection_gaps()
        connections = "Missing: " + " and ".join(gaps) if gaps else "Board UART and scope handles are present."
        out = self._resolve(self.cap_out.text().strip() or "experiments/capture")
        suffix = os.path.splitext(out)[1].lower()
        if suffix == ".tracepack":
            storage = "New tracepack directory; incremental chunks. The current CPA page cannot open this format."
        elif suffix in (".npz", ".h5", ".hdf5"):
            storage = "Single-file snapshot; rows stay in memory and each checkpoint rewrites the dataset."
        else:
            storage = "Automatic suffix: .h5 when HDF5 is available, otherwise .npz. An explicit suffix makes the choice clearer."
        behavior = ("Nonce and associated data are each 16 zero bytes in this capture form. The internal trigger applies here. "
                    "Only the first 16 result bytes are stored; AEAD tags are not retained."
                    if core in ("ascon", "xoodyak") else
                    "Internal trigger phase is unused for this core. " +
                    ("Instruction/data images and base come from Memory / Sw-RV and are loaded when capture starts."
                     if core == "swrv" else "This workflow performs hardware AES encryption."))
        rows = [
            ("1 · Connections", connections + " This review does not verify firmware, clock lock, wiring or waveform quality."),
            ("2 · Operation", f"{core.upper()} encryption. Key: {self.cap_key_mode.currentText()}; plaintext: {self.cap_input.currentText()}. " + behavior),
            ("3 · Record length", values + ". Samples are ADC values, not target cycles or a crop boundary. Duration = samples / actual ADC rate. Choose the window from measured timing with margin."),
            ("Clock field", f"Requested target frequency: {self.freq.text()} MHz. Applied when connecting/programming, not when editing an existing connection. This review does not read the actual ADC clock."),
            ("4 · Output", out + "\n" + storage + " Existing single-file outputs can be replaced; choose a distinct experiment name."),
            ("Automatic settings", "The capture worker attempts the library's gain preset for the selected core. It is not a noise optimization or clipping check. Cycle counts are not recorded."),
            ("What completion means", "The saved/requested counts describe acquisition completion. This GUI capture loop does not compare ciphertexts with a reference. Use the experiment/reference workflow for functional checks; neither this review nor a connected badge certifies a measurement."),
        ]
        body = "".join(f"<tr><td width='140'><b>{escape(title)}</b></td><td>{escape(text).replace(chr(10), '<br>')}</td></tr>" for title, text in rows)
        html = ("<style>body{color:#dbe3ee;font-family:sans-serif;font-size:13px;}"
                "td{padding:9px;border-bottom:1px solid #303a4d;vertical-align:top;}"
                "h2{color:#eef3ff;}p{color:#afbdd2;}</style>"
                f"<h2>{escape(state)}</h2><p>Offline review · no device commands, file writes or capture started.</p>"
                "<p>Settings snapshot. Open Review setup again after changing the form.</p>"
                f"<table width='100%' cellspacing='0'>{body}</table>")
        if self.capture_review_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("Capture setup review · offline")
            dialog.resize(780, 650)
            layout = QVBoxLayout(dialog)
            self.capture_review_text = QTextBrowser()
            self.capture_review_text.setOpenExternalLinks(False)
            self.capture_review_text.setAccessibleName("Capture configuration and workflow explanation")
            layout.addWidget(self.capture_review_text)
            close = QPushButton("Close review"); close.clicked.connect(dialog.hide)
            close.setProperty("offlineHelp", True)
            layout.addWidget(close)
            self.capture_review_dialog = dialog
        self.capture_review_text.setHtml(html)
        self.capture_review_dialog.show()
        self.capture_review_dialog.raise_()

    def _tab_cpa(self):
        """CPA key-recovery on a local capture with a companion analysis script."""
        w, v = self._page("Inspect recorded traces", "Select an existing dataset and review analysis output here.", "Offline")
        an, ah = self._titled("CPA attack (offline)", "cpa")
        ag = QGridLayout(an); ag.addLayout(ah, 0, 3)
        ag.setColumnStretch(1, 1); ag.setColumnStretch(2, 1)  # widen the path field
        ag.addWidget(QLabel("Capture"), 1, 0)
        self.cpa_file = QLineEdit()
        self.cpa_file.setPlaceholderText("select .npz/.h5; empty checks datasets/<core>_reference.npz")
        ag.addWidget(self.cpa_file, 1, 1, 1, 2)
        bcpa = QPushButton("Browse")
        bcpa.clicked.connect(lambda: self._pick_into(self.cpa_file, "Capture (*.npz *.h5)"))
        ag.addWidget(bcpa, 1, 3)
        ag.addWidget(QLabel("Attack"), 2, 0)
        self.cpa_core = QComboBox(); self.cpa_core.addItems(["aes1", "aes2", "swrv"])
        self.cpa_core.setToolTip("aes1/aes2 use the last-round ciphertext model; "
                                 "swrv uses the first-round S-box model (software AES).")
        ag.addWidget(self.cpa_core, 2, 1)
        ag.addWidget(QLabel("Filter"), 2, 2)
        self.cpa_filter = QComboBox(); self.cpa_filter.addItems(["auto", "1 (off)", "2", "4", "8", "16", "24"])
        self.cpa_filter.setToolTip(
            "Moving-average width in samples. '1 (off)' leaves the waveform unchanged; "
            "'auto' uses the existing analysis script's selection. Filtering does not "
            "guarantee better results. Record the choice when comparing datasets.")
        ag.addWidget(self.cpa_filter, 2, 3)
        self.cpa_btn = QPushButton("Run CPA"); self.cpa_btn.setObjectName("primary")
        self.cpa_btn.clicked.connect(self.on_cpa); ag.addWidget(self.cpa_btn, 3, 0, 1, 4)
        v.addWidget(an)
        # The page is now dedicated to CPA, so the recovered key gets the room.
        self.cpa_out = QPlainTextEdit(); self.cpa_out.setReadOnly(True)
        self.cpa_out.setObjectName("console"); self.cpa_out.setFont(QFont("monospace", 10))
        self.cpa_out.setPlaceholderText("The recovered key is printed here.")
        v.addWidget(QLabel("Result:"))
        v.addWidget(self.cpa_out, 1)
        return w

    def _tab_registers(self):
        w, v = self._page("Inspect chip registers", "Write the selected control bits or read the current hardware status.")
        ctrl, hh = self._titled("Control register (write)", "registers")
        cg = QGridLayout(ctrl); cg.addLayout(hh, 0, 1)
        self.ctrl_bits = {}
        names = [n[5:] for n in dir(regs) if n.startswith("CTRL_") and isinstance(getattr(regs, n), int)
                 and n != "CTRL_CFGSEL_SHIFT"]
        names = sorted(names, key=lambda n: getattr(regs, "CTRL_" + n))
        for i, name in enumerate(names):
            cb = QCheckBox(f"{name}"); self.ctrl_bits[name] = cb; cg.addWidget(cb, 1 + i // 2, i % 2)
        b_wr = QPushButton("Write control register"); b_wr.setObjectName("primary")
        b_wr.clicked.connect(self.on_write_ctrl); cg.addWidget(b_wr, (len(names) + 3) // 2, 0, 1, 2)
        v.addWidget(ctrl)

        stat, hh = self._titled("Status register (read)", "registers")
        sv = QVBoxLayout(stat); sv.addLayout(hh)
        self.status_val = QLabel("value: 0x--------"); self.status_val.setObjectName("monoValue")
        self.status_val.setFont(QFont("monospace", 11)); sv.addWidget(self.status_val)
        self.status_view = StatusRegisterView(); sv.addWidget(self.status_view)
        b = QPushButton("Read status register"); b.clicked.connect(self.on_read_status); sv.addWidget(b)
        v.addWidget(stat)
        v.addStretch()
        return w

    def _tab_memory(self):
        """Raw bus access and the Sw-RV program loader — the two bring-up /
        debug panels, on their own page so the register views stay readable."""
        w, v = self._page("Memory and software programs", "Inspect mapped memory or load a program into the software execution core.")

        # --- Raw memory / bus access (peek & poke) --------------------------
        mem, hh = self._titled("Raw bus access (peek / poke)", "memory")
        mg = QGridLayout(mem); mg.addLayout(hh, 0, 3)
        mg.addWidget(QLabel("Address (hex)"), 1, 0)
        self.mem_addr = QLineEdit("0x20000000")   # status register: safe to read
        self.mem_addr.setToolTip("Word address on the system bus. Words are 32-bit; "
                                 "consecutive accesses step by 4 bytes.\n"
                                 "Reading is safe on mapped addresses; WRITING to CPU RAM "
                                 "(e.g. the controller's own data memory) can wedge the chip "
                                 "-- to load the Sw-RV target use the loader below, not raw poke.")
        self.mem_addr.returnPressed.connect(self.on_mem_read)   # Enter = Read
        mg.addWidget(self.mem_addr, 1, 1)
        mg.addWidget(QLabel("Length (words)"), 1, 2)
        self.mem_len = QSpinBox(); self.mem_len.setRange(1, 4096); self.mem_len.setValue(1)
        mg.addWidget(self.mem_len, 1, 3)
        mg.addWidget(QLabel("Data (hex words,\nspace/comma sep)"), 2, 0)
        self.mem_data = QLineEdit()
        self.mem_data.setPlaceholderText("deadbeef 01234567 ...   (for Write; length words)")
        mg.addWidget(self.mem_data, 2, 1, 1, 3)
        b_rd = QPushButton("Read"); b_rd.clicked.connect(self.on_mem_read)
        b_wr2 = QPushButton("Write"); b_wr2.setObjectName("primary"); b_wr2.clicked.connect(self.on_mem_write)
        mg.addWidget(b_rd, 3, 0); mg.addWidget(b_wr2, 3, 1)
        self.mem_out = QPlainTextEdit(readOnly=True); self.mem_out.setObjectName("console")
        self.mem_out.setFont(QFont("monospace", 10))
        self.mem_out.setMaximumHeight(120)
        mg.addWidget(self.mem_out, 4, 0, 1, 4)
        v.addWidget(mem)

        # --- Sw-RV target program loader ------------------------------------
        swrv, hh = self._titled("Sw-RV target program", "swrv")
        sg = QGridLayout(swrv); sg.addLayout(hh, 0, 3)
        sg.addWidget(QLabel("Instruction mem (.vmem)"), 1, 0)
        self.swrv_imem = QLineEdit(os.path.join(self._repo_root(), "Software", "SW_RV", "sw_rv_imem.vmem"))
        sg.addWidget(self.swrv_imem, 1, 1, 1, 2)
        bi = QPushButton("Browse"); bi.clicked.connect(lambda: self._pick_into(self.swrv_imem, "VMEM (*.vmem)")); sg.addWidget(bi, 1, 3)
        sg.addWidget(QLabel("Data mem (.vmem)"), 2, 0)
        self.swrv_dmem = QLineEdit(os.path.join(self._repo_root(), "Software", "SW_RV", "sw_rv_dmem.vmem"))
        sg.addWidget(self.swrv_dmem, 2, 1, 1, 2)
        bd = QPushButton("Browse"); bd.clicked.connect(lambda: self._pick_into(self.swrv_dmem, "VMEM (*.vmem)")); sg.addWidget(bd, 2, 3)
        sg.addWidget(QLabel("Data mem base (hex)"), 3, 0)
        self.swrv_base = QLineEdit("0x08100000"); sg.addWidget(self.swrv_base, 3, 1)
        bl = QPushButton("Load program into Sw-RV"); bl.setObjectName("primary")
        bl.clicked.connect(self.on_load_swrv); sg.addWidget(bl, 3, 2, 1, 2)
        swrv_hint = QLabel("Loads the selected program into the Sw-RV core's instruction & data "
                           "memories over the controller. Pick your own .vmem files to run different "
                           "software on the target.")
        swrv_hint.setWordWrap(True); sg.addWidget(swrv_hint, 4, 0, 1, 4)
        v.addWidget(swrv); v.addStretch()
        return w

    def _pick_into(self, line_edit, filt):
        p, _ = QFileDialog.getOpenFileName(self, "Select file", "", filt)
        if p:
            line_edit.setText(p)

    def on_mem_read(self):
        if not self._need(): return
        n = self.mem_len.value()
        try:
            addr = self._address(self.mem_addr.text(), "Address")
            self._word_span(addr, n, "Read range")
        except ValueError as exc:
            self.mem_out.setPlainText(str(exc)); return
        def job():
            try:
                words = self.target.peek_words(addr, n)
                lines = [f"0x{addr + i*4:08X}: 0x{w:08X}" for i, w in enumerate(words)]
                self.bus.memout.emit("\n".join(lines))
            except Exception as e:  # noqa: BLE001
                self.bus.memout.emit(f"read error: {e}")
        self._run(job, "Reading memory")

    def on_mem_write(self):
        if not self._need(): return
        try:
            addr = self._address(self.mem_addr.text(), "Address")
            toks = self.mem_data.text().replace(",", " ").split()
            if not toks:
                raise ValueError("Enter one or more 32-bit hexadecimal words to write.")
            words = [self._integer(token, f"Data word {i + 1}", 0, 0xFFFFFFFF, base=16)
                     for i, token in enumerate(toks)]
            self._word_span(addr, len(words), "Write range")
        except ValueError as exc:
            self.mem_out.setPlainText(str(exc)); return
        def job():
            try:
                self.target.poke_words(addr, words)
                self.bus.memout.emit("wrote %d word(s) from 0x%08X:\n%s" % (
                    len(words), addr,
                    "\n".join(f"0x{addr + i*4:08X}: 0x{w:08X}" for i, w in enumerate(words))))
            except Exception as e:  # noqa: BLE001
                self.bus.memout.emit(f"write error: {e}")
        self._run(job, "Writing memory")

    def _resolve(self, path):
        """Resolve a possibly-relative path against the repo root, so the GUI
        works no matter which directory it was launched from."""
        path = os.path.expanduser(path)
        return path if os.path.isabs(path) else os.path.join(self._repo_root(), path)

    @staticmethod
    def _integer(text, label, minimum, maximum, *, base=0):
        try:
            value = int(text.strip(), base)
        except (ValueError, TypeError, AttributeError):
            raise ValueError(f"{label} must be an integer between {minimum} and {maximum}.") from None
        if not minimum <= value <= maximum:
            raise ValueError(f"{label} must be between {minimum} and {maximum}; got {value}.")
        return value

    @classmethod
    def _address(cls, text, label):
        value = cls._integer(text, label, 0, 0xFFFFFFFF)
        if value % 4:
            raise ValueError(f"{label} must be 4-byte aligned; got 0x{value:X}.")
        return value

    @staticmethod
    def _word_span(address, count, label):
        if count > 0 and address + (count - 1) * 4 > 0xFFFFFFFF:
            raise ValueError(f"{label} exceeds the 32-bit address space.")

    def _frequency_hz(self):
        try:
            value = float(self.freq.text()) * 1e6
        except ValueError:
            value = math.nan
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Frequency must be a finite, positive number in MHz.")
        return value

    def _input_error(self, context, error):
        message = f"{context}: {error}"
        self._set_status("status", message)
        self._log("Input", message)

    def on_load_swrv(self):
        if not self._need(): return
        imf = self._resolve(self.swrv_imem.text().strip())
        dmf = self._resolve(self.swrv_dmem.text().strip())
        try:
            base = self._address(self.swrv_base.text(), "Sw-RV data-memory base")
        except ValueError as exc:
            self._input_error("Sw-RV load", exc); return
        if not os.path.exists(imf):
            self.bus.info.emit("Sw-RV load FAILED",
                               f"instruction file not found:\n{imf}\n\n"
                               "Supply matching VMEM images, or build them from the "
                               "companion design package."); return
        if not os.path.exists(dmf):
            self.bus.info.emit("Sw-RV load FAILED", f"data file not found:\n{dmf}"); return
        def job():
            try:
                from proact_host.vmem import parse_vmem
                imem = [v for _, v in parse_vmem(imf)]
                dmem = [v for _, v in parse_vmem(dmf)]
                self._word_span(base, len(dmem), "Sw-RV data-memory range")
                if not imem:
                    self.bus.info.emit("Sw-RV load", f"no instructions in {imf}"); return
                # Correct order (imem+dmem loaded while the target is held in
                # reset, then released to boot the fresh code).
                self.target.load_swrv_program(imem, dmem, base)
                self.bus.log.emit("Sw-RV", f"loaded {len(imem)}w imem / {len(dmem)}w dmem @ 0x{base:08X}", "")
                self.bus.info.emit("Sw-RV program loaded",
                                   f"{len(imem)} instr words and {len(dmem)} data words loaded "
                                   "and the target booted.\n"
                                   "Select core 'swrv' in the Crypto experiment tab to run it.")
            except Exception as e:  # noqa: BLE001
                self.bus.info.emit("Sw-RV load FAILED", str(e))
        self._run(job, "Loading software program")

    def _tab_monitor(self):
        w, v = self._page("UART and activity log", "Inspect messages from every page. CSV export includes the retained display history.", "Session log")
        row = QHBoxLayout()
        row.addWidget(help_button("monitor"))
        self.cmd_edit = QLineEdit(placeholderText="hex byte e.g. 05"); self.cmd_edit.setMaxLength(2)
        self.cmd_edit.returnPressed.connect(self.on_send_cmd)   # Enter = Send byte
        bs = QPushButton("Send byte"); bs.clicked.connect(self.on_send_cmd)
        bc = QPushButton("Clear"); bc.clicked.connect(self._clear_logs)
        bx = QPushButton("Export CSV"); bx.clicked.connect(self.on_export_csv)
        self.readmon = QCheckBox("Live read"); self.readmon.setChecked(True)
        for x in (self.cmd_edit, bs, bc, bx, self.readmon):
            row.addWidget(x)
        v.addLayout(row)
        self.table = QTableWidget(0, 4)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAccessibleName("UART and application log")
        self.table.setAlternatingRowColors(True)
        self.table.setHorizontalHeaderLabels(["Time", "Source", "Text", "Hex"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 88); self.table.setColumnWidth(1, 92)
        v.addWidget(self.table)
        self.log_count = QLabel("0 messages · retains the latest 5,000")
        self.log_count.setObjectName("pageDescription"); v.addWidget(self.log_count)
        return w

    def _menu(self):
        m = self.menuBar().addMenu("&File")
        a = QAction("Export log CSV", self); a.triggered.connect(self.on_export_csv); m.addAction(a)
        q = QAction("Quit", self); q.setShortcut("Ctrl+Q"); q.triggered.connect(self.close); m.addAction(q)
        h = self.menuBar().addMenu("&Help")
        d = QAction("Documentation", self)
        d.triggered.connect(lambda: QMessageBox.information(self, "Docs",
            "See docs/ and docs/wiki/ in the repository, and docs/manual/proact_manual.pdf."))
        h.addAction(d)

    # ------------------------------------------------------------- helpers
    def _log(self, source, text, hexcol=""):
        if len(self._pending_logs) == self._pending_logs.maxlen:
            self._log_discarded += 1
        self._pending_logs.append((datetime.now().strftime("%H:%M:%S"), source, text, hexcol))

    def _flush_logs(self, limit=None):
        """Batch painting and follow the tail only if the user was already there."""
        count = min(len(self._pending_logs), self.LOG_BATCH_SIZE if limit is None else limit)
        if not count:
            return
        bar = self.table.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 2
        self.table.setUpdatesEnabled(False)
        try:
            overflow = max(0, self.table.rowCount() + count - self.LOG_LIMIT)
            for _ in range(overflow):
                self.table.removeRow(0)
            self._log_discarded += overflow
            start = self.table.rowCount(); self.table.setRowCount(start + count)
            for row in range(start, start + count):
                for col, value in enumerate(self._pending_logs.popleft()):
                    item = QTableWidgetItem(value); item.setToolTip(value)
                    self.table.setItem(row, col, item)
        finally:
            self.table.setUpdatesEnabled(True)
        if at_end:
            self.table.scrollToBottom()
        self.log_count.setText(f"{self.table.rowCount():,} messages · latest {self.LOG_LIMIT:,} retained"
                               + (f" · {self._log_discarded:,} older messages discarded" if self._log_discarded else ""))

    def _clear_logs(self):
        self._pending_logs.clear(); self.table.setRowCount(0); self._log_discarded = 0
        self.log_count.setText("0 messages · retains the latest 5,000")

    def _set_status(self, key, text):
        if key == "status": self.status_lbl.setText(text)
        elif key == "spi": self.spi_led.set_color(GREEN if text == "ok" else RED if text == "err" else GRAY)
        elif key == "uart":
            self.uart_led.set_color(GREEN if text == "ok" else RED if text == "err" else GRAY)
            self._retitle()
        elif key == "cw": self.cw_lbl.setText(text)
        if key in ("uart", "cw"):
            self._update_capture_state()
            if hasattr(self, "activity_label") and self.activity_label.text().endswith("no task running"):
                connection = ("Board connected" if self.target is not None else
                              "Scope connected" if self.scope is not None and getattr(self.scope, "is_connected", False)
                              else "Disconnected")
                self.activity_label.setText(connection + " · no task running")

    def _retitle(self):
        port = getattr(self.uart, "port", None) if self.uart else None
        self.setWindowTitle(f"PROACT Chip GUI — {port}" if port
                            else "PROACT Chip GUI — not connected")

    def _update_leds(self, d):
        for name, val in d.items():
            led = self.line_leds.get(name)
            if led: led.set_color(GREEN if val is True else RED if val is False else GRAY)

    def _sync_line_toggles(self, d):
        for name, val in d.items():
            cb = self.line_toggles.get(name)
            if cb is not None and val is not None:
                cb.blockSignals(True); cb.setChecked(bool(val)); cb.blockSignals(False)

    def _show_statusreg(self, val):
        self.status_val.setText(f"value: 0x{val:08X}")
        self.status_view.update_value(val)

    def _core_changed(self, core):
        aead = core in ("ascon", "xoodyak")
        self.rows["nonce"].set_enabled(aead)
        self.rows["ad"].set_enabled(aead)
        self.int_trig.setEnabled(aead)
        self.dec_rb.setEnabled(not aead)
        if aead:
            self.enc_rb.setChecked(True)
        self.dec_rb.setToolTip("ASCON and Xoodyak hardware are encryption-only." if aead else "")

    def _run(self, fn, label="Task"):
        """One foreground task owns the session. Never wait for device locks in Qt."""
        if self._busy or self._closing:
            self._set_status("status", f"Wait for {self._job_label or 'the current task'} to finish.")
            return False
        self._busy = True
        self._job_label = label; self._job_started = time.monotonic()
        self._job_progress = None
        self._task_status_start = f"{label} is running. You can switch pages and inspect output."
        self._set_status("status", self._task_status_start)
        # Keep tabs, text selection, help and scrollbars usable. Disable inputs
        # that could otherwise queue a reset/reprogram/disconnect behind a run.
        types = (QPushButton, QLineEdit, QComboBox, QSpinBox, QCheckBox, QRadioButton, Section)
        self._busy_controls = {w: not w.testAttribute(Qt.WidgetAttribute.WA_ForceDisabled)
                               for w in self.findChildren(QWidget)
                               if isinstance(w, types) and w.objectName() != "help"
                               and not w.property("offlineHelp")}
        for widget in self._busy_controls:
            widget.setEnabled(False)
        self.activity_bar.setRange(0, 0); self.activity_bar.show(); self._refresh_activity()
        self._update_capture_state()

        def guarded():
            error = ""
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {exc}"
                self.bus.log.emit("Error", f"{label}: {error}", "")
            finally:
                self.bus.jobdone.emit(error)
        threading.Thread(target=guarded, name="proact-gui-operation", daemon=True).start()
        return True

    def _finish_job(self, error):
        elapsed = time.monotonic() - self._job_started
        for widget, enabled in self._busy_controls.items():
            widget.setEnabled(enabled)
        self._busy_controls.clear(); self._busy = False
        self.activity_bar.hide()
        if error:
            self.activity_label.setText(f"Failed · {self._job_label} · {elapsed:.1f}s")
            self.activity_label.setToolTip(error)
            self._set_status("status", f"{self._job_label} failed: {error}")
            if self._job_label == "Capturing traces":
                self._capture_outcome = error
                self.cap_bar.setFormat("Incomplete / failed · inspect saved counts")
        else:
            self.activity_label.setText(f"Finished · {self._job_label} · {elapsed:.1f}s")
            self.activity_label.setToolTip("See the page output or UART log for the result.")
            if self.status_lbl.text() == self._task_status_start or self.status_lbl.text().startswith("Wait for "):
                self._set_status("status", f"{self._job_label} finished. See the output or log for its result.")
        self._update_capture_state()
        if self._close_requested:
            self._close_requested = False
            if not error:
                self.close()

    def _refresh_activity(self):
        self._flush_logs()
        if self._busy:
            seconds = int(time.monotonic() - self._job_started)
            count = ""
            if self._job_progress:
                done, total = self._job_progress
                count = f" · {done:,}/{total:,} operations"
            self.activity_label.setText(f"{self._job_label} · {seconds // 60:02d}:{seconds % 60:02d} elapsed{count} · other actions paused")

    def _set_job_progress(self, done, total):
        if self._busy and total > 0:
            self._job_progress = (done, total)
            self.activity_bar.setRange(0, total); self.activity_bar.setValue(done)
            self._refresh_activity()

    def closeEvent(self, event):
        if self._busy or self._poll_active:
            # Closing a window used to silently kill daemon workers mid-write.
            # Do not terminate a hardware operation; keep its progress visible.
            self._set_status("status", "A task is still running. Wait for it to finish before closing.")
            event.ignore(); return
        if self.uart or self.programmer or self.scope:
            self._close_requested = True
            self._run(self._disconnect_resources, "Closing connections")
            event.ignore(); return
        self._closing = True; self.timer.stop(); self.ui_timer.stop()
        if self.capture_review_dialog is not None:
            self.capture_review_dialog.close()
        event.accept()

    def _ensure_sendback(self):
        """Put the controller into frame (send-back) mode, as the CLI's
        _target() does. `send_back` is CPU state, so it clears on EVERY
        controller reboot: programming, Restart ctrl, and any reset action that
        re-releases the CPU. Without it CMD_RDSTAT answers in plain ASCII and
        read_frame() waits for a 0xA5 marker that never arrives. Sending the
        byte is idempotent and harmless, so re-assert it rather than tracking
        the chip's state."""
        if not self.target:
            return
        try:
            self.target.enable_sendback()
        except Exception:  # noqa: BLE001
            pass          # a dead link is reported by whatever comes next

    def _sendback_after_reboot(self, st):
        """A reset action that leaves the CPU RUNNING has just rebooted it,
        which clears `send_back`. Re-assert it, after the same settle delay
        programmer.restart_controller() already proves is enough, so the byte
        does not land mid-boot. If the controller is left held there is nobody
        to talk to, so skip it."""
        if st.get("controller"):
            time.sleep(0.3)
            self._ensure_sendback()

    def _need(self):
        if self._busy:
            self._set_status("status", f"Wait for {self._job_label} to finish."); return False
        if not self.target:
            self._set_status("status", "Connect the board UART before using this action.")
            self.bus.log.emit("System", "connect UART first", ""); return False
        return True

    def _poll(self):
        if self._busy or self._poll_active or self._closing:
            return
        resets, uart = self.resets, self.uart
        read_monitor = self.target is not None and self.readmon.isChecked()
        if not resets and not (uart and read_monitor):
            return
        self._poll_active = True
        def poll():
            try:
                st = resets.try_status() if resets else None
                if st:
                    self.bus.leds.emit(st)
                    self.bus.linestate.emit(st)
                if uart and read_monitor:
                    self._pump_monitor(uart)
            except Exception as exc:  # noqa: BLE001
                self.bus.log.emit("Monitor", f"Polling failed: {exc}", "")
            finally:
                self.bus.polldone.emit()
        threading.Thread(target=poll, name="proact-gui-poll", daemon=True).start()

    def _finish_poll(self):
        self._poll_active = False

    def _pump_monitor(self, uart=None):
        # Passive UART logging. MUST (a) never block and (b) never run during a
        # command/reply transaction -- a blocking read(256) here used to eat the
        # reply frames, and every command failed with "no frame marker".
        uart = self.uart if uart is None else uart
        lock = getattr(uart, "lock", None)
        if lock is None or not lock.acquire(blocking=False):
            return                        # an operation owns the port right now
        try:
            data = uart.read_available()
        except Exception:  # noqa: BLE001
            data = b""
        finally:
            lock.release()
        for text, hexcol in self.mon.feed(data):
            if text or hexcol:
                self.bus.log.emit("RX", text, hexcol)

    # -------------------------------------------------------------- actions
    @staticmethod
    def _hint(exc):
        """Turn a raw connection error into an actionable hint."""
        s = str(exc).lower()
        if ("permission" in s or "denied" in s or "unable to open" in s
                or "could not open" in s or "errno 13" in s or "access" in s):
            if sys.platform == "linux":
                return "  ->  run once:  sudo bash tools/install_udev.sh   then replug the device (no sudo needed after)"
            return "  ->  check if another program is using the device, and try replugging it"
        if "no module named" in s:
            if "chipwhisperer" in s:
                return "  ->  install it:  pip install chipwhisperer"
            if "hid" in s or "mcp2210" in s:
                return "  ->  missing dependencies: run  bash tools/setup_env.sh"
        if "no device" in s or "not found" in s or "no cw" in s or "returned none" in s:
            msg = "  ->  device not detected: check USB"
            if sys.platform == "linux":
                msg += ", and run  sudo bash tools/install_udev.sh"
            return msg
        return ""

    def _repo_root(self):
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    def _default_bitstream(self):
        """The default PROACT_top.bit in the repo main folder, if present."""
        p = os.path.join(self._repo_root(), "PROACT_top.bit")
        return p if os.path.exists(p) else None

    def on_connect(self):
        plat = self._platform()
        # Read all widgets on the GUI thread; the worker only uses these locals.
        port = self.port_edit.text().strip() or None
        try:
            baud = int(self.baud.currentText())
        except ValueError:
            baud = 115200
        bit = self.bit_edit.text().strip() or self._default_bitstream()
        if bit:
            bit = self._resolve(bit)
        try:
            freq = self._frequency_hz()
        except ValueError as exc:
            self._input_error("Clock settings", exc); return
        def job():
            # 1) UART (MCP2200)
            try:
                self.bus.status.emit("status", "connecting UART...")
                if self.uart:
                    self.uart.close()
                self.uart = self.target = None
                self.uart = UartTransport(port=port, baud=baud).open()
                self.target = ProactTarget(self.uart)
                self._ensure_sendback()
                self.bus.status.emit("uart", "ok"); self.bus.status.emit("status", f"UART {self.uart.port}")
            except Exception as e:  # noqa: BLE001
                self.bus.status.emit("uart", "err")
                self.bus.log.emit("System", f"UART error: {e}{self._hint(e)}", "")
            # 2) SPI (MCP2210)
            try:
                from proact_host.programmer import Mcp2210Programmer
                from proact_host.resets import ResetController
                close = getattr(self.programmer, "close", None)
                if close:
                    close()
                self.programmer = self.resets = None
                self.programmer = Mcp2210Programmer().open(); self.resets = ResetController(self.programmer)
                self.bus.status.emit("spi", "ok")
                st = self.resets.status()      # seed LEDs + toggles from the real pins
                self.bus.leds.emit(st); self.bus.linestate.emit(st)
            except Exception as e:  # noqa: BLE001
                self.bus.status.emit("spi", "err")
                self.bus.log.emit("System", f"SPI error: {e}{self._hint(e)}", "")
            # 3) FPGA: connect the scope and auto-upload the PROACT bitstream
            if plat == "fpga":
                self._fpga_connect_and_program(bit, freq)
        self._run(job, "Connecting board")

    def _fpga_connect_and_program(self, bit, freq):
        """Connect the Husky and program the CW305 with the bitstream (default:
        PROACT_top.bit in the repo main folder). Runs on the worker thread;
        `bit`/`freq` are captured on the GUI thread by the caller."""
        if not bit:
            self.bus.status.emit("cw", "FPGA: no bitstream found (put PROACT_top.bit in the "
                                 "repo main folder, or pick one in the ChipWhisperer tab)")
            return
        try:
            self.bus.status.emit("cw", "connecting scope + programming FPGA bitstream...")
            if self.scope is None:
                scope = self._connect_new_scope(freq, "fpga", bit)
                self.scope = scope
            else:
                self.scope.program_fpga(bit, fpga_id="100t", freq_hz=freq)
            st = self.scope.clock_status()
            self.bus.status.emit("cw", f"FPGA programmed with {os.path.basename(bit)}. "
                                 f"{st.get('clock_source','')} @ {st.get('target_clock_MHz')}MHz")
            self.bus.log.emit("System", f"FPGA: uploaded {bit}", "")
            self.bus.info.emit("FPGA bitstream loaded",
                               f"{os.path.basename(bit)} is running on the CW305 "
                               f"({st.get('clock_source','')} @ {st.get('target_clock_MHz')} MHz).\n"
                               "Now load the controller firmware (Program in the sidebar).")
        except Exception as e:  # noqa: BLE001
            self.bus.status.emit("cw", f"FPGA program error: {e}{self._hint(e)}")

    def _connect_new_scope(self, frequency, platform, bitstream=None):
        """Clean up a newly allocated scope if setup fails before publication."""
        from proact_host.capture import ChipWhispererCapture
        scope = ChipWhispererCapture(samples=5000, clock_hz=frequency, platform=platform)
        try:
            scope.connect(clock_hz=frequency, platform=platform, bitstream=bitstream)
        except BaseException:
            try:
                scope.disconnect()
            except Exception as cleanup_error:  # noqa: BLE001
                self.bus.log.emit("Scope", f"Partial connection cleanup failed: {cleanup_error}", "")
            raise
        return scope

    def on_disconnect(self):
        self._run(lambda: self._disconnect_resources(include_scope=False), "Disconnecting board")

    def _disconnect_resources(self, include_scope=True):
        """Called only in an operation worker, including when closing the window."""
        uart = self.uart
        lock = getattr(uart, "lock", None)
        if lock is not None:
            lock.acquire()
        try:
            if uart:
                uart.close()
            self.uart = self.target = None
        finally:
            if lock is not None:
                lock.release()
        close = getattr(self.programmer, "close", None)
        if close:
            close()
        self.programmer = self.resets = None
        if include_scope and self.scope:
            self.scope.disconnect()
            self.scope = None
        self.bus.status.emit("spi", "off"); self.bus.status.emit("uart", "off")
        self.bus.status.emit("status", "Disconnected.")

    def on_apply_reset(self):
        if not self.resets:
            self.bus.log.emit("System", "connect SPI first", ""); return
        mode = self.reset_group.checkedButton().property("mode")
        def job():
            self.resets.apply_mode(mode)
            self.bus.log.emit("Reset", f"applied preset '{mode}'", "")
            st = self.resets.status()
            self.bus.leds.emit(st)
            self.bus.linestate.emit(st)   # sync the per-line toggle checkboxes
            self._sendback_after_reboot(st)   # a released CPU was just rebooted
        self._run(job, "Applying reset preset")

    def on_toggle_line(self, name, active):
        """Toggle one line, but through SAFE sequences. spi_select is a plain
        GPIO and toggles directly; the three reset lines route through the
        preset vectors, because a bare global/spi flip under a running CPU
        wedges the bus (hardware-verified). The poll re-syncs all toggles to
        the read-back state a second later."""
        if not self.resets:
            self.bus.log.emit("System", "connect SPI first", "")
            self.line_toggles[name].setChecked(not active)  # revert visual
            return

        def job():
            if name == "spi_select":
                self.resets.set(name, active)          # harmless plain GPIO
            elif active:
                # releasing something = go (back) to a safe running state;
                # 'spi' released has its own safe (CPU-held) mode.
                self.resets.apply_mode("spi" if name == "spi" else "run")
            else:
                # holding something = that line's safe preset vector
                self.resets.apply_mode(name)
            self.bus.log.emit("Reset", f"{name} -> {'active' if active else 'in reset'}", "")
            st = self.resets.status()
            self.bus.leds.emit(st); self.bus.linestate.emit(st)
            self._sendback_after_reboot(st)   # a released CPU was just rebooted
        self._run(job, "Updating reset line")

    def on_browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "Select .vmem", "", "VMEM (*.vmem)")
        if p: self.vmem_edit.setText(p)

    def on_pick_inputs(self):
        p, _ = QFileDialog.getOpenFileName(self, "Input file", "", "Text (*.txt)")
        if p: self.file_edit.setText(p); self.use_file.setChecked(True)

    def on_program(self):
        if not self.programmer:
            self.bus.log.emit("System", "connect SPI first", ""); return
        path = self.vmem_edit.text().strip()
        if not path:
            self.bus.log.emit("System", "select a .vmem", ""); return
        self.prog_btn.setEnabled(False)
        self._run(lambda: self._program(self._resolve(path)), "Programming controller")

    def _program(self, path):
        try:
            self.bus.status.emit("status", f"programming {os.path.basename(path)}...")
            self.programmer.program(path, progress=lambda p: self.bus.progress.emit(p))
            self.bus.progress.emit(100)
            # Confirm the load produced a RUNNING chip: the SPI slave is
            # write-only, so program() reports 100% even with nothing attached.
            # verify_running reboots with the UART listening and waits for the
            # firmware's boot banner.
            running = None
            if self.uart is not None:
                try:
                    running = self.programmer.verify_running(self.uart)
                except Exception:  # noqa: BLE001
                    running = None
            self._ensure_sendback()   # programming rebooted the CPU
            self.bus.status.emit("status", "programmed."); self.bus.log.emit("Program", "done", "")
            if running is False:
                self.bus.info.emit("Programmed, but no response",
                                   f"{os.path.basename(path)} streamed over SPI, but the "
                                   "controller did not announce itself over UART.\n\n"
                                   "- On FPGA, upload the bitstream first (ChipWhisperer tab).\n"
                                   "- Check the MCP2200 UART connection.\n"
                                   "See docs/bringup_guide.md.")
            else:
                self.bus.info.emit("Programming complete",
                                   f"{os.path.basename(path)} was loaded and the controller "
                                   + ("announced itself -- the chip is running."
                                      if running else "restarted.")
                                   + "\nYou can use every tab now.")
        except Exception as e:  # noqa: BLE001
            self.bus.log.emit("Program", f"error: {e}", "")
            self.bus.info.emit("Programming FAILED", f"{e}{self._hint(e)}")
        finally:
            self.bus.btn.emit("program", True)

    def on_restart(self):
        if self.programmer:
            self._run(lambda: (self.programmer.restart_controller(),
                               self._ensure_sendback(),   # the reboot cleared it
                               self.bus.log.emit("System", "restarted", "")), "Restarting controller")

    def _build_plan(self, rows_widget, core, n):
        if self.use_file.isChecked():
            path = self.file_edit.text().strip()
            if not path:
                raise ValueError("Choose an input file, or turn off 'Read runs from file'.")
            return InputPlan(runs=parse_input_file(self._resolve(path))).validate_for_hardware(core)
        active = set(VARS) if core in ("ascon", "xoodyak") else {"key", "pt"}
        variables = {name: rows_widget[name].variable() if name in active else Variable(random=False)
                     for name in VARS}
        return InputPlan(variables=variables, n=n).validate_for_hardware(core)

    def on_experiment(self):
        if not self._need(): return
        core = self.exp_core.currentText()
        dec = self.dec_rb.isChecked()
        trig = self.trig_src.currentText()
        try:
            inttrig = (self._integer(self.int_trig.text(), "Internal trigger", 0, 0x7F)
                       if core in ("ascon", "xoodyak") else 0x12)
        except ValueError as exc:
            self._input_error("Experiment", exc)
            self.bus.out.emit(f"input error: {exc}"); return
        n = self.runs.value()
        want_timer = self.timer_chk.isChecked()
        want_compare = self.compare_chk.isChecked()
        savelog = self.savelog_chk.isChecked()

        # Build the input plan HERE, on the GUI thread, so no Qt widget
        # (InputRow radios/fields, use_file, file_edit) is read off-thread.
        try:
            plan = self._build_plan(self.rows, core, n)
        except Exception as e:  # noqa: BLE001
            self.bus.out.emit(f"input error: {e}"); return

        self.exp_run_btn.setEnabled(False)

        def job():
            try:
                with self.target.lock:  # own the port for the whole experiment
                    self._experiment_body(plan, core, dec, trig, inttrig,
                                          want_timer, want_compare, savelog)
            finally:
                self.bus.btn.emit("experiment", True)

        self._run(job, "Running experiment")

    def _experiment_body(self, plan, core, dec, trig, inttrig,
                         want_timer, want_compare, savelog):
        # Stream optional disk logging. Keeping a second in-memory list used to
        # grow with every run even when saving was off, and omitted error rows.
        path = None
        if savelog:
            folder = self._resolve("experiments")
            os.makedirs(folder, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = os.path.join(folder, f"exp_{core}_{stamp}.log")
        with open(path, "x", encoding="utf-8") if path else nullcontext(None) as logfile:
            lines = []
            def record(text):
                lines.append(text)
                if logfile:
                    logfile.write(text + "\n")
                if len(lines) >= 25:
                    self.bus.out.emit("\n".join(lines)); lines.clear()
                    if logfile:
                        logfile.flush()
            try:
                self.target.enable_sendback()
                self.target.select(core)
                self.target.set_cfgsel(None if trig == "auto" else trig)
                if core in ("ascon", "xoodyak"):
                    self.target.set_trigger_cfg(inttrig)
                self.target.set_decrypt(dec)
                record(f"# {core} {'decrypt' if dec else 'encrypt'} x{plan.n}  trig={trig} int=0x{inttrig:02X}")
                npass = 0; total = 0; failures = 0
                for i, run in enumerate(plan):
                    self.target.set_key(run["key"])
                    if core in ("ascon", "xoodyak"):
                        self.target.set_nonce(run["nonce"]); self.target.set_ad(run["ad"])
                    self.target.set_plaintext(run["pt"])
                    try:
                        mode, payload = self.target.run_and_read()
                    except Exception as exc:  # noqa: BLE001
                        record(f"{i:4d} ERROR {exc}"); failures += 1
                        if (i + 1) % 25 == 0 or i + 1 == plan.n:
                            self.bus.taskprogress.emit(i + 1, plan.n)
                        continue
                    out = payload[:16]; total += 1
                    verdict = ""
                    if want_compare:
                        ok = False
                        if core in ("aes1", "aes2", "swrv"):
                            ok = validate_aes(run["key"], run["pt"], out, decrypt=dec)
                        elif core in ("ascon", "xoodyak"):
                            ok = validate_aead(core, run["key"], run["pt"], payload,
                                               nonce=run["nonce"], ad=run["ad"], decrypt=dec)
                        if core in ("aes1", "aes2", "swrv", "ascon", "xoodyak"):
                            verdict = " PASS" if ok else " FAIL"; npass += int(ok)
                    cyc = ""
                    if want_timer:
                        try: cyc = f" cyc=0x{self.target.get_timer():08X}"
                        except Exception: cyc = " cyc=n/a"  # noqa: BLE001
                    record(f"{i:4d} pt={run['pt'].hex()} -> {payload.hex()}{verdict}{cyc}")
                    if (i + 1) % 25 == 0 or i + 1 == plan.n:
                        self.bus.taskprogress.emit(i + 1, plan.n)
                if want_compare and total:
                    record(f"# compared: {npass}/{total} passed")
                record(f"# finished: {total} responses, {failures} communication errors")
            except Exception as exc:  # noqa: BLE001
                record(f"# stopped: {type(exc).__name__}: {exc}")
                raise
            finally:
                if lines:
                    self.bus.out.emit("\n".join(lines))
                if path:
                    self.bus.out.emit(f"# log file: {path}")

    def _btn_enabled(self, name, on):
        b = {"experiment": getattr(self, "exp_run_btn", None),
             "capture": getattr(self, "cap_btn", None),
             "program": getattr(self, "prog_btn", None),
             "cpa": getattr(self, "cpa_btn", None),
             "selfcheck": getattr(self, "az_run", None)}.get(name)
        if b is not None:
            if self._busy and b in self._busy_controls:
                self._busy_controls[b] = on
            else:
                b.setEnabled(on)

    def _platform(self):
        return "fpga" if self.target_sel.currentText().startswith("FPGA") else "asic"

    def _target_changed(self, text):
        fpga = text.startswith("FPGA")
        for wdg in (self.bit_edit, self.bit_browse, self.bit_prog):
            wdg.setEnabled(fpga)
        self.plat_lbl.setText("FPGA (CW305): upload bitstream, CW305 PLL clock"
                              if fpga else "ASIC: clock generated on HS2")
        # auto-fill the default bitstream (PROACT_top.bit in the repo main folder)
        if fpga and not self.bit_edit.text().strip():
            dflt = self._default_bitstream()
            if dflt:
                self.bit_edit.setText(dflt)

    def on_pick_companion(self):
        # the controller command-server firmware this GUI drives
        path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                               "Controller", "main.vmem"))
        self.vmem_edit.setText(path)
        if not os.path.exists(path):
            self.bus.log.emit("System",
                "GUI companion firmware is not present -- select a separately supplied "
                "main.vmem or build it from the companion design package", "")
        else:
            self.bus.log.emit("System", "selected GUI companion firmware", "")

    def on_pick_bitstream(self):
        p, _ = QFileDialog.getOpenFileName(self, "Select PROACT bitstream", "", "Bitstream (*.bit)")
        if p:
            self.bit_edit.setText(p)

    def on_program_fpga(self):
        if self._platform() != "fpga":
            self.bus.status.emit("cw", "Set Target = FPGA (CW305) first"); return
        bit = self.bit_edit.text().strip()
        if not bit:
            self.bus.status.emit("cw", "select a PROACT bitstream (.bit) first"); return
        bit = self._resolve(bit)
        try:
            freq = self._frequency_hz()
        except ValueError as exc:
            self._input_error("Clock settings", exc); return
        def job():
            try:
                if self.scope is None:
                    scope = self._connect_new_scope(freq, "fpga")
                    self.scope = scope
                self.bus.status.emit("cw", "programming CW305 bitstream...")
                self.scope.program_fpga(bit, fpga_id="100t", freq_hz=freq)
                st = self.scope.clock_status()
                self.bus.status.emit("cw", f"FPGA programmed. {st.get('clock_source','')} "
                                     f"@ {st.get('target_clock_MHz')}MHz")
            except Exception as e:  # noqa: BLE001
                self.bus.status.emit("cw", f"FPGA program error: {e}{self._hint(e)}")
        self._run(job, "Programming FPGA")

    def on_cw_connect(self):
        if self.scope is not None:
            self.bus.status.emit("cw", "Scope already connected. Disconnect it before changing clock settings.")
            return
        plat = self._platform()
        bit = self.bit_edit.text().strip() or None
        if bit:
            bit = self._resolve(bit)
        try:
            freq = self._frequency_hz()
        except ValueError as exc:
            self._input_error("Clock settings", exc); return
        def job():
            try:
                scope = self._connect_new_scope(freq, plat, bit if plat == "fpga" else None)
                self.scope = scope
                st = self.scope.clock_status()
                self.bus.status.emit("cw", f"Connected ({plat.upper()}). "
                                     f"{st.get('clock_source','')} @ {st.get('target_clock_MHz')}MHz "
                                     f"adc={st.get('adc_freq_MHz')}MHz locked={st.get('locked')}")
            except Exception as e:  # noqa: BLE001
                self.bus.status.emit("cw", f"CW error: {e}{self._hint(e)}")
                raise
        self._run(job, "Connecting scope")

    def on_cw_disconnect(self):
        sc = self.scope
        if sc:
            def job():
                sc.disconnect()
                self.scope = None
                self.bus.status.emit("cw", "Scope disconnected.")
            self._run(job, "Disconnecting scope")

    @staticmethod
    def _hex16(text, label):
        """Parse an explicit 16-byte value without silently replacing user input."""
        try:
            value = bytes.fromhex(text.strip())
        except ValueError:
            raise ValueError(f"{label} must contain exactly 16 bytes of hexadecimal data.") from None
        if len(value) != 16:
            raise ValueError(f"{label} must contain exactly 16 bytes; got {len(value)}.")
        return value

    def on_capture(self):
        if not self._need(): return
        if self.scope is None or not getattr(self.scope, "is_connected", False):
            self._input_error("Capture", "Connect the scope first. Use Crypto experiment for functional runs without waveforms.")
            return
        try:
            settings = self._capture_settings()
        except ValueError as exc:
            self._input_error("Capture", exc); return
        core, n, out = settings["core"], settings["n"], settings["out"]
        pt_random, key_random = settings["pt_random"], settings["key_random"]
        fixed_key, fixed_pt = settings["fixed_key"], settings["fixed_pt"]
        samples, inttrig = settings["samples"], settings["inttrig"]
        # The Sw-RV core runs SOFTWARE AES: its program image must be loaded onto
        # the target before it can encrypt. Read + parse the vmem paths on the GUI
        # thread so the worker only handles plain word lists.
        swrv_prog = None
        if core == "swrv":
            try:
                from proact_host.vmem import parse_vmem
                imf = self._resolve(self.swrv_imem.text().strip())
                dmf = self._resolve(self.swrv_dmem.text().strip())
                base = self._address(self.swrv_base.text(), "Sw-RV data-memory base")
                swrv_prog = ([v for _, v in parse_vmem(imf)],
                             [v for _, v in parse_vmem(dmf)], base)
                if not swrv_prog[0]:
                    raise ValueError("Sw-RV instruction file contains no instructions.")
                self._word_span(base, len(swrv_prog[1]), "Sw-RV data-memory range")
            except Exception as e:  # noqa: BLE001
                self._input_error("Capture / Sw-RV program", e); return
        self.cap_btn.setEnabled(False)
        self.cap_bar.setFormat("%p%")
        self.cap_bar.setValue(0)
        self._capture_outcome = ""

        def job():
            import secrets
            store = None
            try:
                from proact_host.storage import TraceStore
                if self.scope is not None:   # honor the Samples field
                    self.scope.samples = samples
                    # If the scope rejects the requested record length, stop
                    # before issuing target commands instead of using its old size.
                    self.scope.scope.adc.samples = samples
                    # Apply the per-core ADC gain. The scope was connected with the
                    # default (low/10 dB, tuned for the hardware AES cores, whose
                    # last round leaks in the clipping-prone leading samples). The
                    # Sw-RV software AES leaks far from that edge, so 10 dB just
                    # wastes ADC range and costs traces -- see RECOMMENDED_GAIN.
                    try:
                        from proact_host.capture import RECOMMENDED_GAIN
                        gmode, gdb = RECOMMENDED_GAIN.get(core, ("low", 10.0))
                        self.scope.gain_mode, self.scope.gain_db = gmode, gdb
                        self.scope.scope.gain.mode = gmode
                        self.scope.scope.gain.db = gdb
                        self.bus.log.emit("Capture", f"ADC gain {gmode}/{gdb:g} dB for {core}", "")
                    except Exception:  # noqa: BLE001
                        pass
                store = TraceStore(out, {"target": core, "traces_requested": n,
                                         "key_mode": "random" if key_random else "fixed",
                                         "pt_mode": "random" if pt_random else "fixed"})
                done = 0
                # Hold the port for the WHOLE run -- setup + every trace -- so no
                # monitor pump or other action can reconfigure the core midway.
                with self.target.lock:
                    self.target.enable_sendback()
                    if core == "swrv" and swrv_prog is not None:
                        self.target.load_swrv_program(*swrv_prog)   # boot the software AES
                    self.target.select(core)
                    if core in ("ascon", "xoodyak"):
                        self.target.set_nonce(bytes(16)); self.target.set_ad(bytes(16))
                        self.target.set_trigger_cfg(inttrig)
                    self.target.set_decrypt(False)
                    key = fixed_key
                    if not key_random:
                        self.target.set_key(key)
                    for i in range(n):
                        if key_random:
                            key = secrets.token_bytes(16); self.target.set_key(key)
                        pt = secrets.token_bytes(16) if pt_random else fixed_pt
                        self.target.set_plaintext(pt)
                        try:
                            if self.scope: self.scope.arm()
                            mode, payload = self.target.run_and_read()
                            trace = self.scope.capture() if self.scope else []
                        except Exception as e:  # noqa: BLE001
                            store.record_failure(i, str(e)); continue
                        store.append(trace, pt, key, payload[:16])
                        done += 1
                        if i % 20 == 0:
                            self.bus.capprogress.emit(int(i * 100 / n)); store.flush()
                            self.bus.log.emit("Capture", f"{done}/{n}", "")
                path = store.close(); self.bus.capprogress.emit(100)
                self.bus.log.emit("Capture", f"saved {done} traces -> {path}", "")
                if done != n:
                    raise RuntimeError(f"Incomplete capture: saved {done}/{n} traces to {path}. Inspect failed rows in the output and UART log.")
                self.bus.info.emit("Capture complete",
                                   f"Saved {done}/{n} traces to:\n{path}")
            except Exception as e:  # noqa: BLE001
                self.bus.log.emit("Capture", f"error: {e}", "")
                actual_path = getattr(store, "path", None)
                self.bus.log.emit("Capture", (
                    f"Output location: {actual_path}. Completeness is not verified."
                    if actual_path is not None else
                    f"Requested output: {out}. No output file was confirmed."), "")
                raise
            finally:
                self.bus.btn.emit("capture", True)
        self._run(job, "Capturing traces")

    def on_cpa(self):
        """Run a separately supplied CPA helper on a local capture file."""
        core = self.cpa_core.currentText()
        path = self._resolve(self.cpa_file.text().strip()) if self.cpa_file.text().strip() else \
            os.path.join(self._repo_root(), "datasets", f"{core}_reference.npz")
        filt = self.cpa_filter.currentText().split()[0]     # "1 (off)" -> "1"
        if not os.path.exists(path):
            self.bus.info.emit(
                "CPA",
                f"capture not found:\n{path}\n\nSelect a separately supplied .npz/.h5 file.")
            return
        script = "cpa_swrv.py" if core == "swrv" else "cpa_lastround.py"
        script_path = os.path.join(self._repo_root(), "examples", script)
        if not os.path.exists(script_path):
            self.bus.info.emit(
                "CPA helper not found",
                f"Missing: {script_path}\n\nInstall the companion example scripts, or use "
                "Acquisition automatic analysis for new captures.")
            return
        self.cpa_btn.setEnabled(False)
        self.cpa_out.setPlainText(f"running CPA on {os.path.basename(path)} ...")

        def job():
            import subprocess
            import sys as _sys
            cmd = [_sys.executable, script_path, path, "--filter", filt]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
                self.bus.cpaout.emit((r.stdout or "") + (r.stderr or ""))
            except Exception as e:  # noqa: BLE001
                self.bus.cpaout.emit(f"CPA failed: {e}")
            finally:
                self.bus.btn.emit("cpa", True)
        self._run(job, "Analyzing recorded traces")

    def on_goto_fullcheck(self):
        """One-click from the ChipWhisperer tab: jump to the A-Z tab and run
        the single unified self-check (with capture when the scope is up)."""
        if self.scope is not None and getattr(self.scope, "is_connected", False):
            self.az_capture.setChecked(True)
        self.tabs.setCurrentWidget(self.az_tab)
        self.on_fullcheck()

    def on_write_ctrl(self):
        if not self._need(): return
        val = 0
        for name, cb in self.ctrl_bits.items():
            if cb.isChecked():
                val |= getattr(regs, "CTRL_" + name)
        def job():
            try:
                self.target.write_control(val)
                self.bus.log.emit("Ctrl", f"wrote control = 0x{val & 0x7FFFFFFF:08X}", "")
            except Exception as e:  # noqa: BLE001
                self.bus.log.emit("Ctrl", f"error: {e}", "")
        self._run(job, "Writing control register")

    def on_read_status(self):
        if not self._need(): return
        def job():
            try:
                v = self.target.read_status()   # real status-register read (CMD_RDSTAT)
                self.bus.statusreg.emit(v)
                self.bus.log.emit("Status", f"0x{v:08X}", "")
            except Exception as e:  # noqa: BLE001
                self.bus.log.emit("Status", f"error: {e}", "")
        self._run(job, "Reading status register")

    def on_send_cmd(self):
        if not self._need(): return
        text = self.cmd_edit.text().strip()   # read the widget on the GUI thread
        try:
            b = bytes([int(text, 16)])
        except ValueError:
            self.bus.log.emit("System", "enter a hex byte", ""); return
        uart = self.uart
        def job():
            # Hold the transaction lock so this raw byte never lands in the
            # middle of another command's request/reply framing.
            with uart.lock:
                uart.write(b)
            self.bus.log.emit("Send", text, "")
        self._run(job, "Sending UART byte")

    def on_export_csv(self):
        p, _ = QFileDialog.getSaveFileName(self, "Save CSV", "proact_log.csv", "CSV (*.csv)")
        if not p: return
        self._flush_logs(limit=len(self._pending_logs))
        try:
            self._export_table(p, ["Time", "Source", "Text", "Hex"], self.table)
        except OSError as exc:
            self._set_status("status", f"Export failed: {exc}")
            self._log("Export", f"Log CSV failed: {exc}"); return
        self.bus.log.emit("System", f"exported {p}", "")

    @staticmethod
    def _export_table(path, headers, table):
        with open(path, "w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream); writer.writerow(headers)
            for row in range(table.rowCount()):
                writer.writerow([table.item(row, col).text() if table.item(row, col) else ""
                                 for col in range(len(headers))])


def _apply_theme(app):
    """Fusion style + a matching dark palette, so the widgets the stylesheet
    does not cover (check/radio indicators, combo popups, spin arrows) render
    consistently with the QSS theme on every platform."""
    app.setStyle("Fusion")
    pal = app.palette()
    for role, col in (
        (QPalette.ColorRole.Window, "#14181f"),
        (QPalette.ColorRole.WindowText, "#e6eaf2"),
        (QPalette.ColorRole.Base, "#10141c"),
        (QPalette.ColorRole.AlternateBase, "#151b26"),
        (QPalette.ColorRole.Text, "#e6eaf2"),
        (QPalette.ColorRole.Button, "#252c3a"),
        (QPalette.ColorRole.ButtonText, "#e6eaf2"),
        (QPalette.ColorRole.BrightText, "#ffffff"),
        (QPalette.ColorRole.Highlight, "#2563eb"),
        (QPalette.ColorRole.HighlightedText, "#ffffff"),
        (QPalette.ColorRole.Link, "#8ab4f8"),
        (QPalette.ColorRole.ToolTipBase, "#1d2430"),
        (QPalette.ColorRole.ToolTipText, "#dbe3ee"),
        (QPalette.ColorRole.PlaceholderText, "#5d6675"),
    ):
        pal.setColor(role, QColor(col))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor("#5d6675"))
    app.setPalette(pal)


def main():
    app = QApplication(sys.argv)
    _apply_theme(app)
    if b"svg" not in QImageReader.supportedImageFormats():
        # Icons degrade silently without the Qt SVG image plugin (separate
        # distro package for system Pythons); everything still works.
        print("proact_gui: Qt SVG image plugin not found -- theme icons (check "
              "marks, radio dots, arrows) will be blank. Use the pip environment "
              "(bash tools/setup_env.sh) or install the Qt6 SVG package "
              "(e.g. libqt6svg6 / qt6-svg).", file=sys.stderr)
    win = MainWindow(); win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
