"""Shared reflector arguments and the country fallback.

The numbers quoted here were measured on a 100 Mbit line; they are the
reason the installer and the post-install task sort differently.
"""

from archsetup.core import mirrors


def _pairs(args):
    return dict(zip(args[::2], args[1::2]))


def test_installer_sorting_downloads_nothing():
    """`--sort rate` times every candidate: 43-72 s against 0 s for score,
    to gain ~32 s over an 800 MB pacstrap. It does not pay for itself."""
    args = mirrors.reflector_args()
    assert args[args.index("--sort") + 1] == "score"
    assert "--latest" not in args


def test_thorough_sorting_measures_the_connection():
    args = mirrors.reflector_args(thorough=True)
    assert args[args.index("--sort") + 1] == "rate"
    pairs = _pairs(args)
    assert int(pairs["--connection-timeout"]) <= 5


def test_download_timeout_is_a_floor_not_a_deadline():
    """reflector downloads the whole db and needs it to finish inside the
    timeout, so anything slower than DB_BYTES/timeout rates 0.00 -- the same
    reading a dead mirror gets. A 5 s timeout put that floor at 1730 KiB/s and
    silently zeroed all ten candidates, leaving an 88 KiB/s mirror first."""
    pairs = _pairs(mirrors.reflector_args(thorough=True))
    floor_kib = mirrors.DB_BYTES / int(pairs["--download-timeout"]) / 1024
    assert floor_kib <= mirrors.MIN_RATE_KIB


def test_rating_cost_stays_bounded():
    """Every candidate can burn the full timeout, so the floor cannot be
    lowered by simply waiting longer."""
    pairs = _pairs(mirrors.reflector_args(thorough=True))
    worst_case_s = int(pairs["--latest"]) * int(pairs["--download-timeout"])
    assert worst_case_s <= 180


def test_candidate_pool_is_not_widened():
    """Going from 10 to 20 candidates cost 29 extra seconds and picked no
    better mirror, because --latest selects by sync recency, not speed."""
    assert int(_pairs(mirrors.reflector_args(thorough=True))["--latest"]) <= 10


def test_stale_mirrors_are_excluded():
    assert "--age" in mirrors.reflector_args()
    assert "--age" in mirrors.reflector_args(thorough=True)


def test_country_is_passed_through_when_given():
    assert _pairs(mirrors.reflector_args("TR"))["--country"] == "TR"


def test_no_country_flag_when_none_asked_for():
    assert "--country" not in mirrors.reflector_args()
    assert "--country" not in mirrors.reflector_args("   ")


# --- country fallback ------------------------------------------------------


def _recorder(fail_when=None):
    calls = []

    def runner(cmd):
        calls.append(cmd)
        return 1 if (fail_when and fail_when in cmd) else 0

    runner.calls = calls
    return runner


def test_country_is_tried_first():
    runner = _recorder()
    assert mirrors.rank(runner, "/tmp/ml", "TR") == 0
    assert len(runner.calls) == 1
    assert "--country" in runner.calls[0]


def test_unmatched_country_falls_back_to_worldwide():
    """reflector matches country names exactly -- "Türkiye" resolves and
    "Turkey" does not -- and some countries host no mirrors at all. Either
    way it exits with "no mirrors found" and writes nothing, which is worse
    than ignoring the preference."""
    runner = _recorder(fail_when="Turkey")
    assert mirrors.rank(runner, "/tmp/ml", "Turkey") == 0
    assert len(runner.calls) == 2
    assert "--country" not in runner.calls[1]


def test_no_pointless_second_attempt_without_a_country():
    runner = _recorder()
    assert mirrors.rank(runner, "/tmp/ml") == 0
    assert len(runner.calls) == 1


def test_sudo_only_when_asked():
    runner = _recorder()
    mirrors.rank(runner, "/tmp/ml", sudo=True)
    assert runner.calls[0][0] == "sudo"
    runner = _recorder()
    mirrors.rank(runner, "/tmp/ml")
    assert runner.calls[0][0] == "reflector"


# --- both phases go through this module ------------------------------------
#
# The module exists because the two phases once built their arguments
# separately and disagreed (see its docstring). Nothing in the tree fails if
# that happens again: sharing is a convention, and a later edit is free to
# drop it -- reaching for `reflector` directly in one phase looks like a
# one-line change and leaves the other phase behind silently. These two pin
# the call sites, and with them the one difference that was decided by
# measurement: score in the installer, rate afterwards.


def _capture_rank(monkeypatch):
    seen = []

    def fake_rank(runner, save_path, country="", thorough=False, sudo=False):
        seen.append(
            {"path": save_path, "country": country,
             "thorough": thorough, "sudo": sudo}
        )
        return 0

    monkeypatch.setattr(mirrors, "rank", fake_rank)
    return seen


def _ran_reflector_itself(calls):
    """Any command that is reflector, rather than one that installs it."""
    return [c for c in calls if c[:1] == ["reflector"] or c[:2] == ["sudo", "reflector"]]


def test_installer_ranks_through_this_module(monkeypatch, runlog):
    from archsetup.installer import base

    seen = _capture_rank(monkeypatch)
    monkeypatch.setattr(base, "run", runlog)
    monkeypatch.setattr(base, "input", lambda prompt="": "Turkey", raising=False)

    assert base.run_reflector() == 0
    assert seen == [
        {"path": "/etc/pacman.d/mirrorlist", "country": "Turkey",
         "thorough": False, "sudo": False}
    ]
    # Live ISO runs as root, and the ranking must not be built here.
    assert _ran_reflector_itself(runlog.calls) == []


def test_post_install_ranks_through_this_module(monkeypatch, runlog, tmp_path):
    from archsetup.core import tasks

    mirrorlist = tmp_path / "mirrorlist"
    mirrorlist.write_text("Server = https://example.invalid/$repo/os/$arch\n")
    seen = _capture_rank(monkeypatch)
    monkeypatch.setattr(tasks, "MIRRORLIST", mirrorlist)
    monkeypatch.setattr(tasks, "run", runlog)
    monkeypatch.setattr(tasks.shutil, "which", lambda name: "/usr/bin/reflector")
    monkeypatch.setattr(tasks, "input", lambda prompt="": "Turkey", raising=False)

    assert tasks.reflector_mirrors() == 0
    assert seen == [
        {"path": str(mirrorlist), "country": "Turkey",
         "thorough": True, "sudo": True}
    ]
    assert _ran_reflector_itself(runlog.calls) == []
