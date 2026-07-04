"""Hardware detection used by data-file conditions like "gpu:amd"."""

from __future__ import annotations

import subprocess
from functools import cache
from pathlib import Path


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


def gpu_matches(query: str) -> bool:
    for line in _lspci().splitlines():
        if any(tag in line for tag in ("VGA", "3D", "Display")):
            if query.lower() in line.lower():
                return True
    return False


def cpu_matches(query: str) -> bool:
    return query.lower() in _cpuinfo().lower()


def condition_ok(condition: str | None) -> bool:
    """Evaluate a data-file condition. Unknown kinds are treated as met."""
    if not condition:
        return True
    kind, _, value = condition.partition(":")
    if kind == "gpu":
        return gpu_matches(value)
    if kind == "cpu":
        return cpu_matches(value)
    return True
