"""指数の API(/api/index/prices)のテスト。"""

from datetime import date

import numpy as np
import pandas as pd
import psycopg
from fastapi.testclient import TestClient
from psycopg.rows import DictRow
from web_sample import LAST_DAY, price_days

import const
from api import web_const
from api.routers.stocks import build_prices_response, find_trendlines


def _insert_index(conn: psycopg.Connection[DictRow], days: list[date], code: str = const.INDEX_CODE_N225) -> None:
    """日経平均の日足を入れる(上昇の波。上値抵抗線と下値支持線を引ける形)。"""
    t = np.arange(len(days))
    close = 30000 + 60 * t + 900 * np.sin(t * 2 * np.pi / 15)
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO index_daily_price (index_code, trade_date, open, high, low, close)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            [(code, d, c - 10, c + 150, c - 150, c) for d, c in zip(days, close.tolist(), strict=True)],
        )


def test_index_prices(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """date を省略すると最新の予測日まで。期間の分の行、出来高は None、移動平均などは全ての期間で計算。"""
    days = price_days()
    _insert_index(sample, days)
    body = client.get("/api/index/prices?period=1M").json()
    assert body["code"] == const.INDEX_CODE_N225
    assert body["period"] == "1M"
    assert body["bb_days"] == const.BB_DAYS
    rows = body["rows"]
    assert 18 <= len(rows) <= 24
    assert rows[-1]["date"] == LAST_DAY.isoformat()
    assert all(r["volume"] is None for r in rows)
    last = rows[-1]
    assert last["ma75"] is not None
    assert last["bb_up2"] > last["bb_up1"] > last["bb_mid"] > last["bb_dn1"] > last["bb_dn2"]
    assert last["high"] >= last["close"] >= last["low"]
    assert len(client.get("/api/index/prices?period=ALL").json()["rows"]) == len(days)
    assert client.get("/api/index/prices").json()["period"] == web_const.CHART_DEFAULT_PERIOD
    # 指数日足のほかの指数の行は使わない
    _insert_index(sample, [date(2026, 7, 23)], code="^TOPX")
    assert client.get("/api/index/prices?period=ALL").json()["rows"][-1]["date"] == LAST_DAY.isoformat()


def test_index_prices_until_date(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """date より後の行は使わない(右端・移動平均・トレンドラインとも)。date の日に日経平均がなければ、その前の日まで。"""
    days = price_days()
    _insert_index(sample, days)
    cut = days[-11]
    body = client.get(f"/api/index/prices?period=ALL&date={cut.isoformat()}").json()
    assert body["rows"][-1]["date"] == cut.isoformat()
    assert len(body["rows"]) == len(days) - 10
    # 銘柄のチャートと同じ計算で、date 以前の行だけから作った値と同じ
    rows = sample.execute(
        "SELECT trade_date, open::float8, high::float8, low::float8, close::float8 FROM index_daily_price"
        " WHERE trade_date <= %s ORDER BY trade_date",
        (cut,),
    ).fetchall()
    df = pd.DataFrame([dict(r) for r in rows]).rename(
        columns={"open": "adj_open", "high": "adj_high", "low": "adj_low", "close": "adj_close"}
    )
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["adj_volume"] = np.nan
    expected = build_prices_response(df, const.INDEX_CODE_N225, "ALL")
    assert body["rows"][-1]["ma25"] == expected.rows[-1].ma25
    assert body["trendlines"] == [t.model_dump(mode="json") for t in find_trendlines(df)]
    assert body["trendlines"] != []
    assert all(t["end_date"] == cut.isoformat() for t in body["trendlines"])
    # 予測日の日経平均がない(休場の日など)ときは、その前の日まで
    sample.execute("DELETE FROM index_daily_price WHERE trade_date = %s", (cut,))
    gap = client.get(f"/api/index/prices?period=ALL&date={cut.isoformat()}").json()
    assert gap["rows"][-1]["date"] == days[-12].isoformat()


def test_index_prices_trendlines(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """トレンドラインは接触2回以上の線だけ。表示する期間の中に収まる。"""
    _insert_index(sample, price_days())
    body = client.get("/api/index/prices").json()
    assert body["trendlines"] != []
    first = body["rows"][0]["date"]
    for line in body["trendlines"]:
        assert line["kind"] in ("SUPPORT", "RESIST")
        assert line["touches"] >= const.TREND_CHART_MIN_TOUCHES
        assert first <= line["start_date"] < line["end_date"] == LAST_DAY.isoformat()


def test_index_prices_no_rows(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """データがなければ rows を空にして返す(エラーにしない)。date より前に1行もない日も同じ。"""
    body = client.get("/api/index/prices").json()
    assert body["rows"] == []
    assert body["trendlines"] == []
    _insert_index(sample, price_days()[-5:])
    old = client.get(f"/api/index/prices?date={price_days()[0].isoformat()}")
    assert old.status_code == 200
    assert old.json()["rows"] == []


def test_index_prices_no_prediction(client: TestClient, conn: psycopg.Connection[DictRow]) -> None:
    """予測が1つもなければ、全ての日を使う。期間の指定が正しくなければ 400。"""
    _insert_index(conn, price_days()[-3:])
    assert len(client.get("/api/index/prices").json()["rows"]) == 3
    assert client.get("/api/index/prices?period=2Y").status_code == 400
    assert client.get("/api/index/prices?date=2026-13-01").status_code in (400, 422)
