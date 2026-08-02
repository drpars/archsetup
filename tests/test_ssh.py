"""SSH module: inventory, generated config, hardening and rotation."""

from types import SimpleNamespace

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


def test_pub_path_survives_a_dotted_hostname(ssh_env):
    """FQDN hostname'de .pub yolu bozulmamali.

    with_suffix(".pub") "github_host.example.com" icin ".com"u uzanti sayip
    "github_host.example.pub" uretir; anahtar uretildikten hemen sonra acik
    anahtari okumaya calisirken FileNotFoundError ile cokerdi.
    """
    key = ssh_env / "github_host.example.com"
    assert ssh._pub(key).name == "github_host.example.com.pub"
    assert ssh._priv(ssh._pub(key)) == key


def test_generated_key_is_printed_for_a_dotted_hostname(ssh_env, monkeypatch, capsys):
    monkeypatch.setattr(ssh, "machine_id", lambda: "host.example.com")
    monkeypatch.setattr(ssh, "_interactive", lambda: False)

    assert ssh._ensure_github_key() == 0

    out = capsys.readouterr().out
    assert "ssh-ed25519" in out  # acik anahtar gercekten yazdirildi
    assert (ssh_env / "github_host.example.com.pub").is_file()


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


def test_key_without_a_comment_does_not_report_the_blob(ssh_env):
    """Yorumsuz anahtarda son alan base64 govdesidir, yorum degil."""
    (ssh_env / "authorized_keys").write_text(
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBUZUNBASE64GOVDE\n", encoding="utf-8"
    )
    assert ssh.authorized_entries() == [("", "")]


def test_comment_with_spaces_is_kept_whole(ssh_env):
    (ssh_env / "authorized_keys").write_text(
        "ssh-ed25519 AAAAC3Nza github testmakine (desktop/BOARD)\n", encoding="utf-8"
    )
    assert ssh.authorized_entries() == [("", "github testmakine (desktop/BOARD)")]


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


def test_second_forge_block_appears_only_with_its_key(ssh_env, capsys):
    """codeberg.org blogu anahtari olmayan makineye yazilmamali.

    IdentityFile + IdentitiesOnly ile var olmayan bir anahtari sabitlemek,
    o host'a baglanmayi tumden imkansiz kilar. Anahtar disaridan geldigi
    icin (her servise ayri anahtar) varligi kosul.
    """
    ssh._write_config_local()
    assert "Host codeberg.org" not in (ssh_env / "config.local").read_text(
        encoding="utf-8"
    )

    (ssh_env / "codeberg_testmakine").write_text("x", encoding="utf-8")
    ssh._write_config_local()
    text = (ssh_env / "config.local").read_text(encoding="utf-8")
    assert "Host codeberg.org" in text
    assert "IdentityFile ~/.ssh/codeberg_testmakine" in text
    assert "Host github.com" in text  # birincil blok yerinde kaldi
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


def test_include_is_added_even_when_a_comment_mentions_config_local(ssh_env, capsys):
    """Duz alt dizgi aramasi yorum satirina takilirdi.

    Bizim yazdigimiz baslik yorumu "config.local" kelimesini iceriyor;
    Include satiri silinmis olsa bile geri eklenmezdi ve github.com
    yapilandirmasi sessizce devre disi kalirdi.
    """
    (ssh_env / "config").write_text(
        "# Makineye ozel kimlikler config.local icinde; onu archsetup uretir.\n"
        "Host *\n    ForwardAgent no\n",
        encoding="utf-8",
    )
    ssh._ensure_include()
    lines = (ssh_env / "config").read_text(encoding="utf-8").splitlines()
    assert lines[0] == ssh.INCLUDE_LINE
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


def test_harden_refuses_to_run_as_root(ssh_env, monkeypatch, capsys):
    """root altinda AllowUsers root + PermitRootLogin no = kimse giremez."""
    monkeypatch.setattr(ssh.os, "geteuid", lambda: 0)
    assert ssh.harden() == 1
    assert not ssh.DROPIN.exists()
    capsys.readouterr()


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


# --------------------------------------------------------------------------
# ssh-authorize: EKLER, yeniden yazmaz
# --------------------------------------------------------------------------

KEY_LINE = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleAuthorizeKeyAAAAAAAAAAAA masaustu"
)
KEY_BODY = KEY_LINE.split()[1]


@pytest.fixture
def authorize_env(ssh_env, monkeypatch):
    monkeypatch.setattr(ssh, "_interactive", lambda: True)
    monkeypatch.setattr(ssh, "_validate_key", lambda line: "SHA256:sahte")
    monkeypatch.setattr(ssh, "ask_yes", lambda prompt: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": KEY_LINE)
    return ssh_env


def test_authorize_appends_and_keeps_existing_lines(authorize_env):
    keys = authorize_env / "authorized_keys"
    keys.write_text("ssh-ed25519 AAAAOnceden var eski-makine\n", encoding="utf-8")
    _write_inventory(authorize_env, 'format = 1\n[lan]\nsubnet = "192.168.1.0/24"\n')

    assert ssh.authorize() == 0

    text = keys.read_text(encoding="utf-8")
    assert "AAAAOnceden" in text, "mevcut satır silinmiş"
    assert KEY_BODY in text
    assert 'from="192.168.1.0/24"' in text


def test_authorize_backs_up_before_touching_the_file(authorize_env):
    keys = authorize_env / "authorized_keys"
    keys.write_text("ssh-ed25519 AAAAOnceden var eski-makine\n", encoding="utf-8")

    assert ssh.authorize() == 0

    backups = list(authorize_env.glob("authorized_keys.yedek-*"))
    assert len(backups) == 1
    assert "AAAAOnceden" in backups[0].read_text(encoding="utf-8")


def test_authorize_refuses_to_add_the_same_key_twice(authorize_env):
    keys = authorize_env / "authorized_keys"
    keys.write_text(KEY_LINE + "\n", encoding="utf-8")

    assert ssh.authorize() == 0
    assert keys.read_text(encoding="utf-8").count(KEY_BODY) == 1


def test_authorize_rejects_a_line_that_is_not_a_key(authorize_env, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "merhaba dunya")

    assert ssh.authorize() == 1
    assert not (authorize_env / "authorized_keys").exists()


def test_authorize_rejects_a_line_that_already_carries_options(authorize_env, monkeypatch):
    """Iki kisitin hangisinin kazandigi belirsiz kalmasin."""
    monkeypatch.setattr(
        "builtins.input", lambda prompt="": f'from="10.0.0.0/8" {KEY_LINE}'
    )

    assert ssh.authorize() == 1


def test_authorize_stops_when_ssh_keygen_cannot_read_the_key(authorize_env, monkeypatch):
    monkeypatch.setattr(ssh, "_validate_key", lambda line: None)

    assert ssh.authorize() == 1
    assert not (authorize_env / "authorized_keys").exists()


def test_authorize_adds_without_a_restriction_when_no_subnet(authorize_env):
    assert ssh.authorize() == 0

    text = (authorize_env / "authorized_keys").read_text(encoding="utf-8")
    assert text.startswith("ssh-ed25519 ")


def test_generated_host_entry_pins_ipv4(ssh_env, capsys):
    """from= yalnizca IPv4 kapsiyor; istemci IPv6'ya kaymamali."""
    _write_inventory(
        ssh_env, 'format = 1\n[hosts.masaustu]\nhostname = "192.168.1.82"\n'
    )
    ssh._write_config_local()

    text = (ssh_env / "config.local").read_text(encoding="utf-8")
    assert "AddressFamily inet" in text


# --------------------------------------------------------------------------
# ssh-forget: her zaman acik onay
# --------------------------------------------------------------------------


def test_forget_never_runs_without_confirmation(ssh_env, monkeypatch, runlog):
    monkeypatch.setattr(ssh, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "masaustu")
    monkeypatch.setattr(ssh, "run", runlog)
    monkeypatch.setattr(ssh, "ask_yes", lambda prompt: False)
    monkeypatch.setattr(
        ssh.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="192.168.1.82 ssh-ed25519 AAA\n"),
    )

    assert ssh.forget() == 0
    assert runlog.calls == []


def test_forget_resolves_the_inventory_name_to_its_address(ssh_env, monkeypatch, runlog):
    _write_inventory(
        ssh_env, 'format = 1\n[hosts.masaustu]\nhostname = "192.168.1.82"\n'
    )
    monkeypatch.setattr(ssh, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "masaustu")
    monkeypatch.setattr(ssh, "run", runlog)
    monkeypatch.setattr(ssh, "ask_yes", lambda prompt: True)
    monkeypatch.setattr(
        ssh.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="192.168.1.82 ssh-ed25519 AAA\n"),
    )

    assert ssh.forget() == 0
    assert runlog.calls == [["ssh-keygen", "-R", "192.168.1.82"]]
