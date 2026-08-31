#!/bin/bash
# Attach the ddcci i2c device for every display that answers DDC/CI.
#
# Why this exists at all: kernel 6.8 dropped I2C_CLASS_DDC, so the ddcci driver
# cannot auto-probe displays any more -- it says so itself in ddcci_module_init().
# The bus has to be named from userspace.
#
# Distributed by archsetup (core/ddcci.py, data/ddcci/); the measurements below
# were taken on the machine it was written for, so they are dated and attributed
# rather than claimed about yours.
#
# Why the bus comes from ddcutil and not from /sys/class/drm/<connector>/ddc:
# that symlink is what every upstream helper (ddcci-discover, ddcci-probe) reads,
# and the nvidia driver does not create it.  MEASURED 2026-08-28 on two machines
# (an nvidia desktop and an amdgpu laptop), same kernel 7.1.10-zen1: amdgpu
# connectors carry the symlink, nvidia connectors carry nothing.  ddcutil finds
# the bus by talking to it instead.
#
# Why recovery reloads the modules instead of re-adding the i2c device: the driver
# leaks its own bus device on unbind.  MEASURED 2026-08-28: after delete_device the
# node /sys/bus/ddcci/devices/ddcci2 stays behind, so the next probe dies with
# "cannot create duplicate filename" / -EEXIST, and every probe after that reads
# corrupted DDC/CI frames (-ENODEV).  A module reload is the only thing that clears
# it -- which is what the hand-written predecessor was really doing.

set -uo pipefail

ADDR=0x37
MAX_RETRIES=${MAX_RETRIES:-5}
RETRY_DELAY=${RETRY_DELAY:-1}
DETECT_TRIES=${DETECT_TRIES:-15}

log() { printf '%s: %s\n' "${0##*/}" "$*"; }

reload_modules() {
  modprobe -r ddcci_backlight 2>/dev/null
  modprobe -r ddcci 2>/dev/null
  modprobe ddcci
  modprobe ddcci_backlight
}

modprobe ddcci
modprobe ddcci_backlight

detect_buses() {
  ddcutil detect --skip-ddc-checks --disable-dynamic-sleep --brief 2>/dev/null |
    awk -F'/dev/i2c-' '/I2C bus:/ {print $2}'
}

# At boot the GPU may not be ready yet; wait for it instead of racing a fixed
# window.  Costs nothing on the udev path, where the display is already there.
buses=""
for try in $(seq 1 "$DETECT_TRIES"); do
  buses=$(detect_buses)
  [ -n "$buses" ] && { log "ddcutil found bus(es) [$(echo $buses | tr '\n' ' ')] on try $try"; break; }
  sleep 1
done

[ -n "$buses" ] || log "no DDC/CI display found after $DETECT_TRIES tries"

while read -r bus; do
  [ -n "$bus" ] || continue
  dir="/sys/bus/i2c/devices/i2c-$bus"
  node="$dir/$bus-0037"
  [ -e "$node" ] || echo "ddcci $ADDR" > "$dir/new_device" 2>/dev/null
  attempt=0
  while :; do
    if [ -e "$node/driver" ]; then
      log "ddcci bound on i2c-$bus after $attempt reload(s)"
      break
    fi
    if [ "$attempt" -ge "$MAX_RETRIES" ]; then
      log "FAILED: ddcci did not bind on i2c-$bus after $MAX_RETRIES reloads"
      break
    fi
    attempt=$((attempt + 1))
    reload_modules
    [ -e "$node" ] || echo "ddcci $ADDR" > "$dir/new_device" 2>/dev/null
    [ -e "$node/driver" ] || sleep "$RETRY_DELAY"
  done
done <<< "$buses"

# Drop what a departed display left behind: instantiated, but no driver bound.
# The entries under /sys/bus/i2c/devices are symlinks in one flat directory, so
# delete_device lives next to the *bus*, not next to the node.
for node in /sys/bus/i2c/devices/*-0037; do
  [ -e "$node" ] || continue
  [ "$(cat "$node/name" 2>/dev/null)" = "ddcci" ] || continue
  [ -e "$node/driver" ] && continue
  name=${node##*/}
  log "detaching stale $name"
  echo "$ADDR" > "/sys/bus/i2c/devices/i2c-${name%%-*}/delete_device" 2>/dev/null
done
