"""core/collect_core.py と collect.py のテスト。J-Quants API は本物を呼ばない。"""

import csv
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

import collect
import const
from common import business_day, jquants_client
from common.env import resolve_path
from common.errors import ApiError, BatchError, PlanRangeError, RateLimitError, TransientApiError
from common.logger import get_logger, log_file_path
from core import collect_core as cc

D_0706 = date(2026, 7, 6)  # 月
D_0707 = date(2026, 7, 7)  # 火
D_0708 = date(2026, 7, 8)  # 水


# =====================================================================
# J-Quants API の代わり
# =====================================================================


def bar(d: date, code: str, close: float | None = 850.0, **extra: Any) -> dict[str, Any]:
    """株価四本値のレスポンスの1件。close が None なら取引なしの日。"""
    row: dict[str, Any] = {
        "Date": d.isoformat(),
        "Code": code,
        "O": None if close is None else close - 8,
        "H": None if close is None else close + 5,
        "L": None if close is None else close - 10,
        "C": close,
        "Vo": None if close is None else 1250000.0,
        "Va": None if close is None else 1061250000.0,
        "AdjFactor": 1.0,
        "UL": "0",
        "LL": "0",
    }
    row.update(extra)
    return row


def master(d: date, code: str, name: str = "サンプル商事", market: str = "0111") -> dict[str, Any]:
    return {
        "Date": d.isoformat(),
        "Code": code,
        "CoName": name,
        "CoNameEn": "SAMPLE",
        "S17": "13",
        "S17Nm": "商社・卸売",
        "S33": "6050",
        "S33Nm": "卸売業",
        "ScaleCat": "TOPIX Small 1",
        "Mkt": market,
        "MktNm": "プライム",
    }


def valuation(d: date, code: str) -> dict[str, Any]:
    return {
        "Date": d.isoformat(),
        "Code": code,
        "EPS": 85.2,
        "FwdEPS": 90.0,
        "BPS": 1020.5,
        "ROE": 0.0835,
        "FwdROE": 0.0882,
        "PER": 9.98,
        "FwdPER": 9.44,
        "PBR": 0.83,
        "MktCap": 37400.0,
    }


def fin(d: date, code: str, disc_no: str, **extra: str) -> dict[str, Any]:
    row = {
        "DiscNo": disc_no,
        "Code": code,
        "DiscDate": d.isoformat(),
        "DiscTime": "15:00:00",
        "DocType": "FYFinancialStatements_Consolidated_JP",
        "CurPerType": "FY",
        "CurPerSt": "2025-04-01",
        "CurPerEn": "2026-03-31",
        "CurFYSt": "2025-04-01",
        "CurFYEn": "2026-03-31",
        "Sales": "52000000000",
        "OP": "3100000000",
        "OdP": "",
        "NP": "2100000000",
        "EPS": "47.73",
        "BPS": "1020.5",
        "EqAR": "0.384",
        "FSales": "",
        "FOP": "",
        "FOdP": "",
        "FNP": "",
        "FEPS": "",
        "FDivAnn": "",
        "NxFSales": "54000000000",
        "NxFOP": "3300000000",
        "NxFNp": "2200000000",
        "NxFDivAnn": "26.0",
    }
    row.update(extra)
    return row


def earnings(pub: date, code: str, sch: str = "2026-08-07", fq: str = "1Q") -> dict[str, Any]:
    return {"PubDate": pub.isoformat(), "SchDate": sch, "FQName": fq, "FYE": "0331", "Code": code}


class FakeJQuants:
    """日付ごとに決めておいたデータを返す。呼ばれた日付を記録する。"""

    def __init__(self) -> None:
        self.bars: dict[date, list[dict[str, Any]]] = {}
        self.master: dict[date, list[dict[str, Any]]] = {}
        self.valuation: dict[date, list[dict[str, Any]]] = {}
        self.fins: dict[date, list[dict[str, Any]]] = {}
        self.earnings: dict[date, list[dict[str, Any]]] = {}
        self.calendar: list[dict[str, Any]] = []
        # 取引カレンダーの期間を受け取り、投げるエラーを返す(None ならエラーにしない)
        self.calendar_error: Callable[[date, date], Exception | None] | None = None
        self.calls: list[tuple[str, Any]] = []
        self.errors: dict[tuple[str, date], Exception] = {}

    def _get(self, kind: str, store: dict[date, list[dict[str, Any]]], d: date) -> list[dict[str, Any]]:
        self.calls.append((kind, d))
        if (kind, d) in self.errors:
            raise self.errors[(kind, d)]
        return list(store.get(d, []))

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(jquants_client, "fetch_daily_bars", lambda d: self._get("bars", self.bars, d))
        monkeypatch.setattr(jquants_client, "fetch_master", lambda d: self._get("master", self.master, d))
        monkeypatch.setattr(jquants_client, "fetch_valuation", lambda d: self._get("valuation", self.valuation, d))
        monkeypatch.setattr(jquants_client, "fetch_fin_summary", lambda d: self._get("fins", self.fins, d))
        monkeypatch.setattr(jquants_client, "fetch_earnings_date", lambda d: self._get("earnings", self.earnings, d))

        def fetch_calendar(date_from: date, date_to: date) -> list[dict[str, Any]]:
            self.calls.append(("calendar", (date_from, date_to)))
            if self.calendar_error is not None:
                error = self.calendar_error(date_from, date_to)
                if error is not None:
                    raise error
            # 本物と同じく、指定した期間の分だけを返す
            return [r for r in self.calendar if date_from.isoformat() <= r["Date"] <= date_to.isoformat()]

        monkeypatch.setattr(jquants_client, "fetch_calendar", fetch_calendar)

    def add_day(self, d: date, codes: list[str]) -> None:
        """その日の株価・銘柄一覧・バリュエーション指標をそろえる。"""
        self.bars[d] = [bar(d, c) for c in codes]
        self.master[d] = [master(d, c) for c in codes]
        self.valuation[d] = [valuation(d, c) for c in codes]

    def called(self, kind: str) -> list[Any]:
        return [d for k, d in self.calls if k == kind]


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeJQuants:
    fake = FakeJQuants()
    fake.install(monkeypatch)
    return fake


def _count(conn: psycopg.Connection[DictRow], sql: str, params: tuple[Any, ...] = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    assert row is not None
    return int(next(iter(row.values())))


def _fetched(conn: psycopg.Connection[DictRow], data_type: str) -> dict[date, tuple[str, int]]:
    rows = conn.execute(
        "SELECT target_date, status, record_count FROM fetched_date WHERE data_type = %s", (data_type,)
    ).fetchall()
    return {r["target_date"]: (r["status"], r["record_count"]) for r in rows}


def _seed_fetched(conn: psycopg.Connection[DictRow], d: date, count: int, status: str = "FETCHED") -> None:
    conn.execute(
        "INSERT INTO fetched_date (target_date, data_type, status, record_count) VALUES (%s, 'PRICE', %s, %s)",
        (d, status, count),
    )


# =====================================================================
# 値の変換
# =====================================================================


def test_to_decimal() -> None:
    assert cc.to_decimal(842.0) == Decimal("842.0")
    assert cc.to_decimal("47.73") == Decimal("47.73")
    assert cc.to_decimal(0.1) == Decimal("0.1")
    for empty in (None, "", "  ", "-", "abc", True, float("nan"), float("inf")):
        assert cc.to_decimal(empty) is None


def test_to_integerは四捨五入する() -> None:
    assert cc.to_integer(1250000.0) == Decimal(1250000)
    assert cc.to_integer("1.5") == Decimal(2)
    assert cc.to_integer(None) is None


def test_日付と時刻とフラグと文字列() -> None:
    assert cc.to_date("2026-07-08") == D_0708
    assert cc.to_date("20260708") == D_0708
    assert cc.to_date("") is None and cc.to_date(None) is None
    assert cc.to_time("15:00:00") == time(15, 0)
    assert cc.to_time("15:30") == time(15, 30)
    assert cc.to_time("") is None and cc.to_time(None) is None
    assert cc.to_flag("1") is True and cc.to_flag("0") is False
    assert cc.to_flag(None) is None and cc.to_flag("") is None
    assert cc.to_text(" a ") == "a" and cc.to_text("") is None and cc.to_text(None) is None


# =====================================================================
# 処理対象の日付
# =====================================================================


def test_dateの場合は取り逃した営業日と指定日(conn: psycopg.Connection[DictRow]) -> None:
    # 指定日の COLLECT_BACKFILL_DAYS 日前〜前日の営業日のうち、07-01 と 07-06 だけ取得済み
    _seed_fetched(conn, date(2026, 7, 1), 4400)
    _seed_fetched(conn, D_0706, 4400)
    _seed_fetched(conn, date(2026, 7, 2), 0, status="EMPTY")  # 空振りは取得済みではない
    targets = cc.decide_target_dates(conn, D_0708, None, None)
    start = D_0708 - timedelta(days=const.COLLECT_BACKFILL_DAYS)
    days = [start + timedelta(days=i) for i in range(const.COLLECT_BACKFILL_DAYS)]
    expected = [d for d in days if business_day.is_business_day(d) and d not in (date(2026, 7, 1), D_0706)]
    assert targets == [*expected, D_0708]
    assert date(2026, 7, 2) in targets


def test_補完の範囲は31日で取得済みの日は対象にならない(conn: psycopg.Connection[DictRow]) -> None:
    assert const.COLLECT_BACKFILL_DAYS == 31
    monday = date(2026, 6, 8)
    # 07-09 からはちょうど31日前なので範囲の中、07-10 からは32日前なので範囲の外
    assert monday in cc.decide_target_dates(conn, date(2026, 7, 9), None, None)
    assert monday not in cc.decide_target_dates(conn, date(2026, 7, 10), None, None)
    _seed_fetched(conn, monday, 4400)
    assert monday not in cc.decide_target_dates(conn, date(2026, 7, 9), None, None)


def test_指定日は休場日でも取得済みでも必ず含める(conn: psycopg.Connection[DictRow]) -> None:
    for d in cc.decide_target_dates(conn, date(2026, 7, 11), None, None):
        _seed_fetched(conn, d, 4400)
    assert cc.decide_target_dates(conn, date(2026, 7, 11), None, None) == [date(2026, 7, 11)]


def test_期間の場合は期間内の営業日の全て(conn: psycopg.Connection[DictRow]) -> None:
    _seed_fetched(conn, D_0707, 4400)
    assert cc.decide_target_dates(conn, D_0708, D_0706, D_0708) == [D_0706, D_0707, D_0708]
    assert cc.decide_target_dates(conn, date(2026, 7, 12), date(2026, 7, 11), date(2026, 7, 12)) == []


# =====================================================================
# 収集の全体
# =====================================================================


def _run(
    conn: psycopg.Connection[DictRow], target: date, date_from: date | None = None, date_to: date | None = None
) -> cc.CollectResult:
    return cc.collect(conn, target, date_from, date_to, get_logger("収集", target))


def test_指定日の分を取得して全てのテーブルに登録する(conn: psycopg.Connection[DictRow], api: FakeJQuants) -> None:
    # 補完の範囲は取得済みにしておく
    for d in cc.decide_target_dates(conn, D_0708, None, None)[:-1]:
        _seed_fetched(conn, d, 2)
    api.add_day(D_0708, ["11110", "22220"])
    api.bars[D_0708].append(bar(D_0708, "33330", close=None))  # 取引なし
    api.bars[D_0708][1].update({"UL": "1"})
    api.fins[D_0707] = [fin(D_0707, "11110", "100")]  # 前営業日の開示
    api.fins[D_0708] = [fin(D_0708, "22220", "200")]
    api.earnings[D_0708] = [earnings(D_0708, "11110"), earnings(D_0708, "22220", sch="")]

    result = _run(conn, D_0708)

    assert result == cc.CollectResult(fetched_dates=[D_0708], target_empty=False, price_count=3)
    rows = conn.execute("SELECT * FROM daily_price ORDER BY code").fetchall()
    assert [r["code"] for r in rows] == ["11110", "22220", "33330"]
    assert rows[0]["close"] == Decimal("850.0000")
    assert rows[0]["volume"] == Decimal(1250000)
    assert rows[0]["adj_factor"] == Decimal("1.0000000000")
    assert (rows[0]["upper_limit_flag"], rows[1]["upper_limit_flag"]) == (False, True)
    assert rows[2]["close"] is None and rows[2]["volume"] is None
    assert _fetched(conn, "PRICE")[D_0708] == ("FETCHED", 3)

    m = conn.execute("SELECT * FROM stock_master WHERE code = '11110'").fetchone()
    assert m is not None
    assert (m["name"], m["market_code"], m["applied_date"]) == ("サンプル商事", "0111", D_0708)
    assert _fetched(conn, "MASTER")[D_0708] == ("FETCHED", 2)

    v = conn.execute("SELECT * FROM daily_valuation WHERE code = '11110'").fetchone()
    assert v is not None
    assert (v["eps"], v["roe"], v["market_cap"]) == (Decimal("85.2000"), Decimal("0.083500"), Decimal(37400))
    assert _fetched(conn, "VALUATION")[D_0708] == ("FETCHED", 2)

    # 財務情報は株価を登録した日と、指定日の前営業日(07-07)を取得する
    assert api.called("fins") == [D_0707, D_0708]
    f = conn.execute("SELECT * FROM fin_summary WHERE disc_no = '100'").fetchone()
    assert f is not None
    assert (f["sales"], f["odp"], f["eps"], f["equity_ratio"]) == (
        Decimal(52000000000),
        None,
        Decimal("47.7300"),
        Decimal("0.384000"),
    )
    assert (f["nx_f_sales"], f["nx_f_div_ann"]) == (Decimal(54000000000), Decimal("26.0000"))
    assert f["disc_time"] == time(15, 0)
    assert _fetched(conn, "FIN_SUMMARY") == {D_0707: ("FETCHED", 1), D_0708: ("FETCHED", 1)}

    e = conn.execute("SELECT * FROM earnings_date ORDER BY code").fetchall()
    assert [r["sch_date"] for r in e] == [date(2026, 8, 7), None]
    assert _fetched(conn, "EARNINGS_DATE") == {D_0707: ("EMPTY", 0), D_0708: ("FETCHED", 2)}

    log = log_file_path().read_text(encoding="utf-8")
    assert "収集 2026-07-08 - 株価を登録しました(3件)" in log
    assert "収集 2026-07-07 - 財務情報を登録しました(1件)" in log


def test_取り逃した日も古い順に取得する(conn: psycopg.Connection[DictRow], api: FakeJQuants) -> None:
    targets = cc.decide_target_dates(conn, D_0708, None, None)
    for d in targets[:-2]:
        _seed_fetched(conn, d, 2)
    api.add_day(D_0707, ["11110", "22220"])
    api.add_day(D_0708, ["11110", "22220"])

    result = _run(conn, D_0708)

    assert result.fetched_dates == [D_0707, D_0708]
    assert result.price_count == 4
    assert api.called("bars") == [D_0707, D_0708]
    assert _count(conn, "SELECT count(*) FROM daily_price") == 4


def test_指定日が0件なら空振りでDBの株価は変えない(conn: psycopg.Connection[DictRow], api: FakeJQuants) -> None:
    for d in cc.decide_target_dates(conn, D_0708, None, None)[:-1]:
        _seed_fetched(conn, d, 2)

    result = _run(conn, D_0708)

    assert result.fetched_dates == []
    assert result.target_empty is True
    assert _fetched(conn, "PRICE")[D_0708] == ("EMPTY", 0)
    # 株価を登録できた日がないので、銘柄一覧などは取得しない。財務情報は前営業日の分だけ取得する
    assert api.called("master") == []
    assert api.called("fins") == [D_0707]


def test_取得済みの日を空振りで上書きしない(conn: psycopg.Connection[DictRow], api: FakeJQuants) -> None:
    api.add_day(D_0708, ["11110"])
    _run(conn, D_0708, D_0708, D_0708)
    api.bars[D_0708] = []
    _run(conn, D_0708, D_0708, D_0708)
    assert _fetched(conn, "PRICE")[D_0708] == ("FETCHED", 1)
    assert _count(conn, "SELECT count(*) FROM daily_price") == 1


def test_再実行するとその日の株価を削除して作り直す(conn: psycopg.Connection[DictRow], api: FakeJQuants) -> None:
    api.add_day(D_0708, ["11110", "22220"])
    _run(conn, D_0708, D_0708, D_0708)
    api.add_day(D_0708, ["11110", "33330"])
    api.bars[D_0708][0]["C"] = 900.0
    _run(conn, D_0708, D_0708, D_0708)
    rows = conn.execute("SELECT code, close FROM daily_price ORDER BY code").fetchall()
    assert [(r["code"], r["close"]) for r in rows] == [("11110", Decimal("900.0000")), ("33330", Decimal("850.0000"))]
    assert [r["code"] for r in conn.execute("SELECT code FROM daily_valuation ORDER BY code").fetchall()] == [
        "11110",
        "33330",
    ]


def test_件数が前回の半分未満なら失敗にしてDBを変えない(conn: psycopg.Connection[DictRow], api: FakeJQuants) -> None:
    _seed_fetched(conn, D_0707, 10)
    api.add_day(D_0708, ["1", "2", "3", "4"])  # 4件 < 10 × 0.5
    with pytest.raises(BatchError, match=r"^取得件数が少なすぎます\(今回 4件 / 前回 10件\)$"):
        _run(conn, D_0708, D_0708, D_0708)
    assert _count(conn, "SELECT count(*) FROM daily_price") == 0
    assert D_0708 not in _fetched(conn, "PRICE")


def test_件数がちょうど半分なら登録する(conn: psycopg.Connection[DictRow], api: FakeJQuants) -> None:
    _seed_fetched(conn, D_0707, 10)
    api.add_day(D_0708, ["1", "2", "3", "4", "5"])
    assert _run(conn, D_0708, D_0708, D_0708).fetched_dates == [D_0708]


def test_期間の途中で失敗したらそれまでの日は残る(conn: psycopg.Connection[DictRow], api: FakeJQuants) -> None:
    api.add_day(D_0706, ["11110"])
    api.errors[("bars", D_0707)] = ApiError("403", 403)
    with pytest.raises(ApiError):
        _run(conn, D_0708, D_0706, D_0708)
    assert set(_fetched(conn, "PRICE")) == {D_0706}
    assert _count(conn, "SELECT count(*) FROM daily_price WHERE trade_date = %s", (D_0706,)) == 1


def test_銘柄マスタは古い情報で上書きしない(conn: psycopg.Connection[DictRow], api: FakeJQuants) -> None:
    api.add_day(D_0708, ["11110"])
    api.master[D_0708] = [master(D_0708, "11110", name="新しい名前")]
    _run(conn, D_0708, D_0708, D_0708)
    api.add_day(D_0706, ["11110"])
    api.master[D_0706] = [master(D_0706, "11110", name="古い名前", market="0101")]
    _run(conn, D_0706, D_0706, D_0706)
    m = conn.execute("SELECT name, market_code, applied_date FROM stock_master").fetchone()
    assert m is not None
    assert (m["name"], m["market_code"], m["applied_date"]) == ("新しい名前", "0111", D_0708)
    # 新しい情報なら上書きする
    api.add_day(date(2026, 7, 9), ["11110"])
    api.master[date(2026, 7, 9)] = [master(date(2026, 7, 9), "11110", name="さらに新しい名前")]
    _run(conn, date(2026, 7, 9), date(2026, 7, 9), date(2026, 7, 9))
    m = conn.execute("SELECT name FROM stock_master").fetchone()
    assert m is not None and m["name"] == "さらに新しい名前"


def test_財務情報と決算発表予定日は削除せず同じキーは上書きする(
    conn: psycopg.Connection[DictRow], api: FakeJQuants
) -> None:
    api.add_day(D_0708, ["11110"])
    api.fins[D_0708] = [fin(D_0708, "11110", "200", Sales="100")]
    api.earnings[D_0708] = [earnings(D_0708, "11110", sch="")]
    _run(conn, D_0708, D_0708, D_0708)
    api.fins[D_0708] = [fin(D_0708, "11110", "200", Sales="120"), fin(D_0708, "11110", "201")]
    api.earnings[D_0708] = [earnings(D_0708, "11110", sch="2026-08-07")]
    api.fins[D_0707] = []
    _run(conn, D_0708, D_0708, D_0708)
    rows = conn.execute("SELECT disc_no, sales FROM fin_summary ORDER BY disc_no").fetchall()
    assert [(r["disc_no"], r["sales"]) for r in rows] == [("200", Decimal(120)), ("201", Decimal(52000000000))]
    e = conn.execute("SELECT sch_date FROM earnings_date").fetchall()
    assert [r["sch_date"] for r in e] == [date(2026, 8, 7)]


def test_翌期の予想の項目がない場合はWARNINGで実際の項目名を出す(
    conn: psycopg.Connection[DictRow], api: FakeJQuants
) -> None:
    api.add_day(D_0708, ["11110"])
    row = fin(D_0708, "11110", "200")
    for key in ("NxFSales", "NxFOP", "NxFNp", "NxFDivAnn"):
        row.pop(key)
    row["NxFNetSales"] = "1"
    api.fins[D_0708] = [row]
    _run(conn, D_0708, D_0708, D_0708)
    log = log_file_path().read_text(encoding="utf-8")
    assert "WARNING 収集 2026-07-08 - 財務情報に、翌事業年度の予想の項目がありません" in log
    assert "レスポンスの NxF の項目: NxFNetSales" in log
    f = conn.execute("SELECT nx_f_sales FROM fin_summary").fetchone()
    assert f is not None and f["nx_f_sales"] is None


def test_翌期の予想の項目がそろっていればWARNINGは出さない(conn: psycopg.Connection[DictRow], api: FakeJQuants) -> None:
    cc.check_next_year_fields([], get_logger("収集", D_0708))
    cc.check_next_year_fields([fin(D_0708, "1", "1")], get_logger("収集", D_0708))
    assert "WARNING" not in log_file_path().read_text(encoding="utf-8")


def test_変換できない値は銘柄コード付きのエラー(conn: psycopg.Connection[DictRow], api: FakeJQuants) -> None:
    api.add_day(D_0708, ["11110"])
    api.bars[D_0708][0]["Date"] = "2026-99-99"
    with pytest.raises(BatchError) as e:
        _run(conn, D_0708, D_0708, D_0708)
    assert e.value.code == "11110"
    api.bars[D_0708][0]["Date"] = D_0708.isoformat()
    api.fins[D_0708] = [fin(D_0708, "22220", "1", DiscTime="25:99")]
    with pytest.raises(BatchError) as e2:
        _run(conn, D_0708, D_0708, D_0708)
    assert e2.value.code == "22220"


def test_CALENDARの場合は先に取引カレンダーを取得する(
    conn: psycopg.Connection[DictRow], api: FakeJQuants, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(const, "BUSINESS_DAY_SOURCE", const.BUSINESS_DAY_SOURCE_CALENDAR)
    start = D_0708 - timedelta(days=const.COLLECT_BACKFILL_DAYS)
    days = [start + timedelta(days=i) for i in range(const.COLLECT_BACKFILL_DAYS + 1)]
    api.calendar = [
        {"Date": d.isoformat(), "HolDiv": "0" if d.weekday() >= 5 else ("2" if d == D_0707 else "1")} for d in days
    ]
    api.add_day(D_0708, ["11110"])

    _run(conn, D_0708)

    assert api.called("calendar") == [(date(2026, 1, 1), date(2027, 12, 31))]
    cal = conn.execute("SELECT * FROM trading_calendar WHERE cal_date = %s", (D_0707,)).fetchone()
    assert cal is not None
    assert (cal["holiday_division"], cal["is_business_day"]) == ("2", True)
    # 取引カレンダーで営業日を判定し、補完の範囲の最初の営業日〜07-08 を取得しにいく
    assert api.called("bars")[0] == next(d for d in days if d.weekday() < 5)
    assert api.called("bars")[-1] == D_0708


def test_CALENDARで期間の場合は期間の年で取引カレンダーを取得する(
    conn: psycopg.Connection[DictRow], api: FakeJQuants, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(const, "BUSINESS_DAY_SOURCE", const.BUSINESS_DAY_SOURCE_CALENDAR)
    api.calendar = [
        {"Date": "2024-12-27", "HolDiv": "1"},
        {"Date": "2024-12-28", "HolDiv": "0"},
        {"Date": "2024-12-29", "HolDiv": "0"},
        {"Date": "2024-12-30", "HolDiv": "1"},
    ]
    _run(conn, date(2024, 12, 30), date(2024, 12, 30), date(2024, 12, 30))
    assert api.called("calendar") == [(date(2024, 1, 1), date(2025, 12, 31))]


# =====================================================================
# run_collect と collect.py
# =====================================================================


def test_run_collectは実行履歴に成功と件数を残す(conn: psycopg.Connection[DictRow], api: FakeJQuants) -> None:
    api.add_day(D_0708, ["11110", "22220"])
    result = cc.run_collect(D_0708, D_0708, D_0708, const.BATCH_TYPE_DAILY)
    assert result.fetched_dates == [D_0708]
    row = conn.execute("SELECT * FROM run_history").fetchone()
    assert row is not None
    assert (row["batch_type"], row["process_name"], row["status"], row["record_count"]) == (
        "DAILY",
        "COLLECT",
        "SUCCESS",
        2,
    )
    log = log_file_path().read_text(encoding="utf-8")
    assert "収集 2026-07-08 - 処理を開始します" in log
    assert "処理を終了します(取得できた日付: 1件、指定日の空振り: なし)" in log


def test_run_collectで空振りは実行履歴がEMPTY(conn: psycopg.Connection[DictRow], api: FakeJQuants) -> None:
    result = cc.run_collect(date(2026, 7, 11))
    assert result.target_empty is True
    row = conn.execute("SELECT status, batch_type FROM run_history").fetchone()
    assert row is not None
    assert (row["status"], row["batch_type"]) == ("EMPTY", "SINGLE")


def test_run_collectで失敗は実行履歴がFAILED(conn: psycopg.Connection[DictRow], api: FakeJQuants) -> None:
    api.errors[("bars", D_0708)] = ApiError(
        "J-Quants API がエラーを返しました(403)。契約プランの期間外の可能性があります", 403
    )
    with pytest.raises(BatchError) as e:
        cc.run_collect(D_0708, D_0708, D_0708)
    assert e.value.process_name == "収集"
    row = conn.execute("SELECT status, error_message FROM run_history").fetchone()
    assert row is not None
    assert row["status"] == "FAILED"
    assert "契約プランの期間外の可能性があります" in row["error_message"]


def test_collect_pyの終了コード(conn: psycopg.Connection[DictRow], api: FakeJQuants) -> None:
    api.add_day(D_0708, ["11110"])
    assert collect.main(["--from", "2026-07-08", "--to", "2026-07-08"]) == 0
    assert collect.main(["--date", "2026-07-11"]) == 0  # 空振りも 0
    api.errors[("bars", D_0707)] = ApiError("403", 403)
    assert collect.main(["--date", "2026-07-07"]) == 1


def test_collect_pyの引数エラーは終了コード1でDBを変えない(conn: psycopg.Connection[DictRow]) -> None:
    assert collect.main(["--date", "2026/07/08"]) == 1
    assert collect.main(["--from", "2026-07-08"]) == 1
    assert _count(conn, "SELECT count(*) FROM run_history") == 0
    log = log_file_path().read_text(encoding="utf-8")
    assert "日付の形式が正しくありません(--date)" in log
    assert "--from と --to は両方を指定してください" in log


# =====================================================================
# 期間の収集で取れない日の扱い
# =====================================================================


def _state(tmp_path: Path) -> cc.RangeCollectState:
    return cc.RangeCollectState(tmp_path / "収集失敗_20261102_2100.csv")


def _run_range(
    conn: psycopg.Connection[DictRow],
    state: cc.RangeCollectState,
    target: date,
    date_from: date | None = None,
    date_to: date | None = None,
) -> cc.CollectResult:
    return cc.collect(conn, target, date_from, date_to, get_logger("収集", target), state)


def _plan_error(status: int = 403) -> PlanRangeError:
    return PlanRangeError(f"J-Quants API がエラーを返しました({status})。契約プランの期間外の可能性があります", status)


def _subscription_error(first: date | None = date(2016, 11, 2)) -> PlanRangeError:
    """契約プランの期間外の 400(メッセージに契約期間が入ったもの)。"""
    return PlanRangeError(
        "J-Quants API がエラーを返しました(400)。契約プランの期間外の可能性があります",
        400,
        subscription_range=True,
        first_date=first,
    )


def _failure_rows(state: cc.RangeCollectState) -> list[list[str]]:
    with state.failure_path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def test_期間の収集でまだ株価がない日の403はWARNINGでとばす(
    conn: psycopg.Connection[DictRow], api: FakeJQuants, tmp_path: Path
) -> None:
    state = _state(tmp_path)
    api.errors[("bars", D_0706)] = _plan_error()
    api.add_day(D_0707, ["11110"])
    api.add_day(D_0708, ["11110"])

    result = _run_range(conn, state, D_0708, D_0706, D_0708)

    assert result.fetched_dates == [D_0707, D_0708]
    # 期間外は失敗の一覧に書かない。取得済み日付にも登録しない
    assert state.failure_count == 0
    assert not state.failure_path.exists()
    assert D_0706 not in _fetched(conn, "PRICE")
    assert state.out_of_range_dates == {D_0706}
    log = log_file_path().read_text(encoding="utf-8")
    assert (
        "WARNING 収集 2026-07-06 - 株価四本値を取得できませんでした。この日以前の株価をまだ取得できていないため" in log
    )


def test_期間の収集でその日以前の株価がある日の403は止める(
    conn: psycopg.Connection[DictRow], api: FakeJQuants, tmp_path: Path
) -> None:
    _seed_fetched(conn, D_0706, 4400)
    api.errors[("bars", D_0708)] = _plan_error()
    with pytest.raises(BatchError) as e:
        _run_range(conn, _state(tmp_path), D_0708, D_0708, D_0708)
    assert "APIキーと契約を確かめてください" in e.value.message


@pytest.mark.parametrize(
    "data_type",
    [
        const.DATA_TYPE_PRICE,
        const.DATA_TYPE_MASTER,
        const.DATA_TYPE_VALUATION,
        const.DATA_TYPE_FIN_SUMMARY,
        const.DATA_TYPE_EARNINGS_DATE,
    ],
)
def test_期間の収集で契約期間の400はどのデータの種類でもWARNINGでとばす(
    conn: psycopg.Connection[DictRow], tmp_path: Path, data_type: str
) -> None:
    state = _state(tmp_path)

    def fetch(target: date) -> list[dict[str, Any]]:
        raise _subscription_error()

    rows = cc.fetch_in_range(conn, fetch, D_0706, data_type, get_logger("収集", D_0706), state)

    # 止めない。失敗の一覧にも書かない
    assert rows is None
    assert state.failure_count == 0
    assert not state.failure_path.exists()
    assert "を取得できませんでした" in log_file_path().read_text(encoding="utf-8")


def test_期間の収集で株価の400は株価がないまま期間外とした日に数える(
    conn: psycopg.Connection[DictRow], api: FakeJQuants, tmp_path: Path
) -> None:
    state = _state(tmp_path)
    api.errors[("bars", D_0706)] = _subscription_error()
    api.add_day(D_0707, ["11110"])

    result = _run_range(conn, state, D_0707, D_0706, D_0707)

    assert result.fetched_dates == [D_0707]
    assert state.out_of_range_dates == {D_0706}
    assert state.failure_count == 0


def test_期間の収集で株価のない日の契約期間の400は日数が上限を超えたら止める(
    conn: psycopg.Connection[DictRow], api: FakeJQuants, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(const, "COLLECT_MAX_OUT_OF_RANGE_DAYS", 2)
    for d in (D_0706, D_0707, D_0708):
        api.errors[("bars", d)] = _subscription_error()
    with pytest.raises(BatchError) as e:
        _run_range(conn, _state(tmp_path), D_0708, D_0706, D_0708)
    assert "を超えたため止めます" in e.value.message


def test_期間の収集でその日以前の株価がある日の契約期間の400は止める(
    conn: psycopg.Connection[DictRow], api: FakeJQuants, tmp_path: Path
) -> None:
    _seed_fetched(conn, D_0706, 4400)
    api.errors[("bars", D_0708)] = _subscription_error()
    with pytest.raises(BatchError) as e:
        _run_range(conn, _state(tmp_path), D_0708, D_0708, D_0708)
    assert "APIキーと契約を確かめてください" in e.value.message


def test_日次バッチでは契約期間の400で止める(conn: psycopg.Connection[DictRow], api: FakeJQuants) -> None:
    api.errors[("bars", D_0708)] = _subscription_error()
    with pytest.raises(PlanRangeError):
        _run(conn, D_0708, D_0708, D_0708)


def test_期間の収集で株価がある日の銘柄一覧の403は止める(
    conn: psycopg.Connection[DictRow], api: FakeJQuants, tmp_path: Path
) -> None:
    api.add_day(D_0708, ["11110"])
    api.errors[("master", D_0708)] = _plan_error()
    with pytest.raises(BatchError):
        _run_range(conn, _state(tmp_path), D_0708, D_0708, D_0708)


def test_期間の収集で決算発表予定日の400はとばす(
    conn: psycopg.Connection[DictRow], api: FakeJQuants, tmp_path: Path
) -> None:
    api.add_day(D_0708, ["11110"])
    api.fins[D_0708] = [fin(D_0708, "11110", "20260708001")]
    api.errors[("earnings", D_0708)] = _plan_error(400)

    result = _run_range(conn, _state(tmp_path), D_0708, D_0708, D_0708)

    assert result.fetched_dates == [D_0708]
    assert _count(conn, "SELECT count(*) FROM fin_summary") == 1
    assert D_0708 not in _fetched(conn, "EARNINGS_DATE")
    assert "決算発表予定日を取得できませんでした。期間外とみなして、とばします" in log_file_path().read_text(
        encoding="utf-8"
    )


def test_日次バッチでは決算発表予定日の400で止める(conn: psycopg.Connection[DictRow], api: FakeJQuants) -> None:
    api.add_day(D_0708, ["11110"])
    api.errors[("earnings", D_0708)] = _plan_error(400)
    with pytest.raises(PlanRangeError):
        _run(conn, D_0708, D_0708, D_0708)


def test_期間の収集で0件の日はWARNINGを出す(
    conn: psycopg.Connection[DictRow], api: FakeJQuants, tmp_path: Path
) -> None:
    api.bars[D_0708] = [bar(D_0708, "11110")]
    result = _run_range(conn, _state(tmp_path), D_0708, D_0707, D_0708)
    assert result.fetched_dates == [D_0708]
    assert _fetched(conn, "PRICE")[D_0707] == ("EMPTY", 0)
    log = log_file_path().read_text(encoding="utf-8")
    assert "WARNING 収集 2026-07-07 - 株価が0件でした(空振り)。とばします" in log
    assert "WARNING 収集 2026-07-08 - 銘柄マスタが0件でした" in log
    assert "WARNING 収集 2026-07-08 - バリュエーション指標が0件でした" in log


def test_期間の収集で一時的なエラーは失敗の一覧に書いて次の日へ進む(
    conn: psycopg.Connection[DictRow], api: FakeJQuants, tmp_path: Path
) -> None:
    state = _state(tmp_path)
    for d in (D_0706, D_0707, D_0708):
        api.add_day(d, ["11110"])
    api.errors[("bars", D_0707)] = TransientApiError("通信エラー(/equities/bars/daily date=2026-07-07): 接続できません")
    api.errors[("master", D_0706)] = RateLimitError("リクエスト数の上限を超えました", 429)
    api.errors[("fins", D_0708)] = TransientApiError("J-Quants API がエラーを返しました(503)", 503)

    result = _run_range(conn, state, D_0708, D_0706, D_0708)

    # 株価を登録できた日は、銘柄一覧が取れなくても取得できた日に入る
    assert result.fetched_dates == [D_0706, D_0708]
    assert state.failed_price_dates == {D_0707}
    # 銘柄一覧が取れなかった日は、その日の残り(バリュエーション指標)をとばす
    assert D_0706 not in api.called("valuation")
    # 財務情報が取れなかった日は、その日の決算発表予定日もとばす
    assert D_0708 not in api.called("earnings")
    assert state.failure_count == 3
    # 3日続けて失敗していない(07-08 は全て取れた)ので止まらない。07-08 で数え直す
    assert state.network_fail_streak == 0
    assert state.failure_path.read_bytes().startswith(b"\xef\xbb\xbf")
    rows = _failure_rows(state)
    assert rows[0] == cc.FAILURE_CSV_HEADER
    assert [r[:3] for r in rows[1:]] == [
        ["2026-07-06", "上場銘柄一覧", "429(リクエスト数の上限)"],
        ["2026-07-07", "株価四本値", "通信エラー・タイムアウト"],
        ["2026-07-08", "財務情報", "503(サーバー側のエラー)"],
    ]
    assert rows[2][3] == "通信エラー(/equities/bars/daily date=2026-07-07): 接続できません"
    assert rows[2][5] == "python collect.py --from 2026-07-07 --to 2026-07-07"


def test_期間の収集でネットワークのエラーが3日続いたら止める(
    conn: psycopg.Connection[DictRow], api: FakeJQuants, tmp_path: Path
) -> None:
    state = _state(tmp_path)
    days = [D_0706, D_0707, D_0708]
    for d in days:
        api.errors[("bars", d)] = TransientApiError("通信エラー")
    with pytest.raises(BatchError) as e:
        _run_range(conn, state, D_0708, D_0706, D_0708)
    assert "取れなかった日が3日続いたため止めます" in e.value.message
    assert state.failure_count == 3


def test_期間の収集でネットワークのエラーの日数は日をまたいで数える(
    conn: psycopg.Connection[DictRow], api: FakeJQuants, tmp_path: Path
) -> None:
    # range_date_full_process.py のように、1日ずつ呼んでも数え続ける
    state = _state(tmp_path)
    api.errors[("bars", D_0706)] = TransientApiError("通信エラー")
    api.errors[("valuation", D_0707)] = TransientApiError("通信エラー")
    api.add_day(D_0707, ["11110"])
    _run_range(conn, state, D_0706, D_0706, D_0706)
    _run_range(conn, state, D_0707, D_0707, D_0707)
    assert state.network_fail_streak == 2
    api.errors[("bars", D_0708)] = TransientApiError("通信エラー")
    with pytest.raises(BatchError):
        _run_range(conn, state, D_0708, D_0708, D_0708)


def test_期間の収集で株価が1日もないまま期間外の日が上限を超えたら止める(
    conn: psycopg.Connection[DictRow], api: FakeJQuants, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(const, "COLLECT_MAX_OUT_OF_RANGE_DAYS", 2)
    for d in (D_0706, D_0707, D_0708):
        api.errors[("bars", d)] = _plan_error()
    with pytest.raises(BatchError) as e:
        _run_range(conn, _state(tmp_path), D_0708, D_0706, D_0708)
    assert "期間外(403、400)としてとばした日が2日を超えたため止めます" in e.value.message


def test_日次バッチではネットワークのエラーで止める(conn: psycopg.Connection[DictRow], api: FakeJQuants) -> None:
    api.errors[("bars", D_0708)] = TransientApiError("通信エラー")
    with pytest.raises(TransientApiError):
        _run(conn, D_0708, D_0708, D_0708)


def _calendar_rows(start: date, end: date) -> list[dict[str, Any]]:
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    return [{"Date": d.isoformat(), "HolDiv": "0" if d.weekday() >= 5 else "1"} for d in days]


def test_期間の収集で取引カレンダーが取れなければ年と月に分けて取り直す(
    conn: psycopg.Connection[DictRow], api: FakeJQuants, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(const, "BUSINESS_DAY_SOURCE", const.BUSINESS_DAY_SOURCE_CALENDAR)
    oldest_available = date(2016, 10, 15)
    api.calendar = _calendar_rows(oldest_available, date(2017, 12, 31))
    # 期間外(2016-10-15 より前)を含む期間を指定すると 403 になる
    api.calendar_error = lambda f, t: _plan_error() if f < oldest_available else None
    api.add_day(date(2016, 10, 31), ["11110"])
    api.add_day(date(2016, 11, 1), ["11110"])
    state = _state(tmp_path)

    result = _run_range(conn, state, date(2016, 11, 1), date(2016, 10, 24), date(2016, 11, 1))

    calls = api.called("calendar")
    assert calls[0] == (date(2016, 1, 1), date(2017, 12, 31))
    assert calls[1] == (date(2016, 1, 1), date(2016, 12, 31))
    assert (date(2016, 10, 1), date(2016, 10, 31)) in calls
    assert (date(2016, 11, 1), date(2016, 11, 30)) in calls
    assert (date(2017, 1, 1), date(2017, 12, 31)) in calls
    # 2016-10 は取れなかったので、取引カレンダーがない日としてとばす。11月から取得する
    assert result.fetched_dates == [date(2016, 11, 1)]
    assert date(2016, 10, 31) not in api.called("bars")
    log = log_file_path().read_text(encoding="utf-8")
    assert "取引カレンダーを取得できませんでした(2016-10-01 〜 2016-10-31" in log
    assert "取引カレンダーがない日をとばします(2016-10-24 〜 2016-10-31 のうち 8日" in log
    # 同じ実行の中では、同じ期間の取引カレンダーを取り直さない
    count = len(api.called("calendar"))
    _run_range(conn, state, date(2016, 11, 1), date(2016, 11, 1), date(2016, 11, 1))
    assert len(api.called("calendar")) == count


def test_期間の収集で取引カレンダーの開始日が契約の最初の日より前なら最初の日にして取り直す(
    conn: psycopg.Connection[DictRow], api: FakeJQuants, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(const, "BUSINESS_DAY_SOURCE", const.BUSINESS_DAY_SOURCE_CALENDAR)
    first = date(2016, 11, 2)
    api.calendar = _calendar_rows(first, date(2017, 12, 31))
    api.calendar_error = lambda f, t: _subscription_error(first) if f < first else None
    api.add_day(date(2016, 11, 2), ["11110"])
    api.add_day(date(2016, 11, 4), ["11110"])

    result = _run_range(conn, _state(tmp_path), date(2016, 11, 4), date(2016, 10, 3), date(2016, 11, 4))

    # 年・月に分けず、開始日だけを直して1回取り直す
    assert api.called("calendar") == [(date(2016, 1, 1), date(2017, 12, 31)), (first, date(2017, 12, 31))]
    assert result.fetched_dates == [date(2016, 11, 2), date(2016, 11, 4)]
    assert "契約の最初の日(2016-11-02)にして取り直します" in log_file_path().read_text(encoding="utf-8")


def test_取引カレンダーの契約の最初の日が期間より後なら取れない期間として扱う(
    conn: psycopg.Connection[DictRow], api: FakeJQuants, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(const, "BUSINESS_DAY_SOURCE", const.BUSINESS_DAY_SOURCE_CALENDAR)
    state = _state(tmp_path)
    logger = get_logger("収集", D_0708)
    api.calendar_error = lambda f, t: _subscription_error(date(2030, 1, 1))
    part = cc._calendar_part(conn, date(2016, 1, 1), date(2016, 12, 31), logger, state, (D_0708, D_0708))
    assert part is None
    assert api.called("calendar") == [(date(2016, 1, 1), date(2016, 12, 31))]


def test_期間の収集で株価がある期間の取引カレンダーの403は止める(
    conn: psycopg.Connection[DictRow], api: FakeJQuants, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(const, "BUSINESS_DAY_SOURCE", const.BUSINESS_DAY_SOURCE_CALENDAR)
    _seed_fetched(conn, date(2025, 12, 30), 4400)
    api.calendar_error = lambda f, t: _plan_error()
    with pytest.raises(BatchError) as e:
        _run_range(conn, _state(tmp_path), D_0708, D_0708, D_0708)
    assert "APIキーと契約を確かめてください" in e.value.message


def test_期間の収集で取引カレンダーのネットワークのエラーは失敗の一覧に書く(
    conn: psycopg.Connection[DictRow], api: FakeJQuants, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(const, "BUSINESS_DAY_SOURCE", const.BUSINESS_DAY_SOURCE_CALENDAR)
    api.calendar_error = lambda f, t: TransientApiError("通信エラー")
    state = _state(tmp_path)

    result = _run_range(conn, state, D_0708, D_0706, D_0708)

    # 取引カレンダーがないので、全ての日をとばす(止めない)
    assert result.fetched_dates == []
    rows = _failure_rows(state)
    assert rows[1][:2] == ["2026-07-06", "取引カレンダー"]
    assert rows[1][5] == "python collect.py --from 2026-07-06 --to 2026-07-08"
    # 次に呼ばれたときは取り直す
    _run_range(conn, state, D_0708, D_0706, D_0708)
    assert len(api.called("calendar")) == 2


def test_期間の収集で前営業日を判定できなければ前営業日の財務情報をとばす(
    conn: psycopg.Connection[DictRow], api: FakeJQuants, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(const, "BUSINESS_DAY_SOURCE", const.BUSINESS_DAY_SOURCE_CALENDAR)
    api.calendar = _calendar_rows(D_0708, D_0708)
    api.add_day(D_0708, ["11110"])
    result = _run_range(conn, _state(tmp_path), D_0708, D_0708, D_0708)
    assert result.fetched_dates == [D_0708]
    assert api.called("fins") == [D_0708]
    assert "前営業日を判定できないため" in log_file_path().read_text(encoding="utf-8")


def test_日次バッチでは前営業日を判定できなければ止める(
    conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(const, "BUSINESS_DAY_SOURCE", const.BUSINESS_DAY_SOURCE_CALENDAR)
    with pytest.raises(business_day.CalendarMissingError):
        cc.disclosure_dates(conn, [], D_0708)


def test_collect_pyの期間で失敗の一覧に書いた日があれば終了コード1(
    conn: psycopg.Connection[DictRow], api: FakeJQuants
) -> None:
    api.add_day(D_0707, ["11110"])
    api.errors[("bars", D_0708)] = TransientApiError("通信エラー")
    assert collect.main(["--from", "2026-07-07", "--to", "2026-07-08"]) == 1
    log = log_file_path().read_text(encoding="utf-8")
    assert "取れなかったデータが 1件あります" in log
    files = list(log_file_path().parent.glob("収集失敗_*.csv"))
    assert len(files) == 1
    # 実行履歴は、取れた分を登録したので SUCCESS
    row = conn.execute("SELECT status FROM run_history").fetchone()
    assert row is not None
    assert row["status"] == "SUCCESS"


def test_failure_file_pathは実行を始めた日時の名前() -> None:
    path = cc.failure_file_path(datetime(2026, 11, 2, 21, 5))
    assert path.name == "収集失敗_20261102_2105.csv"
    assert path.parent == resolve_path(const.LOG_DIR)
