#!/usr/bin/env python3
"""acquire — fancy unified PROACT trace-acquisition CLI.

Two ways to run:
  interactive wizard (no target given, or --wizard):
      ./run.sh
  one-shot flags:
      ./run.sh --target aes1    --traces 10000 --key fixed --input random
      ./run.sh --target xoodyak --traces 100000 --trigger 0x11
      ./run.sh --target sw_rv   --traces 1000000            # resumes automatically
      ./run.sh --target aes2 --backend scope --scope-resource TCPIP0::192.168.0.10::INSTR

Re-running the same command RESUMES from the last checkpoint. The board is reset and
the controller program uploaded automatically at bring-up (force with --reset).
"""
import math
import sys
import shutil
import textwrap
import click

from acq.config import (AcqConfig, parse_count, TARGETS, UART_DIVISOR,
                        auto_uart_baud)
from acq.console import CONSOLE, HAVE_RICH, interactive_rich


class _StyledQuestionary:
    """Apply one prompt palette without changing questionary's public API."""

    _PROMPTS = frozenset(("text", "select", "checkbox", "confirm", "password"))

    def __init__(self, module):
        self._module = module
        self._style = None
        style_type = getattr(module, "Style", None)
        if style_type is not None and interactive_rich():
            self._style = style_type([
                ("qmark", "fg:#00d7ff bold"),
                ("question", "bold"),
                ("answer", "fg:#5fd7ff bold"),
                ("pointer", "fg:#ff5fff bold"),
                ("highlighted", "fg:#ff5fff bold"),
                ("selected", "fg:#5fff87"),
                ("separator", "fg:#6c7086"),
                ("instruction", "fg:#8a8fa3"),
                ("text", ""),
                ("disabled", "fg:#6c7086 italic"),
            ])

    def __getattr__(self, name):
        original = getattr(self._module, name)
        if name not in self._PROMPTS or self._style is None:
            return original

        def themed(*args, **kwargs):
            kwargs.setdefault("style", self._style)
            return original(*args, **kwargs)
        return themed


def _wizard_step(number, total, title, detail):
    """Print a stable section boundary before a group of interactive prompts."""
    if interactive_rich():
        from rich.rule import Rule
        from rich.text import Text
        heading = Text()
        heading.append(f"{number:02d}/{total:02d} ", style="bold bright_cyan")
        heading.append(title, style="bold bright_white")
        heading.append(f"  {detail}", style="dim")
        CONSOLE.print(Rule(heading, style="cyan"))
    else:
        click.echo(f"\n[{number}/{total}] {title} — {detail}")


def _run_animated_stage(label, operation):
    """Run a long offline stage with one spinner or ordinary log messages."""
    if not interactive_rich():
        return operation(click.echo)
    from rich.text import Text
    with CONSOLE.status(Text(label, style="bold bright_cyan"), spinner="dots12",
                        spinner_style="bright_cyan", refresh_per_second=8) as status:
        def note(message):
            status.update(Text(str(message), style="bright_white"))
        result = operation(note)
    done = Text("  ✓  ", style="bold green")
    done.append(label, style="green")
    CONSOLE.print(done)
    return result


def _normalise_formats(formats):
    selected = tuple(dict.fromkeys(formats or ("native",)))
    return (("native",) + tuple(f for f in selected if f != "native"))


def _terminal_columns():
    return max(32, shutil.get_terminal_size(fallback=(80, 24)).columns)


def _echo_field(label, value):
    """Print one estimate field without overflowing a narrow terminal."""
    prefix = f"  {label:<14}: "
    available = max(12, _terminal_columns() - len(prefix))
    lines = textwrap.wrap(
        str(value), width=available, break_long_words=False,
        break_on_hyphens=False) or [""]
    click.echo(prefix + lines[0])
    continuation = " " * len(prefix)
    for line in lines[1:]:
        click.echo(continuation + line)


def _echo_wrapped(value, indent="  "):
    available = max(12, _terminal_columns() - len(indent))
    lines = textwrap.wrap(
        str(value), width=available, break_long_words=False,
        break_on_hyphens=False) or [""]
    for line in lines:
        click.echo(indent + line)


def _parse_baud(value):
    return None if str(value).strip().lower() == "auto" else parse_count(value)


def _valid_clock(value):
    try:
        import math
        number = float(value)
        return (math.isfinite(number) and number > 0) or "enter a positive finite MHz value"
    except (TypeError, ValueError, OverflowError):
        return "enter a positive finite MHz value"


def _valid_baud(value, clock_mhz):
    try:
        AcqConfig("aes1", 1, clock_mhz=float(clock_mhz),
                  baud=parse_count(value)).validate()
        return True
    except (AssertionError, TypeError, ValueError, OverflowError) as exc:
        return str(exc) or "enter a positive baud within 2% of the UART wire rate"


def _select_uart_port(q):
    """Choose from metadata-only discovery; an empty result means live auto-detect."""
    from acq import serial_ports
    while True:
        ports = serial_ports.discover_serial_ports()
        suggested = serial_ports.recommended_port(ports)
        choices = []
        for port in ports:
            label = f"{port.device} — {port.description}"
            if port.serial_number:
                label += f" | {port.serial_number[-12:]}"
            if port == suggested:
                label += " (suggested)"
            choices.append({"name": label, "value": port.path})
        choices.extend([
            {"name": "Auto-detect the MCP2200 when acquisition starts", "value": ""},
            {"name": "Rescan USB UARTs", "value": "rescan"},
            {"name": "Enter a port path manually", "value": "manual"},
        ])
        default = suggested.path if suggested else ""
        selected = q.select("PROACT UART port:", choices=choices, default=default).ask()
        if selected is None:
            return None
        if selected == "rescan":
            continue
        if selected == "manual":
            return q.text("UART path (for example /dev/serial/by-id/...):",
                          default="").ask()
        return selected


def _uart_line(cfg):
    source = "auto-scaled" if cfg.baud is None else "explicit"
    return (f"host {cfg.uart_host_baud:,} baud ({source}), wire "
            f"{cfg.uart_actual_baud:,.3f} baud from fixed divisor {UART_DIVISOR}, "
            f"mismatch {cfg.uart_baud_error_percent:.4f}%")


def _export_estimates(cfg, formats, samples_override=None):
    from acq import space
    from acq.exporters import estimate_csv_bytes, estimate_h5_bytes
    samples = (samples_override or cfg.samples or
               space.TYPICAL_SAMPLES.get(cfg.target, 600))
    out_len = 32 if cfg.is_aead else 16
    estimates = {}
    if "h5" in formats:
        estimates["h5"] = estimate_h5_bytes(
            cfg.traces, samples, out_len, key_varies=cfg.key_policy == "random")
    if "csv" in formats:
        estimates["csv"] = estimate_csv_bytes(cfg.traces, samples, out_len)
    return estimates


def _panel(cfg, resume_n, formats):
    if not interactive_rich():
        count = (f"{cfg.tvla_per_class} per TVLA class ({cfg.traces} total)"
                 if cfg.tvla else f"{cfg.traces} traces")
        print(f"\nPROACT ACQUISITION PLAN\nAcquire {cfg.target}: {count}, key={cfg.key_policy}, "
              f"input={cfg.input_policy}, backend={cfg.backend}, "
              f"formats={'+'.join(formats)}")
        print(f"clock={cfg.clock_mhz:g} MHz; {_uart_line(cfg)}")
        if cfg.backend == "scope":
            print("board clock=" + ("Husky HS2 (configured and held open)"
                  if cfg.scope_clock_source == "husky"
                  else "external source (declared, not verified)"))
            print("measurement gain=scope-controlled; vertical range, coupling and impedance "
                  "are preserved and calibration/clipping are validated")
            print(f"scope trigger={cfg.scope_trig_source} at "
                  f"{cfg.scope_trig_level:g} V (applied and read back)")
            print("scope transfer timeout=" +
                  (f"{cfg.scope_transfer_timeout_ms:,} ms"
                   if cfg.scope_transfer_timeout_ms is not None
                   else "automatic from record size"))
        else:
            print(f"measurement gain={cfg.gain_db:g} dB on Husky")
        print(f"UART port={cfg.port or 'auto-detect MCP2200 at start'}")
        analyses = " + ".join(x for x, enabled in
                              (("TVLA", cfg.tvla), ("CPA", cfg.auto_cpa))
                              if enabled) or "none"
        print(f"warm-up={cfg.warmup} discarded operations, analysis={analyses}")
        if "csv" in formats:
            from acq.exporters import human_bytes
            estimate = _export_estimates(cfg, formats)["csv"]
            print(f"WARNING: CSV is wide and slow (estimated {human_bytes(estimate)}); "
                  "prefer HDF5 for large campaigns.")
        if cfg.auto_cpa and cfg.target == "sw_rv_masked":
            print("WARNING: masked-AES CPA is a first-order diagnostic; "
                  "no second-order recovery is run.")
        return
    from rich import box
    from rich.table import Table
    from rich.panel import Panel
    t = Table.grid(padding=(0, 2), expand=True)
    t.add_column(style="dim cyan", justify="right", no_wrap=CONSOLE.width >= 70)
    t.add_column(style="bright_white", ratio=1)
    trace_text = (f"{cfg.tvla_per_class:,} fixed + {cfg.tvla_per_class:,} random "
                  f"= {cfg.traces:,} total" if cfg.tvla else f"{cfg.traces:,}")
    analysis_text = " + ".join(x for x, enabled in
                               (("TVLA", cfg.tvla), ("automatic CPA", cfg.auto_cpa))
                               if enabled) or "none"
    rows = [("target clock", f"[bold cyan]{cfg.clock_mhz:g} MHz[/bold cyan]"),
            ("UART", _uart_line(cfg)),
            ("UART port", cfg.port or "auto-detect MCP2200 at start"),
            ("core", f"[bold magenta]{cfg.target.upper()}[/bold magenta]"),
            ("traces", f"[bold]{trace_text}[/bold]"),
            ("key policy", cfg.key_policy), (f"{cfg.input_name} policy", cfg.input_policy),
            ("backend", cfg.backend), ("save formats", " + ".join(formats)),
            ("warm-up", f"{cfg.warmup:,} discarded operations per session"),
            ("post-capture analysis", analysis_text),
            ("trigger", (f"AEAD cfg 0x{cfg.aead_trigger:02x}" if cfg.is_aead
                         else cfg.trigger_mode))]
    if cfg.backend == "scope":
        rows.insert(1, ("board clock source",
                        "Husky HS2 (configured and held open)"
                        if cfg.scope_clock_source == "husky"
                        else "external (declared, not verified)"))
        rows.insert(8, ("measurement gain",
                        "scope-controlled; preserve range, coupling and impedance; "
                        "validate calibration and clipping"))
        rows.insert(9, ("scope trigger",
                        f"{cfg.scope_trig_source} at {cfg.scope_trig_level:g} V; "
                        "applied and read back"))
        rows.insert(10, ("transfer timeout",
                         (f"{cfg.scope_transfer_timeout_ms:,} ms"
                          if cfg.scope_transfer_timeout_ms is not None
                          else "automatic from record size")))
    else:
        rows.insert(7, ("measurement gain", f"{cfg.gain_db:g} dB on Husky"))
    if cfg.tvla:
        rows.append(("TVLA acquisition order", cfg.tvla_order +
                     (f" ({cfg.tvla_block_size}-row blocks)"
                      if cfg.tvla_order == "block" else " (parity-confounded)")))
    if cfg.auto_cpa and cfg.is_aead:
        rows.append(("AEAD CPA result",
                     "positional hypotheses only; no automatic full-key decoder"))
    if cfg.auto_cpa and cfg.target == "sw_rv_masked":
        rows.append(("masked AES CPA",
                     "first-order diagnostic only; no second-order recovery"))
    if resume_n:
        rows.append(("resume from", f"{resume_n:,} ({100*resume_n/cfg.traces:.0f}%)"))
    from acq import space
    e = space.estimate(cfg, samples=(cfg.samples or None))
    rows.append(("est. size", f"{e['total_h']}"
                 + (f" (need {e['need_h']} more)" if e['already_b'] else "")))
    export_estimates = _export_estimates(cfg, formats)
    if export_estimates:
        from acq.exporters import human_bytes
        rows.append(("additional exports", ", ".join(
            f"{name} ~{human_bytes(size)}" for name, size in export_estimates.items())))
    fits = e["need_b"] + sum(export_estimates.values()) < e["free_b"] * 0.98
    disk_style = "green" if fits else "red"
    rows.append(("free disk", f"[{disk_style}]{e['free_h']}[/{disk_style}]"))
    for k, v in rows:
        t.add_row(k, str(v))
    CONSOLE.print(Panel(
        t,
        title="[bold bright_cyan] PROACT [/bold bright_cyan] [bold]ACQUISITION PLAN[/bold]",
        subtitle="[dim]review settings before instruments are opened[/dim]",
        title_align="left",
        border_style="bright_cyan",
        box=box.ROUNDED,
        padding=(1, 2),
        expand=True,
    ))
    if not fits:
        CONSOLE.print("[bold red]  warning: capture plus selected exports may exceed free disk")
    if "csv" in formats:
        CONSOLE.print("[bold yellow]  CSV warning: wide and slow; use HDF5 for large campaigns.")
    if cfg.tvla and cfg.tvla_order == "alternate":
        CONSOLE.print("[bold yellow]  TVLA warning: strict alternation aliases the known "
                      "odd/even acquisition artefact; balanced blocks are recommended.")
    if cfg.auto_cpa and cfg.target == "sw_rv_masked":
        CONSOLE.print("[bold yellow]  Masked-AES CPA is a first-order diagnostic. "
                      "Masking may suppress it; no second-order attack is run.")


def wizard(clock_mhz_default=50.0):
    """Interactive selection. Returns an AcqConfig or None if cancelled."""
    import questionary
    q = _StyledQuestionary(questionary)
    _wizard_step(1, 4, "CLOCK & LINK", "set frequency, UART rate and board port")
    clock = q.text("Target clock frequency (MHz):", default=f"{clock_mhz_default:g}",
                   validate=_valid_clock).ask()
    if clock is None:
        return None
    clock_mhz = float(clock)
    suggested_baud = auto_uart_baud(clock_mhz)
    baud_mode = q.select(
        "Host UART speed:",
        choices=[
            {"name": f"Auto — {suggested_baud:,} baud (scaled from proven 50 MHz setup)",
             "value": "auto"},
            {"name": "Enter an explicit host baud", "value": "custom"},
        ], default="auto").ask()
    if baud_mode is None:
        return None
    baud = None
    if baud_mode == "custom":
        raw_baud = q.text("Host UART baud:", default=str(suggested_baud),
                          validate=lambda value: _valid_baud(value, clock_mhz)).ask()
        if raw_baud is None:
            return None
        baud = parse_count(raw_baud)
    uart_preview = AcqConfig("aes1", 1, clock_mhz=clock_mhz, baud=baud).validate()
    click.echo("UART: " + _uart_line(uart_preview))
    port = _select_uart_port(q)
    if port is None:
        return None
    _wizard_step(2, 4, "WORKLOAD", "choose the core, analysis and campaign size")
    target = q.select("Core to attack:", choices=list(TARGETS)).ask()
    if target is None:
        return None
    is_aead = target in ("xoodyak", "ascon")
    analyses = q.checkbox(
        "Post-capture analysis:",
        choices=[
            {"name": "Automatic CPA (model selected for this core)", "value": "cpa"},
            {"name": "First-order TVLA (fixed vs random)", "value": "tvla"},
        ]).ask()
    if analyses is None:
        return None
    tvla = "tvla" in analyses
    auto_cpa = "cpa" in analyses
    if tvla or auto_cpa:
        key, inp = "fixed", "random"
    else:
        key = q.select("Key policy:", choices=["fixed", "random"], default="fixed").ask()
        if key is None:
            return None
        inname = "nonce" if is_aead else "plaintext"
        inp = q.select(f"{inname.capitalize()} policy:",
                       choices=["random", "fixed"], default="random").ask()
        if inp is None:
            return None
    count_prompt = ("How many traces PER TVLA CLASS? (total capture is twice this)"
                    if tvla else "How many traces?")
    traces = q.text(count_prompt, default="10000",
                    validate=lambda s: _valid_count(s)).ask()
    if traces is None:
        return None
    _wizard_step(3, 4, "MEASUREMENT", "configure backend, warm-up and trigger")
    backend = q.select("Measurement backend:",
                       choices=["husky (connected)", "scope (oscilloscope)"]).ask()
    if backend is None:
        return None
    backend = "scope" if backend.startswith("scope") else "husky"
    requested = parse_count(traces)
    cfg = AcqConfig(target=target, traces=(2 * requested if tvla else requested),
                    key_policy=key, input_policy=inp, backend=backend,
                    clock_mhz=clock_mhz, baud=baud, port=port,
                    auto_cpa=auto_cpa, tvla=tvla,
                    tvla_per_class=(requested if tvla else 0))
    if tvla:
        order = q.select(
            "TVLA trace order:",
            choices=[
                {"name": "Balanced shuffled blocks (recommended)", "value": "block"},
                {"name": "Strict fixed, random, fixed, random (parity-confounded)",
                 "value": "alternate"},
            ], default="block").ask()
        if order is None:
            return None
        cfg.tvla_order = order
    wants_warmup = q.confirm("Warm up the core before recording each session?",
                             default=False).ask()
    if wants_warmup is None:
        return None
    if wants_warmup:
        warmup = q.text("How many successful warm-up operations to discard?",
                        default="1000", validate=lambda s: _valid_count(s)).ask()
        if warmup is None:
            return None
        cfg.warmup = parse_count(warmup)
    if is_aead:
        trg = q.select("AEAD trigger window (in-core triggercfg):",
                       choices=["0x12 default (nonce->done)",
                                "0x11 narrow (key->nonce, ~10cy)",
                                "0x23 (nonce->s..p, ~21cy)", "0x00 (key->done)"]).ask()
        if trg is None:
            return None
        cfg.aead_trigger = int(trg.split()[0], 0)
    else:
        mode = q.select(
            "Trigger source:",
            choices=[
                {"name": "Auto — controller follows the selected core (recommended)",
                 "value": "auto"},
                {"name": "Core — explicitly pin the mux to this core", "value": "core"},
                {"name": "Firmware — control-register trigger (current firmware does not pulse it)",
                 "value": "firmware"},
            ], default="auto").ask()
        if mode is None:
            return None
        cfg.trigger_mode = mode
    if backend == "scope":
        board_clock = q.select(
            "Board clock during oscilloscope capture:",
            choices=[
                {"name": "Husky HS2 — configure and hold the requested clock (recommended)",
                 "value": "husky"},
                {"name": "External source — already connected and set to this frequency",
                 "value": "external"},
            ], default="husky").ask()
        if board_clock is None:
            return None
        cfg.scope_clock_source = board_clock
        res = q.text("VISA resource (blank = auto-detect):", default="").ask()
        if res is None:
            return None
        cfg.scope_resource = res or ""
        dialect = q.select(
            "Oscilloscope SCPI family:",
            choices=[
                {"name": "Auto-detect from *IDN? (recommended)", "value": "auto"},
                {"name": "Keysight / Agilent / compatible", "value": "keysight"},
                {"name": "Tektronix", "value": "tek"},
            ], default="auto").ask()
        if dialect is None:
            return None
        cfg.scope_dialect = dialect
        channel = q.text(
            "Oscilloscope measurement channel (1-8):", default="1",
            validate=lambda value: (
                str(value).isdigit() and 1 <= int(value) <= 8) or
                "enter a channel from 1 to 8").ask()
        if channel is None:
            return None
        cfg.scope_channel = int(channel)
        trigger_source = q.select(
            "Oscilloscope trigger input:",
            choices=[
                {"name": "External trigger input", "value": "EXTernal"},
                {"name": "Analog channel 2", "value": "CHANnel2"},
                {"name": "Tektronix auxiliary input", "value": "AUX"},
            ], default="EXTernal").ask()
        if trigger_source is None:
            return None
        cfg.scope_trig_source = trigger_source
        trigger_level = q.text(
            "Oscilloscope trigger level (V):", default="1.5",
            validate=_valid_finite_float).ask()
        if trigger_level is None:
            return None
        cfg.scope_trig_level = float(trigger_level)
    _wizard_step(4, 4, "OUTPUT", "choose portable copies; native resume stays enabled")
    selected = q.checkbox(
        "Additional save formats (native resumable files are always kept):",
        choices=[
            {"name": "HDF5 (.h5, chunked and compressed)", "value": "h5"},
            {"name": "CSV (.csv, wide and slow for large captures)", "value": "csv"},
        ]).ask()
    if selected is None:
        return None
    return cfg, _normalise_formats(selected)


def _valid_count(s):
    try:
        return parse_count(s) > 0 or "must be > 0"
    except Exception:
        return "enter a number like 10000, 100k, 5M"


def _valid_finite_float(value):
    try:
        return math.isfinite(float(value)) or "enter a finite voltage"
    except (TypeError, ValueError, OverflowError):
        return "enter a finite voltage"


@click.command(context_settings=dict(help_option_names=["-h", "--help"]))
@click.option("--clock-mhz", type=float, default=50.0, show_default=True,
              help="target clock; Husky applies it unless scope clock source is external")
@click.option("--baud", default="auto", show_default=True,
              help="host UART baud; auto scales the proven 115200-at-50-MHz setting")
@click.option("--port", default="",
              help="PROACT UART path; blank auto-detects the MCP2200")
@click.option("--target", type=click.Choice(TARGETS), default=None)
@click.option("--traces", default=None, help="e.g. 10000 / 100k / 5M")
@click.option("--key", type=click.Choice(["fixed", "random"]), default="fixed")
@click.option("--input", "input_", type=click.Choice(["fixed", "random"]), default="random")
@click.option("--trigger", default=None, help="AES/Sw-RV: auto|core|firmware ; AEAD: hex e.g. 0x11")
@click.option(
    "--samples", type=int, default=0,
    help=("record length; 0 uses trigger-based auto-size on Husky and a "
          "2,000-sample pilot record on a scope"))
@click.option("--offset", type=int, default=0,
              help="Husky ADC sample offset; nonzero scope offset is refused")
@click.option("--gain", type=float, default=25.0, show_default=True,
              help="Husky gain in dB; scope vertical settings remain instrument-controlled")
@click.option("--suffix", default="")
@click.option("--chunk", type=int, default=20000)
@click.option("--seed", type=int, default=0,
              help="reproducible input schedule seed; masked RNG reseeds stay fresh")
@click.option("--warmup", default="0",
              help="successful core operations discarded before each capture session")
@click.option("--tvla", is_flag=True,
              help="capture --traces rows PER class, then run first-order Welch TVLA")
@click.option("--tvla-order", type=click.Choice(["block", "alternate"]), default="block",
              help="balanced shuffled blocks (recommended) or strict F,R alternation")
@click.option("--tvla-block-size", type=int, default=100,
              help="even block size used by --tvla-order block")
@click.option("--auto-cpa", is_flag=True,
              help="run the target-specific CPA model after a completed capture")
@click.option("--backend", type=click.Choice(["husky", "scope"]), default="husky")
@click.option("--scope-resource", default=None, help="VISA resource string for the scope backend")
@click.option("--scope-clock-source", type=click.Choice(["husky", "external"]),
              default="husky", show_default=True,
              help="board clock for scope capture: Husky HS2 or an existing external source")
@click.option("--scope-dialect", type=click.Choice(["auto", "keysight", "tek"]),
              default="auto", show_default=True,
              help="oscilloscope SCPI family; auto uses *IDN?")
@click.option("--scope-channel", type=click.IntRange(1, 8), default=1,
              show_default=True, help="oscilloscope analog measurement channel")
@click.option("--scope-trigger-source", default="EXTernal", show_default=True,
              help="oscilloscope trigger input, for example EXTernal or CHANnel2")
@click.option("--scope-trigger-level", type=float, default=1.5, show_default=True,
              help="oscilloscope trigger threshold in volts; applied and read back")
@click.option("--scope-transfer-timeout-ms", type=click.IntRange(min=1), default=None,
              help="binary waveform timeout in ms; default scales with record size")
@click.option("--format", "formats", type=click.Choice(["native", "h5", "csv"]),
              multiple=True, help="save format; repeat for both HDF5 and CSV (native is always kept)")
@click.option("--reset", is_flag=True, help="force controller reprogram before starting")
@click.option("--allow-nofit", is_flag=True, help="proceed even if the estimate exceeds free disk")
@click.option("--estimate", "estimate_only", is_flag=True, help="show disk-space estimate and exit")
@click.option("--wizard", "force_wizard", is_flag=True, help="force interactive wizard")
@click.option("-y", "--yes", is_flag=True, help="skip the confirmation prompt")
def main(clock_mhz, baud, port, target, traces, key, input_, trigger, samples, offset, gain, suffix, chunk,
         seed, warmup, tvla, tvla_order, tvla_block_size, auto_cpa, backend,
         scope_resource, scope_clock_source, scope_dialect, scope_channel,
         scope_trigger_source, scope_trigger_level, scope_transfer_timeout_ms,
         formats, reset, allow_nofit, estimate_only, force_wizard, yes):
    # interactive when asked, or when no target/traces given on a tty
    interactive = force_wizard or (target is None and traces is None and sys.stdin.isatty())
    if interactive:
        answer = wizard(clock_mhz_default=clock_mhz)
        if answer is None:
            click.echo("cancelled."); return
        cfg, selected_formats = answer
    else:
        if not target or not traces:
            raise click.UsageError("give --target and --traces (or run with no args for the wizard)")
        if tvla and key != "fixed":
            raise click.UsageError("--tvla requires --key fixed")
        if tvla and input_ != "random":
            raise click.UsageError("--tvla controls fixed/random inputs; use --input random")
        if auto_cpa and key != "fixed":
            raise click.UsageError("--auto-cpa requires --key fixed")
        if auto_cpa and not tvla and input_ != "random":
            raise click.UsageError("--auto-cpa requires --input random")
        if chunk <= 0:
            raise click.UsageError("--chunk must be greater than zero")
        if backend == "scope" and offset != 0:
            raise click.UsageError(
                "nonzero --offset is not yet applied/query-verified by the scope backend")
        try:
            requested = parse_count(traces)
            requested_warmup = parse_count(warmup)
            requested_baud = _parse_baud(baud)
        except (TypeError, ValueError, OverflowError) as exc:
            raise click.UsageError(str(exc)) from exc
        cfg = AcqConfig(target=target,
                        traces=(2 * requested if tvla else requested), key_policy=key,
                        input_policy=input_, gain_db=gain, samples=samples, offset=offset,
                        suffix=suffix, chunk=chunk, seed=seed, backend=backend,
                        clock_mhz=clock_mhz, baud=requested_baud, port=port,
                        scope_clock_source=scope_clock_source,
                        scope_dialect=scope_dialect, scope_channel=scope_channel,
                        scope_trig_source=scope_trigger_source,
                        scope_trig_level=scope_trigger_level,
                        scope_transfer_timeout_ms=scope_transfer_timeout_ms,
                        warmup=requested_warmup, tvla=tvla,
                        tvla_per_class=(requested if tvla else 0),
                        tvla_order=tvla_order, tvla_block_size=tvla_block_size,
                        auto_cpa=auto_cpa)
        if trigger is not None:
            if cfg.is_aead:
                try:
                    cfg.aead_trigger = int(trigger, 0)
                except (TypeError, ValueError, OverflowError) as exc:
                    raise click.UsageError(
                        "AEAD --trigger must be a 7-bit number such as 0x12") from exc
            else:
                cfg.trigger_mode = trigger
        if scope_resource:
            cfg.scope_resource = scope_resource
        selected_formats = _normalise_formats(formats)
    cfg.force_reset = reset
    cfg.allow_nofit = allow_nofit
    try:
        cfg.validate()
    except (AssertionError, TypeError, ValueError, OverflowError) as exc:
        raise click.UsageError(str(exc) or "invalid acquisition configuration") from exc
    requested_tvla_analysis = bool(cfg.tvla)
    requested_cpa_analysis = bool(cfg.auto_cpa)
    capture_is_tvla = bool(cfg.tvla)
    completed_native = False
    stored_samples = None

    # Resolve a stored random seed and reject v2 capture-identity drift before any
    # instrument is opened. A default --seed 0 means "reuse the recorded seed".
    import os, json, numpy as np
    resume_n = 0
    mp = cfg.base + "_meta.npz"
    if os.path.exists(mp):
        try:
            with np.load(mp, allow_pickle=True) as existing:
                resume_n = int(existing["done"])
                stored_seed = int(existing["seed"])
                if not cfg.seed:
                    cfg.seed = stored_seed
                stored_n = int(existing["n_alloc"])
                if stored_n != cfg.traces:
                    raise click.ClickException(
                        "existing dataset trace allocation differs from this request: "
                        f"stored {stored_n:,}, requested {cfg.traces:,}. Use its original "
                        "--traces value or choose a new --suffix.")
                if "capture_signature_json" in existing.files:
                    from acq.native import normalized_signature, signature_json
                    out_len = int(existing["out_len"])
                    actual_samples = int(existing["samples"])
                    have = str(existing["capture_signature_json"].item())
                    want = signature_json(cfg, actual_samples, out_len)
                    if normalized_signature(have) != normalized_signature(want):
                        have_d = normalized_signature(have)
                        want_d = normalized_signature(want)
                        changed = [k for k in sorted(set(have_d) | set(want_d))
                                   if have_d.get(k) != want_d.get(k)]
                        raise click.ClickException(
                            "resume settings differ from this dataset: " +
                            ", ".join(changed) + ". Use its original settings or "
                            "choose a new --suffix.")
        except click.ClickException:
            raise
        except Exception as exc:
            raise click.ClickException(f"cannot validate existing native metadata: {exc}")

    if resume_n == cfg.traces and resume_n > 0:
        from acq.native import open_native
        try:
            with open_native(cfg.base) as completed:
                if completed.done != cfg.traces:
                    raise ValueError(
                        f"done changed while finalizing: {completed.done}/{cfg.traces}")
                recorded_target = str(completed["target"].item())
                if recorded_target != cfg.target:
                    raise ValueError(
                        f"recorded target {recorded_target!r} != {cfg.target!r}")
                stored_samples = completed.samples
                capture_is_tvla = (bool(completed["tvla"])
                                   if "tvla" in completed.files else False)
                completed_native = True
        except Exception as exc:
            raise click.ClickException(
                f"cannot finalize invalid native dataset: {exc}")

    if (requested_cpa_analysis and cfg.is_aead and not completed_native and
            (cfg.backend != "husky" or int(cfg.aead_trigger) != 0x12)):
        raise click.UsageError(
            "new AEAD --auto-cpa captures require Husky triggercfg 0x12; "
            "capture other trigger modes without --auto-cpa")

    # --estimate: disk report only, no capture
    if estimate_only:
        from acq import space
        from acq.exporters import human_bytes
        e = space.estimate(cfg, samples=(stored_samples or cfg.samples or None))
        click.echo(f"\nDisk estimate for {cfg.target} x {cfg.traces:,} traces:")
        if cfg.tvla:
            _echo_field(
                "TVLA protocol", f"{cfg.tvla_per_class:,} fixed + "
                f"{cfg.tvla_per_class:,} random; order={cfg.tvla_order}")
        analyses = "+".join(
            name for name, enabled in (("TVLA", cfg.tvla),
                                        ("CPA", cfg.auto_cpa)) if enabled)
        _echo_field("analysis", analyses or "none")
        _echo_field("warm-up", f"{cfg.warmup:,} discarded operations/session")
        _echo_field("save formats", " + ".join(selected_formats))
        _echo_field("target clock", f"{cfg.clock_mhz:g} MHz")
        if cfg.backend == "scope":
            clock_text = ("Husky HS2 (will be configured and held open)"
                          if cfg.scope_clock_source == "husky" else
                          "external (declared; connect and verify it yourself)")
            _echo_field("board clock", clock_text)
            _echo_field(
                "scope trigger", f"{cfg.scope_trig_source} at "
                f"{cfg.scope_trig_level:g} V (applied and read back)")
            _echo_field(
                "transfer wait", f"{cfg.scope_transfer_timeout_ms:,} ms"
                if cfg.scope_transfer_timeout_ms is not None
                else "automatic from record size")
            _echo_field(
                "gain/range", "scope-controlled; settings preserved")
            _echo_field("scope check", "calibration/clipping validated")
        else:
            _echo_field("gain", f"{cfg.gain_db:g} dB on Husky")
        _echo_field("UART", _uart_line(cfg))
        _echo_field("UART port", cfg.port or "auto-detect MCP2200 at start")
        for ln in space.report_lines(e):
            _echo_wrapped(ln)
        for name, size in _export_estimates(
                cfg, selected_formats, samples_override=stored_samples).items():
            _echo_field(f"additional {name}", f"about {human_bytes(size)}")
        if "csv" in selected_formats:
            _echo_wrapped(
                "WARNING: CSV is wide and slow; HDF5 is recommended for large campaigns.")
        return

    # Include optional completed-copy formats in the pre-instrument fit gate. The
    # capture driver performs a second native-only check after exact auto-sizing.
    from acq import space as _space
    preliminary = _space.estimate(
        cfg, samples=(stored_samples or cfg.samples or None),
        out_len=(32 if cfg.is_aead else 16))
    export_need = sum(_export_estimates(
        cfg, selected_formats, samples_override=stored_samples).values())
    if (preliminary["need_b"] + export_need >= preliminary["free_b"] * 0.98
            and not cfg.allow_nofit):
        raise click.ClickException(
            "native capture plus selected exports may not fit on this volume; "
            "lower --traces/remove CSV or use --allow-nofit after checking space")

    # Fail before opening any instrument when a selected export dependency is absent.
    from acq.exporters import ensure_dependencies
    try:
        ensure_dependencies(selected_formats)
    except RuntimeError as exc:
        raise click.ClickException(str(exc))
    if cfg.auto_cpa:
        from acq.analysis import ensure_dependencies as ensure_analysis_dependencies
        try:
            ensure_analysis_dependencies(cfg)
        except RuntimeError as exc:
            raise click.ClickException(str(exc))

    # resume peek + summary + confirm
    _panel(cfg, resume_n, selected_formats)
    if not yes and sys.stdin.isatty() and HAVE_RICH:
        import questionary
        if not questionary.confirm("Start acquisition?", default=True).ask():
            click.echo("cancelled."); return

    if completed_native:
        click.echo(
            f"Native capture is already complete ({resume_n:,}/{cfg.traces:,}); "
            "finalizing offline without opening the board or instrument.")
        result = {"done": resume_n, "target": cfg.target}
    else:
        from acq.run import run
        from acq.validate import ValidationError
        try:
            result = run(cfg, selected_formats=selected_formats)
        except KeyboardInterrupt as exc:
            if getattr(exc, "proact_checkpoint_succeeded", False):
                message = (
                    "acquisition interrupted; progress checkpointed; "
                    "re-run the same command to resume")
            elif hasattr(exc, "proact_checkpoint_succeeded"):
                detail = getattr(exc, "proact_checkpoint_error", None)
                suffix = f": {detail}" if detail else ""
                message = (
                    "acquisition interrupted; emergency checkpoint failed" + suffix +
                    ". The previous on-disk checkpoint remains authoritative")
            else:
                message = (
                    "acquisition interrupted before checkpoint status could be confirmed. "
                    "The existing on-disk checkpoint remains authoritative")
            raise click.ClickException(message) from exc
        except ValidationError as exc:
            raise click.ClickException(f"preflight refused acquisition: {exc}") from exc
        except Exception as exc:
            raise click.ClickException(f"acquisition failed: {exc}") from exc

    analysis_error = None
    if (cfg.tvla or cfg.auto_cpa) and result["done"] == cfg.traces:
        from acq.analysis import run_requested
        try:
            analysis_outputs = _run_animated_stage(
                "Running requested analysis",
                lambda note: run_requested(
                    cfg, note=note,
                    run_tvla_flag=requested_tvla_analysis,
                    run_cpa_flag=requested_cpa_analysis,
                    capture_is_tvla=capture_is_tvla))
        except Exception as exc:
            analysis_error = exc
            click.echo(
                f"Analysis failed; native capture is complete and safe: {exc}",
                err=True)
            analysis_outputs = []
        if analysis_outputs:
            click.echo("Analysis outputs: " + ", ".join(analysis_outputs))
    elif cfg.tvla or cfg.auto_cpa:
        click.echo("Post-capture analysis deferred until the native capture is complete.")

    additional = tuple(f for f in selected_formats if f != "native")
    if additional and result["done"] == cfg.traces:
        from acq.exporters import export_selected
        try:
            outputs = _run_animated_stage(
                "Writing portable exports",
                lambda note: export_selected(cfg.base, additional, note=note))
        except Exception as exc:
            raise click.ClickException(
                f"native capture is complete and safe, but export failed: {exc}")
        click.echo("Exported: " + ", ".join(outputs))
    elif additional:
        click.echo("Additional exports deferred until the native capture is complete.")

    # Requested format exports are independent of analysis.  Report an analysis
    # failure only after attempting them, so a bad model cannot suppress H5/CSV.
    if analysis_error is not None:
        raise click.ClickException(f"post-capture analysis failed: {analysis_error}")


if __name__ == "__main__":
    main()
