"""Dotfiles management: clone/pull, copy with rsync backups, symlink, validate.

The repo lives directly in ~/.dotfiles; SECTIONS below says which of its
top-level folders are installed and where.
Copy mode mirrors items with rsync (previous versions saved under
~/Documents/dotfiles_yedek/<timestamp>); symlink mode backs up existing
targets and creates links atomically (temp link + rename).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from . import i18n
from .pacman import run
from .prompt import ask_yes

t = i18n.t

REPO_BASE = "https://github.com/drpars"
DOTFILES_DIR = Path.home() / ".dotfiles"
WALLPAPER_REPO_DIR = Path.home() / ".cache" / "archsetup" / "Wallpaper"


# Bolum -> (depodaki yol, sistemdeki hedef).
#
# Bolum adi klasor adiyla ayni olmak zorunda degil: local/ dogrudan
# eslenirse tek oge "share" olurdu ve onu baglamak ~/.local/share'in
# TAMAMINI -- her uygulamanin verisini -- depodaki uc klasorle degistirirdi.
# Bir seviye asagidan baslayinca ogeler applications/, color-schemes/,
# icons/ oluyor; secim de geri alinabilir kaliyor.
#
# Depodaki her ust klasor bir bolum degildir, bilerek:
#   browser/  Firefox profil dizini rastgele adli, sabit hedef yok
#   sddm/     /etc altina gider, root ister -- core/sddm.py'nin isi
#   windows/  baska bir isletim sistemi
#   docs/     belge, kurulacak bir sey degil
#   claude/   kendi install.sh'i var
SECTIONS: dict[str, tuple[str, Path]] = {
    "config": ("config", Path.home() / ".config"),
    "home": ("home", Path.home()),
    "local": ("local/share", Path.home() / ".local" / "share"),
}


def section_target(section: str) -> Path:
    return SECTIONS[section][1]


def section_source(section: str) -> Path:
    return DOTFILES_DIR / SECTIONS[section][0]


def ensure_repo(name: str, target: Path) -> int:
    if (target / ".git").is_dir():
        return run(["git", "-C", str(target), "pull", "--ff-only"])
    target.parent.mkdir(parents=True, exist_ok=True)
    return run(
        ["git", "clone", "--depth", "1", f"{REPO_BASE}/{name}.git", str(target)]
    )


def ensure_dotfiles_repo() -> int:
    return ensure_repo("dotfiles", DOTFILES_DIR)


def list_items(section: str) -> list[str]:
    base = section_source(section)
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir())


def _query_xdg(name: str) -> Path | None:
    try:
        out = subprocess.run(
            ["xdg-user-dir", name], capture_output=True, text=True
        )
    except OSError:
        return None
    value = out.stdout.strip()
    return Path(value) if value else None


def _create_xdg_dirs() -> bool:
    if shutil.which("xdg-user-dirs-update") is None:
        if not ask_yes(t("dotfiles.xdg_install_q")):
            return False
        if run(["sudo", "pacman", "-S", "--needed", "xdg-user-dirs"]) != 0:
            return False
    print(t("dotfiles.xdg_creating"))
    return run(["xdg-user-dirs-update"]) == 0


def _xdg_dir(name: str, fallback: str) -> Path:
    """Path of an XDG user directory, created if it is not there yet.

    On a fresh install nothing has run xdg-user-dirs-update, so
    ~/.config/user-dirs.dirs does not exist and `xdg-user-dir PICTURES`
    answers $HOME — not an error, just "no such directory configured".
    Taking that answer at face value dropped the wallpapers straight into
    ~/Wallpaper, and the dotfiles backups into ~/dotfiles_yedek.
    """
    home = Path.home()
    path = _query_xdg(name)
    if path is None or path == home:
        _create_xdg_dirs()
        path = _query_xdg(name)
    if path is None or path == home:
        path = home / fallback
    path.mkdir(parents=True, exist_ok=True)
    return path


def _backup_dir() -> Path:
    docs = _xdg_dir("DOCUMENTS", "Documents")
    backup = docs / "dotfiles_yedek" / datetime.now().strftime("%Y%m%d-%H%M%S")
    backup.mkdir(parents=True, exist_ok=True)
    return backup


def _ensure_rsync() -> int:
    if shutil.which("rsync") is None:
        return run(["sudo", "pacman", "-S", "--needed", "rsync"])
    return 0


def _rsync_cmd(source: Path, target: Path, backup: Path, dry: bool) -> list[str]:
    src = f"{source}/" if source.is_dir() else str(source)
    cmd = ["rsync", "-avh", "--backup", f"--backup-dir={backup}", "--delete"]
    if dry:
        cmd.append("--dry-run")
    return [*cmd, src, str(target)]


def copy_items(section: str, items: list[str]) -> int:
    if _ensure_rsync() != 0:
        return 1

    target_base = section_target(section)
    backup = _backup_dir()

    for item in items:
        print(f"─── {item} ───")
        run(_rsync_cmd(section_source(section) / item, target_base / item, backup, True))
    if not ask_yes(t("dotfiles.apply_q", backup=backup)):
        print(t("msg.cancelled"))
        return 0

    rc = 0
    for item in items:
        target = target_base / item
        if target.is_symlink():
            print(t("dotfiles.removing_link", target=target))
            target.unlink()
        rc |= run(_rsync_cmd(section_source(section) / item, target, backup, False))
    return rc


def symlink_items(section: str, items: list[str]) -> int:
    target_base = section_target(section)
    backup = _backup_dir()

    for item in items:
        source = section_source(section) / item
        target = target_base / item
        if target.exists() or target.is_symlink():
            print(t("dotfiles.backing_up", target=target))
            shutil.move(str(target), str(backup / item))
        target.parent.mkdir(parents=True, exist_ok=True)

        temp = target.parent / f"{target.name}.newlink"
        if temp.exists() or temp.is_symlink():
            temp.unlink()
        os.symlink(source, temp)
        os.replace(temp, target)
        print(f"{target} → {source}")
    return validate_items(section, items)


def validate_items(section: str, items: list[str]) -> int:
    target_base = section_target(section)
    broken = []
    rc = 0

    for item in items:
        target = target_base / item
        if not target.is_symlink():
            print(t("dotfiles.not_symlink", target=target))
            rc = 1
        elif not Path(os.path.realpath(target)).exists():
            broken.append(f"{target} → {os.readlink(target)}")
            rc = 1

    if broken:
        print(t("dotfiles.broken_links"))
        for link in broken:
            print(f"  - {link}")
    elif rc == 0:
        print(t("dotfiles.links_ok"))
    return rc


def _wallpaper_sources() -> list[Path]:
    """Top-level folders of the repo — Icons/, Wallpaper/, ... — not files.

    The repo mirrors the pictures directory rather than holding a flat
    pile of images, so its root is the folder layout itself.
    """
    return sorted(
        path
        for path in WALLPAPER_REPO_DIR.iterdir()
        if path.is_dir() and path.name != ".git"
    )


def _rsync_wallpapers(source: Path, target: Path, dry: bool) -> list[str]:
    cmd = ["rsync", "-avh", "--delete", "--exclude=.git"]
    if dry:
        cmd += ["--dry-run", "--itemize-changes"]
    return [*cmd, f"{source}/", str(target)]


def install_wallpapers() -> int:
    """Mirror drpars/Wallpaper into the XDG pictures directory.

    Each top-level folder of the repo is synced onto its own counterpart,
    rather than the repo root onto the pictures directory in one go. Two
    things fall out of that:

    * The layout stays flat. Syncing the root into Pictures/Wallpaper is
      what produced Pictures/Wallpaper/Wallpaper — the repo already
      contains the Wallpaper folder, so the destination added a level.
    * --delete only ever looks inside a folder the repo owns. A directory
      that exists only locally — ScreenShot/, say — is never a deletion
      candidate for the crime of not being in the repo, which it would be
      if --delete ran against the pictures directory itself.
    """
    if _ensure_rsync() != 0:
        return 1
    if ensure_repo("Wallpaper", WALLPAPER_REPO_DIR) != 0:
        return 1

    sources = _wallpaper_sources()
    if not sources:
        print(t("dotfiles.wallpapers_empty", path=WALLPAPER_REPO_DIR))
        return 1

    pictures = _xdg_dir("PICTURES", "Pictures")
    print(t("dotfiles.wallpapers_folders", names=", ".join(p.name for p in sources)))
    for source in sources:
        run(_rsync_wallpapers(source, pictures / source.name, dry=True))

    if not ask_yes(t("dotfiles.wallpapers_q", target=pictures)):
        print(t("msg.cancelled"))
        return 0

    rc = 0
    for source in sources:
        target = pictures / source.name
        target.mkdir(parents=True, exist_ok=True)
        rc |= run(_rsync_wallpapers(source, target, dry=False))
    return rc


def install_nvim() -> int:
    nvim_config = Path.home() / ".config" / "nvim"
    rc = ensure_repo("nvim", nvim_config)
    if rc != 0:
        return rc
    if ask_yes(t("dotfiles.nvim_root_q")):
        rc |= run(["sudo", "mkdir", "-p", "/root/.config"])
        rc |= run(["sudo", "ln", "-sfn", str(nvim_config), "/root/.config/nvim"])
    return rc


def remove_nvim() -> int:
    if not ask_yes(t("dotfiles.nvim_remove_q")):
        print(t("msg.cancelled"))
        return 0
    for directory in (
        Path.home() / ".config" / "nvim",
        Path.home() / ".local" / "share" / "nvim",
        Path.home() / ".cache" / "nvim",
    ):
        shutil.rmtree(directory, ignore_errors=True)
        print(t("dotfiles.removed", path=directory))
    return 0
