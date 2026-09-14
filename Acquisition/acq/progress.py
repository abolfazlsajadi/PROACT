"""Responsive, low-flicker acquisition progress for terminals and log files.

The capture driver uses one small interface: ``log``, ``note``, ``start``,
``update``, ``summary`` and ``close``. Rich owns one live render surface on an
interactive terminal. Redirected output and ``TERM=dumb`` use stable plain text.
"""
from __future__ import annotations

import time

from .console import CONSOLE, HAVE_RICH, interactive_rich, is_tty

try:
    from tqdm import tqdm
    _HAVE_TQDM = True
except Exception:  # pragma: no cover - optional fallback dependency
    tqdm = None
    _HAVE_TQDM = False


def _compact_count(value):
    value = float(value)
    for suffix, divisor in (("G", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(value) >= divisor:
            scaled = value / divisor
            return f"{scaled:.0f}{suffix}" if scaled >= 100 else f"{scaled:.1f}{suffix}"
    return f"{value:.0f}"


def _clock(seconds):
    if seconds is None or seconds < 0:
        return "--:--"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


class PlainUI:
    """Stable text/tqdm output for pipes, logs and dependency-light hosts."""

    report_interval_s = 10.0

    def __init__(self, cfg):
        self.cfg = cfg
        self.bar = None
        self._started_at = time.monotonic()
        self._last_report_at = self._started_at
        self._start_n = 0
        self._total = 0

    def log(self, msg):
        print(str(msg), flush=True)

    def note(self, msg):
        if self.bar is not None and _HAVE_TQDM:
            tqdm.write(str(msg))
        else:
            print(str(msg), flush=True)

    def start(self, total, done):
        self._total = int(total)
        self._start_n = int(done)
        self._started_at = self._last_report_at = time.monotonic()
        if _HAVE_TQDM and is_tty():
            self.bar = tqdm(
                total=total,
                initial=done,
                unit="trace",
                desc=str(self.cfg.target).upper(),
                dynamic_ncols=True,
                smoothing=0.08,
                mininterval=0.12,
            )
        else:
            print(f"CAPTURE start target={self.cfg.target} done={done:,} total={total:,}",
                  flush=True)

    def update(self, count, stats):
        if self.bar is not None:
            self.bar.update(count)
            self.bar.set_postfix(
                err=stats.get("fail", 0), rec=stats.get("rec", 0),
                quality=stats.get("quality", 0), trig=stats.get("trig", "-"),
                refresh=False,
            )
            return

        now = time.monotonic()
        done = int(stats.get("done", 0))
        if now - self._last_report_at < self.report_interval_s and done < self._total:
            return
        elapsed = max(now - self._started_at, 1e-9)
        rate = (done - self._start_n) / elapsed
        remaining = max(self._total - done, 0)
        eta = remaining / rate if rate > 0 else None
        print(
            f"CAPTURE {done:,}/{self._total:,} "
            f"({100 * done / max(self._total, 1):.1f}%) "
            f"rate={rate:.1f} trace/s eta={_clock(eta)} "
            f"errors={stats.get('fail', 0)} recoveries={stats.get('rec', 0)} "
            f"quality={stats.get('quality', 0)}",
            flush=True,
        )
        self._last_report_at = now

    def summary(self, data):
        print("\nCAPTURE COMPLETE", flush=True)
        for key, value in data.items():
            print(f"  {key:22} {value}", flush=True)

    def close(self):
        if self.bar is not None:
            self.bar.close()
            self.bar = None


class RichUI:
    """One animated Rich surface with width-aware secondary information."""

    def __init__(self, cfg, console=None):
        self.cfg = cfg
        self.c = console or CONSOLE
        self.prog = None
        self.task = None

    def log(self, msg):
        from rich.rule import Rule
        from rich.text import Text

        message = str(msg).strip()
        if message.startswith("===") and message.endswith("==="):
            self.c.print(Rule(Text(message.strip("= "), style="bold bright_cyan"),
                              style="cyan"))
        else:
            self.c.print(Text(str(msg)))

    @staticmethod
    def _note_style(message):
        lowered = message.lower()
        if "warning" in lowered or "mismatch" in lowered or "too many" in lowered:
            return "yellow", "!"
        if any(word in lowered for word in ("verified", "preflight ok", "applied")):
            return "green", "✓"
        if lowered.startswith("disk:"):
            return "blue", "•"
        return "dim", "•"

    def note(self, msg):
        from rich.text import Text

        message = str(msg)
        style, marker = self._note_style(message)
        line = Text("  ")
        line.append(marker, style=f"bold {style}")
        line.append("  ")
        line.append(message, style=style)
        destination = self.prog.console if self.prog is not None else self.c
        destination.print(line)

    def start(self, total, done):
        from rich.progress import (BarColumn, Progress, ProgressColumn,
                                   SpinnerColumn, TextColumn)
        from rich.text import Text

        console = self.c

        class PercentColumn(ProgressColumn):
            def render(self, task):
                style = "bold green" if task.finished else "bold bright_cyan"
                return Text(f"{task.percentage:5.1f}%", style=style)

        class CountColumn(ProgressColumn):
            def render(self, task):
                if console.width < 96:
                    label = f"{_compact_count(task.completed)}/{_compact_count(task.total or 0)}"
                else:
                    label = f"{int(task.completed):,}/{int(task.total or 0):,}"
                return Text(label, style="bright_white")

        class RateColumn(ProgressColumn):
            def render(self, task):
                if console.width < 68:
                    return Text("")
                return Text(f"{task.speed or 0:,.1f}/s", style="cyan")

        class EtaColumn(ProgressColumn):
            def render(self, task):
                if console.width < 84:
                    return Text("")
                return Text("eta " + _clock(task.time_remaining), style="dim")

        class HealthColumn(ProgressColumn):
            def render(self, task):
                fail = int(task.fields.get("fail", 0))
                rec = int(task.fields.get("rec", 0))
                quality = int(task.fields.get("quality", 0))
                if console.width < 100:
                    if console.width >= 72 and (fail or rec or quality):
                        return Text(f"!{fail} r{rec} q{quality}", style="yellow")
                    return Text("")
                text = Text("err ", style="dim")
                text.append(str(fail), style="red" if fail else "green")
                text.append("  rec ", style="dim")
                text.append(str(rec), style="yellow" if rec else "green")
                text.append("  quality ", style="dim")
                text.append(str(quality), style="yellow" if quality else "green")
                return text

        class TriggerColumn(ProgressColumn):
            def render(self, task):
                if console.width < 126:
                    return Text("")
                return Text(f"trig {task.fields.get('trig', '-')}", style="magenta")

        self.prog = Progress(
            SpinnerColumn(spinner_name="dots12", style="bright_cyan",
                          finished_text=Text("✓", style="bold green")),
            TextColumn("[bold bright_white]CAPTURE[/] [bold magenta]{task.description}[/]"),
            BarColumn(bar_width=None, style="grey37", complete_style="bright_cyan",
                      finished_style="green", pulse_style="magenta"),
            PercentColumn(), CountColumn(), RateColumn(), EtaColumn(),
            HealthColumn(), TriggerColumn(),
            console=self.c, refresh_per_second=8, auto_refresh=True,
            transient=False, expand=True,
        )
        self.prog.start()
        self.task = self.prog.add_task(
            str(self.cfg.target).upper(), total=total, completed=done,
            fail=0, rec=0, quality=0, trig="-",
        )

    def update(self, count, stats):
        if self.prog is None or self.task is None:
            return
        self.prog.update(
            self.task, advance=count, fail=stats.get("fail", 0),
            rec=stats.get("rec", 0), quality=stats.get("quality", 0),
            trig=stats.get("trig", "-"),
        )

    @staticmethod
    def _summary_value(key, value):
        from rich.text import Text

        lowered = key.lower()
        style = "bright_white"
        if lowered in ("target", "valid traces"):
            style = "bold green"
        elif "reject" in lowered or "mismatch" in lowered or "fail" in lowered:
            nonzero = any(ch.isdigit() and ch != "0" for ch in str(value))
            style = "yellow" if nonzero else "green"
        elif lowered == "dataset":
            style = "cyan"
        return Text(str(value), style=style)

    def summary(self, data):
        from rich import box
        from rich.panel import Panel
        from rich.table import Table

        items = list(data.items())
        wide = self.c.width >= 104
        table = Table.grid(expand=True, padding=(0, 2))
        if wide:
            table.add_column(style="dim cyan", ratio=1)
            table.add_column(ratio=2)
            table.add_column(style="dim cyan", ratio=1)
            table.add_column(ratio=2)
            for index in range(0, len(items), 2):
                left = items[index]
                right = items[index + 1] if index + 1 < len(items) else ("", "")
                table.add_row(
                    str(left[0]), self._summary_value(*left), str(right[0]),
                    self._summary_value(*right) if right[0] else "",
                )
        else:
            table.add_column(style="dim cyan", no_wrap=True)
            table.add_column(ratio=1)
            for key, value in items:
                table.add_row(str(key), self._summary_value(key, value))
        self.c.print(Panel(
            table, title="[bold green]✓ CAPTURE COMPLETE[/bold green]",
            title_align="left", border_style="green", box=box.ROUNDED,
            padding=(1, 2), expand=True,
        ))

    def close(self):
        if self.prog is not None:
            self.prog.stop()
            self.prog = None
            self.task = None


def make_ui(cfg, fancy=None, console=None):
    """Use Rich on capable TTYs and plain output for logs or dumb terminals."""
    if fancy is None:
        fancy = interactive_rich()
    return RichUI(cfg, console=console) if (fancy and HAVE_RICH) else PlainUI(cfg)
