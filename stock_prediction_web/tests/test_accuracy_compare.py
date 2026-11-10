"""精度の比較の API(/api/accuracy/compare)のテスト。"""

from decimal import Decimal

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow
from web_sample import LAST_DAY, MODEL_VERSION, pred_days

import const
from common import business_day


def _setup(conn: psycopg.Connection[DictRow]) -> str:
    """一番古い予測日の、最初の予測日数の対象日を集計の基準日にした集計を足し、その日付を返す。

    web_sample の予測日ごとの集計(最初の予測日数)に加えて、主となる日数の予測日ごとの集計
    (対象日は予測日と同じにする)を入れる。
    """
    first, h0 = pred_days()[0], const.PREDICT_HORIZONS[0]
    target = business_day.business_days_after(first, h0)
    for segment, verified, up_rate, sector_rate in (
        (const.SEGMENT_ALL, 1000, "0.46", "0.48"),
        (const.SEGMENT_TOP, 20, "0.747", "0.579"),
        (const.SEGMENT_TOP_SECTOR, 30, "0.543", "0.544"),
    ):
        conn.execute(
            """
            INSERT INTO verify_period_summary (summary_date, period_type, horizon, segment, verified_count, hit_count,
                up_rate, avg_change_rate, beat_rate, avg_excess_rate, sector_beat_rate, avg_sector_excess_rate)
            VALUES (%s, 'ALL', %s, %s, %s, 1, %s, 0.062, 0.813, 0.0682, %s, 0.0185)
            """,
            (target, const.MAIN_HORIZON, segment, verified, up_rate, sector_rate),
        )
    for segment, verified, up, excess, n_sector, sector_excess in (
        (const.SEGMENT_ALL, 10, 6, "0", 10, "0"),
        (const.SEGMENT_TOP, 4, 3, "0.08", 4, "0.04"),
        (const.SEGMENT_TOP_SECTOR, 6, 3, "0.06", 5, "0.05"),
    ):
        conn.execute(
            """
            INSERT INTO verify_daily_summary (pred_date, horizon, segment, target_date, model_version, verified_count,
                hit_count, up_count, beat_count, sum_change_rate, sum_excess_rate, market_median,
                sector_verified_count, sector_beat_count, sum_sector_excess_rate, sum_price_error_rate,
                unverifiable_count)
            VALUES (%s, %s, %s, %s, %s, %s, 0, %s, 0, 0, %s, 0, %s, 0, %s, 0, 0)
            """,
            (first, const.MAIN_HORIZON, segment, first, MODEL_VERSION, verified, up, Decimal(excess), n_sector,
             Decimal(sector_excess)),
        )  # fmt: skip
    return target.isoformat()


def test_compare(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    target = _setup(sample)
    first = pred_days()[0].isoformat()
    body = client.get(f"/api/accuracy/compare?date={target}").json()
    assert body["summary_date"] == target and body["period"] == "ALL" and body["period_start"] is None
    assert body["main_horizon"] == const.MAIN_HORIZON
    # ①・②: 全ての予測日数 × 3つの対象区分
    rows = {(r["horizon"], r["segment"]): r for r in body["rows"]}
    assert len(body["rows"]) == len(const.PREDICT_HORIZONS) * 3
    assert [r["segment"] for r in body["rows"][:3]] == ["ALL", "TOP", "TOP_SECTOR"]
    main = rows[(const.MAIN_HORIZON, "TOP")]
    assert (main["verified_count"], main["up_rate"], main["avg_change_rate"]) == (20, 0.747, 0.062)
    assert (main["beat_rate"], main["avg_excess_rate"]) == (0.813, 0.0682)
    assert (main["sector_beat_rate"], main["avg_sector_excess_rate"]) == (0.579, 0.0185)
    # ③: 検証できた予測日の最初と最後、銘柄の数(主となる日数は検証済みの予測がないので0)
    assert (main["first_pred_date"], main["last_pred_date"], main["code_count"]) == (first, first, 0)
    h0 = const.PREDICT_HORIZONS[0]
    five = rows[(h0, "TOP_SECTOR")]
    assert five["verified_count"] == 0 and five["up_rate"] is None  # 期間の集計がない
    assert (five["first_pred_date"], five["code_count"]) == (first, 3)
    assert (rows[(h0, "ALL")]["code_count"], rows[(h0, "TOP")]["code_count"]) == (5, 3)
    # ④: 業種の内訳(主となる日数の、検証できた予測日の行)。件数の多い順、同じ件数は業種の名前の順
    sectors = {s["segment"]: s for s in body["sectors"]}
    top = sectors["TOP"]
    assert (top["total"], top["sector_count"], top["other_count"]) == (3, 2, 0)
    assert [(s["sector"], s["count"]) for s in top["top"]] == [("銀行業", 2), ("卸売業", 1)]
    assert top["top"][0]["share"] == pytest.approx(2 / 3)
    spread = sectors["TOP_SECTOR"]
    assert [s["sector"] for s in spread["top"]] == sorted(["銀行業", "卸売業", "電気機器"])
    # ⑤: 月ごと(主となる日数)
    month = pred_days()[0].strftime("%Y-%m")
    monthly = {m["segment"]: m for m in body["monthly"] if m["month"] == month}
    assert [m["segment"] for m in body["monthly"]] == ["ALL", "TOP", "TOP_SECTOR"]
    assert (monthly["TOP"]["up_rate"], monthly["TOP"]["avg_excess_rate"]) == (0.75, 0.02)
    assert monthly["TOP_SECTOR"]["avg_sector_excess_rate"] == 0.01  # 業種比の件数(5)で割る
    # ⑥: 使ったモデル
    assert body["models"] == [
        {
            "model_version": MODEL_VERSION,
            "first_pred_date": first,
            "last_pred_date": first,
            "base_date": "2026-07-18",
            "train_start_date": "2024-07-18",
            "train_end_date": "2026-07-18",
        }
    ]


def test_compare_top_n_and_other(
    client: TestClient, sample: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    """上位の業種の数を超えた分は「その他」。業種がない行は「(不明)」。"""
    from api import web_const

    target = _setup(sample)
    sample.execute("UPDATE prediction SET sector33_code = NULL WHERE code = '11120'")
    monkeypatch.setattr(web_const, "COMPARE_SECTOR_TOP_N", 1)
    body = client.get(f"/api/accuracy/compare?date={target}").json()
    top = {s["segment"]: s for s in body["sectors"]}["TOP"]
    assert [(s["sector"], s["count"]) for s in top["top"]] == [("銀行業", 2)]
    assert (top["other_count"], top["other_share"], top["sector_count"]) == (1, pytest.approx(1 / 3), 2)


def test_compare_period(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """期間を指定すると、その期間の集計。期間の集計がない区分は件数0。"""
    target = _setup(sample)
    body = client.get(f"/api/accuracy/compare?date={target}&period=1M").json()
    assert body["period"] == "1M" and body["period_start"] is not None
    assert all(r["verified_count"] == 0 for r in body["rows"])


def test_compare_no_summary(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    body = client.get("/api/accuracy/compare?date=2026-01-01").json()
    assert body["summary_date"] is None
    assert (body["rows"], body["sectors"], body["monthly"], body["models"]) == ([], [], [], [])


def test_compare_latest_without_main_horizon(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """最新の集計の基準日。主となる日数の検証できた予測日がなければ、業種の内訳は0件。"""
    body = client.get("/api/accuracy/compare").json()
    assert body["summary_date"] == LAST_DAY.isoformat()
    assert all(s["total"] == 0 and s["top"] == [] and s["other_share"] is None for s in body["sectors"])


def test_compare_bad_period(client: TestClient) -> None:
    res = client.get("/api/accuracy/compare?period=2M")
    assert res.status_code == 400
    assert res.json() == {"detail": "期間は 1M・3M・ALL のどれかを指定してください"}
