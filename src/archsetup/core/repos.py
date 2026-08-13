"""Third-party pacman repositories: the stanza, the key, and the order.

Order is the part that is easy to get wrong, and it is not a matter of taste.
man 5 pacman.conf: repositories listed first take precedence over later ones
"regardless of version number". A name is therefore served by the first
repository that carries it, and a newer build further down the file is never
even considered. Appending is right against the official repositories -- they
should win -- and wrong against a second third-party repository publishing the
same names, which is exactly the [ogc]/[g14] pair.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Repo:
    name: str
    server: str
    key: str


# Open Gaming Collective. asus-linux's GitLab is archived read-only and its
# README names OGC as where development continues; the packager on every [g14]
# package is listed as an OGC member, so this is a forwarding address rather
# than a fork. Verified 2026-08-13 against an isolated keyring: ogc.db and the
# packages carry a good signature from this key's [S] subkey.
OGC = Repo(
    "ogc",
    "https://pacman.opengamingcollective.org",
    "F79100EF8C802DAB81C323BB8EEA5962FE510E19",  # gitleaks:allow
)

# Kept, not deleted. [g14] still serves linux-g14, and nothing announces its
# retirement -- only that it stopped publishing. Measured 2026-08-13: the
# database on the server is byte-identical to the copy synced 2026-07-19, and
# its asusctl is four releases and roughly four months behind [ogc]'s. The
# fingerprint has to carry its exemption marker on its own line; gitleaks does
# not look at the line above (measured 2026-08-06).
G14 = Repo(
    "g14",
    "https://arch.asus-linux.org",
    "8F654886F17D497FEFE3DB448B15A6B0E9A3FA35",  # gitleaks:allow
)


def stanza(repo: Repo) -> str:
    return f"[{repo.name}]\nServer = {repo.server}\n"


def has(text: str, name: str) -> bool:
    return re.search(rf"^\[{re.escape(name)}\]", text, re.MULTILINE) is not None


def insert(text: str, repo: Repo, above: Sequence[str] = ()) -> str:
    """Add `repo`'s stanza, above the first of `above` that is present.

    With nothing to outrank this appends, which keeps the official
    repositories ahead of it. With `above` naming a repository that publishes
    the same package names, appending would be silently useless: pacman would
    keep resolving those names from the older repository forever. Returns the
    text unchanged when the repository is already there.
    """
    if has(text, repo.name):
        return text
    body = stanza(repo)
    for name in above:
        match = re.search(rf"^\[{re.escape(name)}\]", text, re.MULTILINE)
        if match:
            return f"{text[:match.start()]}{body}\n{text[match.start():]}"
    return f"{text.rstrip(chr(10))}\n\n{body}"
