"""Reading and rewriting mkinitcpio's configuration and preset files.

One parser, five callers. core.gpuconfig merges MODULES, core.hibernate adds
the resume hook, core.kms_hook drops the kms one, and installer.chroot and
core.uki rewrite presets for UKI output -- each used to carry its own
`^NAME=(...)` regex, and a boot-critical line with four independent writers
drifts. The last two rewrite the same four preset lines, so that rule lives
here as well rather than once per side of the install.

The text helpers take and return strings, so a caller keeps pointing at its own
file constant and stays testable against a temporary file. The three functions
that touch the filesystem -- `presets`, `outputs`, `regenerate` -- take the
root they work under, so the installer can aim them at /mnt while a
post-install task aims them at /.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Sequence

from . import secureboot
from .pacman import run

CONF = Path("/etc/mkinitcpio.conf")
CONF_D = Path("/etc/mkinitcpio.conf.d")
ROOT = Path("/")


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


def presets(root: Path = ROOT) -> list[Path]:
    return sorted((root / "etc/mkinitcpio.d").glob("*.preset"))


def outputs(root: Path = ROOT) -> list[Path]:
    """Every image the presets under `root` name, across all of them."""
    found: list[Path] = []
    for preset in presets(root):
        try:
            text = preset.read_text(encoding="utf-8")
        except OSError:
            continue
        found.extend(preset_outputs(text))
    return found


def _sudo_stat(paths: Sequence[Path]) -> str:
    """`stat` for images this user cannot see; "" when it gives no answer.

    Its own function so a test can seal it by name: the alternative is
    patching subprocess.run for the whole module, which would also silence
    everything else that shells out.
    """
    try:
        return subprocess.run(
            ["sudo", "stat", "-c", "%s %n", *[str(p) for p in paths]],
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def sizes(paths: Sequence[Path]) -> dict[Path, int]:
    """Sizes of the images that could be measured, absent ones simply missing.

    An ESP is mode 0700 on any sane install, so stat() on a UKI is a
    permission error for the user running a task rather than an answer. The
    fallback is one `sudo stat` covering all of them rather than one per file:
    sudo asks per command, and a password prompt repeated once per kernel is
    one the user stops reading.
    """
    if not paths:
        return {}
    found: dict[Path, int] = {}
    missing: list[Path] = []
    for path in paths:
        try:
            found[path] = path.stat().st_size
        except OSError:
            missing.append(path)
    if not missing:
        return found
    for line in _sudo_stat(missing).splitlines():
        size, _, name = line.partition(" ")
        if size.isdigit() and name:
            found[Path(name)] = int(size)
    return found


def regenerate(root: Path = ROOT) -> int:
    """Rebuild every image, and then ask whether the result is still signed.

    Four tasks rebuilt boot images and stopped at the exit code of `-P`. On a
    Secure Boot machine that leaves the signing to sbctl's own post hook and
    never checks that it happened, and the answer arrives at the next
    power-on. The check lives here rather than at each call site for the
    reason those four were missing it: whoever writes the fifth task will
    reach for the rebuild, not for the verification.
    """
    rc = run(["sudo", "mkinitcpio", "-P"])
    # Enumerated only when there is something to check with it -- otherwise
    # this reads every preset on the machine to produce an answer nobody wants.
    if secureboot.enabled() is not True:
        return rc
    return rc | secureboot.verify(outputs(root))


def preset_value(text: str, key: str) -> str | None:
    """An active (uncommented) preset assignment, e.g. `default_uki`."""
    match = re.search(rf'^{re.escape(key)}="?([^"\n]+)"?', text, re.MULTILINE)
    return match.group(1).strip() if match else None


def preset_passes(text: str) -> list[str]:
    """The pass names in PRESETS, without the shell quoting."""
    return [entry.strip("'\"") for entry in read_array(text, "PRESETS") or []]


def preset_ukis(text: str) -> list[str]:
    """Every UKI path this preset names -- fallback included.

    Separate from preset_outputs() because the two answer different
    questions: that one lists everything a rebuild overwrites, `.img`
    initramfs files included, and a plain initramfs is not signed on any
    setup. This one is the list of things Secure Boot has to be able to
    verify, and the fallback is in it. Measured 2026-08-30 in QEMU with
    keys actually enrolled: the Secure Boot step read `default_uki` alone,
    signed `arch-linux-zen.efi`, and its own `sbctl verify` closed the run
    with `\u2717 /efi/EFI/Linux/arch-linux-zen-fallback.efi is not signed` --
    the recovery entry, unbootable on the machine that most needs it, on a
    step that exited 0.

    Every uncommented `<name>_uki` line counts, rather than only the passes
    PRESETS lists. A preset with no PRESETS array would otherwise answer
    "no UKIs" and the caller would sign nothing -- silence in the direction
    that costs the most. Naming a pass that is not active cannot do harm
    the other way: the caller signs paths that exist, and an inactive pass
    writes none.
    """
    return [m.group(2).strip() for m in re.finditer(
        r'^(\w+)_uki="?([^"\n]+)"?', text, re.MULTILINE
    )]


def preset_outputs(text: str) -> list[Path]:
    """Every image a `mkinitcpio -p` run over this preset will overwrite.

    PRESETS names the passes; each pass writes `<name>_uki` and/or
    `<name>_image`, whichever is uncommented. Commented-out assignments are
    skipped because the regex is anchored -- a preset that produces a UKI has
    its `default_image` line commented, and copying a stale .img as a "backup"
    of a file that is not being written would be worse than no backup at all.
    """
    outputs: list[Path] = []
    for name in preset_passes(text):
        for kind in ("uki", "image"):
            value = preset_value(text, f"{name}_{kind}")
            if value:
                outputs.append(Path(value))
    return outputs


def set_uki_output(text: str, passes: Sequence[str]) -> str:
    """Point the named passes at a Unified Kernel Image instead of an image.

    Four assignments decide it and they are the same four everywhere the
    conversion happens: `ALL_config` on, and per pass `_uki` on, `_options` on
    (the splash line the stock template carries), `_image` off. The installer
    writes them into a fresh target and core.uki writes them into a preset a
    kernel package just generated; a boot-critical rewrite with two authors
    drifts, which is the reason the arrays are parsed in one place too.

    Nothing here touches PRESETS: which passes exist is the preset's own
    business, and this function only changes what the ones it is given write.

    A pass whose `_uki` line is not in the file comes back unchanged rather
    than half-converted -- the caller is expected to read the result and check,
    because "the regex did not match" and "the preset now builds a UKI" look
    identical from the outside.
    """
    text = re.sub(r"^#(ALL_config)", r"\1", text, flags=re.MULTILINE)
    for name in passes:
        escaped = re.escape(name)
        text = re.sub(rf"^#({escaped}_uki)", r"\1", text, flags=re.MULTILINE)
        text = re.sub(rf"^#({escaped}_options)", r"\1", text, flags=re.MULTILINE)
        text = re.sub(rf"^({escaped}_image=)", r"#\1", text, flags=re.MULTILINE)
    return text
