"""Data-driven package definitions.

Menus are generated from TOML files under data/; adding a package to the
tool means adding a table entry here, not writing code. Category display
names come from the locale files via the key "category.<id>".
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass

from .. import paths
from . import i18n


@dataclass(frozen=True)
class Package:
    name: str
    aur: bool = False
    default: bool = True
    note: str = ""
    condition: str | None = None
    post_msg: str | None = None  # locale key shown after a successful install


@dataclass(frozen=True)
class Category:
    id: str
    packages: tuple[Package, ...]
    condition: str | None = None


@dataclass(frozen=True)
class DisplayManager:
    id: str
    package: str
    service: str
    aur: bool = False


def _note(raw: object) -> str:
    """Notes may be a plain string or a per-language table {tr = "...", en = "..."}."""
    if isinstance(raw, dict):
        return str(
            raw.get(i18n.current) or raw.get(i18n.FALLBACK_LANG)
            or next(iter(raw.values()), "")
        )
    return str(raw) if raw else ""


def load_categories(filename: str, section: str = "postinstall") -> list[Category]:
    path = paths.DATA_DIR / section / filename
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)

    categories: list[Category] = []
    for cat in raw.get("category", []):
        packages = tuple(
            Package(
                name=pkg["name"],
                aur=pkg.get("aur", False),
                default=pkg.get("default", True),
                note=_note(pkg.get("note")),
                condition=pkg.get("condition"),
                post_msg=pkg.get("post_msg"),
            )
            for pkg in cat.get("packages", [])
        )
        categories.append(
            Category(id=cat["id"], packages=packages, condition=cat.get("condition"))
        )
    return categories


def load_display_managers(section: str = "postinstall") -> list[DisplayManager]:
    path = paths.DATA_DIR / section / "displaymanagers.toml"
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    return [
        DisplayManager(
            id=dm["id"],
            package=dm.get("package", dm["id"]),
            service=dm["service"],
            aur=dm.get("aur", False),
        )
        for dm in raw.get("dm", [])
    ]
