"""core/index_collect_core.py(日経平均の収集)のテスト。yfinance は本物を呼ばない(Yahoo Finance に接続しない)。"""

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pandas as pd
import psycopg
import pytest
from psycopg.rows import DictRow

import collect
import const
from common.logger import get_logger, log_file_path
from core import collect_core as cc
from core import index_collect_core as ic
from tests.conftest import fake_index_history
from tests.test_collect_core import D_0708, FakeJQuants, api  # noqa: F401  (fixture を使う)

N225 = const.INDEX_CODE_N225
# conftest で偽物に差し替える前の、本物の関数(読み込んだ時点ではまだ差し替えていない)
_REAL_FETCH = ic.fetch_index_history


class FakeYahoo:
    """yfinance の代わり。呼ばれた範囲を記録する。errors の回数だけ例外を投げてから、frame(なければ平日の行)を返す。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, date, date]] = []
        self.errors: int = 0
        self.frame: Callable[[date, date], pd.DataFrame] | None = None

    def __call__(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        self.calls.append((ticker, start, end))
        if self.errors > 0:
            self.errors -= 1
            raise ConnectionError("Yahoo Finance に接続できません")
        if self.frame is not None:
            return self.frame(start, end)
        return fake_index_history(ticker, start, end)


@pytest.fixture
def yahoo(monkeypatch: pytest.MonkeyPatch) -> FakeYahoo:
    fake = FakeYahoo()
    monkeypatch.setattr(ic, "fetch_index_history", fake)
    return fake


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """待った秒数を記録する(実際には待たない)。"""
    recorded: list[float] = []
    monkeypatch.setattr(ic, "sleep", recorded.append)
    return recorded


def _price(conn: psycopg.Connection[DictRow], *days: date) -> None:
    for d in days:
        conn.execute("INSERT INTO daily_price (trade_date, code, close, adj_factor) VALUES (%s, '11110', 100, 1)", (d,))


def _index_rows(conn: psycopg.Connection[DictRow]) -> dict[date, Decimal]:
    rows = conn.execute("SELECT trade_date, close FROM index_daily_price WHERE index_code = %s", (N225,)).fetchall()
    return {r["trade_date"]: r["close"] for r in rows}


def _collect(conn: psycopg.Connection[DictRow], target: date) -> str | None:
    return ic.collect_index(conn, target, get_logger("収集", target))


def _frame(rows: dict[Any, float | None]) -> pd.DataFrame:
    """日時 → 終値 の表(yfinance の history と同じ列名)。"""
    index = pd.DatetimeIndex(list(rows.keys()))
    closes = [float("nan") if v is None else v for v in rows.values()]
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes}, index=index)


# =====================================================================
# 取る範囲
# =====================================================================


def test_最初の1回は株価日足の一番古い日から指定日まで取る(conn: psycopg.Connection[DictRow], yahoo: FakeYahoo) -> None:
    _price(conn, date(2026, 6, 1), D_0708)
    assert _collect(conn, D_0708) is None
    # yfinance の end は含まれないので、指定日の翌日を渡す
    assert yahoo.calls == [(N225, date(2026, 6, 1), date(2026, 7, 9))]
    rows = _index_rows(conn)
    assert min(rows) == date(2026, 6, 1) and max(rows) == D_0708
    assert rows[D_0708] == Decimal("30000.0000")


def test_2回目以降は指定日の31日前から取る(conn: psycopg.Connection[DictRow], yahoo: FakeYahoo) -> None:
    _price(conn, date(2026, 1, 5), D_0708)
    conn.execute("INSERT INTO index_daily_price (index_code, trade_date, close) VALUES (%s, '2026-01-05', 1)", (N225,))
    assert _collect(conn, D_0708) is None
    assert yahoo.calls == [(N225, D_0708 - timedelta(days=const.COLLECT_BACKFILL_DAYS), date(2026, 7, 9))]
    assert const.COLLECT_BACKFILL_DAYS == 31


def test_株価日足が空なら取らない(conn: psycopg.Connection[DictRow], yahoo: FakeYahoo) -> None:
    assert _collect(conn, D_0708) is None
    assert yahoo.calls == []
    assert _index_rows(conn) == {}


def test_指定日より後の行と終値が空の行は登録しない(conn: psycopg.Connection[DictRow], yahoo: FakeYahoo) -> None:
    _price(conn, D_0708)
    yahoo.frame = lambda start, end: _frame(
        {
            pd.Timestamp("2026-07-07", tz="Asia/Tokyo"): None,
            pd.Timestamp("2026-07-08", tz="Asia/Tokyo"): 40000.5,
            pd.Timestamp("2026-07-09", tz="Asia/Tokyo"): 41000.0,
        }
    )
    assert _collect(conn, D_0708) is None
    assert _index_rows(conn) == {D_0708: Decimal("40000.5000")}


def test_日付は東京の日付にする(conn: psycopg.Connection[DictRow], yahoo: FakeYahoo) -> None:
    _price(conn, D_0708)
    # UTC の 7/7 15:00 は、東京の 7/8 0:00
    yahoo.frame = lambda start, end: _frame({pd.Timestamp("2026-07-07 15:00", tz="UTC"): 40000.0})
    assert _collect(conn, D_0708) is None
    assert list(_index_rows(conn)) == [D_0708]
    # 時刻帯がない場合は、そのままの日付
    assert ic.to_rows(_frame({pd.Timestamp("2026-07-08"): 1.0}), N225, D_0708)[0][1] == D_0708


def test_同じ日は上書きする(conn: psycopg.Connection[DictRow], yahoo: FakeYahoo) -> None:
    _price(conn, D_0708)
    conn.execute(
        "INSERT INTO index_daily_price (index_code, trade_date, close, updated_at)"
        " VALUES (%s, %s, 1, now() - interval '1 day')",
        (N225, D_0708),
    )
    assert _collect(conn, D_0708) is None
    row = conn.execute(
        "SELECT close, open, updated_at > created_at AS updated FROM index_daily_price WHERE trade_date = %s",
        (D_0708,),
    ).fetchone()
    assert row is not None
    assert (row["close"], row["open"]) == (Decimal("30000.0000"), Decimal("30000.0000"))
    assert row["updated"] is True


# =====================================================================
# 取れなかったとき
# =====================================================================


def test_失敗はやり直して取れれば警告にしない(
    conn: psycopg.Connection[DictRow], yahoo: FakeYahoo, waits: list[float]
) -> None:
    _price(conn, D_0708)
    yahoo.errors = 2
    assert _collect(conn, D_0708) is None
    assert len(yahoo.calls) == 3
    assert waits == [const.INDEX_FETCH_RETRY_WAIT_SECONDS] * 2
    assert D_0708 in _index_rows(conn)


def test_やり直しても取れなければ警告の文言を返す(
    conn: psycopg.Connection[DictRow], yahoo: FakeYahoo, waits: list[float]
) -> None:
    _price(conn, D_0708)
    yahoo.errors = 100
    assert _collect(conn, D_0708) == ic.INDEX_WARNING_MESSAGE
    assert len(yahoo.calls) == const.INDEX_FETCH_RETRY_COUNT + 1
    assert len(waits) == const.INDEX_FETCH_RETRY_COUNT
    assert _index_rows(conn) == {}
    assert "WARNING" in log_file_path().read_text(encoding="utf-8")


def test_指定日の行がなければ警告の文言を返す(conn: psycopg.Connection[DictRow], yahoo: FakeYahoo) -> None:
    _price(conn, D_0708)
    yahoo.frame = lambda start, end: _frame({pd.Timestamp("2026-07-07", tz="Asia/Tokyo"): 40000.0})
    assert _collect(conn, D_0708) == ic.INDEX_WARNING_MESSAGE
    # 取れた日の分は登録する
    assert list(_index_rows(conn)) == [date(2026, 7, 7)]
    assert "日経平均に、指定日(2026-07-08)の行がありませんでした" in log_file_path().read_text(encoding="utf-8")
    # 空の表でも同じ
    yahoo.frame = lambda start, end: pd.DataFrame()
    assert _collect(conn, D_0708) == ic.INDEX_WARNING_MESSAGE


def test_fetch_index_historyはyfinanceを日足で呼ぶ(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    called: dict[str, Any] = {}

    class Ticker:
        def __init__(self, ticker: str) -> None:
            called["ticker"] = ticker

        def history(self, **kwargs: Any) -> pd.DataFrame:
            called.update(kwargs)
            return pd.DataFrame()

    config = types.SimpleNamespace(debug=types.SimpleNamespace(hide_exceptions=True))
    module = types.SimpleNamespace(Ticker=Ticker, config=config)
    monkeypatch.setitem(sys.modules, "yfinance", module)
    result = _REAL_FETCH(N225, date(2026, 7, 1), date(2026, 7, 9))
    assert result.empty
    assert called == {
        "ticker": "^N225",
        "start": "2026-07-01",
        "end": "2026-07-09",
        "interval": "1d",
        "auto_adjust": False,
        "actions": False,
    }
    # 失敗を空の表にせず、例外にする
    assert config.debug.hide_exceptions is False


# =====================================================================
# 収集の中での動き
# =====================================================================


def _seed_backfill(conn: psycopg.Connection[DictRow]) -> None:
    """補完の範囲を取得済みにしておく。"""
    for d in cc.decide_target_dates(conn, D_0708, None, None)[:-1]:
        conn.execute(
            "INSERT INTO fetched_date (target_date, data_type, status, record_count)"
            " VALUES (%s, 'PRICE', 'FETCHED', 1)",
            (d,),
        )


def test_収集の最後に日経平均を取る(conn: psycopg.Connection[DictRow], api: FakeJQuants, yahoo: FakeYahoo) -> None:  # noqa: F811
    _seed_backfill(conn)
    api.add_day(D_0708, ["11110"])
    result = cc.collect(conn, D_0708, None, None, get_logger("収集", D_0708))
    assert result.index_warning is None
    assert len(yahoo.calls) == 1
    assert D_0708 in _index_rows(conn)


def test_空振りの日は日経平均を取らない(
    conn: psycopg.Connection[DictRow],
    api: FakeJQuants,  # noqa: F811
    yahoo: FakeYahoo,
) -> None:
    _seed_backfill(conn)
    _price(conn, date(2026, 7, 7))
    result = cc.collect(conn, D_0708, None, None, get_logger("収集", D_0708))
    assert result.target_empty is True
    assert yahoo.calls == []
    assert result.index_warning is None


def test_日経平均を取れなくても収集は成功のまま警告を返す(
    conn: psycopg.Connection[DictRow],
    api: FakeJQuants,  # noqa: F811
    yahoo: FakeYahoo,
    waits: list[float],
) -> None:
    api.add_day(D_0708, ["11110"])
    yahoo.errors = 100
    result = cc.run_collect(D_0708, D_0708, D_0708, const.BATCH_TYPE_DAILY)
    assert result.fetched_dates == [D_0708]
    assert result.index_warning == ic.INDEX_WARNING_MESSAGE
    row = conn.execute("SELECT status FROM run_history").fetchone()
    assert row is not None and row["status"] == "SUCCESS"


def test_期間の収集では日経平均を取れなかった日を状態に残す(
    conn: psycopg.Connection[DictRow],
    api: FakeJQuants,  # noqa: F811
    yahoo: FakeYahoo,
    tmp_path: Any,
) -> None:
    api.add_day(D_0708, ["11110"])
    yahoo.frame = lambda start, end: pd.DataFrame()
    state = cc.RangeCollectState(tmp_path / "収集失敗.csv")
    cc.run_collect(D_0708, batch_type=const.BATCH_TYPE_DAILY, state=state)
    assert state.index_warning_dates == [D_0708]
    # 収集失敗の一覧には書かない
    assert state.failure_count == 0


def test_単独起動では日経平均を取れなくてもメールを送らず終了コード0(
    conn: psycopg.Connection[DictRow],
    api: FakeJQuants,  # noqa: F811
    yahoo: FakeYahoo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[Any] = []
    monkeypatch.setattr("common.mailer.send_mail", lambda *a, **k: sent.append(a))
    monkeypatch.setenv("ADMIN_MAIL_TO", "admin@example.com")
    api.add_day(D_0708, ["11110"])
    yahoo.frame = lambda start, end: pd.DataFrame()
    assert collect.main(["--date", "2026-07-08"]) == 0
    assert sent == []
    assert "日経平均に、指定日(2026-07-08)の行がありませんでした" in log_file_path().read_text(encoding="utf-8")
