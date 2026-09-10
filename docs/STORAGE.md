# Dataset storage and large-run performance

Choose the format explicitly in the output path. Existing array names (`traces`, `plaintext`, `key`, `output`, `expected`, `valid`) are preserved.

| Path | Writer behavior | Reader behavior |
|---|---|---|
| `run.npz` | Whole dataset in memory; atomic compressed snapshot | `load()` materializes the snapshot |
| `run.h5` / `run.hdf5` | Whole dataset in memory; atomic HDF5 snapshot when h5py is available | `load()` materializes the snapshot |
| `run.tracepack` | New directory; immutable compressed chunks and an atomic checkpoint | `iter_chunks()` keeps one waveform chunk plus the descriptor index |

Legacy explicit `.h5` output without h5py still contains NPZ for compatibility. `load()` now detects the actual container; `metadata.storage_format` identifies it. Use `doctor` to check whether h5py is available. A file suffix alone is not proof of container type.

This offline example creates synthetic data only:

```python
from proact_host.storage import TraceStore, iter_chunks

with TraceStore("example.tracepack", {"synthetic": True}, chunk_rows=128) as store:
    for i in range(1000):
        store.append([0.0, float(i), 0.0], bytes(16), bytes(16), bytes(16))

for chunk in iter_chunks("example.tracepack"):
    print(chunk["metadata"]["row_start"], chunk["traces"].shape)
```

A tracepack path must be new. The default buffer holds up to 1,024 rows before automatic checkpointing. `count` includes accepted rows; `buffered_count` shows pending rows. Committed waveforms are released from the writer. Reader descriptors and failure logs can still grow with chunk/failure count; this is not a fixed-total-memory guarantee for arbitrarily large metadata.

The GUI's output text field and CLI output path can select `.tracepack`, but existing analysis file pickers have not been migrated to this new directory format. Use NPZ/HDF5 when direct compatibility with those tools is required. Read tracepacks through the host library's `iter_chunks()`/`load()` API; `load()` materializes the full dataset and is not a streaming analysis interface.

Each append copies all fields before accepting the row. Invalid conversion/shape leaves the row count unchanged. Different waveform lengths are retained with length arrays; shorter rows are padded and marked invalid relative to the global width. Presence of expected outputs is retained separately. This prevents a reused input buffer or one short record from silently altering earlier records.

Snapshots and manifests are published only after writing the temporary file and syncing it. After process interruption, readers see a preceding or newly committed checkpoint. Pending rows can be lost. Atomic rename/fsync here does not establish power-loss durability of the filesystem, support concurrent writers, or implement automatic acquisition resume.

If an automatic flush fails after append accepted a row, retry `flush()` rather than appending the row again. Orphan chunks from interrupted publication are ignored by the committed manifest. Tracepack readers verify chunk checksums by default. Keep the entire directory together when moving or backing up a run.

New files include row-length arrays and `storage_schema_version=2`. New HDF5 files preserve structured metadata in JSON as well as legacy attributes; failures are also returned at top level. `load()` accepts legacy metadata-free NPZ without inventing successful validation flags or expanding fixed-key arrays.

Snapshot durability adds I/O work; do not assume every save operation became faster. The [release report](../reports/HOST_SOFTWARE_RELEASE.md) records the evidence and limitations. Development storage measurements use synthetic data, not board captures.
