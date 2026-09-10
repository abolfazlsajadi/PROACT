# Install and run PROACT host software

Use Python 3.9 or newer and Bash for the launch scripts. The current local validation uses Python 3.10.12 on Linux. Windows/macOS are not tested by this update.

From the repository root:

```bash
bash tools/setup_env.sh --dry-run --with gui --with hdf5 --with dev
bash tools/setup_env.sh --with gui --with hdf5 --with dev
./run_cli.sh doctor
./tools/run_tests.sh
./run_gui.sh
```

The dry run only prints a plan. Setup creates `.venv` here or reuses an existing environment; it never deletes it. `--copies` and a precreated `lib64` directory support filesystems such as exFAT without requiring symbolic links. A nonempty directory without `pyvenv.cfg` is rejected.

Optional groups are `gui`, `hdf5`, `capture`, `dev` and `all`; repeat `--with` as needed. Base installation provides the host stack. Add `--with capture` for the pinned ChipWhisperer 6.0.0 dependency when preparing a scope workflow. `--system-site-packages` explicitly enables package inheritance when creating a new environment; otherwise setup creates an isolated environment. The release's installation checks and runtime versions are recorded in the [release report](reports/HOST_SOFTWARE_RELEASE.md).

To choose a different new environment or Python:

```bash
PROACT_SETUP_PYTHON=python3.10 bash tools/setup_env.sh --venv /path/to/dedicated-env --with gui
PROACT_VENV=/path/to/dedicated-env ./run_cli.sh doctor
```

Setup refuses the reserved `~/.proact-venv` bench environment. Use a dedicated path. For environment creation without package installation, use `--no-install` (creating a fresh environment still needs Python's bundled ensurepip support).

Launchers select `PROACT_PYTHON` if explicitly set, then `PROACT_VENV`, then this repository's `.venv`, then an available shared/system interpreter. Explicit broken overrides report an error instead of silently choosing another environment. Every launcher puts the checked-out Python package first. Check the paths printed by `doctor` before using an existing interpreter.

`doctor --json` reports the interpreter, workspace, discoverable dependency versions and missing firmware files. Package discovery does not check version constraints or prove that drivers can import or that a board is connected. It performs no USB enumeration.

The public host release does not include firmware sources, generated VMEM images, an FPGA bitstream or reference capture datasets. Missing image files in `doctor` are expected until you supply matching artifacts. GUI operation needs the matching controller VMEM; Sw-RV workloads additionally need their instruction/data images, and an unconfigured FPGA needs its matching bitstream.

USB permissions and board programming are separate from Python setup. See the [bring-up guide](docs/bringup_guide.md) for artifact and board prerequisites and the [software quick start](docs/START_HERE.md) for the workflow. Offline software checks do not validate a connected board.
