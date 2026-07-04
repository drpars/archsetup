"""Command line entry point.

    archsetup              interactive TUI
    archsetup --list       list headless tasks
    archsetup <task-id>    run a single task without the TUI
    archsetup --lang en    override the interface language
"""

from __future__ import annotations

import argparse
import os
import sys

from .core import config, env, i18n


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="archsetup",
        description="Interactive Arch Linux install & post-install tool",
    )
    parser.add_argument("task", nargs="?", help="task id to run headlessly")
    parser.add_argument("--list", action="store_true", dest="list_tasks",
                        help="list available tasks")
    parser.add_argument("--lang", help="interface language (tr, en, ...)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    conf = config.load()
    lang = args.lang or conf.get("language")
    i18n.load(lang or i18n.FALLBACK_LANG)
    t = i18n.t

    from .core import tasks

    if args.list_tasks:
        for task in tasks.TASKS:
            print(f"{task.id:18} {t(task.key)}")
        return 0

    if env.is_archiso():
        print(t("msg.installer_todo"))
        return 1

    if env.is_root():
        print(t("msg.root_forbidden"), file=sys.stderr)
        return 1

    if args.task:
        task = tasks.get(args.task)
        if task is None:
            print(t("msg.unknown_task", task=args.task), file=sys.stderr)
            return 1
        return task.fn()

    try:
        import textual  # noqa: F401
    except ModuleNotFoundError:
        print(t("msg.textual_missing"), file=sys.stderr)
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
