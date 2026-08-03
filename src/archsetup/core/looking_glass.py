"""Looking Glass on the machine that shows the guest: client plus kvmfr device.

A terminology trap first, because it inverts the usual meaning of the words:
Looking Glass calls the program running *inside* the VM the "host" (it captures
frames) and the program on the physical machine the "client" (it displays
them). This task is the physical machine's side, so it installs the *client* --
the host binary is a Windows download and stays the guest's problem. It is only
pointed at, not fetched: it belongs to the other operating system, and a zip
dropped into a directory nobody chose is a file that goes stale in silence.

The two AUR packages hand over a client binary and DKMS sources, and stop
there. Everything that makes the shared-memory device usable is missing from
them, and that gap is what this task owns:

- the module is never loaded -- no modules-load.d entry,
- ``static_size_mb`` has no default -- no modprobe.d entry, and no constant we
  could ship either: the value follows the resolution being streamed,
- ``/dev/kvmfr0`` comes up root:root 0600 -- no udev rule, so the client cannot
  open the device it exists to open,
- libvirt's cgroup device ACL does not list ``/dev/kvmfr0``, so qemu is denied
  the node even once the permissions are right.

Written by hand, all four are gone at the next reinstall. That is the whole
argument for the task existing.

The ACL is the one piece that is edited rather than generated. Naming
``cgroup_device_acl`` at all replaces libvirt's built-in list, so a block
holding only ``/dev/kvmfr0`` would take /dev/null, /dev/urandom and the rest
away from every VM on the machine. Rather than reconstruct that list from
memory, this uncomments the sample block libvirt itself ships in qemu.conf and
adds one line to it; a file carrying neither an active nor a commented block is
refused instead of guessed at.
"""

from __future__ import annotations

import fcntl
import getpass
import math
import os
import re
import stat
from pathlib import Path

from . import i18n, pacman, sysedit
from .pacman import run

t = i18n.t

CLIENT_PKG = "looking-glass"
MODULE_PKG = "looking-glass-module-dkms"

DRM_CLASS = Path("/sys/class/drm")
MODULES_DIR = Path("/usr/lib/modules")

# _IO('u', 0x44) from the module's own kvmfr.h; it answers with the byte size
# the device was created with. Frozen in a test, because a wrong number here
# would not raise -- it would quietly report "size unknown" for ever.
KVMFR_GETSIZE = ord("u") << 8 | 0x44

MODULES_LOAD = Path("/etc/modules-load.d/kvmfr.conf")
MODPROBE = Path("/etc/modprobe.d/kvmfr.conf")
UDEV_RULES = Path("/etc/udev/rules.d/99-kvmfr.rules")
QEMU_CONF = Path("/etc/libvirt/qemu.conf")
DEV_NODE = Path("/dev/kvmfr0")

ACL_KEY = "cgroup_device_acl"
ACL_DEVICE = "/dev/kvmfr0"

# 32-bit RGB. Looking Glass can carry 64-bit HDR, but neither Xorg nor Wayland
# can display it today -- the drivers convert it back to SDR while it costs
# twice the memory and bandwidth. Doubling this is a deliberate, separate
# decision, not a default.
BPP = 4

# The client needs room for two frames plus about 10 MiB of overhead, rounded
# up to a power of two (Looking Glass B7 docs, "Determining memory").
OVERHEAD_MIB = 10

_MODE = re.compile(r"^(\d+)x(\d+)")

MODULES_LOAD_CONTENT = """\
# Written by archsetup (task: looking-glass). The DKMS package ships the
# sources and nothing that loads them.
kvmfr
"""

MODPROBE_TEMPLATE = """\
# Written by archsetup (task: looking-glass). The size follows the resolution
# being streamed, so it is read off this machine rather than baked in:
#   {basis}
# Raising it past what the guest needs buys no speed -- it only takes that RAM
# away from the host for good.
options kvmfr static_size_mb={mb}
"""

RULE_TEMPLATE = """\
# Written by archsetup (task: looking-glass). The module creates /dev/kvmfr0
# as root:root 0600; the client runs as the user and has to open it.
SUBSYSTEM=="kvmfr", OWNER="{user}", GROUP="kvm", MODE="0660"
"""


def connected_modes() -> list[tuple[int, int]]:
    """(width, height) of the preferred mode of every connected output.

    The first line of a connector's `modes` file is its preferred mode, which
    for a fixed panel is its native resolution. Disconnected outputs still have
    the file and would otherwise contribute a mode nothing can display.
    """
    found = []
    try:
        connectors = sorted(DRM_CLASS.iterdir())
    except OSError:
        return found
    for connector in connectors:
        try:
            status = (connector / "status").read_text(encoding="utf-8").strip()
            modes = (connector / "modes").read_text(encoding="utf-8")
        except OSError:
            continue
        if status != "connected":
            continue
        for line in modes.splitlines():
            match = _MODE.match(line.strip())
            if match:
                found.append((int(match.group(1)), int(match.group(2))))
                break
    return found


def required_mb(width: int, height: int, bpp: int = BPP) -> int:
    """Shared memory a frame of this size needs, in MiB."""
    frame = width * height * bpp * 2
    needed = frame / 1024 / 1024 + OVERHEAD_MIB
    return 1 << math.ceil(math.log2(needed))


def largest_mode() -> tuple[int, int] | None:
    """The biggest connected display, or None on a machine showing nothing.

    The client draws the guest into a window on this machine, so no guest
    resolution above the largest local display is worth paying memory for.
    """
    modes = connected_modes()
    if not modes:
        return None
    return max(modes, key=lambda mode: mode[0] * mode[1])


def kernel_headers() -> tuple[str | None, bool]:
    """(headers package for the running kernel, whether they are installed).

    DKMS needs the build tree, and without it the module package installs
    cleanly and produces nothing -- the failure surfaces much later as a
    missing /dev/kvmfr0. The package name is derived from the kernel's own
    `pkgbase` file rather than a table of kernel names: this machine runs
    linux-g14, which no such table ever knows about.
    """
    release = os.uname().release
    try:
        pkgbase = (MODULES_DIR / release / "pkgbase").read_text(encoding="utf-8")
    except OSError:
        return None, (MODULES_DIR / release / "build").is_dir()
    return f"{pkgbase.strip()}-headers", (MODULES_DIR / release / "build").is_dir()


def client_release() -> str | None:
    """The installed client's upstream release (B7), epoch and pkgrel removed.

    The guest-side host application must match the client exactly, and its
    download URL carries the release, so the address is derived from what is
    actually installed instead of being written down and left to rot.
    """
    fields = pacman.query(["pacman", "-Q", CLIENT_PKG])
    if len(fields) < 2:
        return None
    version = fields[1].split(":", 1)[-1]
    return version.rsplit("-", 1)[0] or None


def _acl_block(text: str, commented: bool) -> tuple[int, int] | None:
    """(start, end) line indices of the ACL block, or None if there is none."""
    prefix = "#" if commented else ""
    start = None
    for index, line in enumerate(text.splitlines()):
        if start is None:
            if line.startswith(f"{prefix}{ACL_KEY}") and line.rstrip().endswith("["):
                start = index
            continue
        if line.rstrip() == f"{prefix}]":
            return start, index
    return None


def _has_active_key(text: str) -> bool:
    return any(line.startswith(ACL_KEY) for line in text.splitlines())


def acl_allows_kvmfr(text: str) -> bool:
    """True when an *active* cgroup_device_acl already lists the device."""
    if not _has_active_key(text):
        return False
    lines = text.splitlines()
    block = _acl_block(text, commented=False)
    if block is None:
        # The key is set but not as a block we recognise; the honest answer
        # about a list we cannot parse is whatever it plainly contains.
        return any(line.startswith(ACL_KEY) and ACL_DEVICE in line for line in lines)
    start, end = block
    return any(ACL_DEVICE in line for line in lines[start : end + 1])


def acl_with_kvmfr(text: str) -> str | None:
    """qemu.conf text with /dev/kvmfr0 added to cgroup_device_acl.

    Returns None when there is no block that can be edited safely. Uncommenting
    libvirt's own sample is deliberate: the moment the key is set it replaces
    the built-in list entirely, so the list to start from is the one shipped
    next to the key, not one written from memory. And an active key we cannot
    parse is refused rather than joined -- a second active assignment leaves
    the outcome to file order, which is how the mkinitcpio PRESETS bug worked.
    """
    lines = text.splitlines()

    block = _acl_block(text, commented=False)
    if block is not None:
        start, _ = block
        lines.insert(start + 1, f'    "{ACL_DEVICE}",')
        return "\n".join(lines) + "\n"

    if _has_active_key(text):
        return None

    block = _acl_block(text, commented=True)
    if block is None:
        return None
    start, end = block
    uncommented = [
        line[1:] if line.startswith("#") else line for line in lines[start : end + 1]
    ]
    uncommented.insert(1, f'    "{ACL_DEVICE}",')
    return "\n".join(lines[:start] + uncommented + lines[end + 1 :]) + "\n"


def _ask_size(default: int | None) -> int | None:
    """Confirm or override the calculated size; None when there is no answer."""
    prompt = (
        t("lg.size_q", mb=default) if default is not None else t("lg.size_q_blank")
    )
    try:
        reply = input(f"{prompt} ").strip()
    except EOFError:
        reply = ""
    if not reply:
        return default
    if reply.isdigit() and int(reply) > 0:
        return int(reply)
    print(t("lg.size_bad", value=reply))
    return default


def node_is_device() -> bool:
    """True when /dev/kvmfr0 is the character device the module created.

    One predicate for both questions the task asks about that path -- whether
    something else is squatting on it before we start, and whether the module
    produced it in the end -- so the two can never disagree.
    """
    try:
        return stat.S_ISCHR(os.stat(DEV_NODE).st_mode)
    except OSError:
        return False


def loaded_size_mb() -> int | None:
    """MiB the running module actually gave the device, None when unknown.

    Not read from /sys/module/kvmfr/parameters: the module declares
    static_size_mb with permission 0000, so the kernel publishes no sysfs entry
    for it and that directory does not exist at all. Measured on this machine
    -- the obvious check would have returned "unknown" every single time and
    the guard below would never once have fired.

    The device answers instead, which is better anyway: it reports the size in
    force rather than the size someone wrote in a file. The udev rule this task
    installs is what makes it openable without root.
    """
    try:
        fd = os.open(DEV_NODE, os.O_RDWR)
    except OSError:
        return None
    try:
        return fcntl.ioctl(fd, KVMFR_GETSIZE) // 1024 // 1024
    except OSError:
        return None
    finally:
        os.close(fd)


def install() -> int:
    """Install the Looking Glass client and make /dev/kvmfr0 usable."""
    if not pacman.is_installed("libvirt"):
        print(t("virt.libvirt_missing"))
        return 1

    # QEMU creates /dev/kvmfr0 as an ordinary file when a VM starts before the
    # module is loaded, and from then on the module cannot own that name. The
    # symptom otherwise is a client that fails to open a device that appears to
    # be right there.
    if DEV_NODE.exists() and not node_is_device():
        print(t("lg.node_not_a_device", node=DEV_NODE))
        return 1

    headers, present = kernel_headers()
    if not present and headers is None:
        print(t("lg.headers_unknown", release=os.uname().release))
        return 1
    repo_pkgs = [] if present or headers is None else [headers]

    mode = largest_mode()
    if mode is None:
        print(t("lg.no_display"))
        basis = "size given by hand -- no connected display to calculate from"
        size = _ask_size(None)
    else:
        width, height = mode
        size = required_mb(width, height)
        basis = (
            f"{width}x{height} x {BPP} bytes x 2 frames + {OVERHEAD_MIB} MiB,"
            " rounded up to a power of two"
        )
        print(t("lg.size_from_display", width=width, height=height, mb=size))
        size = _ask_size(size)
    if size is None:
        print(t("lg.size_missing"))
        return 1

    rc = pacman.install(repo_pkgs, [CLIENT_PKG, MODULE_PKG])
    if rc != 0:
        return rc

    modprobe_rc, _ = sysedit.write_with_backup(
        MODPROBE, MODPROBE_TEMPLATE.format(basis=basis, mb=size)
    )
    rc |= modprobe_rc
    load_rc, _ = sysedit.write_with_backup(MODULES_LOAD, MODULES_LOAD_CONTENT)
    rc |= load_rc

    rule_rc, rule_changed = sysedit.write_with_backup(
        UDEV_RULES, RULE_TEMPLATE.format(user=getpass.getuser())
    )
    rc |= rule_rc
    if rule_changed:
        rc |= run(["sudo", "udevadm", "control", "--reload-rules"])
        rc |= run(["sudo", "udevadm", "trigger", "--subsystem-match=kvmfr"])

    rc |= _allow_device_in_libvirt()

    # An already-loaded module keeps the size it was loaded with; modprobe will
    # not reapply the parameter and will not complain either. The file changing
    # is not the question -- only whether the value in force is the wanted one,
    # or a rewritten comment would raise a false alarm.
    loaded = loaded_size_mb()
    if loaded is None:
        rc |= run(["sudo", "modprobe", "kvmfr"])
    elif loaded != size:
        print(t("lg.reload_needed", loaded=loaded, wanted=size))
        rc |= 1

    if node_is_device():
        print(t("lg.node_ok", node=DEV_NODE, mb=size))
    else:
        print(t("lg.node_missing", node=DEV_NODE))
        rc |= 1

    release = client_release()
    if release:
        print(t("lg.guest_side", release=release))
    return rc


def _allow_device_in_libvirt() -> int:
    try:
        text = QEMU_CONF.read_text(encoding="utf-8")
    except OSError:
        print(t("msg.file_missing", path=QEMU_CONF))
        return 1

    if acl_allows_kvmfr(text):
        print(t("lg.acl_already", path=QEMU_CONF))
        return 0

    updated = acl_with_kvmfr(text)
    if updated is None:
        print(t("lg.acl_no_block", path=QEMU_CONF, device=ACL_DEVICE))
        return 1

    rc, changed = sysedit.write_with_backup(QEMU_CONF, updated)
    if changed:
        print(t("lg.acl_added", path=QEMU_CONF, device=ACL_DEVICE))
        # Socket-activated here, so the daemon is usually not running at all;
        # restarting one that is not up would start it for nothing.
        rc |= run(["sudo", "systemctl", "try-restart", "libvirtd.service"])
    return rc
