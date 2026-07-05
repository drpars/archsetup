"""Textual application shell.

Anything that must talk to the real terminal (pacman, editors, makepkg)
runs inside app.suspend(); the TUI redraws afterwards. Language changes
save the config and exit with RESTART so the launcher re-execs the
process with fresh strings.
"""

from __future__ import annotations

import sys
import termios
import time
from typing import Callable

from textual.app import App
from textual.theme import Theme

from ..core import config, i18n, pacman
from ..core.tasks import Task
from . import screens

t = i18n.t

RESTART = "__restart__"

DARK_THEME = "tokyonight-night"
LIGHT_THEME = "tokyo-night-day"

# folke/tokyonight.nvim "night" palette — matches the user-side kitty and
# neovim configs; error is pure red to mirror the nvim on_colors override.
TOKYONIGHT_NIGHT = Theme(
    name=DARK_THEME,
    primary="#7aa2f7",
    secondary="#7dcfff",
    accent="#0db9d7",
    foreground="#c0caf5",
    background="#1a1b26",
    surface="#16161e",
    panel="#292e42",
    success="#9ece6a",
    warning="#e0af68",
    error="#ff0000",
    dark=True,
)

# Official Tokyo Night "day" palette (folke/tokyonight.nvim), registered as
# the light counterpart of Textual's built-in tokyo-night theme.
TOKYO_NIGHT_DAY = Theme(
    name=LIGHT_THEME,
    primary="#2e7de9",
    secondary="#007197",
    accent="#9854f1",
    foreground="#3760bf",
    background="#e1e2e7",
    surface="#d0d5e3",
    panel="#c4c8da",
    success="#587539",
    warning="#8c6c3e",
    error="#f52a65",
    dark=False,
)


class ArchSetupApp(App):
    CSS = """
    .screen-title {
        padding: 1 2 0 2;
        text-style: bold;
        color: $accent;
    }
    .screen-subtitle {
        padding: 0 2;
        color: $text-muted;
    }
    OptionList, SelectionList {
        margin: 1 2;
        height: 1fr;
        border: round $primary;
    }
    OptionList:focus, SelectionList:focus {
        border: round $accent;
    }
    /* Seçim kutucukları: seçili olan net şekilde ayrışsın */
    SelectionList > .selection-list--button {
        color: $panel;
    }
    SelectionList > .selection-list--button-highlighted {
        color: $text-muted;
    }
    SelectionList > .selection-list--button-selected,
    SelectionList > .selection-list--button-selected-highlighted {
        color: $success;
        text-style: bold;
    }
    """

    def __init__(self, ask_language: bool = False, installer: bool = False) -> None:
        super().__init__()
        self._ask_language = ask_language
        self._installer = installer

    def on_mount(self) -> None:
        self.title = t("app.title")
        self.register_theme(TOKYONIGHT_NIGHT)
        self.register_theme(TOKYO_NIGHT_DAY)
        saved = config.load().get("theme", DARK_THEME)
        if saved == "tokyo-night":  # pre-custom-theme configs
            saved = DARK_THEME
        self.theme = saved
        if self._installer:
            self.push_screen(screens.make_installer_menu())
        else:
            self.push_screen(screens.make_main_menu())
        if self._ask_language:
            self.push_screen(screens.LanguageScreen())

    def set_app_theme(self, name: str) -> None:
        self.theme = name
        conf = config.load()
        conf["theme"] = name
        config.save(conf)

    @staticmethod
    def _drain_stdin() -> None:
        """Discard pending terminal input after leaving application mode.

        Kitty-protocol key-release events queued while the TUI was active
        would otherwise be read by the next process — sudo password
        prompts in particular receive them as garbage characters.
        """
        try:
            time.sleep(0.1)
            termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
        except (OSError, termios.error, ValueError):
            pass

    def run_in_terminal(self, fn: Callable[[], int]) -> None:
        """Suspend the TUI, run fn in the real terminal, report the result."""
        with self.suspend():
            self._drain_stdin()
            print()
            rc = fn()
            print()
            try:
                input(t("ui.press_enter"))
            except EOFError:
                pass
        if rc == 0:
            self.notify(t("msg.done"))
        else:
            self.notify(t("msg.failed"), severity="error")

    def run_task(self, task: Task) -> None:
        self.run_in_terminal(task.fn)

    def install_packages(self, repo_pkgs: list[str], aur_pkgs: list[str]) -> None:
        if not repo_pkgs and not aur_pkgs:
            self.notify(t("ui.none_selected"), severity="warning")
            return
        self.run_in_terminal(lambda: pacman.install(repo_pkgs, aur_pkgs))

    def restart_for_language(self) -> None:
        self.exit(RESTART)
