# PROACT acquisition configuration

This public folder provides an **offline configuration wizard, JSON review/export
and synthetic progress demo**. Live integration is pending. It cannot open an
instrument, program or reset a board, start a capture, resume a dataset or perform
an analysis. The original acquisition package remains unchanged.

The wizard asks for target frequency first (default **50 MHz**), then the
existing firmware UART divisor (default **27**) and a requested host baud.
Other fields describe the intended instrument, input policies, record length,
trigger input and output configuration. Every value is a request; no setting is
applied or measured here.

## Install and explore

From this directory:

```bash
./install.sh --check
./install.sh
./run.sh --demo
./run.sh
```

Setup uses a dedicated local environment and installs only the offline UI
dependencies. It does not install instrument drivers, modify the shared bench
environment or supply firmware. Use `--use-venv DIR` to select a dedicated
environment, or `PROACT_ACQ_PYTHON=/path/to/python ./run.sh …` to use an explicit
existing interpreter. `--check` is read-only and reports missing dependencies.

For a small **configuration example**, with no capture:

```bash
./run.sh --target aes1 --traces 10 --clock-mhz 50 --samples 100 --estimate --plain
./run.sh --target aes1 --traces 10 --show-config
./run.sh --target aes1 --traces 10 --save-config setup.json
```

These counts and sample lengths illustrate the interface, not a recommended
measurement setup. `--save-config` creates a new JSON file and refuses to
overwrite an existing file. It contains configured key/input values as well as
instrument requests, so review it before sharing.

## Frequency and UART

The displayed firmware UART rate is calculated from the **declared** existing
clock and divisor:

```text
declared firmware baud = target frequency / (16 × divisor)
host baud in auto mode = nearest integer to that calculated rate
```

At the default 50 MHz and divisor 27 this gives **115740.740741 baud**, so auto
requests **115741**. Explicit `--baud 115200` is also within the 2% configuration
tolerance. The software never writes a UART divisor or verifies that the declared
divisor is present in firmware. A calculated rate is not a measured rate.

## Reviewable output

- `--show-config`: JSON containing requested values, calculated rates and
  `observed_instrument: null`.
- `--estimate`: uncompressed waveform-payload arithmetic for an explicit sample
  count. Zero samples remains unknown; metadata, temporary files and filesystem
  overhead are excluded. The storage representation is prospective.
- `--demo`: labelled synthetic counters with elapsed time, ETA, failures, retries
  and a checkpoint position. It creates no waveform or dataset and measures no
  acquisition performance.
- `--plain`, `--no-color` and `NO_COLOR`: accessible terminal alternatives.
  Redirected output uses plain text.

The exact oscilloscope model and live compatibility remain pending. The existing
source backend's broad Keysight/Tektronix support claims have not been verified
by this release. Read the [offline guide](docs/OFFLINE_GUIDE.md) and
[source audit](docs/SOURCE_AUDIT.md) for the remaining work.

## Tests

The included tests use fake prompts, temporary configuration files and a recorded
80-column terminal. They guard against capture/driver imports. With pytest and
the UI dependencies available in a dedicated interpreter:

```bash
python -m pytest acq/tests/test_cli_config.py acq/tests/test_setup_offline.py -W error
```

Integrated counts and remaining limits are recorded in the
[host release report](../reports/HOST_SOFTWARE_RELEASE.md).
