"""Locale loading and string lookup.

Strings live in locales/<code>.toml as nested tables; keys are flattened
to dot notation, e.g. [menu.main] update = "..." -> t("menu.main.update").
English is always loaded as the fallback dictionary.
"""

from __future__ import annotations

import tomllib

from .. import paths

FALLBACK_LANG = "en"

current: str = FALLBACK_LANG
_strings: dict[str, str] = {}
_fallback: dict[str, str] = {}


def _flatten(table: dict, prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in table.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten(value, full))
        else:
            flat[full] = str(value)
    return flat


def _read(lang: str) -> dict[str, str]:
    path = paths.LOCALE_DIR / f"{lang}.toml"
    with open(path, "rb") as fh:
        return _flatten(tomllib.load(fh))


def load(lang: str) -> None:
    global current, _strings, _fallback
    _fallback = _read(FALLBACK_LANG)
    try:
        _strings = _read(lang)
        current = lang
    except FileNotFoundError:
        _strings = _fallback
        current = FALLBACK_LANG


def t(key: str, **fmt: object) -> str:
    text = _strings.get(key) or _fallback.get(key) or key
    return text.format(**fmt) if fmt else text


def available() -> dict[str, str]:
    """Language code -> native display name, from each file's [meta] name."""
    langs: dict[str, str] = {}
    for path in sorted(paths.LOCALE_DIR.glob("*.toml")):
        with open(path, "rb") as fh:
            meta = tomllib.load(fh).get("meta", {})
        langs[path.stem] = meta.get("name", path.stem)
    return langs
