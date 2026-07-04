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

from ..core import data, hardware, i18n, pacman, services, tasks

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


class PackageScreen(Screen):
    BINDINGS = [
        Binding("escape", "go_back", t("ui.back")),
        Binding("i", "install", t("ui.install")),
    ]

    def __init__(self, category: data.Category) -> None:
        super().__init__()
        self._category = category
        self._visible = [
            pkg for pkg in category.packages if hardware.condition_ok(pkg.condition)
        ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(
            t(f"category.{self._category.id}"), classes="screen-title"
        )
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
        self.app.install_packages(repo_pkgs, aur_pkgs)
        for pkg in chosen:
            if pkg.post_msg and pacman.is_installed(pkg.name):
                self.app.notify(t(pkg.post_msg), severity="warning", timeout=12)


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
    return MenuScreen(t("menu.config.title"), _task_items("config"))


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
