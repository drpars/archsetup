"""Runtime environment detection: live ISO vs installed system, root vs user."""

from __future__ import annotations

import os
from pathlib import Path


def is_archiso() -> bool:
    return Path("/run/archiso").exists()


def is_root() -> bool:
    return os.geteuid() == 0


def mode() -> str:
    return "installer" if is_archiso() else "postinstall"
