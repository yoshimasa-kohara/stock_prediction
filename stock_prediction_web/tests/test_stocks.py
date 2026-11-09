"""銘柄の API(/api/stocks/...)のテスト。"""

import numpy as np
import pandas as pd
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow
from web_sample import LAST_DAY, PRICE_DAYS, pred_days

import const
from api import web_const
from api.routers.stocks import escape_like, find_trendlines, zero_stop
from core import trendline


@pytest.mark.parametrize(
    ("q", "scope", "codes"),
    [
        ("1114", "target", ["11140", "11145"]),  # 4桁の前方一致で、普通株と優先株式の両方
        ("11140", "target", ["11140"]),
        ("銀行", "target", ["11110"]),
        ("サンプル", "target", ["11110", "11120", "11130", "11140", "11145"]),
        ("1305", "target", []),  # 対象外の市場(ETF)は、上のメニューの検索では出さない
        ("1305", "all", ["13050"]),  # 保有銘柄の登録では出す
        ("  ", "target", []),
        ("%", "target", []),  # LIKE の特別な文字は、文字そのものとして探す
    ],
)
def test_search(client: TestClient, sample: psycopg.Connection[DictRow], q: str, scope: str, codes: list[str]) -> None:
    body = client.get("/api/stocks/search", params={"q": q, "scope": scope}).json()
    assert [i["code"] for i in body["items"]] == codes


def test_search_limit_and_fields(
    client: TestClient, sample: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(web_const, "STOCK_SEARCH_LIMIT", 2)
    body = client.get("/api/stocks/search?q=サンプル").json()
    assert body["items"] == [
        {"code": "11110", "name": "サンプル銀行A", "market": "プライム", "sector33": "銀行業"},
        {"code": "11120", "name": "サンプル商事A", "market": "スタンダード", "sector33": "卸売業"},
    ]


def test_search_bad_scope(client: TestClient) -> None:
    res = client.get("/api/stocks/search?q=1&scope=x")
    assert res.status_code == 400


def test_escape_like() -> None:
    assert escape_like("a%b_c\\") == "a\\%b\\_c\\\\"


def test_stock_info(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """銘柄の情報、終値と前日比、指標、業種の予想PERの中央値、次の決算発表予定日、備考。"""
    body = client.get("/api/stocks/11110").json()
    assert body["name"] == "サンプル銀行A"
    assert body["market"] == "プライム"
    assert body["sector33"] == "銀行業"
    assert body["scale_category"] == "TOPIX Small 1"
    assert body["is_target"] is True
    assert body["price_date"] == LAST_DAY.isoformat()
    assert body["close"] > 0
    assert body["prev_change"] is not None
    assert body["pbr"] == 0.4
    assert body["fwd_per"] == 7.9
    # 同じ銀行業(11110 の 7.9 倍と 11140 の 11.8 倍)の中央値
    assert body["sector_median_per"] == pytest.approx(9.85)
    # 時価総額: DB は百万円(182000)、API は円で返す(1,820億円)
    assert body["market_cap"] == 182_000_000_000
    assert body["earnings_date"] == "2026-08-07"
    assert body["earnings_name"] == "1Q"
    assert body["earnings_days"] == 12
    assert body["notes_date"] == LAST_DAY.isoformat()
    assert {"text": "ストップ高", "color": "red"} in body["notes"]


def test_stock_info_not_target(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """予測の対象でない銘柄(ETF)は is_target が false。指標がない項目は null。"""
    body = client.get("/api/stocks/13050").json()
    assert body["is_target"] is False
    assert body["pbr"] is None
    assert body["earnings_date"] is None
    assert body["notes_date"] == LAST_DAY.isoformat()


def test_stock_info_no_prices(client: TestClient, conn: psycopg.Connection[DictRow]) -> None:
    conn.execute("INSERT INTO stock_master (code, name, applied_date) VALUES ('99990', '株価なし', '2026-07-22')")
    body = client.get("/api/stocks/99990").json()
    assert body["close"] is None
    assert body["notes"] == []


def test_stock_not_found(client: TestClient) -> None:
    for path in ["/api/stocks/00000", "/api/stocks/00000/prices", "/api/stocks/00000/predictions"]:
        res = client.get(path)
        assert res.status_code == 404
        assert res.json() == {"detail": "銘柄が見つかりません"}


def test_prices(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """期間の分の株価と、移動平均・ボリンジャーバンド(全ての期間で計算)。"""
    body = client.get("/api/stocks/11110/prices?period=1M").json()
    assert body["bb_days"] == const.BB_DAYS
    rows = body["rows"]
    assert 18 <= len(rows) <= 24
    assert rows[-1]["date"] == LAST_DAY.isoformat()
    last = rows[-1]
    assert last["ma75"] is not None
    assert last["bb_up2"] > last["bb_up1"] > last["bb_mid"] > last["bb_dn1"] > last["bb_dn2"]
    assert last["high"] >= last["close"] >= last["low"]
    all_rows = client.get("/api/stocks/11110/prices?period=ALL").json()["rows"]
    assert len(all_rows) == PRICE_DAYS
    assert all_rows[0]["ma5"] is None
    six = client.get("/api/stocks/11110/prices").json()
    assert six["period"] == web_const.CHART_DEFAULT_PERIOD


def test_prices_trendlines(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """トレンドラインは、接触2回以上の線だけ。両端の日付と値を返す(0円で止めた線は、右端が今日より前)。"""
    for code in ["11110", "11120", "11130", "11140"]:
        for line in client.get(f"/api/stocks/{code}/prices").json()["trendlines"]:
            assert line["kind"] in ("SUPPORT", "RESIST")
            assert line["touches"] >= const.TREND_CHART_MIN_TOUCHES
            assert line["start_date"] < line["end_date"] <= LAST_DAY.isoformat()
            assert line["end_value"] >= 0
            assert (line["break_date"] is None) == (line["break_value"] is None)


def test_prices_no_rows_and_bad_period(client: TestClient, conn: psycopg.Connection[DictRow]) -> None:
    conn.execute("INSERT INTO stock_master (code, name, applied_date) VALUES ('99990', '株価なし', '2026-07-22')")
    body = client.get("/api/stocks/99990/prices").json()
    assert body["rows"] == []
    assert body["trendlines"] == []
    assert client.get("/api/stocks/99990/prices?period=2Y").status_code == 400


def test_predictions(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """最新の予測(見込み = 予測株価 ÷ 現在値 − 1)と、過去の予測と答え合わせ(新しい順)。"""
    body = client.get("/api/stocks/11110/predictions").json()
    assert body["latest_date"] == LAST_DAY.isoformat()
    assert body["latest_buy_rank"] == 1
    assert [r["horizon"] for r in body["latest"]] == const.PREDICT_HORIZONS
    first = body["latest"][0]
    assert first["expected"] == pytest.approx(first["pred_price"] / body["latest_base_price"] - 1)
    history = body["history"]
    assert len(history) == len(pred_days()) * len(const.PREDICT_HORIZONS)
    assert history[0]["pred_date"] == LAST_DAY.isoformat()
    verified = [h for h in history if h["verify_status"] == "VERIFIED"]
    assert len(verified) == 1
    assert verified[0]["actual_price"] is not None
    assert verified[0]["is_hit"] is not None


def test_predictions_filters(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    d = pred_days()
    body = client.get(
        f"/api/stocks/11110/predictions?horizon={const.MAIN_HORIZON}&from={d[1].isoformat()}&to={d[2].isoformat()}"
    ).json()
    assert [(h["pred_date"], h["horizon"]) for h in body["history"]] == [
        (d[2].isoformat(), const.MAIN_HORIZON),
        (d[1].isoformat(), const.MAIN_HORIZON),
    ]
    assert client.get("/api/stocks/11110/predictions?horizon=7").status_code == 400


def test_predictions_none(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    body = client.get("/api/stocks/13050/predictions").json()
    assert body["latest_date"] is None
    assert body["latest"] == []
    assert body["history"] == []


def test_find_trendlines_on_wave() -> None:
    """きれいな上昇の波では、上値抵抗線と下値支持線の両方を、接触3回以上で引ける。"""
    n = 120
    t = np.arange(n)
    close = 1000 + 2 * t + 30 * np.sin(t * 2 * np.pi / 15)
    df = pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2026-01-01", periods=n),
            "adj_close": close,
            "adj_high": close + 5,
            "adj_low": close - 5,
        }
    )
    lines = {line.kind: line for line in find_trendlines(df)}
    assert set(lines) == {"RESIST", "SUPPORT"}
    assert all(line.touches >= const.TREND_MIN_TOUCHES for line in lines.values())
    assert all(line.break_date is None for line in lines.values())
    assert lines["SUPPORT"].end_value > lines["SUPPORT"].start_value
    # 表示する期間の最初の行より前から始まる線は、その行の位置の値から始める
    clipped = {line.kind: line for line in find_trendlines(df, n - 5)}
    assert clipped["SUPPORT"].start_date == df["trade_date"].iloc[n - 5].date()
    assert clipped["SUPPORT"].end_value == lines["SUPPORT"].end_value
    # 株価が足りなければ線は引かない(線を引く期間が20営業日未満)。期間が短くても20営業日あれば引く
    assert find_trendlines(df.head(25)) == []
    assert find_trendlines(df.head(26)) != []
    assert find_trendlines(df.head(0)) == []


def _frame(close: np.ndarray, high: np.ndarray, low: np.ndarray) -> pd.DataFrame:
    """find_trendlines に渡す株価の表(2026-01-01 からの営業日)。"""
    return pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2026-01-01", periods=len(close)),
            "adj_close": close,
            "adj_high": high,
            "adj_low": low,
        }
    )


def test_find_trendlines_two_touches_and_break() -> None:
    """接触2回の線も返す。抜けた日がある線は、抜けた日と、その日の線の値を返す。"""
    n = 100
    t = np.arange(n, dtype=float)
    line = 1000 + 2 * t
    close = line + 30
    high, low = close + 5, close - 5
    # 60 と 75 の2つの谷を結ぶ下値支持線。97 に終値が線より5%下に抜けた
    # (抜けた日は、線を引く期間(3営業日前の 96 まで)の外にして、抜けた日の谷が線に使われないようにする)
    for d in (60, 75):
        low[d] = line[d]
        close[d] = line[d] + 5
    close[97] = line[97] * 0.95
    low[97] = close[97] - 5
    df = _frame(close, high, low)
    support = next(x for x in find_trendlines(df) if x.kind == "SUPPORT")
    assert support.touches == 2
    assert support.start_date == df["trade_date"].iloc[60].date()
    assert support.break_date == df["trade_date"].iloc[97].date()
    assert support.break_value == pytest.approx(line[97])
    assert support.end_date == df["trade_date"].iloc[n - 1].date()


def test_find_trendlines_stop_at_zero() -> None:
    """線の値が0円以下になる線は、0円になる日で止める。

    線の今日の値が0円以下の線は、離れ具合の条件で外れる。今日の終値が空の場合だけは条件をかけないので引く。
    """
    n = 100
    t = np.arange(n, dtype=float)
    close = 1000 + t
    high, low = close + 5, close - 5
    # 50 と 70 の谷を結ぶ線(400円 → 100円。1日に15円下がる)は、76.67 で0円になる。
    low[50], low[70] = 400.0, 100.0
    # 今日の終値があれば、今日の値が0円以下の線は引かない
    assert all(x.kind != "SUPPORT" for x in find_trendlines(_frame(close, high, low)))
    close[n - 1] = np.nan
    df = _frame(close, high, low)
    support = next(x for x in find_trendlines(df) if x.kind == "SUPPORT")
    assert support.start_date == df["trade_date"].iloc[50].date()
    assert support.end_date == df["trade_date"].iloc[77].date()
    assert support.end_value == 0.0
    assert support.break_date is None
    # 今日の値が0円より上なら、今日で止める
    rising = trendline.TrendLine(trendline.SUPPORT, 0, 10, 100.0, 1.0, 2)
    assert zero_stop(rising, 50) == 50
    # 表示する期間で全て0円以下なら返さない
    assert all(x.kind != "SUPPORT" for x in find_trendlines(df, 90))
