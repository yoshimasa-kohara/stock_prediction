"""J-Quants API のレート制限と、やり直しのデコレータ。

- rate_limited : リクエストの間隔を API_REQUEST_INTERVAL_SEC 以上あける(無料プランは 16、ライトプランは 0.5)。
                 加えて、直近1分間に送ったリクエストの時刻を覚えておき、上限に達していたら空くまで待つ。
                 上限はアカウント単位で数えられるので、全ての呼び出しで1つのリミッターを共有する。
- fins_rate_limited: 財務情報だけの上限(API_RATE_LIMIT_FINS_PER_MIN)を守る。rate_limited の外側に付けて、
                 両方の上限を守る(内側だと、財務情報の上限を待つ間も全体の数に入ってしまう)。
- retry_on_error: 429 は API_RETRY_WAIT_SEC 待って最大 API_MAX_RETRY 回、
                 通信エラー・タイムアウト・5xx は API_ERROR_WAIT_SEC 待って最大 API_ERROR_RETRY 回やり直す。
やり直したリクエストも上限の数に入れるため、retry_on_error を外側、rate_limited と fins_rate_limited を内側に付ける。
"""

import functools
import logging
import time
from collections import deque
from collections.abc import Callable

import const
from common.errors import RateLimitError, TransientApiError

# 判定に使う時間の幅(秒)。J-Quants は1分間のスライディングウィンドウで判定する
_WINDOW_SEC: float = 60.0
# 時刻のずれや、通信にかかる時間を見込んだ余裕(秒)。
# 2026-10-18 の初回の一括取得で、ぎりぎりの間隔だと 429 が返ったため、余裕を持たせた。
# 2秒の余裕でも 429 が返ったため、5秒に増やした(2026-10-18)
_MARGIN_SEC: float = 5.0

# 待つときに使う関数(テストで差し替える)
sleep: Callable[[float], None] = time.sleep
clock: Callable[[], float] = time.monotonic

_log = logging.getLogger("stock_prediction")


class RateLimiter:
    """1分あたりのリクエスト数を、上限以下に抑える。"""

    def __init__(self, max_per_min: int, interval_sec: float) -> None:
        """
        Args:
            max_per_min: 1分あたりのリクエスト上限
            interval_sec: 次のリクエストまであける最低の間隔(秒)。0 なら間隔をあけない
        """
        if max_per_min <= 0:
            raise ValueError(f"1分あたりのリクエスト上限には1以上を指定してください({max_per_min})")
        if interval_sec < 0:
            raise ValueError(f"リクエストの間隔には0以上を指定してください({interval_sec})")
        self.max_per_min: int = max_per_min
        self._sent: deque[float] = deque()
        # 次のリクエストまであける間隔(秒)。1分間に上限以内でも、短い間隔で続けて送ると 429 が返ったため、
        # 無料プランでは均等にあけた(2026-10-18)
        self.interval_sec: float = interval_sec

    def acquire(self) -> None:
        """送ってよくなるまで待ち、送った時刻を記録する。"""
        while True:
            now = clock()
            # 前のリクエストから、決めた間隔がたっていなければ待つ
            if self._sent and now - self._sent[-1] < self.interval_sec:
                sleep(self.interval_sec - (now - self._sent[-1]))
                continue
            # 1分より前の記録を捨てる
            while self._sent and now - self._sent[0] >= _WINDOW_SEC + _MARGIN_SEC:
                self._sent.popleft()
            if len(self._sent) < self.max_per_min:
                self._sent.append(now)
                return
            # 最も古い記録が1分を過ぎるまで待つ(余裕を持たせる)
            wait = _WINDOW_SEC - (now - self._sent[0]) + _MARGIN_SEC
            sleep(max(wait, 0.0))

    def complete(self) -> None:
        """リクエストが終わったときに呼び、最後の記録を終わった時刻に置き換える。

        J-Quants がリクエストを受け付けた時刻は、送った時刻より後になる。
        終わった時刻で数えると、J-Quants の数え方より必ず遅くなるので、上限を超えにくい。
        """
        if self._sent:
            self._sent[-1] = clock()


_limiter: RateLimiter | None = None
_fins_limiter: RateLimiter | None = None


def get_limiter() -> RateLimiter:
    """全ての呼び出しで共有するリミッターを返す。"""
    global _limiter
    if (
        _limiter is None
        or _limiter.max_per_min != const.API_RATE_LIMIT_PER_MIN
        or _limiter.interval_sec != const.API_REQUEST_INTERVAL_SEC
    ):
        _limiter = RateLimiter(const.API_RATE_LIMIT_PER_MIN, const.API_REQUEST_INTERVAL_SEC)
    return _limiter


def get_fins_limiter() -> RateLimiter:
    """財務情報の呼び出しで共有するリミッターを返す。間隔は全体のリミッターであけるので、ここではあけない。"""
    global _fins_limiter
    if _fins_limiter is None or _fins_limiter.max_per_min != const.API_RATE_LIMIT_FINS_PER_MIN:
        _fins_limiter = RateLimiter(const.API_RATE_LIMIT_FINS_PER_MIN, 0)
    return _fins_limiter


def reset_limiter() -> None:
    """リミッターを作り直す(テストで使う)。"""
    global _limiter, _fins_limiter
    _limiter = None
    _fins_limiter = None


def rate_limited[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    """1回のリクエストを送る関数に付ける。送る前に、上限を超えないように待つ。"""

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        limiter = get_limiter()
        limiter.acquire()
        try:
            return func(*args, **kwargs)
        finally:
            limiter.complete()

    return wrapper


def fins_rate_limited[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    """財務情報の1回のリクエストを送る関数に付ける。送る前に、財務情報の上限を超えないように待つ。"""

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        limiter = get_fins_limiter()
        limiter.acquire()
        try:
            return func(*args, **kwargs)
        finally:
            limiter.complete()

    return wrapper


def retry_on_error[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    """1回のリクエストを送る関数に付ける。429、通信エラー、5xx のときに待ってやり直す。

    回数を超えた場合は、最後のエラーをそのまま投げる(呼び出し元で失敗になる)。
    401・403 などのエラー(ApiError)は、やり直さずにそのまま投げる。
    """

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        rate_limit_count = 0
        error_count = 0
        while True:
            try:
                return func(*args, **kwargs)
            except RateLimitError:
                rate_limit_count += 1
                if rate_limit_count > const.API_MAX_RETRY:
                    raise
                _log.warning(
                    "J-Quants API のリクエスト数の上限を超えました。%d秒待ってやり直します(%d回目)",
                    const.API_RETRY_WAIT_SEC,
                    rate_limit_count,
                    extra={"process_label": "API", "target_label": "-", "code_label": ""},
                )
                sleep(const.API_RETRY_WAIT_SEC)
            except TransientApiError as e:
                error_count += 1
                if error_count > const.API_ERROR_RETRY:
                    raise
                _log.warning(
                    "J-Quants API の呼び出しに失敗しました(%s)。%d秒待ってやり直します(%d回目)",
                    e,
                    const.API_ERROR_WAIT_SEC,
                    error_count,
                    extra={"process_label": "API", "target_label": "-", "code_label": ""},
                )
                sleep(const.API_ERROR_WAIT_SEC)

    return wrapper
