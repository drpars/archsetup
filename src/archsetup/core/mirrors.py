"""Shared reflector arguments for mirror ranking.

The installer and the post-install task both rank mirrors, and they used to
build the arguments separately and disagree: the post-install one asked which
country to prefer, the installer one did not. That divergence mattered most
in the wrong direction, since the live ISO is where a slow mirror is felt.

Three things measured, worth keeping in mind before "improving" any of this:

`--latest N` does not mean "the N best mirrors", it means "the N most
recently synced", and only those get timed afterwards. Asking for 10 that way
produced a run rating Singapore, Taipei, Johannesburg and Los Angeles -- all
four timed out, half a minute gone, and Taiwan still placed second because
nothing better was measured. Raising N does not fix that; it just times more
mirrors. Going from 10 to 20 candidates cost 29 extra seconds and picked no
better mirror.

It is tempting to read that as "then preselect by something better", and
measurement says no. On 2026-08-24 the filtered pool held 363 mirrors, and
three ways of drawing ten from it were timed back to back: --latest, --score,
and a random sample. Fastest mirror 3593 / 3593 / 3103 KiB/s, median 749 /
828 / 847 -- indistinguishable. The published score does not predict bandwidth
to any particular machine either: Spearman -0.13 over 26 mirrors, and the
best-scored band's median was 837 KiB/s against 754 KiB/s for the worst-scored.
So the preselect is not the thing to fix, and the Taiwan run above was an
artifact of the floor below rather than of the pool: with the floor corrected
those "timed out" mirrors resolve to real numbers and sort where they belong
(Johannesburg 157 KiB/s, last).

`--download-timeout` is a speed floor, not a patience setting -- see
MIN_RATE_KIB below. Setting it too low fails silently, because a mirror that
did not finish in time reads 0.00 KiB/s, which is exactly what a dead mirror
reads.

`--sort rate` downloads from every candidate to time it, and that is the
whole cost: 43-72 s against 0 s for `--sort score`, which uses the score the
mirror status API already computed. On a 100 Mbit line measured top speeds
were 8.6 MB/s for rate against 6.4 MB/s for score -- about 32 seconds saved
over a ~800 MB pacstrap, less than the ranking spent earning it. That
comparison is line-dependent and it flips on a slow line: measured 2026-08-24
on a link that reached ~1 MB/s to the candidate pool, score's first mirror
served 784 KiB/s and rate's served 1035 KiB/s, so rate saved ~253 s over the
same 800 MB for the 130 s it spent. So the installer sorts by score, and only
the post-install task, which the user runs deliberately and which may precede
gigabytes of packages, pays for rate.
"""

from __future__ import annotations

import math

# Rate this many candidates when timing them...
CANDIDATES = "10"
# ...and keep this many in the final list.
KEEP = "10"
# Skip mirrors that have not synced recently: they serve stale packages, and
# pacman then fails on signatures or missing targets for no obvious reason.
MAX_AGE_HOURS = "12"
# An unreachable mirror should cost seconds, not the default wait.
CONNECT_TIMEOUT = "3"

# reflector times a mirror by downloading the whole database and requiring the
# read to finish inside --download-timeout (Reflector.py, rate_http). So the
# timeout is not patience, it is a floor: a mirror slower than
# DB_BYTES / timeout is not rated slow, it is rated 0.00 KiB/s -- the same
# value rate_http returns for a mirror that refused the connection. When the
# floor sits above what the line can actually do, every candidate reads 0, the
# sort is stable so it leaves them in sync-recency order, and the saved list is
# simply whatever --latest picked: a ranking that costs a minute and ranks
# nothing.
#
# Measured 2026-08-24 (worldwide pool, ~1 MB/s to it): with the floor at
# 1730 KiB/s all ten candidates read 0.00, and the one that led the saved list
# actually served 88 KiB/s. Dropping the floor to 577 KiB/s rated five of the
# ten between 784 and 1035 KiB/s and put the fastest first. Cost 57 s -> 130 s.
#
# Size of extra/os/x86_64/extra.db, the file reflector times (its DB_SUBPATH).
# It grows with the repo, so the floor drifts up as it does; re-measure with
#   curl -sI https://<mirror>/extra/os/x86_64/extra.db | grep -i content-length
DB_BYTES = 8_859_805
# Low enough that a usable mirror still gets measured, high enough that the
# rating terminates. Note what it does NOT do: it decides what gets *measured*,
# it rejects nothing. KEEP equals CANDIDATES, so a mirror below the floor is
# still written to the list -- rated 0.00 and sorted last, as a fallback for
# when the fast ones fail. Three of the ten saved on 2026-08-24 were there
# (255, 193, 157 KiB/s).
MIN_RATE_KIB = 600
# Round up, so a mirror exactly at the floor still gets timed rather than
# rejected. Worst case for the whole rating is CANDIDATES * this.
DOWNLOAD_TIMEOUT = str(math.ceil(DB_BYTES / (MIN_RATE_KIB * 1024)))


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
