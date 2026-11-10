"""GET /api/candidates、/api/candidates/range、/api/candidates/results のテスト。"""

from datetime import date

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow
from web_sample import LAST_DAY, MODEL_VERSION, PRED_CODES, pred_days

import const
import core.notify_core as notify_core
from core.market_state_core import IndexTrend, IndexTrendItem, MarketState


def test_candidates_latest(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """日付を省略すると最新の予測日。買い候補を順位の順に、メールと同じ備考を付けて返す。"""
    body = client.get("/api/candidates").json()
    assert body["pred_date"] == LAST_DAY.isoformat()
    assert body["model_version"] == MODEL_VERSION
    assert body["code_count"] == len(PRED_CODES)
    assert [(c["rank"], c["code"]) for c in body["candidates"]] == [(1, "11110"), (2, "11120"), (3, "11140")]
    first = body["candidates"][0]
    assert first["name"] == "サンプル銀行A"
    assert first["market"] == "プライム"
    assert [h["horizon"] for h in first["horizons"]] == const.PREDICT_HORIZONS
    texts = [n["text"] for n in first["notes"]]
    assert texts[0] == "ストップ高"
    assert {"text": "PBR0.5倍割れ(割安)", "color": "red"} in first["notes"]
    second = body["candidates"][1]
    assert {"text": "流動性注意", "color": "green"} in second["notes"]


def test_candidates_fallback_note(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """上昇確率が50%未満の銘柄がある方の買い候補にだけ、メールと同じ注記を返す。"""
    note = "本日は上昇確率50%以上の銘柄が足りないため、上昇確率の上位25%で選びました"
    assert client.get("/api/candidates").json()["fallback_note"] is None
    assert client.get("/api/candidates?kind=sector").json()["fallback_note"] is None
    sample.execute(
        "UPDATE prediction SET up_prob = 0.45 WHERE code = '11110' AND pred_date = %s AND horizon = %s",
        (LAST_DAY, const.MAIN_HORIZON),
    )
    assert client.get("/api/candidates").json()["fallback_note"] == note


def test_candidates_market_state(
    client: TestClient, sample: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    """相場の状態。計算できない日は null、計算できる日は state・above25・ret20 を返す。"""
    assert "market_state" in client.get("/api/candidates").json()
    monkeypatch.setattr(notify_core, "load_market_state", lambda conn, d: None)
    assert client.get("/api/candidates").json()["market_state"] is None
    monkeypatch.setattr(notify_core, "load_market_state", lambda conn, d: MarketState("弱い", 0.321, -0.021))
    monkeypatch.setattr(notify_core, "load_index_trend", lambda conn, d: None)
    assert client.get("/api/candidates").json()["market_state"] == {
        "state": "弱い", "above25": 0.321, "ret20": -0.021, "index_trend": None
    }  # fmt: skip
    assert client.get("/api/candidates?kind=sector").json()["market_state"]["state"] == "弱い"


def test_candidates_index_trend(
    client: TestClient, sample: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    """日経平均の向き。出さない日は null、出す日は10・20・60日それぞれの日数・向き・傾きを返す。"""
    monkeypatch.setattr(notify_core, "load_market_state", lambda conn, d: MarketState("弱い", 0.321, -0.021))
    # サンプルには日経平均がないので、出さない(null)
    assert client.get("/api/candidates").json()["market_state"]["index_trend"] is None
    trend = IndexTrend(
        (
            IndexTrendItem(10, "上向き", 0.0021),
            IndexTrendItem(20, "横ばい", 0.0003),
            IndexTrendItem(60, "下向き", -0.0008),
        )
    )
    monkeypatch.setattr(notify_core, "load_index_trend", lambda conn, d: trend)
    assert client.get("/api/candidates").json()["market_state"]["index_trend"] == [
        {"days": 10, "direction": "上向き", "slope": 0.0021},
        {"days": 20, "direction": "横ばい", "slope": 0.0003},
        {"days": 60, "direction": "下向き", "slope": -0.0008},
    ]


def test_candidates_by_date(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    d = pred_days()[0]
    body = client.get(f"/api/candidates?date={d.isoformat()}").json()
    assert body["pred_date"] == d.isoformat()


def test_candidates_no_prediction_day(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """予測がない日は 404 とメッセージを返す。"""
    res = client.get("/api/candidates?date=2026-07-25")
    assert res.status_code == 404
    assert res.json() == {"detail": "この日の予測はありません(土日・祝日、またはバッチが動いていない日)"}


def test_candidates_no_prediction_at_all(client: TestClient) -> None:
    res = client.get("/api/candidates")
    assert res.status_code == 404
    assert "予測がまだありません" in res.json()["detail"]


def test_results(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """期間の予測日ごとの買い候補と、実際の株価(答え合わせ済みの日数だけ)、まとめ行の値。"""
    days = pred_days()
    body = client.get(f"/api/candidates/results?from={days[0].isoformat()}&to={days[-1].isoformat()}").json()
    assert [d["pred_date"] for d in body["days"]] == [d.isoformat() for d in days]
    first = body["days"][0]
    assert [r["code"] for r in first["rows"]] == ["11110", "11120", "11140"]
    cells = {c["horizon"]: c for c in first["rows"][0]["cells"]}
    assert cells[const.PREDICT_HORIZONS[0]]["actual"] is not None
    assert cells[const.MAIN_HORIZON]["actual"] is None
    assert first["summary"] == [{"horizon": const.PREDICT_HORIZONS[0], "market_median": 0.01, "beat_count": 2}]
    # 備考はメールと同じ(予測日ごとに計算する)
    assert {"text": "流動性注意", "color": "green"} in first["rows"][1]["notes"]


def test_results_default_period(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """省略すると、終了日 = 最新の株価の日の10営業日前、開始日 = 終了日の10営業日前(営業日は株価がある日で数える)。

    最新の株価の日は 2026-07-22。7/20 は海の日(休日)なので、10営業日前は 7/7、さらに10営業日前は 6/23。
    """
    body = client.get("/api/candidates/results").json()
    assert body["date_to"] == "2026-07-07"
    assert body["date_from"] == "2026-06-23"
    assert len(body["days"]) == len([d for d in pred_days() if date(2026, 6, 23) <= d <= date(2026, 7, 7)])


def test_results_default_period_without_price(client: TestClient) -> None:
    """株価も予測もなければ、今日の10営業日前〜その10営業日前で、空の一覧を返す。"""
    body = client.get("/api/candidates/results").json()
    assert body["days"] == []
    assert body["date_from"] < body["date_to"]


def test_results_too_long(client: TestClient) -> None:
    res = client.get("/api/candidates/results?from=2026-01-01&to=2026-07-22")
    assert res.status_code == 400
    assert res.json() == {"detail": "期間は3か月以内にしてください"}


def test_candidates_sector(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """kind=sector で買い候補(業種分散)。業種は予測した時点の33業種の名前。"""
    body = client.get("/api/candidates?kind=sector").json()
    assert body["kind"] == "sector"
    assert [(c["rank"], c["code"], c["sector"]) for c in body["candidates"]] == [
        (1, "11110", "銀行業"),
        (2, "11120", "卸売業"),
        (3, "11130", "電気機器"),
    ]
    # 備考は買い候補と同じ計算(11130 は赤字予想)
    assert {"text": "赤字予想(業績注意)", "color": "blue"} in body["candidates"][2]["notes"]
    # 省略すると買い候補(top)。業種の列も返す
    top = client.get("/api/candidates").json()
    assert top["kind"] == "top" and top["candidates"][0]["sector"] == "銀行業"


def test_candidates_range(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """期間の予測がある日ごとに、/api/candidates と同じ中身を、古い日が先で返す。"""
    days = pred_days()
    body = client.get(f"/api/candidates/range?from={days[0].isoformat()}&to={days[-1].isoformat()}").json()
    assert [d["pred_date"] for d in body] == [d.isoformat() for d in days]
    assert all(d["kind"] == "top" for d in body)
    for d in body:
        assert d == client.get(f"/api/candidates?date={d['pred_date']}").json()
    assert [c["code"] for c in body[0]["candidates"]] == ["11110", "11120", "11140"]


def test_candidates_range_skips_days_without_prediction(
    client: TestClient, sample: psycopg.Connection[DictRow]
) -> None:
    """予測がない日は含めない。期間に予測が1日もなければ、404 にせず空の配列。"""
    days = pred_days()
    # 予測日の間(営業日でない日)や、予測より前だけの期間
    body = client.get(f"/api/candidates/range?from={days[0].isoformat()}&to={days[1].isoformat()}").json()
    assert [d["pred_date"] for d in body] == [days[0].isoformat(), days[1].isoformat()]
    res = client.get("/api/candidates/range?from=2026-07-25&to=2026-07-31")
    assert res.status_code == 200
    assert res.json() == []


def test_candidates_range_sector(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """kind=sector なら、業種分散の買い候補を日ごとに返す。"""
    days = pred_days()
    body = client.get(f"/api/candidates/range?from={days[0].isoformat()}&to={days[-1].isoformat()}&kind=sector").json()
    assert [d["kind"] for d in body] == ["sector"] * len(days)
    assert [c["code"] for c in body[0]["candidates"]] == ["11110", "11120", "11130"]


def test_candidates_range_fallback_note(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """上昇確率の条件を置き換えた日にだけ fallback_note が付く(日ごとに計算する)。"""
    sample.execute(
        "UPDATE prediction SET up_prob = 0.45 WHERE code = '11110' AND pred_date = %s AND horizon = %s",
        (LAST_DAY, const.MAIN_HORIZON),
    )
    days = pred_days()
    body = client.get(f"/api/candidates/range?from={days[0].isoformat()}&to={days[-1].isoformat()}").json()
    assert [d["fallback_note"] is not None for d in body] == [False] * (len(days) - 1) + [True]


def test_candidates_range_errors(client: TestClient) -> None:
    """from・to は必須。開始日が終了日より後なら 400。1か月を超えたら 400。ちょうど1か月は通る。"""
    for path in (
        "/api/candidates/range",
        "/api/candidates/range?from=2026-07-01",
        "/api/candidates/range?to=2026-07-01",
    ):
        res = client.get(path)
        assert res.status_code == 400
        assert res.json() == {"detail": "from と to を指定してください"}
    res = client.get("/api/candidates/range?from=2026-07-22&to=2026-07-01")
    assert (res.status_code, res.json()) == (400, {"detail": "開始日が終了日より後になっています"})
    res = client.get("/api/candidates/range?from=2026-06-21&to=2026-07-22")
    assert (res.status_code, res.json()) == (400, {"detail": "期間は1か月以内にしてください"})
    assert client.get("/api/candidates/range?from=2026-06-22&to=2026-07-22").status_code == 200
    # 2/28 の1か月前は 1/28、月末のない月は末日(RESULTS_MAX_MONTHS と同じ決め方)
    assert client.get("/api/candidates/range?from=2026-01-31&to=2026-02-28").status_code == 200


def test_candidates_bad_kind(client: TestClient) -> None:
    res = client.get("/api/candidates/range?from=2026-07-01&to=2026-07-22&kind=x")
    assert res.status_code == 400
    for path in ("/api/candidates?kind=x", "/api/candidates/results?kind=x"):
        res = client.get(path)
        assert res.status_code == 400
        assert res.json() == {"detail": "kind は top か sector を指定してください"}


def test_results_sector(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """kind=sector の買い候補(業種分散)と実際の株価。まとめ行の中央値を上回った銘柄は TOP_SECTOR の集計の値。"""
    days = pred_days()
    body = client.get(
        f"/api/candidates/results?from={days[0].isoformat()}&to={days[-1].isoformat()}&kind=sector"
    ).json()
    assert body["kind"] == "sector"
    first = body["days"][0]
    assert [r["code"] for r in first["rows"]] == ["11110", "11120", "11130"]
    assert first["summary"] == [{"horizon": const.PREDICT_HORIZONS[0], "market_median": 0.01, "beat_count": 1}]
