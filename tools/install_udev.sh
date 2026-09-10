#!/usr/bin/env bash
# Install the PROACT udev rules so the MCP2200/MCP2210 adapters and the
# ChipWhisperer work WITHOUT sudo. Run once:
#
#     sudo bash tools/install_udev.sh
#
# Then UNPLUG and REPLUG the USB devices (or reboot). After that you can launch
# the GUI as your normal user: python3 Software/GUI/proact_gui.py
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo:  sudo bash tools/install_udev.sh"
  exit 1
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/../udev/60-proact.rules"
DST="/etc/udev/rules.d/60-proact.rules"

mkdir -p /etc/udev/rules.d
cp "$SRC" "$DST"
echo "installed $DST"

# Also add the invoking user to the 'dialout' group (needed for /dev/ttyACM* on
# some distros). SUDO_USER is the real user who ran sudo.
if [ -n "${SUDO_USER:-}" ] && getent group dialout >/dev/null; then
  usermod -aG dialout "$SUDO_USER" || true
  echo "added $SUDO_USER to the 'dialout' group (log out/in for this to take effect)"
fi

udevadm control --reload
udevadm trigger
echo
echo "Done. Now UNPLUG and REPLUG the MCP2200/MCP2210 and the ChipWhisperer,"
echo "then run the GUI as your normal user (no sudo)."
