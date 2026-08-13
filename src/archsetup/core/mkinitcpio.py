"""Reading and rewriting mkinitcpio's configuration and preset files.

One parser, four callers. core.gpuconfig merges MODULES, core.hibernate adds
the resume hook, core.kms_hook drops the kms one and installer.chroot rewrites
presets for UKI output -- each used to carry its own `^NAME=(...)` regex, and a
boot-critical line with four independent writers drifts.

The text helpers take and return strings, so a caller keeps pointing at its own
file constant and stays testable against a temporary file. Only `presets()`
touches the filesystem, and it takes the root it works under so the installer
can aim it at /mnt while a post-install task aims it at /.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

CONF = Path("/etc/mkinitcpio.conf")
CONF_D = Path("/etc/mkinitcpio.conf.d")


def _last_array(text: str, name: str) -> re.Match[str] | None:
    """The assignment that wins.

    mkinitcpio sources the configuration as shell, and it concatenates the
    drop-ins after the main file, so when a name is assigned more than once the
    *last* one is the value in force. Taking the first match instead reads a
    setting the built image does not have -- and reads it most confidently
    exactly where someone bothered to override it.
    """
    matches = list(re.finditer(rf"^{re.escape(name)}=\(([^)]*)\)", text, re.MULTILINE))
    return matches[-1] if matches else None


def read_array(text: str, name: str) -> list[str] | None:
    """The entries of `NAME=(...)`, or None when there is no such line.

    None and [] are different answers: an empty array is a configuration,
    a missing line is a file we do not recognise, and callers act on that
    distinction rather than treating both as "nothing set".
    """
    match = _last_array(text, name)
    return None if match is None else match.group(1).split()


def set_array(text: str, name: str, values: Sequence[str]) -> str | None:
    """Rewrite `NAME=(...)` in place; None when the line is not there."""
    match = _last_array(text, name)
    if match is None:
        return None
    return f"{text[:match.start(1)]}{' '.join(values)}{text[match.end(1):]}"


def effective_text(conf: Path = CONF, conf_d: Path = CONF_D) -> str:
    """The configuration mkinitcpio actually sees: main file, then drop-ins.

    mkinitcpio concatenates `conf.d/*.conf` after the main file, so a drop-in
    silently wins any array it redefines. A gate that reads only the main file
    can therefore report a hook as absent while the built image still has it --
    "written" and "in force" being different sentences.

    Note this is *not* the file to write back to: drop-ins belong to whoever
    put them there. Callers read the effective text to decide and edit the
    main file to act.
    """
    text = conf.read_text(encoding="utf-8")
    try:
        drop_ins = sorted(conf_d.glob("*.conf"))
    except OSError:
        return text
    for drop_in in drop_ins:
        try:
            text = f"{text}\n{drop_in.read_text(encoding='utf-8')}"
        except OSError:
            continue
    return text


def presets(root: Path = Path("/")) -> list[Path]:
    return sorted((root / "etc/mkinitcpio.d").glob("*.preset"))


def preset_value(text: str, key: str) -> str | None:
    """An active (uncommented) preset assignment, e.g. `default_uki`."""
    match = re.search(rf'^{re.escape(key)}="?([^"\n]+)"?', text, re.MULTILINE)
    return match.group(1).strip() if match else None


def preset_outputs(text: str) -> list[Path]:
    """Every image a `mkinitcpio -p` run over this preset will overwrite.

    PRESETS names the passes; each pass writes `<name>_uki` and/or
    `<name>_image`, whichever is uncommented. Commented-out assignments are
    skipped because the regex is anchored -- a preset that produces a UKI has
    its `default_image` line commented, and copying a stale .img as a "backup"
    of a file that is not being written would be worse than no backup at all.
    """
    outputs: list[Path] = []
    for entry in read_array(text, "PRESETS") or []:
        name = entry.strip("'\"")
        for kind in ("uki", "image"):
            value = preset_value(text, f"{name}_{kind}")
            if value:
                outputs.append(Path(value))
    return outputs
