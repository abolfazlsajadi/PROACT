# Host maintenance tools

- `setup_env.sh` / `setup_env.py`: dedicated environment setup.
- `run_tests.sh`: offline regressions with checked-out imports.
- `check_gui_layout.py` / `gen_gui_screenshots.py`: disconnected Qt checks and screenshots.
- `generate_cli_reference.py`: current command help without command dispatch.
- `install_udev.sh`: optional Linux device-access rules, requires explicit administrator execution.

See [installation](../INSTALL.md), [test instructions](../tests/README.md), and [release evidence](../reports/HOST_SOFTWARE_RELEASE.md). Historical isolated-copy verification and private design-build tools are not part of this host release.
