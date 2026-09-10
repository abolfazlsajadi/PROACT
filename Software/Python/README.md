# PROACT host Python library

Version **1.1.0.dev1** supplies the CLI and GUI, USB/UART transport, firmware loader, reference validation, input generation and dataset storage. The firmware command bytes and hardware register map retain their existing meanings.

From the workspace root:

```bash
./run_cli.sh doctor --json
./run_cli.sh --help
./tools/run_tests.sh
```

Use [INSTALL.md](../../INSTALL.md) for setup and [CLI_REFERENCE.md](../../docs/CLI_REFERENCE.md) for command options. Launchers put this checkout's package ahead of installed versions. The public host release requires separately supplied matching controller/Sw-RV images and, for FPGA programming, a bitstream; it does not ship the full design, firmware sources or capture datasets.

## Library map

| Module | Responsibility |
|---|---|
| `config`, `regs` | Bench defaults and generated register/protocol constants |
| `transport` | UART connection and controller command/reply framing |
| `programmer`, `vmem` | SPI loader, reset control, validated firmware parsing |
| `inputs` | Fixed/random values and input-file plans |
| `validation`, `aead_soft` | Reference comparisons and existing host AEAD decrypt |
| `experiment`, `capture` | Board/scope workflow and ChipWhisperer adapter |
| `storage` | Atomic NPZ/HDF5 snapshots and chunked `.tracepack` datasets |
| `diagnostics` | Offline environment discovery with no device enumeration |
| `fullcheck` | Existing live A–Z board check; separate from offline tests |

`import proact_host` does not import NumPy, HDF5 or hardware backends. Storage loads when requested. The basic API remains:

```python
from proact_host.inputs import InputPlan, Variable, VARS

variables = {name: Variable(random=False, value=bytes(16)) for name in VARS}
plan = InputPlan(variables=variables, n=10).validate_for_hardware("aes1")
# Validation checks widths before a board job and consumes no randomness.
```

For synthetic storage examples, bounded iteration and compatibility notes, see [STORAGE.md](../../docs/STORAGE.md). `load()` materializes all records; `iter_chunks()` is the bounded-waveform reader for tracepacks.

`PROACTExperiment` remains the hardware workflow API. A requested scope failure now raises and closes acquired handles instead of silently saving empty waveforms. `capture=False` explicitly selects functional-only operation. Use a context manager so cleanup occurs on exceptions. This release's verification used fake devices; no live capture was run.

The firmware parser accepts normal SRecord VMEM output, inline/multiline comments and a UTF-8 BOM. It rejects malformed tokens and values outside unsigned 32-bit range with filename and line number. `Mcp2210Programmer.program()` validates before its reset sequence, and `close()` releases its HID handle without changing GPIO state.

The ASCON-128 v1.2 and Xoodyak reference algorithms are retained. Their authentication-tag acceptance now requires exactly 16 bytes. They remain validation code, not constant-time production cryptographic implementations.

See [migration notes](../../docs/MIGRATION.md) for behavior changes and the [test guide](../../tests/README.md) for evidence and hardware limits.
