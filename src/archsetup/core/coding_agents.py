"""Terminal coding agents, installed from their own release channels.

Neither project is in the official repositories, and both publish a shell
installer as their documented Linux path:

* Claude Code (anthropics/claude-code) marks npm **deprecated** in its
  README and gives `claude.ai/install.sh` as the recommended install.
* Codewhale (Hmbown/CodeWhale) offers npm too, but docs/INSTALL.md calls
  `codewhale.net/install.sh` the shortest install and update path on
  Linux -- and it verifies every download against a published SHA256
  list.

Both install into ~/.local/bin and neither wants root, which is also why
they are tasks rather than entries in the pacman-driven package screens.
The same script updates an existing install, so there is no separate
update path to write.

The scripts are downloaded to a file and then executed, rather than piped
from curl straight into a shell. Same source, same script -- but a piped
shell runs bytes as they arrive, so a connection that drops halfway
through executes half an installer. Writing the file first turns that
into a failed download with nothing run.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import i18n
from .pacman import run
from .prompt import ask_yes

t = i18n.t

LOCAL_BIN = Path.home() / ".local" / "bin"


@dataclass(frozen=True)
class Agent:
    id: str
    name: str
    url: str
    shell: str  # the interpreter each project documents for its own script
    command: str
    project: str


AGENTS = (
    Agent(
        "claude-code",
        "Claude Code",
        "https://claude.ai/install.sh",
        "bash",
        "claude",
        "https://github.com/anthropics/claude-code",
    ),
    Agent(
        "codewhale",
        "Codewhale",
        "https://codewhale.net/install.sh",
        "sh",
        "codewhale",
        "https://github.com/Hmbown/CodeWhale",
    ),
)


def _on_path(directory: Path) -> bool:
    entries = os.environ.get("PATH", "").split(os.pathsep)
    return str(directory) in entries


def _fetch(agent: Agent, target: Path) -> int:
    rc = run(["curl", "-fsSL", agent.url, "-o", str(target)])
    if rc != 0:
        print(t("agents.download_failed", url=agent.url))
        return rc
    if not target.is_file() or target.stat().st_size == 0:
        # curl -f reports most failures, but an empty body is a 200 too.
        print(t("agents.download_empty", url=agent.url))
        return 1
    return 0


def install(agent: Agent) -> int:
    if shutil.which("curl") is None:
        print(t("agents.no_curl"))
        return 1

    existing = shutil.which(agent.command)
    if existing:
        print(t("agents.already", name=agent.name, path=existing))
        if not ask_yes(t("agents.update_q", name=agent.name)):
            print(t("msg.cancelled"))
            return 0

    print(t("agents.installing", name=agent.name, url=agent.url))
    with tempfile.TemporaryDirectory(prefix="archsetup-") as tmp:
        script = Path(tmp) / f"{agent.id}.sh"
        rc = _fetch(agent, script)
        if rc != 0:
            return rc
        rc = run([agent.shell, str(script)])
    if rc != 0:
        return rc

    return _report(agent, existing)


def _report(agent: Agent, existing: str | None) -> int:
    installed = LOCAL_BIN / agent.command
    if not installed.exists():
        print(t("agents.not_where_expected", path=installed))
        return 1

    # An earlier npm -g install leaves a second copy on the PATH; whichever
    # directory comes first wins, so an "updated" agent can keep running
    # the old build with no sign that anything is wrong.
    if existing and Path(existing) != installed:
        print(t("agents.shadowed", old=existing, new=installed))
    if not _on_path(LOCAL_BIN):
        print(t("agents.path_missing", path=LOCAL_BIN))

    print(t("agents.done", name=agent.name, command=agent.command))
    return 0


def install_claude_code() -> int:
    return install(AGENTS[0])


def install_codewhale() -> int:
    return install(AGENTS[1])
