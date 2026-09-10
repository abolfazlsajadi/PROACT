# PROACT host-software maintenance

Maintain the GUI, CLI, Python library, setup tools and current documentation in this repository. Read `reports/HOST_SOFTWARE_RELEASE.md` for release evidence and `skills/proact-host-maintenance/SKILL.md` for the verification workflow. Preserve firmware command bytes, address semantics and supported interfaces unless the task requires a documented change.

The public tree includes platform/PCB documentation but not the complete design, firmware sources, generated images or capture datasets. Do not infer that a path mentioned in historical platform documentation is an available release artifact. Keep host-software claims separate from board and side-channel measurements.

Routine software validation is offline: use fake devices, temporary files and Qt offscreen rendering. Do not open or enumerate serial, HID, ChipWhisperer or other bench devices as a side effect of tests. Hardware work, changes to services or udev rules, and publication require authorization for the task. Tests must import the checked-out source. Keep dependencies in a dedicated environment; do not modify another bench environment.

When collaborating, stay within assigned files and announce shared-file needs. Record concrete bugs, changed behavior, test results, performance conditions and unverified hardware behavior. Keep reviewable release evidence in `reports/` and current user guides in `docs/`. Preserve unrelated changes and review the complete diff before committing or publishing.
