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
    assert int(pairs["--download-timeout"]) <= 10


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
