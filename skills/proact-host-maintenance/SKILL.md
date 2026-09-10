---
name: proact-host-maintenance
description: Maintain and verify PROACT host software, including GUI, CLI, storage, setup and documentation. Use for repository software changes; PCB redesign and live measurements require their own task scope.
---

# PROACT host software maintenance

Use the repository containing this skill. Read `AGENTS.md` for maintenance scope and [the release report](../../reports/HOST_SOFTWARE_RELEASE.md) for current evidence and limitations. Review `git status` before editing and preserve unrelated work. The public tree does not include the full design, firmware sources, generated images or reference capture datasets; historical references to those artifacts require a separate design package.

The GUI has its own foreground worker loop; CLI acquisition uses `PROACTExperiment`. They share low-level libraries, not identical orchestration. Use [the architecture map](../../docs/ARCHITECTURE.md) to find both callers of a changed contract. Preserve firmware command bytes and address semantics unless the task specifically requires a protocol change.

For host-only work, use `./tools/run_tests.sh` with fake devices and temporary files. GUI checks use Qt offscreen. The GUI's Self-Check and hardware CLI commands communicate with devices; they are not substitutes for the offline suite. Do not infer a live hardware result from a fake-resource test.

Use a dedicated environment and the repository launchers. They put checked-out source first on the Python path. `doctor --json` discovers packages/files without enumeration; it does not prove driver compatibility or connectivity. ChipWhisperer 6.0.0 declares NumPy <=1.26.4. Read actual runtime versions before comparing performance, and do not alter another bench environment.

Before changing storage, read [storage semantics](../../docs/STORAGE.md). Tracepack is an opt-in directory format; existing single-file analysis readers have not been migrated. `load()` materializes all rows; `iter_chunks()` retains one waveform chunk and a descriptor index. Keep partial-publication and mutable-buffer regressions when altering checkpoints. A write failure after an accepted append should retry flush, not duplicate the row.

Use saved benchmark JSON and source hashes as evidence. Re-run timings when source/runtime changes affect them, not automatically for documentation edits. State whether a timing measures UI callback latency, basic import, storage writes or a real acquisition. Atomic legacy snapshots can be slower despite stronger interruption handling.

Update the relevant guide and regenerate `docs/CLI_REFERENCE.md` with `tools/generate_cli_reference.py` after parser changes. For visual changes, run `tools/check_gui_layout.py` and inspect generated page screenshots. Validate local documentation links, review the complete diff and save concrete test results and remaining limits in a release report. When the task authorizes publication, publish only the reviewed scope and verify the remote commit; otherwise keep publication separate from software verification.
