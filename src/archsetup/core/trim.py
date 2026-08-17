"""Enable the weekly TRIM timer.

Nothing in archsetup ever turned TRIM on: `fstrim.timer` was disabled and
inactive on a machine this tool installed, and no fstab it writes carries
`discard` either. So every install it produced left an SSD with no way to
learn which blocks are free -- the drive keeps garbage-collecting data the
filesystem stopped caring about, which is write amplification paid for
nothing. The two NVMe drives measured here both advertise it
(`discard_max_bytes` = 2 TB), so the capability was there the whole time.

Why the timer and not the `discard` mount option. mount(8) describes that
option as issuing TRIM "when blocks are freed", i.e. inline, in the delete
path, per filesystem; the timer batches the same work into one weekly pass
over everything listed in fstab. Neither man page here ranks them, so the
reason for choosing is scope, not performance: the timer takes no fstab
edit, which is what lets one task fix a machine that is already installed
without touching how its root filesystem mounts.

No `fstrim --all` here. The unit shipped by util-linux runs `fstrim
--listed-in /etc/fstab:/proc/self/mountinfo --verbose --quiet-unsupported`,
which is a narrower and better-informed list than anything worth rewriting.
The unit is started once by hand though, because enabling the timer is not
the same as trimming: measured on this machine, a freshly enabled
fstrim.timer scheduled its first pass 6 days out. Persistent=true only
makes up for elapses that were *missed*, and a timer that never ran has
missed nothing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import i18n
from .pacman import run
from .services import enable_now

t = i18n.t

BLOCK = Path("/sys/block")
TIMER = "fstrim.timer"
SERVICE = "fstrim.service"


def capable_disks() -> list[str]:
    """Real disks whose request queue advertises discard.

    sysfs rather than `lsblk --discard`, for the same reason ethernet_pm
    reads sysfs rather than lspci: this is the attribute the block layer
    answers from, so an empty list here means fstrim has nothing to trim
    either. /sys/block holds whole devices only -- a partition has no
    queue/ of its own -- so nothing has to be filtered out by name.

    Except that discard_max_bytes alone answers the wrong question.
    Measured here: loop0 and loop1 report 131072, so a machine with one
    spinning disk and any loop mount (an AppImage, a mounted ISO) would
    look trimmable. The separator is structural rather than a name
    blocklist: a hardware disk carries a device/ link to what backs it and
    a loop device does not. dm-*/md-* have no such link either, and that is
    correct here -- this asks whether the machine has a disk worth trimming,
    and the SSD under a LUKS mapper answers for itself.
    """
    try:
        entries = sorted(BLOCK.iterdir())
    except OSError:
        return []

    found = []
    for device in entries:
        if not (device / "device").exists():
            continue
        try:
            if int((device / "queue/discard_max_bytes").read_text()) > 0:
                found.append(device.name)
        except (OSError, ValueError):
            continue
    return found


def _state(name: str) -> str:
    """`systemctl is-enabled`, whatever it says.

    Its exit code is not the answer -- it is non-zero for `disabled` as much
    as for a unit that does not exist -- and the two are different failures.
    """
    out = subprocess.run(
        ["systemctl", "is-enabled", name], capture_output=True, text=True
    )
    return out.stdout.strip() or "?"


def _result(name: str) -> str:
    """How the oneshot ended, which its own exit code does not report here.

    `systemctl start` on a Type=oneshot returns non-zero when the unit
    fails, but a unit that has never run also shows Result=success, so the
    field is read after the run rather than instead of it.
    """
    out = subprocess.run(
        ["systemctl", "show", name, "-p", "Result", "--value"],
        capture_output=True,
        text=True,
    )
    return out.stdout.strip() or "?"


def configure() -> int:
    disks = capable_disks()
    if not disks:
        print(t("trim.no_device"))
        return 1
    print(t("trim.capable", disks=", ".join(disks)))

    rc = enable_now(TIMER)

    # Enabled is not armed. `enable --now` can return 0 on a unit that a
    # condition then declines to start, so the state is read back rather
    # than inferred from the exit code.
    state = _state(TIMER)
    if state != "enabled":
        print(t("trim.not_enabled", unit=TIMER, state=state))
        return rc | 1
    print(t("trim.done", unit=TIMER))

    # The first pass, which the timer alone would leave up to a week away.
    rc |= run(["sudo", "systemctl", "start", SERVICE])
    result = _result(SERVICE)
    print(t("trim.first_pass", unit=SERVICE, result=result))
    return rc
