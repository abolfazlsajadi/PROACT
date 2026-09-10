# Acquisition-source audit and publication boundary

This is a static review of the existing acquisition package. No instrument was
opened and no capture was started. The original package remains unchanged.
The public addition contains only offline configuration, review/export, terminal
UI/demo and installer checks. Backend, acquisition-driver, target and dataset
storage integration is pending and is not included in this public folder.

The source findings below are **unresolved in the original package**. They are
not claims that the public tool fixes live capture behavior. Source paths in the
table refer to that separate package, not files promised in this public release.

| Area | Static finding in the source | Required review before live integration |
|---|---|---|
| Completion status | `acq/backend/scope.py` treats a failed Keysight completion-status query as completed | A query failure must remain a failed/unknown acquisition; stale data must not be accepted as a new record |
| Waveform units | Scope output is physical volts, but shared clipping/storage logic assumes normalized ±0.5 values and clips on conversion | Preserve explicit per-backend units/scaling and reject invalid or nonfinite data before storage |
| Scope settings | Offset/gain arguments are ignored; requested sample rate and actual horizontal preamble are not verified/retained | Separate supported settings from unused fields and preserve actual readbacks and waveform time coordinates |
| Model support | Default dialect is Keysight, despite documentation claiming autodetection; unknown automatic identities fall through to Tektronix | Match exact instrument identity and supported model/firmware commands; unsupported devices must not receive guessed commands |
| Resource lifetime | Driver cleanup begins after bring-up/preflight/store construction; a final checkpoint failure skips later closes | Cover partial initialization and independent cleanup failures |
| VISA and programmer handles | VISA ResourceManager is local and not explicitly closed; controller reprogramming retains its programmer handle | Make resource ownership explicit and test failure paths with fakes |
| Clock/UART ordering | UART verification precedes Husky clock setup, while the scope backend supplies no board clock | Require a known clock source and existing firmware divisor; distinguish calculated from measured UART rate |
| Resume identity | Source compares shape and sampled regenerated inputs, but not every acquisition setting or a fixed key | Refuse incompatible/unverifiable continuation before any dataset write or hardware operation |
| Partial dataset pair | A missing metadata or waveform partner takes the fresh-allocation path | Preserve existing evidence and reject incomplete pairs instead of recreating files |
| Retained-record validity | Most stored outputs are not checked per row; periodic mismatches do not invalidate their rows | Separate attempted, saved and validated record counts and report incomplete/error outcomes accurately |
| Source installer | Prefers a shared bench environment and may modify it; `--check` can create an environment/write a path file | Public offline installer uses a dedicated environment and read-only checking; source installer remains unchanged |
| Source tests | Handwritten check counters only cause failure at the module entry point | Public tests use pytest assertions and explicit no-driver-import guards |

The public configuration interface addresses only request validation and honest
presentation: frequency first, declared UART arithmetic with a mismatch gate,
raw field bounds, explicit resource descriptions, unresolved instrument readbacks
and synthetic progress labels. A configuration review is not a board validation.

## Material excluded from publication

The inspected source folder contained a measured waveform/metadata pair, three
generated firmware VMEM images, cached bytecode, a machine-specific interpreter
path, a bundled older host runtime and historical reports/images. They are not
part of this public addition. Full design sources, matching firmware/bitstreams
and existing measured datasets retain their separate publication and provenance
boundaries. No dataset, key-recovery result or trace-count claim is produced here.

See the [offline guide](OFFLINE_GUIDE.md) for current capabilities and the
[host release report](../../reports/HOST_SOFTWARE_RELEASE.md) for integrated
software validation. Exact-model oscilloscope compatibility and all live
instrument tests remain pending.
