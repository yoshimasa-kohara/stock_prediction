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


# =====================================================================
# 銘柄別・業種別の的中率(/api/accuracy/stocks・/api/accuracy/sectors)
# =====================================================================

H5 = const.PREDICT_HORIZONS[0]


class _Breakdown:
    """銘柄別・業種別のテスト用に、検証済みの予測と、市場の中央値(verify_daily_summary)を入れる。"""

    def __init__(self, conn: psycopg.Connection[DictRow]) -> None:
        self.conn = conn
        self.medians: set[tuple[date, int]] = set()

    def master(self, code: str, name: str, market: str, sector_code: str, sector: str) -> None:
        self.conn.execute(
            """
            INSERT INTO stock_master (code, name, market_code, market_name, sector33_code, sector33_name, applied_date)
            VALUES (%s, %s, '0111', %s, %s, %s, %s)
            """,
            (code, name, market, sector_code, sector, RANK_DAY),
        )

    def median(self, pred_date: date, horizon: int, median: str | None) -> None:
        """その予測日・予測日数の市場の中央値。"""
        self.medians.add((pred_date, horizon))
        self.conn.execute(
            """
            INSERT INTO verify_daily_summary (pred_date, horizon, segment, target_date, model_version,
                                              verified_count, hit_count, market_median, up_count, beat_count,
                                              sum_change_rate, sum_excess_rate, sector_verified_count,
                                              sector_beat_count, sum_sector_excess_rate, sum_price_error_rate,
                                              unverifiable_count)
            VALUES (%s, %s, %s, %s, 'v1', 1, 1, %s, 0, 0, 0, 0, 0, 0, 0, 0, 0)
            """,
            (pred_date, horizon, const.SEGMENT_ALL, pred_date + timedelta(days=7), median),
        )

    def add(
        self,
        code: str,
        target: date,
        hit: bool,
        change_rate: str,
        *,
        buy_rank: int | None = None,
        sector: str | None = "3050",
        horizon: int = H5,
        status: str = const.VERIFY_STATUS_VERIFIED,
        pred_price: str = "110",
        actual_price: str | None = "100",
        up_prob: str = "0.6",
    ) -> None:
        """予測を1行入れる(予測日は対象日の7日前)。up_prob の初期値 0.6 は上がる予想。"""
        self.conn.execute(
            """
            INSERT INTO prediction (code, pred_date, horizon, target_date, up_prob, rel_prob, pred_price, base_price,
                                    model_version, sector33_code, buy_rank, actual_price, change_rate, is_hit,
                                    verify_status)
            VALUES (%s, %s, %s, %s, %s, 0.6, %s, 100, 'v1', %s, %s, %s, %s, %s, %s)
            """,
            (code, target - timedelta(days=7), horizon, target, up_prob, pred_price, sector, buy_rank, actual_price,
             change_rate, hit, status),
        )  # fmt: skip


@pytest.fixture
def breakdown(conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> _Breakdown:
    """件数の下限を小さくした(全銘柄3件・買い候補2件)データの入れ物。集計の基準日は RANK_DAY。"""
    from api import web_const

    monkeypatch.setattr(web_const, "ACCURACY_STOCK_MIN_COUNT_ALL", 3)
    monkeypatch.setattr(web_const, "ACCURACY_STOCK_MIN_COUNT_TOP", 2)
    _summary_day(conn, RANK_DAY)
    b = _Breakdown(conn)
    b.master("10010", "い銘柄", "プライム", "3050", "銀行業")
    b.master("10020", "あ銘柄", "グロース", "6050", "卸売業")
    b.master("10030", "う銘柄", "スタンダード", "3050", "銀行業")
    return b


def _fill(b: _Breakdown) -> None:
    """3つの対象日の予測を入れる。市場の中央値は、1日目 0.01、2日目 0.02、3日目はなし(NULL)。"""
    days = [RANK_DAY - timedelta(days=2), RANK_DAY - timedelta(days=1), RANK_DAY]
    b.median(days[0] - timedelta(days=7), H5, "0.01")
    b.median(days[1] - timedelta(days=7), H5, "0.02")
    b.median(days[2] - timedelta(days=7), H5, None)
    # 10010: 3件(的中2)。中央値を上回ったのは 0.05 > 0.01 だけ(0.02 は同じ値なので上回らない)。3日目は比べない
    b.add("10010", days[0], True, "0.05", buy_rank=1, pred_price="110", actual_price="100")
    b.add("10010", days[1], False, "0.02", buy_rank=25, pred_price="90", actual_price="100")
    b.add("10010", days[2], True, "0.03", buy_rank=3, pred_price="100", actual_price="100")
    # 10020: 3件(的中3)。中央値を上回ったのは2件のうち2件。業種は卸売業。買い候補は1日だけ(下限 2件に足りない)
    b.add("10020", days[0], True, "0.10", buy_rank=2, sector="6050")
    b.add("10020", days[1], True, "0.03", sector="6050")
    b.add("10020", days[2], True, "0.03", sector="6050")
    # 10030: 4件(的中2)。10010 と的中率は違う(0.5)。中央値を上回った割合は 10010 と同じ(0.5)。業種がない行が1つ
    b.add("10030", days[0], False, "-0.05", buy_rank=5)
    b.add("10030", days[1], True, "0.04", buy_rank=6)
    b.add("10030", days[2], False, "0.00", sector=None)
    b.add("10030", days[2] - timedelta(days=40), True, "0.01")  # 1か月より前
    # 10040: 2件だけ(下限 3件に足りない)。銘柄マスタにない
    b.add("10040", days[0], True, "0.2")
    b.add("10040", days[1], True, "0.2")
    # 数えない行: 検証待ち、検証できない、ほかの予測日数、基準日より後
    b.add("10010", days[0] - timedelta(days=1), True, "0.5", status=const.VERIFY_STATUS_PENDING)
    b.add("10010", days[0] - timedelta(days=3), True, "0.5", status=const.VERIFY_STATUS_UNVERIFIABLE)
    b.add("10010", days[0], True, "0.5", horizon=60)
    b.add("10010", RANK_DAY + timedelta(days=1), True, "0.5")


DOWN = "0.4"  # 下がる予想の上昇確率(const.UP_PROB_THRESHOLD 未満)


def _fill_down(b: _Breakdown) -> None:
    """下がると予想した行を入れる(上昇予想の集計には入らない)。対象日は _fill と重ならない、1か月の中の3日
    (同じ銘柄・予測日の予測は1つだけなので)。市場の中央値は、1日目 0.01、2日目 0.02、3日目はなし。"""
    days = [RANK_DAY - timedelta(days=12), RANK_DAY - timedelta(days=11), RANK_DAY - timedelta(days=10)]
    b.median(days[0] - timedelta(days=7), H5, "0.01")
    b.median(days[1] - timedelta(days=7), H5, "0.02")
    b.median(days[2] - timedelta(days=7), H5, None)
    # 10010: 的中 2/3、騰落率の平均 -0.0133。10020: 的中 2/3、騰落率の平均 -0.03(10010 と同じ的中率で、より低い)
    # 10030: 的中 3/3。10040: 2件だけ(下限 3件に足りない)
    for code, hits, changes, sector in (
        ("10010", (True, True, False), ("-0.05", "-0.01", "0.02"), "3050"),
        ("10020", (True, True, False), ("-0.10", "-0.02", "0.03"), "6050"),
        ("10030", (True, True, True), ("-0.01", "-0.01", "-0.01"), "3050"),
    ):
        for day, hit, change in zip(days, hits, changes, strict=True):
            b.add(code, day, hit, change, sector=sector, up_prob=DOWN, buy_rank=1 if code == "10010" else None)
    b.add("10040", days[0], True, "-0.2", up_prob=DOWN)
    b.add("10040", days[1], True, "-0.2", up_prob=DOWN)


def test_accuracy_stocks_all(client: TestClient, breakdown: _Breakdown) -> None:
    """全銘柄(上昇予想。初期値): 上がると予想した検証済みの行を銘柄ごとに数え、件数の下限以上の銘柄を、
    市場の中央値を上回った割合の高い順(同じなら騰落率の平均の高い順)に返す。下がると予想した行は数えない。"""
    _fill(breakdown)
    _fill_down(breakdown)
    body = client.get(f"/api/accuracy/stocks?horizon={H5}&period=ALL").json()
    assert body["summary_date"] == RANK_DAY.isoformat()
    assert (body["period"], body["period_start"], body["segment"], body["direction"]) == ("ALL", None, "all", "up")
    assert (body["min_count"], body["total"], body["offset"], body["has_more"]) == (3, 3, 0, False)
    assert (body["sort"], body["order"]) == ("rank", "asc")
    rows = body["rows"]
    assert [(r["rank"], r["code"], r["verified_count"], r["beat_rate"]) for r in rows] == [
        (1, "10020", 3, pytest.approx(1.0)),
        (2, "10010", 3, pytest.approx(0.5)),  # 10030 と同じ割合。騰落率の平均が高い(0.033 > 0.0)
        (3, "10030", 4, pytest.approx(0.5)),
    ]
    first = rows[0]
    assert (first["name"], first["market"], first["sector33"]) == ("あ銘柄", "グロース", "卸売業")
    assert first["hit_rate"] == pytest.approx(1.0)
    # 10010: 的中 2/3、中央値を上回った割合は中央値がある2件のうち1件、騰落率の平均、予測株価の平均誤差率
    r = rows[1]
    assert r["hit_rate"] == pytest.approx(2 / 3)
    assert r["beat_rate"] == pytest.approx(0.5)
    assert r["avg_change_rate"] == pytest.approx((0.05 + 0.02 + 0.03) / 3)
    assert r["avg_price_error_rate"] == pytest.approx((0.1 + 0.1 + 0.0) / 3)
    assert rows[2]["hit_rate"] == pytest.approx(0.5)
    assert client.get(f"/api/accuracy/stocks?horizon={H5}&period=ALL&direction=up").json() == body


def test_accuracy_stocks_down(client: TestClient, breakdown: _Breakdown) -> None:
    """下降予想: 下がると予想した行だけを数え、的中率の高い順(同じなら騰落率の平均の低い順)に並べる。
    件数の下限は、下がると予想した件数に当てる。"""
    _fill(breakdown)
    _fill_down(breakdown)
    body = client.get(f"/api/accuracy/stocks?horizon={H5}&period=ALL&direction=down").json()
    assert (body["direction"], body["total"]) == ("down", 3)
    assert [(r["rank"], r["code"], r["verified_count"]) for r in body["rows"]] == [
        (1, "10030", 3),
        (2, "10020", 3),  # 10010 と同じ的中率。騰落率の平均が低い(-0.03 < -0.013)
        (3, "10010", 3),
    ]
    r = body["rows"][2]
    assert r["hit_rate"] == pytest.approx(2 / 3)
    assert r["avg_change_rate"] == pytest.approx((-0.05 - 0.01 + 0.02) / 3)
    # 中央値がある2件(1日目 0.01、2日目 0.02)のうち、上回ったのは 0 件(-0.05、-0.01)
    assert r["beat_rate"] == pytest.approx(0.0)
    # 並べ替えても順位は変えない
    sorted_body = client.get(f"/api/accuracy/stocks?horizon={H5}&period=ALL&direction=down&sort=code").json()
    assert [(r["code"], r["rank"]) for r in sorted_body["rows"]] == [("10010", 3), ("10020", 2), ("10030", 1)]
    # 買い候補に入った日だけ(下降予想で買い候補に入ったのは 10010 の3件)
    top = client.get(f"/api/accuracy/stocks?horizon={H5}&period=ALL&direction=down&segment=top").json()
    assert [(r["code"], r["verified_count"]) for r in top["rows"]] == [("10010", 3)]


def test_accuracy_stocks_direction_threshold(client: TestClient, breakdown: _Breakdown) -> None:
    """上昇確率がちょうど const.UP_PROB_THRESHOLD(50%)の予測は、上がる予想に入る(バッチの is_hit と同じ基準)。"""
    for i in range(3):
        breakdown.add("10010", RANK_DAY - timedelta(days=i), True, "0.01", up_prob=str(const.UP_PROB_THRESHOLD))
    up = client.get(f"/api/accuracy/stocks?horizon={H5}&period=ALL&direction=up").json()
    down = client.get(f"/api/accuracy/stocks?horizon={H5}&period=ALL&direction=down").json()
    assert ([r["code"] for r in up["rows"]], down["rows"]) == (["10010"], [])


def test_accuracy_sort_keys() -> None:
    """初期の並びのキー: 同じ値のときの順と、値がないものを後ろにすること。"""
    from api.routers.accuracy import sector_sort_key, stock_sort_key

    def stock(code: str, hit: float | None, beat: float | None, change: float | None, count: int = 3) -> dict[str, Any]:
        return {"code": code, "hit_rate": hit, "beat_rate": beat, "avg_change_rate": change, "verified_count": count}

    rows = [
        stock("A", 0.5, None, 0.1),
        stock("B", 0.5, 0.5, None),
        stock("C", 0.5, 0.5, 0.1),
        stock("D", 0.5, 0.5, -0.1),
        stock("E", 0.5, 0.5, 0.1, count=5),
        stock("F", None, 0.9, 0.0),
    ]
    assert [r["code"] for r in sorted(rows, key=stock_sort_key("up"))] == ["F", "E", "C", "D", "B", "A"]
    assert [r["code"] for r in sorted(rows, key=stock_sort_key("down"))] == ["D", "E", "A", "C", "B", "F"]

    def sector(code: str | None, hit: float | None, beat: float | None, change: float | None) -> dict[str, Any]:
        return {
            "sector33_code": code, "all_hit_rate": hit, "all_beat_rate": beat, "all_avg_change_rate": change,
            "all_verified_count": 3,
        }  # fmt: skip

    sectors = [sector("1", 0.6, 0.5, 0.1), sector("2", 0.6, 0.5, 0.2), sector(None, 0.7, None, -0.1)]
    assert [s["sector33_code"] for s in sorted(sectors, key=sector_sort_key("up"))] == ["2", "1", None]
    assert [s["sector33_code"] for s in sorted(sectors, key=sector_sort_key("down"))] == [None, "1", "2"]


def test_accuracy_stocks_period(client: TestClient, breakdown: _Breakdown) -> None:
    """期間は対象日で区切る(順位別の的中率と同じ決め方)。初期値は直近3か月。"""
    _fill(breakdown)
    body = client.get(f"/api/accuracy/stocks?horizon={H5}&period=1M").json()
    assert body["period_start"] == "2026-07-31"
    # 10030 は1か月の中では3件(的中1)。10010 と割合も件数も同じなので、銘柄コードの順
    assert [(r["code"], r["verified_count"]) for r in body["rows"]] == [("10020", 3), ("10010", 3), ("10030", 3)]
    assert body["rows"][2]["hit_rate"] == pytest.approx(1 / 3)
    default = client.get(f"/api/accuracy/stocks?horizon={H5}").json()
    assert (default["period"], default["period_start"]) == ("3M", "2026-05-31")
    assert {r["code"]: r["verified_count"] for r in default["rows"]}["10030"] == 4


def test_accuracy_stocks_top(client: TestClient, breakdown: _Breakdown) -> None:
    """買い候補に入った日だけ: buy_rank が 1〜20 の行を数える。件数の下限は買い候補用の値。"""
    _fill(breakdown)
    body = client.get(f"/api/accuracy/stocks?horizon={H5}&period=ALL&segment=top").json()
    assert (body["segment"], body["min_count"]) == ("top", 2)
    # 10010 は 25位の日を数えない(2件・的中2)。10020 は1件だけなので出ない。10030 は2件(的中1)
    assert [(r["rank"], r["code"], r["verified_count"]) for r in body["rows"]] == [(1, "10010", 2), (2, "10030", 2)]
    assert body["rows"][0]["hit_rate"] == pytest.approx(1.0)
    assert body["rows"][0]["beat_rate"] == pytest.approx(1.0)  # 中央値がある1件(0.05 > 0.01)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("sort=hit_rate&order=desc", ["10020", "10010", "10030"]),
        ("sort=hit_rate&order=asc", ["10030", "10010", "10020"]),
        ("sort=rank&order=asc", ["10020", "10010", "10030"]),
        ("sort=rank&order=desc", ["10030", "10010", "10020"]),
        ("sort=code&order=asc", ["10010", "10020", "10030"]),
        ("sort=name&order=asc", ["10020", "10010", "10030"]),
        ("sort=market&order=desc", ["10010", "10030", "10020"]),
        ("sort=sector33&order=asc", ["10020", "10010", "10030"]),
        # 件数が同じ銘柄は、初期の並び(順位)の順のまま
        ("sort=verified_count&order=desc", ["10030", "10020", "10010"]),
        ("sort=verified_count&order=asc", ["10020", "10010", "10030"]),
        # 中央値を上回った割合の大きい順は初期の並びと同じ。10010 と 10030 は同じ値(初期の並びの順のまま)
        ("sort=beat_rate&order=desc", ["10020", "10010", "10030"]),
        ("sort=beat_rate&order=asc", ["10010", "10030", "10020"]),
        ("sort=avg_change_rate&order=desc", ["10020", "10010", "10030"]),
        ("sort=avg_price_error_rate&order=asc", ["10010", "10020", "10030"]),
    ],
)
def test_accuracy_stocks_sort(client: TestClient, breakdown: _Breakdown, query: str, expected: list[str]) -> None:
    """列の見出しで並べ替える。順位は、並べ替えても初期の並び(市場の中央値を上回った割合の高い順)の順位のまま。"""
    _fill(breakdown)
    body = client.get(f"/api/accuracy/stocks?horizon={H5}&period=ALL&{query}").json()
    assert [r["code"] for r in body["rows"]] == expected
    assert {r["code"]: r["rank"] for r in body["rows"]} == {"10020": 1, "10010": 2, "10030": 3}


def test_accuracy_stocks_sort_missing_last(client: TestClient, breakdown: _Breakdown) -> None:
    """値がない銘柄(銘柄マスタにない、実株価がない)は、どちらの順でも後ろに並べる。"""
    b = breakdown
    for code, actual in (("10010", "100"), ("10050", None), ("10020", "100")):
        for i in range(3):
            b.add(code, RANK_DAY - timedelta(days=i), True, "0.01", actual_price=actual)
    for order in ("asc", "desc"):
        body = client.get(f"/api/accuracy/stocks?horizon={H5}&period=ALL&sort=name&order={order}").json()
        assert [r["code"] for r in body["rows"]][-1] == "10050"
        body = client.get(
            f"/api/accuracy/stocks?horizon={H5}&period=ALL&sort=avg_price_error_rate&order={order}"
        ).json()
        assert [r["code"] for r in body["rows"]][-1] == "10050"
        assert body["rows"][-1]["avg_price_error_rate"] is None and body["rows"][-1]["name"] == ""
        # 市場の中央値がないので、上回った割合は全て None(並びは初期の順)
        assert all(r["beat_rate"] is None for r in body["rows"])


def test_accuracy_stocks_paging(client: TestClient, breakdown: _Breakdown, monkeypatch: pytest.MonkeyPatch) -> None:
    """ACCURACY_STOCK_PAGE_SIZE 件ずつ返す。"""
    from api import web_const

    monkeypatch.setattr(web_const, "ACCURACY_STOCK_PAGE_SIZE", 2)
    _fill(breakdown)
    first = client.get(f"/api/accuracy/stocks?horizon={H5}&period=ALL").json()
    assert ([r["code"] for r in first["rows"]], first["total"], first["has_more"]) == (["10020", "10010"], 3, True)
    more = client.get(f"/api/accuracy/stocks?horizon={H5}&period=ALL&offset=2").json()
    assert ([r["code"] for r in more["rows"]], more["offset"], more["has_more"]) == (["10030"], 2, False)
    assert more["rows"][0]["rank"] == 3


def test_accuracy_stocks_default_min_counts() -> None:
    """件数の下限の設定値(全銘柄20件・買い候補5件)と、1回の件数。"""
    from api import web_const

    assert web_const.ACCURACY_STOCK_MIN_COUNT_ALL == 20
    assert web_const.ACCURACY_STOCK_MIN_COUNT_TOP == 5
    assert web_const.ACCURACY_STOCK_PAGE_SIZE == 50
    assert web_const.ACCURACY_BREAKDOWN_DEFAULT_PERIOD == "3M"


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("horizon=7", "予測日数は"),
        ("period=9M", "期間は 1M・3M・6M・1Y・2Y・ALL のどれかを指定してください"),
        ("segment=sector", "対象の指定が正しくありません(sector)"),
        ("sort=xxx", "並べ替えの指定が正しくありません(xxx)"),
        ("order=up", "order は asc か desc を指定してください"),
        ("offset=-1", "offset は 0 以上を指定してください"),
        ("direction=all", "予想の向きが正しくありません"),
    ],
)
def test_accuracy_stocks_bad_params(client: TestClient, query: str, message: str) -> None:
    res = client.get(f"/api/accuracy/stocks?{query}")
    assert res.status_code == 400
    assert message in res.json()["detail"]


def test_accuracy_stocks_and_sectors_no_summary(client: TestClient, conn: psycopg.Connection[DictRow]) -> None:
    """集計がまだなければ、summary_date は None で rows は空。"""
    body = client.get("/api/accuracy/stocks").json()
    assert body["summary_date"] is None and body["rows"] == [] and body["total"] == 0
    assert body["has_more"] is False and body["min_count"] == 20
    body = client.get("/api/accuracy/sectors").json()
    assert body == {
        "summary_date": None, "horizon": const.MAIN_HORIZON, "period": "3M", "period_start": None,
        "direction": "up", "rows": [],
    }  # fmt: skip


def test_accuracy_stocks_date(client: TestClient, breakdown: _Breakdown) -> None:
    """集計の基準日は /api/accuracy と同じ決め方(指定した日以前で一番新しい日)。基準日より後の対象日は数えない。"""
    _fill(breakdown)
    _summary_day(breakdown.conn, RANK_DAY - timedelta(days=1))
    body = client.get(f"/api/accuracy/stocks?horizon={H5}&period=ALL&date={RANK_DAY - timedelta(days=1)}").json()
    assert body["summary_date"] == (RANK_DAY - timedelta(days=1)).isoformat()
    # 10010・10020 は2件になり、下限(3件)に足りない。10030 は3件
    assert [(r["code"], r["verified_count"]) for r in body["rows"]] == [("10030", 3)]


def test_accuracy_sectors(client: TestClient, breakdown: _Breakdown) -> None:
    """業種別: 予測した時点の33業種ごとに、全銘柄と買い候補の値を返す。業種がない行は「(不明)」。"""
    _fill(breakdown)
    body = client.get(f"/api/accuracy/sectors?horizon={H5}&period=ALL").json()
    assert (body["summary_date"], body["period"], body["period_start"]) == (RANK_DAY.isoformat(), "ALL", None)
    rows = {r["sector33"]: r for r in body["rows"]}
    # 並びは全銘柄の的中率の高い順(卸売業 3/3 → 銀行業 7/8 → (不明) 0/1)
    assert [r["sector33"] for r in body["rows"]] == ["卸売業", "銀行業", "(不明)"]
    assert rows["(不明)"]["sector33_code"] is None
    assert rows["卸売業"]["sector33_code"] == "6050"
    # 銀行業: 10010 の3件 + 10030 の3件 + 10040 の2件(件数の下限は銘柄別だけ)
    bank = rows["銀行業"]
    assert bank["all"]["verified_count"] == 8
    assert bank["all"]["hit_rate"] == pytest.approx(6 / 8)
    # 中央値がある行(1日目 0.01、2日目 0.02): 0.05・-0.05・0.2 / 0.02(同じ)・0.04・0.2 → 4/6
    assert bank["all"]["beat_rate"] == pytest.approx(4 / 6)
    assert bank["all"]["avg_change_rate"] == pytest.approx((0.05 + 0.02 + 0.03 - 0.05 + 0.04 + 0.01 + 0.2 + 0.2) / 8)
    # 買い候補(1〜20位): 10010 の1位・3位、10030 の5位・6位
    assert bank["top"]["verified_count"] == 4
    assert bank["top"]["hit_rate"] == pytest.approx(3 / 4)
    assert bank["top"]["beat_rate"] == pytest.approx(2 / 3)
    assert bank["top"]["avg_change_rate"] == pytest.approx((0.05 + 0.03 - 0.05 + 0.04) / 4)
    # 卸売業の買い候補は1件
    assert rows["卸売業"]["top"]["verified_count"] == 1
    # 買い候補が0件の業種は、件数0で率は None
    assert rows["(不明)"]["top"] == {
        "verified_count": 0, "hit_rate": None, "beat_rate": None, "avg_change_rate": None,
    }  # fmt: skip
    assert rows["(不明)"]["all"]["hit_rate"] == pytest.approx(0.0)
    # 期間で区切る(1か月より前の1件は数えない)
    month = client.get(f"/api/accuracy/sectors?horizon={H5}&period=1M").json()
    assert month["period_start"] == "2026-07-31"
    assert {r["sector33"]: r["all"]["verified_count"] for r in month["rows"]} == {"卸売業": 3, "銀行業": 7, "(不明)": 1}


def test_accuracy_sectors_direction(client: TestClient, breakdown: _Breakdown) -> None:
    """業種別の予想の向き: 選んだ向きの行だけを数える。上昇予想は全銘柄の市場の中央値を上回った割合の高い順、
    下降予想は全銘柄の的中率の高い順。"""
    _fill(breakdown)
    _fill_down(breakdown)
    up = client.get(f"/api/accuracy/sectors?horizon={H5}&period=ALL").json()
    assert up["direction"] == "up"
    # 上昇予想の数え方は _fill だけのときと同じ(卸売業 1.0 → 銀行業 4/6 → (不明) は割合がない)
    counts = [(r["sector33"], r["all"]["verified_count"]) for r in up["rows"]]
    assert counts == [("卸売業", 3), ("銀行業", 8), ("(不明)", 1)]
    down = client.get(f"/api/accuracy/sectors?horizon={H5}&period=ALL&direction=down").json()
    assert down["direction"] == "down"
    rows = {r["sector33"]: r for r in down["rows"]}
    # 銀行業: 10010 の3件 + 10030 の3件 + 10040 の2件 → 的中 7/8。卸売業: 的中 2/3
    assert [r["sector33"] for r in down["rows"]] == ["銀行業", "卸売業"]
    assert rows["銀行業"]["all"]["verified_count"] == 8
    assert rows["銀行業"]["all"]["hit_rate"] == pytest.approx(7 / 8)
    assert rows["銀行業"]["top"]["verified_count"] == 3  # 10010 の3件(1位)
    assert rows["卸売業"]["all"]["avg_change_rate"] == pytest.approx((-0.10 - 0.02 + 0.03) / 3)


def test_accuracy_sectors_unknown_code(client: TestClient, breakdown: _Breakdown) -> None:
    """銘柄マスタにない業種コードは、コードをそのまま出す。"""
    breakdown.add("10010", RANK_DAY, True, "0.01", sector="9999")
    body = client.get(f"/api/accuracy/sectors?horizon={H5}&period=ALL").json()
    assert [(r["sector33_code"], r["sector33"]) for r in body["rows"]] == [("9999", "9999")]


@pytest.mark.parametrize(
    ("query", "message"),
    [("horizon=7", "予測日数は"), ("period=X", "期間は"), ("direction=x", "予想の向きが正しくありません")],
)
def test_accuracy_sectors_bad_params(client: TestClient, query: str, message: str) -> None:
    res = client.get(f"/api/accuracy/sectors?{query}")
    assert res.status_code == 400
    assert message in res.json()["detail"]


def test_accuracy_breakdown_uses_rank_period_start(
    client: TestClient, breakdown: _Breakdown, monkeypatch: pytest.MonkeyPatch
) -> None:
    """期間の区切りは、順位別の的中率と同じ関数(rank_period_start)で決める。"""
    from api.routers import accuracy as accuracy_module

    called: list[tuple[date, int | None]] = []
    original = accuracy_module.rank_period_start

    def spy(summary_date: date, months: int | None) -> date | None:
        called.append((summary_date, months))
        return original(summary_date, months)

    monkeypatch.setattr(accuracy_module, "rank_period_start", spy)
    client.get(f"/api/accuracy/stocks?horizon={H5}&period=6M")
    client.get(f"/api/accuracy/sectors?horizon={H5}&period=2Y")
    assert called == [(RANK_DAY, 6), (RANK_DAY, 24)]
