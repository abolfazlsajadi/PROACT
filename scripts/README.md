# Offline helper scripts

`gen_hardware.py` contains pure `load()` and `gen_py()` functions for comparing the host register map with configuration. Its direct entry point also writes a firmware header and belongs in a complete design checkout.

`benchmark_startup.py`, `benchmark_storage.py` and `../tests/test_gui_benchmark.py` require an explicit local `--baseline COMMIT` containing the relevant host sources. They use synthetic inputs or fake handles. The historical development baseline is not bundled in public Git history. Run `--help` for the procedure; no benchmark starts automatically.

`plot_software_benchmarks.py` plots the saved historical measurements described in [the release report](../reports/HOST_SOFTWARE_RELEASE.md).
