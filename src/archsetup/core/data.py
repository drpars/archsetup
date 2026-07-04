"""Data-driven package definitions.

Menus are generated from TOML files under data/; adding a package to the
tool means adding a table entry here, not writing code. Category display
names come from the locale files via the key "category.<id>".
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass

from .. import paths


@dataclass(frozen=True)
class Package:
    name: str
    aur: bool = False
    default: bool = True
    note: str = ""
    condition: str | None = None


@dataclass(frozen=True)
class Category:
    id: str
    packages: tuple[Package, ...]
    condition: str | None = None


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
                note=pkg.get("note", ""),
                condition=pkg.get("condition"),
            )
            for pkg in cat.get("packages", [])
        )
        categories.append(
            Category(id=cat["id"], packages=packages, condition=cat.get("condition"))
        )
    return categories
