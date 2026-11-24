"""GET /api/rankings(ランキング)のテスト。

架空のデータ(tests/web_sample.py): 予測する銘柄は 11110・11120・11130・11140・11145 の5つ。
市場比の確率は銘柄の順に下がり(0.80、0.73、…)、どの予測日・予測日数でも同じ。11120 は売買代金が少ない(流動性注意)。
買い候補の順位は、どの予測日も 11110 = 1位、11120 = 2位、11140 = 3位。
"""

from datetime import date
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow
from web_sample import LAST_DAY, PRED_CODES, pred_days, price_days

import const
from api import notes as notes_module
from api import web_const
from api.routers import rankings as rankings_module
from api.routers import screen as screen_module

MAIN = const.MAIN_HORIZON
H5 = const.PREDICT_HORIZONS[0]


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    screen_module.note_cache.clear()


def get(client: TestClient, query: str = "") -> dict[str, Any]:
    res = client.get(f"/api/rankings?{query}")
    assert res.status_code == 200, res.text
    body: dict[str, Any] = res.json()
    return body


def codes(body: dict[str, Any]) -> list[str]:
    return [str(r["code"]) for r in body["rows"]]


def set_rel(
    conn: psycopg.Connection[DictRow], code: str, pred_date: date, rel: str, horizon: int | None = None
) -> None:
    """その銘柄・予測日の市場比の確率を変える(horizon を省略すると全ての予測日数)。"""
    conn.execute(
        "UPDATE prediction SET rel_prob = %s WHERE code = %s AND pred_date = %s AND (%s::int IS NULL OR horizon = %s)",
        (rel, code, pred_date, horizon, horizon),
    )


def test_rankings_default(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """省略すると、最新の予測日・市場比の確率・主となる日数。流動性注意の銘柄を除く。列の値と選択肢を返す。"""
    body = get(client)
    assert (body["pred_date"], body["type"], body["horizon"]) == (LAST_DAY.isoformat(), "rel", MAIN)
    assert (body["code_count"], body["total"], body["offset"], body["has_more"]) == (len(PRED_CODES), 4, 0, False)
    assert (body["prev_pred_date"], body["regular_days"]) == (None, 0)
    # 11120(流動性注意)は出ない
    assert codes(body) == ["11110", "11130", "11140", "11145"]
    assert [r["rank"] for r in body["rows"]] == [1, 2, 3, 4]
    first = body["rows"][0]
    assert (first["name"], first["market"], first["sector33"]) == ("サンプル銀行A", "プライム", "銀行業")
    assert first["value"] == pytest.approx(0.80) and first["sub_value"] is None
    assert (first["fwd_per"], first["pbr"]) == (7.9, 0.4)
    assert first["avg_turnover"] > const.LIQUIDITY_WARN_TURNOVER
    assert [h["horizon"] for h in first["horizons"]] == const.PREDICT_HORIZONS
    assert first["horizons"][0]["rel_prob"] == pytest.approx(0.80)
    assert {"text": "ストップ高", "color": "red", "bold": False} in first["notes"]
    # バリュエーション指標がない銘柄は None
    assert (body["rows"][3]["fwd_per"], body["rows"][3]["pbr"]) == (None, None)
    assert body["market_options"] == ["プライム", "スタンダード", "グロース"]
    assert {"value": "3050", "label": "銀行業"} in body["sector_options"]


def test_rankings_include_illiquid(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """exclude_illiquid=false なら流動性注意の銘柄も出す。判定は備考と同じ条件。"""
    body = get(client, "exclude_illiquid=false")
    assert codes(body) == PRED_CODES
    low = body["rows"][1]
    assert low["avg_turnover"] < const.LIQUIDITY_WARN_TURNOVER
    assert {"text": "流動性注意", "color": "green", "bold": False} in low["notes"]
    # 備考に流動性注意が付く銘柄と、exclude_illiquid で除かれる銘柄は同じ
    noted = {r["code"] for r in body["rows"] if any(n["text"] == "流動性注意" for n in r["notes"])}
    assert noted == set(PRED_CODES) - set(codes(get(client)))


@pytest.mark.parametrize(
    ("query", "expected", "value"),
    [
        ("type=rel", ["11110", "11120", "11130", "11140", "11145"], 0.80),
        (f"type=up&horizon={H5}", ["11110", "11120", "11130", "11140", "11145"], 0.62),
        ("type=ret", ["11110", "11120", "11130", "11140", "11145"], 0.04),
        ("type=down", ["11145", "11140", "11130", "11120", "11110"], 0.52),
    ],
)
def test_rankings_types(
    client: TestClient, sample: psycopg.Connection[DictRow], query: str, expected: list[str], value: float
) -> None:
    """① 市場比の確率 ② 上昇確率 ③ 予測の値上がり率は高い順、④ 下がりそうな銘柄は市場比の確率の低い順。"""
    body = get(client, f"{query}&exclude_illiquid=false")
    assert codes(body) == expected
    assert body["rows"][0]["value"] == pytest.approx(value, abs=1e-4)
    assert all(r["sub_value"] is None for r in body["rows"])


def test_rankings_horizon(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """①〜④ は、選んだ予測日数の値で並べる。その予測日数の予測がない銘柄は出さない。"""
    set_rel(sample, "11145", LAST_DAY, "0.99", H5)
    sample.execute("DELETE FROM prediction WHERE code = '11110' AND pred_date = %s AND horizon = %s", (LAST_DAY, H5))
    body = get(client, f"type=rel&horizon={H5}&exclude_illiquid=false")
    assert codes(body) == ["11145", "11120", "11130", "11140"]
    assert body["horizon"] == H5
    # 主となる日数では、今までどおり
    assert codes(get(client, "exclude_illiquid=false")) == PRED_CODES


def test_rankings_ties_by_code(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """値が同じ銘柄は、銘柄コードの順(大きい順の種類でも、小さい順の種類でも)。"""
    sample.execute("UPDATE prediction SET rel_prob = 0.6 WHERE pred_date = %s", (LAST_DAY,))
    assert codes(get(client, "type=rel&exclude_illiquid=false")) == PRED_CODES
    assert codes(get(client, "type=down&exclude_illiquid=false")) == PRED_CODES
    assert codes(get(client, "type=all&exclude_illiquid=false")) == PRED_CODES


def test_rankings_rise(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """⑤: 選んだ予測日の市場比 − その前の予測日の市場比の大きい順。前の予測日に予測がない銘柄は出さない。"""
    prev = pred_days()[-2]
    set_rel(sample, "11130", prev, "0.56")  # 0.66 → 差 +0.10
    set_rel(sample, "11110", prev, "0.85")  # 0.80 → 差 -0.05
    sample.execute("DELETE FROM prediction WHERE code = '11140' AND pred_date = %s", (prev,))
    body = get(client, "type=rise&exclude_illiquid=false")
    assert body["prev_pred_date"] == prev.isoformat()
    # 差が同じ(0)の 11120・11145 は銘柄コードの順
    assert codes(body) == ["11130", "11120", "11145", "11110"]
    assert [r["value"] for r in body["rows"]] == pytest.approx([0.10, 0.0, 0.0, -0.05])
    # 選んだ予測日数だけで比べる
    set_rel(sample, "11145", prev, "0.10", H5)
    assert codes(get(client, f"type=rise&horizon={H5}&exclude_illiquid=false"))[0] == "11145"
    assert codes(get(client, "type=rise&exclude_illiquid=false")) == ["11130", "11120", "11145", "11110"]


def test_rankings_rise_without_previous(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """前の予測日がない(一番古い予測日)ときは、1件も出さない。"""
    body = get(client, f"type=rise&date={pred_days()[0].isoformat()}&exclude_illiquid=false")
    assert (body["prev_pred_date"], body["total"], body["rows"]) == (None, 0, [])
    assert body["code_count"] == len(PRED_CODES)


def test_rankings_all(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """⑥: 全ての予測日数の市場比のうち一番低い値の高い順。どれかの予測日数がない銘柄は出さない。"""
    set_rel(sample, "11110", LAST_DAY, "0.10", const.PREDICT_HORIZONS[-1])
    sample.execute("DELETE FROM prediction WHERE code = '11120' AND pred_date = %s AND horizon = %s", (LAST_DAY, H5))
    body = get(client, "type=all&exclude_illiquid=false")
    assert codes(body) == ["11130", "11140", "11145", "11110"]
    assert [r["value"] for r in body["rows"]] == pytest.approx([0.66, 0.59, 0.52, 0.10])


def test_rankings_regular(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """⑦: 直近の予測日のうち、買い候補(1〜20位)に入った日数の多い順。同じ日数なら平均順位の良い順。"""
    days = pred_days()
    body = get(client, "type=regular&exclude_illiquid=false")
    # 予測日は3日分しかない(RANKING_REGULAR_DAYS より少ない)
    assert body["regular_days"] == len(days) == 3
    assert codes(body) == ["11110", "11120", "11140"]  # 1日も入っていない 11130・11145 は出ない
    assert [(r["value"], r["sub_value"]) for r in body["rows"]] == [(3, 1), (3, 2), (3, 3)]
    # 11110 は1日だけ 21位(数えない)、11140 は1日だけ買い候補から外れた → どちらも2日。平均順位の良い 11110 が先
    sample.execute("UPDATE prediction SET buy_rank = 21 WHERE code = '11110' AND pred_date = %s", (days[0],))
    sample.execute("UPDATE prediction SET buy_rank = NULL WHERE code = '11140' AND pred_date = %s", (days[1],))
    # 11130 は 11140 と同じ日数・同じ平均順位(銘柄コードの順)
    sample.execute("UPDATE prediction SET buy_rank = 3 WHERE code = '11130' AND pred_date <> %s", (days[1],))
    body = get(client, "type=regular&exclude_illiquid=false")
    assert codes(body) == ["11120", "11110", "11130", "11140"]
    assert [(r["value"], r["sub_value"]) for r in body["rows"]] == [(3, 2), (2, 1), (2, 3), (2, 3)]


def test_rankings_regular_days(
    client: TestClient, sample: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    """数えるのは、選んだ予測日を含む直近 RANKING_REGULAR_DAYS 日分の予測日。その日に予測がない銘柄は出さない。"""
    assert web_const.RANKING_REGULAR_DAYS == 20
    days = pred_days()
    monkeypatch.setattr(web_const, "RANKING_REGULAR_DAYS", 2)
    body = get(client, "type=regular&exclude_illiquid=false")
    assert body["regular_days"] == 2
    assert [(r["code"], r["value"]) for r in body["rows"]] == [("11110", 2), ("11120", 2), ("11140", 2)]
    assert rankings_module.recent_pred_dates(sample, LAST_DAY, 2) == [days[2], days[1]]
    # 予測日を選ぶと、その日以前の予測日で数える(一番古い予測日は1日分だけ)
    body = get(client, f"type=regular&date={days[0].isoformat()}&exclude_illiquid=false")
    assert body["regular_days"] == 1
    assert [(r["code"], r["value"]) for r in body["rows"]] == [("11110", 1), ("11120", 1), ("11140", 1)]
    # 選んだ予測日に予測がない銘柄は、買い候補に入った日があっても出さない
    sample.execute("DELETE FROM prediction WHERE code = '11110' AND pred_date = %s", (LAST_DAY,))
    assert codes(get(client, "type=regular&exclude_illiquid=false")) == ["11120", "11140"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("markets=スタンダード,グロース", ["11120", "11130"]),
        ("sectors=3050", ["11110", "11140", "11145"]),
        ("sectors=3050,3650&markets=プライム", ["11110", "11140", "11145"]),
        # 売買代金の下限(億円)。架空のデータは 11120 だけが少ない
        ("min_turnover=1", ["11110", "11130", "11140", "11145"]),
        ("min_turnover=100000", []),
        # PER の条件を入れると、PER がない銘柄(11130・11145)は出ない
        ("per_max=20", ["11110", "11120", "11140"]),
        ("per_min=10", ["11120", "11140"]),
        ("per_min=10&per_max=12", ["11140"]),
        # PBR の条件を入れると、PBR がない銘柄(11145)は出ない
        ("pbr_max=1", ["11110", "11140"]),
        ("pbr_min=1", ["11120", "11130"]),
        ("pbr_min=0.5&pbr_max=1.5", ["11120", "11140"]),
        # ROE(%)の条件を入れると、ROE がない銘柄(11145)は出ない
        ("min_roe=6", ["11110", "11140"]),
        ("min_roe=0", ["11110", "11120", "11130", "11140"]),
        ("per_max=20&pbr_max=1&min_roe=8", ["11110"]),
    ],
)
def test_rankings_filters(
    client: TestClient, sample: psycopg.Connection[DictRow], query: str, expected: list[str]
) -> None:
    body = get(client, f"{query}&exclude_illiquid=false")
    assert codes(body) == expected
    assert body["total"] == len(expected) and body["code_count"] == len(PRED_CODES)


def test_rankings_negative_per(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """PER の条件を入れたときは、PER がマイナスの銘柄を除く(上限だけの条件でも)。PER の条件がなければ除かない。"""
    sample.execute("UPDATE daily_valuation SET fwd_per = -5 WHERE code = '11140'")
    assert codes(get(client, "per_max=20&exclude_illiquid=false")) == ["11110", "11120"]
    assert codes(get(client, "per_min=-10&exclude_illiquid=false")) == ["11110", "11120"]
    assert codes(get(client, "pbr_max=1&exclude_illiquid=false")) == ["11110", "11140"]
    assert get(client, "exclude_illiquid=false")["rows"][3]["fwd_per"] == -5


def test_rankings_valuation_of_pred_date(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """PER・PBR・ROE は、選んだ予測日の値を使う(架空のデータは最新の日だけに値がある)。"""
    older = pred_days()[0].isoformat()
    body = get(client, f"date={older}&exclude_illiquid=false")
    assert all(r["fwd_per"] is None and r["pbr"] is None for r in body["rows"])
    assert get(client, f"date={older}&pbr_max=100&exclude_illiquid=false")["rows"] == []


def test_rankings_without_prices(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """株価がない銘柄は、売買代金が None。流動性注意にはしない(備考と同じ)。売買代金の下限を入れたときは除く。"""
    sample.execute("DELETE FROM daily_price WHERE code = '11145'")
    body = get(client)
    assert codes(body) == ["11110", "11130", "11140", "11145"]
    assert body["rows"][3]["avg_turnover"] is None
    assert codes(get(client, "min_turnover=0")) == ["11110", "11130", "11140"]


def test_rankings_paging_and_notes(
    client: TestClient, sample: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    """RANKING_PAGE_SIZE 件ずつ返す。備考は表に出す行だけを、銘柄検索と同じメモリの入れ物で計算する。"""
    assert web_const.RANKING_PAGE_SIZE == 50
    monkeypatch.setattr(web_const, "RANKING_PAGE_SIZE", 2)
    computed: list[list[str]] = []
    original = notes_module.stock_notes

    def spy(conn: psycopg.Connection[DictRow], target: date, target_codes: list[str]) -> dict[str, list[str]]:
        computed.append(sorted(target_codes))
        return original(conn, target, target_codes)

    monkeypatch.setattr(notes_module, "stock_notes", spy)
    first = get(client, "exclude_illiquid=false")
    assert (codes(first), first["total"], first["has_more"]) == (["11110", "11120"], 5, True)
    more = get(client, "exclude_illiquid=false&offset=2")
    assert (codes(more), more["offset"], more["has_more"]) == (["11130", "11140"], 2, True)
    assert [r["rank"] for r in more["rows"]] == [3, 4]
    last = get(client, "exclude_illiquid=false&offset=4")
    assert (codes(last), last["has_more"]) == (["11145"], False)
    assert computed == [["11110", "11120"], ["11130", "11140"], ["11145"]]
    # 同じ予測日の2回目は計算しない。銘柄検索が計算した備考も使う
    get(client, "exclude_illiquid=false")
    assert len(computed) == 3
    screen_module.note_cache.clear()
    client.get("/api/screen")
    get(client, "exclude_illiquid=false")
    assert len(computed) == 4


def test_rankings_same_values_as_screen(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """予測の値と備考は、銘柄検索(/api/screen)と同じ。"""
    ranking = {r["code"]: r for r in get(client, "exclude_illiquid=false")["rows"]}
    for row in client.get("/api/screen").json()["rows"]:
        r = ranking[row["code"]]
        assert (r["base_price"], r["horizons"], r["notes"]) == (row["base_price"], row["horizons"], row["notes"])
        assert (r["name"], r["market"], r["sector33"]) == (row["name"], row["market"], row["sector33"])


@pytest.mark.parametrize(
    ("query", "status", "message"),
    [
        ("type=xxx", 400, "ランキングの種類は rel・up・ret・down・rise・all・regular のどれかを指定してください"),
        ("horizon=7", 400, "予測日数は"),
        ("type=regular&horizon=7", 400, "予測日数は"),
        ("offset=-1", 400, "offset は 0 以上を指定してください"),
        ("date=2026-07-19", 404, "この日の予測はありません"),
    ],
)
def test_rankings_bad_params(
    client: TestClient, sample: psycopg.Connection[DictRow], query: str, status: int, message: str
) -> None:
    res = client.get(f"/api/rankings?{query}")
    assert res.status_code == status
    assert message in res.json()["detail"]


def test_rankings_no_prediction(client: TestClient) -> None:
    res = client.get("/api/rankings")
    assert res.status_code == 404
    assert res.json() == {"detail": "予測がまだありません。バッチで予測を実行してください"}


def test_avg_turnovers(sample: psycopg.Connection[DictRow]) -> None:
    """売買代金の平均は、指定日以前の直近 LIQUIDITY_AVG_DAYS 営業日の平均(NULL は 0)。株価がない銘柄は入らない。"""
    days = price_days()
    assert notes_module.avg_turnovers(sample, LAST_DAY, []) == {}
    rows = sample.execute(
        "SELECT turnover FROM daily_price WHERE code = '11110' AND trade_date <= %s ORDER BY trade_date DESC LIMIT %s",
        (days[-5], const.LIQUIDITY_AVG_DAYS),
    ).fetchall()
    expected = sum(float(r["turnover"]) for r in rows) / const.LIQUIDITY_AVG_DAYS
    got = notes_module.avg_turnovers(sample, days[-5], ["11110", "99999"])
    assert got == {"11110": pytest.approx(expected)}
    sample.execute("UPDATE daily_price SET turnover = NULL WHERE code = '11110' AND trade_date = %s", (days[-5],))
    assert notes_module.avg_turnovers(sample, days[-5], ["11110"])["11110"] == pytest.approx(
        expected - float(rows[0]["turnover"]) / const.LIQUIDITY_AVG_DAYS
    )
    assert notes_module.is_low_liquidity(const.LIQUIDITY_WARN_TURNOVER - 1)
    assert not notes_module.is_low_liquidity(const.LIQUIDITY_WARN_TURNOVER)
