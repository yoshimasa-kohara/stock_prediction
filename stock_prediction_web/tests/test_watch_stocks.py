"""検討中銘柄の API(/api/watch-stocks)のテスト。"""

from datetime import date
from decimal import Decimal

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow
from web_sample import LAST_DAY, pred_days

import const
from api import web_const
from api.routers import screen as screen_module


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """備考の入れ物(銘柄検索と共通)を空にする。"""
    screen_module.note_cache.clear()


def close_of(conn: psycopg.Connection[DictRow], code: str, day: date) -> float:
    row = conn.execute("SELECT close FROM daily_price WHERE code = %s AND trade_date = %s", (code, day)).fetchone()
    assert row is not None
    return float(row["close"])


def add_watch(
    conn: psycopg.Connection[DictRow],
    code: str,
    pred_date: date | None,
    base_price: float | None,
    memo: str = "",
    created_at: str = "2026-07-20 10:00:00+09",
) -> None:
    """検討中銘柄を1行入れる(追加した日を過去にするため、SQL で直接入れる)。"""
    conn.execute(
        """
        INSERT INTO watch_stock (code, memo, pred_date, base_price, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (code, memo, pred_date, None if base_price is None else Decimal(str(base_price)), created_at, created_at),
    )


def test_codes_empty(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    assert client.get("/api/watch-stocks/codes").json() == {"items": []}
    assert client.get("/api/watch-stocks").json() == {"latest_pred_date": LAST_DAY.isoformat(), "rows": []}


def test_put_adds_with_latest_pred_date_and_close(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """追加すると、押した時点の最新の予測日と、その日の終値を残す。"""
    res = client.put("/api/watch-stocks/11110", json={"memo": "  決算の後に買う\n2行目  "})
    assert res.status_code == 200
    assert res.json() == {"code": "11110", "created": True, "message": "検討中銘柄に追加しました"}
    row = sample.execute("SELECT * FROM watch_stock").fetchone()
    assert row is not None
    assert row["code"] == "11110"
    assert row["memo"] == "決算の後に買う\n2行目"  # 前後の空白は除く。改行は残す
    assert row["pred_date"] == LAST_DAY
    assert float(row["base_price"]) == close_of(sample, "11110", LAST_DAY)
    assert row["created_at"] == row["updated_at"]
    assert client.get("/api/watch-stocks/codes").json() == {
        "items": [{"code": "11110", "memo": "決算の後に買う\n2行目"}]
    }


def test_put_existing_updates_memo_only(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """追加済みなら、メモと更新日時だけを直す(pred_date・base_price・created_at は最初のまま)。"""
    first = pred_days()[0]
    add_watch(sample, "11120", first, 123.4, memo="前のメモ")
    res = client.put("/api/watch-stocks/11120", json={"memo": ""})
    assert res.json() == {"code": "11120", "created": False, "message": "メモを保存しました"}
    rows = sample.execute("SELECT * FROM watch_stock").fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["memo"] == ""
    assert row["pred_date"] == first
    assert float(row["base_price"]) == 123.4
    assert row["updated_at"] > row["created_at"]


def test_put_without_body_memo_is_empty(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """メモは省略できる(空の文字)。銘柄コードは小文字でもよい。"""
    sample.execute("INSERT INTO stock_master (code, name, applied_date) VALUES ('130A0', 'A', '2026-07-22')")
    assert client.put("/api/watch-stocks/130a0", json={}).json()["code"] == "130A0"
    row = sample.execute("SELECT memo, base_price FROM watch_stock WHERE code = '130A0'").fetchone()
    assert row is not None
    assert row["memo"] == ""
    assert row["base_price"] is None  # その日の終値がない銘柄


def test_put_no_prediction_yet(client: TestClient, conn: psycopg.Connection[DictRow]) -> None:
    """予測が1件もないときは、pred_date・base_price を NULL にして追加する。一覧も返す。"""
    conn.execute("INSERT INTO stock_master (code, name, applied_date) VALUES ('11110', 'A', '2026-07-22')")
    assert client.put("/api/watch-stocks/11110", json={"memo": "m"}).status_code == 200
    row = conn.execute("SELECT pred_date, base_price FROM watch_stock").fetchone()
    assert row is not None
    assert (row["pred_date"], row["base_price"]) == (None, None)
    body = client.get("/api/watch-stocks").json()
    assert body["latest_pred_date"] is None
    r = body["rows"][0]
    assert (r["name"], r["market"], r["sector33"]) == ("A", "", "")
    for point in (r["picked"], r["latest"]):
        assert point["pred_date"] is None
        assert point["close"] is None
        assert point["has_prediction"] is False
        assert [h["rel_prob"] for h in point["horizons"]] == [None] * len(const.PREDICT_HORIZONS)
        assert point["notes"] == []
    assert r["change_rate"] is None


def test_put_errors(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """銘柄のテーブルにないコードは 404、メモが長すぎるときは 400、メモが文字でないときは 422。"""
    res = client.put("/api/watch-stocks/99990", json={"memo": ""})
    assert res.status_code == 404
    assert res.json() == {"detail": "銘柄が見つかりません"}
    limit = web_const.WATCH_STOCK_MEMO_MAX_LENGTH
    assert client.put("/api/watch-stocks/11110", json={"memo": "あ" * limit}).status_code == 200
    res = client.put("/api/watch-stocks/11110", json={"memo": "あ" * (limit + 1)})
    assert res.status_code == 400
    assert res.json() == {"detail": f"メモは{limit}文字以内にしてください"}
    res = client.put("/api/watch-stocks/11110", json={"memo": 1})
    assert res.status_code == 422
    assert res.json() == {"detail": "入力の値が正しくありません(memo)"}


def test_delete(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    client.put("/api/watch-stocks/11110", json={"memo": ""})
    res = client.delete("/api/watch-stocks/11110")
    assert res.status_code == 200
    assert res.json() == {"code": "11110", "created": False, "message": "検討中銘柄から削除しました"}
    assert sample.execute("SELECT count(*) AS n FROM watch_stock").fetchone() == {"n": 0}
    res = client.delete("/api/watch-stocks/11110")
    assert res.status_code == 404
    assert res.json() == {"detail": "検討中銘柄に入っていません"}


def test_list(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """一覧は追加した日時の新しい順。追加した日と最新の予測を並べ、値上がり率を返す。値は銘柄検索と同じ。"""
    first = pred_days()[0]
    base = close_of(sample, "11110", first)
    add_watch(sample, "11110", first, base, memo="メモ1", created_at="2026-07-20 10:00:00+09")
    add_watch(sample, "11120", LAST_DAY, close_of(sample, "11120", LAST_DAY), created_at="2026-07-22 20:00:00+09")
    # 予測の対象でない銘柄(ETF)。予測の項目は None、終値と値上がり率は出す
    add_watch(sample, "13050", first, close_of(sample, "13050", first), created_at="2026-07-21 09:00:00+09")
    body = client.get("/api/watch-stocks").json()
    assert body["latest_pred_date"] == LAST_DAY.isoformat()
    assert [r["code"] for r in body["rows"]] == ["11120", "13050", "11110"]

    a = body["rows"][2]
    assert (a["name"], a["market"], a["sector33"], a["memo"]) == ("サンプル銀行A", "プライム", "銀行業", "メモ1")
    assert a["created_at"].startswith("2026-07-20T")
    assert a["picked"]["pred_date"] == first.isoformat()
    assert a["picked"]["close"] == base
    assert a["picked"]["has_prediction"] is True
    assert [h["horizon"] for h in a["picked"]["horizons"]] == const.PREDICT_HORIZONS
    latest_close = close_of(sample, "11110", LAST_DAY)
    assert a["latest"]["pred_date"] == LAST_DAY.isoformat()
    assert a["latest"]["close"] == latest_close
    assert a["change_rate"] == pytest.approx(latest_close / base - 1)
    # 最新の予測と備考は、銘柄検索(/api/screen)と同じ
    screen_row = next(r for r in client.get("/api/screen").json()["rows"] if r["code"] == "11110")
    assert a["latest"]["horizons"] == screen_row["horizons"]
    assert a["latest"]["notes"] == screen_row["notes"]
    assert {"text": "ストップ高", "color": "red"} in a["latest"]["notes"]
    # 追加した日の予測と備考は、その予測日の銘柄検索と同じ(ストップ高は最後の日だけ)
    old_row = next(r for r in client.get(f"/api/screen?date={first}").json()["rows"] if r["code"] == "11110")
    assert a["picked"]["horizons"] == old_row["horizons"]
    assert a["picked"]["notes"] == old_row["notes"]
    assert {"text": "ストップ高", "color": "red"} not in a["picked"]["notes"]

    # 追加した日 = 最新の予測日なら、値上がり率は 0
    b = body["rows"][0]
    assert b["picked"]["pred_date"] == b["latest"]["pred_date"] == LAST_DAY.isoformat()
    assert b["change_rate"] == 0
    assert b["picked"]["horizons"] == b["latest"]["horizons"]

    etf = body["rows"][1]
    for point in (etf["picked"], etf["latest"]):
        assert point["has_prediction"] is False
        assert [h["pred_price"] for h in point["horizons"]] == [None] * len(const.PREDICT_HORIZONS)
        assert point["close"] is not None
    assert etf["change_rate"] is not None


def test_list_without_close(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """追加した日の終値がない(NULL)ときと、最新の予測日の終値がないときは、値上がり率を None にする。"""
    add_watch(sample, "11110", pred_days()[0], None)
    add_watch(sample, "11120", LAST_DAY, 100.0)
    sample.execute("DELETE FROM daily_price WHERE code = '11120' AND trade_date = %s", (LAST_DAY,))
    rows = {r["code"]: r for r in client.get("/api/watch-stocks").json()["rows"]}
    assert rows["11110"]["picked"]["close"] is None
    assert rows["11110"]["latest"]["close"] is not None
    assert rows["11110"]["change_rate"] is None
    assert rows["11120"]["latest"]["close"] is None
    assert rows["11120"]["change_rate"] is None


def test_list_deleted_from_master(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """銘柄のテーブルから消えた銘柄(上場廃止など)も、行は返す(銘柄名などは空)。"""
    add_watch(sample, "99990", LAST_DAY, None)
    r = client.get("/api/watch-stocks").json()["rows"][0]
    assert (r["code"], r["name"], r["market"], r["sector33"]) == ("99990", "", "", "")
    assert r["latest"]["has_prediction"] is False
