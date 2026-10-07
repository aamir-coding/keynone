"""Smart warm-up: how long the engine stays loaded after a dictation."""

from __future__ import annotations

from keynone.listener.warm import WarmPolicy

MIN = 60.0


def _policy(*finished: float, keep: float = 300, smart: bool = True) -> WarmPolicy:
    policy = WarmPolicy(keep, smart)
    for t in finished:
        policy.dictated(t)
    return policy


def test_one_dictation_keeps_the_usual_warm_period() -> None:
    policy = _policy(1000)
    assert policy.warm_for(1000) == 300
    assert not policy.should_unload(1000 + 299)
    assert policy.should_unload(1000 + 300)


def test_dictating_often_keeps_it_loaded_longer() -> None:
    two = _policy(0, 20 * MIN)  # 2 within half an hour
    assert two.warm_for(20 * MIN) == 15 * MIN
    assert not two.should_unload(20 * MIN + 10 * MIN)
    assert two.should_unload(20 * MIN + 15 * MIN)
    three = _policy(0, 25 * MIN, 50 * MIN)  # 3 within an hour
    assert three.warm_for(50 * MIN) == 30 * MIN
    assert not three.should_unload(50 * MIN + 29 * MIN)
    assert three.should_unload(50 * MIN + 30 * MIN)
    sparse = _policy(0, 45 * MIN)  # 45 minutes apart: nothing extra
    assert sparse.warm_for(45 * MIN) == 300


def test_stepping_away_or_battery_saver_ends_the_extra_time() -> None:
    three = _policy(0, 10 * MIN, 20 * MIN)
    now = 20 * MIN + 11 * MIN
    assert not three.should_unload(now, idle_seconds=60)
    assert three.should_unload(now, idle_seconds=10 * MIN)  # away 10 minutes
    assert three.warm_for(20 * MIN, battery_saver=True) == 300
    assert three.should_unload(20 * MIN + 300, battery_saver=True)
    # ... but never sooner than keep_warm_seconds
    assert not three.should_unload(20 * MIN + 200, idle_seconds=20 * MIN)


def test_settings() -> None:
    assert _policy(0, 1, 2, smart=False).warm_for(2) == 300  # smart warm-up off
    assert _policy(0, 1, 2, keep=0).warm_for(2) == 0  # strict on-demand stays strict
    assert _policy(0, 1, 2, keep=3600).warm_for(2) == 3600  # a longer own setting wins
    assert not WarmPolicy(300).should_unload(10_000)  # no dictation finished yet
