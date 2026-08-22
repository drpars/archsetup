"""Named maintenance tasks.

Each task is a plain function returning an exit code, so it can run both
headlessly (`./archsetup <task-id>`) and from the TUI (terminal suspended).
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import (
    asus,
    audio_dsp,
    bootloader,
    coding_agents,
    coredump,
    dotfiles,
    ethernet_pm,
    gitid,
    gpuconfig,
    hibernate,
    i18n,
    iwd,
    kms_hook,
    kmscon,
    mirrors,
    network,
    nvidia_laptop,
    pacman,
    sddm,
    ssh,
    trim,
    uki,
    virt,
    waydroid,
    wifi_power_save,
    writeback,
)
from .pacman import run

t = i18n.t

PACMAN_LOCK = Path("/var/lib/pacman/db.lck")
MIRRORLIST = Path("/etc/pacman.d/mirrorlist")


def system_update() -> int:
    return run(["sudo", "pacman", "-Syu"])


def clean_orphans() -> int:
    orphans = pacman.query(["pacman", "-Qqtd"])
    if not orphans:
        print(t("msg.no_orphans"))
        return 0
    return run(["sudo", "pacman", "-Rns", *orphans])


def clean_cache() -> int:
    return run(["sudo", "pacman", "-Sc"])


def update_keyring() -> int:
    return run(["sudo", "pacman", "-S", "--needed", "archlinux-keyring"])


def refresh_keys() -> int:
    return run(["sudo", "pacman-key", "--refresh-keys"])


def _edit(path: str) -> int:
    editor = os.environ.get("EDITOR") or (
        "nvim" if shutil.which("nvim") else "nano"
    )
    return run(["sudo", editor, path])


def edit_pacman_conf() -> int:
    return _edit("/etc/pacman.conf")


def edit_mirrorlist() -> int:
    return _edit(str(MIRRORLIST))


def reflector_mirrors() -> int:
    """Rank the mirrors again with reflector.

    The installer runs this once on the live ISO, but mirrors go stale:
    a country's fastest mirror at install time can be the one timing out
    six months later, and that shows up as pacman "hanging" rather than
    as an error pointing at the mirrorlist.
    """
    if shutil.which("reflector") is None:
        if run(["sudo", "pacman", "-S", "--needed", "reflector"]) != 0:
            return 1

    try:
        country = input(f"{t('msg.reflector_country_q')} ").strip()
    except EOFError:
        country = ""

    # A bad mirrorlist stops every pacman call, including the one that
    # would reinstall reflector — so keep the working copy.
    if MIRRORLIST.is_file():
        run(["sudo", "cp", str(MIRRORLIST), f"{MIRRORLIST}.bak"])
        print(t("msg.reflector_backup", path=f"{MIRRORLIST}.bak"))

    # Thorough here: this is run deliberately, and often before a large
    # update, so timing the candidates earns its cost back.
    return mirrors.rank(run, str(MIRRORLIST), country, thorough=True, sudo=True)


def install_yay() -> int:
    return pacman.install_from_aur_git(pacman.YAY_BIN)


def install_paru() -> int:
    return pacman.install_from_aur_git("https://aur.archlinux.org/paru-bin.git")


def bat_cache() -> int:
    if shutil.which("bat") is None:
        print(t("msg.bat_missing"))
        return 1
    return run(["bat", "cache", "--build"])


def remove_db_lock() -> int:
    if not PACMAN_LOCK.exists():
        print(t("msg.no_db_lock"))
        return 0
    return run(["sudo", "rm", str(PACMAN_LOCK)])


@dataclass(frozen=True)
class Task:
    id: str
    key: str  # locale key for the title; "<key>_desc" is the description
    fn: Callable[[], int]
    group: str = "update"  # which menu the task appears in


TASKS: tuple[Task, ...] = (
    Task("system-update", "task.system_update", system_update),
    Task("clean-orphans", "task.clean_orphans", clean_orphans),
    Task("clean-cache", "task.clean_cache", clean_cache),
    Task("update-keyring", "task.update_keyring", update_keyring),
    Task("refresh-keys", "task.refresh_keys", refresh_keys),
    Task("edit-pacman-conf", "task.edit_pacman_conf", edit_pacman_conf),
    Task("edit-mirrorlist", "task.edit_mirrorlist", edit_mirrorlist),
    Task("reflector", "task.reflector", reflector_mirrors),
    Task("install-yay", "task.install_yay", install_yay),
    Task("install-paru", "task.install_paru", install_paru),
    Task("remove-db-lock", "task.remove_db_lock", remove_db_lock),
    Task(
        "nvidia-modules",
        "task.nvidia_modules",
        gpuconfig.configure_nvidia_modules,
        group="drivers",
    ),
    Task(
        "amd-modules",
        "task.amd_modules",
        gpuconfig.configure_amd_modules,
        group="drivers",
    ),
    Task(
        "nvidia-kms-hook",
        "task.nvidia_kms_hook",
        kms_hook.configure,
        group="drivers",
    ),
    Task("asus-tools", "task.asus_tools", asus.install, group="drivers"),
    Task(
        "nvidia-laptop-power",
        "task.nvidia_laptop_power",
        nvidia_laptop.configure,
        group="drivers",
    ),
    Task(
        "nvidia-sleep",
        "task.nvidia_sleep",
        nvidia_laptop.enable_sleep_services,
        group="drivers",
    ),
    Task(
        "swap-hibernate",
        "task.swap_hibernate",
        hibernate.configure,
        group="system",
    ),
    Task(
        "swap-resize",
        "task.swap_resize",
        hibernate.resize,
        group="system",
    ),
    Task(
        "swap-remove",
        "task.swap_remove",
        hibernate.remove,
        group="system",
    ),
    Task(
        "nvim-dotfiles",
        "task.nvim_dotfiles",
        dotfiles.install_nvim,
        group="dotfiles",
    ),
    Task(
        "nvim-remove",
        "task.nvim_remove",
        dotfiles.remove_nvim,
        group="dotfiles",
    ),
    Task(
        "wallpapers",
        "task.wallpapers",
        dotfiles.install_wallpapers,
        group="dotfiles",
    ),
    Task("sddm-silent", "task.sddm_silent", sddm.install_silent, group="appearance"),
    Task("kmscon", "task.kmscon", kmscon.install, group="appearance"),
    Task(
        "network-sharing",
        "task.network_sharing",
        network.configure,
        group="network",
    ),
    Task(
        "wait-online-timeout",
        "task.wait_online_timeout",
        network.wait_online_timeout,
        group="network",
    ),
    Task(
        "ethernet-runtime-pm",
        "task.ethernet_pm",
        ethernet_pm.configure,
        group="network",
    ),
    Task(
        "ethernet-runtime-pm-off",
        "task.ethernet_pm_off",
        ethernet_pm.disable,
        group="network",
    ),
    Task(
        "wifi-power-save-off",
        "task.wifi_power_save_off",
        wifi_power_save.turn_off,
        group="network",
    ),
    Task(
        "wifi-power-save-on",
        "task.wifi_power_save_on",
        wifi_power_save.turn_on,
        group="network",
    ),
    Task("virt-config", "task.virt_config", virt.configure, group="virt"),
    Task("vfioctl", "task.vfioctl", virt.install_vfioctl, group="virt"),
    Task(
        "waydroid-setup",
        "task.waydroid_setup",
        waydroid.setup,
        group="virt",
    ),
    Task("audio-dsp", "task.audio_dsp", audio_dsp.configure, group="drivers"),
    Task(
        "coredump-cap",
        "task.coredump_cap",
        coredump.configure,
        group="system",
    ),
    Task(
        "ssd-trim",
        "task.ssd_trim",
        trim.configure,
        group="system",
    ),
    Task(
        "dirty-writeback",
        "task.dirty_writeback",
        writeback.configure,
        group="system",
    ),
    Task(
        "iwd-netconfig",
        "task.iwd_netconfig",
        iwd.configure,
        group="network",
    ),
    Task("ssh-status", "task.ssh_status", ssh.status, group="ssh"),
    Task("ssh-harden", "task.ssh_harden", ssh.harden, group="ssh"),
    Task("ssh-identity", "task.ssh_identity", ssh.identity, group="ssh"),
    Task("ssh-rotate", "task.ssh_rotate", ssh.rotate, group="ssh"),
    Task("ssh-authorize", "task.ssh_authorize", ssh.authorize, group="ssh"),
    Task("ssh-forget", "task.ssh_forget", ssh.forget, group="ssh"),
    Task("git-identity", "task.git_identity", gitid.configure, group="ssh"),
    Task(
        "claude-code",
        "task.claude_code",
        coding_agents.install_claude_code,
        group="agents",
    ),
    Task(
        "codewhale",
        "task.codewhale",
        coding_agents.install_codewhale,
        group="agents",
    ),
    Task("bat-cache", "task.bat_cache", bat_cache, group="config"),
    Task(
        "uki-preset",
        "task.uki_preset",
        uki.configure,
        group="system",
    ),
    Task(
        "bootloader-info",
        "task.bootloader_info",
        bootloader.info,
        group="system",
    ),
)


def get(task_id: str) -> Task | None:
    for task in TASKS:
        if task.id == task_id:
            return task
    return None
