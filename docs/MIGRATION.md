# Migrate to PROACT host software 1.1.0.dev1

This update changes host behavior where errors were silent, resources leaked or large jobs became difficult to inspect. The host protocol bytes and register definitions retain their existing meanings; this release does not change firmware. Historical repository release headings used a different numbering sequence from the Python package version.

For a public checkout, supply matching controller/Sw-RV images and an FPGA bitstream separately. Complete design and firmware sources and reference capture datasets are not part of this host release. See [installation](../INSTALL.md) and [image prerequisites](bringup_guide.md).

| Before | Now | What a caller should do |
|---|---|---|
| Setup deleted `~/.proact-venv` and could uninstall a package from the base interpreter | Repository-local environment is created or reused without deletion | Use the repository launchers; inspect `doctor` paths |
| GUI disconnect waited for a transaction lock in the Qt thread | Cleanup runs as an asynchronous operation | Watch the visible job status; allow a current operation to finish |
| Repeated GUI actions/polls could accumulate workers | One foreground job and coalesced background polling | Start a new operation after the current one completes |
| GUI display logs and unsaved experiment-result lists grew indefinitely | Bounded display histories and streamed optional log files | Enable file logging for a complete long-run text archive |
| Invalid numeric CLI options reached device handlers | Positive/finite/range/alignment validation happens before dispatch | Correct the argument shown in the error; usage errors exit 2 |
| Unsupported ASCON/Xoodyak hardware decryption could wait for a timeout | Hardware decrypt selection is rejected/disabled | Use the existing host `decrypt-soft` implementation |
| Failed scope connection silently produced functional-only records | Requested scope connection failure aborts preparation and closes handles | Choose `--no-scope` / `capture=False` explicitly for functional-only runs |
| A partial CLI acquisition could exit successfully | Completed records are saved, then an incomplete run exits 1 | Check the exit code, record count and failure log |
| Failed UART open or serial close could retain its process lock | Open failure releases the lock; close releases it even if serial cleanup raises | Retrying a failed connection no longer waits for garbage collection |
| Invalid VMEM could reset the chip before parsing failed | Program input is validated before the reset sequence; CLI checks before opening SPI | Fix the filename and line-specific diagnostic |
| Out-of-range firmware words were later masked to 32 bits | VMEM parser rejects them; multiline/inline comments and BOM are supported | Regenerate a valid 32-bit VMEM instead of relying on truncation |
| A valid tag with extra trailing bytes could authenticate | Reference decrypt requires exactly 16 tag bytes | Separate the tag from surrounding framing bytes |
| Dataset append retained caller-owned buffers | Complete rows are copied before acceptance | Buffer reuse no longer mutates previous records |
| Snapshot writes truncated the previous checkpoint in place | Complete temporary files replace the last checkpoint atomically | Allow temporary disk headroom; see storage limits |
| NPZ/HDF5 snapshots rewrote the whole dataset | Snapshots retain that compatibility; optional `.tracepack` commits bounded chunks | Use a fresh `.tracepack` path for large runs |
| HDF5/NPZ metadata and file suffix handling disagreed | Reader detects container content and preserves structured metadata | Check `storage_format`; HDF5 failures are also available at top level |

Generic `InputPlan` still supports arbitrary-length offline values. The GUI uses `validate_for_hardware(core)` before jobs to enforce the current 16-byte block protocol. This check validates every generated variable without consuming random inputs, and owns byte copies of accepted fixed/file inputs. Byte lengths are checked after conversion, including memoryviews with multi-byte elements. Input-file duplicate fields and malformed hex now identify the line and field instead of silently replacing a value or returning a bare parser error.

Basic imports and CLI information no longer load NumPy/HDF5. `from proact_host import storage` and `proact_host.storage` remain supported through lazy loading. The new `doctor --json` command discovers packages without importing hardware backends; it is an installation aid, not a device self-test.

`Mcp2210Programmer.close()` is idempotent and closes the backend HID handle without changing reset GPIO state. Failed setup and the CLI programming/reset/restart handlers now close acquired resources. Offline fake-device tests cover these paths; physical disconnect timing has not been measured.

The standard interface-board feedback map is now SPI reset GPIO0, SPI select GPIO3, controller reset GPIO6 and global reset GPIO8; GPIO7 is the X1 debug input. Older host scripts mislabeled these readers as controller/global/select GPIO3/6/7. Code for a differently wired board must pass an explicit `Mcp2210Pins(...)` instance to `Mcp2210Programmer`; do not modify the shared standard-board defaults. With the tested standard board and `mcp2210-python` 1.0.4, opening the programmer now preserves observed output levels during one explicit setup flush. A live UART-continuity test confirmed that a status-only connection did not restart the controller.

Retained historical material remains useful for chip architecture, but the new guides take precedence for launch/setup/storage behavior. No universal minimum CPA/TVLA count, measured analog improvement or new board validation follows from these changes.
