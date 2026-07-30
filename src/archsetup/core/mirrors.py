"""Shared reflector arguments for mirror ranking.

The installer and the post-install task both rank mirrors, and they used to
build the arguments separately and disagree: the post-install one asked which
country to prefer, the installer one did not. That divergence mattered most
in the wrong direction, since the live ISO is where a slow mirror is felt.

Two things measured on a 100 Mbit line, worth keeping in mind before
"improving" any of this:

`--latest N` does not mean "the N best mirrors", it means "the N most
recently synced", and only those get timed afterwards. Asking for 10 that way
produced a run rating Singapore, Taipei, Johannesburg and Los Angeles -- all
four timed out, half a minute gone, and Taiwan still placed second because
nothing better was measured. Raising N does not fix that; it just times more
mirrors. Going from 10 to 20 candidates cost 29 extra seconds and picked no
better mirror.

`--sort rate` downloads from every candidate to time it, and that is the
whole cost: 43-72 s against 0 s for `--sort score`, which uses the score the
mirror status API already computed. Measured top speeds were 8.6 MB/s for
rate against 6.4 MB/s for score. Over a ~800 MB pacstrap that is about 32
seconds saved -- less than the ranking spent earning it. So the installer
sorts by score, and only the post-install task, which the user runs
deliberately and which may precede gigabytes of packages, pays for rate.
"""

from __future__ import annotations

# Rate this many candidates when timing them...
CANDIDATES = "10"
# ...and keep this many in the final list.
KEEP = "10"
# Skip mirrors that have not synced recently: they serve stale packages, and
# pacman then fails on signatures or missing targets for no obvious reason.
MAX_AGE_HOURS = "12"
# An unreachable mirror should cost seconds, not the default wait.
CONNECT_TIMEOUT = "3"
DOWNLOAD_TIMEOUT = "5"


def reflector_args(country: str = "", thorough: bool = False) -> list[str]:
    """Argument list for reflector.

    thorough=False sorts by the published score and downloads nothing;
    thorough=True times each candidate, which is slower but measures the
    connection as it actually is right now.
    """
    args = ["--protocol", "https", "--age", MAX_AGE_HOURS]
    if thorough:
        args += [
            "--connection-timeout", CONNECT_TIMEOUT,
            "--download-timeout", DOWNLOAD_TIMEOUT,
            "--latest", CANDIDATES,
            "--sort", "rate",
        ]
    else:
        args += ["--sort", "score"]
    args += ["--number", KEEP]
    if country.strip():
        args += ["--country", country.strip()]
    return args


def rank(runner, save_path: str, country: str = "", thorough: bool = False,
         sudo: bool = False) -> int:
    """Rank mirrors into save_path, falling back to worldwide if needed.

    A country filter that matches nothing makes reflector exit with "no
    mirrors found" and write no list at all, which is a worse outcome than
    ignoring the preference. That is easy to hit: reflector matches the
    country name exactly, so "Türkiye" works and "Turkey" does not, and some
    countries genuinely host no mirrors. Retrying worldwide turns a typo into
    a slightly less local mirrorlist instead of a failed step.
    """
    prefix = (["sudo"] if sudo else []) + ["reflector", "--verbose"]
    if country.strip():
        rc = runner([*prefix, *reflector_args(country, thorough),
                     "--save", save_path])
        if rc == 0:
            return rc
    return runner([*prefix, *reflector_args("", thorough), "--save", save_path])
