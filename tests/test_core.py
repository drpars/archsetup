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


def test_aur_helper_is_never_silenced(monkeypatch):
    """AUR yardımcısına --noconfirm geçilmez.

    Yardımcının PKGBUILD diff istemi, zehirlenmiş bir paketle bu makine
    arasındaki tek şey. "Kurulum akıcı olsun" diye eklenecek tek bir bayrak
    onu sessizce kapatır; bu test o bayrağı bekliyor.
    """
    from archsetup.core import pacman

    calls = []
    monkeypatch.setattr(pacman, "run", lambda cmd, **kw: calls.append(cmd) or 0)
    monkeypatch.setattr(pacman, "ensure_aur_helper", lambda: "yay")

    pacman.install(["repo-pkg"], ["aur-pkg"])

    aur_cmd = next(cmd for cmd in calls if cmd[0] == "yay")
    assert "--noconfirm" not in aur_cmd


def test_no_source_line_silences_an_aur_helper():
    """Yukarıdaki test yalnızca install()'ı görüyor; bu tarama tüm kaynağı.

    Sınırı açık olsun: satır bazlı arama, komutu birden çok satıra yayan
    bir çağrıyı kaçırır. Yine de bayrağı elle yazan birinin en olası
    yaptığı şey tek satırlık bir ekleme.
    """
    from pathlib import Path

    from archsetup.core.pacman import AUR_HELPERS

    def silences_a_helper(line: str) -> bool:
        # Tırnaklı dizge aranıyor: kuralı anlatan yorum ve docstring satırları
        # bayrağı düz metin olarak yazıyor ve ilk hâli onlara takılıyordu.
        if '"--noconfirm"' not in line:
            return False
        # Yardımcının adı çağrı yerinde çoğunlukla bir değişken ("helper"),
        # sabit değil; yalnızca sabitleri aramak en olası ihlali kaçırırdı.
        return "helper" in line or any(f'"{h}"' in line for h in AUR_HELPERS)

    assert silences_a_helper('run([helper, "-S", "--needed", "--noconfirm"])')
    assert silences_a_helper('run(["yay", "-S", "--noconfirm", pkg])')
    assert not silences_a_helper('run(["pacman", "-S", "--noconfirm", pkg])')
    assert not silences_a_helper("# the helper is never given --noconfirm")

    src = Path(__file__).resolve().parents[1] / "src"
    offenders = [
        f"{path.name}:{no}"
        for path in src.rglob("*.py")
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if silences_a_helper(line)
    ]
    assert offenders == []
