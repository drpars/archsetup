"""Tests for core.uki and the preset rewriting under it.

The task reads /etc/mkinitcpio.d and writes into it, so ROOT and both command
line locations are module constants and every one of them is pointed at
tmp_path. That matters more here than usual: the machine running this suite
does boot by UKI, which is exactly the state the gate looks for, so an
unsealed constant would let the tests pass by reading the developer's laptop.

The two preset shapes below are transcribed rather than generated, and they
are the before and after of one measured event: the hand edit of 2026-08-13
that gave linux-zen a boot entry on this laptop. STOCK is
/usr/share/mkinitcpio/hook.preset with %PKGBASE% substituted (kept as
linux-zen.preset.sablon), CONVERTED is the file that produced
arch-linux-zen.efi and made the kernel appear in the menu. Building either of
them with set_uki_output would make the rule agree with itself.
"""

from pathlib import Path

import pytest

from archsetup.core import mkinitcpio, sysedit, uki

STOCK = """# mkinitcpio preset file for the '{base}' package

#ALL_config="/etc/mkinitcpio.conf"
ALL_kver="/boot/vmlinuz-{base}"
#ALL_kerneldest="/boot/vmlinuz-{base}"

PRESETS=('default')
#PRESETS=('default' 'fallback')

#default_config="/etc/mkinitcpio.conf"
default_image="/boot/initramfs-{base}.img"
#default_uki="{esp}/arch-{base}.efi"
#default_options="--splash /usr/share/systemd/bootctl/splash-arch.bmp"

#fallback_config="/etc/mkinitcpio.conf"
#fallback_image="/boot/initramfs-{base}-fallback.img"
#fallback_uki="{esp}/arch-{base}-fallback.efi"
#fallback_options="-S autodetect"
"""

CONVERTED = """# mkinitcpio preset file for the '{base}' package

ALL_config="/etc/mkinitcpio.conf"
ALL_kver="/boot/vmlinuz-{base}"
#ALL_kerneldest="/boot/vmlinuz-{base}"

PRESETS=('default')
#PRESETS=('default' 'fallback')

#default_config="/etc/mkinitcpio.conf"
#default_image="/boot/initramfs-{base}.img"
default_uki="{esp}/arch-{base}.efi"
default_options="--splash /usr/share/systemd/bootctl/splash-arch.bmp"

#fallback_config="/etc/mkinitcpio.conf"
#fallback_image="/boot/initramfs-{base}-fallback.img"
#fallback_uki="{esp}/arch-{base}-fallback.efi"
#fallback_options="-S autodetect"
"""


def _with_fallback_image(text: str) -> str:
    """A preset that also builds a fallback image, which the template does not."""
    text = text.replace("PRESETS=('default')\n", "PRESETS=('default' 'fallback')\n", 1)
    return text.replace("#fallback_image=", "fallback_image=", 1)


class Env:
    def __init__(self, root: Path, esp: Path):
        self.root = root
        self.esp = esp
        self.dir = root / "etc/mkinitcpio.d"

    def write(self, base: str, template: str) -> Path:
        path = self.dir / f"{base}.preset"
        path.write_text(template.format(base=base, esp=self.esp), encoding="utf-8")
        return path

    def text(self, base: str) -> str:
        return (self.dir / f"{base}.preset").read_text(encoding="utf-8")

    def build(self, base: str, suffix: str = "") -> Path:
        """Stand in for what mkinitcpio -P would have produced."""
        image = self.esp / f"arch-{base}{suffix}.efi"
        image.write_bytes(b"x" * 2048)
        return image


@pytest.fixture
def env(tmp_path, monkeypatch, runlog, fake_write):
    root = tmp_path / "root"
    (root / "etc/mkinitcpio.d").mkdir(parents=True)
    (root / "etc/kernel").mkdir(parents=True)
    (root / "etc/kernel/cmdline").write_text("root=PARTUUID=abc rw\n")
    esp = tmp_path / "efi/EFI/Linux"
    esp.mkdir(parents=True)

    monkeypatch.setattr(uki, "ROOT", root)
    monkeypatch.setattr(uki, "CMDLINE", root / "etc/kernel/cmdline")
    monkeypatch.setattr(uki, "CMDLINE_D", root / "etc/cmdline.d")
    monkeypatch.setattr(uki, "ask_yes", lambda q: True)
    # The real write_with_backup runs; only its two sudo halves are replaced,
    # so the assertions below read the bytes the task actually produced.
    monkeypatch.setattr(sysedit, "run", runlog)
    monkeypatch.setattr(sysedit, "sudo_write", fake_write)
    return Env(root, esp)


# --- the four-line rule ------------------------------------------------------


def test_the_rule_reproduces_the_hand_edit_that_boots_this_laptop():
    """Byte equality against the pair above, which is the only place the
    output of this rule has been compared with an image known to boot."""
    esp = "/efi/EFI/Linux"
    assert mkinitcpio.set_uki_output(
        STOCK.format(base="linux-zen", esp=esp), ["default"]
    ) == CONVERTED.format(base="linux-zen", esp=esp)


def test_set_uki_output_leaves_presets_and_unnamed_passes_alone():
    text = _with_fallback_image(STOCK.format(base="linux-zen", esp="/efi/EFI/Linux"))
    out = mkinitcpio.set_uki_output(text, ["default"])

    # Which passes exist is the preset's own business.
    assert "\nPRESETS=('default' 'fallback')\n" in out
    # And a pass that was not named keeps writing what it wrote.
    assert '\nfallback_image="/boot/initramfs-linux-zen-fallback.img"' in out
    assert "\n#fallback_uki=" in out
    assert "\n#fallback_options=" in out


def test_set_uki_output_changes_nothing_without_the_line_to_enable():
    """The regex matching nothing and the conversion having happened look the
    same from outside, which is why the caller re-reads the result."""
    text = "PRESETS=('default')\ndefault_image=\"/boot/initramfs-linux.img\"\n"
    out = mkinitcpio.set_uki_output(text, ["default"])
    assert mkinitcpio.preset_value(out, "default_uki") is None


def test_preset_passes_drops_the_shell_quoting():
    assert mkinitcpio.preset_passes("PRESETS=('default' 'fallback')\n") == [
        "default",
        "fallback",
    ]
    assert mkinitcpio.preset_passes("MODULES=()\n") == []


# --- image sizes -------------------------------------------------------------


def test_sizes_falls_back_to_sudo_only_for_what_it_cannot_read(tmp_path, monkeypatch):
    readable = tmp_path / "readable.efi"
    readable.write_bytes(b"x" * 10)
    hidden = Path("/efi/EFI/Linux/arch-linux-zen.efi")

    asked = []

    def fake_stat(paths):
        asked.extend(paths)
        return f"1024 {hidden}\n"

    monkeypatch.setattr(mkinitcpio, "_sudo_stat", fake_stat)
    assert mkinitcpio.sizes([readable, hidden]) == {readable: 10, hidden: 1024}
    # One call, and only about the file that could not be stat()ed directly.
    assert asked == [hidden]


def test_sizes_without_an_answer_reports_what_it_could_read(tmp_path):
    """`_sudo_stat` is sealed to "" by conftest -- a refused or absent sudo."""
    assert mkinitcpio.sizes([tmp_path / "gone.efi"]) == {}


# --- the gate ----------------------------------------------------------------


def test_a_machine_that_does_not_boot_by_uki_is_refused(env, capsys):
    env.write("linux", STOCK)

    assert uki.configure() == 1
    assert "linux-zen" not in capsys.readouterr().out
    # Refusing means refusing to write, not writing and reporting.
    assert 'default_image="/boot/initramfs-linux.img"' in env.text("linux")


def test_nothing_to_do_when_every_preset_already_builds_one(env, runlog):
    env.write("linux-ogc", CONVERTED)
    env.write("linux-zen", CONVERTED)
    before = env.text("linux-zen")

    assert uki.configure() == 0
    assert env.text("linux-zen") == before
    # Nothing to convert is also nothing to rebuild: -P on every preset on the
    # machine is not a free way to end a task that found no work.
    assert runlog.calls == []


def test_a_missing_command_line_stops_before_anything_is_written(env, capsys):
    (env.root / "etc/kernel/cmdline").unlink()
    env.write("linux-ogc", CONVERTED)
    env.write("linux-zen", STOCK)

    assert uki.configure() == 1
    assert 'default_image="/boot/initramfs-linux-zen.img"' in env.text("linux-zen")
    assert "/proc/cmdline" in capsys.readouterr().out


def test_a_command_line_drop_in_counts_as_one(env):
    (env.root / "etc/kernel/cmdline").unlink()
    (env.root / "etc/cmdline.d").mkdir()
    (env.root / "etc/cmdline.d/10-root.conf").write_text("root=PARTUUID=abc rw\n")
    env.write("linux-ogc", CONVERTED)
    env.write("linux-zen", STOCK)
    env.build("linux-zen")

    assert uki.configure() == 0


def test_a_preset_without_the_line_is_refused_rather_than_half_converted(env, capsys):
    env.write("linux-ogc", CONVERTED)
    (env.dir / "linux-hand.preset").write_text(
        "PRESETS=('default')\ndefault_image=\"/boot/initramfs-linux-hand.img\"\n"
    )

    assert uki.configure() == 1
    assert "default_uki" in capsys.readouterr().out


# --- the conversion ----------------------------------------------------------


def test_the_new_kernel_gets_what_the_booting_one_has(env, runlog, capsys):
    env.write("linux-ogc", CONVERTED)
    env.write("linux-zen", STOCK)
    produced = env.build("linux-zen")

    assert uki.configure() == 0

    text = env.text("linux-zen")
    assert f'default_uki="{produced}"' in text
    assert '\n#default_image="/boot/initramfs-linux-zen.img"' in text
    assert '\nALL_config="/etc/mkinitcpio.conf"' in text
    assert "\ndefault_options=" in text
    # The preset that already booted is not touched.
    assert env.text("linux-ogc") == CONVERTED.format(base="linux-ogc", esp=env.esp)
    assert ["sudo", "mkinitcpio", "-P"] in runlog.calls
    out = capsys.readouterr().out
    assert str(produced) in out
    # systemd-boot orders by version, so the new kernel becomes the default on
    # its own -- measured on this laptop, and not what a fallback plan expects.
    assert "bootloader-info" in out


def test_a_pass_the_reference_does_not_build_is_left_alone_and_said_so(env, capsys):
    """The reference decides which passes become UKIs, so this task can never
    be the reason a 214 MiB fallback image lands on an ESP sized for one."""
    env.write("linux-ogc", CONVERTED)
    path = env.write("linux-zen", STOCK)
    path.write_text(_with_fallback_image(path.read_text()), encoding="utf-8")
    env.build("linux-zen")

    assert uki.configure() == 0

    text = env.text("linux-zen")
    assert '\nfallback_image="/boot/initramfs-linux-zen-fallback.img"' in text
    assert "\n#fallback_uki=" in text
    assert "\nPRESETS=('default' 'fallback')\n" in text
    assert "fallback" in capsys.readouterr().out


def test_declining_writes_nothing(env, monkeypatch, runlog):
    monkeypatch.setattr(uki, "ask_yes", lambda q: False)
    env.write("linux-ogc", CONVERTED)
    env.write("linux-zen", STOCK)

    assert uki.configure() == 0
    assert 'default_image="/boot/initramfs-linux-zen.img"' in env.text("linux-zen")
    assert runlog.calls == []


def test_the_backup_is_not_a_second_preset(env, runlog):
    env.write("linux-ogc", CONVERTED)
    env.write("linux-zen", STOCK)
    env.build("linux-zen")

    assert uki.configure() == 0

    copies = [cmd for cmd in runlog.calls if cmd[:2] == ["sudo", "cp"]]
    assert len(copies) == 1
    # mkinitcpio globs *.preset; a name ending in a date is not read as one.
    assert not copies[0][-1].endswith(".preset")
    assert Path(copies[0][-1]).parent == env.dir


# --- what came out of it -----------------------------------------------------


def test_an_image_the_preset_names_but_that_is_not_there_fails(env, capsys):
    """The pair matters: with one image present, sudo demonstrably worked, so
    the other one's absence is a finding rather than a failed measurement."""
    env.write("linux-ogc", CONVERTED)
    env.write("linux-zen", STOCK)
    env.write("linux-lts", STOCK)
    env.build("linux-zen")  # linux-lts deliberately not built

    assert uki.configure() != 0
    assert "arch-linux-lts.efi" in capsys.readouterr().out


def test_nothing_measurable_is_reported_as_unknown_not_as_failure(env, capsys):
    """`_sudo_stat` sealed to "" is what a refused sudo looks like. Calling
    that a missing boot entry would report a failure never established."""
    env.write("linux-ogc", CONVERTED)
    env.write("linux-zen", STOCK)

    assert uki.configure() == 0
    assert "sudo ls -l" in capsys.readouterr().out
