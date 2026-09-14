# Linux udev rules

[`60-proact.rules`](60-proact.rules) grants the active desktop user and members
of the `dialout` group access to the MCP2200, MCP2210 and NewAE/ChipWhisperer USB
interfaces, with mode `0660`. It also tells ModemManager to ignore their serial
ports. Install it once on Linux:

```bash
sudo bash tools/install_udev.sh
```

Then unplug and reconnect the USB devices and log out/in if group membership
changed. Run the PROACT GUI and CLI as your normal user; do not use `sudo`.
