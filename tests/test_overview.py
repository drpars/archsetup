"""--overview katalogu ve locale kapsamı.

Katalog locale dosyalarından üretiliyor, yani elle yazılmış bir belgenin
aksine görev listesinden sapamaz. Buradaki testler o güvenceyi koruyor:
çeviri eksikse ya da yeni bir grup GROUP_ORDER'a yazılmadıysa kırmızı olur.
"""

import tomllib

import pytest

from archsetup import paths
from archsetup.core import i18n, overview, tasks

LOCALES = sorted(path.stem for path in paths.LOCALE_DIR.glob("*.toml"))


def _strings(lang: str) -> dict[str, str]:
    with open(paths.LOCALE_DIR / f"{lang}.toml", "rb") as fh:
        return _flatten(tomllib.load(fh))


def _flatten(table: dict, prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in table.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten(value, full))
        else:
            flat[full] = str(value)
    return flat


@pytest.mark.parametrize("lang", LOCALES)
def test_every_task_has_a_title_and_a_description(lang):
    strings = _strings(lang)
    missing = [
        key
        for task in tasks.TASKS
        for key in (task.key, f"{task.key}_desc")
        if key not in strings
    ]
    assert missing == []


@pytest.mark.parametrize("lang", LOCALES)
def test_every_task_group_has_a_menu_title(lang):
    strings = _strings(lang)
    groups = {task.group for task in tasks.TASKS}
    assert [g for g in sorted(groups) if f"menu.{g}.title" not in strings] == []


def test_group_order_covers_every_group():
    """Sırada olmayan grup katalogun sonuna düşer ama sessizce kaybolmaz."""
    groups = {task.group for task in tasks.TASKS}
    assert groups - set(overview.GROUP_ORDER) == set()


def test_render_lists_every_task_once():
    # Satırın ilk sözcüğüne bakılıyor: "kmscon" gibi bir id, kendi
    # açıklamasının içinde de geçiyor ve düz alt dize sayımı onu iki kez
    # bulup sahte hata veriyordu.
    ids = [task.id for task in tasks.TASKS]
    listed = [
        line.split()[0]
        for line in overview.render().splitlines()
        if line.startswith("  ") and line.split()[0] in ids
    ]
    assert sorted(listed) == sorted(ids)


def test_render_uses_the_selected_language():
    i18n.load("en")
    try:
        assert "Tasks --" in overview.render()
    finally:
        i18n.load("tr")
    assert "Görevler —" in overview.render()
