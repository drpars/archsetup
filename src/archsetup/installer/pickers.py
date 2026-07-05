"""List sources for installer pick screens (keymaps, locales, timezones).

Data is read from the target (/mnt) when it exists, otherwise from the
live environment — matching where the setting will be applied.
"""

from __future__ import annotations

import re
from pathlib import Path

MNT = Path("/mnt")


def _prefer_mnt(relative: str) -> Path:
    target = MNT / relative
    return target if target.exists() else Path("/") / relative


def keymaps() -> list[str]:
    base = _prefer_mnt("usr/share/kbd/keymaps")
    names = {
        path.name.removesuffix(".map.gz")
        for path in base.rglob("*.map.gz")
    }
    return sorted(names)


def locales() -> list[str]:
    """UTF-8 locales offered by locale.gen (so uncommenting always works)."""
    locale_gen = _prefer_mnt("etc/locale.gen")
    try:
        text = locale_gen.read_text(encoding="utf-8")
    except OSError:
        return []
    found = re.findall(r"^#?\s*([A-Za-z_@0-9]+)\.UTF-8 UTF-8", text, re.MULTILINE)
    return sorted(set(found))


def timezone_regions() -> list[str]:
    zoneinfo = _prefer_mnt("usr/share/zoneinfo")
    return sorted(
        entry.name
        for entry in zoneinfo.iterdir()
        if entry.is_dir() and entry.name not in ("posix", "right")
        and entry.name[0].isupper()
    )


def timezone_cities(region: str) -> list[str]:
    base = _prefer_mnt("usr/share/zoneinfo") / region
    return sorted(
        str(path.relative_to(base))
        for path in base.rglob("*")
        if path.is_file()
    )
