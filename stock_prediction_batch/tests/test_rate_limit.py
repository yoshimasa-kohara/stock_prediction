"""common/rate_limit.py のテスト。"""

import pytest

import const
from common import rate_limit
from common.errors import ApiError, RateLimitError, TransientApiError


class FakeClock:
    """時刻を進められる時計。sleep で時刻を進める。"""

    def __init__(self) -> None:
        self.now: float = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fc = FakeClock()
    monkeypatch.setattr(rate_limit, "clock", fc.clock)
    monkeypatch.setattr(rate_limit, "sleep", fc.sleep)
    return fc


def test_間隔をあけて送る(fake_clock: FakeClock) -> None:
    limiter = rate_limit.RateLimiter(5, 13.0)
    limiter.acquire()
    assert fake_clock.slept == []  # 1回目は待たない
    for _ in range(4):
        limiter.acquire()
    # 2回目以降は、前のリクエストから指定した間隔(13秒)だけあける
    assert fake_clock.slept == [pytest.approx(13.0)] * 4


def test_間隔が0なら上限の数までは待たずに送る(fake_clock: FakeClock) -> None:
    limiter = rate_limit.RateLimiter(119, 0)
    for _ in range(119):
        limiter.acquire()
    assert fake_clock.slept == []
    # 120回目は、1回目から 60秒 + 余裕 がたつまで待つ
    limiter.acquire()
    assert fake_clock.slept == [pytest.approx(60.0 + rate_limit._MARGIN_SEC)]


def test_間隔がたっていれば待たない(fake_clock: FakeClock) -> None:
    limiter = rate_limit.RateLimiter(5, 13.0)
    limiter.acquire()
    fake_clock.now += 20
    limiter.acquire()
    assert fake_clock.slept == []


def test_上限に達したら最も古い記録が1分を過ぎるまで待つ(fake_clock: FakeClock) -> None:
    limiter = rate_limit.RateLimiter(2, 31.0)
    limiter.acquire()
    fake_clock.now += 31
    limiter.acquire()
    fake_clock.now += 31
    limiter.acquire()  # 3回目は間隔はたっているが、1回目から 60秒 + 余裕 がたつまで待つ
    assert len(fake_clock.slept) == 1
    assert fake_clock.slept[0] == pytest.approx(60.0 + rate_limit._MARGIN_SEC - 62.0)
    # 直近1分間に送った数は上限以下になっている
    assert fake_clock.now - 1000.0 >= 60


def test_上限は1以上() -> None:
    with pytest.raises(ValueError):
        rate_limit.RateLimiter(0, 0)


def test_間隔は0以上() -> None:
    with pytest.raises(ValueError):
        rate_limit.RateLimiter(1, -1)


def test_リミッターは共有され上限が変わったら作り直す(monkeypatch: pytest.MonkeyPatch) -> None:
    first = rate_limit.get_limiter()
    assert rate_limit.get_limiter() is first
    assert first.max_per_min == const.API_RATE_LIMIT_PER_MIN
    monkeypatch.setattr(const, "API_RATE_LIMIT_PER_MIN", 60)
    second = rate_limit.get_limiter()
    assert second is not first
    assert second.max_per_min == 60
    # 間隔が変わった場合も作り直す
    monkeypatch.setattr(const, "API_REQUEST_INTERVAL_SEC", 16)
    third = rate_limit.get_limiter()
    assert third is not second
    assert third.interval_sec == 16


def test_財務情報のリミッターは全体とは別で上限が変わったら作り直す(monkeypatch: pytest.MonkeyPatch) -> None:
    first = rate_limit.get_fins_limiter()
    assert first is not rate_limit.get_limiter()
    assert rate_limit.get_fins_limiter() is first
    assert first.max_per_min == const.API_RATE_LIMIT_FINS_PER_MIN
    assert first.interval_sec == 0
    monkeypatch.setattr(const, "API_RATE_LIMIT_FINS_PER_MIN", 10)
    second = rate_limit.get_fins_limiter()
    assert second is not first
    assert second.max_per_min == 10


def test_財務情報の上限は公表値の60より少ない() -> None:
    assert const.API_RATE_LIMIT_FINS_PER_MIN < 60


def test_fins_rate_limitedは呼ぶたびに財務情報のリミッターを通す(
    fake_clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(const, "API_RATE_LIMIT_FINS_PER_MIN", 1)

    @rate_limit.fins_rate_limited
    def call(x: int) -> int:
        return x * 2

    assert call(1) == 2
    assert call(2) == 4
    # 2回目は、1回目から 60秒 + 余裕 がたつまで待つ
    assert sum(fake_clock.slept) == pytest.approx(60.0 + rate_limit._MARGIN_SEC)


def test_rate_limitedは呼ぶたびにリミッターを通す(fake_clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(const, "API_RATE_LIMIT_PER_MIN", 1)

    @rate_limit.rate_limited
    def call(x: int) -> int:
        return x * 2

    assert call(1) == 2
    assert call(2) == 4
    # 2回目は、1回目から 60秒 + 余裕 がたつまで待つ
    assert sum(fake_clock.slept) == pytest.approx(60.0 + rate_limit._MARGIN_SEC)


def test_終わった時刻で数える(fake_clock: FakeClock) -> None:
    limiter = rate_limit.RateLimiter(1, 0)
    limiter.acquire()
    fake_clock.now += 30  # リクエストに30秒かかった
    limiter.complete()
    limiter.acquire()
    # 終わった時刻(1030)から 60 秒 + 余裕の秒数だけ待つ
    assert fake_clock.now == pytest.approx(1090.0 + rate_limit._MARGIN_SEC)


def test_記録がなければcompleteは何もしない() -> None:
    rate_limit.RateLimiter(1, 0).complete()


def test_429は待ってやり直し上限を超えたら失敗(fake_clock: FakeClock) -> None:
    calls: list[int] = []

    @rate_limit.retry_on_error
    def call() -> str:
        calls.append(1)
        raise RateLimitError("429", 429)

    with pytest.raises(RateLimitError):
        call()
    assert len(calls) == const.API_MAX_RETRY + 1
    assert fake_clock.slept == [const.API_RETRY_WAIT_SEC] * const.API_MAX_RETRY


def test_通信エラーは待ってやり直し成功すれば値を返す(fake_clock: FakeClock) -> None:
    calls: list[int] = []

    @rate_limit.retry_on_error
    def call() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise TransientApiError("timeout")
        return "ok"

    assert call() == "ok"
    assert fake_clock.slept == [const.API_ERROR_WAIT_SEC] * 2


def test_通信エラーが続いたら失敗(fake_clock: FakeClock) -> None:
    calls: list[int] = []

    @rate_limit.retry_on_error
    def call() -> str:
        calls.append(1)
        raise TransientApiError("503", 503)

    with pytest.raises(TransientApiError):
        call()
    assert len(calls) == const.API_ERROR_RETRY + 1


def test_429以外の4xxはやり直さない(fake_clock: FakeClock) -> None:
    calls: list[int] = []

    @rate_limit.retry_on_error
    def call() -> str:
        calls.append(1)
        raise ApiError("403", 403)

    with pytest.raises(ApiError):
        call()
    assert len(calls) == 1
    assert fake_clock.slept == []
