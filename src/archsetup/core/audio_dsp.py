"""EasyEffects speaker DSP chain (ASUS ROG Strix G513RM).

What Windows gets from the Dolby driver -- aggressive EQ for small drivers,
psychoacoustic bass, dynamic range compression and a limiter -- has no
counterpart on Linux. This task installs EasyEffects, drops in the preset
tuned for these speakers and enables a user service that swaps the preset
when the active output port changes.

The hardware itself is fine: the ALC294 pins are driven correctly and there
is no CS35L41 smart amp, so none of the hda-verb / model= quirks that other
2021+ ASUS laptops need apply here.

EasyEffects' own autoload cannot be used: its data model keys on device +
profile, and on this laptop the speakers and the headphone jack share both
the sink and the profile (analog-stereo) -- only the port differs. The
ee-port-watch script closes that gap by watching the port directly.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .. import paths
from . import hardware, i18n, pacman, prompt, services

t = i18n.t

# libpulse ships pactl, which the watcher uses to read the active port.
REPO_PACKAGES = ("easyeffects", "lsp-plugins", "calf", "alsa-utils", "libpulse")

BOARD = "G513RM"

ASSETS = paths.DATA_DIR / "audio"

HOME = Path.home()
PRESET_DIR = HOME / ".local/share/easyeffects/output"
BIN_DIR = HOME / ".local/bin"
UNIT_DIR = HOME / ".config/systemd/user"
AUTOSTART_DIR = HOME / ".config/autostart"

PRESETS = ("ROG-G513RM.json", "Flat.json")
WATCHER = "ee-port-watch"
UNIT = "ee-port-watch.service"
DESKTOP = "easyeffects-service.desktop"


def _install(name: str, dest_dir: Path, mode: int = 0o644) -> int:
    src = ASSETS / name
    dst = dest_dir / name
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        dst.chmod(mode)
    except OSError as exc:
        print(t("audio.copy_failed", path=dst, error=exc))
        return 1
    print(f"{dst}")
    return 0


def configure() -> int:
    # The preset is voiced for this laptop's drivers and enclosure; elsewhere
    # it is a starting point at best, so make that an explicit choice.
    if not hardware.board_matches(BOARD):
        print(t("audio.other_board", board=BOARD))
        if not prompt.ask_yes(t("audio.continue_q")):
            print(t("msg.cancelled"))
            return 0

    rc = pacman.install([*REPO_PACKAGES], [])
    if not pacman.is_installed("easyeffects"):
        print(t("audio.missing"))
        return 1

    for name in PRESETS:
        rc |= _install(name, PRESET_DIR)
    rc |= _install(WATCHER, BIN_DIR, 0o755)
    rc |= _install(UNIT, UNIT_DIR)
    rc |= _install(DESKTOP, AUTOSTART_DIR)

    rc |= services.enable_user_now(UNIT)

    print(t("audio.done"))
    return rc
