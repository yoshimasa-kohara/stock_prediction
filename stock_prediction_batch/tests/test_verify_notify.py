"""core/verify_core.py、core/notify_core.py と、verify.py、notify.py のテスト。"""

from datetime import date
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg
import pytest
from psycopg.rows import DictRow

import const
import notify as notify_cli
import verify as verify_cli
from common import mailer
from common.batch_runner import BatchWarnings
from common.errors import BatchError, MailSendError
from common.logger import log_file_path
from core import notify_core, verify_core
from core.market_state_core import IndexTrend, IndexTrendItem, MarketState

P1 = date(2026, 7, 1)  # 予測日
T5 = date(2026, 7, 8)  # 5営業日後
T10 = date(2026, 7, 15)  # 10営業日後


def _price(conn: psycopg.Connection[DictRow], d: date, code: str, close: str | None, factor: str = "1",
           turnover: int | None = 200_000_000, upper: bool = False) -> None:  # fmt: skip
    conn.execute(
        "INSERT INTO daily_price (trade_date, code, close, turnover, adj_factor, upper_limit_flag, lower_limit_flag)"
        " VALUES (%s, %s, %s, %s, %s, %s, false)",
        (d, code, None if close is None else Decimal(close), turnover, Decimal(factor), upper),
    )


def _fetched(conn: psycopg.Connection[DictRow], d: date) -> None:
    conn.execute(
        "INSERT INTO fetched_date (target_date, data_type, status, record_count) VALUES (%s, 'PRICE', 'FETCHED', 3)"
        " ON CONFLICT DO NOTHING",
        (d,),
    )


def _pred(conn: psycopg.Connection[DictRow], code: str, pred: date, horizon: int, target: date, up: str,
          base: str, pred_price: str = "1000", rank: int | None = None, status: str = "PENDING",
          rel: str = "0.5", sector: str | None = None, rank_sector: int | None = None) -> None:  # fmt: skip
    conn.execute(
        "INSERT INTO prediction (code, pred_date, horizon, target_date, up_prob, rel_prob, pred_price, base_price,"
        " model_version, sector33_code, buy_rank, buy_rank_sector, verify_status)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '20260627', %s, %s, %s, %s)",
        (code, pred, horizon, target, Decimal(up), Decimal(rel), Decimal(pred_price), Decimal(base), sector, rank,
         rank_sector, status),
    )  # fmt: skip


@pytest.fixture
def verify_data(conn: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    """予測日 07-01 の5営業日後(07-08)の予測。"""
    # A: 上昇予測で上がった(当たり)。B: 上昇予測で同じ値(外れ)。C: 下降予測で下がった(当たり)
    # D: 07-03 に1株を2株に分割(調整係数 0.5)。実株価は 520 ÷ 0.5 = 1040。E: 対象日に取引がない(検証不能)
    # F: 対象日の行がない(上場廃止。検証不能)
    for code, base, target_close, up, rank in [
        ("11110", "1000", "1100", "0.80", 1),
        ("22220", "500", "500", "0.60", 2),
        ("33330", "800", "700", "0.30", None),
        ("44440", "1000", "520", "0.55", None),
        ("55550", "300", None, "0.75", None),
        ("66660", "300", "-", "0.40", None),
    ]:
        _price(conn, P1, code, base)
        if target_close != "-":
            _price(conn, T5, code, target_close)
        _pred(conn, code, P1, 5, T5, up, base, rank=rank)
        _pred(conn, code, P1, 10, T10, up, base, rank=rank)
    _price(conn, date(2026, 7, 3), "44440", "500", factor="0.5")
    _fetched(conn, T5)
    return conn


def _row(conn: psycopg.Connection[DictRow], code: str, horizon: int = 5) -> DictRow:
    r = conn.execute("SELECT * FROM prediction WHERE code = %s AND horizon = %s", (code, horizon)).fetchone()
    assert r is not None
    return r


# =====================================================================
# 検証
# =====================================================================


def test_答え合わせをして結果を書き込む(verify_data: psycopg.Connection[DictRow]) -> None:
    result = verify_core.run_verify(T5)
    assert (result.verified_count, result.unverifiable_count) == (4, 2)
    a = _row(verify_data, "11110")
    assert (a["actual_price"], a["change_rate"], a["is_hit"], a["verify_status"]) == (
        Decimal("1100.0000"),
        Decimal("0.100000"),
        True,
        "VERIFIED",
    )
    assert _row(verify_data, "22220")["is_hit"] is False  # 同じ値は「上がっていない」
    assert _row(verify_data, "33330")["is_hit"] is True
    d = _row(verify_data, "44440")
    assert (d["actual_price"], d["is_hit"]) == (Decimal("1040.0000"), True)
    for code in ("55550", "66660"):
        e = _row(verify_data, code)
        assert (e["verify_status"], e["actual_price"], e["is_hit"]) == ("UNVERIFIABLE", None, None)
    # 10営業日後の予測は、対象日の株価がまだないので更新しない
    assert _row(verify_data, "11110", 10)["verify_status"] == "PENDING"


def test_調整係数が0の日は1として扱いエラーにしない(verify_data: psycopg.Connection[DictRow]) -> None:
    # 07-06 に調整係数 0 の行(誤ったデータ)。ln(0) で検証全体が止まらず、その日は 1 として扱う
    _price(verify_data, date(2026, 7, 6), "11110", "1050", factor="0")
    verify_core.run_verify(T5)
    assert _row(verify_data, "11110")["actual_price"] == Decimal("1100.0000")
    # 分割(0.5)は今までどおり反映する
    assert _row(verify_data, "44440")["actual_price"] == Decimal("1040.0000")


def test_予測日ごとの集計と期間の集計(verify_data: psycopg.Connection[DictRow]) -> None:
    verify_core.run_verify(T5)
    daily = {
        r["segment"]: r for r in verify_data.execute("SELECT * FROM verify_daily_summary WHERE horizon = 5").fetchall()
    }
    assert set(daily) == {"ALL", "TOP", "HIGH_PROB"}
    all_ = daily["ALL"]
    assert (all_["verified_count"], all_["hit_count"], all_["up_count"], all_["unverifiable_count"]) == (4, 3, 2, 2)
    assert all_["target_date"] == T5 and all_["model_version"] == "20260627"
    # 騰落率の合計: 0.1 + 0 − 0.125 + 0.04
    assert all_["sum_change_rate"] == Decimal("0.015000")
    assert (daily["TOP"]["verified_count"], daily["TOP"]["hit_count"]) == (2, 1)
    # 市場の中央値: 検証済みの 0.1、0、−0.125、0.04 の中央値は 0.02
    assert all(r["market_median"] == Decimal("0.020000") for r in daily.values())
    # 上回ったのは A(0.1)と D(0.04)。差の合計は 0.015 − 4 × 0.02
    assert (all_["beat_count"], all_["sum_excess_rate"]) == (2, Decimal("-0.065000"))
    # 買い候補は A(0.1)と B(0)。同じ値は上回っていない扱いなので、B は数えない
    assert (daily["TOP"]["beat_count"], daily["TOP"]["sum_excess_rate"]) == (1, Decimal("0.060000"))
    # 上昇確率 0.7 以上は A(当たり)と E(検証不能)
    assert (daily["HIGH_PROB"]["verified_count"], daily["HIGH_PROB"]["unverifiable_count"]) == (1, 1)

    periods = verify_data.execute(
        "SELECT * FROM verify_period_summary WHERE summary_date = %s AND segment = 'ALL' ORDER BY period_type", (T5,)
    ).fetchall()
    assert [r["period_type"] for r in periods] == ["1M", "3M", "ALL"]
    assert all(r["hit_rate"] == Decimal("0.7500") and r["up_rate"] == Decimal("0.5000") for r in periods)
    assert all(r["beat_rate"] == Decimal("0.5000") and r["avg_excess_rate"] == Decimal("-0.016250") for r in periods)
    top = verify_data.execute(
        "SELECT * FROM verify_period_summary WHERE summary_date = %s AND segment = 'TOP' AND period_type = 'ALL'", (T5,)
    ).fetchone()
    assert top is not None and (top["beat_rate"], top["avg_excess_rate"]) == (Decimal("0.5000"), Decimal("0.030000"))


def test_業種の中央値と買い候補の業種分散の集計(conn: psycopg.Connection[DictRow]) -> None:
    # 予測時の株価は全て 1000。業種 S1: a(+10%)・b(+5%)・c(0%)、S2: d(+20%)・e(0%)、業種なし: f(−10%)
    for code, sector, close, rank, rank_sector in [
        ("a", "S1", "1100", 1, 1),
        ("b", "S1", "1050", 2, None),
        ("c", "S1", "1000", None, None),
        ("d", "S2", "1200", None, 2),
        ("e", "S2", "1000", None, None),
        ("f", None, "900", None, 3),
    ]:
        _price(conn, P1, code, "1000")
        _price(conn, T5, code, close)
        conn.execute(
            "INSERT INTO prediction (code, pred_date, horizon, target_date, up_prob, rel_prob, pred_price, base_price,"
            " model_version, sector33_code, buy_rank, buy_rank_sector)"
            " VALUES (%s, %s, 5, %s, 0.6, 0.5, 1000, 1000, '20260627', %s, %s, %s)",
            (code, P1, T5, sector, rank, rank_sector),
        )
    _fetched(conn, T5)
    verify_core.run_verify(T5)
    daily = {r["segment"]: r for r in conn.execute("SELECT * FROM verify_daily_summary").fetchall()}
    assert set(daily) == {"ALL", "TOP", "TOP_SECTOR"}
    # 業種の中央値: S1 は +5%、S2 は +10%。同じ値(b)は上回っていない扱い。業種なし(f)は業種比から除く
    all_ = daily["ALL"]
    assert (all_["sector_verified_count"], all_["sector_beat_count"], all_["sum_sector_excess_rate"]) == (
        5, 2, Decimal("0.000000")
    )  # fmt: skip
    top = daily["TOP"]
    assert (top["sector_verified_count"], top["sector_beat_count"], top["sum_sector_excess_rate"]) == (
        2, 1, Decimal("0.050000")
    )  # fmt: skip
    # 買い候補(業種分散)は a・d・f。市場の中央値(0% と +5% の間の +2.5%)を上回ったのは a と d
    sec = daily["TOP_SECTOR"]
    assert (sec["verified_count"], sec["beat_count"], sec["market_median"]) == (3, 2, Decimal("0.025000"))
    assert (sec["sector_verified_count"], sec["sector_beat_count"], sec["sum_sector_excess_rate"]) == (
        2, 2, Decimal("0.150000")
    )  # fmt: skip
    periods = {
        r["segment"]: r
        for r in conn.execute(
            "SELECT * FROM verify_period_summary WHERE summary_date = %s AND period_type = 'ALL'", (T5,)
        ).fetchall()
    }
    assert (periods["ALL"]["sector_beat_rate"], periods["ALL"]["avg_sector_excess_rate"]) == (
        Decimal("0.4000"), Decimal("0.000000")
    )  # fmt: skip
    assert (periods["TOP_SECTOR"]["sector_beat_rate"], periods["TOP_SECTOR"]["avg_sector_excess_rate"]) == (
        Decimal("1.0000"), Decimal("0.075000")
    )  # fmt: skip
    assert periods["TOP_SECTOR"]["beat_rate"] == Decimal("0.6667")


def test_業種がない行だけなら業種比は空(conn: psycopg.Connection[DictRow]) -> None:
    _price(conn, P1, "a", "1000")
    _price(conn, T5, "a", "1100")
    _pred(conn, "a", P1, 5, T5, "0.6", "1000", rank=1)
    _fetched(conn, T5)
    verify_core.run_verify(T5)
    row = conn.execute("SELECT * FROM verify_daily_summary WHERE segment = 'TOP'").fetchone()
    assert row is not None and (row["sector_verified_count"], row["sector_beat_count"]) == (0, 0)
    assert row["sum_sector_excess_rate"] == Decimal("0.000000")
    period = conn.execute(
        "SELECT * FROM verify_period_summary WHERE segment = 'TOP' AND period_type = 'ALL'"
    ).fetchone()
    assert period is not None and (period["sector_beat_rate"], period["avg_sector_excess_rate"]) == (None, None)


def test_検証済みの行がない日は市場の中央値が空(conn: psycopg.Connection[DictRow]) -> None:
    _price(conn, P1, "11110", "100")
    _pred(conn, "11110", P1, 5, T5, "0.6", "100")
    _fetched(conn, T5)
    verify_core.run_verify(T5)
    row = conn.execute("SELECT * FROM verify_daily_summary WHERE segment = 'ALL'").fetchone()
    assert row is not None
    assert (row["unverifiable_count"], row["beat_count"], row["sum_excess_rate"], row["market_median"]) == (
        1,
        0,
        Decimal("0.000000"),
        None,
    )


def test_上昇確率の確かさとずれの注意(
    verify_data: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(const, "CALIBRATION_MIN_COUNT", 1)
    result = verify_core.run_verify(T5)
    rows = verify_data.execute(
        "SELECT * FROM verify_calibration_summary WHERE period_type = '3M' ORDER BY prob_band"
    ).fetchall()
    assert [r["prob_band"] for r in rows] == [30, 50, 60, 80]
    band80 = rows[-1]
    assert (band80["verified_count"], band80["up_count"], band80["up_rate"], band80["gap"]) == (
        1,
        1,
        Decimal("1.0000"),
        Decimal("0.2000"),
    )
    assert len(result.calibration_warnings) == 4
    from common.logger import log_file_path

    assert (
        "WARNING 検証 2026-07-08 - 上昇確率がずれています(直近3か月、5日後、80〜90%の帯"
        in log_file_path().read_text(encoding="utf-8")
    )


def test_確率1は90の帯に入る(conn: psycopg.Connection[DictRow]) -> None:
    _price(conn, P1, "11110", "100")
    _price(conn, T5, "11110", "110")
    _pred(conn, "11110", P1, 5, T5, "1.0000", "100")
    _fetched(conn, T5)
    verify_core.run_verify(T5)
    band = conn.execute("SELECT prob_band FROM verify_calibration_summary LIMIT 1").fetchone()
    assert band is not None and band["prob_band"] == 90


def test_株価が収集されていない対象日は更新しない(verify_data: psycopg.Connection[DictRow]) -> None:
    verify_data.execute("DELETE FROM fetched_date")
    result = verify_core.run_verify(T5)
    assert result.verified_count == 0
    assert _row(verify_data, "11110")["verify_status"] == "PENDING"
    status = verify_data.execute("SELECT status FROM run_history").fetchone()
    assert status is not None and status["status"] == "EMPTY"


def test_対象日を過ぎた未検証は後の日付でまとめて拾い検証済みは同じ日付だけやり直す(
    verify_data: psycopg.Connection[DictRow],
) -> None:
    later = date(2026, 7, 20)
    verify_core.run_verify(later)
    assert _row(verify_data, "11110")["verify_status"] == "VERIFIED"
    # 株価を直して、同じ対象日で再実行すると、検証済みでも更新し直す
    verify_data.execute("UPDATE daily_price SET close = 900 WHERE code = '11110' AND trade_date = %s", (T5,))
    verify_core.run_verify(later)
    assert _row(verify_data, "11110")["actual_price"] == Decimal("1100.0000")  # 対象日が違うので更新しない
    verify_core.run_verify(T5)
    assert _row(verify_data, "11110")["actual_price"] == Decimal("900.0000")


def test_期間の始まり() -> None:
    assert verify_core.period_start(date(2026, 3, 31), "1M") == date(2026, 2, 28)
    assert verify_core.period_start(date(2026, 7, 8), "3M") == date(2026, 4, 8)
    assert verify_core.period_start(date(2026, 7, 8), "ALL") is None


def test_verify_pyの終了コード(verify_data: psycopg.Connection[DictRow]) -> None:
    assert verify_cli.main(["--date", "2026-07-08"]) == 0
    assert verify_cli.main(["--date", "bad"]) == 1


# =====================================================================
# 通知
# =====================================================================


class FakeSMTP:
    sent: list[EmailMessage] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def starttls(self) -> None:
        pass

    def login(self, *args: str) -> None:
        pass

    def send_message(self, message: EmailMessage) -> None:
        FakeSMTP.sent.append(message)


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.sent = []
    monkeypatch.setattr(mailer, "smtp_class", FakeSMTP)
    return FakeSMTP


def _bodies(message: EmailMessage) -> tuple[str, str]:
    html_part = message.get_body(("html",))
    text_part = message.get_body(("plain",))
    assert html_part is not None and text_part is not None
    return str(html_part.get_content()), str(text_part.get_content())


@pytest.fixture
def notify_data(conn: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    """2026-07-08 の予測、保有銘柄、的中率。"""
    d = T5
    conn.execute(
        "INSERT INTO stock_master (code, name, market_name, sector33_code, sector33_name, applied_date) VALUES"
        " ('11110', '□□商事', 'プライム', '6050', '卸売業', %s),"
        " ('44440', '△△電機', 'スタンダード', '3650', '電気機器', %s),"
        " ('55550', '○○フーズ', 'グロース', '3050', '食料品', %s),"
        " ('22220', '◇◇製薬', 'プライム', '3250', '医薬品', %s),"
        " ('33330', '☆☆建設', NULL, '2050', '建設業', %s)",
        (d, d, d, d, d),
    )
    _price(conn, d, "11110", "850")
    _price(conn, d, "44440", "2310", upper=True)
    _price(conn, d, "55550", "412", turnover=10_000_000)
    _price(conn, d, "22220", "1480")
    _price(conn, d, "33330", "1920")
    # 買値 1,000円から12%下がった保有銘柄(損切りライン10%を下回る)
    _price(conn, d, "99990", "880")
    # 予測日数ごとの(上昇確率, 市場比の確率)。主となる日数(20日後)の値で保有銘柄を判定する
    # 買い候補(業種分散)は 11110・33330・55550。88880・99990 は業種がない
    for code, rank, rank_sector, sector, probs in [
        ("11110", 1, 1, "6050", (("0.74", "0.58"), ("0.70", "0.60"), ("0.63", "0.72"), ("0.60", "0.66"))),
        ("44440", 2, None, "3650", (("0.72", "0.55"), ("0.66", "0.57"), ("0.61", "0.70"), ("0.58", "0.62"))),
        ("55550", 3, 3, "3050", (("0.71", "0.61"), ("0.69", "0.63"), ("0.58", "0.69"), ("0.57", "0.60"))),
        ("22220", None, None, "3250", (("0.35", "0.40"), ("0.41", "0.42"), ("0.48", "0.38"), ("0.49", "0.45"))),
        ("33330", None, 2, "2050", (("0.62", "0.52"), ("0.60", "0.55"), ("0.57", "0.58"), ("0.55", "0.56"))),
        ("88880", None, None, None, (("0.60", "0.45"), ("0.58", "0.44"), ("0.55", "0.41"), ("0.52", "0.43"))),
        ("99990", None, None, None, (("0.45", "0.60"), ("0.44", "0.58"), ("0.42", "0.57"), ("0.40", "0.55"))),
    ]:
        for h, (up, rel) in zip((5, 10, 20, 60), probs, strict=True):
            _pred(conn, code, d, h, d, up, "850", pred_price="872.4", rank=rank, rel=rel, sector=sector,
                  rank_sector=rank_sector)  # fmt: skip
    conn.execute(
        "INSERT INTO holding (code, buy_price, quantity, buy_date, sell_date) VALUES"
        " ('11110', 800, 100, '2026-06-15', NULL), ('22220', 1500, 100, '2026-06-01', NULL),"
        " ('33330', 2000, 100, '2026-06-01', NULL), ('77770', 500, 100, '2026-06-01', NULL),"
        " ('88880', 2300, 100, '2026-06-01', NULL), ('99990', 1000, 100, '2026-06-01', NULL),"
        " ('11110', 700, 100, '2026-05-01', '2026-06-30'), ('11110', 900, 100, '2026-07-09', NULL)"
    )
    conn.execute(
        "INSERT INTO verify_period_summary (summary_date, period_type, horizon, segment, verified_count, hit_count,"
        " hit_rate, up_rate, beat_rate, avg_excess_rate, sector_beat_rate, avg_sector_excess_rate)"
        " VALUES (%s, '1M', 5, 'ALL', 83380, 45859, 0.55, 0.51, 0.5, 0, 0.48, -0.001),"
        " (%s, '3M', 5, 'TOP', 1260, 743, 0.59, 0.5, 0.725, 0.039, 0.579, 0.0185),"
        " (%s, 'ALL', 5, 'TOP_SECTOR', 1890, 1020, 0.54, 0.543, 0.582, 0.0208, 0.544, 0.016)",
        (d, d, d),
    )
    return conn


def test_通知メールの内容(notify_data: psycopg.Connection[DictRow], smtp: type[FakeSMTP]) -> None:
    subject = notify_core.run_notify(T5)
    assert subject == "【株価予測】買い候補・保有銘柄 2026-07-08"
    message = smtp.sent[0]
    assert message["Subject"] == subject
    html_body, text = _bodies(message)
    assert "2026-07-08(水)" in html_body and "20260627" in html_body
    assert html_body.index("買い候補") < html_body.index("保有銘柄") < html_body.index("的中率(検証結果)")
    # 買い候補(業種分散)は、買い候補の次。業種の列は2つの買い候補の表の、市場の次
    title = "買い候補(業種分散。同じ33業種は3銘柄まで、上位30銘柄)"
    assert html_body.index("買い候補(上位20銘柄)") < html_body.index(title) < html_body.index(">保有銘柄</div>")
    assert html_body.count(">業種</th>") == 2 and ">市場</th><th" in html_body
    sector_table = html_body[html_body.index(title) : html_body.index(">保有銘柄</div>")]
    positions = [sector_table.index(n) for n in ("□□商事", "☆☆建設", "○○フーズ")]
    assert positions == sorted(positions)
    assert "△△電機" not in sector_table and ">建設業</td>" in sector_table
    assert ">卸売業</td>" in html_body and ">電気機器</td>" in html_body
    # 買い候補: コードは4桁、株価はカンマ区切りの整数
    assert ">1111</td>" in html_body and ">□□商事</td>" in html_body
    # 市場の列。銘柄名の次に出す。市場が分からない銘柄は空にする
    assert ">銘柄名</th><th" in html_body and ">市場</th>" in html_body
    assert html_body.count(">市場</th>") == 3  # 2つの買い候補と保有銘柄の表
    assert (
        '>□□商事</td><td style="border:1px solid #cccccc;padding:4px 8px;white-space:nowrap;">プライム</td>'
        in html_body
    )
    assert ">スタンダード</td>" in html_body and ">グロース</td>" in html_body
    assert '>☆☆建設</td><td style="border:1px solid #cccccc;padding:4px 8px;white-space:nowrap;"></td>' in html_body
    assert ">872</td>" in html_body
    # 予測日数ごとの列は「市場比 → 上昇確率 → 予測株価」。主となる日数(20日後)の見出しは薄い緑、数字は太字
    assert (
        html_body.index("5日後<br>市場比") < html_body.index("5日後<br>上昇確率") < html_body.index("5日後<br>予測株価")
    )
    assert html_body.count("background:#d9ead3;") == 9  # 2つの買い候補と保有銘柄の表に3つずつ
    assert 'background:#d9ead3;">20日後<br>市場比</th>' in html_body
    assert 'background:#d9ead3;">5日後' not in html_body
    assert (
        'font-weight:bold;">72%</td><td style="border:1px solid #cccccc;padding:4px 8px;text-align:right;'
        'font-weight:bold;">63%</td>' in html_body
    )
    assert '">58%</td><td style="border:1px solid #cccccc;padding:4px 8px;text-align:right;">74%</td>' in html_body
    assert "市場比の確率が高い順(上昇確率50%以上・見込みプラス)" in html_body
    assert "主となる日数: 20営業日後" in html_body
    assert '<span style="color:#c00000;">ストップ高</span>' in html_body
    assert '<span style="color:#b35900;">流動性注意</span>' in html_body
    # 保有銘柄: 売却済みと、購入日が指定日より後の行は出さない。判定と損益の色
    assert html_body.count(">保持</td>") == 1
    assert 'color:#c00000;font-weight:bold;">売却(下降予測、市場の中央値も下回る予測)</td>' in html_body
    assert 'color:#b35900;font-weight:bold;">保持(注意: 上昇予測だが下降しています)</td>' in html_body
    assert 'color:#b35900;font-weight:bold;">乗り換えを検討(上昇予測だが市場の中央値を下回る予測)</td>' in html_body
    assert 'color:#b35900;font-weight:bold;">保持(注意: 相場全体が下がる予測)</td>' in html_body
    assert "判定なし(本日の予測がありません)" in html_body
    assert 'color:#c00000;">+6.3%</td>' in html_body and 'color:#0055cc;">-1.3%</td>' in html_body
    # 損切りライン。買値から12%下がった 99990 だけ。-4.0% の 33330 には出さない
    assert html_body.count('<span style="color:#c00000;">損切りライン10%の値を下回っています。</span>') == 1
    assert 'color:#0055cc;">-12.0%</td>' in html_body
    # 的中率
    assert "55.0%(83,380件)" in html_body and "59.0%(1,260件)" in html_body
    assert "参考: 実際に上がった銘柄の割合" in html_body and ">51.0%</td>" in html_body
    # 2つの買い候補の下に、市場の中央値・業種の中央値を上回った割合と差(平均)を出す。予測日数ごとに13行
    assert html_body.index("買い候補(上位20銘柄)</td>") < html_body.index("買い候補: 市場の中央値を上回った割合")
    assert ">72.5%</td>" in html_body and ">+3.9%</td>" in html_body
    assert ">57.9%</td>" in html_body and ">+1.9%</td>" in html_body
    order = [
        "買い候補: 業種の中央値との差(平均)",
        "買い候補(業種分散)</td>",
        "買い候補(業種分散): 市場の中央値を上回った割合",
        "買い候補(業種分散): 業種の中央値との差(平均)",
        "上昇確率が高い銘柄(70%以上)</td>",
    ]
    assert [html_body.index(s) for s in order] == sorted(html_body.index(s) for s in order)
    assert ">58.2%</td>" in html_body and ">+1.6%</td>" in html_body
    assert 'rowspan="13"' in html_body
    assert "全銘柄: 業種" not in html_body  # 全銘柄の業種比はメールに出さない
    assert (
        "市場比は、N営業日後までの騰落率が、市場の中央値(対象の全銘柄の騰落率の中央値)を上回る確率です。" in html_body
    )
    assert "注意: 上昇確率" not in html_body
    assert "上昇確率は予測であり、値上がりを保証するものではありません。" in html_body
    # テキストの本文
    assert "1. 1111 □□商事(プライム) 業種 卸売業 現在値 850" in text
    assert "■ 買い候補(業種分散。同じ33業種は3銘柄まで、上位30銘柄)(並べ方は買い候補と同じ)" in text
    assert "2. 3333 ☆☆建設 業種 建設業 現在値 850" in text
    sector_text = text[text.index("■ 買い候補(業種分散") : text.index("■ 保有銘柄")]
    assert "3. 5555 ○○フーズ(グロース) 業種 食料品" in sector_text and "△△電機" not in sector_text
    assert "3333 ☆☆建設 / 買値" in text  # 市場が分からない銘柄は何も付けない
    assert "[ストップ高]" in text and "判定: 売却(下降予測、市場の中央値も下回る予測)" in text
    assert (
        "損益 -12.0%" in text
        and "判定: 保持(注意: 相場全体が下がる予測) [損切りライン10%の値を下回っています。]" in text
    )
    assert "5日後 市場比 58% 上昇 74% 872円、10日後" in text and "★20日後 市場比 72% 上昇 63% 872円" in text
    assert "■ 買い候補(上位20銘柄。主となる日数: 20営業日後、順位付け: 市場比の確率が高い順" in text
    assert "5日後 全銘柄: 55.0%(83,380件) / - / -" in text
    assert "5日後 買い候補: 市場の中央値を上回った割合: - / 72.5% / -" in text
    assert "5日後 買い候補: 市場の中央値との差(平均): - / +3.9% / -" in text
    assert "5日後 買い候補: 業種の中央値を上回った割合: - / 57.9% / -" in text
    assert "5日後 買い候補(業種分散): - / - / 54.0%(1,890件)" in text
    assert "5日後 買い候補(業種分散): 業種の中央値との差(平均): - / - / +1.6%" in text
    history = notify_data.execute("SELECT status FROM run_history").fetchone()
    assert history is not None and history["status"] == "SUCCESS"


def test_買い増しした銘柄は1行にまとめて平均買値で判定する(
    notify_data: psycopg.Connection[DictRow], smtp: type[FakeSMTP]
) -> None:
    # 33330 を 2,000円で100株(fixture)のあと、1,850円で100株買い増した。平均買値は 1,925円、現在値は 1,920円
    notify_data.execute(
        "INSERT INTO holding (code, buy_price, quantity, buy_date, sell_date)"
        " VALUES ('33330', 1850, 100, '2026-06-20', NULL)"
    )
    data = notify_core.load_notify_data(notify_data, T5)
    held = [h for h in data.holdings if h.code == "33330"]
    assert len(held) == 1  # 銘柄ごとに1行
    assert held[0].buy_price == Decimal("1925") and held[0].quantity == 200
    # 買値 2,000円の1行だけなら -4.0% で「保持(注意: …)」だが、平均買値 1,925円では -0.3% なので「保持」
    assert held[0].judgment == "保持"
    notify_core.run_notify(T5)
    html_body, text = _bodies(smtp.sent[0])
    assert html_body.count(">☆☆建設</td>") == 2  # 買い候補(業種分散)と保有銘柄(保有銘柄は1行にまとめる)
    assert ">1,925</td>" in html_body and 'color:#0055cc;">-0.3%</td>' in html_body
    assert "3333 ☆☆建設 / 買値 1,925 現在値 1,920 損益 -0.3%" in text


def test_保有銘柄の現在値はその日以前で一番新しい終値で備考に日付を出す(
    notify_data: psycopg.Connection[DictRow], smtp: type[FakeSMTP]
) -> None:
    # 77770 はその日(07-08)の終値がなく、前の日(07-06)の終値 400 がある → 買値 500 から20%下がっている
    _price(notify_data, date(2026, 7, 6), "77770", "400")
    # 終値が空の日(取引なし)は飛ばして、その前の終値を使う
    _price(notify_data, date(2026, 7, 7), "77770", None)
    data = notify_core.load_notify_data(notify_data, T5)
    held = {h.code: h for h in data.holdings}
    assert held["77770"].current == Decimal("400") and held["77770"].current_date == date(2026, 7, 6)
    assert held["77770"].notes[0] == "07/06 の終値"
    assert any("損切りライン" in n for n in held["77770"].notes)  # 損切りラインも古い終値で見る
    # その日の終値がある銘柄には出さない
    assert "07/08 の終値" not in held["33330"].notes and held["33330"].current_date == T5
    assert notify_core.is_stale_close_note("07/06 の終値") and not notify_core.is_stale_close_note("ストップ高")
    notify_core.run_notify(T5)
    html_body, text = _bodies(smtp.sent[0])
    assert f'<span style="color:{notify_core._ORANGE};">07/06 の終値</span>' in html_body
    assert "07/06 の終値" in text


def test_平均買値() -> None:
    assert notify_core.average_buy_price(Decimal("380000"), 200) == Decimal("1900")
    assert notify_core.average_buy_price(Decimal("0"), 0) == Decimal("0")


def test_補完分とずれの注意と検証結果なし(
    notify_data: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    notify_data.execute("DELETE FROM verify_period_summary")
    notify_data.execute(
        "INSERT INTO verify_calibration_summary (summary_date, period_type, horizon, prob_band, verified_count,"
        " up_count, avg_up_prob, up_rate, gap) VALUES (%s, '3M', 5, 70, 1020, 622, 0.742, 0.61, -0.132)",
        (T5,),
    )
    subject = notify_core.run_notify(T5, is_backfill=True)
    assert subject == "【株価予測】【補完分】買い候補・保有銘柄 2026-07-08"
    html_body, text = _bodies(smtp.sent[0])
    assert "この通知は、取り逃した日の分を後から作成したものです。" in html_body
    assert "検証結果はまだありません" in html_body and "検証結果はまだありません" in text
    assert "注意: 上昇確率が、実際に上がった割合とずれています(直近3か月)。" in html_body
    assert "5日後・70〜80%の帯: 確率の平均 74.2% / 実際に上がった割合 61.0%(1,020件)" in html_body
    assert "5日後・70〜80%の帯" in text


FALLBACK_NOTE: str = "本日は上昇確率50%以上の銘柄が足りないため、上昇確率の上位25%で選びました"


def test_足りない日は買い候補の見出しの下に注記を出す(
    notify_data: psycopg.Connection[DictRow], smtp: type[FakeSMTP]
) -> None:
    """買い候補の中に主となる日数の上昇確率が50%未満の銘柄があれば、置き換えた日とする。"""
    # 11110 は買い候補と買い候補(業種分散)の両方に入っている
    notify_data.execute("UPDATE prediction SET up_prob = 0.4999 WHERE code = '11110' AND horizon = 20")
    notify_core.run_notify(T5)
    html_body, text = _bodies(smtp.sent[0])
    assert html_body.count(FALLBACK_NOTE) == 2 and text.count(FALLBACK_NOTE) == 2
    # 見出しの下に出す
    title = text.index("■ 買い候補(上位20銘柄。")
    assert title < text.index(FALLBACK_NOTE) < text.index(notify_core.candidate_sector_title())
    assert html_body.index(notify_core.candidate_title()) < html_body.index(FALLBACK_NOTE)


STATE_LINE = "相場の状態: 弱い(25日移動平均より上の銘柄 32.1%、直近20営業日の騰落率の中央値 -2.1%)"
STATE_NOTE = "※今の相場の状態を表すもので、この先の上げ下げの予測ではありません"
INDEX_TITLE = "日経平均(2026-07-08)"
TEXT_CAND = "■ 買い候補(上位20銘柄。"  # 文字の本文の買い候補の見出し(行頭)


def test_相場の状態を日経平均のブロックに出す(
    notify_data: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    """HTMLと文字の本文の両方で、対象日の表の下・買い候補の見出しの前の「日経平均」ブロックに出す。色は付けない。"""
    monkeypatch.setattr(notify_core, "load_market_state", lambda conn, d: MarketState("弱い", 0.321, -0.021))
    monkeypatch.setattr(notify_core, "load_index_trend", lambda conn, d: TREND)
    notify_core.run_notify(T5)
    html_body, text = _bodies(smtp.sent[0])
    for body, date_label, title, cand in (
        (html_body, "対象日", INDEX_TITLE, notify_core.candidate_title()),
        (text, "対象日: ", "■ " + INDEX_TITLE, TEXT_CAND),
    ):
        order = [body.index(date_label), body.index(title), body.index(STATE_LINE), body.index(TREND_LINE),
                 body.index(STATE_NOTE), body.index(cand)]  # fmt: skip
        assert order == sorted(order) and len(set(order)) == len(order)
        for s in (title, STATE_LINE, TREND_LINE, STATE_NOTE):
            assert body.count(s) == 1
    assert f'<div style="font-size:13px;margin-bottom:2px;">{STATE_LINE}</div>' in html_body  # 赤・青の色なし
    # 文字の本文: ブロックの後に空行を1つ入れる
    assert f"{STATE_NOTE}\n\n{TEXT_CAND}" in text


def test_買い候補の見出しの下に相場の行を出さない(
    notify_data: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(notify_core, "load_market_state", lambda conn, d: MarketState("弱い", 0.321, -0.021))
    notify_core.run_notify(T5)
    html_body, text = _bodies(smtp.sent[0])
    assert html_body.index(STATE_NOTE) < html_body.index(notify_core.candidate_title())
    # 買い候補の見出しより後(業種分散の見出しの下も含む)には出ない
    assert "相場の状態" not in html_body[html_body.index(notify_core.candidate_title()) :]
    assert "相場の状態" not in text[text.index(TEXT_CAND) :]
    assert "※今の相場の状態" not in text[text.index(TEXT_CAND) :]


def test_日経平均の向きを出さない日はブロックに相場の状態と注意書きだけ(
    notify_data: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(notify_core, "load_market_state", lambda conn, d: MarketState("弱い", 0.321, -0.021))
    notify_core.run_notify(T5)
    _, text = _bodies(smtp.sent[0])
    assert f"■ {INDEX_TITLE}\n{STATE_LINE}\n{STATE_NOTE}\n\n■ " in text


def test_補完分でもブロックは補完分の説明の後_対象日の表の下に出す(
    notify_data: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(notify_core, "load_market_state", lambda conn, d: MarketState("弱い", 0.321, -0.021))
    notify_core.run_notify(T5, is_backfill=True)
    html_body, text = _bodies(smtp.sent[0])
    for body, title in ((html_body, INDEX_TITLE), (text, "■ " + INDEX_TITLE)):
        assert body.index("取り逃した日の分") < body.index("予測した銘柄数") < body.index(title)


def test_置き換えの注記は買い候補の見出しの下に残る(
    notify_data: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(notify_core, "load_market_state", lambda conn, d: MarketState("弱い", 0.321, -0.021))
    notify_data.execute("UPDATE prediction SET up_prob = 0.4999 WHERE code = '11110' AND horizon = 20")
    notify_core.run_notify(T5)
    html_body, text = _bodies(smtp.sent[0])
    assert html_body.index(STATE_NOTE) < html_body.index(notify_core.candidate_title()) < html_body.index(FALLBACK_NOTE)
    assert text.index(STATE_NOTE) < text.index(TEXT_CAND) < text.index(FALLBACK_NOTE)


TREND = IndexTrend(
    (IndexTrendItem(10, "上向き", 0.0021), IndexTrendItem(20, "横ばい", 0.0003), IndexTrendItem(60, "下向き", -0.0008))
)
TREND_LINE = "日経平均の向き: 10日 上向き(+0.21%/日)/20日 横ばい(+0.03%/日)/60日 下向き(-0.08%/日)"


@pytest.mark.parametrize("is_backfill", [False, True])
def test_日経平均の向きを相場の状態の次の行に出す(
    notify_data: psycopg.Connection[DictRow],
    smtp: type[FakeSMTP],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    is_backfill: bool,
) -> None:
    """相場の状態の行の次、注意書きの前に1行。買い候補(業種分散)の上には出さない。色は付けない。補完分も同じ。"""
    monkeypatch.setattr(notify_core, "load_market_state", lambda conn, d: MarketState("弱い", 0.321, -0.021))
    monkeypatch.setattr(notify_core, "load_index_trend", lambda conn, d: TREND)
    text_path = tmp_path / "全処理_通知.txt"
    notify_core.run_notify(T5, is_backfill, text_path=text_path)
    html_body, text = _bodies(smtp.sent[0])
    line = "相場の状態: 弱い(25日移動平均より上の銘柄 32.1%、直近20営業日の騰落率の中央値 -2.1%)"
    note = "※今の相場の状態を表すもので、この先の上げ下げの予測ではありません"
    assert html_body.index(line) < html_body.index(TREND_LINE) < html_body.index(note)
    assert html_body.count(TREND_LINE) == 1
    assert f'<div style="font-size:13px;margin-bottom:2px;">{TREND_LINE}</div>' in html_body  # 色なし
    lines = text.splitlines()
    assert lines[lines.index(line) + 1] == TREND_LINE and lines[lines.index(line) + 2] == note
    assert text.count(TREND_LINE) == 1
    assert text.index(TREND_LINE) < text.index(notify_core.candidate_sector_title())
    # 通知ファイル(全処理の通知ファイルと同じ書き方)にも出る
    assert TREND_LINE in text_path.read_text(encoding="utf-8")


def test_日経平均の向きを出さない日は行ごと出さない(
    notify_data: psycopg.Connection[DictRow], smtp: type[FakeSMTP]
) -> None:
    notify_core.run_notify(T5)
    html_body, text = _bodies(smtp.sent[0])
    assert "日経平均の向き" not in html_body and "日経平均の向き" not in text


def test_相場の状態が計算できない日は横線にする(notify_data: psycopg.Connection[DictRow], smtp: type[FakeSMTP]) -> None:
    notify_core.run_notify(T5)
    html_body, text = _bodies(smtp.sent[0])
    assert "相場の状態: -" in html_body and "相場の状態: -" in text


def test_注記は足りない方の買い候補にだけ出す(notify_data: psycopg.Connection[DictRow], smtp: type[FakeSMTP]) -> None:
    # 33330 は買い候補(業種分散)にだけ入っている
    notify_data.execute("UPDATE prediction SET up_prob = 0.45 WHERE code = '33330' AND horizon = 20")
    notify_core.run_notify(T5)
    _, text = _bodies(smtp.sent[0])
    assert text.count(FALLBACK_NOTE) == 1
    assert text.index(notify_core.candidate_sector_title()) < text.index(FALLBACK_NOTE)


def test_注記の割合は設定値から作る(
    notify_data: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(const, "BUY_CANDIDATE_FALLBACK_TOP_RATE", 0.125)
    notify_data.execute("UPDATE prediction SET up_prob = 0.45 WHERE code = '11110' AND horizon = 20")
    notify_core.run_notify(T5)
    assert "上昇確率の上位12.5%で選びました" in _bodies(smtp.sent[0])[1]


def test_主となる日数以外の50パーセント未満や上昇確率の基準を満たす日は注記を出さない(
    notify_data: psycopg.Connection[DictRow], smtp: type[FakeSMTP]
) -> None:
    notify_data.execute("UPDATE prediction SET up_prob = 0.40 WHERE code = '11110' AND horizon = 5")
    notify_data.execute("UPDATE prediction SET up_prob = 0.50 WHERE code = '11110' AND horizon = 20")
    notify_core.run_notify(T5)
    html_body, text = _bodies(smtp.sent[0])
    assert FALLBACK_NOTE not in html_body and FALLBACK_NOTE not in text


def test_買い候補と保有銘柄がない場合(conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP]) -> None:
    _pred(conn, "11110", T5, 5, T5, "0.4", "100")
    notify_core.run_notify(T5)
    html_body, text = _bodies(smtp.sent[0])
    assert "買い候補はありません" in html_body and "保有銘柄はありません" in html_body
    # 0銘柄でも表の見出しは出し、1行目を全ての列(6 + 4日数 × 3 + 1 = 19列)で連結する。2つの買い候補の表の両方
    assert html_body.count('<tr><td colspan="19"') == 2 and "20日後<br>市場比</th>" in html_body
    assert text.count("買い候補はありません") == 2 and "保有銘柄はありません" in text


def test_send_mailがFalseならメールを送らずログに残す(conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP]) -> None:
    _pred(conn, "11110", T5, 5, T5, "0.4", "100")
    subject = notify_core.run_notify(T5, send_mail=False)
    assert subject == "【株価予測】買い候補・保有銘柄 2026-07-08"
    assert smtp.sent == []
    assert "メールは送りませんでした(【株価予測】買い候補・保有銘柄 2026-07-08" in log_file_path().read_text(
        encoding="utf-8"
    )


def test_text_pathを渡すとテキストの本文をファイルに追記する(
    conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP], tmp_path: Path
) -> None:
    # range_date_full_process.py の通知の内容のファイル
    _pred(conn, "11110", T5, 5, T5, "0.4", "100")
    path = tmp_path / "全処理_通知_2026-07-08_2026-07-08.txt"
    notify_core.run_notify(T5, send_mail=False, text_path=path)
    notify_core.run_notify(T5, send_mail=False, text_path=path)
    text = path.read_text(encoding="utf-8")
    assert smtp.sent == []
    # メールのテキスト形式の本文と同じ形で、日付順に追記する
    assert text.count("件名: 【株価予測】買い候補・保有銘柄 2026-07-08") == 2
    assert text.count("対象日: 2026-07-08") == 2
    assert text.startswith(notify_core.TEXT_FILE_SEPARATOR + "\n件名: ")


def test_指定日の予測がない場合はメールを送らずに失敗(conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP]) -> None:
    with pytest.raises(BatchError, match=r"指定日の予測がありません\(指定日: 2026-07-08\)"):
        notify_core.run_notify(T5)
    assert smtp.sent == []


def test_クロスの備考の文言() -> None:
    notes = notify_core.cross_notes
    # 直近5営業日以内(1〜5)だけ。プラスはゴールデンクロス、マイナスはデッドクロス
    # 値 1 がクロスした日(本日)、値 N は N-1 営業日前
    assert notes(1.0, None) == ["短期ゴールデンクロス発生(本日)"]
    assert notes(-5.0, 3.0) == ["短期デッドクロス発生(4営業日前)", "中長期ゴールデンクロス発生(2営業日前)"]
    assert notes(6.0, -6.0) == []
    assert notes(float("nan"), -2.0) == ["中長期デッドクロス発生(1営業日前)"]
    assert notes(-1.0, 4.0) == ["短期デッドクロス発生(本日)", "中長期ゴールデンクロス発生(3営業日前)"]
    assert notes(float(const.CROSS_DAYS_CAP), -float(const.CROSS_DAYS_CAP)) == []


def test_買い候補と保有銘柄の備考にクロスを出す(
    notify_data: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake(conn: psycopg.Connection[DictRow], target: date, codes: list[str], cap: int) -> pd.DataFrame:
        calls.append(codes)
        assert (target, cap) == (T5, const.CROSS_DAYS_CAP)
        return pd.DataFrame(
            {
                "code": ["11110", "44440", "22220"],
                "days_since_cross_5_25": [2.0, -1.0, 7.0],
                "days_since_cross_25_75": [-20.0, 5.0, -3.0],
            }
        )

    monkeypatch.setattr(notify_core, "cross_days_on", fake)
    notify_core.run_notify(T5)
    # 買い候補と保有銘柄の銘柄コードをまとめて1回だけ計算する
    assert calls == [["11110", "22220", "33330", "44440", "55550", "77770", "88880", "99990"]]
    html_body, text = _bodies(smtp.sent[0])
    # ゴールデンクロスは赤、デッドクロスは青
    assert '<span style="color:#c00000;">短期ゴールデンクロス発生(1営業日前)</span>' in html_body
    assert '<span style="color:#0055cc;">短期デッドクロス発生(本日)</span>' in html_body
    assert '<span style="color:#c00000;">中長期ゴールデンクロス発生(4営業日前)</span>' in html_body
    assert '<span style="color:#0055cc;">中長期デッドクロス発生(2営業日前)</span>' in html_body
    # 保有銘柄の表の最後に備考の列がある。11110 は2つの買い候補と保有銘柄にあるので3回、22220 は保有銘柄だけ
    assert '判定</th><th style="border:1px solid #b8c4d6;padding:4px 8px;">備考</th></tr>' in html_body
    assert html_body.count("短期ゴールデンクロス発生") == 3
    assert html_body.count("中長期デッドクロス発生") == 1
    assert "[ストップ高／短期デッドクロス発生(本日)／中長期ゴールデンクロス発生(4営業日前)]" in text
    assert "判定: 売却(下降予測、市場の中央値も下回る予測) [中長期デッドクロス発生(2営業日前)]" in text


def test_割安さと稼ぐ力の備考の文言(monkeypatch: pytest.MonkeyPatch) -> None:
    notes = notify_core.valuation_notes
    assert notify_core.pbr_note() == "PBR0.5倍割れ(割安)"
    assert notify_core.roe_note() == "ROE8%以上(稼ぐ力が高い)"
    # PBR は 0.5 未満(ちょうどは含まない)、予想PER は業種の中央値の 0.7倍以下(ちょうどを含む)、
    # ROE は 8%以上
    assert notes(0.49, 7.0, 10.0, 0.08, 10.0) == [
        "PBR0.5倍割れ(割安)",
        "予想PERが業種平均より低い(割安)",
        "ROE8%以上(稼ぐ力が高い)",
    ]
    assert notes(0.5, 7.01, 10.0, 0.0799, 10.0) == []
    # 赤字予想は青の備考。予想PER がマイナスでも、赤字予想なら「割安」は出さない
    assert notes(1.2, -5.0, -3.0, -0.1, 10.0) == ["赤字予想(業績注意)"]
    assert notes(1.2, 3.0, -3.0, None, 10.0) == ["赤字予想(業績注意)"]
    # 値がない項目(None、NaN)の備考は出さない。PBR が 0 以下は出さない
    assert notes(None, None, None, None, None) == []
    assert notes(float("nan"), 5.0, 10.0, float("nan"), None) == []
    assert notes(0.0, 5.0, 10.0, None, float("nan")) == []
    # 閾値が 0 なら、その備考は出さない
    monkeypatch.setattr(const, "NOTE_PBR_MAX", 0.0)
    monkeypatch.setattr(const, "NOTE_PER_SECTOR_RATIO", 0.0)
    monkeypatch.setattr(const, "NOTE_ROE_MIN_PERCENT", 0)
    assert notes(0.3, 1.0, 10.0, 0.5, 10.0) == []


def test_買い候補と保有銘柄の備考に割安さと稼ぐ力を出す(
    notify_data: psycopg.Connection[DictRow], smtp: type[FakeSMTP]
) -> None:
    conn = notify_data
    conn.execute("UPDATE stock_master SET sector33_code = '3050', market_code = '0111'")
    # 業種の予想PER の中央値は、プラスの 5・15・20 の中央値 15(0.7倍 = 10.5)
    conn.execute(
        "INSERT INTO daily_valuation (trade_date, code, fwd_eps, fwd_per, pbr, roe) VALUES"
        " (%s, '11110', 100, 5, 0.4, 0.12), (%s, '44440', 50, 20, 1.5, 0.05),"
        " (%s, '55550', 10, 15, 0.9, 0.08), (%s, '22220', -30, -10, 2.0, -0.1)",
        (T5, T5, T5, T5),
    )
    notify_core.run_notify(T5)
    html_body, text = _bodies(smtp.sent[0])
    # 割安・稼ぐ力は赤、赤字予想は青。11110 は2つの買い候補と保有銘柄にあるので3回
    assert '<span style="color:#c00000;">PBR0.5倍割れ(割安)</span>' in html_body
    assert '<span style="color:#c00000;">予想PERが業種平均より低い(割安)</span>' in html_body
    assert '<span style="color:#0055cc;">赤字予想(業績注意)</span>' in html_body
    assert html_body.count("PBR0.5倍割れ(割安)") == 3
    # 11110 が3回、55550(ちょうど8%。2つの買い候補にある)が2回
    assert html_body.count("ROE8%以上(稼ぐ力が高い)") == 5
    assert "[ストップ高]" in text  # 44440 には割安さ・稼ぐ力の備考は付かない
    assert "判定: 売却(下降予測、市場の中央値も下回る予測) [赤字予想(業績注意)]" in text


def test_損切りラインの判定() -> None:
    hits = notify_core.hits_stop_loss
    assert const.STOP_LOSS_PERCENT == 10
    # 買値から10%以上下がったら(ちょうど10%も含む)表示する
    assert hits(Decimal(2000), Decimal(1800)) is True
    assert hits(Decimal(2000), Decimal("1800.01")) is False
    assert hits(Decimal(2000), Decimal(1500)) is True
    assert hits(Decimal(2000), None) is False
    assert hits(Decimal(0), Decimal(100)) is False
    assert notify_core.stop_loss_note() == "損切りライン10%の値を下回っています。"


def test_損切りラインは0なら表示しない(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(const, "STOP_LOSS_PERCENT", 0)
    assert notify_core.hits_stop_loss(Decimal(2000), Decimal(100)) is False


def test_判定と書式() -> None:
    judge = notify_core.judge_holding
    half, low = Decimal("0.5"), Decimal("0.4999")
    assert judge(None, None, Decimal(800), Decimal(850)) == "判定なし(本日の予測がありません)"
    assert judge(half, None, Decimal(800), Decimal(850)) == "判定なし(本日の予測がありません)"
    # 市場比の確率と上昇確率の組み合わせ
    assert judge(low, low, Decimal(800), Decimal(850)) == "売却(下降予測、市場の中央値も下回る予測)"
    assert judge(low, half, Decimal(800), Decimal(850)) == "乗り換えを検討(上昇予測だが市場の中央値を下回る予測)"
    assert judge(half, low, Decimal(800), Decimal(700)) == "保持(注意: 相場全体が下がる予測)"
    assert judge(half, half, Decimal(1000), Decimal(970)) == "保持(注意: 上昇予測だが下降しています)"
    assert judge(half, half, Decimal(1000), Decimal("970.01")) == "保持"
    assert judge(half, half, Decimal(1000), None) == "保持"
    assert notify_core.fmt_price(Decimal("2364.5")) == "2,365"
    assert notify_core.fmt_price(None) == "-"
    assert notify_core.fmt_prob(Decimal("0.745")) == "75%"
    assert notify_core.fmt_rate(Decimal("0.0625"), sign=True) == "+6.3%"
    assert notify_core.fmt_rate(Decimal("-0.04"), sign=True) == "-4.0%"
    assert notify_core.fmt_rate(None) == "-"


def test_notify_pyの終了コード(notify_data: psycopg.Connection[DictRow], smtp: type[FakeSMTP]) -> None:
    assert notify_cli.main(["--date", "2026-07-08"]) == 0
    assert "【補完分】" not in str(smtp.sent[0]["Subject"])
    assert notify_cli.main(["--date", "2026-07-09"]) == 1
    assert notify_cli.main(["--from", "2026-07-09"]) == 1


def test_買い候補と保有銘柄の備考にトレンドラインの抜けを出す(
    notify_data: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    up = "上値抵抗線(接触3回)を上抜け(下降から転換・2営業日前)"
    down = "下値支持線(接触4回)を下抜け(上昇から転換・本日)"

    def fake(conn: psycopg.Connection[DictRow], target: date, codes: list[str]) -> dict[str, list[str]]:
        calls.append(codes)
        assert target == T5
        return {"11110": [up], "22220": [down]}

    monkeypatch.setattr(notify_core, "trendline_notes_on", fake)
    notify_core.run_notify(T5)
    # 買い候補と保有銘柄の銘柄コードをまとめて1回だけ計算する
    assert calls == [["11110", "22220", "33330", "44440", "55550", "77770", "88880", "99990"]]
    html_body, text = _bodies(smtp.sent[0])
    # 上抜けは赤、下抜けは青
    assert f'<span style="color:#c00000;">{up}</span>' in html_body
    assert f'<span style="color:#0055cc;">{down}</span>' in html_body
    # 11110 は2つの買い候補と保有銘柄にあるので3回
    assert html_body.count(up) == 3 and html_body.count(down) == 1
    assert up in text and down in text


def test_青で表示する備考() -> None:
    assert notify_core.is_blue_note(notify_core.NOTE_DEAD_SHORT)
    assert notify_core.is_blue_note("短期デッドクロス発生(本日)")
    assert notify_core.is_blue_note("中長期デッドクロス発生(3営業日前)")
    assert not notify_core.is_blue_note("短期ゴールデンクロス発生(本日)")
    assert not notify_core.is_blue_note("中長期ゴールデンクロス発生(3営業日前)")
    assert notify_core.is_blue_note("下値支持線(接触3回)を下抜け")
    assert notify_core.is_blue_note("下値支持線(接触3回)を下抜け(下降が加速・本日)")
    assert notify_core.is_blue_note("下値支持線(接触3回)を下抜け(1営業日前)")
    assert not notify_core.is_blue_note("上値抵抗線(接触3回)を上抜け(上昇が加速)")
    assert not notify_core.is_blue_note("上値抵抗線(接触3回)を上抜け(上昇が加速・2営業日前)")
    assert notify_core.is_blue_note(notify_core.NOTE_DEFICIT)
    assert not notify_core.is_blue_note(notify_core.NOTE_LOW_LIQUIDITY)


# =====================================================================
# 宛先とファイル
# =====================================================================


def test_通知は日ごとのファイルに残しMAIL_TOに送る(
    conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAIL_TO", "to@example.com")
    monkeypatch.setenv("ADMIN_MAIL_TO", "admin@example.com")
    _pred(conn, "11110", T5, 5, T5, "0.4", "100")
    subject = notify_core.run_notify(T5, is_backfill=True)
    assert [m["To"] for m in smtp.sent] == ["to@example.com"]
    text = mailer.notify_file_path(T5).read_text(encoding="utf-8")
    assert f"件名: {subject}" in text and "対象日: 2026-07-08" in text


def test_send_mailがFalseなら日ごとのファイルを作らない(
    conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP]
) -> None:
    _pred(conn, "11110", T5, 5, T5, "0.4", "100")
    notify_core.run_notify(T5, send_mail=False)
    assert not mailer.notify_file_path(T5).exists()


def test_単独の通知は一部の宛先に送れなければ失敗(
    conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    _pred(conn, "11110", T5, 5, T5, "0.4", "100")

    def partial(*args: Any, **kwargs: Any) -> None:
        raise MailSendError(["ok@example.com"], {"ng@example.com": "x"})

    monkeypatch.setattr(mailer, "send_mail", partial)
    with pytest.raises(BatchError):
        notify_core.run_notify(T5)
    # 日次バッチは警告にする
    warnings = BatchWarnings()
    subject = notify_core.run_notify(T5, warnings=warnings)
    assert len(warnings.items) == 1 and subject in warnings.items[0].message
