"""Screens: generic menus built from the task registry and data files.

Menu dispatch is by stable item id — display text comes from the locale
files and never participates in program logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, OptionList, SelectionList, Static
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection

from ..core import data, dotfiles, hardware, i18n, pacman, services, tasks

t = i18n.t


@dataclass
class MenuItem:
    id: str
    label: str
    desc: str = ""
    action: Callable[["MenuScreen"], None] = field(default=lambda screen: None)


class MenuScreen(Screen):
    BINDINGS = [Binding("escape", "go_back", t("ui.back"))]

    def __init__(self, title: str, items: list[MenuItem]) -> None:
        super().__init__()
        self._menu_title = title
        self._items = {item.id: item for item in items}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(self._menu_title, classes="screen-title")
        options = [
            Option(self._prompt(item), id=item.id)
            for item in self._items.values()
        ]
        yield OptionList(*options)
        yield Footer()

    @staticmethod
    def _prompt(item: MenuItem) -> str:
        if item.desc:
            return f"{item.label}\n[dim]{item.desc}[/dim]"
        return item.label

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        item = self._items.get(event.option.id or "")
        if item is not None:
            item.action(self)

    def action_go_back(self) -> None:
        self.app.pop_screen()


class MainMenuScreen(MenuScreen):
    BINDINGS = [Binding("escape", "go_back", t("ui.quit"))]

    def action_go_back(self) -> None:
        self.app.exit()


class SelectionCountMixin:
    """Shared 'N/M selected' counter + select/deselect-all for SelectionList screens."""

    def _update_count(self) -> None:
        selection_list = self.query_one(SelectionList)
        self.query_one("#count", Static).update(
            t("ui.selected_count",
              sel=len(selection_list.selected),
              total=selection_list.option_count)
        )

    def on_mount(self) -> None:
        self._update_count()

    def on_selection_list_selected_changed(
        self, event: SelectionList.SelectedChanged
    ) -> None:
        self._update_count()

    def action_toggle_all(self) -> None:
        selection_list = self.query_one(SelectionList)
        if len(selection_list.selected) == selection_list.option_count:
            selection_list.deselect_all()
        else:
            selection_list.select_all()


class PackageScreen(SelectionCountMixin, Screen):
    BINDINGS = [
        Binding("escape", "go_back", t("ui.back")),
        Binding("i", "install", t("ui.install")),
        Binding("a", "toggle_all", t("ui.toggle_all")),
    ]

    def __init__(self, category: data.Category, install_fn=None) -> None:
        super().__init__()
        self._category = category
        self._install_fn = install_fn  # None -> host pacman via the app
        self._visible = [
            pkg for pkg in category.packages if hardware.condition_ok(pkg.condition)
        ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(
            t(f"category.{self._category.id}"), classes="screen-title"
        )
        yield Static("", id="count", classes="screen-subtitle")
        selections = [
            Selection(self._prompt(pkg), index, pkg.default)
            for index, pkg in enumerate(self._visible)
        ]
        yield SelectionList(*selections)
        yield Footer()

    @staticmethod
    def _prompt(pkg: data.Package) -> str:
        parts = [pkg.name]
        if pkg.aur:
            parts.append("[yellow](AUR)[/yellow]")
        if pkg.note:
            parts.append(f"[dim]{pkg.note}[/dim]")
        return " ".join(parts)

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_install(self) -> None:
        selected = self.query_one(SelectionList).selected
        chosen = [self._visible[index] for index in selected]
        repo_pkgs = [pkg.name for pkg in chosen if not pkg.aur]
        aur_pkgs = [pkg.name for pkg in chosen if pkg.aur]
        if self._install_fn is not None:
            if not repo_pkgs and not aur_pkgs:
                self.app.notify(t("ui.none_selected"), severity="warning")
                return
            fn = self._install_fn
            self.app.run_in_terminal(lambda: fn(repo_pkgs, aur_pkgs))
        else:
            self.app.install_packages(repo_pkgs, aur_pkgs)
        for pkg in chosen:
            if pkg.post_msg and pacman.is_installed(pkg.name):
                self.app.notify(t(pkg.post_msg), severity="warning", timeout=12)


class DotfilesScreen(SelectionCountMixin, Screen):
    BINDINGS = [
        Binding("escape", "go_back", t("ui.back")),
        Binding("c", "copy", t("ui.copy")),
        Binding("s", "symlink", t("ui.symlink")),
        Binding("v", "validate", t("ui.validate")),
        Binding("a", "toggle_all", t("ui.toggle_all")),
    ]

    def __init__(self, section: str) -> None:
        super().__init__()
        self._section = section
        self._names = dotfiles.list_items(section)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(
            f"{t(f'dotfiles.section_{self._section}')} → {dotfiles.section_target(self._section)}",
            classes="screen-title",
        )
        yield Static("", id="count", classes="screen-subtitle")
        yield SelectionList(
            *[Selection(name, index, False) for index, name in enumerate(self._names)]
        )
        yield Footer()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def _chosen(self) -> list[str]:
        selected = self.query_one(SelectionList).selected
        names = [self._names[index] for index in selected]
        if not names:
            self.app.notify(t("ui.none_selected"), severity="warning")
        return names

    def action_copy(self) -> None:
        if names := self._chosen():
            self.app.run_in_terminal(
                lambda: dotfiles.copy_items(self._section, names)
            )

    def action_symlink(self) -> None:
        if names := self._chosen():
            self.app.run_in_terminal(
                lambda: dotfiles.symlink_items(self._section, names)
            )

    def action_validate(self) -> None:
        if names := self._chosen():
            self.app.run_in_terminal(
                lambda: dotfiles.validate_items(self._section, names)
            )


def make_dotfiles_menu() -> MenuScreen:
    def open_section(screen: MenuScreen, section: str) -> None:
        if not dotfiles.DOTFILES_DIR.is_dir():
            screen.app.run_in_terminal(dotfiles.ensure_dotfiles_repo)
        if dotfiles.DOTFILES_DIR.is_dir():
            screen.app.push_screen(DotfilesScreen(section))

    items = [
        MenuItem(
            "update-repo",
            t("dotfiles.update_repo"),
            t("dotfiles.update_repo_desc"),
            lambda screen: screen.app.run_in_terminal(dotfiles.ensure_dotfiles_repo),
        ),
        MenuItem(
            "config",
            t("dotfiles.section_config"),
            str(dotfiles.section_target("config")),
            lambda screen: open_section(screen, "config"),
        ),
        MenuItem(
            "home",
            t("dotfiles.section_home"),
            str(dotfiles.section_target("home")),
            lambda screen: open_section(screen, "home"),
        ),
    ]
    return MenuScreen(t("menu.dotfiles.title"), items)


class LanguageScreen(Screen):
    BINDINGS = [Binding("escape", "go_back", t("ui.back"))]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(t("lang.select_title"), classes="screen-title")
        options = [
            Option(name, id=code) for code, name in i18n.available().items()
        ]
        yield OptionList(*options)
        yield Footer()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        from ..core import config

        lang = event.option.id or i18n.current
        conf = config.load()
        conf["language"] = lang
        config.save(conf)
        if lang != i18n.current:
            self.app.restart_for_language()
        else:
            self.app.pop_screen()


def _category_items(filename: str, screen_factory) -> list[MenuItem]:
    items = []
    for category in data.load_categories(filename):
        desc = ""
        if category.condition is not None:
            met = hardware.condition_ok(category.condition)
            desc = t("msg.detected") if met else t("msg.not_detected")
            desc = f"{category.condition} — {desc}"
        items.append(
            MenuItem(
                id=category.id,
                label=t(f"category.{category.id}"),
                desc=desc,
                action=lambda screen, cat=category: screen.app.push_screen(
                    screen_factory(cat)
                ),
            )
        )
    return items


def make_apps_menu() -> MenuScreen:
    return MenuScreen(t("menu.apps.title"), _category_items("apps.toml", PackageScreen))


def make_drivers_menu() -> MenuScreen:
    items = _category_items("drivers.toml", PackageScreen)
    items.extend(_task_items("drivers"))
    return MenuScreen(t("menu.drivers.title"), items)


def _task_items(group: str) -> list[MenuItem]:
    items = []
    for task in tasks.TASKS:
        if task.group != group:
            continue
        if task.id == "remove-db-lock" and not tasks.PACMAN_LOCK.exists():
            continue
        items.append(
            MenuItem(
                id=task.id,
                label=t(task.key),
                desc=t(f"{task.key}_desc"),
                action=lambda screen, tsk=task: screen.app.run_task(tsk),
            )
        )
    return items


def make_update_menu() -> MenuScreen:
    return MenuScreen(t("menu.update.title"), _task_items("update"))


def make_dm_menu() -> MenuScreen:
    def install_dm(screen: MenuScreen, dm: data.DisplayManager) -> None:
        def fn() -> int:
            repo = [] if dm.aur else [dm.package]
            aur = [dm.package] if dm.aur else []
            rc = pacman.install(repo, aur)
            if rc == 0:
                rc = services.enable(dm.service)
            return rc

        screen.app.run_in_terminal(fn)

    items = [
        MenuItem(
            id=dm.id,
            label=dm.package,
            desc=t(f"dm.{dm.id}_desc"),
            action=lambda screen, mgr=dm: install_dm(screen, mgr),
        )
        for dm in data.load_display_managers()
    ]
    return MenuScreen(t("menu.dm.title"), items)


def make_desktops_menu() -> MenuScreen:
    items = _category_items("desktops.toml", PackageScreen)
    items.append(
        MenuItem(
            id="displaymanager",
            label=t("menu.dm.title"),
            desc=t("menu.dm.desc"),
            action=lambda screen: screen.app.push_screen(make_dm_menu()),
        )
    )
    return MenuScreen(t("menu.desktops.title"), items)


def make_config_menu() -> MenuScreen:
    items = [
        MenuItem(
            "dotfiles",
            t("menu.dotfiles.title"),
            t("dotfiles.menu_desc"),
            lambda screen: screen.app.push_screen(make_dotfiles_menu()),
        )
    ]
    items.extend(_task_items("config"))
    return MenuScreen(t("menu.config.title"), items)


def make_theme_menu() -> MenuScreen:
    from .app import DARK_THEME, LIGHT_THEME

    def apply(screen: MenuScreen, name: str) -> None:
        screen.app.set_app_theme(name)

    items = [
        MenuItem(
            "dark",
            t("theme.dark"),
            t("theme.dark_desc"),
            lambda screen: apply(screen, DARK_THEME),
        ),
        MenuItem(
            "light",
            t("theme.light"),
            t("theme.light_desc"),
            lambda screen: apply(screen, LIGHT_THEME),
        ),
    ]
    return MenuScreen(t("menu.theme.title"), items)


# ==========================================================
# INSTALLER (LIVE ISO) MENUS
# ==========================================================


def _run_item(id_: str, label_key: str, desc: str, fn) -> MenuItem:
    return MenuItem(
        id_, t(label_key), desc,
        lambda screen: screen.app.run_in_terminal(fn),
    )


def make_bootloader_install_menu() -> MenuScreen:
    from ..installer import bootloaders

    items = [
        _run_item("systemd-boot", "inst.sdboot", "bootctl + UKI (/etc/kernel/cmdline)",
                  bootloaders.install_systemd_boot),
        _run_item("grub", "inst.grub", "grub-install + grub-mkconfig",
                  bootloaders.install_grub),
        _run_item("refind", "inst.refind", "refind-install + refind_linux.conf",
                  bootloaders.install_refind),
    ]
    return MenuScreen(t("inst.bootloader_title"), items)


def make_target_menu() -> MenuScreen:
    from ..installer import base, chroot

    items = [
        _run_item("hostname", "inst.hostname", "/etc/hostname", chroot.set_hostname),
        _run_item("vconsole", "inst.vconsole", "/etc/vconsole.conf", chroot.set_vconsole),
        _run_item("locale", "inst.locale", "/etc/locale.conf + locale-gen", chroot.set_locale),
        _run_item("timezone", "inst.timezone", "/etc/localtime + hwclock", chroot.set_timezone),
        _run_item("rootpw", "inst.rootpw", "passwd root", chroot.set_root_password),
        _run_item("adduser", "inst.adduser", "useradd + wheel", chroot.add_user),
        _run_item("swapfile", "inst.swapfile", "/swapfile", chroot.create_swapfile),
        _run_item("multilib", "inst.multilib", "pacman.conf [multilib]", base.enable_multilib),
        _run_item("g14", "inst.g14", "asus-linux [g14]", base.add_g14_repo),
        _run_item("fstab", "inst.fstab", "genfstab", base.genfstab),
        MenuItem(
            "bootloader", t("inst.bootloader_title"), "systemd-boot / GRUB / rEFInd",
            lambda screen: screen.app.push_screen(make_bootloader_install_menu()),
        ),
        _run_item("uki", "inst.uki", "mkinitcpio preset -> UKI", chroot.gen_uki),
        _run_item("secureboot", "inst.secureboot", "sbctl", chroot.setup_secure_boot),
        _run_item("watchdog", "inst.watchdog", "nowatchdog / iTCO_wdt", chroot.disable_watchdog),
        _run_item("services", "inst.services", "sshd, ağ, bluetooth (systemctl --root)",
                  chroot.enable_services),
        _run_item("edit-fstab", "inst.edit_fstab", "/mnt/etc/fstab",
                  lambda: chroot.edit_file("/mnt/etc/fstab")),
        _run_item("edit-mkinitcpio", "inst.edit_mkinitcpio", "/mnt/etc/mkinitcpio.conf",
                  lambda: chroot.edit_file("/mnt/etc/mkinitcpio.conf")),
        _run_item("edit-cmdline", "inst.edit_cmdline", "/mnt/etc/kernel/cmdline",
                  lambda: chroot.edit_file("/mnt/etc/kernel/cmdline")),
    ]
    return MenuScreen(t("inst.target_title"), items)


def make_installer_menu() -> MenuScreen:
    from ..installer import base, chroot, disk

    def extras_screen(screen: MenuScreen) -> None:
        categories = data.load_categories("extras.toml", section="install")
        screen.app.push_screen(
            PackageScreen(categories[0], install_fn=chroot.chroot_install)
        )

    items = [
        _run_item("keymap", "inst.keymap", "loadkeys", base.set_live_keymap),
        _run_item("reflector", "inst.reflector", "mirrorlist", base.run_reflector),
        _run_item("parallel", "inst.parallel", "pacman.conf", base.parallel_downloads),
        _run_item("cfdisk", "inst.cfdisk", "", disk.run_cfdisk),
        _run_item("select", "inst.select", "boot/swap/root/home", disk.select_partitions),
        _run_item("format", "inst.format", "mkfs", disk.format_devices),
        _run_item("mount", "inst.mount", "/mnt", disk.mount_all),
        _run_item("pacstrap", "inst.pacstrap", "base + kernel", base.pacstrap_base),
        MenuItem(
            "target", t("inst.target_title"), "arch-chroot /mnt",
            lambda screen: screen.app.push_screen(make_target_menu()),
        ),
        MenuItem(
            "extras", t("inst.extras"), "arch-chroot pacman",
            extras_screen,
        ),
        _run_item("unmount", "inst.unmount", "umount -R /mnt", disk.unmount_all),
        _run_item("reboot", "inst.reboot", "systemctl reboot",
                  lambda: pacman.run(["systemctl", "reboot"])),
        _run_item("poweroff", "inst.poweroff", "systemctl poweroff",
                  lambda: pacman.run(["systemctl", "poweroff"])),
    ]
    return MainMenuScreen(t("inst.title"), items)


def make_main_menu() -> MenuScreen:
    items = [
        MenuItem(
            "update",
            t("menu.main.update"),
            t("menu.main.update_desc"),
            lambda screen: screen.app.push_screen(make_update_menu()),
        ),
        MenuItem(
            "apps",
            t("menu.main.apps"),
            t("menu.main.apps_desc"),
            lambda screen: screen.app.push_screen(make_apps_menu()),
        ),
        MenuItem(
            "drivers",
            t("menu.main.drivers"),
            t("menu.main.drivers_desc"),
            lambda screen: screen.app.push_screen(make_drivers_menu()),
        ),
        MenuItem(
            "desktops",
            t("menu.main.desktops"),
            t("menu.main.desktops_desc"),
            lambda screen: screen.app.push_screen(make_desktops_menu()),
        ),
        MenuItem(
            "config",
            t("menu.main.config"),
            t("menu.main.config_desc"),
            lambda screen: screen.app.push_screen(make_config_menu()),
        ),
        MenuItem(
            "theme",
            t("menu.main.theme"),
            t("menu.main.theme_desc"),
            lambda screen: screen.app.push_screen(make_theme_menu()),
        ),
        MenuItem(
            "language",
            t("menu.main.language"),
            t("menu.main.language_desc"),
            lambda screen: screen.app.push_screen(LanguageScreen()),
        ),
        MenuItem(
            "quit",
            t("menu.main.quit"),
            t("menu.main.quit_desc"),
            lambda screen: screen.app.exit(),
        ),
    ]
    return MainMenuScreen(t("menu.main.title"), items)
