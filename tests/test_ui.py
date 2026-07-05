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
