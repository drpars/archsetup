"""Printing: the daemon, the name resolution it needs, and the font that
crashes the text filter.

Three of the four parts here are not about printers at all, and that is the
point -- each one was found by a failure that named something else.

**nss-mdns is load-bearing, not a convenience.** `lpadmin -m everywhere`
resolves the dnssd record to `<host>.local` and then hands that name to the
ordinary resolver. Without mDNS in nsswitch the queue cannot be created
("Temporary failure in name resolution"), and the stored device URI stays
name-based afterwards, so every later job needs it too. Order carries the
fix: `resolve [!UNAVAIL=return]` ends the lookup where it stands, so an mdns
entry placed after it is never read.

**The fontconfig rule is a segfault fix wearing a font preference's clothes.**
cups-filters' texttopdf asks fontconfig for "monospace" on the UTF-8 path and
embeds whatever comes back; a CFF/OTF face makes it crash, so plain text
printing dies as "texttopdf ... crashed on signal 11" with nothing pointing at
fonts. Measured 2026-08-27 on cups-filters 2.0.1-2: OTF -> rc=139, TrueType ->
rc=0, and CHARSET=us-ascii never opens a font at all. It has to be a system
file because cupsd runs filters as uid 209 with HOME=/var/spool/cups/tmp --
no per-user config is ever read.

Why the rule names a family instead of just demanding TrueType. A rule that
only assigns `fontformat=TrueType` to the request does work: measured in a
copy of this machine's own /etc/fonts/conf.d, it turned the crash into rc=0.
It was still rejected, because it cannot be scoped to the generic alias.
fontconfig appends the generic family to concrete requests, so `qual="any"`
also steers every explicit monospace family -- an explicit "Red Hat Mono"
request flipped CFF -> TrueType -- and `qual="first"` does not fire at all,
leaving the crash in place. An alias touches only the generic name. So the
criterion is encoded here and the *value* is asked of fontconfig on the
machine being set up, rather than being written into this file.

**cups-pdf ships no queue.** The package installs a backend and two PPDs and
stops there, so installing it and walking away leaves nothing to print to.
The queue matters beyond convenience: it runs the real cupsd filter chain
with the real job environment, which is the only way to verify the font fix
without spending paper.

What is NOT measured here: that `lpadmin -m everywhere` works against any
printer other than the one it was measured on (2026-08-27, an Epson that
announces image/pwg-raster), and the discovery listing was exercised only
with no printer on the network. `lpadmin -m <ppd>` already warns that static
drivers are deprecated and will stop working in a future CUPS; when that
lands, the PDF queue is what breaks, not the network one.
"""

from __future__ import annotations

import os
import re
import subprocess
import urllib.parse
from pathlib import Path

from . import i18n, pacman, prompt, services, sysedit
from .pacman import run

t = i18n.t

REPO_PACKAGES = ("cups", "nss-mdns", "cups-pdf")

# Installed only when the machine has no TrueType monospace at all; the family
# below is the one this package provides, and it is one of the three measured
# to survive texttopdf.
FALLBACK_FONT_PACKAGE = "ttf-liberation"
FALLBACK_FONT_FAMILY = "Liberation Mono"

CUPS_SERVICE = "cups.service"
# nss-mdns depends on avahi, so the package is always there by now; what is
# not automatic is the daemon running, and the NSS module is useless without
# it.
AVAHI_SERVICE = "avahi-daemon.service"

NSSWITCH = Path("/etc/nsswitch.conf")
SYSTEMD_UNITS = Path("/run/systemd/units")
MDNS_MODULE = "mdns_minimal"
MDNS_ACTION = "[NOTFOUND=return]"
# Insert before the first of these that appears. `resolve` is the one that
# actually swallows the lookup; `dns` is the fallback anchor for a machine not
# running systemd-resolved.
MDNS_BEFORE = ("resolve", "dns")

FONT_RULE = Path("/etc/fonts/conf.d/99-monospace-ttf.conf")
FONT_RULE_FAMILY = re.compile(r"<prefer>\s*<family>([^<]*)</family>")
FC_MATCH = "fc-match"
TRUETYPE = "TrueType"

PDF_QUEUE = "PDF"
PDF_URI = "cups-pdf:/"
PDF_PPD = "CUPS-PDF_opt.ppd"

DRIVERLESS_MODEL = "everywhere"
DISCOVERY_SCHEMES = "dnssd,ipp,ipps"
CUPS_WEB = "http://localhost:631/admin"

# lpinfo browses the network and lpstat talks to cupsd; both can sit there when
# something upstream is wrong. Defensive, not measured: no timeout was ever
# observed, but a task that hangs with no output is worse than one that gives
# up. Discovery gets the long one because browsing legitimately takes seconds.
QUERY_TIMEOUT = 5
DISCOVERY_TIMEOUT = 20


# --------------------------------------------------------------------------
# nsswitch.conf
# --------------------------------------------------------------------------


def _tokens(fields: str) -> list[str]:
    """Split an nsswitch entry, keeping each [action] span as one token.

    `[SUCCESS=return NOTFOUND=continue]` is one action containing spaces, so a
    plain split() would tear it in half and the halves would then be treated as
    service names.
    """
    tokens: list[str] = []
    pending: list[str] = []
    for word in fields.split():
        if pending:
            pending.append(word)
            if word.endswith("]"):
                tokens.append(" ".join(pending))
                pending = []
            continue
        if word.startswith("[") and not word.endswith("]"):
            pending = [word]
            continue
        tokens.append(word)
    if pending:
        tokens.append(" ".join(pending))
    return tokens


def _without_mdns(tokens: list[str]) -> list[str]:
    """Drop any mdns module and the action that belongs to it."""
    out: list[str] = []
    drop_action = False
    for token in tokens:
        if token.startswith("mdns"):
            drop_action = True
            continue
        if drop_action:
            drop_action = False
            if token.startswith("["):
                continue
        out.append(token)
    return out


def hosts_with_mdns(line: str) -> str:
    """A `hosts:` line with mdns_minimal in front of the resolver that ends it.

    Rewritten rather than patched in place: a line that already carries mdns
    *after* `resolve` reads as configured and does nothing, so the entry is
    removed and reinserted at the position that works.
    """
    label, _, fields = line.partition(":")
    tokens = _without_mdns(_tokens(fields))
    for anchor in MDNS_BEFORE:
        if anchor in tokens:
            at = tokens.index(anchor)
            break
    else:
        at = len(tokens)
    tokens[at:at] = [MDNS_MODULE, MDNS_ACTION]
    return f"{label}: {' '.join(tokens)}"


def patched_nsswitch(text: str) -> str | None:
    """The whole file with its hosts: line fixed, or None if it has none."""
    out: list[str] = []
    found = False
    for raw in text.splitlines(keepends=True):
        body = raw.rstrip("\n")
        if not found and body.strip().startswith("hosts:"):
            found = True
            indent = body[: len(body) - len(body.lstrip())]
            out.append(indent + hosts_with_mdns(body.strip()) + raw[len(body) :])
        else:
            out.append(raw)
    return "".join(out) if found else None


def mdns_ready() -> bool:
    """Whether the live hosts: line already resolves .local names."""
    try:
        text = NSSWITCH.read_text(encoding="utf-8")
    except OSError:
        return False
    patched = patched_nsswitch(text)
    return patched is not None and patched == text


def _configure_mdns() -> int:
    rc = services.enable_now(AVAHI_SERVICE)
    try:
        text = NSSWITCH.read_text(encoding="utf-8")
    except OSError:
        print(t("printing.nsswitch_unreadable", path=NSSWITCH))
        return rc | 1

    patched = patched_nsswitch(text)
    if patched is None:
        print(t("printing.no_hosts_line", path=NSSWITCH))
        return rc | 1
    if patched == text:
        print(t("printing.mdns_ok"))
        return rc

    write_rc, changed = sysedit.write_with_backup(NSSWITCH, patched)
    rc |= write_rc
    if changed and services.is_active(CUPS_SERVICE):
        # NSS modules are resolved per process. cupsd is already running and
        # would keep answering with the old set until something restarts it.
        rc |= run(["sudo", "systemctl", "restart", CUPS_SERVICE])
    return rc


# --------------------------------------------------------------------------
# the font the text filter embeds
# --------------------------------------------------------------------------


def _fc(pattern: str) -> tuple[str, str, str]:
    """(family, fontformat, file) for a fontconfig pattern, ("", "", "") if unknown.

    fc-match never fails. An unsatisfiable pattern degrades silently to the
    ordinary best match -- measured 2026-08-27, `fontformat=ZZZNOSUCH` still
    returned a TrueType face -- so an answer never means the pattern was
    honoured. What it does report honestly is the format of the font it
    actually picked, which is why every decision here is taken from the
    reported format rather than from having asked for one.
    """
    try:
        out = subprocess.run(
            [FC_MATCH, pattern, r"--format=%{family[0]}\n%{fontformat}\n%{file}"],
            capture_output=True,
            text=True,
            timeout=QUERY_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "", "", ""
    parts = out.stdout.split("\n")
    if out.returncode != 0 or len(parts) < 3:
        return "", "", ""
    return parts[0], parts[1], parts[2]


def monospace() -> tuple[str, str, str]:
    """What a request for the generic `monospace` resolves to right now."""
    return _fc("monospace")


def _fc_sorted(pattern: str) -> list[tuple[str, str]]:
    """(family, fontformat) for every candidate, in fontconfig's own order.

    `fc-match -s` is what keeps the choice out of this file: the ranking is
    fontconfig's, built from the machine's own configuration, and all this
    does is walk it until something usable turns up.
    """
    try:
        out = subprocess.run(
            [FC_MATCH, "-s", pattern, r"--format=%{family[0]}\t%{fontformat}\n"],
            capture_output=True,
            text=True,
            timeout=QUERY_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0:
        return []
    pairs = []
    for line in out.stdout.splitlines():
        family, tab, fontformat = line.partition("\t")
        if tab:
            pairs.append((family, fontformat))
    return pairs


def _yields_truetype(family: str) -> bool:
    """Whether pinning `family` actually gets a TrueType face.

    A family is not a format, and that gap cost a working machine once.
    redhat-fonts ships Red Hat Mono twice -- an OTF under redhat/ and a
    variable TTF under redhat-vf/ -- so asking fontconfig for a TrueType
    monospace answers "Red Hat Mono", and an alias to that *family* resolves
    straight back to the OTF that crashes texttopdf. Measured 2026-08-27: the
    first version of this picker took the constrained answer at its word and
    pinned the CFF face, and the readback afterwards reported it. So every
    candidate is asked for by name and the answer checked.
    """
    _, fontformat, _ = _fc(family)
    return fontformat == TRUETYPE


# Walking every candidate would mean one fc-match per font on the machine.
# Exhausting the cap is not a wrong answer, only a slower path to the same
# one: the fallback package below still runs.
CANDIDATE_LIMIT = 20


def _candidates() -> list[str]:
    """Families worth pinning, best first, without repeating one.

    The machine's current answer goes first when it is already TrueType, so a
    second run defends what is in place rather than replacing it -- including
    the rule this task wrote last time.
    """
    found: list[str] = []
    seen: set[str] = set()

    current, fontformat, _ = monospace()
    if current and fontformat == TRUETYPE:
        found.append(current)
        seen.add(current)

    for family, fontformat in _fc_sorted("monospace"):
        if len(found) >= CANDIDATE_LIMIT:
            break
        if family and fontformat == TRUETYPE and family not in seen:
            found.append(family)
            seen.add(family)
    return found


def _pick_family() -> tuple[str, int]:
    """(family to pin, rc) -- asked of fontconfig, not chosen in this file."""
    for family in _candidates():
        if _yields_truetype(family):
            return family, 0

    print(t("printing.font_no_ttf", pkg=FALLBACK_FONT_PACKAGE))
    rc = pacman.install([FALLBACK_FONT_PACKAGE], [])
    for family in _candidates():
        if _yields_truetype(family):
            return family, rc
    return FALLBACK_FONT_FAMILY, rc


def _xml_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def rule_content(family: str) -> str:
    return f"""<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<!--
  Written by archsetup. Delete this file to undo.

  This is a crash fix, not a font preference. cups-filters' texttopdf asks
  fontconfig for "monospace" on the UTF-8 text path and embeds the face it
  gets back; a CFF/OTF face segfaults it, so printing a plain text file dies
  as "texttopdf ... crashed on signal 11" and nothing in the log mentions
  fonts. Measured 2026-08-27, cups-filters 2.0.1-2: OTF -> rc=139, TrueType
  -> rc=0, CHARSET=us-ascii never opens a font at all.

  It lives in /etc because cupsd runs its filters as uid 209 with
  HOME=/var/spool/cups/tmp; ~/.config/fontconfig is never read by them.

  It is numbered 99 because conf.d is read in numeric order and a font
  package can state its own preference lower down (redhat-fonts uses
  64-redhat-mono.conf). local.conf is read through conf.d/51-local.conf,
  which is below 64 and would lose.

  The family below was asked of fontconfig on this machine, not chosen by
  archsetup: whatever the machine already resolved to, if it was TrueType.
  Read the result back with: fc-match monospace
  (an XML comment cannot contain a double hyphen, so no flags are shown here)
-->
<fontconfig>
  <alias binding="strong">
    <family>monospace</family>
    <prefer><family>{_xml_text(family)}</family></prefer>
  </alias>
</fontconfig>
"""


def _configure_font() -> int:
    family, rc = _pick_family()
    if not family:
        print(t("printing.font_unknown"))
        return rc | 1

    # The default sibling backup lands in a drop-in directory, which is the one
    # hazard write_with_backup warns about -- but measured 2026-08-27 on
    # fontconfig 2.18.3, conf.d loads only *.conf: the same rule as a .bak had
    # no effect while the .conf control did. So the default is inert here.
    write_rc, _ = sysedit.write_with_backup(FONT_RULE, rule_content(family))
    rc |= write_rc

    now_family, now_format, now_file = monospace()
    print(
        t(
            "printing.font_now",
            family=now_family or "?",
            format=now_format or "?",
            path=now_file or "?",
        )
    )
    if now_format != TRUETYPE:
        print(t("printing.font_unfixed", path=FONT_RULE))
        return rc | 1
    return rc


# --------------------------------------------------------------------------
# queues
# --------------------------------------------------------------------------


def _query(cmd: list[str], timeout: int = QUERY_TIMEOUT) -> str:
    """Run a CUPS query with C messages, or return "" if it cannot answer.

    LC_ALL=C because these outputs get parsed: cupsd translates its own
    messages, and "permanent" is a field this file compares against.
    """
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout


def permanent_queues() -> list[str]:
    """Queue names cupsd stores, excluding the ones it invents from mDNS.

    `lpstat -e` alone would also list the temporary queue CUPS conjures from a
    printer's own announcement, which looks like a configured printer and is
    not one -- it carries none of the queue's settings and vanishes with the
    announcement. The long form marks the difference.
    """
    names = []
    for line in _query(["lpstat", "-l", "-e"]).splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "permanent":
            names.append(parts[0])
    return names


def _add_queue(name: str, uri: str, model: str, description: str) -> int:
    return run(
        [
            "sudo",
            "lpadmin",
            "-p",
            name,
            "-E",
            "-v",
            uri,
            "-m",
            model,
            "-D",
            description,
        ]
    )


def _ensure_pdf_queue() -> int:
    if PDF_QUEUE in permanent_queues():
        print(t("printing.pdf_exists", name=PDF_QUEUE))
        return 0
    rc = _add_queue(PDF_QUEUE, PDF_URI, PDF_PPD, t("printing.pdf_desc"))
    if rc == 0:
        print(t("printing.pdf_added", name=PDF_QUEUE))
    return rc


def discovered_uris(output: str) -> list[str]:
    """Device URIs out of `lpinfo -v` output.

    lpinfo lists the schemes it supports on their own lines ("network ipp")
    alongside the devices it found ("network ipp://host/..."), and the two are
    the same shape. Anything without an authority is a scheme, not a printer.
    """
    uris = []
    for line in output.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and "://" in parts[1]:
            uris.append(parts[1].strip())
    return uris


def queue_name(uri: str) -> str:
    """A CUPS-legal queue name suggested from a device URI.

    CUPS rejects a name containing a space, a slash or a '#'; lpadmin does not
    sanitise, it just fails. The authority is the only part of a device URI
    that means anything to a person, and it needs three cuts to get there: the
    percent escapes decoded (a dnssd instance name carries real spaces), the
    service type dropped (`._ipp._tcp` is protocol, not printer), and `.local`
    removed.
    """
    host = uri.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    host = urllib.parse.unquote(host).split("._", 1)[0].removesuffix(".local")
    safe = "".join(char if char.isalnum() else "_" for char in host)
    return safe.strip("_") or "printer"


def _offer_network_queue() -> int:
    print(t("printing.searching"))
    uris = discovered_uris(
        _query(
            ["lpinfo", "--include-schemes", DISCOVERY_SCHEMES, "-v"],
            timeout=DISCOVERY_TIMEOUT,
        )
    )
    if not uris:
        print(t("printing.no_device", url=CUPS_WEB))
        return 0

    for index, uri in enumerate(uris, 1):
        print(f"  {index}) {uri}")
    if not prompt.ask_yes(t("printing.add_q")):
        print(t("printing.add_later", url=CUPS_WEB))
        return 0

    try:
        answer = input(f"{t('printing.which_q', count=len(uris))} ").strip()
    except EOFError:
        return 0
    if not answer.isdigit() or not 1 <= int(answer) <= len(uris):
        print(t("printing.bad_choice"))
        return 1
    uri = uris[int(answer) - 1]

    suggestion = queue_name(uri)
    try:
        name = input(f"{t('printing.name_q', name=suggestion)} ").strip()
    except EOFError:
        name = ""
    name = queue_name(name) if name else suggestion

    rc = _add_queue(name, uri, DRIVERLESS_MODEL, t("printing.queue_desc", uri=uri))
    if rc != 0:
        print(t("printing.add_failed", url=CUPS_WEB))
        return rc
    print(t("printing.queue_added", name=name))
    return rc


# --------------------------------------------------------------------------
# task entry points
# --------------------------------------------------------------------------


def _unit_running(unit: str) -> bool:
    """Whether systemd has a live invocation for `unit`, read from a file.

    `systemctl is-active` is the answer that counts and this is not it. The
    menu draws this row, and a subprocess there is banned outright --
    test_network_menu_draws_state_lines_without_touching_the_machine replaces
    subprocess.run with something that raises, for every module at once. So
    the row reads what systemd leaves on disk: an invocation file per started
    unit. Measured 2026-08-27 against four units, two running and two not; it
    agreed with systemctl every time. It is not a documented interface, so a
    wrong answer is possible -- and harmless, because nothing decides anything
    on it. The two candidates that looked better and were not: the cgroup path
    (cups sits in a nested system-cups.slice, so the obvious path misses a
    running unit) and /run/cups/cups.sock (created by cups.socket, so it is
    there whether or not cupsd is).
    """
    # lexists, not exists: the entry is a symlink whose target is an
    # invocation *id*, not a file, so it is dangling by design and
    # exists() follows it to False for a unit that is plainly running
    # (measured 2026-08-27 against a running cups.service).
    return os.path.lexists(SYSTEMD_UNITS / f"invocation:{unit}")


def pinned_family() -> str:
    """The family this machine's rule pins, or "" when there is no rule."""
    try:
        text = FONT_RULE.read_text(encoding="utf-8")
    except OSError:
        return ""
    found = FONT_RULE_FAMILY.search(text)
    return found.group(1).strip() if found else ""


def status() -> str:
    """One menu line, built only out of files.

    Nothing here asks cupsd or fontconfig, and the reason is not only the
    no-subprocess rule above: `lpstat -l -e` browses for announcements and was
    measured at roughly 1.0 s per call on this machine, which is a second
    added to every redraw of a menu. So the row reports what is *written* --
    the resolver order and the font rule -- and configure() is what reads the
    live answer back out of fc-match after changing it.
    """
    daemon = t(
        "printing.status_up" if _unit_running(CUPS_SERVICE) else "printing.status_down"
    )
    mdns = t("printing.status_mdns_yes" if mdns_ready() else "printing.status_mdns_no")
    family = pinned_family()
    font = (
        t("printing.status_font_rule", family=family)
        if family
        else t("printing.status_font_none")
    )
    return t("printing.status_line", daemon=daemon, mdns=mdns, font=font)


def configure() -> int:
    rc = pacman.install(list(REPO_PACKAGES), [])
    if rc != 0:
        return rc

    # Name resolution before the daemon: the queue that gets added below is
    # reached by name, and cupsd inherits the resolver set it starts with.
    rc |= _configure_mdns()
    rc |= _configure_font()
    rc |= services.enable_now(CUPS_SERVICE)
    rc |= _ensure_pdf_queue()
    rc |= _offer_network_queue()

    print(status())
    print(t("printing.verify", queue=PDF_QUEUE))
    return rc
