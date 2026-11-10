"""保有銘柄の API(/api/holdings)のテスト。"""

from datetime import date, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow
from web_sample import LAST_DAY, add_holding

import const
from api import common
from api.routers import holdings

TODAY = date(2026, 10, 27)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """今日の日付を固定する(購入日・売却日のチェック用)。"""
    monkeypatch.setattr(holdings, "today", lambda: TODAY)
    monkeypatch.setattr(common, "today", lambda: TODAY)


def close_of(conn: psycopg.Connection[DictRow], code: str) -> float:
    row = conn.execute("SELECT close FROM daily_price WHERE code = %s AND trade_date = %s", (code, LAST_DAY)).fetchone()
    assert row is not None
    return float(row["close"])


def test_holdings_empty(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    body = client.get("/api/holdings").json()
    assert body["judge_date"] == LAST_DAY.isoformat()
    assert body["groups"] == []
    assert body["totals"] == {"cost": 0.0, "value": 0.0, "pnl_amount": 0.0, "pnl_rate": None}
    assert body["sold"] == []
    # 売却済みの行がないときの合計(保有中の合計と同じく 0 円、損益(%)は None)
    assert (body["sold_buy_amount"], body["sold_sell_amount"], body["sold_pnl_amount"]) == (0.0, 0.0, 0.0)
    assert body["sold_pnl_rate"] is None


def test_holdings_no_prediction_yet(client: TestClient, conn: psycopg.Connection[DictRow]) -> None:
    """予測がまだなくても一覧は返す(判定は「判定なし」)。"""
    conn.execute("INSERT INTO stock_master (code, name, applied_date) VALUES ('11110', 'A', '2026-07-22')")
    add_holding(conn, "11110", "1000", 100, date(2026, 7, 1))
    body = client.get("/api/holdings").json()
    assert body["judge_date"] is None
    assert body["groups"][0]["judgment"] == "判定なし(本日の予測がありません)"
    assert body["groups"][0]["current"] is None


def test_holdings_group_average_and_judgment(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """買い増しは銘柄ごとに合計の行にまとめ、平均買値で判定と損益を出す。"""
    a = add_holding(sample, "11110", "1000", 100, date(2026, 6, 1))
    b = add_holding(sample, "11110", "1300", 100, date(2026, 6, 29))
    add_holding(sample, "11120", "5000", 200, date(2026, 6, 15))
    body = client.get("/api/holdings").json()
    groups = {g["code"]: g for g in body["groups"]}
    g = groups["11110"]
    assert g["quantity"] == 200
    assert g["avg_buy_price"] == 1150.0
    current = close_of(sample, "11110")
    assert g["current"] == current
    assert g["pnl_rate"] == pytest.approx((current - 1150) / 1150)
    assert g["pnl_amount"] == pytest.approx((current - 1150) * 200)
    assert [lot["holding_id"] for lot in g["lots"]] == [a, b]
    assert g["lots"][0]["pnl_amount"] == pytest.approx((current - 1000) * 100)
    assert g["name"] == "サンプル銀行A"
    assert [h["horizon"] for h in g["horizons"]] == const.PREDICT_HORIZONS
    # 11110 の主となる日数: 市場比 80%・上昇確率 64%以上 → 保持(含み損が3%以上なら注意)
    assert g["judgment"].startswith("保持")
    # 11120 は大きく値下がり(買値 5000) → 損切りラインの備考(判定は変えない)
    g2 = groups["11120"]
    assert g2["notes"][0] == {"text": "損切りライン10%の値を下回っています。", "color": "red"}
    totals = body["totals"]
    assert totals["cost"] == 1150 * 200 + 5000 * 200
    assert totals["pnl_amount"] == pytest.approx(totals["value"] - totals["cost"])


def test_holdings_現在値が判定の日の終値でないときは備考に日付を出す(
    client: TestClient, sample: psycopg.Connection[DictRow]
) -> None:
    """判定の日に取引がない銘柄は、その日以前で一番新しい終値を使い、備考の最初に「MM/DD の終値」を出す。"""
    sample.execute("DELETE FROM daily_price WHERE code = '11110' AND trade_date = %s", (LAST_DAY,))
    prev = sample.execute(
        "SELECT trade_date, close FROM daily_price WHERE code = '11110' ORDER BY trade_date DESC LIMIT 1"
    ).fetchone()
    assert prev is not None
    earlier, earlier_close = prev["trade_date"], float(prev["close"])
    add_holding(sample, "11110", "1000", 100, date(2026, 6, 1))
    add_holding(sample, "11120", "1000", 100, date(2026, 6, 1))
    groups = {g["code"]: g for g in client.get("/api/holdings").json()["groups"]}
    g = groups["11110"]
    assert g["current"] == earlier_close and g["current_date"] == earlier.isoformat()
    assert g["notes"][0] == {"text": f"{earlier:%m/%d} の終値", "color": "orange"}
    # その日の終値がある銘柄には出さない
    assert all("の終値" not in n["text"] for n in groups["11120"]["notes"])


def test_holdings_sold(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """売却済みは購入ごとの行のまま、売値で損益を出す。include_sold=false なら返さない(件数は返す)。"""
    add_holding(sample, "11130", "2000", 100, date(2026, 4, 14), date(2026, 6, 12), "2200")
    body = client.get("/api/holdings").json()
    assert body["groups"] == []
    sold = body["sold"][0]
    assert sold["sell_price"] == 2200
    assert sold["pnl_amount"] == pytest.approx(20000)
    assert sold["pnl_rate"] == pytest.approx(0.1)
    assert sold["held_days"] == 59
    assert sold["current"] == close_of(sample, "11130")
    assert body["sold_pnl_amount"] == pytest.approx(20000)
    hidden = client.get("/api/holdings?include_sold=false").json()
    assert hidden["sold"] == []
    assert hidden["sold_count"] == 1


def test_holdings_sold_totals(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """売却済みの合計: 買値・売値は (値 × 売り株数) の合計。

    一部の売却の行は売った株数で数え、売値が NULL の行は買値で数える。
    """
    # 一部の売却(300株のうち100株を 1200円で売る)
    holding_id = add_holding(sample, "11110", "1000", 300, date(2026, 6, 1))
    client.post(
        f"/api/holdings/{holding_id}/sell", json={"sell_date": "2026-07-23", "quantity": 100, "sell_price": 1200}
    )
    # 全部の売却(2000円 × 100株 → 1800円)
    add_holding(sample, "11130", "2000", 100, date(2026, 4, 14), date(2026, 6, 12), "1800")
    # 売値が NULL の行(買値で数える)
    add_holding(sample, "11120", "500", 200, date(2026, 4, 1), date(2026, 5, 1), None)
    body = client.get("/api/holdings").json()
    buy = 1000 * 100 + 2000 * 100 + 500 * 200
    sell = 1200 * 100 + 1800 * 100 + 500 * 200
    assert body["sold_count"] == 3
    assert body["sold_buy_amount"] == pytest.approx(buy)
    assert body["sold_sell_amount"] == pytest.approx(sell)
    assert body["sold_pnl_amount"] == pytest.approx(sell - buy)
    # 損益の合計は、各行の損益(円)の合計と同じ
    assert body["sold_pnl_amount"] == pytest.approx(sum(r["pnl_amount"] for r in body["sold"]))
    assert body["sold_pnl_rate"] == pytest.approx((sell - buy) / buy)
    # 保有中の行(残りの200株)は売却済みの合計に入らない
    assert body["groups"][0]["quantity"] == 200


def test_holdings_bad_date(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    assert client.get("/api/holdings?date=2026-07-25").status_code == 404


def test_create(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    res = client.post(
        "/api/holdings",
        json={"code": "11110", "buy_price": 1234.5, "quantity": 100, "buy_date": "2026-07-23", "note": " メモ "},
    )
    assert res.status_code == 200
    holding_id = res.json()["holding_id"]
    row = sample.execute("SELECT * FROM holding WHERE holding_id = %s", (holding_id,)).fetchone()
    assert row is not None
    assert float(row["buy_price"]) == 1234.5
    assert row["note"] == "メモ"
    assert row["sell_date"] is None


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"code": "99999"}, "銘柄が見つかりません"),
        ({"buy_price": 0}, "買値は0より大きい値を入れてください"),
        ({"quantity": 0}, "買い株数は1以上を入れてください"),
        ({"buy_date": (TODAY + timedelta(days=1)).isoformat()}, "購入日は今日以前の日付を入れてください"),
        ({"note": "x" * 501}, "メモは500文字以内にしてください"),
    ],
)
def test_create_bad_input(
    client: TestClient, sample: psycopg.Connection[DictRow], body: dict[str, object], message: str
) -> None:
    base = {"code": "11110", "buy_price": 1000, "quantity": 100, "buy_date": "2026-07-01", "note": None}
    res = client.post("/api/holdings", json={**base, **body})
    assert res.status_code == 400
    assert res.json() == {"detail": message}


def test_update(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    holding_id = add_holding(sample, "11110", "1000", 100, date(2026, 6, 1))
    res = client.put(
        f"/api/holdings/{holding_id}",
        json={"buy_price": 1100, "quantity": 200, "buy_date": "2026-06-02", "note": ""},
    )
    assert res.status_code == 200
    row = sample.execute("SELECT * FROM holding WHERE holding_id = %s", (holding_id,)).fetchone()
    assert row is not None
    assert (float(row["buy_price"]), row["quantity"], row["buy_date"], row["note"]) == (
        1100.0,
        200,
        date(2026, 6, 2),
        None,
    )
    assert row["updated_at"] >= row["created_at"]


def test_update_errors(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    body = {"buy_price": 1100, "quantity": 200, "buy_date": "2026-06-02"}
    assert client.put("/api/holdings/999", json=body).json() == {"detail": "保有銘柄が見つかりません"}
    sold_id = add_holding(sample, "11110", "1000", 100, date(2026, 6, 1), date(2026, 7, 1), "1100")
    res = client.put(f"/api/holdings/{sold_id}", json=body)
    assert res.status_code == 400
    assert res.json() == {"detail": "売却済みの行は変更できません"}


def test_sell_all(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """全部の売却は、その行に売却日と売値を入れる。"""
    holding_id = add_holding(sample, "11110", "1000", 300, date(2026, 6, 1))
    res = client.post(
        f"/api/holdings/{holding_id}/sell", json={"sell_date": "2026-07-23", "quantity": 300, "sell_price": 1200}
    )
    assert res.json()["sold_holding_id"] is None
    row = sample.execute("SELECT * FROM holding WHERE holding_id = %s", (holding_id,)).fetchone()
    assert row is not None
    assert row["sell_date"] == date(2026, 7, 23)
    assert float(row["sell_price"]) == 1200
    assert sample.execute("SELECT count(*) AS n FROM holding").fetchone() == {"n": 1}


def test_sell_part(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """一部の売却は、元の行の株数を減らし、売った株数の行を足す。"""
    holding_id = add_holding(sample, "11110", "1000", 300, date(2026, 6, 1))
    sample.execute("UPDATE holding SET note = 'メモ' WHERE holding_id = %s", (holding_id,))
    res = client.post(
        f"/api/holdings/{holding_id}/sell", json={"sell_date": "2026-07-23", "quantity": 100, "sell_price": 1200}
    ).json()
    assert res["message"] == "100株の売却を登録しました(残り 200株は保有中のままです)"
    rows = sample.execute("SELECT * FROM holding ORDER BY holding_id").fetchall()
    assert [(r["quantity"], r["sell_date"], r["note"]) for r in rows] == [
        (200, None, "メモ"),
        (100, date(2026, 7, 23), "メモ"),
    ]
    assert rows[1]["buy_date"] == date(2026, 6, 1)
    assert res["sold_holding_id"] == rows[1]["holding_id"]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"sell_date": "2026-05-31"}, "売却日は購入日以降で、今日以前の日付を入れてください"),
        ({"sell_date": "2026-10-28"}, "売却日は購入日以降で、今日以前の日付を入れてください"),
        ({"quantity": 0}, "売却株数は1〜300株にしてください"),
        ({"quantity": 301}, "売却株数は1〜300株にしてください"),
        ({"sell_price": 0}, "売値は0より大きい値を入れてください"),
    ],
)
def test_sell_bad_input(
    client: TestClient, sample: psycopg.Connection[DictRow], body: dict[str, object], message: str
) -> None:
    holding_id = add_holding(sample, "11110", "1000", 300, date(2026, 6, 1))
    base = {"sell_date": "2026-07-23", "quantity": 100, "sell_price": 1200}
    res = client.post(f"/api/holdings/{holding_id}/sell", json={**base, **body})
    assert res.status_code == 400
    assert res.json() == {"detail": message}


def test_sell_twice(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    holding_id = add_holding(sample, "11110", "1000", 100, date(2026, 6, 1), date(2026, 7, 1), "1100")
    res = client.post(
        f"/api/holdings/{holding_id}/sell", json={"sell_date": "2026-07-23", "quantity": 100, "sell_price": 1200}
    )
    assert res.json() == {"detail": "売却済みの行は変更できません"}


def test_holdings_amounts(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """保有中の金額: 1回の購入、買い増しの合計の行と購入ごとの行(買いの金額・現在の金額)。"""
    add_holding(sample, "11110", "1000", 100, date(2026, 6, 1))
    add_holding(sample, "11110", "1300", 100, date(2026, 6, 29))
    add_holding(sample, "11120", "5000", 200, date(2026, 6, 15))
    groups = {g["code"]: g for g in client.get("/api/holdings").json()["groups"]}
    current = close_of(sample, "11110")
    many = groups["11110"]
    # 合計の行: 平均買値 × 合計の株数、現在値 × 合計の株数
    assert many["buy_amount"] == pytest.approx(1150 * 200)
    assert many["current_amount"] == pytest.approx(current * 200)
    # 購入ごとの行: 買値 × 買い株数、現在値 × 買い株数
    assert [lot["buy_amount"] for lot in many["lots"]] == [100000, 130000]
    assert [lot["current_amount"] for lot in many["lots"]] == [pytest.approx(current * 100)] * 2
    # 1回の購入
    single = groups["11120"]
    assert single["buy_amount"] == 1000000
    assert single["current_amount"] == pytest.approx(close_of(sample, "11120") * 200)
    assert single["lots"][0]["buy_amount"] == 1000000


def test_holdings_amounts_no_close(client: TestClient, conn: psycopg.Connection[DictRow]) -> None:
    """現在値がない(一度も終値がない)銘柄は、現在の金額を None(画面では「-」)にする。買いの金額は出す。"""
    conn.execute("INSERT INTO stock_master (code, name, applied_date) VALUES ('11110', 'A', '2026-07-22')")
    add_holding(conn, "11110", "1000", 100, date(2026, 7, 1))
    g = client.get("/api/holdings").json()["groups"][0]
    assert g["current"] is None and g["current_amount"] is None
    assert g["buy_amount"] == 100000
    assert g["lots"][0]["buy_amount"] == 100000 and g["lots"][0]["current_amount"] is None


def test_holdings_sold_amounts(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """売却済みの金額: 買いの金額 = 買値 × 売り株数、売りの金額 = 売値 × 売り株数(売値が NULL の行は買値で数える)。"""
    add_holding(sample, "11130", "2000", 100, date(2026, 4, 14), date(2026, 6, 12), "2200")
    add_holding(sample, "11120", "500", 200, date(2026, 4, 1), date(2026, 5, 1), None)
    sold = {s["code"]: s for s in client.get("/api/holdings").json()["sold"]}
    assert (sold["11130"]["buy_amount"], sold["11130"]["sell_amount"]) == (200000, 220000)
    assert (sold["11120"]["buy_amount"], sold["11120"]["sell_amount"]) == (100000, 100000)
