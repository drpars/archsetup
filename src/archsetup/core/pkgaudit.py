"""Audit the package names in data/ against the sync databases.

Package lists rot quietly. Names get merged into another package, renamed
without a Replaces, or dropped outright, and nothing says so until someone
picks that category months later.

The failure is worse than one missing program, because installs go through
one `pacman -S --needed <all of them>`: a single unresolvable name aborts
the whole transaction with "target not found" and *nothing* gets installed.
So one dead entry silently disables an entire category.

Names fall into four cases, and telling them apart matters:

  ok        a package by exactly that name exists
  group     not a package but a group, which pacman installs happily
  provided  resolves to a differently named package, via provides or
            replaces (mlocate -> plocate). Installing still works, so this
            is a warning, not an error
  missing   nothing resolves it. This is what breaks a category

AUR entries are reported as unchecked: answering for them would mean
querying the AUR over the network, and this audit is meant to stay offline
and safe to run anywhere.
"""

from __future__ import annotations

import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .. import paths
from . import i18n

t = i18n.t

OK = "ok"
GROUP = "group"
PROVIDED = "provided"
MISSING = "missing"
AUR = "aur"


@dataclass(frozen=True)
class Finding:
    source: str  # data file, relative to data/
    category: str
    name: str
    status: str
    resolved: str = ""  # the package a PROVIDED name actually resolves to


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
    first = out.stdout.strip().splitlines()
    return first[0].strip() if first else None


def classify(name: str, aur: bool = False) -> tuple[str, str]:
    """Return (status, resolved_name) for one package name."""
    if aur:
        return AUR, ""
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


def audit(data_dir: Path | None = None) -> list[Finding]:
    root = data_dir or paths.DATA_DIR
    findings: list[Finding] = []
    for path in sorted(root.rglob("*.toml")):
        source = str(path.relative_to(root))
        for category, name, aur in _entries(path):
            status, resolved = classify(name, aur)
            findings.append(Finding(source, category, name, status, resolved))
    return findings


def report(findings: list[Finding]) -> int:
    """Print the problems and return an exit code (non-zero if any are fatal)."""
    missing = [f for f in findings if f.status == MISSING]
    provided = [f for f in findings if f.status == PROVIDED]
    aur = [f for f in findings if f.status == AUR]

    if missing:
        print(t("pkgaudit.missing_header"))
        for f in missing:
            print(f"  {f.source} [{f.category}]  {f.name}")
        # Name the categories: one dead entry takes the whole install with it.
        broken = sorted({f"{f.source} [{f.category}]" for f in missing})
        print(t("pkgaudit.missing_effect", categories=", ".join(broken)))
        print()

    if provided:
        print(t("pkgaudit.provided_header"))
        for f in provided:
            print(f"  {f.source} [{f.category}]  {f.name} -> {f.resolved}")
        print()

    print(t(
        "pkgaudit.summary",
        total=len(findings),
        missing=len(missing),
        provided=len(provided),
        aur=len(aur),
    ))
    return 1 if missing else 0


def run() -> int:
    return report(audit())
