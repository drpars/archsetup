"""SSH module: inventory, generated config, hardening and rotation."""

import pytest

from archsetup.core import ssh


@pytest.fixture
def ssh_env(tmp_path, monkeypatch):
    """Redirect every path the module touches into tmp_path."""
    home = tmp_path / "home"
    ssh_dir = home / ".ssh"
    ssh_dir.mkdir(parents=True)
    etc = tmp_path / "etc"
    etc.mkdir()

    monkeypatch.setattr(ssh, "SSH_DIR", ssh_dir)
    monkeypatch.setattr(ssh, "ARCHIVE_DIR", home / ".ssh-arsiv")
    monkeypatch.setattr(ssh, "INVENTORY", ssh_dir / "archsetup.toml")
    monkeypatch.setattr(ssh, "CONFIG", ssh_dir / "config")
    monkeypatch.setattr(ssh, "CONFIG_LOCAL", ssh_dir / "config.local")
    monkeypatch.setattr(ssh, "AUTHORIZED_KEYS", ssh_dir / "authorized_keys")
    monkeypatch.setattr(ssh, "SSHD_CONFIG", etc / "sshd_config")
    monkeypatch.setattr(ssh, "DROPIN_DIR", etc / "sshd_config.d")
    monkeypatch.setattr(ssh, "DROPIN", etc / "sshd_config.d" / "10-local.conf")

    # Makine tespiti deterministik olsun.
    monkeypatch.setattr(ssh, "machine_id", lambda: "testmakine")
    monkeypatch.setattr(ssh, "chassis", lambda: "desktop")
    monkeypatch.setattr(ssh, "board", lambda: "TEST-BOARD")
    return ssh_dir


def _write_inventory(ssh_dir, body):
    (ssh_dir / "archsetup.toml").write_text(body, encoding="utf-8")


# --------------------------------------------------------------------------
# Envanter
# --------------------------------------------------------------------------


def test_machine_id_slugifies(monkeypatch, tmp_path):
    hostname = tmp_path / "hostname"
    hostname.write_text("PANTHERA ARCH\n", encoding="utf-8")
    monkeypatch.setattr(ssh.Path, "read_text", lambda self, **kw: "PANTHERA ARCH\n")
    assert ssh.machine_id() == "panthera-arch"


def test_missing_inventory_is_not_an_error(ssh_env):
    assert ssh.read_inventory() is None


def test_malformed_inventory_reports_and_returns_none(ssh_env, capsys):
    _write_inventory(ssh_env, "format = = 1\n")
    assert ssh.read_inventory() is None
    assert "archsetup.toml" in capsys.readouterr().out


def test_marker_is_created_with_format_version(ssh_env):
    ssh.write_marker()
    text = (ssh_env / "archsetup.toml").read_text(encoding="utf-8")
    assert f"format = {ssh.INVENTORY_FORMAT}" in text
    assert (ssh_env / "archsetup.toml").stat().st_mode & 0o777 == 0o600


def test_marker_does_not_overwrite_existing(ssh_env):
    _write_inventory(ssh_env, 'format = 1\ncreated = "eski"\n')
    ssh.write_marker()
    assert "eski" in (ssh_env / "archsetup.toml").read_text(encoding="utf-8")


def test_invalid_subnet_is_rejected(ssh_env, capsys):
    _write_inventory(ssh_env, '[lan]\nsubnet = "192.168.1.0"\n')
    assert ssh.lan_subnet() is None
    assert "192.168.1.0" in capsys.readouterr().out


def test_valid_subnet_passes(ssh_env):
    _write_inventory(ssh_env, '[lan]\nsubnet = "192.168.1.0/24"\n')
    assert ssh.lan_subnet() == "192.168.1.0/24"


# --------------------------------------------------------------------------
# authorized_keys okuma (yazma yok)
# --------------------------------------------------------------------------


def test_authorized_entries_parses_options_and_comment(ssh_env):
    (ssh_env / "authorized_keys").write_text(
        "# yorum\n"
        "ssh-ed25519 AAAAC3Nza kisitsiz\n"
        'from="192.168.1.0/24" ssh-ed25519 AAAAC3Nzb kisitli\n',
        encoding="utf-8",
    )
    entries = ssh.authorized_entries()
    assert entries == [
        ("", "kisitsiz"),
        ('from="192.168.1.0/24"', "kisitli"),
    ]


def test_audit_reports_missing_from_without_touching_the_file(ssh_env, capsys):
    """Denetim rapor verir; authorized_keys ASLA yeniden yazilmaz.

    Bozuk bir from= degeri anahtarin hicbir zaman eslesmemesine yol acar,
    yani makineye uzaktan erisimi tamamen kapatir.
    """
    keys = ssh_env / "authorized_keys"
    original = "ssh-ed25519 AAAAC3Nza kisitsiz\n"
    keys.write_text(original, encoding="utf-8")
    _write_inventory(ssh_env, '[lan]\nsubnet = "192.168.1.0/24"\n')

    ssh._audit_from_restriction()

    assert "kisitsiz" in capsys.readouterr().out
    assert keys.read_text(encoding="utf-8") == original


def test_running_agent_is_left_alone(monkeypatch, runlog, capsys):
    """Calisan agent yeniden baslatilmaz.

    Soketi restart etmek agent'taki tum cozulmus anahtarlari dusurur ve
    kullanici her calistirmada parolalarini yeniden girmek zorunda kalir.
    """
    monkeypatch.setattr(ssh.services, "user_unit_exists", lambda name: True)
    monkeypatch.setattr(ssh, "_unit_state", lambda unit, user=False: "active")
    monkeypatch.setattr(ssh, "run", runlog)

    assert ssh._enable_agent() == 0
    assert runlog.calls == []
    capsys.readouterr()


def test_agent_is_started_when_inactive(monkeypatch, runlog, capsys):
    monkeypatch.setattr(ssh.services, "user_unit_exists", lambda name: True)
    monkeypatch.setattr(ssh, "_unit_state", lambda unit, user=False: "inactive")
    monkeypatch.setattr(ssh, "run", runlog)

    assert ssh._enable_agent() == 0
    assert runlog.calls == [
        ["systemctl", "--user", "enable", "--now", "ssh-agent.socket"]
    ]
    capsys.readouterr()


def test_agent_count_is_zero_without_a_socket(tmp_path, monkeypatch):
    """SSH_AUTH_SOCK yok ve soket dosyasi da yoksa ssh-add hic cagrilmaz.

    Bu kurulumda SSH_AUTH_SOCK bilerek ayarlanmiyor (soketi ssh_config'teki
    IdentityAgent gosteriyor), o yuzden geri dusus yolu dogru calismali.
    """
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert ssh._agent_key_count() == 0


# --------------------------------------------------------------------------
# Uretilen istemci yapilandirmasi
# --------------------------------------------------------------------------


def test_config_local_has_github_identity(ssh_env, capsys):
    ssh._write_config_local()
    text = (ssh_env / "config.local").read_text(encoding="utf-8")
    assert "Host github.com" in text
    assert "IdentityFile ~/.ssh/github_testmakine" in text
    capsys.readouterr()


def test_config_local_includes_inventory_hosts(ssh_env, capsys):
    _write_inventory(
        ssh_env,
        "[hosts.laptop]\n"
        'hostname = "10.0.0.5"\n'
        'user = "kullanici"\n'
        'key = "laptop_ed25519"\n',
    )
    (ssh_env / "laptop_ed25519").write_text("x", encoding="utf-8")
    ssh._write_config_local()
    text = (ssh_env / "config.local").read_text(encoding="utf-8")
    assert "Host laptop" in text
    assert "HostName 10.0.0.5" in text
    assert "User kullanici" in text
    assert "IdentityFile ~/.ssh/laptop_ed25519" in text
    capsys.readouterr()


def test_config_local_warns_when_host_key_is_absent(ssh_env, capsys):
    _write_inventory(
        ssh_env,
        '[hosts.laptop]\nhostname = "10.0.0.5"\nkey = "yok_ed25519"\n',
    )
    ssh._write_config_local()
    assert "yok_ed25519" in capsys.readouterr().out


def test_include_is_prepended_so_host_star_stays_last(ssh_env, capsys):
    """Include en ustte olmali: ssh ilk eslesen degeri kullanir."""
    (ssh_env / "config").write_text("Host *\n    ForwardAgent no\n", encoding="utf-8")
    ssh._ensure_include()
    lines = (ssh_env / "config").read_text(encoding="utf-8").splitlines()
    assert lines[0] == ssh.INCLUDE_LINE
    assert lines[-1].strip() == "ForwardAgent no"
    capsys.readouterr()


def test_include_is_not_duplicated(ssh_env, capsys):
    (ssh_env / "config").write_text(f"{ssh.INCLUDE_LINE}\n", encoding="utf-8")
    ssh._ensure_include()
    text = (ssh_env / "config").read_text(encoding="utf-8")
    assert text.count("config.local") == 1
    capsys.readouterr()


# --------------------------------------------------------------------------
# Sunucu sertlestirme
# --------------------------------------------------------------------------


def _prepare_harden(ssh_env, monkeypatch, fake_write, runner):
    ssh.SSHD_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    ssh.SSHD_CONFIG.write_text(
        "Include /etc/ssh/sshd_config.d/*.conf\n"
        "PubkeyAuthentication no\n"
        "PasswordAuthentication no\n"
        "Subsystem sftp /usr/lib/ssh/sftp-server\n",
        encoding="utf-8",
    )
    (ssh_env / "authorized_keys").write_text(
        "ssh-ed25519 AAAAC3Nza yetkili\n", encoding="utf-8"
    )
    monkeypatch.setattr(ssh, "sudo_write", fake_write)
    monkeypatch.setattr(ssh, "run", runner)
    monkeypatch.setattr(ssh, "_unit_state", lambda unit, user=False: "active")


def test_harden_writes_dropin_and_neutralises_conflicts(
    ssh_env, monkeypatch, fake_write, runlog, capsys
):
    _prepare_harden(ssh_env, monkeypatch, fake_write, runlog)

    assert ssh.harden() == 0

    dropin = ssh.DROPIN.read_text(encoding="utf-8")
    assert "PubkeyAuthentication yes" in dropin
    assert "PasswordAuthentication no" in dropin
    assert "PermitRootLogin no" in dropin

    main = ssh.SSHD_CONFIG.read_text(encoding="utf-8")
    assert "#PubkeyAuthentication no" in main
    assert "#PasswordAuthentication no" in main
    # Cakismayan satirlar korunur.
    assert "Subsystem sftp /usr/lib/ssh/sftp-server" in main
    capsys.readouterr()


def test_harden_rolls_back_when_validation_fails(
    ssh_env, monkeypatch, fake_write, capsys
):
    """sshd -t basarisizsa sshd'ye hic dokunulmaz, eski hâl geri gelir."""
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        return 1 if cmd[:3] == ["sudo", "sshd", "-t"] else 0

    _prepare_harden(ssh_env, monkeypatch, fake_write, runner)
    original = ssh.SSHD_CONFIG.read_text(encoding="utf-8")

    assert ssh.harden() == 1
    assert ssh.SSHD_CONFIG.read_text(encoding="utf-8") == original
    assert not any(cmd[:3] == ["sudo", "systemctl", "restart"] for cmd in calls)
    capsys.readouterr()


def test_harden_refuses_key_only_without_authorized_keys(
    ssh_env, monkeypatch, fake_write, runlog, capsys
):
    """Yetkili anahtar yokken parola girisini kapatmak kilitlenme demektir."""
    _prepare_harden(ssh_env, monkeypatch, fake_write, runlog)
    (ssh_env / "authorized_keys").unlink()
    monkeypatch.setattr(ssh, "ask_yes", lambda prompt: False)

    assert ssh.harden() == 1
    assert not ssh.DROPIN.exists()
    capsys.readouterr()


def test_harden_allows_password_login_when_confirmed(
    ssh_env, monkeypatch, fake_write, runlog, capsys
):
    _prepare_harden(ssh_env, monkeypatch, fake_write, runlog)
    (ssh_env / "authorized_keys").unlink()
    monkeypatch.setattr(ssh, "ask_yes", lambda prompt: True)

    assert ssh.harden() == 0
    assert "PasswordAuthentication yes" in ssh.DROPIN.read_text(encoding="utf-8")
    capsys.readouterr()


# --------------------------------------------------------------------------
# Anahtar yenileme
# --------------------------------------------------------------------------


def test_rotate_archives_old_key_and_reports_fingerprint(
    ssh_env, monkeypatch, capsys
):
    key = ssh_env / "github_testmakine"
    key.write_text("ozel", encoding="utf-8")
    key.with_suffix(".pub").write_text("acik", encoding="utf-8")

    monkeypatch.setattr(ssh, "github_key", lambda: key)
    monkeypatch.setattr(ssh, "fingerprint", lambda pub: "SHA256:ESKI")
    monkeypatch.setattr(ssh, "ask_yes", lambda prompt: True)
    monkeypatch.setattr(ssh, "_ensure_github_key", lambda allow_new=False: 0)

    assert ssh.rotate() == 0
    assert not key.exists()
    assert list(ssh.ARCHIVE_DIR.glob("github_testmakine.*"))
    assert "SHA256:ESKI" in capsys.readouterr().out


def test_rotate_is_cancellable(ssh_env, monkeypatch, capsys):
    key = ssh_env / "github_testmakine"
    key.write_text("ozel", encoding="utf-8")
    key.with_suffix(".pub").write_text("acik", encoding="utf-8")

    monkeypatch.setattr(ssh, "github_key", lambda: key)
    monkeypatch.setattr(ssh, "fingerprint", lambda pub: "SHA256:ESKI")
    monkeypatch.setattr(ssh, "ask_yes", lambda prompt: False)

    assert ssh.rotate() == 0
    assert key.exists()
    capsys.readouterr()
