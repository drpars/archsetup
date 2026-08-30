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
            "settings", "quit",
        ]


async def test_settings_menu_holds_theme_and_language():
    """Ana menü yalnızca makineyi değiştiren maddeleri taşır.

    Dil ve tema aracın kendi ayarları (config.toml'a yazılıyor), o yüzden
    ana menüde değil Ayarlar'ın altındalar.
    """
    app = ArchSetupApp(ask_language=False)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        app.screen.query_one(OptionList).highlighted = 5  # Ayarlar
        await pilot.press("enter")
        await pilot.pause()
        assert list(app.screen._items) == ["theme", "language"]


async def test_navigation_and_package_screen():
    app = ArchSetupApp(ask_language=False)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        app.screen.query_one(OptionList).highlighted = 1  # Uygulamalar
        await pilot.press("enter")
        await pilot.pause()
        # 16 kategori, ama ikisi (virtualization + passthrough) tek satırın
        # altında: listede 15 madde görünür.
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


async def test_apps_menu_folds_the_two_virtualisation_categories():
    """İki kategori tek satırın altında toplanır ve satır ilişkiyi söyler.

    Yan yana durmaları ilişkiyi anlatmıyordu: aynı paketleri iki eksende
    listeliyorlar (genel VM kullanımı / passthrough gerekliliği) ve kardeş
    olarak okununca birbirinin alternatifi gibi görünüyorlardı.
    """
    app = ArchSetupApp(ask_language=False)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        app.screen.query_one(OptionList).highlighted = 1  # Uygulamalar
        await pilot.press("enter")
        await pilot.pause()
        ids = list(app.screen._items)
        assert "virt" in ids
        for folded in ("virtualization", "passthrough"):
            assert folded not in ids
        # Üst satır ilişkiyi taşımalı; taşımazsa seviye eklemek bedava değil.
        assert app.screen._items["virt"].desc

        app.screen.query_one(OptionList).highlighted = ids.index("virt")
        await pilot.press("enter")
        await pilot.pause()
        assert list(app.screen._items) == ["virtualization", "passthrough"]


async def test_category_note_is_readable_before_installing_anything():
    """The passthrough pointer used to be a post_msg, so it only reached
    whoever had already found the right entries. It has to be on the menu
    row and on the list screen, both before pacman runs."""
    app = ArchSetupApp(ask_language=False)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        app.screen.query_one(OptionList).highlighted = 1  # Uygulamalar
        await pilot.press("enter")
        await pilot.pause()
        app.screen.query_one(OptionList).highlighted = list(
            app.screen._items
        ).index("virt")
        await pilot.press("enter")
        await pilot.pause()
        index = list(app.screen._items).index("passthrough")
        assert "vfioctl" in app.screen._items["passthrough"].desc

        app.screen.query_one(OptionList).highlighted = index
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, screens.PackageScreen)
        subtitles = [str(s.render()) for s in app.screen.query(Static)]
        assert any("vfioctl" in text for text in subtitles)
        # Requirement axis: the screen opens with the whole set ticked.
        selection_list = app.screen.query_one(SelectionList)
        assert len(selection_list.selected) == selection_list.option_count


async def test_config_menu_is_submenus_not_a_flat_list():
    """Yapılandırma alt menülerden oluşur; düz görev listesi değil."""
    app = ArchSetupApp(ask_language=False)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        app.screen.query_one(OptionList).highlighted = 4
        await pilot.press("enter")
        await pilot.pause()
        ids = list(app.screen._items)
        assert ids[:7] == [
            "dotfiles", "ssh", "agents", "network", "appearance", "virt", "system",
        ]
        # Alt menülere taşınan görevler üst seviyede kalmamalı.
        for moved in ("swap-hibernate", "virt-config", "wallpapers", "kmscon"):
            assert moved not in ids


async def test_dotfiles_menu_holds_every_dotfiles_task():
    """core/dotfiles.py görevleri kendi alt menüsünde toplanır.

    Duvar kağıtları ve nvim görevleri bir zamanlar Yapılandırma'nın
    kökünde dururken alt menü de mevcuttu; aynı alan iki seviyeye
    bölündüğü için aranan şey bulunamıyordu.
    """
    app = ArchSetupApp(ask_language=False)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        app.screen.query_one(OptionList).highlighted = 4
        await pilot.press("enter")
        await pilot.pause()
        app.screen.query_one(OptionList).highlighted = 0  # Dotfile Yönetimi
        await pilot.press("enter")
        await pilot.pause()
        ids = list(app.screen._items)
        for task_id in ("nvim-dotfiles", "nvim-remove", "wallpapers"):
            assert task_id in ids


def test_every_task_group_is_reachable():
    """Hiçbir görev grubu menüsüz kalmamalı.

    Gruplar serbest metin; bir yazım hatası (group="netwrok") görevi
    arayüzden sessizce yok eder, hiçbir yerde hata vermez.
    """
    from archsetup.core import tasks

    reachable = {"update", "drivers", "dotfiles", "ssh", "config"}
    reachable |= {group for group, _ in screens.CONFIG_SUBMENUS}
    orphans = {task.group for task in tasks.TASKS} - reachable
    assert not orphans, f"menüye bağlanmamış grup(lar): {orphans}"


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
        # Prepare comes before partitioning: a second-hand disk should
        # enter cfdisk without a stale identity on it.
        assert ids.index("disk-prepare") < ids.index("cfdisk")
        assert ids.index("disk-erase") < ids.index("cfdisk")
        # Ek Paketler sbctl/efibootmgr/ucode getirir; chroot adımları bunlara
        # dayandığı için Sistem Yapılandırması'ndan önce gelmeli.
        assert ids.index("pacstrap") < ids.index("extras") < ids.index("target")

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


async def test_pick_screen_filter_stays_reachable():
    """Filtre yazıldıktan sonra Enter seçmeli, liste odaklıyken de yazılabilmeli.

    Eski davranış: clear_options() vurguyu None yapıyordu, Enter hiçbir şey
    seçmeden odağı listeye taşıyordu ve oradan yazmak hiçbir işe yaramıyordu
    -- kullanıcıya filtre çalışmıyormuş gibi görünüyordu.
    """
    from textual.widgets import Input

    picked = []
    app = ArchSetupApp(ask_language=False)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        app.push_screen(
            screens.PickScreen(
                "Test", ["Amsterdam", "Ankara", "Athens", "Istanbul"], picked.append
            )
        )
        await pilot.pause()
        option_list = app.screen.query_one(OptionList)

        await pilot.press("a", "n", "k")
        await pilot.pause()
        assert option_list.option_count == 1
        assert option_list.highlighted == 0  # Enter'in alacagi bir satir var

        # Odak listeye kaysa bile yazmak filtreye gitmeli
        option_list.focus()
        await pilot.pause()
        await pilot.press("backspace", "backspace", "backspace", "i", "s")
        await pilot.pause()
        assert app.screen.query_one(Input).value == "is"
        assert app.screen.query_one(Input).has_focus

        await pilot.press("enter")
        await pilot.pause()
        assert picked == ["Istanbul"]


def test_a_computed_description_is_read_when_drawn_not_when_built():
    """Satirin degeri kurulma aninda donarsa canli olmaz.

    Mekanizmanin tamami bu: MenuItem.desc cagirilabilir olabiliyor ve
    _prompt onu her cizimde cozuyor. Donmus olsaydi asagidaki ikinci
    okuma da ilkini verirdi.
    """
    state = {"v": "once"}
    item = screens.MenuItem("x", "Etiket", lambda: state["v"])
    assert "once" in screens.MenuScreen._prompt(item)
    state["v"] = "sonra"
    assert "sonra" in screens.MenuScreen._prompt(item)

    # Duz dize hala duz dize: cagirilabilir olmayan yol bozulmadi.
    assert "sabit" in screens.MenuScreen._prompt(
        screens.MenuItem("y", "Etiket", "sabit")
    )


async def test_running_a_task_rewrites_its_state_line_and_keeps_the_cursor(
    monkeypatch,
):
    """Gorev donunce satir tazelenmeli -- ve imlec yerinde kalmali.

    Ikisi tek testte, cunku ikisi tek tasarim kararinin iki yuzu:
    refresh(recompose=True) satiri tazeler ama imleci ilk satira dusurur, ve
    bu cagrinin yapildigi an kullanicinin uzerinde durdugu satiri az once
    calistirdigi andir. replace_option_prompt ikisini birden verir.

    Tazelemeyen bir arayuz, degistirmeden onceki durumu bildirir; bu deponun
    kendi cizgisiyle ayni hata: bir seyi yazan gorev onun yururlukte
    oldugunu iddia edemez.
    """
    from archsetup.core import tasks
    from archsetup.ui.app import ArchSetupApp

    state = {"v": "ONCE"}
    task = tasks.Task("fake-toggle", "task.system_update", lambda: 0,
                      group="update", state=lambda: state["v"])
    other = tasks.Task("fake-plain", "task.clean_orphans", lambda: 0,
                       group="update")
    monkeypatch.setattr(tasks, "TASKS", (other, task))

    def fake_run_task(self, tsk):
        state["v"] = "SONRA"

    monkeypatch.setattr(ArchSetupApp, "run_task", fake_run_task)

    app = ArchSetupApp(ask_language=False)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        await pilot.press("enter")  # Guncelleme menusu, ilk satir
        await pilot.pause()
        options = app.screen.query_one(OptionList)
        options.highlighted = 1  # ikinci satir: durum tasiyan gorev
        await pilot.pause()
        assert "ONCE" in str(options.get_option("fake-toggle").prompt)

        await pilot.press("enter")
        await pilot.pause()
        options = app.screen.query_one(OptionList)
        assert "SONRA" in str(options.get_option("fake-toggle").prompt)
        assert options.highlighted == 1, "imlec ilk satira dustu"


async def test_refresh_leaves_static_rows_alone(monkeypatch):
    """Sabit aciklamali satirlar her gorev kosusunda yeniden yazilmamali."""
    from archsetup.core import tasks
    from archsetup.ui.app import ArchSetupApp

    task = tasks.Task("fake-plain", "task.clean_orphans", lambda: 0,
                      group="update")
    monkeypatch.setattr(tasks, "TASKS", (task,))
    monkeypatch.setattr(ArchSetupApp, "run_task", lambda self, tsk: None)

    app = ArchSetupApp(ask_language=False)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        screen = app.screen
        options = screen.query_one(OptionList)
        touched = []
        monkeypatch.setattr(
            type(options),
            "replace_option_prompt",
            lambda self, option_id, prompt: touched.append(option_id) or self,
        )
        screen.refresh_state()
        assert touched == []


async def test_network_menu_draws_state_lines_without_touching_the_machine(
    monkeypatch,
):
    """Ag satirlari cizim aninda durum okuyor; test o okumayi makineye indirmez.

    Bu ayni zamanda conftest'teki sealed_network_state'in pozitif kontrolu:
    muhur kalkarsa bu makinede NIC bulunur ve asagidaki "cihaz yok" iddiasi
    duser. Muhursuz bir suit, altindaki donanima gore farkli cevap verirdi --
    bu deponun uc kez odedigi sizinti.
    """
    from archsetup.core import i18n, wifi_power_save
    from archsetup.ui.app import ArchSetupApp

    def explode(*args, **kwargs):
        raise AssertionError("menu cizilirken alt surec calisti")

    monkeypatch.setattr(wifi_power_save.subprocess, "run", explode)

    app = ArchSetupApp(ask_language=False)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        app.screen.query_one(OptionList).highlighted = 4  # Yapilandirma
        await pilot.press("enter")
        await pilot.pause()
        app.screen.query_one(OptionList).highlighted = 3  # Ag
        await pilot.press("enter")
        await pilot.pause()

        ids = list(app.screen._items)
        for task_id in (
            "ethernet-power-save",
            "ethernet-power-save-off",
            "wifi-power-save-off",
            "wifi-power-save-on",
        ):
            assert task_id in ids

        options = app.screen.query_one(OptionList)
        eth = str(options.get_option("ethernet-power-save").prompt)
        wifi = str(options.get_option("wifi-power-save-off").prompt)
        assert i18n.t("ethernet_pm.status_no_device") in eth
        assert i18n.t("wifi_power_save.status_no_device") in wifi
