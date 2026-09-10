#!/usr/bin/env python3
"""Offline PROACT acquisition configuration, review and synthetic progress demo.

This public entry point never imports capture drivers or opens instruments.
Live integration is pending; the original acquisition package is unchanged.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import click
from acq.config import AcqConfig, TARGETS, parse_count
from acq import console

STATUS = "Offline configuration; live integration pending."


def configuration_document(cfg):
    return {
        "schema": "proact-acquisition-config-v1",
        "mode": "offline-configuration",
        "live_integration": "pending",
        "requested": cfg.to_meta(),
        "calculated": {
            "declared_firmware_uart_baud": cfg.uart_actual_baud,
            "host_uart_baud": cfg.uart_host_baud,
            "uart_mismatch_percent": cfg.uart_baud_error_percent,
            "requested_adc_samples_per_second": cfg.clock_mhz * 1e6 * cfg.adc_mul,
        },
        "observed_instrument": None,
    }


def _panel(cfg):
    rows = [
        ("Status", STATUS),
        ("Requested target clock", f"{cfg.clock_mhz:g} MHz from {cfg.clock_source}"),
        ("Declared firmware divisor", str(cfg.firmware_uart_divisor)),
        ("Calculated firmware UART", f"{cfg.uart_actual_baud:.6f} baud (not measured)"),
        ("Requested host UART", f"{cfg.uart_host_baud} baud "
         f"({'auto' if cfg.baud is None else 'explicit'}; {cfg.uart_baud_error_percent:.6f}% mismatch)"),
        ("UART resource", cfg.port or "not supplied"),
        ("Target / records", f"{cfg.target} / {cfg.traces:,}"),
        ("Input policies", f"key={cfg.key_policy}, {cfg.input_name}={cfg.input_policy}"),
        ("Measurement backend", cfg.backend),
        ("ADC multiplier request", str(cfg.adc_mul)),
        ("Sample-count request", str(cfg.samples) if cfg.samples else "auto request; unresolved offline"),
        ("Offset request", str(cfg.offset)),
        ("Gain request", f"{cfg.gain_db:g} dB (Husky only; unused for scope)"),
        ("Trigger request", f"raw AEAD 0x{cfg.aead_trigger:02x}" if cfg.is_aead else cfg.trigger_mode),
        ("Observed instrument settings", "none — no instrument accessed"),
    ]
    if cfg.backend == "scope":
        rows.extend([
            ("VISA resource", cfg.scope_resource or "not supplied"),
            ("Expected scope model", cfg.scope_model or "not supplied; exact model pending"),
            ("Requested SCPI dialect", cfg.scope_dialect),
            ("Scope channel / trigger", f"CH{cfg.scope_channel} / "
             f"{cfg.scope_trig_source} at {cfg.scope_trig_level:g} V"),
        ])
    if console.HAVE_RICH and console.is_tty() and not console.PLAIN:
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
        table = Table.grid(padding=(0, 2))
        table.add_column(style="cyan")
        table.add_column()
        for name, value in rows:
            table.add_row(Text(name), Text(value))
        console.CONSOLE.print(Panel(table, title="PROACT configuration review", expand=False))
    else:
        for name, value in rows:
            click.echo(f"{name}: {value}")


def _valid_count(value):
    try:
        parse_count(value)
        return True
    except ValueError as exc:
        return str(exc)


def _positive_float(value):
    try:
        import math
        number = float(value)
        return (math.isfinite(number) and number > 0) or "enter a positive finite number"
    except (TypeError, ValueError):
        return "enter a positive finite number"


def _baud(value):
    if str(value).strip().lower() == "auto":
        return None
    return parse_count(value)


def _nonnegative_integer(value):
    return (value.isascii() and value.isdecimal()) or "enter a whole number >= 0"


def _finite_float(value):
    try:
        import math
        return math.isfinite(float(value)) or "enter a finite number"
    except (ValueError, TypeError):
        return "enter a finite number"


def wizard(clock_mhz=50.0):
    """Frequency is first. All answers are requests; no instrument is queried."""
    try:
        import questionary as q
    except ImportError as exc:
        raise click.ClickException("wizard needs questionary; use command-line options instead") from exc

    def ask(prompt):
        result = prompt.ask()
        if result is None:
            raise click.Abort()
        return result

    frequency = ask(q.text("Requested target clock (MHz):", default=f"{clock_mhz:g}",
                           validate=_positive_float))
    click.echo("Clock and UART rates below are calculated requests, not measurements.")
    divisor = ask(q.text("Existing firmware UART divisor (declaration only):",
                         default="27", validate=_valid_count))
    host_baud = ask(q.text("Host UART baud (auto or an explicit integer):", default="auto",
                           validate=lambda s: True if s.strip().lower() == "auto" else _valid_count(s)))
    port = ask(q.text("UART resource (for the saved configuration; blank = unresolved):", default=""))
    backend = ask(q.select("Measurement backend to configure:", choices=["husky", "scope"]))
    clock_source = ask(q.select("Declared board clock source:",
                               choices=["husky", "external"] if backend == "husky" else ["external"]))
    target = ask(q.select("Target:", choices=list(TARGETS)))
    key = ask(q.select("Key policy:", choices=["fixed", "random"]))
    input_policy = ask(q.select("Input policy:", choices=["random", "fixed"]))
    records = ask(q.text("Requested record count:", default="10000", validate=_valid_count))
    cfg = AcqConfig(target=target, traces=parse_count(records), clock_mhz=float(frequency),
                    firmware_uart_divisor=parse_count(divisor), baud=_baud(host_baud),
                    port=port, backend=backend, clock_source=clock_source,
                    key_policy=key, input_policy=input_policy)
    cfg.adc_mul = parse_count(ask(q.text("Requested ADC multiplier:", default="4", validate=_valid_count)))
    cfg.samples = int(ask(q.text("Requested samples per record (0 = unresolved auto request):",
                                 default="0", validate=_nonnegative_integer)))
    cfg.offset = int(ask(q.text("Requested sample offset:", default="0", validate=_nonnegative_integer)))
    if backend == "husky":
        cfg.gain_db = float(ask(q.text("Requested Husky gain (dB):", default="25", validate=_finite_float)))
    else:
        click.echo("Husky gain does not apply to the scope configuration.")
    if cfg.is_aead:
        raw = ask(q.text("Raw 7-bit AEAD trigger field from your verified setup:", default="0x12"))
        cfg.aead_trigger = int(raw, 0)
    else:
        cfg.trigger_mode = ask(q.select("Existing trigger mode:", choices=["auto", "core"]))
    if backend == "scope":
        cfg.scope_resource = ask(q.text("VISA resource (blank = unresolved):", default=""))
        cfg.scope_model = ask(q.text("Exact oscilloscope model (blank = pending):", default=""))
        cfg.scope_dialect = ask(q.select("Requested SCPI dialect:", choices=["auto", "keysight", "tek"]))
        cfg.scope_channel = parse_count(ask(q.text("Requested waveform channel number:",
                                                  default="1", validate=_valid_count)))
        cfg.scope_trig_source = ask(q.text("Raw trigger input name from the instrument manual:", default="EXTernal"))
        cfg.scope_trig_level = float(ask(q.text("Requested scope trigger level (V):",
                                               default="1.5", validate=_finite_float)))
    return cfg


def _demo(plain=False):
    from acq.progress import make_ui
    ui = make_ui(SimpleNamespace(target="SYNTHETIC UI DEMO"), fancy=False if plain else None)
    ui.log("SYNTHETIC UI DEMO — scripted counters, no instrument, waveform, dataset or performance measurement.")
    ui.start(12, 0)
    try:
        for i in range(1, 13):
            time.sleep(0.035)
            ui.update(1, {"done": i, "fail": int(i >= 5), "retry": int(i >= 6),
                          "checkpoint": (i // 4) * 4})
        ui.summary({"status": "synthetic preview finished",
                    "measured records": 0, "files written": 0,
                    "live integration": "pending"})
    finally:
        ui.close()


@click.command(context_settings=dict(help_option_names=["-h", "--help"]))
@click.option("--clock-mhz", type=float, default=50.0, show_default=True,
              help="Requested target frequency; never applied by this offline tool.")
@click.option("--clock-source", type=click.Choice(["husky", "external"]), default=None)
@click.option("--firmware-uart-divisor", type=int, default=27, show_default=True,
              help="Declare the divisor already used by firmware; no register write.")
@click.option("--baud", default="auto", show_default=True, help="Host UART request: auto or integer.")
@click.option("--port", default="", help="Explicit UART resource recorded in the configuration.")
@click.option("--adc-mul", type=int, default=4, show_default=True)
@click.option("--target", type=click.Choice(TARGETS), default=None)
@click.option("--traces", default=None, help="Requested count, e.g. 10000, 100k, 1M.")
@click.option("--key", type=click.Choice(["fixed", "random"]), default="fixed")
@click.option("--input", "input_", type=click.Choice(["fixed", "random"]), default="random")
@click.option("--trigger", default=None, help="Existing mode auto/core or raw 7-bit AEAD field; no tuning.")
@click.option("--samples", type=int, default=0, help="Requested samples per record; zero remains unresolved offline.")
@click.option("--offset", type=int, default=0, help="Requested sample offset.")
@click.option("--gain", type=float, default=25.0, help="Requested gain, not an optimum or an applied setting.")
@click.option("--suffix", default="")
@click.option("--chunk", type=int, default=20000)
@click.option("--seed", type=int, default=0)
@click.option("--backend", type=click.Choice(["husky", "scope"]), default="husky")
@click.option("--scope-resource", default="")
@click.option("--scope-model", default="", help="Expected exact scope model; compatibility remains unverified.")
@click.option("--scope-dialect", type=click.Choice(["auto", "keysight", "tek"]), default="auto")
@click.option("--scope-channel", type=int, default=1)
@click.option("--scope-trigger-source", default="EXTernal")
@click.option("--scope-trigger-level", type=float, default=1.5)
@click.option("--reset", is_flag=True, help="Record a reset request only; this tool cannot reset hardware.")
@click.option("--allow-nofit", is_flag=True, help="Record the legacy disk-space override only.")
@click.option("--estimate", is_flag=True, help="Estimate uncompressed waveform payload from explicit samples.")
@click.option("--show-config", is_flag=True, help="Print configuration JSON without writing a file.")
@click.option("--save-config", type=click.Path(path_type=Path, dir_okay=False),
              help="Write a new JSON configuration file; existing files are preserved.")
@click.option("--wizard", "force_wizard", is_flag=True)
@click.option("--plain", is_flag=True, help="Plain text, without animated progress.")
@click.option("--no-color", is_flag=True, help="Disable colors; NO_COLOR is also honored.")
@click.option("--demo", is_flag=True, help="Show a labelled synthetic progress preview; no device or data access.")
@click.option("-y", "--yes", is_flag=True, hidden=True,
              help="Legacy compatibility; no live action exists to confirm.")
def main(clock_mhz, clock_source, firmware_uart_divisor, baud, port, adc_mul,
         target, traces, key, input_, trigger, samples, offset, gain, suffix,
         chunk, seed, backend, scope_resource, scope_model, scope_dialect,
         scope_channel, scope_trigger_source, scope_trigger_level, reset,
         allow_nofit, estimate, show_config, save_config, force_wizard,
         plain, no_color, demo, yes):
    """Review/export acquisition requests offline. Live integration is pending."""
    console.configure_console(plain=plain, no_color=no_color)
    if demo:
        if save_config or show_config or estimate or force_wizard:
            raise click.UsageError("--demo cannot be combined with export, estimate or wizard")
        _demo(plain=plain)
        return
    interactive = force_wizard or (target is None and traces is None and sys.stdin.isatty())
    if force_wizard and not sys.stdin.isatty():
        raise click.UsageError("--wizard requires an interactive terminal; use explicit options")
    if interactive:
        from click.core import ParameterSource
        context = click.get_current_context()
        allowed = {"clock_mhz", "force_wizard", "plain", "no_color", "save_config", "show_config", "estimate"}
        conflicts = [name for name in context.params if name not in allowed
                     and context.get_parameter_source(name) == ParameterSource.COMMANDLINE]
        if conflicts:
            raise click.UsageError("wizard collects its own settings; remove conflicting options: "
                                   + ", ".join(name.replace("_", "-") for name in conflicts))
    try:
        if interactive:
            cfg = wizard(clock_mhz=clock_mhz)
        else:
            if target is None or traces is None:
                raise click.UsageError("give --target and --traces, or use --demo / an interactive wizard")
            cfg = AcqConfig(
                target=target, traces=parse_count(traces), clock_mhz=clock_mhz,
                clock_source=clock_source or "", firmware_uart_divisor=firmware_uart_divisor,
                baud=_baud(baud), port=port, adc_mul=adc_mul, key_policy=key,
                input_policy=input_, gain_db=gain, samples=samples, offset=offset,
                suffix=suffix, chunk=chunk, seed=seed, backend=backend,
                scope_resource=scope_resource, scope_model=scope_model,
                scope_dialect=scope_dialect, scope_channel=scope_channel,
                scope_trig_source=scope_trigger_source, scope_trig_level=scope_trigger_level)
            if trigger is not None:
                if cfg.is_aead:
                    cfg.aead_trigger = int(trigger, 0)
                else:
                    cfg.trigger_mode = trigger
        cfg.force_reset, cfg.allow_nofit = reset, allow_nofit
        cfg.validate()
    except (ValueError, TypeError, OverflowError) as exc:
        raise click.UsageError(str(exc)) from exc

    document = configuration_document(cfg)
    if estimate:
        bytes_per_sample = 2 if cfg.backend == "husky" else 4
        document["waveform_payload_estimate"] = {
            "samples_per_record": cfg.samples or None,
            "assumed_bytes_per_sample": bytes_per_sample,
            "bytes": cfg.traces * cfg.samples * bytes_per_sample if cfg.samples else None,
            "scope": "waveforms only; excludes metadata, temporary files and filesystem overhead",
            "assumptions": "prospective storage layout; live/storage integration is not published",
        }
    if show_config:
        click.echo(json.dumps(document, indent=2, allow_nan=False))
    else:
        _panel(cfg)
        if estimate:
            value = document["waveform_payload_estimate"]["bytes"]
            click.echo("Uncompressed waveform payload estimate: "
                       + (f"{value:,} bytes (metadata/overhead excluded)" if value is not None
                          else "unknown; provide --samples for arithmetic only"))
    if save_config:
        try:
            with save_config.open("x", encoding="utf-8") as handle:
                json.dump(document, handle, indent=2, allow_nan=False)
                handle.write("\n")
        except OSError as exc:
            raise click.ClickException(f"cannot create configuration {save_config}: {exc}") from exc
        if not show_config:
            click.echo(f"Saved configuration: {save_config}. No hardware action was started.")


if __name__ == "__main__":
    main()
