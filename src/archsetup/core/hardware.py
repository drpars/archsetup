"""Hardware detection used by data-file conditions like "gpu:amd"."""

from __future__ import annotations

import subprocess
from functools import cache
from pathlib import Path

BOARD_NAME = Path("/sys/class/dmi/id/board_name")
CHASSIS_TYPE = Path("/sys/class/dmi/id/chassis_type")

# SMBIOS chassis type numbers that mean "portable". 8 Portable, 9 Laptop,
# 10 Notebook, 11 Hand Held, 14 Sub Notebook, 30 Tablet, 31 Convertible,
# 32 Detachable. Used only as a fallback -- hostnamectl already folds these
# into a word, but it is not there on a live ISO without systemd running.
_PORTABLE_TYPES = {"8", "9", "10", "11", "14", "30", "31", "32"}


@cache
def _lspci() -> str:
    try:
        out = subprocess.run(
            ["lspci"], capture_output=True, text=True, timeout=10
        )
        return out.stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


@cache
def _cpuinfo() -> str:
    try:
        return Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _gpu_lines() -> list[str]:
    return [
        line
        for line in _lspci().splitlines()
        if any(tag in line for tag in ("VGA", "3D", "Display"))
    ]


def gpu_matches(query: str) -> bool:
    return any(query.lower() in line.lower() for line in _gpu_lines())


def nvidia_gpu_lines() -> list[str]:
    """GPU lspci lines belonging to NVIDIA, e.g. for reading the codename."""
    return [line for line in _gpu_lines() if "nvidia" in line.lower()]


def cpu_matches(query: str) -> bool:
    return query.lower() in _cpuinfo().lower()


def board_matches(query: str) -> bool:
    """Match against the DMI board name, e.g. "G513RM"."""
    try:
        name = BOARD_NAME.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return query.lower() in name.strip().lower()


def condition_ok(condition: str | None) -> bool:
    """Evaluate a data-file condition. Unknown kinds are treated as met."""
    if not condition:
        return True
    kind, _, value = condition.partition(":")
    if kind == "gpu":
        return gpu_matches(value)
    if kind == "cpu":
        return cpu_matches(value)
    if kind == "board":
        return board_matches(value)
    return True


@cache
def chassis() -> str:
    """"laptop", "desktop", ... — hostnamectl'in kelimesi, yoksa DMI'dan.

    Bos dize "bilinmiyor" demek; cagiran taraf bunu "masaustu" saymamali.
    """
    try:
        out = subprocess.run(
            ["hostnamectl", "chassis"], capture_output=True, text=True, timeout=10
        )
        word = out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        word = ""
    if word:
        return word

    try:
        number = CHASSIS_TYPE.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if number in _PORTABLE_TYPES:
        return "laptop"
    return "desktop" if number else ""


def is_laptop() -> bool | None:
    """True/False; sasi tespit edilemezse None.

    Uc durumu ayirmak onemli: "masaustu" ile "bilmiyorum" ayni sey degil.
    Bilinmiyorken kullaniciyi uyarmak, sessizce dizustu ayari yazmaktan da
    gorevi bosuna reddetmekten de iyidir.
    """
    word = chassis()
    if not word:
        return None
    return word in {"laptop", "convertible", "detachable", "tablet", "handset"}
