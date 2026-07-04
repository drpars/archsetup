"""Textual application shell.

Anything that must talk to the real terminal (pacman, editors, makepkg)
runs inside app.suspend(); the TUI redraws afterwards. Language changes
save the config and exit with RESTART so the launcher re-execs the
process with fresh strings.
"""

from __future__ import annotations

from typing import Callable

from textual.app import App

from ..core import i18n, pacman
from ..core.tasks import Task
from . import screens

t = i18n.t

RESTART = "__restart__"


class ArchSetupApp(App):
    CSS = """
    .screen-title {
        padding: 1 2 0 2;
        text-style: bold;
        color: $accent;
    }
    OptionList, SelectionList {
        margin: 1 2;
        height: 1fr;
        border: round $primary;
    }
    OptionList:focus, SelectionList:focus {
        border: round $accent;
    }
    """

    def __init__(self, ask_language: bool = False) -> None:
        super().__init__()
        self._ask_language = ask_language

    def on_mount(self) -> None:
        self.title = t("app.title")
        self.push_screen(screens.make_main_menu())
        if self._ask_language:
            self.push_screen(screens.LanguageScreen())

    def run_in_terminal(self, fn: Callable[[], int]) -> None:
        """Suspend the TUI, run fn in the real terminal, report the result."""
        with self.suspend():
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
