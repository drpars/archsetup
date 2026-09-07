"""greetd + tuigreet: two packages and the one line that joins them.

Arch's greetd package ships a working /etc/greetd/config.toml, so
``pacman -S greetd`` followed by ``systemctl enable greetd`` already boots into
a greeter -- just not this one. The shipped file runs ``agreety --cmd /bin/sh``
(measured 2026-09-07, greetd 0.10.3-2: the path is a pacman backup file and the
pristine copy still names agreety), and installing greetd-tuigreet does not
change a byte of it. That is why this module exists: the menu entry promises
tuigreet, and without the rewrite it would quietly deliver agreety instead --
a working login prompt, so nothing looks broken.

What this module deliberately does NOT write is /etc/tuigreet/config.toml. The
greeter's looks -- palette, session command, clock format -- belong to whoever
dresses the machine; here that is the dotfiles repo, whose greetd/ section
owns that file plus the console palette unit. Writing a second copy would put
one fact in two places, and this repo has already paid for that once: the live
/etc/sddm.conf drifted from dotfiles' versioned copy by three environment
variables and nobody noticed.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import i18n
from .prompt import ask_yes
from .sysedit import write_with_backup

t = i18n.t

CONFIG = Path("/etc/greetd/config.toml")

# tuigreet's default config path. It is read even when nothing points at it,
# so it decides where most of the greeter's settings come from -- see the
# command below for what that does and does not do to the flags.
TUIGREET_CONFIG = Path("/etc/tuigreet/config.toml")

# Measured, not read off --help (greetd-tuigreet 0.11.1-2, 2026-09-07): each
# flag was run through `--dump-config` with TUIGREET_CONFIG hidden behind a
# bind mount in an unprivileged mount namespace, and all four land --
# show_time = true, remember.username = true, remember.session = true,
# secret.mode = "characters".
#
# No --cmd: the session to start is the machine's business, not the
# installer's, and tuigreet already lists /usr/share/wayland-sessions and
# /usr/share/xsessions behind F2. --remember-session makes that choice stick.
GREETER_CMD = "tuigreet --remember --remember-session --time --asterisks"

CONFIG_TEMPLATE = f"""# /etc/greetd/config.toml -- written by archsetup.
#
# tuigreet is a TTY greeter: no X server, no EGL/GLX/VA-API, no QML, and so no
# code path that opens /dev/nvidia*. On a machine whose greeter is a victim of
# the nvidia GSP-init deadlock that is the point of picking it; elsewhere it is
# simply a small greeter.

[terminal]
# greetd.service carries Conflicts=getty@tty1.service, so vt 1 is ours.
vt = 1

[default_session]
# Deliberately no --config. That one flag is what makes tuigreet drop the rest
# of the command line, and it does so without a word: measured 2026-09-07 on
# greetd-tuigreet 0.11.1-2, `--config <file> --width 42` reported the file's
# width = 80 and `--config <file> --title` reported show_title = false, both
# exit 0. Without it the same file is still read -- it is tuigreet's default
# path -- but it only supplies what the command line leaves alone: the same
# two runs without --config reported 42 and true, while window_padding still
# came from the file. So the flags below stay in force, and anything not
# named here is /etc/tuigreet/config.toml's to answer.
command = "{GREETER_CMD}"
user = "greeter"
"""

# The `command =` value of whatever is on disk. Used for one decision only --
# whether to ask before overwriting -- so a missing match falls through to
# asking, which is the safe direction.
_COMMAND_RE = re.compile(r'^\s*command\s*=\s*"(?P<cmd>[^"]*)"', re.MULTILINE)


def _current_command() -> str | None:
    try:
        text = CONFIG.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _COMMAND_RE.search(text)
    return match.group("cmd") if match else None


def configure() -> int:
    """Point greetd at tuigreet, asking first if someone else got here.

    The prompt is not the usual "file exists?" question, because this file
    always exists -- the package ships it -- and a question whose answer is
    always yes teaches people to stop reading it. It fires on the one case
    that matters: the command line names something other than the shipped
    agreety, i.e. this machine already has a greeter someone chose. dotfiles'
    `tuigreet --config /etc/tuigreet/config.toml` is exactly that case, and
    answering no is what keeps it.
    """
    current = _current_command()
    chosen_by_someone_else = (
        current is not None
        and "agreety" not in current  # the shipped default, i.e. nobody yet
        and current != GREETER_CMD  # ours already, from an earlier run
    )
    if chosen_by_someone_else and not ask_yes(
        t("greetd.overwrite_q", path=CONFIG, command=current)
    ):
        print(t("msg.cancelled"))
        return 0

    rc, _ = write_with_backup(CONFIG, CONFIG_TEMPLATE)
    if rc != 0:
        return rc

    if TUIGREET_CONFIG.is_file():
        # Not a warning: the flags just written still win for what they name.
        # It is said out loud because the greeter about to appear will not
        # look like this file alone predicts, and the next person to change
        # its colours needs to know there is a second file in play.
        print(t("greetd.tuigreet_config", path=TUIGREET_CONFIG))
    else:
        print(t("greetd.done", path=CONFIG))
    return 0
