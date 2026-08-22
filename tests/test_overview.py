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


def _promises_a_bare_command(text: str) -> bool:
    """Metin, kurulu olmayan `archsetup` komutunu vaat ediyor mu.

    Araç sisteme kurulmuyor (karar 2026-08-22) — PATH'te `archsetup` diye
    bir komut yok, çalıştırma biçimi checkout'tan `./archsetup`. Aracı
    **adıyla** anan nesir doğru; kopyalanıp çalıştırılacak biçimde yazılan
    ad yanlış. İkisini ayıran şey ardından gelen argüman: bayrak,
    yer tutucu, ya da görev id'si. Argümansız tek istisna "…: archsetup"
    kalıbı, çünkü orada iki nokta zaten komut vaat ediyor.
    """
    import re

    return bool(re.search(
        r'(?<![./\w-])archsetup(?=\s+(?:--|<|[a-z]+-[a-z])|(?<=: archsetup)$)',
        text,
    ))


def test_no_user_facing_text_promises_an_uninstalled_command():
    """Bekçi: yardım metni ve locale dizgeleri `./archsetup` yazar.

    Yakaladığı hata ölçüldü (2026-08-22): `--help` epilog'u ve 16 locale
    dizgesi `archsetup --list` gibi satırlar veriyordu, `command -v
    archsetup` ise rc=1. Kopyalayan kullanıcı "command not found" alır ve
    hiçbir test kırmızıya dönmezdi.

    Aşağıdaki dört pozitif örnek korumanın sökülmüş hâlidir ve dördü de
    **ayrı şekil** — aynı şekli tekrarlayan bir küme, bir şekil bozulunca
    yeşil kalırdı (bu deponun ölçülmüş tuzağı).
    """
    assert _promises_a_bare_command("Flat list for scripts: archsetup --list")
    assert _promises_a_bare_command("run it with `archsetup <task-id>`")
    assert _promises_a_bare_command("wrong task: archsetup nvidia-sleep")
    assert _promises_a_bare_command("open the menu: archsetup")

    # Nesirde geçen ad, bir yol parçası ve düzeltilmiş biçim susmalı.
    assert not _promises_a_bare_command("archsetup installs Arch and maintains it")
    assert not _promises_a_bare_command("looks hand-written (no archsetup marker).")
    assert not _promises_a_bare_command("that file is not archsetup's")
    assert not _promises_a_bare_command("~/.config/archsetup/config.toml")
    assert not _promises_a_bare_command("Flat list for scripts: ./archsetup --list")

    root = paths.LOCALE_DIR.parent
    scanned = sorted(paths.LOCALE_DIR.glob("*.toml")) + [
        root / "src" / "archsetup" / "__main__.py",
        root / "README.md",
    ]
    offenders = [
        f"{path.name}:{no}"
        for path in scanned
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _promises_a_bare_command(line)
    ]
    assert not offenders, f"kurulu olmayan komut vaat ediliyor: {offenders}"
