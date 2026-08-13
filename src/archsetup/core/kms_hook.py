"""Dropping the stock `kms` hook when all it still carries is blacklisted.

`configure_nvidia_modules` names the NVIDIA modules in MODULES, which is the
whitelist mkinitcpio upstream points at -- FS#53316 asked for blacklisted
modules to be skipped and was closed Won't fix with "if you want to slim down
your initramfs, whitelist instead". The stock `kms` hook is the other half of
that story and nothing here looked at it.

Why the blacklist does not reach the image. `install/kms` is two lines: every
module under /drivers/char/agp/ and /drivers/gpu/drm/ that the autodetect cache
mentions. That cache is not "what is loaded" -- `install/autodetect` collects
DRIVER=/MODALIAS= out of sysfs and resolves them with `modprobe -qaR`, and an
NVIDIA card's PCI modalias resolves to nouveau just as much as to nvidia.
`modprobe`'s blacklist flag does not help: measured on kmod 34.2, `-b` changes
nothing under `--resolve-alias` (it only bites under `--show-depends`), and
`add_module` then pulls the module's declared firmware in unconditionally.
mkinitcpio 41.1 offers no MODULES exclusion of any kind, so there is no knob
that keeps nouveau out while `kms` is in HOOKS.

What that costs, and what it does not. On an NVIDIA box whose kernel ships
nouveau the image carries nouveau plus roughly a hundred megabytes of firmware
for a driver that nvidia-utils blacklists and that therefore never loads. The
cost is size. It is *not* early loading: `modconf` copies /usr/lib/modprobe.d
into the image, so the blacklist travels with it. Arch's NVIDIA page suggests
this same removal and words it as keeping the kernel from loading nouveau
early; on a modconf system that half is already true, so this task is written
around the bytes and says so.

What it deliberately does not do is guess. The set `kms` contributes is
computable -- the agp+drm module pool intersected with the autodetect cache --
so it is computed per installed kernel instead of reading GPU names off lspci,
where i915 and xe are indistinguishable. If that set holds anything which is
neither blacklisted nor already in MODULES, removing the hook would lose a
driver, and the task refuses and names the task that owns it.

Undo: put `kms` back into HOOKS and run `sudo mkinitcpio -P`. The configuration
is backed up first under a dated name rather than the sibling `.bak`, which is
already taken on at least one machine here by an unrelated edit. The images
about to be overwritten are copied to /var/lib/archsetup, deliberately not
beside themselves on the ESP: systemd-boot enumerates /EFI/Linux, so a copy
left there risks becoming a second, stale boot entry -- the same trap the
libvirt hook directory sprang in 2026-08.
"""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

from . import hardware, i18n, mkinitcpio, pacman, secureboot, sysedit
from .pacman import run
from .prompt import ask_yes

t = i18n.t

CONF = Path("/etc/mkinitcpio.conf")
CONF_D = Path("/etc/mkinitcpio.conf.d")
MODULES_ROOT = Path("/usr/lib/modules")
FIRMWARE_ROOT = Path("/usr/lib/firmware")
BACKUP_DIR = Path("/var/lib/archsetup")
SYS_DEVICES = Path("/sys/devices")
ROOT = Path("/")

HOOK = "kms"

# install/kms: map add_checked_modules '/drivers/char/agp/' '/drivers/gpu/drm/'
KMS_PATHS = ("/drivers/char/agp/", "/drivers/gpu/drm/")

# Built into every Arch kernel for years, so removing kms does not leave the
# machine without a console -- but it is checked rather than assumed, because
# a kernel that has neither this nor a whitelisted DRM driver would come up
# blind and no message afterwards could explain why.
CONSOLE_BUILTINS = ("simpledrm", "efifb", "vesafb")


def _norm(name: str) -> str:
    """mkinitcpio compares module names with dashes folded to underscores."""
    return name.replace("-", "_")


def _capture(cmd: list[str]) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout


def kernels() -> list[str]:
    """Installed kernels, by module directory -- not `uname -r`.

    HOOKS is one line shared by every preset, so the decision has to hold for
    every kernel on the disk, including the one that is not running.
    """
    try:
        return sorted(
            path.name
            for path in MODULES_ROOT.iterdir()
            if (path / "kernel").is_dir()
        )
    except OSError:
        return []


def autodetect_modules(kver: str) -> set[str]:
    """Rebuild what install/autodetect would cache for this kernel.

    Same pipeline as the hook: every DRIVER=/MODALIAS= token under /sys,
    resolved by modprobe. `-R` only prints names, it loads nothing.
    """
    tokens: set[str] = set()
    try:
        uevents = list(SYS_DEVICES.rglob("uevent"))
    except OSError:
        return set()
    for uevent in uevents:
        try:
            lines = uevent.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            key, _, value = line.partition("=")
            if key in ("DRIVER", "MODALIAS") and value:
                tokens.add(value)
    if not tokens:
        return set()
    out = _capture(["modprobe", "-S", kver, "-qaR", *sorted(tokens)])
    return {_norm(name) for name in out.split()}


def kms_pool(kver: str) -> set[str]:
    """Every module `kms` could add for this kernel, before the cache filter."""
    pool: set[str] = set()
    try:
        candidates = list((MODULES_ROOT / kver / "kernel").rglob("*.ko*"))
    except OSError:
        return pool
    for path in candidates:
        if any(fragment in str(path) for fragment in KMS_PATHS):
            pool.add(_norm(path.name.split(".ko")[0]))
    return pool


def kms_contribution(kver: str) -> set[str]:
    """What `kms` actually puts in the image: the pool the cache agrees with."""
    return kms_pool(kver) & autodetect_modules(kver)


def blacklisted() -> set[str]:
    """Blacklisted module names, read from modprobe's own merged view.

    `modprobe -c` is the parser of record for /etc/modprobe.d and
    /usr/lib/modprobe.d together; globbing those directories here would be a
    second parser for a format we do not own.
    """
    names: set[str] = set()
    for line in _capture(["modprobe", "-c"]).splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] == "blacklist":
            names.add(_norm(fields[1]))
    return names


def firmware_bytes(kver: str, module: str, seen: set | None = None) -> int:
    """On-disk size of the firmware a module declares, compressed variants too.

    Counted per inode, not per declared path, and `seen` carries that across
    calls so two kernels declaring the same blob do not both pay for it.
    /usr/lib/firmware is full of symlinks pointing at shared blobs and stat()
    follows them: measured 2026-08-13, nouveau declares 519 paths that resolve
    to 222 distinct inodes, and summing paths reported 1302 MiB where the real
    cost is 103 MiB. The image delta the task prints afterwards was 107 MiB, so
    the wrong number was wrong by an order of magnitude in the one place a user
    would have checked it.
    """
    if seen is None:
        seen = set()
    total = 0
    for relative in _capture(["modinfo", "-k", kver, "-F", "firmware", module]).split():
        base = FIRMWARE_ROOT / relative
        for candidate in (base, base.with_name(base.name + ".zst"),
                          base.with_name(base.name + ".xz")):
            try:
                stat = candidate.stat()
            except OSError:
                continue
            key = (stat.st_dev, stat.st_ino)
            if key not in seen:
                seen.add(key)
                total += stat.st_size
            break
    return total


def _console_survives(kver: str, whitelisted: set[str]) -> bool:
    if whitelisted & kms_pool(kver):
        return True
    try:
        builtin = (MODULES_ROOT / kver / "modules.builtin").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return False
    return any(f"/{name}.ko" in builtin for name in CONSOLE_BUILTINS)


def _images() -> list[Path]:
    outputs: list[Path] = []
    for preset in mkinitcpio.presets(ROOT):
        try:
            text = preset.read_text(encoding="utf-8")
        except OSError:
            continue
        outputs.extend(mkinitcpio.preset_outputs(text))
    return outputs


def _sizes(paths: list[Path]) -> dict[Path, int]:
    """Sizes of images that may sit on a 0700 ESP -- one sudo call, not one each."""
    if not paths:
        return {}
    sizes: dict[Path, int] = {}
    missing: list[Path] = []
    for path in paths:
        try:
            sizes[path] = path.stat().st_size
        except OSError:
            missing.append(path)
    if missing:
        out = _capture(["sudo", "stat", "-c", "%s %n", *[str(p) for p in missing]])
        for line in out.splitlines():
            size, _, name = line.partition(" ")
            if size.isdigit() and name:
                sizes[Path(name)] = int(size)
    return sizes


def _backup_images(stamp: str) -> int:
    present = _sizes(_images())
    if not present:
        print(t("kms_hook.no_images"))
        return 1
    rc = run(["sudo", "mkdir", "-p", str(BACKUP_DIR)])
    for path in sorted(present):
        backup = BACKUP_DIR / f"{path.name}.yedek-{stamp}"
        rc |= run(["sudo", "cp", "-a", str(path), str(backup)])
        print(t("kms_hook.image_backup", path=path, backup=backup))
    return rc


def configure() -> int:
    if not hardware.gpu_matches("nvidia"):
        print(t("kms_hook.no_nvidia"))
        return 1
    if not pacman.is_installed("nvidia-utils"):
        print(t("kms_hook.driver_missing"))
        return 1

    # Decide on what mkinitcpio sees, act on the file we own.
    effective = mkinitcpio.effective_text(CONF, CONF_D)
    hooks = mkinitcpio.read_array(effective, "HOOKS")
    if hooks is None:
        print(t("kms_hook.no_hooks", path=CONF))
        return 1
    if HOOK not in hooks:
        print(t("kms_hook.already"))
        return 0

    installed = kernels()
    if not installed:
        print(t("kms_hook.no_kernels", path=MODULES_ROOT))
        return 1

    whitelisted = {_norm(name) for name in mkinitcpio.read_array(effective, "MODULES") or []}
    blacklist = blacklisted()

    waste: set[str] = set()
    losses: set[str] = set()
    blind: list[str] = []
    counted: set = set()
    freed = 0
    for kver in installed:
        contribution = kms_contribution(kver)
        print(t("kms_hook.contribution", kernel=kver,
                modules=" ".join(sorted(contribution)) or "-"))
        for name in contribution:
            if name in blacklist:
                waste.add(name)
                freed += firmware_bytes(kver, name, counted)
            elif name not in whitelisted:
                losses.add(name)
        if not _console_survives(kver, whitelisted):
            blind.append(kver)

    if losses:
        print(t("kms_hook.would_lose", modules=" ".join(sorted(losses))))
        if "amdgpu" in losses:
            print(t("kms_hook.amd_hint"))
        return 1
    if not waste:
        print(t("kms_hook.nothing_to_drop"))
        return 0
    if blind:
        print(t("kms_hook.no_console", kernels=" ".join(blind)))
        return 1

    print(t("kms_hook.plan", modules=" ".join(sorted(waste)), mib=round(freed / 1024**2, 1)))
    print(t("kms_hook.size_only"))
    if not ask_yes(t("kms_hook.continue_q")):
        print(t("msg.cancelled"))
        return 0

    # kms may be coming from a drop-in we do not own; editing the main file
    # would then report success and change nothing.
    text = CONF.read_text(encoding="utf-8")
    own = mkinitcpio.read_array(text, "HOOKS")
    if own is None or HOOK not in own:
        print(t("kms_hook.from_dropin", path=CONF_D))
        return 1

    stamp = date.today().isoformat()
    rc = _backup_images(stamp)
    if rc != 0:
        return rc

    before = _sizes(_images())
    trimmed = mkinitcpio.set_array(text, "HOOKS", [h for h in own if h != HOOK])
    if trimmed is None:
        print(t("kms_hook.no_hooks", path=CONF))
        return 1

    backup = CONF.with_name(f"{CONF.name}.yedek-{stamp}")
    rc, changed = sysedit.write_with_backup(CONF, trimmed, backup=backup)
    if rc != 0 or not changed:
        return rc

    rc |= run(["sudo", "mkinitcpio", "-P"])

    # What was written is not what is in force: report the produced images.
    produced = _images()
    for path, size in sorted(_sizes(produced).items()):
        was = before.get(path)
        if was:
            print(t("kms_hook.image_size", path=path,
                    before=round(was / 1024**2, 1), after=round(size / 1024**2, 1)))
    # Asked even when -P failed: a half-written image is exactly the one whose
    # signature is worth knowing about before the next boot.
    rc |= secureboot.verify(produced)
    print(t("kms_hook.undo", backup=backup, conf=CONF))
    return rc
