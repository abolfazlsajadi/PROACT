# Start here

Use the launchers from the repository root. For a fresh checkout, complete [installation](../INSTALL.md) first; a prepared virtual environment is not shipped in Git.

1. Run `./run_cli.sh doctor`. Check the printed workspace and Python paths. Missing GUI, HDF5 or test packages can be installed with the matching setup extras in [INSTALL.md](../INSTALL.md).
2. Run `./tools/run_tests.sh`. This is the offline software suite. The GUI's **Self-Check (A–Z)** is a different operation that communicates with a real board.
3. Run `./run_gui.sh`. The GUI opens disconnected. Each page states its purpose, and the bottom status bar shows the active task and elapsed time.
4. When the bench is available, select the correct board and matching, separately obtained firmware images. The public host release does not include the complete RTL, firmware sources, generated VMEM images or an FPGA bitstream. Do not mistake an absent image for a USB failure. See the [image prerequisites](bringup_guide.md).
5. For incremental storage through the new Python reader, choose a new output path ending in `.tracepack`. Keep NPZ/HDF5 when a retained analysis script needs a single file. Read [storage semantics](STORAGE.md) first: committed chunks can be read after interruption, but automatic acquisition resume is not implemented.

For scripted use, global CLI options precede the subcommand:

```bash
./run_cli.sh --no-color info
./run_cli.sh doctor --json
./run_cli.sh run --help
./run_cli.sh program --help
```

These examples inspect software only. A valid hardware command opens devices; choose it when the board is available. Bad counts, nonfinite clocks, unaligned addresses, unsupported hardware AEAD decryption and invalid firmware files now fail before their device operation begins.

The GUI serializes foreground jobs. Disconnect and close no longer wait on a device lock in the Qt event thread; they perform cleanup asynchronously. A busy task must finish before another board operation starts. The UI does not force-stop a programming operation halfway through a transfer.

The display retains a bounded recent history. Enable **Also save log file** before an experiment when you need its complete text record. CSV monitor export contains the retained rows, not an unlimited archive. Dataset records are stored separately from display logs.

For current controls see the [GUI guide](wiki/GUI-Guide.md); for every option see the [generated CLI reference](CLI_REFERENCE.md). Historical gain settings, trace counts and old screenshots in the retained manual are not new experimental guarantees. Integrated software checks and their limits are recorded in the [release report](../reports/HOST_SOFTWARE_RELEASE.md).
