"""The live-ISO caller for the two whole-disk surfaces.

The surfaces themselves are `core/diskwipe.py`; this module is what makes
them install steps. Two things belong here and nowhere else:

**`guard()`** -- these rows sit inside the installer menu, which is
reachable with `--installer` on a running system. The gate keeps the
installer's own copy of them where they were measured. It is not a
safety gate against erasing the wrong disk (`blockdev.refuse()` is), and
it deliberately does not apply to the installed-system tasks: those are
the same code answering a different question, with a gate of their own.

**`_forget_partitions()`** -- installer state, and meaningless anywhere
else. A disk that was just wiped no longer holds the partition somebody
selected as root, and leaving the stale path in `state` would carry it
into `format_devices()` as a device that no longer exists.
"""

from __future__ import annotations

from ..core import diskwipe
from .disk import guard
from .state import state


def _forget_partitions(dev: str) -> None:
    """Drop selections pointing into a disk that no longer has them."""
    for attr in ("bootdev", "swapdev", "rootdev", "homedev"):
        if (value := getattr(state, attr)) and value.startswith(dev):
            setattr(state, attr, None)


def prepare_disk() -> int:
    """Wipe the partition table and filesystem signatures off one disk."""
    if not guard():
        return 1
    return diskwipe.prepare_disk(on_wiped=_forget_partitions)


def erase_disk() -> int:
    """Destroy the contents of one whole disk, irreversibly."""
    if not guard():
        return 1
    return diskwipe.erase_disk(on_wiped=_forget_partitions)
