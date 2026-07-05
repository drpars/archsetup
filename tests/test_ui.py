"""Textual pilot tests: menu structure and theme handling."""

import pytest
from textual.widgets import OptionList, SelectionList, Static

from archsetup.ui import screens
from archsetup.ui.app import ArchSetupApp


@pytest.fixture(autouse=True)
def _config(isolated_config):
    return isolated_config


async def test_main_menu_structure():
    app = ArchSetupApp(ask_language=False)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, screens.MainMenuScreen)
        assert list(app.screen._items) == [
            "update", "apps", "drivers", "desktops", "config",
            "theme", "language", "quit",
        ]


async def test_navigation_and_package_screen():
    app = ArchSetupApp(ask_language=False)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        app.screen.query_one(OptionList).highlighted = 1  # Uygulamalar
        await pilot.press("enter")
        await pilot.pause()
        assert len(app.screen._items) == 15
        await pilot.press("enter")  # ilk kategori (console)
        await pilot.pause()
        assert isinstance(app.screen, screens.PackageScreen)
        selection_list = app.screen.query_one(SelectionList)
        assert selection_list.option_count > 0

        # Sayaç ilk durumda varsayılan seçimleri göstermeli
        counter = app.screen.query_one("#count", Static)
        expected = f"{len(selection_list.selected)}/{selection_list.option_count} seçili"
        assert str(counter.render()) == expected

        # 'a' tümünü seçer, sayaç güncellenir; ikinci 'a' tümünü bırakır
        await pilot.press("a")
        await pilot.pause()
        assert len(selection_list.selected) == selection_list.option_count
        assert str(counter.render()).startswith(f"{selection_list.option_count}/")
        await pilot.press("a")
        await pilot.pause()
        assert len(selection_list.selected) == 0

        await pilot.press("escape", "escape")
        await pilot.pause()
        assert isinstance(app.screen, screens.MainMenuScreen)


async def test_config_menu_tasks():
    app = ArchSetupApp(ask_language=False)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        app.screen.query_one(OptionList).highlighted = 4
        await pilot.press("enter")
        await pilot.pause()
        ids = list(app.screen._items)
        assert ids[0] == "dotfiles" and "swap-hibernate" in ids
        assert "virt-config" in ids and "waydroid-setup" in ids


async def test_theme_default_and_switch():
    app = ArchSetupApp(ask_language=False)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        assert app.theme == "tokyonight-night"
        app.set_app_theme("tokyo-night-day")
        assert app.theme == "tokyo-night-day"
        from archsetup.core import config

        assert config.load()["theme"] == "tokyo-night-day"


async def test_installer_menu_structure():
    app = ArchSetupApp(ask_language=False, installer=True)
    async with app.run_test(size=(110, 45)) as pilot:
        await pilot.pause()
        ids = list(app.screen._items)
        assert ids[:3] == ["keymap", "reflector", "parallel"]
        assert "pacstrap" in ids and "target" in ids

        app.screen.query_one(OptionList).highlighted = ids.index("target")
        await pilot.press("enter")
        await pilot.pause()
        target_ids = list(app.screen._items)
        assert "bootloader" in target_ids and "secureboot" in target_ids

        app.screen.query_one(OptionList).highlighted = target_ids.index("bootloader")
        await pilot.press("enter")
        await pilot.pause()
        assert list(app.screen._items) == ["systemd-boot", "grub", "refind"]


async def test_low_color_terminal_falls_back_to_ansi_theme(monkeypatch):
    """TERM=linux (QEMU sanal konsolu) 16 renk: özel tema okunmaz olur."""
    monkeypatch.setenv("TERM", "linux")
    monkeypatch.delenv("COLORTERM", raising=False)
    app = ArchSetupApp(ask_language=False)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        assert app.theme == "ansi-dark"
        # Tema değişimi ANSI eşleniğine gider ama tercih gerçek adla saklanır
        app.set_app_theme("tokyo-night-day")
        assert app.theme == "ansi-light"
        from archsetup.core import config

        assert config.load()["theme"] == "tokyo-night-day"


async def test_truecolor_terminal_keeps_custom_theme(monkeypatch):
    monkeypatch.setenv("TERM", "xterm-kitty")
    monkeypatch.setenv("COLORTERM", "truecolor")
    app = ArchSetupApp(ask_language=False)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        assert app.theme == "tokyonight-night"


async def test_pick_screen_filters_and_picks():
    picked = []
    app = ArchSetupApp(ask_language=False)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        app.push_screen(
            screens.PickScreen("Test", ["trq", "trf", "us", "de"], picked.append)
        )
        await pilot.pause()
        option_list = app.screen.query_one(OptionList)
        assert option_list.option_count == 4

        # 'tr' yaz -> 2 sonuç; 'trq' yaz -> tek sonuç, Enter seçer
        await pilot.press("t", "r")
        await pilot.pause()
        assert option_list.option_count == 2
        await pilot.press("q", "enter")
        await pilot.pause()
        assert picked == ["trq"]
        assert not isinstance(app.screen, screens.PickScreen)  # ekran kapandı


async def test_pick_screen_arrows_work_while_filtering():
    """Odak filtre kutusundayken ok tuşları listeyi gezdirmeli."""
    picked = []
    app = ArchSetupApp(ask_language=False)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        app.push_screen(
            screens.PickScreen("Test", ["alpha", "bravo", "charlie"], picked.append)
        )
        await pilot.pause()
        from textual.widgets import Input

        assert app.screen.query_one(Input).has_focus
        option_list = app.screen.query_one(OptionList)

        await pilot.press("down", "down")
        await pilot.pause()
        assert app.screen.query_one(Input).has_focus  # odak kutuda kaldı
        assert option_list.highlighted == 2

        # Enter, çok eşleşme varken vurgulanan öğeyi seçer
        await pilot.press("enter")
        await pilot.pause()
        assert picked == ["charlie"]


async def test_extras_screen_uses_chroot_installer():
    app = ArchSetupApp(ask_language=False, installer=True)
    async with app.run_test(size=(110, 45)) as pilot:
        await pilot.pause()
        ids = list(app.screen._items)
        app.screen.query_one(OptionList).highlighted = ids.index("extras")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, screens.PackageScreen)
        assert app.screen._install_fn is not None
        assert app.screen.query_one(SelectionList).option_count >= 20
