"""Human-facing catalogue of what archsetup can do.

`--list` is the flat, greppable form -- one "id title" line per task, meant
for scripts and completion. This is the other half: the same tasks grouped
the way the menus group them, each with the one-line description the TUI
already shows, in the interface language.

Both are built from the task table and the locale files, so neither can
drift from reality the way a hand-written list does. That is the point of
generating it rather than writing a document: README paragraphs go stale
silently, this cannot.
"""

from __future__ import annotations

from . import i18n, tasks

t = i18n.t

# Groups in the order the TUI presents them: the main menu first
# (update, drivers), then what sits behind Configuration.
GROUP_ORDER: tuple[str, ...] = (
    "update",
    "drivers",
    "dotfiles",
    "ssh",
    "agents",
    "network",
    "appearance",
    "virt",
    "system",
    "config",
)


def _ordered_groups() -> list[str]:
    """GROUP_ORDER first, then anything it does not mention.

    A group missing from GROUP_ORDER is a bug, and tests catch it -- but
    printing it at the end beats dropping tasks out of the catalogue
    entirely, which is the kind of omission nobody notices.
    """
    known = [group for group in GROUP_ORDER if _tasks_in(group)]
    extra = sorted({task.group for task in tasks.TASKS} - set(GROUP_ORDER))
    return known + extra


def _tasks_in(group: str) -> list[tasks.Task]:
    return [task for task in tasks.TASKS if task.group == group]


def render() -> str:
    width = max(len(task.id) for task in tasks.TASKS)
    lines = [
        t("app.title"),
        "",
        t("overview.intro"),
        "",
        f"  * {t('overview.mode_installer')}",
        f"  * {t('overview.mode_postinstall')}",
        "",
        t("overview.tasks_intro"),
    ]

    for group in _ordered_groups():
        lines += ["", t(f"menu.{group}.title")]
        for task in _tasks_in(group):
            lines.append(f"  {task.id:{width}}  {t(f'{task.key}_desc')}")

    lines += [
        "",
        t("overview.footer_count", count=len(tasks.TASKS)),
        t("overview.footer_list"),
    ]
    return "\n".join(lines)


def run() -> int:
    print(render())
    return 0
