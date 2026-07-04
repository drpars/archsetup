"""Mutable installer session state (selected devices, chosen kernel)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class InstallState:
    bootdev: str | None = None
    swapdev: str | None = None
    rootdev: str | None = None
    homedev: str | None = None
    kernel: str | None = None
    fs_packages: list[str] = field(default_factory=list)


state = InstallState()
