# Review an acquisition configuration offline

The public tool prepares a configuration and previews the terminal interface.
Live integration is pending. It never imports the acquisition driver, enumerates
devices, opens UART/VISA/USB resources, or changes a board. See the
[folder README](../README.md) for installation.

## 1. Declare frequency and the existing UART configuration

Run `./run.sh` in an interactive terminal. Frequency is the first question, with
a 50 MHz default. Then declare the divisor already used by the firmware; the
default is 27. These declarations are not hardware readbacks or programming
instructions.

The calculated firmware UART rate is `frequency_hz / (16 × divisor)`. Host baud
`auto` chooses the nearest integer. An explicit host baud is preserved, and a
declared mismatch greater than 2% is rejected. Check the existing firmware and
board clock independently before relying on this calculation.

## 2. Describe the intended resources and requests

| Field | What the offline tool records |
|---|---|
| UART resource | Explicit path/name; blank means unresolved |
| Backend | Husky or oscilloscope as a requested measurement path |
| Clock source | Husky or external; the scope path requires a separate external board clock |
| Target and input policies | Intended workload and fixed/random input policy |
| ADC multiplier | Requested sample-rate ratio, not a measured rate |
| Samples and offset | Requested ADC record length and sample offset; zero length stays unresolved offline |
| Gain | Husky-only request, unused for the scope configuration |
| Trigger | Existing `auto`/`core` mode or a raw 7-bit AEAD field, without tuning presets |
| Scope identity | Explicit VISA resource, expected exact model and requested dialect |
| Scope inputs | Requested waveform channel, raw trigger-source name and trigger level |

The tool rejects nonfinite values, nonpositive counts/clocks/divisors, invalid
sample offsets, unsupported trigger names and path separators in dataset suffixes.
The existing serializer uses a 7-bit AEAD field, so values above 127 are rejected
instead of silently truncated. The source's `firmware` trigger label is not
implemented and is rejected.

Wizard settings come from its prompts. Mixing other configuration flags with
`--wizard` is rejected; `--clock-mhz` may supply its initial frequency, and
presentation/export flags remain available. Use explicit CLI options for scripted
configuration generation.

## 3. Review or export

Without an export option, the command prints a review and stops. `--show-config`
prints JSON. `--save-config FILE.json` creates a new file and preserves an existing
file. JSON contains the requested values, the calculated UART/sample rates,
`live_integration: "pending"` and `observed_instrument: null`. It may contain
configured key/input values; inspect it before sharing.

`--estimate` computes waveform-payload bytes only when a sample count is explicit.
Its prospective layout uses two bytes per Husky sample or four bytes per scope
sample. This is arithmetic, not a verified writer, disk-capacity admission or
compatibility result. Metadata and temporary/filesystem overhead are excluded.

## 4. Preview progress without a board

`./run.sh --demo` shows scripted synthetic counters. The animated display puts the
record count/bar on one line and elapsed time, ETA, rate, failures, retries and
checkpoint position below it so an 80-column terminal remains readable.

The demo creates no waveform or dataset. Its displayed timing/rate reflects the
UI preview, not instrument or acquisition performance. Use `--plain` to disable
animation, `--no-color` or `NO_COLOR` to suppress colors; redirected output uses
plain text. Cancellation exits the wizard without saving a configuration.

## Instrument documentation still needed

The exact oscilloscope model and firmware must be identified before a future
backend integration can select supported commands and verify readbacks. These
vendor references are starting points to match against the actual model, not a
claim of compatibility with every Keysight or Tektronix instrument:

- [Keysight programming guide supplied for model review](https://www.keysight.com/us/en/assets/9018-07265/programming-guides/9018-07265.pdf).
- [Tektronix MDO3012 documentation lookup](https://www.tek.com/en/support/datasheets-manuals-software-downloads?model=MDO3012).

No connected model, physical wiring, ADC scaling or trigger completion behavior
was verified by this offline release. The [source audit](SOURCE_AUDIT.md) lists
the unresolved implementation findings.
