"""i18n, config, data files and hardware condition parsing."""

from archsetup.core import config, data, hardware, i18n


def test_translation_and_fallback():
    assert i18n.t("menu.main.title") == "Ana Menü"
    assert i18n.t("nonexistent.key") == "nonexistent.key"
    i18n.load("en")
    assert i18n.t("menu.main.title") == "Main Menu"


def test_unknown_language_falls_back_to_english():
    i18n.load("xx")
    assert i18n.current == "en"


def test_locale_files_have_identical_keys():
    """tr.toml and en.toml must never drift apart."""
    tr_keys = set(i18n._read("tr"))
    en_keys = set(i18n._read("en"))
    assert tr_keys == en_keys, tr_keys.symmetric_difference(en_keys)


def test_available_languages():
    langs = i18n.available()
    assert langs["tr"] == "Türkçe"
    assert langs["en"] == "English"


def test_config_roundtrip(isolated_config):
    config.save({"language": "tr", "theme": "tokyonight-night"})
    assert config.load() == {"language": "tr", "theme": "tokyonight-night"}


def test_config_missing_file(isolated_config):
    assert config.load() == {}


ALL_DATA = [
    ("apps.toml", "postinstall"),
    ("drivers.toml", "postinstall"),
    ("desktops.toml", "postinstall"),
    ("extras.toml", "install"),
]


def test_all_data_files_parse_and_have_locale_names():
    for filename, section in ALL_DATA:
        categories = data.load_categories(filename, section=section)
        assert categories, filename
        for category in categories:
            key = f"category.{category.id}"
            assert i18n.t(key) != key, f"missing locale name for {key}"
            assert category.packages, category.id


def test_post_msg_keys_exist_in_locales():
    for filename, section in ALL_DATA:
        for category in data.load_categories(filename, section=section):
            for pkg in category.packages:
                if pkg.post_msg:
                    assert i18n.t(pkg.post_msg) != pkg.post_msg, pkg.name


def test_notes_are_localized():
    cats = {c.id: c for c in data.load_categories("desktops.toml")}
    ark = next(p for p in cats["plasma"].packages if p.name == "ark")
    assert "Arşiv" in ark.note
    i18n.load("en")
    cats = {c.id: c for c in data.load_categories("desktops.toml")}
    ark = next(p for p in cats["plasma"].packages if p.name == "ark")
    assert "Archive" in ark.note


def test_display_managers():
    dms = data.load_display_managers()
    assert [d.id for d in dms] == ["gdm", "sddm", "sddm-git", "lxdm", "lightdm"]
    assert next(d for d in dms if d.id == "sddm-git").aur is True
    assert next(d for d in dms if d.id == "lightdm").package == "lightdm-gtk-greeter"


def test_fix_terminal_env(monkeypatch):
    from archsetup.__main__ import _fix_terminal_env

    # kitty'den ssh: TERM iletilir, COLORTERM iletilmez -> truecolor bildir
    monkeypatch.setenv("TERM", "xterm-kitty")
    monkeypatch.delenv("COLORTERM", raising=False)
    _fix_terminal_env()
    import os

    assert os.environ["COLORTERM"] == "truecolor"

    # bilinmeyen TERM güvenli değere çekilir, truecolor-dışı terminal
    # için COLORTERM uydurulmaz
    monkeypatch.setenv("TERM", "acayip-terminal-999")
    monkeypatch.delenv("COLORTERM", raising=False)
    _fix_terminal_env()
    assert os.environ["TERM"] == "xterm-256color"
    assert "COLORTERM" not in os.environ

    # bilinen TERM'e dokunulmaz
    monkeypatch.setenv("TERM", "xterm-256color")
    _fix_terminal_env()
    assert os.environ["TERM"] == "xterm-256color"


def test_condition_parsing(monkeypatch):
    monkeypatch.setattr(hardware, "gpu_matches", lambda q: q == "amd")
    monkeypatch.setattr(hardware, "cpu_matches", lambda q: q == "intel")
    assert hardware.condition_ok(None)
    assert hardware.condition_ok("gpu:amd")
    assert not hardware.condition_ok("gpu:nvidia")
    assert hardware.condition_ok("cpu:intel")
    assert hardware.condition_ok("unknown:kind")
