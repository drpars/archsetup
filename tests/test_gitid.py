"""Git machine identity: generated .gitconfig.local, appended allowed_signers."""

import pytest

from archsetup.core import gitid

KEY_A = "AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyAAAAAAAAAAAAAAAAAAAAAAAAAA"
KEY_B = "AAAAC3NzaC1lZDI1NTE5AAAAIAnotherKeyBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture
def git_env(tmp_path, monkeypatch):
    """Every path the module touches lands in tmp_path."""
    home = tmp_path / "home"
    ssh_dir = home / ".ssh"
    ssh_dir.mkdir(parents=True)
    (ssh_dir / "github_testmakine.pub").write_text(
        f"ssh-ed25519 {KEY_A} github testmakine\n", encoding="utf-8"
    )

    # _tilde() Path.home()'a bakar; HOME'u da tasimazsak uretilen dosyada
    # tmp yolu gorunur ve testin dogruladigi sey uretimdekinden baska olur.
    monkeypatch.setenv("HOME", str(home))

    monkeypatch.setattr(gitid.ssh, "SSH_DIR", ssh_dir)
    monkeypatch.setattr(gitid.ssh, "machine_id", lambda: "testmakine")
    monkeypatch.setattr(gitid, "GITCONFIG", home / ".gitconfig")
    monkeypatch.setattr(gitid, "GITCONFIG_LOCAL", home / ".gitconfig.local")
    monkeypatch.setattr(gitid, "ALLOWED_SIGNERS", home / ".config/git/allowed_signers")
    monkeypatch.setattr(gitid, "_git_email", lambda: "kisi@example.com")
    monkeypatch.setattr(gitid, "_check_include", lambda: None)
    monkeypatch.setattr(gitid.shutil, "which", lambda name: "/usr/bin/git")
    return home


def test_generates_both_files(git_env, capsys):
    assert gitid.configure() == 0

    local = (git_env / ".gitconfig.local").read_text(encoding="utf-8")
    assert "format = ssh" in local
    assert "signingkey = ~/.ssh/github_testmakine.pub" in local
    assert "gpgsign = true" in local

    signers = (git_env / ".config/git/allowed_signers").read_text(encoding="utf-8")
    assert f"kisi@example.com ssh-ed25519 {KEY_A}" in signers


def test_other_machines_keys_are_never_dropped(git_env):
    """allowed_signers bir güven listesi: yeniden üretilmez, eklenir."""
    signers = git_env / ".config/git/allowed_signers"
    signers.parent.mkdir(parents=True)
    signers.write_text(f"kisi@example.com ssh-ed25519 {KEY_B}\n", encoding="utf-8")

    assert gitid.configure() == 0

    text = signers.read_text(encoding="utf-8")
    assert KEY_B in text, "masaüstünün anahtarı silinmiş"
    assert KEY_A in text


def test_running_twice_does_not_duplicate_the_key(git_env):
    assert gitid.configure() == 0
    assert gitid.configure() == 0

    text = (git_env / ".config/git/allowed_signers").read_text(encoding="utf-8")
    assert text.count(KEY_A) == 1


def test_same_key_under_another_address_is_reported(git_env, capsys):
    signers = git_env / ".config/git/allowed_signers"
    signers.parent.mkdir(parents=True)
    signers.write_text(f"eski@example.com ssh-ed25519 {KEY_A}\n", encoding="utf-8")

    assert gitid.configure() == 0

    out = capsys.readouterr().out
    assert "eski@example.com" in out
    # Satır bize ait değil; düzeltmeye kalkışmaz.
    assert signers.read_text(encoding="utf-8").count(KEY_A) == 1


def test_missing_key_stops_before_writing_anything(git_env, monkeypatch, capsys):
    (git_env / ".ssh" / "github_testmakine.pub").unlink()

    assert gitid.configure() == 1
    assert not (git_env / ".gitconfig.local").exists()


def test_missing_email_stops_before_writing_anything(git_env, monkeypatch):
    monkeypatch.setattr(gitid, "_git_email", lambda: "")

    assert gitid.configure() == 1
    assert not (git_env / ".gitconfig.local").exists()


def test_handwritten_local_is_not_overwritten_without_consent(git_env, monkeypatch):
    local = git_env / ".gitconfig.local"
    local.write_text("# elle yazildi\n[user]\n\tsigningkey = elle\n", encoding="utf-8")
    monkeypatch.setattr(gitid, "ask_yes", lambda prompt: False)

    assert gitid.configure() == 1
    assert "elle yazildi" in local.read_text(encoding="utf-8")


def test_consented_overwrite_keeps_a_backup(git_env, monkeypatch):
    local = git_env / ".gitconfig.local"
    local.write_text("# elle yazildi\n", encoding="utf-8")
    monkeypatch.setattr(gitid, "ask_yes", lambda prompt: True)

    assert gitid.configure() == 0
    assert "elle yazildi" in (git_env / ".gitconfig.local.bak").read_text(
        encoding="utf-8"
    )
    assert "format = ssh" in local.read_text(encoding="utf-8")


def test_our_own_file_is_replaced_without_asking(git_env, monkeypatch):
    monkeypatch.setattr(
        gitid, "ask_yes", lambda prompt: pytest.fail("sorulmamaliydi")
    )
    assert gitid.configure() == 0
    assert gitid.configure() == 0


def test_signer_bodies_ignores_comments_and_reads_the_body():
    text = (
        "# yorum\n"
        "\n"
        f"a@example.com ssh-ed25519 {KEY_A} yorum alani\n"
        f"b@example.com,c@example.com ssh-ed25519 {KEY_B}\n"
    )
    assert gitid.signer_bodies(text) == {
        KEY_A: "a@example.com",
        KEY_B: "b@example.com,c@example.com",
    }
