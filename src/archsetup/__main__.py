"""Command line entry point.

    archsetup                   interactive TUI
    archsetup --list            list headless tasks
    archsetup <task-id>         run a single task without the TUI
    archsetup --lang en         override the interface language
    archsetup --check-packages  audit data/ against the sync databases
"""

from __future__ import annotations

import argparse
import importlib
import os
import subprocess
import sys

from .core import config, env, i18n


# Terminals that always support 24-bit color, recognized by TERM name.
_TRUECOLOR_TERMS = ("xterm-kitty", "kitty", "alacritty", "wezterm", "foot", "ghostty")


def _fix_terminal_env() -> None:
    """Repair TERM/COLORTERM before the TUI starts.

    ssh forwards the client's TERM (e.g. xterm-kitty) but not COLORTERM.
    Without it the renderer falls back to 256-color quantization, where
    dark tones land on palette indexes 16-17 — which themed terminals
    (kitty tokyonight, for one) remap to bright orange/red, washing out
    the whole screen. Declare truecolor for terminals known to have it,
    and swap TERMs missing from the host's terminfo for xterm-256color.
    """
    term = os.environ.get("TERM", "")
    if not term:
        return

    if os.environ.get("COLORTERM", "").lower() not in ("truecolor", "24bit"):
        if term.startswith(_TRUECOLOR_TERMS):
            os.environ["COLORTERM"] = "truecolor"

    try:
        known = subprocess.run(
            ["infocmp", term], capture_output=True
        ).returncode == 0
    except OSError:
        known = True
    if not known:
        os.environ["TERM"] = "xterm-256color"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="archsetup",
        description="Interactive Arch Linux install & post-install tool",
    )
    parser.add_argument("task", nargs="?", help="task id to run headlessly")
    parser.add_argument("--list", action="store_true", dest="list_tasks",
                        help="list available tasks")
    parser.add_argument("--check-packages", action="store_true",
                        dest="check_packages",
                        help="check every package name in data/ against the "
                             "sync databases")
    parser.add_argument("--lang", help="interface language (tr, en, ...)")
    parser.add_argument("--installer", action="store_true",
                        help="force installer (live ISO) mode")
    return parser.parse_args(argv)


def _import_textual() -> bool:
    try:
        import textual  # noqa: F401

        return True
    except ModuleNotFoundError:
        return False


def _ensure_textual_live() -> bool:
    """On the live ISO, install python-textual on first run."""
    if _import_textual():
        return True
    if subprocess.call(
        ["pacman", "-Sy", "--needed", "--noconfirm", "python-textual"]
    ) != 0:
        return False
    importlib.invalidate_caches()
    return _import_textual()


def _ensure_textual(t) -> bool:
    """Install python-textual on the running system instead of only naming it.

    The dependency is one package in the official repositories, so telling
    the user to go and install it by hand is a step nobody benefits from.
    It still asks first: this is the one place archsetup would touch the
    system before the user has chosen any task.
    """
    if _import_textual():
        return True
    print(t("msg.textual_missing"), file=sys.stderr)

    from .core.prompt import ask_yes

    if not ask_yes(t("msg.textual_install_q")):
        return False
    if subprocess.call(
        ["sudo", "pacman", "-S", "--needed", "python-textual"]
    ) != 0:
        return False
    # The package landed in a site-packages directory that was already on
    # sys.path when this process started, so the import cache has to go.
    importlib.invalidate_caches()
    if _import_textual():
        return True
    print(t("msg.textual_still_missing"), file=sys.stderr)
    return False


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _fix_terminal_env()

    conf = config.load()
    lang = args.lang or conf.get("language")
    i18n.load(lang or i18n.FALLBACK_LANG)
    t = i18n.t

    from .core import tasks

    if args.check_packages:
        # Deliberately before the installer/root branches: this only reads the
        # sync databases, so it is safe to run as anyone, anywhere.
        from .core import pkgaudit

        return pkgaudit.run()

    if args.list_tasks:
        width = max(len(task.id) for task in tasks.TASKS)
        for task in tasks.TASKS:
            print(f"{task.id:{width}} {t(task.key)}")
        return 0

    if env.is_archiso() or args.installer:
        if env.is_archiso():
            if not env.is_root():
                print(t("inst.need_root"), file=sys.stderr)
                return 1
            if not _ensure_textual_live():
                print(t("msg.textual_missing"), file=sys.stderr)
                return 1
        from .ui.app import RESTART, ArchSetupApp

        app = ArchSetupApp(ask_language="language" not in conf, installer=True)
        if app.run() == RESTART:
            os.execv(sys.executable, [sys.executable, sys.argv[0], *sys.argv[1:]])
        return 0

    if env.is_root():
        print(t("msg.root_forbidden"), file=sys.stderr)
        return 1

    if args.task:
        task = tasks.get(args.task)
        if task is None:
            print(t("msg.unknown_task", task=args.task), file=sys.stderr)
            return 1
        return task.fn()

    if not _ensure_textual(t):
        return 1

    from .ui.app import RESTART, ArchSetupApp

    ask_language = args.lang is None and "language" not in conf
    app = ArchSetupApp(ask_language=ask_language)
    result = app.run()
    if result == RESTART:
        os.execv(sys.executable, [sys.executable, sys.argv[0], *sys.argv[1:]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
