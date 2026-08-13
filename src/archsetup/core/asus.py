"""ASUS ROG/TUF tooling (asusctl, rog-control-center).

The upstream moved. gitlab.com/asus-linux/asusctl is archived read-only and
its README points at the OpenGamingCollective, whose repository is [ogc];
[g14] stopped publishing on 2026-07-19 and its asusctl has been sitting on a
2026-04 build ever since, four releases behind. The packager named on every
[g14] package is an OGC member, so the quiet is a change of address rather
than abandonment -- which is also why [g14] is not treated as dead here: it
still serves linux-g14, and nobody has announced its retirement.

Order is the whole risk of the switch, not the version gap. pacman resolves a
name from the first repository that has it, regardless of version, so adding
[ogc] the way [g14] was added -- at the end of the file -- would leave asusctl
resolving from [g14] forever. core.repos owns that rule; this module only says
what has to outrank what.

supergfxctl is deliberately not part of the default set and no longer could
be: its upstream is archived too, and measured 2026-08-13 it is in neither
[g14] nor [ogc], so it is an AUR build wherever it comes from. Its successor
is cardwire, which is in [ogc] -- but cardwire hides the GPU from userspace
rather than binding it to vfio-pci, and whether that covers the passthrough
case this task's own note cites is unmeasured. So the task stays, says what
happened, and prescribes nothing.

NVIDIA laptop power management lives in core.nvidia_laptop.
"""

from __future__ import annotations

from pathlib import Path

from . import i18n, pacman, prompt, repos, services
from .pacman import run
from .sysedit import sudo_write

t = i18n.t

PACMAN_CONF = Path("/etc/pacman.conf")

REPO = repos.OGC
# [ogc] publishes asusctl and rog-control-center under the same names as
# [g14], so it has to sit above it or the older builds keep winning.
OUTRANKS = ("g14",)

ASUS_PACKAGES = ("asusctl", "rog-control-center")
REPO_PACKAGES = ("power-profiles-daemon", "switcheroo-control", "brightnessctl")
SUPERGFX_PACKAGES = ("supergfxctl",)

# service -> owning package
SERVICE_OWNERS = {
    "power-profiles-daemon": "power-profiles-daemon",
    "switcheroo-control": "switcheroo-control",
}


def _conf_text() -> str | None:
    try:
        return PACMAN_CONF.read_text(encoding="utf-8")
    except OSError:
        print(t("asus.pacman_conf_unreadable", path=PACMAN_CONF))
        return None


def has_repo(name: str = REPO.name) -> bool:
    text = _conf_text()
    return text is not None and repos.has(text, name)


def setup_repo(repo: repos.Repo = REPO) -> bool:
    """Trust the key, then write the stanza. True on success.

    The key goes first on purpose: a repository line written before its key is
    trusted makes every later `pacman -Sy` fail on an unknown signature.
    """
    rc = run(["sudo", "pacman-key", "--recv-keys", repo.key])
    rc |= run(["sudo", "pacman-key", "--lsign-key", repo.key])
    if rc != 0:
        print(t("asus.key_failed", repo=repo.name))
        return False

    text = _conf_text()
    if text is None:
        return False

    updated = repos.insert(text, repo, above=OUTRANKS)
    if updated != text and sudo_write(PACMAN_CONF, updated) != 0:
        return False
    if repos.has(text, "g14"):
        print(t("asus.above_g14"))
    return run(["sudo", "pacman", "-Suy"]) == 0


def install() -> int:
    if has_repo():
        print(t("asus.repo_found", repo=REPO.name))
        use_repo = True
    elif prompt.ask_yes(t("asus.repo_setup_q", repo=REPO.name, server=REPO.server)):
        use_repo = setup_repo()
    else:
        use_repo = False

    if use_repo:
        repo_pkgs = [*ASUS_PACKAGES, *REPO_PACKAGES]
        aur_pkgs: list[str] = []
    else:
        print(t("asus.repo_missing", repo=REPO.name))
        repo_pkgs = [*REPO_PACKAGES]
        aur_pkgs = [*ASUS_PACKAGES]

    rc = pacman.install(repo_pkgs, aur_pkgs)

    for service, package in SERVICE_OWNERS.items():
        if pacman.is_installed(package):
            rc |= services.enable(service)

    print(t("asus.nvidia_hint"))
    return rc


def install_supergfx() -> int:
    """Optional, and now on borrowed time: upstream archived the project.

    This used to install from [g14] when that repository was configured. It
    could not have worked: measured 2026-08-13, supergfxctl is in neither
    [g14] nor [ogc], so the repository path was a `pacman -S` for a name no
    repository carries. It is an AUR build, and only that.
    """
    print(t("asus.supergfx_note"))
    print(t("asus.supergfx_archived"))
    rc = pacman.install([], [*SUPERGFX_PACKAGES])
    if pacman.is_installed("supergfxctl"):
        rc |= services.enable("supergfxd")
    return rc
