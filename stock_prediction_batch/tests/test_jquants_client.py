"""common/jquants_client.py のテスト。J-Quants API は本物を呼ばない。"""

from datetime import date
from typing import Any

import pytest
import requests

import const
from common import jquants_client as jq
from common import rate_limit
from common.errors import ApiError, PlanRangeError, RateLimitError, TransientApiError


class FakeResponse:
    def __init__(self, status: int, body: Any = None, text: str = "") -> None:
        self.status_code = status
        self._body = body
        self.text = text

    def json(self) -> Any:
        return self._body


class FakeSession:
    """決めておいた応答を順に返す。送ったリクエストを記録する。"""

    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def get(self, url: str, headers: dict[str, str], params: dict[str, str], timeout: int) -> FakeResponse:
        self.requests.append({"url": url, "headers": headers, "params": dict(params), "timeout": timeout})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _use(monkeypatch: pytest.MonkeyPatch, responses: list[FakeResponse | Exception]) -> FakeSession:
    session = FakeSession(responses)
    monkeypatch.setattr(jq, "session", session)
    return session


def test_ヘッダーにAPIキーを入れて呼び出す(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _use(monkeypatch, [FakeResponse(200, {"data": [{"Code": "11110"}]})])
    rows = jq.fetch_daily_bars(date(2026, 7, 8))
    assert rows == [{"Code": "11110"}]
    req = session.requests[0]
    assert req["url"] == "https://api.jquants.com/v2/equities/bars/daily"
    assert req["headers"] == {"x-api-key": "test-api-key"}
    assert req["params"] == {"date": "2026-07-08"}
    assert req["timeout"] == const.API_TIMEOUT_SEC


def test_続きのページを全て取得してつなぐ(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _use(
        monkeypatch,
        [
            FakeResponse(200, {"data": [{"n": 1}], "pagination_key": "k1"}),
            FakeResponse(200, {"data": [{"n": 2}], "pagination_key": "k2"}),
            FakeResponse(200, {"data": [{"n": 3}]}),
        ],
    )
    rows = jq.fetch_valuation(date(2026, 7, 8))
    assert rows == [{"n": 1}, {"n": 2}, {"n": 3}]
    assert [r["params"].get("pagination_key") for r in session.requests] == [None, "k1", "k2"]
    assert all(r["params"]["date"] == "2026-07-08" for r in session.requests)
    assert jq.last_page_count == 3


def test_各APIのパスとパラメータ(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _use(monkeypatch, [FakeResponse(200, {"data": []}) for _ in range(5)])
    d = date(2026, 7, 8)
    jq.fetch_master(d)
    jq.fetch_calendar(date(2026, 1, 1), date(2027, 12, 31))
    jq.fetch_fin_summary(d)
    jq.fetch_earnings_date(d)
    jq.fetch_valuation(d)
    assert [r["url"].removeprefix(const.JQUANTS_BASE_URL) for r in session.requests] == [
        "/equities/master",
        "/markets/calendar",
        "/fins/summary",
        "/fins/earnings-date",
        "/equities/valuation",
    ]
    assert session.requests[1]["params"] == {"from": "2026-01-01", "to": "2027-12-31"}


class TimedSession(FakeSession):
    """リクエストを受けた時刻を、パスごとに記録する。"""

    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        super().__init__(responses)
        self.times: list[tuple[str, float]] = []

    def get(self, url: str, headers: dict[str, str], params: dict[str, str], timeout: int) -> FakeResponse:
        self.times.append((url.removeprefix(const.JQUANTS_BASE_URL), rate_limit.clock()))
        return super().get(url, headers, params, timeout)


def test_財務情報と決算発表予定日を交互に呼んでも両方の上限を超えない(monkeypatch: pytest.MonkeyPatch) -> None:
    # 収集の手順9、手順10と同じ呼び方。財務情報は毎回2ページにして、財務情報が多くなるようにする
    days = 100
    responses: list[FakeResponse | Exception] = []
    for _ in range(days):
        responses += [
            FakeResponse(200, {"data": [], "pagination_key": "k"}),
            FakeResponse(200, {"data": []}),
            FakeResponse(200, {"data": []}),
        ]
    session = TimedSession(responses)
    monkeypatch.setattr(jq, "session", session)
    for _ in range(days):
        jq.fetch_fin_summary(date(2026, 7, 8))
        jq.fetch_earnings_date(date(2026, 7, 8))

    all_times = [t for _, t in session.times]
    fin_times = [t for path, t in session.times if path == jq.PATH_FIN_SUMMARY]
    assert len(fin_times) == days * 2
    fin_limit = const.API_RATE_LIMIT_FINS_PER_MIN
    assert all(fin_times[i + fin_limit] - fin_times[i] >= 60 for i in range(len(fin_times) - fin_limit))
    limit = const.API_RATE_LIMIT_PER_MIN
    assert all(all_times[i + limit] - all_times[i] >= 60 for i in range(len(all_times) - limit))


def test_財務情報以外は財務情報の上限で待たない(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(const, "API_RATE_LIMIT_FINS_PER_MIN", 1)
    session = TimedSession([FakeResponse(200, {"data": []}) for _ in range(3)])
    monkeypatch.setattr(jq, "session", session)
    jq.fetch_earnings_date(date(2026, 7, 8))
    jq.fetch_valuation(date(2026, 7, 8))
    jq.fetch_fin_summary(date(2026, 7, 8))
    assert len({t for _, t in session.times}) == 1


def test_財務情報のやり直しも財務情報の上限に数える(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(const, "API_RATE_LIMIT_FINS_PER_MIN", 1)
    monkeypatch.setattr(const, "API_RETRY_WAIT_SEC", 0)
    session = TimedSession([FakeResponse(429), FakeResponse(200, {"data": []})])
    monkeypatch.setattr(jq, "session", session)
    jq.fetch_fin_summary(date(2026, 7, 8))
    assert session.times[1][1] - session.times[0][1] >= 60


def test_0件は空のリスト(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, [FakeResponse(200, {"data": []})])
    assert jq.fetch_daily_bars(date(2026, 7, 8)) == []


def test_210は0件として扱いやり直さない(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _use(monkeypatch, [FakeResponse(210, None)])
    assert jq.fetch_daily_bars(date(2026, 7, 8)) == []
    assert len(session.requests) == 1


def test_429は待ってやり直す(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _use(monkeypatch, [FakeResponse(429), FakeResponse(200, {"data": [{"n": 1}]})])
    assert jq.fetch_daily_bars(date(2026, 7, 8)) == [{"n": 1}]
    assert len(session.requests) == 2


def test_429が続いたら失敗(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, [FakeResponse(429) for _ in range(const.API_MAX_RETRY + 1)])
    with pytest.raises(RateLimitError):
        jq.fetch_daily_bars(date(2026, 7, 8))


def test_5xxと通信エラーは待ってやり直す(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _use(
        monkeypatch,
        [
            FakeResponse(503),
            requests.ConnectionError("接続できません"),
            requests.Timeout("タイムアウト"),
            FakeResponse(200, {"data": []}),
        ],
    )
    assert jq.fetch_daily_bars(date(2026, 7, 8)) == []
    assert len(session.requests) == 4


def test_通信エラーが続いたら失敗(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, [FakeResponse(500) for _ in range(const.API_ERROR_RETRY + 1)])
    with pytest.raises(TransientApiError):
        jq.fetch_daily_bars(date(2026, 7, 8))


def test_403は契約プランの期間外の可能性をメッセージに加える(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _use(monkeypatch, [FakeResponse(403, text='{"message":"forbidden"}')])
    # 期間の収集で、期間外としてとばすかを判定できるように、PlanRangeError にする
    with pytest.raises(PlanRangeError) as e:
        jq.fetch_daily_bars(date(2026, 7, 8))
    assert e.value.status_code == 403
    assert "契約プランの期間外の可能性があります" in str(e.value)
    assert "date=2026-07-08" in str(e.value)
    assert len(session.requests) == 1  # やり直さない


def test_決算発表予定日の400も期間外の可能性をメッセージに加える(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, [FakeResponse(400)])
    with pytest.raises(PlanRangeError, match="契約プランの期間外の可能性があります") as e:
        jq.fetch_earnings_date(date(2026, 7, 8))
    assert e.value.status_code == 400


_SUBSCRIPTION_TEXT = (
    '{"message": "Your subscription covers the following dates: 2016-11-02 ~ . '
    'If you want more data, please check other plans:https://jpx-jquants.com/#dataset"}'
)


@pytest.mark.parametrize(
    "call",
    [
        lambda: jq.fetch_daily_bars(date(2016, 10, 3)),
        lambda: jq.fetch_master(date(2016, 10, 3)),
        lambda: jq.fetch_calendar(date(2016, 1, 1), date(2027, 12, 31)),
        lambda: jq.fetch_valuation(date(2016, 10, 3)),
        lambda: jq.fetch_fin_summary(date(2016, 10, 3)),
        lambda: jq.fetch_earnings_date(date(2016, 10, 3)),
    ],
)
def test_契約期間の400は全てのAPIで期間外として判定し最初の日を取り出す(
    monkeypatch: pytest.MonkeyPatch, call: Any
) -> None:
    session = _use(monkeypatch, [FakeResponse(400, text=_SUBSCRIPTION_TEXT)])
    with pytest.raises(PlanRangeError, match="契約プランの期間外の可能性があります") as e:
        call()
    assert e.value.status_code == 400
    assert e.value.subscription_range
    assert e.value.first_date == date(2016, 11, 2)
    assert len(session.requests) == 1  # やり直さない


def test_契約期間の400で日付を読み取れなければ最初の日はNone(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, [FakeResponse(400, text='{"message": "Your subscription covers the following dates: unknown"}')])
    with pytest.raises(PlanRangeError) as e:
        jq.fetch_daily_bars(date(2016, 10, 3))
    assert e.value.subscription_range
    assert e.value.first_date is None


def test_契約期間の文がない400はこれまでどおり(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, [FakeResponse(400, text='{"message": "bad request"}')])
    with pytest.raises(ApiError) as e:
        jq.fetch_calendar(date(2016, 1, 1), date(2027, 12, 31))
    assert not isinstance(e.value, PlanRangeError)


def test_それ以外の4xxは期間外の文を加えない(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, [FakeResponse(400), FakeResponse(401)])
    with pytest.raises(ApiError) as e1:
        jq.fetch_daily_bars(date(2026, 7, 8))
    assert "契約プラン" not in str(e1.value)
    assert not isinstance(e1.value, PlanRangeError)
    with pytest.raises(ApiError) as e2:
        jq.fetch_master(date(2026, 7, 8))
    assert e2.value.status_code == 401


def test_レスポンスの形が違う場合はエラー(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, [FakeResponse(200, ["list"]), FakeResponse(200, {"data": "x"})])
    with pytest.raises(ApiError, match="レスポンスの形"):
        jq.fetch_daily_bars(date(2026, 7, 8))
    with pytest.raises(ApiError, match="リストではありません"):
        jq.fetch_daily_bars(date(2026, 7, 8))


def test_APIキーがない場合はエラー(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, [FakeResponse(200, {"data": []})])
    monkeypatch.setenv("JQUANTS_API_KEY", "")
    with pytest.raises(RuntimeError, match="JQUANTS_API_KEY"):
        jq.fetch_daily_bars(date(2026, 7, 8))
