"""Printing setup: the parsers, the rule it writes, and the row it draws."""

from pathlib import Path

import pytest

from archsetup.core import i18n, printing

# The Arch default, and the line this machine ended up with.
ARCH_DEFAULT = "hosts: mymachines resolve [!UNAVAIL=return] files myhostname dns"
FIXED = (
    "hosts: mymachines mdns_minimal [NOTFOUND=return] "
    "resolve [!UNAVAIL=return] files myhostname dns"
)


def test_mdns_goes_in_front_of_the_resolver_that_ends_the_lookup():
    assert printing.hosts_with_mdns(ARCH_DEFAULT) == FIXED


def test_an_already_fixed_line_is_left_exactly_as_it_is():
    assert printing.hosts_with_mdns(FIXED) == FIXED


def test_mdns_sitting_after_resolve_is_moved_rather_than_accepted():
    """The failure this rewrite exists for: present, ordered wrong, silent.

    `resolve [!UNAVAIL=return]` ends the lookup where it stands, so an mdns
    entry behind it never runs. A file like this reads as configured -- grep
    finds mdns_minimal -- and .local names still do not resolve.
    """
    late = "hosts: mymachines resolve [!UNAVAIL=return] mdns_minimal [NOTFOUND=return] dns"
    fixed = printing.hosts_with_mdns(late)
    fields = fixed.split()
    assert fields.index("mdns_minimal") < fields.index("resolve")
    assert fixed.count("mdns_minimal") == 1


def test_a_multi_word_action_is_not_torn_in_half():
    """[SUCCESS=return NOTFOUND=continue] is one action containing a space.

    split() on the raw line would make "NOTFOUND=continue]" look like a
    service name, and it would then be reordered as one.
    """
    line = "hosts: files [SUCCESS=return NOTFOUND=continue] resolve dns"
    fixed = printing.hosts_with_mdns(line)
    assert "[SUCCESS=return NOTFOUND=continue]" in fixed
    assert fixed.split().index("mdns_minimal") < fixed.split().index("resolve")


def test_without_resolve_the_entry_lands_before_dns():
    fixed = printing.hosts_with_mdns("hosts: files dns")
    assert fixed == "hosts: files mdns_minimal [NOTFOUND=return] dns"


def test_with_neither_anchor_the_entry_is_appended():
    assert printing.hosts_with_mdns("hosts: files") == (
        "hosts: files mdns_minimal [NOTFOUND=return]"
    )


def test_only_the_hosts_line_is_touched():
    text = f"passwd: files\n{ARCH_DEFAULT}\nnetworks: files\n"
    patched = printing.patched_nsswitch(text)
    assert patched is not None
    assert patched.splitlines()[0] == "passwd: files"
    assert patched.splitlines()[2] == "networks: files"
    assert patched.splitlines()[1] == FIXED


def test_a_commented_out_hosts_line_is_not_the_hosts_line():
    text = "#hosts: files\nhosts: files dns\n"
    patched = printing.patched_nsswitch(text)
    assert patched is not None
    assert patched.startswith("#hosts: files\n")


def test_a_file_without_a_hosts_line_reports_that_rather_than_guessing():
    assert printing.patched_nsswitch("passwd: files\n") is None


def test_patching_is_idempotent():
    once = printing.patched_nsswitch(f"{ARCH_DEFAULT}\n")
    assert once is not None
    assert printing.patched_nsswitch(once) == once


def test_mdns_ready_reads_the_live_file(monkeypatch, tmp_path):
    path = tmp_path / "nsswitch.conf"
    path.write_text(f"{ARCH_DEFAULT}\n", encoding="utf-8")
    monkeypatch.setattr(printing, "NSSWITCH", path)
    assert printing.mdns_ready() is False

    path.write_text(f"{FIXED}\n", encoding="utf-8")
    assert printing.mdns_ready() is True


def test_mdns_ready_is_false_when_the_file_cannot_be_read(monkeypatch, tmp_path):
    monkeypatch.setattr(printing, "NSSWITCH", tmp_path / "nope")
    assert printing.mdns_ready() is False


# lpinfo prints the schemes it supports in the same shape as the devices it
# found. Captured from this machine 2026-08-27, with no printer on the network.
LPINFO_OUTPUT = """\
network ipp
network http
network beh
network lpd
file cups-pdf:/
network socket
network ipps
network https
network smb
"""


def test_scheme_lines_are_not_mistaken_for_printers():
    assert printing.discovered_uris(LPINFO_OUTPUT) == []


def test_a_real_device_line_is_picked_up():
    output = LPINFO_OUTPUT + (
        "network dnssd://EPSON%20L3250%20Series._ipp._tcp.local/?uuid=cfe92100\n"
        "network ipp://EPSON52D2EF.local:631/ipp/print\n"
    )
    uris = printing.discovered_uris(output)
    assert uris == [
        "dnssd://EPSON%20L3250%20Series._ipp._tcp.local/?uuid=cfe92100",
        "ipp://EPSON52D2EF.local:631/ipp/print",
    ]


@pytest.mark.parametrize(
    "uri, expected",
    [
        ("ipp://EPSON52D2EF.local:631/ipp/print", "EPSON52D2EF"),
        ("dnssd://EPSON%20L3250%20Series._ipp._tcp.local/", "EPSON_L3250_Series"),
        ("ipps://192.168.1.5:631/ipp/print", "192_168_1_5"),
        ("ipp:///", "printer"),
    ],
)
def test_suggested_queue_names_are_legal(uri, expected):
    """CUPS refuses a name with a space, a slash or a '#' -- lpadmin just fails."""
    name = printing.queue_name(uri)
    assert name == expected
    assert not set(name) & set(" /#")


# Measured shape of `lpstat -l -e` on this machine, LC_ALL=C. The third row is
# the queue CUPS invents from an mDNS announcement: it looks configured and is
# not, so it must not count as one.
LPSTAT_OUTPUT = """\
EPSON_L3256 permanent ipp://localhost/printers/EPSON_L3256 ipp://host.local:631/ipp/print
PDF permanent ipp://localhost/printers/PDF cups-pdf:/
EPSON_L3250_Series network ipps://host.local:631/ipp/print ipps://host.local:631/ipp/print
"""


def test_only_permanent_queues_count(monkeypatch):
    monkeypatch.setattr(printing, "_query", lambda cmd, timeout=5: LPSTAT_OUTPUT)
    assert printing.permanent_queues() == ["EPSON_L3256", "PDF"]


def test_no_answer_from_cupsd_is_an_empty_list(monkeypatch):
    monkeypatch.setattr(printing, "_query", lambda cmd, timeout=5: "")
    assert printing.permanent_queues() == []


def test_the_rule_names_the_family_and_survives_a_round_trip(monkeypatch, tmp_path):
    path = tmp_path / "99-monospace-ttf.conf"
    path.write_text(printing.rule_content("Liberation Mono"), encoding="utf-8")
    monkeypatch.setattr(printing, "FONT_RULE", path)
    assert printing.pinned_family() == "Liberation Mono"


def test_a_family_with_xml_syntax_in_it_cannot_break_the_rule():
    content = printing.rule_content("A & B <mono>")
    assert "A &amp; B &lt;mono&gt;" in content
    assert "<family>A & B" not in content


def test_no_rule_file_means_no_pinned_family(monkeypatch, tmp_path):
    monkeypatch.setattr(printing, "FONT_RULE", tmp_path / "absent.conf")
    assert printing.pinned_family() == ""


def test_the_machines_own_truetype_answer_is_kept(monkeypatch):
    """A second run defends what is in place instead of imposing a font."""
    monkeypatch.setattr(
        printing, "_fc", lambda pattern: ("Hack Nerd Font Mono", "TrueType", "/f.ttf")
    )
    monkeypatch.setattr(printing, "_fc_sorted", lambda pattern: [])
    assert printing._pick_family() == ("Hack Nerd Font Mono", 0)


def test_a_cff_answer_is_replaced_from_fontconfigs_own_ranking(monkeypatch):
    """Captured from this machine's pre-fix state, fc-match -s monospace."""
    ranked = [
        ("Red Hat Mono", "CFF"),
        ("FreeMono", "CFF"),
        ("Adwaita Mono", "TrueType"),
        ("Liberation Mono", "TrueType"),
    ]
    answers = {
        "monospace": ("Red Hat Mono", "CFF", "/RedHatMono-Regular.otf"),
        "Adwaita Mono": ("Adwaita Mono", "TrueType", "/AdwaitaMono.ttf"),
    }
    monkeypatch.setattr(printing, "_fc", lambda pattern: answers[pattern])
    monkeypatch.setattr(printing, "_fc_sorted", lambda pattern: ranked)
    monkeypatch.setattr(
        printing.pacman, "install", lambda repo, aur: pytest.fail("installed a font")
    )
    assert printing._pick_family() == ("Adwaita Mono", 0)


def test_a_family_that_ships_both_formats_is_not_pinned(monkeypatch):
    """The trap that reached a real machine on 2026-08-27.

    redhat-fonts ships Red Hat Mono twice: an OTF under redhat/ and a variable
    TTF under redhat-vf/. So the family appears in the ranked list as
    TrueType, and an alias to that *family* resolves back to the OTF that
    crashes texttopdf. The picker asks for each candidate by name and reads
    the format back, which is the only step that tells the two apart.
    """
    ranked = [("Red Hat Mono", "TrueType"), ("Liberation Mono", "TrueType")]
    answers = {
        "monospace": ("Red Hat Mono", "CFF", "/RedHatMono-Regular.otf"),
        # asked for by name, the mixed family answers with its CFF face
        "Red Hat Mono": ("Red Hat Mono", "CFF", "/RedHatMono-Regular.otf"),
        "Liberation Mono": ("Liberation Mono", "TrueType", "/LiberationMono.ttf"),
    }
    monkeypatch.setattr(printing, "_fc", lambda pattern: answers[pattern])
    monkeypatch.setattr(printing, "_fc_sorted", lambda pattern: ranked)
    assert printing._pick_family() == ("Liberation Mono", 0)


def test_a_machine_with_no_truetype_monospace_gets_a_font_package(monkeypatch):
    installed = []
    monkeypatch.setattr(
        printing, "_fc", lambda pattern: ("Red Hat Mono", "CFF", "/RedHatMono.otf")
    )
    monkeypatch.setattr(printing, "_fc_sorted", lambda pattern: [("Red Hat Mono", "CFF")])
    monkeypatch.setattr(
        printing.pacman, "install", lambda repo, aur: installed.append(repo) or 0
    )
    family, rc = printing._pick_family()
    assert installed == [[printing.FALLBACK_FONT_PACKAGE]]
    assert family == printing.FALLBACK_FONT_FAMILY
    assert rc == 0


def test_the_candidate_walk_is_bounded(monkeypatch):
    """Every candidate costs one fc-match, and a machine can have hundreds."""
    ranked = [(f"Font {n}", "TrueType") for n in range(200)]
    monkeypatch.setattr(printing, "_fc", lambda pattern: ("", "CFF", ""))
    monkeypatch.setattr(printing, "_fc_sorted", lambda pattern: ranked)
    assert len(printing._candidates()) == printing.CANDIDATE_LIMIT


def test_status_is_built_only_out_of_files(monkeypatch, tmp_path):
    """The network menu forbids subprocesses while it draws.

    A shared subprocess.run is what the UI test replaces, so this asserts the
    same property directly on the reader instead of only through the pilot.
    """
    units = tmp_path / "units"
    units.mkdir()
    (units / f"invocation:{printing.CUPS_SERVICE}").write_text("x")
    nsswitch = tmp_path / "nsswitch.conf"
    nsswitch.write_text(f"{FIXED}\n", encoding="utf-8")
    rule = tmp_path / "99.conf"
    rule.write_text(printing.rule_content("Liberation Mono"), encoding="utf-8")

    monkeypatch.setattr(printing, "SYSTEMD_UNITS", units)
    monkeypatch.setattr(printing, "NSSWITCH", nsswitch)
    monkeypatch.setattr(printing, "FONT_RULE", rule)
    monkeypatch.setattr(
        printing.subprocess, "run", lambda *a, **k: pytest.fail("status ran a command")
    )

    line = printing.status()
    assert i18n.t("printing.status_up") in line
    assert i18n.t("printing.status_mdns_yes") in line
    assert "Liberation Mono" in line


def test_status_reports_the_untouched_machine_as_untouched(monkeypatch, tmp_path):
    monkeypatch.setattr(printing, "SYSTEMD_UNITS", tmp_path / "no-units")
    monkeypatch.setattr(printing, "NSSWITCH", tmp_path / "no-nsswitch")
    monkeypatch.setattr(printing, "FONT_RULE", tmp_path / "no-rule")
    line = printing.status()
    assert i18n.t("printing.status_down") in line
    assert i18n.t("printing.status_mdns_no") in line
    assert i18n.t("printing.status_font_none") in line


def test_an_already_correct_machine_is_not_written_to(monkeypatch, tmp_path):
    """The whole point of write_with_backup: no rewrite, no cups restart."""
    nsswitch = tmp_path / "nsswitch.conf"
    nsswitch.write_text(f"{FIXED}\n", encoding="utf-8")
    monkeypatch.setattr(printing, "NSSWITCH", nsswitch)
    monkeypatch.setattr(printing.services, "enable_now", lambda unit: 0)
    monkeypatch.setattr(
        printing.sysedit,
        "write_with_backup",
        lambda *a, **k: pytest.fail("rewrote a correct file"),
    )
    monkeypatch.setattr(
        printing, "run", lambda cmd, **kw: pytest.fail("restarted cups for nothing")
    )
    assert printing._configure_mdns() == 0


def test_queries_pin_the_message_language(monkeypatch):
    """cupsd translates its own output, and "permanent" is a parsed field."""
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)

        class Result:
            stdout = ""

        return Result()

    monkeypatch.setattr(printing.subprocess, "run", fake_run)
    printing._query(["lpstat", "-l", "-e"])
    assert seen["env"]["LC_ALL"] == "C"


def test_the_pdf_queue_is_created_only_when_it_is_missing(monkeypatch):
    calls = []
    monkeypatch.setattr(printing, "run", lambda cmd, **kw: calls.append(cmd) or 0)

    monkeypatch.setattr(printing, "permanent_queues", lambda: [printing.PDF_QUEUE])
    assert printing._ensure_pdf_queue() == 0
    assert calls == []

    monkeypatch.setattr(printing, "permanent_queues", lambda: [])
    assert printing._ensure_pdf_queue() == 0
    assert calls[0][:2] == ["sudo", "lpadmin"]
    assert printing.PDF_URI in calls[0]
    assert printing.PDF_PPD in calls[0]


def test_the_task_is_registered_and_reachable():
    from archsetup.core import tasks
    from archsetup.ui import screens

    task = tasks.get("printing")
    assert task is not None
    assert task.fn is printing.configure
    assert task.state is printing.status
    assert task.group in {group for group, _ in screens.CONFIG_SUBMENUS}


def test_the_generated_rule_is_well_formed_xml():
    """An XML comment cannot contain a double hyphen, and this file explains
    itself at length.

    Measured 2026-08-27: the first version wrote `fc-match monospace --format`
    into its own comment, expat rejected the file at that line, and fontconfig
    dropped the whole rule -- silently, apart from one stderr line nobody was
    reading. The machine went straight back to the CFF face that crashes
    texttopdf. The readback in _configure_font() is what caught it; this test
    is what stops it reaching a machine at all.
    """
    from xml.dom import minidom

    for family in ("Liberation Mono", "JetBrainsMono Nerd Font Mono", "A & B"):
        content = printing.rule_content(family)
        minidom.parseString(content)  # raises if the comment breaks the file
        comment = content[content.index("<!--") + 4 : content.index("-->")]
        assert "--" not in comment, family


def test_a_running_unit_is_a_dangling_symlink(monkeypatch, tmp_path):
    """systemd's invocation entry points at an id, not at a file.

    Measured 2026-08-27: /run/systemd/units/invocation:cups.service is a
    symlink to a 32-hex invocation id that does not exist in that directory,
    so Path.exists() follows it and answers False for a unit that is plainly
    running. The row said "cups stopped" while cupsd was serving jobs.
    """
    units = tmp_path / "units"
    units.mkdir()
    (units / "invocation:cups.service").symlink_to("86e9e3d696d14bf29712f09a93d01abf")
    monkeypatch.setattr(printing, "SYSTEMD_UNITS", units)

    assert (units / "invocation:cups.service").exists() is False  # the trap
    assert printing._unit_running("cups.service") is True
    assert printing._unit_running("smb.service") is False
