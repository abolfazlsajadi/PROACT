"""Progress based on actual saved counts; synthetic demos identify themselves."""
from __future__ import annotations

import time
from . import console


class PlainUI:
    def __init__(self, cfg):
        self.cfg = cfg
        self._started = time.monotonic()
        self._last_emit = None
        self._initial = self._done = self._total = 0
        self._stats = {}

    def log(self, msg):
        print(str(msg), flush=True)

    note = log

    def start(self, total, done):
        self._total, self._initial, self._done = total, done, done
        self._started = time.monotonic()
        self._last_emit = None

    def update(self, n, stats):
        self._done = int(stats.get("done", self._done + n))
        self._stats.update(stats)
        now = time.monotonic()
        if self._last_emit is not None and now - self._last_emit < 10 and self._done < self._total:
            return
        elapsed = max(now - self._started, 0)
        completed_here = max(self._done - self._initial, 0)
        rate = completed_here / elapsed if elapsed > 0 else 0
        eta = max(self._total - self._done, 0) / rate if rate > 0 else None
        eta_text = f"{eta:.1f}s" if eta is not None else "unknown"
        self.log(
            f"{self._done:,}/{self._total:,} records "
            f"elapsed={elapsed:.1f}s ETA={eta_text} rate={rate:.1f}/s "
            f"fail={self._stats.get('fail', 0)} retry={self._stats.get('retry', 0)} "
            f"checkpoint={self._stats.get('checkpoint', 0):,}")
        self._last_emit = now

    def summary(self, values):
        self.log("\nRun summary")
        for key, value in values.items():
            self.log(f"  {key}: {value}")

    def close(self):
        pass


class RichUI:
    def __init__(self, cfg):
        self.cfg = cfg
        self.c = console.CONSOLE
        self.prog = self.task = None
        self.live = None

    def log(self, msg):
        self.c.print(str(msg), markup=False, highlight=False)

    note = log

    def start(self, total, done):
        from rich.console import Group
        from rich.live import Live
        from rich.progress import (Progress, BarColumn, TextColumn,
                                   MofNCompleteColumn, SpinnerColumn,
                                   TaskProgressColumn)
        from rich.text import Text

        owner = self

        class Status:
            def __rich_console__(self, rich_console, options):
                task = owner.prog.tasks[0]
                elapsed = task.elapsed or 0
                remaining = task.time_remaining
                eta = f"{remaining:.1f}s" if remaining is not None else "unknown"
                rate = f"{task.speed:.1f}/s" if task.speed is not None else "unknown"
                yield Text(f"elapsed={elapsed:.1f}s  ETA={eta}  rate={rate}", overflow="fold")
                yield Text(f"fail={task.fields['fail']}  retry={task.fields['retry']}  "
                           f"checkpoint={task.fields['checkpoint']:,}", overflow="fold")

        self.prog = Progress(
            SpinnerColumn(), TextColumn("{task.description}"),
            BarColumn(), MofNCompleteColumn(), TaskProgressColumn(),
            console=self.c, auto_refresh=False)
        self.task = self.prog.add_task(
            self.cfg.target, total=total, completed=done,
            fail=0, retry=0, checkpoint=done)
        self.live = Live(Group(self.prog, Status()), console=self.c,
                         refresh_per_second=8, transient=False)
        self.live.start(refresh=True)

    def update(self, n, stats):
        if self.prog is None:
            return
        fields = {key: stats[key] for key in ("fail", "retry", "checkpoint") if key in stats}
        if "done" in stats:
            self.prog.update(self.task, completed=stats["done"], **fields)
        else:
            self.prog.update(self.task, advance=n, **fields)
        if self.live is not None:
            self.live.refresh()

    def summary(self, values):
        from rich.table import Table
        from rich.panel import Panel
        from rich.text import Text
        table = Table.grid(padding=(0, 2))
        table.add_column(style="dim")
        table.add_column()
        for key, value in values.items():
            table.add_row(Text(str(key)), Text(str(value)))
        self.c.print(Panel(table, title="Run summary", expand=False))

    def close(self):
        if self.live is not None:
            self.live.stop()
            self.live = None
        self.prog = None


def make_ui(cfg, fancy=None, no_color=False):
    if no_color:
        console.configure_console(plain=console.PLAIN, no_color=True)
    use_rich = console.HAVE_RICH and console.is_tty() and not console.PLAIN
    if fancy is False:
        use_rich = False
    return RichUI(cfg) if use_rich else PlainUI(cfg)
