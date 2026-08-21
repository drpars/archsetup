"""Audit the package names in data/ against the sync databases (and the AUR).

Package lists rot quietly. Names get merged into another package, renamed
without a Replaces, or dropped outright, and nothing says so until someone
picks that category months later.

The failure is worse than one missing program, because installs go through
one `pacman -S --needed <all of them>`: a single unresolvable name aborts
the whole transaction with "target not found" and *nothing* gets installed.
So one dead entry silently disables an entire category.

Repository names fall into four cases, and telling them apart matters:

  ok        a package by exactly that name exists
  group     not a package but a group, which pacman installs happily.
            `pacman -Si` fails on groups, so reading that failure as
            "missing" would condemn a perfectly installable entry
  provided  resolves to a differently named package, via provides or
            replaces (mlocate -> plocate). Installing still works, so this
            is a warning, not an error
  missing   nothing resolves it. This is what breaks a category

Everything above reads the local sync databases under /var/lib/pacman/sync,
which pacman downloaded on the last -Sy. No network, no root. That also
sets the limit worth knowing: the audit is only as current as those files,
so a stale index can report "clean" about a name that died last week. Hence
the staleness check in report().

AUR entries need the network, so they are skipped unless audit(..., aur=True)
is asked for -- and even then a failed lookup degrades to "unchecked"
instead of failing the run.
"""

from __future__ import annotations

import ast
import json
import subprocess
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .. import paths
from . import i18n

t = i18n.t

# Repository statuses.
OK = "ok"
GROUP = "group"
PROVIDED = "provided"
MISSING = "missing"

# AUR statuses.
AUR_UNCHECKED = "aur-unchecked"
AUR_OK = "aur-ok"
AUR_MISSING = "aur-missing"
AUR_IN_REPO = "aur-in-repo"

# Secondary AUR notes; an entry can carry several.
ORPHAN = "orphan"
OUTDATED = "outdated"
UNWATCHED = "unwatched"

# What "unwatched" means, as two numbers rather than a feeling. Neither
# axis says anything on its own: a -git PKGBUILD barely changes because it
# does not have to (wlogout, 77 votes, untouched for 850 days, is fine),
# and a niche package can be kept perfectly by one person (walker,
# 26 votes, touched 35 days ago). It is the pair that describes an entry
# nobody is looking at -- which is the profile the 2026 "Atomic Arch"
# campaign adopted and poisoned, one step before the orphan flag catches it.
#
# The numbers are a line drawn through this catalogue's own measured
# spread (2026-08-21, 28 AUR entries): votes ran 1..2366 with nine entries
# in single digits, and days-since-packaging ran 0..1582 with a clear gap
# between 347 and 442. Together they flag five entries; either alone
# flags twelve, which is wallpaper rather than a warning.
LOW_VOTES = 10
AUR_STALE_DAYS = 365

FATAL = (MISSING, AUR_MISSING)

# The task modules themselves: pacman.install() calls live here, and the AUR
# names in them never reach data/, so nothing else in this file can see them.
TASK_SOURCES = Path(__file__).resolve().parent

SYNC_DIR = Path("/var/lib/pacman/sync")
STALE_AFTER_DAYS = 7

AUR_RPC = "https://aur.archlinux.org/rpc/v5/info"
AUR_BATCH = 100
AUR_TIMEOUT = 15


@dataclass(frozen=True)
class Finding:
    source: str  # data file, relative to data/
    category: str
    name: str
    status: str
    resolved: str = ""  # the package a PROVIDED name actually resolves to
    notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TaskPackage:
    name: str
    where: str  # core/<module>.py:<line>


def _install_aur_arg(call: ast.Call) -> ast.expr | None:
    """The second argument of a pacman.install(repo, aur) call, or None."""
    if not (isinstance(call.func, ast.Attribute) and call.func.attr == "install"):
        return None
    return call.args[1] if len(call.args) == 2 else None


def _literal_names(node: ast.expr, scope: dict[str, object]) -> list[str]:
    """Package names out of an argument that is not always a literal list.

    Three shapes appear in these modules and a plain-text search sees only
    the first: a literal list, `[*CONSTANT]`, and a bare local whose value is
    decided by an if/else. Measured 2026-08-21 -- grepping for the literal
    form missed binder_linux-dkms and both ASUS packages, which is exactly
    the kind of quietly short answer this listing exists to stop giving.
    """
    try:
        return [str(name) for name in ast.literal_eval(node)]
    except (ValueError, TypeError, SyntaxError):
        pass
    if isinstance(node, ast.List):
        names: list[str] = []
        for element in node.elts:
            if isinstance(element, ast.Starred) and isinstance(element.value, ast.Name):
                names += [str(n) for n in scope.get(element.value.id, ())]
        return names
    if isinstance(node, ast.Name):
        return [str(n) for n in scope.get(node.id, ())]
    return []


def task_aur_packages() -> list[TaskPackage] | None:
    """AUR names a task installs directly, or None when the sources are unreadable.

    None rather than an empty list on failure. "No task installs an AUR
    package" and "there was nothing here to read" are different answers, and
    printing an empty section for the second one is the shape of a clean
    negative that has not actually looked anywhere.
    """
    modules = sorted(TASK_SOURCES.glob("*.py"))
    if not modules:
        return None

    found: list[TaskPackage] = []
    for module in modules:
        try:
            tree = ast.parse(module.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        # Module constants first, then per-function locals: `aur_pkgs` in the
        # ASUS task is assigned in one branch of an if/else and left empty in
        # the other, so the names only exist inside that function.
        # Two passes, because ast.walk() is not source order and one of these
        # names is defined in terms of another: `aur_pkgs = [*ASUS_PACKAGES]`
        # only resolves once the module constant is already known.
        scope: dict[str, list[str]] = {}
        for resolve_through_scope in (False, True):
            for node in ast.walk(tree):
                targets = []
                if isinstance(node, ast.Assign):
                    targets = [t for t in node.targets if isinstance(t, ast.Name)]
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    targets = [node.target] if node.value is not None else []
                if not targets or node.value is None:
                    continue
                if resolve_through_scope:
                    names = _literal_names(node.value, scope)
                else:
                    try:
                        value = ast.literal_eval(node.value)
                    except (ValueError, TypeError, SyntaxError):
                        continue
                    if not isinstance(value, (list, tuple)):
                        continue
                    names = [str(v) for v in value]
                for target in targets:
                    for name in names:
                        if name not in scope.setdefault(target.id, []):
                            scope[target.id].append(name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            arg = _install_aur_arg(node)
            if arg is None:
                continue
            for name in _literal_names(arg, scope):
                found.append(TaskPackage(name, f"{module.name}:{node.lineno}"))
    return found


def _pacman_ok(args: list[str]) -> bool:
    return subprocess.run(
        ["pacman", *args], capture_output=True, text=True
    ).returncode == 0


def _resolves_to(name: str) -> str | None:
    """The package name pacman would actually install, or None.

    -dd skips dependency resolution so the output is the target alone
    rather than its whole dependency tree, and --print keeps it a query:
    nothing is downloaded and no root is needed.
    """
    out = subprocess.run(
        ["pacman", "-Sddp", "--print-format", "%n", name],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        return None
    lines = out.stdout.strip().splitlines()
    return lines[0].strip() if lines else None


def classify(name: str) -> tuple[str, str]:
    """Return (status, resolved_name) for one repository package name."""
    if _pacman_ok(["-Si", name]):
        return OK, name
    if _pacman_ok(["-Sg", name]):
        return GROUP, name
    resolved = _resolves_to(name)
    if resolved is None:
        return MISSING, ""
    if resolved == name:
        return OK, name
    return PROVIDED, resolved


def aur_info(names: list[str]) -> dict[str, dict] | None:
    """Look names up in the AUR. None means the lookup itself failed.

    None and {} mean very different things -- "we could not ask" versus
    "we asked and none of them exist" -- so a network failure must never
    collapse into a pile of missing packages.
    """
    found: dict[str, dict] = {}
    for start in range(0, len(names), AUR_BATCH):
        batch = names[start:start + AUR_BATCH]
        query = urllib.parse.urlencode({"arg[]": batch}, doseq=True)
        try:
            with urllib.request.urlopen(
                f"{AUR_RPC}?{query}", timeout=AUR_TIMEOUT
            ) as response:
                payload = json.load(response)
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return None
        for entry in payload.get("results", []):
            found[entry["Name"]] = entry
    return found


def _unwatched(info: dict) -> str | None:
    """The note for an entry that is both little-voted and long untouched.

    Carries the two numbers, because the tag alone cannot be acted on: what
    to do about a package differs at 2 votes and 1582 days from what to do
    at 9 votes and 400.
    """
    votes = info.get("NumVotes") or 0
    modified = info.get("LastModified")
    if votes >= LOW_VOTES or not modified:
        return None
    days = int((time.time() - modified) // 86400)
    if days < AUR_STALE_DAYS:
        return None
    return f"{UNWATCHED} {votes} votes/{days}d"


def _classify_aur(name: str, info: dict | None) -> tuple[str, tuple[str, ...]]:
    # Ask the repositories first, whatever the AUR said. A package that
    # graduated usually has its AUR entry deleted afterwards as a duplicate,
    # so "not in the AUR" is the normal look of a *promoted* package, not
    # only of a dead one. Calling those missing would be a false alarm on
    # exactly the packages that are in the best shape.
    in_repo = _pacman_ok(["-Si", name])
    if info is None:
        return (AUR_IN_REPO if in_repo else AUR_MISSING), ()
    notes = []
    if not info.get("Maintainer"):
        notes.append(ORPHAN)
    if info.get("OutOfDate"):
        notes.append(OUTDATED)
    unwatched = _unwatched(info)
    if unwatched:
        notes.append(unwatched)
    # Still in the AUR but also in the repos: nothing breaks, but aur = true
    # rebuilds from source what already ships as a signed binary.
    return (AUR_IN_REPO if in_repo else AUR_OK), tuple(notes)


def _entries(path: Path) -> list[tuple[str, str, bool]]:
    """(category, package name, is_aur) for one data file."""
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)

    found = []
    for cat in raw.get("category", []):
        for pkg in cat.get("packages", []):
            found.append((cat["id"], pkg["name"], bool(pkg.get("aur", False))))
    # displaymanagers.toml keeps its packages under a different key.
    for dm in raw.get("dm", []):
        found.append(("dm", dm.get("package", dm["id"]), bool(dm.get("aur", False))))
    return found


def audit(data_dir: Path | None = None, aur: bool = False) -> list[Finding]:
    root = data_dir or paths.DATA_DIR
    collected: list[tuple[str, str, str, bool]] = []
    for path in sorted(root.rglob("*.toml")):
        source = str(path.relative_to(root))
        for category, name, is_aur in _entries(path):
            collected.append((source, category, name, is_aur))

    aur_names = sorted({name for _, _, name, is_aur in collected if is_aur})
    lookup = aur_info(aur_names) if (aur and aur_names) else None

    findings = []
    for source, category, name, is_aur in collected:
        if not is_aur:
            status, resolved = classify(name)
            findings.append(Finding(source, category, name, status, resolved))
        elif lookup is None:
            findings.append(Finding(source, category, name, AUR_UNCHECKED))
        else:
            status, notes = _classify_aur(name, lookup.get(name))
            findings.append(Finding(source, category, name, status, notes=notes))
    return findings


def configured_repos() -> set[str] | None:
    """The repositories pacman actually reads, or None if that cannot be asked.

    None rather than an empty set on failure: with no answer the honest move
    is to judge every database, not to declare every database irrelevant.
    pacman-conf ships in the pacman package itself, so the failure branch is
    about a broken config, not a missing tool.
    """
    out = subprocess.run(
        ["pacman-conf", "--repo-list"], capture_output=True, text=True
    )
    if out.returncode != 0:
        return None
    return {line.strip() for line in out.stdout.split() if line.strip()}


def stale_databases(days: int = STALE_AFTER_DAYS,
                    sync_dir: Path | None = None) -> list[tuple[str, int]]:
    """(repo name, age in days) for sync databases older than `days`.

    Without this the audit can report a clean bill of health from an index
    that predates the rename it was meant to catch -- a wrong answer that
    looks exactly like a right one.

    Only databases belonging to a configured repository count. pacman never
    deletes the sync database of a repository taken out of pacman.conf, so
    the file sits there ageing forever and nothing ever refreshes it --
    measured here, g14.db was 32 days old and reported as stale while [g14]
    had already been removed from pacman.conf and pacman was not reading the
    file at all. A warning that fires on data nobody is reading teaches the
    reader to skip the one that fires on data they are.
    """
    root = sync_dir or SYNC_DIR
    repos = configured_repos()
    now = time.time()
    stale = []
    for db in sorted(root.glob("*.db")):
        if repos is not None and db.stem not in repos:
            continue
        try:
            age = int((now - db.stat().st_mtime) // 86400)
        except OSError:
            continue
        if age >= days:
            stale.append((db.stem, age))
    return stale


def _print_group(header_key: str, rows: list[str]) -> None:
    if not rows:
        return
    print(t(header_key))
    for row in rows:
        print(f"  {row}")
    print()


def report(findings: list[Finding], sync_dir: Path | None = None,
           aur_requested: bool = False) -> int:
    """Print the problems and return an exit code (non-zero if any are fatal)."""
    def of(*statuses):
        return [f for f in findings if f.status in statuses]

    def names(items) -> int:
        """Distinct package names, for the summary line only.

        A name may appear in more than one category on purpose: some
        categories group by topic and some by "what X needs", so the same
        package is listed on both axes (yazi_extras, passthrough). The
        detail blocks above print one line per entry, because the category
        is what tells you where to fix it -- but the summary says "N
        paket", and counting entries there makes it claim more packages
        than the catalogue has.
        """
        return len({f.name for f in items})

    missing = of(MISSING)
    aur_missing = of(AUR_MISSING)
    provided = of(PROVIDED)
    in_repo = of(AUR_IN_REPO)
    unchecked = of(AUR_UNCHECKED)
    aur_entries = of(AUR_OK, AUR_MISSING, AUR_IN_REPO, AUR_UNCHECKED)
    flagged = [f for f in findings if f.notes]

    # Repo and AUR packages go to pacman and to the helper as two separate
    # transactions, so a dead name in one does not touch the other. Keeping
    # the two lists apart is what makes the stated blast radius true.
    if missing:
        _print_group(
            "pkgaudit.missing_header",
            [f"{f.source} [{f.category}]  {f.name}" for f in missing],
        )
        broken = sorted({f"{f.source} [{f.category}]" for f in missing})
        print(t("pkgaudit.missing_effect", categories=", ".join(broken)))
        print()

    if aur_missing:
        _print_group(
            "pkgaudit.aur_missing_header",
            [f"{f.source} [{f.category}]  {f.name}" for f in aur_missing],
        )
        broken = sorted({f"{f.source} [{f.category}]" for f in aur_missing})
        print(t("pkgaudit.aur_missing_effect", categories=", ".join(broken)))
        print()

    _print_group(
        "pkgaudit.provided_header",
        [f"{f.source} [{f.category}]  {f.name} -> {f.resolved}" for f in provided],
    )
    _print_group(
        "pkgaudit.in_repo_header",
        [f"{f.source} [{f.category}]  {f.name}" for f in in_repo],
    )
    _print_group(
        "pkgaudit.flagged_header",
        [f"{f.source} [{f.category}]  {f.name}  ({', '.join(f.notes)})"
         for f in flagged],
    )

    stale = stale_databases(sync_dir=sync_dir)
    if stale:
        _print_group(
            "pkgaudit.stale_header",
            [t("pkgaudit.stale_row", repo=name, days=age) for name, age in stale],
        )

    # The AUR clause has to say which of the three states this run was in.
    # A fixed "(unchecked)" label read as "the AUR was skipped" even on a run
    # that had just queried it and printed its findings a few lines above.
    if not aur_entries:
        aur_clause = ""
    elif unchecked:
        aur_clause = t("pkgaudit.summary_aur_unchecked", count=names(unchecked))
    else:
        aur_clause = t("pkgaudit.summary_aur_checked", count=names(aur_entries))

    print(t(
        "pkgaudit.summary",
        total=names(findings),
        missing=names(missing + aur_missing),
        provided=names(provided),
        aur=aur_clause,
    ))
    # Only worth suggesting when --aur was not asked for. After a failed
    # lookup the run already said the AUR was unreachable, and telling it to
    # pass the flag it just passed reads as if the flag had been ignored.
    if unchecked and not aur_requested:
        print(t("pkgaudit.aur_hint"))
    return 1 if (missing or aur_missing) else 0


def _aur_row(name: str, info: dict | None, where: str) -> str:
    """One inventory line: what it is called, how watched it is, where it lives.

    The numbers are the point. --check-packages names an AUR entry only when
    something is wrong with it, so a healthy catalogue prints a count and
    nothing else -- and the question "what AUR surface do we actually carry"
    had no answer short of reading data/ by hand.
    """
    if info is None:
        return f"{name:<30} {'?':>6} {'?':>7}  {where}"
    votes = info.get("NumVotes")
    modified = info.get("LastModified")
    days = f"{int((time.time() - modified) // 86400)}g" if modified else "?"
    notes = []
    if not info.get("Maintainer"):
        notes.append(ORPHAN)
    if info.get("OutOfDate"):
        notes.append(OUTDATED)
    if _unwatched(info):
        notes.append(UNWATCHED)
    if _pacman_ok(["-Si", name]):
        notes.append(AUR_IN_REPO)
    tail = ("  (" + ", ".join(notes) + ")") if notes else ""
    return f"{name:<30} {votes if votes is not None else '?':>6} {days:>7}  {where}{tail}"


def list_aur(data_dir: Path | None = None) -> int:
    """Print every AUR package this tool can install, catalogue and tasks alike.

    Two sections because they are found two different ways and only one of
    them is auditable: catalogue entries come out of data/, task packages out
    of the call sites in core/. Keeping them apart is not cosmetic -- it says
    which half --check-packages is watching.
    """
    root = data_dir or paths.DATA_DIR
    catalogue: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.toml")):
        source = str(path.relative_to(root))
        for category, name, is_aur in _entries(path):
            if is_aur:
                catalogue.setdefault(name, []).append(f"{source} [{category}]")

    task_packages = task_aur_packages()
    task_map: dict[str, list[str]] = {}
    for entry in task_packages or []:
        task_map.setdefault(entry.name, []).append(entry.where)

    lookup = aur_info(sorted(set(catalogue) | set(task_map))) or {}
    if not lookup:
        print(t("pkgaudit.aur_unreachable"))
        print()

    _print_group(
        "pkgaudit.list_catalogue_header",
        [_aur_row(name, lookup.get(name), ", ".join(where))
         for name, where in sorted(catalogue.items())],
    )

    if task_packages is None:
        # Not the same sentence as "there are none": the sources were not
        # readable, so this run never looked.
        print(t("pkgaudit.list_tasks_unreadable", path=TASK_SOURCES))
        print()
    else:
        _print_group(
            "pkgaudit.list_tasks_header",
            [_aur_row(name, lookup.get(name), ", ".join(where))
             for name, where in sorted(task_map.items())],
        )

    print(t("pkgaudit.list_summary",
            catalogue=len(catalogue),
            tasks="?" if task_packages is None else len(task_map)))
    print(t("pkgaudit.list_legend"))
    return 0


def run(aur: bool = False) -> int:
    findings = audit(aur=aur)
    if aur and any(f.status == AUR_UNCHECKED for f in findings):
        print(t("pkgaudit.aur_unreachable"))
    return report(findings, aur_requested=aur)
