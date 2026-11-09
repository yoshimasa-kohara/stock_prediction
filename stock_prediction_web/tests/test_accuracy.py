"""的中率の API(/api/accuracy、/api/accuracy/daily、/api/accuracy/rank)のテスト。"""

from datetime import date, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow
from web_sample import LAST_DAY, add_holding, pred_days

import const
from common import business_day
from core.verify_core import period_start


def test_accuracy_latest(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """期間ごとの区分の集計と、上昇確率の確かさ(ずれの大きい帯に warn)、メールと同じ注意。"""
    body = client.get("/api/accuracy").json()
    assert body["summary_date"] == LAST_DAY.isoformat()
    assert body["summary_dates"] == [LAST_DAY.isoformat()]
    assert body["horizon"] == const.MAIN_HORIZON
    assert [p["period"] for p in body["periods"]] == const.VERIFY_SUMMARY_PERIODS
    one = body["periods"][0]
    assert one["all"]["hit_rate"] == 0.538
    assert one["all"]["verified_count"] == 1000
    assert one["top"]["beat_rate"] == 0.785
    assert one["top"]["avg_excess_rate"] == 0.059
    assert one["high_prob"]["hit_rate"] == 0.7
    # 買い候補(業種分散)と業種比
    assert one["top_sector"]["hit_rate"] == 0.6 and one["top_sector"]["verified_count"] == 90
    assert one["top"]["sector_beat_rate"] == 0.579 and one["top"]["avg_sector_excess_rate"] == 0.0185
    # 保有銘柄がない期間は null(画面は「この期間に保有していた銘柄はありません」と出す)
    assert one["holding"] is None
    bands = body["calibration"][const.PERIOD_3M]
    assert [(b["prob_band"], b["warn"]) for b in bands] == [(50, False), (60, True)]
    assert set(body["calibration"]) == {const.PERIOD_3M, const.PERIOD_ALL}
    assert [(w["horizon"], w["prob_band"]) for w in body["warnings"]] == [(const.MAIN_HORIZON, 60)]


def test_accuracy_no_summary(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """指定した日より前に集計がなければ、summary_date は null。"""
    body = client.get("/api/accuracy?date=2026-01-01").json()
    assert body["summary_date"] is None
    assert body["periods"] == []


def test_accuracy_bad_horizon(client: TestClient) -> None:
    res = client.get("/api/accuracy?horizon=3")
    assert res.status_code == 400
    assert res.json() == {"detail": "予測日数は 5・10・20・60 のどれかを指定してください"}


def test_accuracy_holding(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """保有銘柄の集計: 予測日に保有していた銘柄の、検証済みの予測を数える(買い増しの行は1回だけ)。"""
    first = pred_days()[0]
    h0 = const.PREDICT_HORIZONS[0]
    # 11110 を2回に分けて保有(同じ予測は1回だけ数える)、11120 は予測日の後に購入(数えない)
    add_holding(sample, "11110", "1000", 100, date(2026, 6, 1))
    add_holding(sample, "11110", "1100", 100, date(2026, 6, 2))
    add_holding(sample, "11120", "1000", 100, first + timedelta(days=1))
    add_holding(sample, "11130", "1000", 100, date(2026, 6, 1), first, "1100")  # 予測日に売却(数えない)
    # 答え合わせの日(対象日)を集計の基準日にする
    target = business_day.business_days_after(first, h0)
    sample.execute(
        "INSERT INTO verify_period_summary (summary_date, period_type, horizon, segment, verified_count, hit_count)"
        " VALUES (%s, '1M', %s, 'ALL', 5, 3)",
        (target, h0),
    )
    body = client.get(f"/api/accuracy?horizon={h0}&date={target.isoformat()}").json()
    assert body["summary_date"] == target.isoformat()
    holding = body["periods"][0]["holding"]
    # 11110(予測日 first、予測日数 5)だけ。11110 は 1.05 倍に上がった(市場の中央値 1% を上回る)
    row = sample.execute(
        "SELECT is_hit, change_rate FROM prediction WHERE code = '11110' AND pred_date = %s AND horizon = %s",
        (first, h0),
    ).fetchone()
    assert row is not None
    assert holding["verified_count"] == 1
    assert holding["hit_rate"] == (1.0 if row["is_hit"] else 0.0)
    assert holding["beat_rate"] == 1.0
    assert holding["avg_excess_rate"] == pytest.approx(float(row["change_rate"]) - 0.01)
    # 保有銘柄の業種比は出さない
    assert holding["sector_beat_rate"] is None and holding["avg_sector_excess_rate"] is None


def test_accuracy_daily(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """予測日ごとの、買い候補の市場の中央値との差の平均と、全銘柄の的中率。"""
    h0 = const.PREDICT_HORIZONS[0]
    body = client.get(f"/api/accuracy/daily?horizon={h0}").json()
    assert body["date_to"] == pred_days()[0].isoformat()
    assert body["points"] == [
        {"pred_date": pred_days()[0].isoformat(), "top_excess": pytest.approx(0.03), "all_hit_rate": 0.6}
    ]
    empty = client.get(f"/api/accuracy/daily?horizon={const.MAIN_HORIZON}&from=2026-07-01&to=2026-07-22").json()
    assert empty["points"] == []


def test_accuracy_daily_no_data(client: TestClient) -> None:
    body = client.get("/api/accuracy/daily").json()
    assert body["points"] == []
    assert client.get("/api/accuracy/daily?from=2026-07-02&to=2026-07-01").status_code == 400


# =====================================================================
# 買い候補 順位別 的中率(/api/accuracy/rank)
# =====================================================================

RANK_DAY = date(2026, 8, 31)  # 集計の基準日(月末。6か月前が 2月28日になる)


class _Pred:
    """順位別の的中率のテスト用に、prediction を1行ずつ入れる(銘柄コードは行ごとに変える)。"""

    def __init__(self, conn: psycopg.Connection[DictRow]) -> None:
        self.conn = conn
        self.n = 0

    def add(
        self,
        target: date,
        buy_rank: int | None,
        change_rate: str | None,
        *,
        horizon: int = const.PREDICT_HORIZONS[0],
        status: str = const.VERIFY_STATUS_VERIFIED,
        buy_rank_sector: int | None = None,
    ) -> None:
        self.n += 1
        self.conn.execute(
            """
            INSERT INTO prediction (code, pred_date, horizon, target_date, up_prob, rel_prob, pred_price, base_price,
                                    model_version, buy_rank, buy_rank_sector, change_rate, verify_status)
            VALUES (%s, %s, %s, %s, 0.6, 0.6, 100, 100, 'v1', %s, %s, %s, %s)
            """,
            (f"{90000 + self.n}", target - timedelta(days=7), horizon, target, buy_rank, buy_rank_sector,
             change_rate, status),
        )  # fmt: skip


def _summary_day(conn: psycopg.Connection[DictRow], d: date) -> None:
    """集計の基準日にする日(verify_period_summary の行)。"""
    conn.execute(
        """
        INSERT INTO verify_period_summary (summary_date, period_type, horizon, segment, verified_count, hit_count)
        VALUES (%s, %s, %s, %s, 0, 0)
        """,
        (d, const.PERIOD_ALL, const.MAIN_HORIZON, const.SEGMENT_ALL),
    )


def _counts(body: dict[str, Any], period: str, horizon: int) -> list[tuple[int, int]]:
    p = next(x for x in body["periods"] if x["period"] == period)
    row = next(r for r in p["rows"] if r["horizon"] == horizon)
    return [(c["verified_count"], c["hit_count"]) for c in row["counts"]]


def test_accuracy_rank_counts(client: TestClient, conn: psycopg.Connection[DictRow]) -> None:
    """帯ごとの数え方: 騰落率 > 0 が的中(0% は外れ)。

    検証済み以外・21位以下・業種分散だけの行・基準日より後は数えない。
    """
    _summary_day(conn, RANK_DAY)
    p = _Pred(conn)
    p.add(RANK_DAY, 1, "0.05")  # 的中
    p.add(RANK_DAY, 3, "0")  # 0% は外れ
    p.add(RANK_DAY, 7, "-0.01")  # 外れ
    p.add(RANK_DAY, 12, "0.02")
    p.add(RANK_DAY, 18, "0.03")
    p.add(RANK_DAY, 20, "0.01")
    p.add(RANK_DAY, 21, "0.01")  # 21位は数えない
    p.add(RANK_DAY, 2, None, status=const.VERIFY_STATUS_PENDING)  # 検証待ち
    p.add(RANK_DAY, 4, "0.1", status=const.VERIFY_STATUS_UNVERIFIABLE)  # 検証できない
    p.add(RANK_DAY, None, "0.1", buy_rank_sector=1)  # 業種分散の順位だけ
    p.add(RANK_DAY, 2, None)  # 騰落率が NULL
    p.add(RANK_DAY + timedelta(days=1), 1, "0.1")  # 基準日より後
    p.add(RANK_DAY, 16, "-0.02", horizon=60)
    body = client.get("/api/accuracy/rank").json()
    assert body["summary_date"] == RANK_DAY.isoformat()
    assert body["default_period"] == "1M"
    assert [(b["rank_from"], b["rank_to"]) for b in body["bands"]] == [(1, 20), (1, 5), (6, 10), (11, 15), (16, 20)]
    assert [(b["rank_from"], b["rank_to"]) for b in body["diff_bands"]] == [(1, 5), (16, 20)]
    assert [x["period"] for x in body["periods"]] == ["1M", "3M", "6M", "1Y", "2Y", "ALL"]
    # 全ての期間で、予測日数は PREDICT_HORIZONS の順に4行
    for x in body["periods"]:
        assert [r["horizon"] for r in x["rows"]] == const.PREDICT_HORIZONS
    h5 = const.PREDICT_HORIZONS[0]
    assert _counts(body, "ALL", h5) == [(6, 4), (2, 1), (1, 0), (1, 1), (2, 2)]
    assert _counts(body, "1M", h5) == [(6, 4), (2, 1), (1, 0), (1, 1), (2, 2)]
    assert _counts(body, "ALL", 60) == [(1, 0), (0, 0), (0, 0), (0, 0), (1, 0)]
    # 件数0の予測日数
    assert _counts(body, "ALL", 10) == [(0, 0)] * 5


def test_accuracy_rank_period_boundaries(client: TestClient, conn: psycopg.Connection[DictRow]) -> None:
    """期間の境界: 期間の最初の日(n か月前の同じ日。ない日は月末)は入らず、その翌日から入る(バッチの 1M・3M と同じ)。"""
    _summary_day(conn, RANK_DAY)
    starts = {
        "1M": date(2026, 7, 31),
        "3M": date(2026, 5, 31),
        "6M": date(2026, 2, 28),
        "1Y": date(2025, 8, 31),
        "2Y": date(2024, 8, 31),
    }
    # 1M・3M はバッチの verify_period_summary と同じ決め方
    assert period_start(RANK_DAY, const.PERIOD_1M) == starts["1M"]
    assert period_start(RANK_DAY, const.PERIOD_3M) == starts["3M"]
    p = _Pred(conn)
    p.add(RANK_DAY, 1, "0.1")
    for s in starts.values():
        p.add(s, 1, "0.1")  # 境界の日(この期間には入らない)
        p.add(s + timedelta(days=1), 1, "-0.1")  # 境界の翌日(入る)
    body = client.get("/api/accuracy/rank").json()
    got = {
        x["period"]: (x["period_start"], _counts(body, x["period"], const.PREDICT_HORIZONS[0])[0])
        for x in body["periods"]
    }
    assert got == {
        "1M": ("2026-07-31", (2, 1)),
        "3M": ("2026-05-31", (4, 2)),
        "6M": ("2026-02-28", (6, 3)),
        "1Y": ("2025-08-31", (8, 4)),
        "2Y": ("2024-08-31", (10, 5)),
        "ALL": (None, (11, 6)),
    }


def test_accuracy_rank_date(client: TestClient, conn: psycopg.Connection[DictRow]) -> None:
    """集計の基準日は /api/accuracy と同じ決め方(指定した日以前で一番新しい日)。集計がなければ periods は空。"""
    _summary_day(conn, RANK_DAY)
    _summary_day(conn, RANK_DAY - timedelta(days=10))
    p = _Pred(conn)
    p.add(RANK_DAY, 1, "0.1")
    p.add(RANK_DAY - timedelta(days=10), 1, "0.1")
    body = client.get(f"/api/accuracy/rank?date={(RANK_DAY - timedelta(days=1)).isoformat()}").json()
    assert body["summary_date"] == (RANK_DAY - timedelta(days=10)).isoformat()
    assert _counts(body, "ALL", const.PREDICT_HORIZONS[0])[0] == (1, 1)
    none = client.get("/api/accuracy/rank?date=2020-01-01").json()
    assert none["summary_date"] is None
    assert none["periods"] == []
