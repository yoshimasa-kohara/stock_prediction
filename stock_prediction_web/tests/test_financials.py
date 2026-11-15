"""GET /api/stocks/{code}/financials(銘柄詳細の業績)と、api/financials.py の計算のテスト。"""

from datetime import date, time
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow
from web_sample import LAST_DAY, add_fin, price_days

from api import financials as fin
from api import web_const

CODE = "11110"


def get(client: TestClient, code: str = CODE) -> dict[str, Any]:
    res = client.get(f"/api/stocks/{code}/financials")
    assert res.status_code == 200
    body: dict[str, Any] = res.json()
    return body


# =====================================================================
# 前年比の判定(分かれ道を全て確かめる)
# =====================================================================


@pytest.mark.parametrize(
    ("current", "previous", "kind", "rate"),
    [
        (110.0, 100.0, fin.YOY_PERCENT, 0.1),  # 前年も今期もプラス(増えた)
        (90.0, 100.0, fin.YOY_PERCENT, -0.1),  # 前年も今期もプラス(減った)
        (100.0, 100.0, fin.YOY_PERCENT, 0.0),  # 同じ
        (10.0, -5.0, fin.YOY_TO_PROFIT, None),  # 前年がマイナスで今期がプラス
        (10.0, 0.0, fin.YOY_TO_PROFIT, None),  # 前年が0で今期がプラス
        (-10.0, 5.0, fin.YOY_TO_LOSS, None),  # 前年がプラスで今期がマイナス
        (0.0, 5.0, fin.YOY_TO_LOSS, None),  # 前年がプラスで今期が0
        (-3.0, -5.0, fin.YOY_LOSS_SHRINK, None),  # 赤字が小さくなった
        (-8.0, -5.0, fin.YOY_LOSS_EXPAND, None),  # 赤字が大きくなった
        (-5.0, -5.0, fin.YOY_NONE, None),  # 赤字が同じ
        (None, 5.0, fin.YOY_NONE, None),  # 今期がない
        (5.0, None, fin.YOY_NONE, None),  # 前年がない
        (None, None, fin.YOY_NONE, None),
        (0.0, 0.0, fin.YOY_NONE, None),  # 前年が0で今期も0
        (-1.0, 0.0, fin.YOY_NONE, None),  # 前年が0で今期がマイナス
        (0.0, -1.0, fin.YOY_NONE, None),  # 前年がマイナスで今期が0
    ],
)
def test_judge_yoy(current: float | None, previous: float | None, kind: str, rate: float | None) -> None:
    result = fin.judge_yoy(current, previous)
    assert result.kind == kind
    if rate is None:
        assert result.rate is None
    else:
        assert result.rate == pytest.approx(rate)


def test_months_between_and_labels() -> None:
    assert fin.months_between(date(2026, 3, 31), date(2025, 3, 20)) == 12
    assert fin.months_between(date(2026, 3, 31), date(2025, 12, 31)) == 3
    assert fin.period_label(date(2026, 3, 31), "FY") == "2026年3月期 通期"
    assert fin.period_label(date(2027, 3, 31), "1Q") == "2027年3月期 1Q"


def test_split_multiplier() -> None:
    """開示日より後(until 以前)の調整係数だけを掛ける。"""
    factors = [(date(2026, 1, 10), 0.5), (date(2026, 6, 1), 0.25)]
    assert fin.split_multiplier(factors, date(2025, 12, 31)) == 0.125
    assert fin.split_multiplier(factors, date(2026, 1, 10)) == 0.25  # 開示日と同じ日の分割は掛けない
    assert fin.split_multiplier(factors, date(2025, 12, 31), date(2026, 5, 31)) == 0.5
    assert fin.split_multiplier(factors, date(2026, 6, 1)) == 1.0


@pytest.mark.parametrize(
    ("doc_type", "kind"),
    [
        ("FYFinancialStatements_Consolidated_JP", fin.DOC_KIND_STATEMENT),
        ("EarnForecastRevision", fin.DOC_KIND_EARN_REVISION),
        ("REITEarnForecastRevision", fin.DOC_KIND_EARN_REVISION),
        ("DividendForecastRevision", fin.DOC_KIND_DIVIDEND_REVISION),
        ("REITDividendForecastRevision", fin.DOC_KIND_DIVIDEND_REVISION),
        ("Unknown", fin.DOC_KIND_OTHER),
    ],
)
def test_doc_kind(doc_type: str, kind: str) -> None:
    assert fin.doc_kind(doc_type) == kind


def test_default_limits() -> None:
    """件数の設定値。"""
    assert web_const.FINANCIAL_ANNUAL_LIMIT == 10
    assert web_const.FINANCIAL_QUARTERLY_LIMIT == 8


# =====================================================================
# 表(通期・四半期)
# =====================================================================


def test_financials_no_data(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """開示がない銘柄は、空で返す(404 にしない)。"""
    body = get(client, "13050")
    assert body["annual"] == [] and body["quarterly"] == [] and body["revisions"] == []
    assert body["forecast"] is None and body["dividend_yield"] is None and body["dividend"] is None


def test_financials_not_found(client: TestClient) -> None:
    res = client.get("/api/stocks/99999/financials")
    assert res.status_code == 404
    assert res.json() == {"detail": "銘柄が見つかりません"}


def test_financials_annual_and_yoy(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """通期の表は通期の行だけを新しい順に出す。前年比・営業利益率を計算する。"""
    add_fin(sample, CODE, "a1", date(2024, 5, 10), "FY", date(2024, 3, 31), sales=1000, op=-50, np=-20, eps=10)
    add_fin(sample, CODE, "a2", date(2025, 5, 10), "FY", date(2025, 3, 31), sales=1200, op=60, np=-10, eps=12)
    add_fin(sample, CODE, "a3", date(2026, 5, 10), "FY", date(2026, 3, 31), sales=900, op=-30, np=-40, eps=-5)
    add_fin(sample, CODE, "q1", date(2025, 8, 5), "1Q", date(2026, 3, 31), sales=300, op=20, np=10, eps=3)
    body = get(client)
    annual = body["annual"]
    assert [r["period_label"] for r in annual] == ["2026年3月期 通期", "2025年3月期 通期", "2024年3月期 通期"]
    assert annual[0]["disc_date"] == "2026-05-10"
    assert annual[0]["sales"] == 900 and annual[0]["sales_yoy"] == {"kind": "percent", "rate": pytest.approx(-0.25)}
    assert annual[0]["op_yoy"]["kind"] == "to_loss"
    assert annual[0]["np_yoy"]["kind"] == "loss_expand"
    assert annual[0]["op_margin"] == pytest.approx(-30 / 900)
    assert annual[0]["eps"] == -5
    assert annual[1]["sales_yoy"]["rate"] == pytest.approx(0.2)
    assert annual[1]["op_yoy"]["kind"] == "to_profit"
    assert annual[1]["np_yoy"]["kind"] == "loss_shrink"
    assert annual[1]["op_margin"] == pytest.approx(0.05)
    # 前年がない行は「-」
    assert annual[2]["sales_yoy"] == {"kind": "none", "rate": None}
    # 四半期の表には、通期も入る(新しい順。同じ事業年度の中では 通期 → 1Q)
    assert [r["period_label"] for r in body["quarterly"]] == [
        "2026年3月期 通期", "2026年3月期 1Q", "2025年3月期 通期", "2024年3月期 通期",
    ]  # fmt: skip
    assert body["quarterly"][1]["sales_yoy"]["kind"] == "none"  # 前年の 1Q がない


def test_financials_limits(
    client: TestClient, sample: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    """通期は FINANCIAL_ANNUAL_LIMIT 行、四半期は FINANCIAL_QUARTERLY_LIMIT 行まで。前年比は表に出ない行とも比べる。"""
    monkeypatch.setattr(web_const, "FINANCIAL_ANNUAL_LIMIT", 2)
    monkeypatch.setattr(web_const, "FINANCIAL_QUARTERLY_LIMIT", 3)
    for i, year in enumerate(range(2022, 2027)):
        add_fin(sample, CODE, f"y{year}", date(year, 5, 10), "FY", date(year, 3, 31), sales=100 * (i + 1), op=10, np=5)
        add_fin(sample, CODE, f"q{year}", date(year, 8, 5), "1Q", date(year + 1, 3, 31), sales=10 * (i + 1), op=1, np=1)
    body = get(client)
    assert [r["fy_end"] for r in body["annual"]] == ["2026-03-31", "2025-03-31"]
    assert body["annual"][1]["sales_yoy"]["rate"] == pytest.approx(400 / 300 - 1)
    assert [(r["fy_end"], r["per_type"]) for r in body["quarterly"]] == [
        ("2027-03-31", "1Q"), ("2026-03-31", "FY"), ("2026-03-31", "1Q"),
    ]  # fmt: skip
    assert body["quarterly"][2]["sales_yoy"]["rate"] == pytest.approx(40 / 30 - 1)


def test_financials_correction_uses_newest(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """同じ期の開示が複数ある(訂正)ときは、開示日・開示時刻が一番新しいものを使う(開示番号の順ではない)。"""
    fy = date(2026, 3, 31)
    add_fin(sample, CODE, "z9", date(2026, 5, 10), "FY", fy, sales=1000, op=100, np=50, disc_time=time(15, 0))
    add_fin(sample, CODE, "z5", date(2026, 5, 20), "FY", fy, sales=1010, op=101, np=51, disc_time=time(9, 0))
    # 同じ日は開示時刻が新しいほう(開示番号は小さい)
    add_fin(sample, CODE, "z1", date(2026, 5, 20), "FY", fy, sales=1020, op=102, np=52, disc_time=time(16, 0))
    # 時刻がない開示は 0時とみなす
    add_fin(sample, CODE, "z8", date(2026, 5, 20), "FY", fy, sales=999, op=99, np=49, disc_time=None)
    body = get(client)
    assert len(body["annual"]) == 1
    assert body["annual"][0]["sales"] == 1020
    assert body["annual"][0]["disc_date"] == "2026-05-20"


def test_financials_skips_all_null(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """売上高・営業利益・純利益が全て NULL の開示(予想の修正だけの開示)は、表の行に使わない。"""
    fy = date(2026, 3, 31)
    add_fin(sample, CODE, "s1", date(2026, 5, 10), "FY", fy, sales=1000, op=100, np=50)
    # 後から出た予想の修正(同じ期・通期。値は全て NULL)が、決算の行を隠さない
    add_fin(sample, CODE, "s2", date(2026, 5, 15), "FY", fy, doc_type="EarnForecastRevision", f_sales=2000)
    # 予想の修正しかない期は、表に出ない
    add_fin(sample, CODE, "s3", date(2026, 6, 1), "FY", date(2027, 3, 31), doc_type="EarnForecastRevision", f_op=10)
    # 1つでも値があれば使う
    add_fin(sample, CODE, "s4", date(2025, 5, 10), "FY", date(2025, 3, 31), np=40)
    body = get(client)
    assert [(r["fy_end"], r["sales"], r["np"]) for r in body["annual"]] == [
        ("2026-03-31", 1000, 50), ("2025-03-31", None, 40),
    ]  # fmt: skip
    assert body["annual"][0]["np_yoy"]["rate"] == pytest.approx(0.25)
    assert body["annual"][1]["op_margin"] is None


@pytest.mark.parametrize(
    ("prev_end", "found"),
    [
        (date(2025, 3, 31), True),  # 12か月前
        (date(2025, 4, 30), True),  # 11か月前
        (date(2025, 2, 28), True),  # 13か月前
        (date(2025, 5, 31), False),  # 10か月前
        (date(2025, 1, 31), False),  # 14か月前
    ],
)
def test_financials_previous_year_range(
    client: TestClient, sample: psycopg.Connection[DictRow], prev_end: date, found: bool
) -> None:
    """前年同期は、同じ会計期間の種類で、事業年度の終了日が 11〜13か月前の開示。"""
    add_fin(sample, CODE, "p1", date(2025, 5, 10), "FY", prev_end, sales=1000, op=100, np=50)
    add_fin(sample, CODE, "p2", date(2026, 5, 10), "FY", date(2026, 3, 31), sales=1100, op=100, np=50)
    row = get(client)["annual"][0]
    assert row["sales_yoy"]["kind"] == ("percent" if found else "none")


def test_financials_previous_year_same_type_and_closest(
    client: TestClient, sample: psycopg.Connection[DictRow]
) -> None:
    """前年同期は同じ会計期間の種類だけ。候補が複数あるときは 12か月前に一番近いもの。"""
    add_fin(sample, CODE, "t1", date(2025, 8, 5), "2Q", date(2026, 3, 31), sales=500, op=1, np=1)
    add_fin(sample, CODE, "t2", date(2025, 6, 5), "1Q", date(2026, 2, 28), sales=250, op=1, np=1)  # 13か月前
    add_fin(sample, CODE, "t3", date(2025, 7, 5), "1Q", date(2026, 3, 31), sales=200, op=1, np=1)  # 12か月前
    add_fin(sample, CODE, "t4", date(2026, 8, 5), "1Q", date(2027, 3, 31), sales=300, op=1, np=1)
    row = get(client)["quarterly"][0]
    assert row["period_label"] == "2027年3月期 1Q"
    assert row["sales_yoy"]["rate"] == pytest.approx(0.5)


@pytest.mark.parametrize("per_type", ["4Q", "5Q"])
def test_financials_4q_5q(client: TestClient, sample: psycopg.Connection[DictRow], per_type: str) -> None:
    """決算期を変えたときの 4Q・5Q は四半期の表に入れ、前年比は「-」。通期の表には入れない。"""
    add_fin(sample, CODE, "c1", date(2025, 5, 10), per_type, date(2025, 3, 31), sales=1000, op=100, np=50)
    add_fin(sample, CODE, "c2", date(2026, 5, 10), per_type, date(2026, 3, 31), sales=1100, op=110, np=55)
    add_fin(sample, CODE, "c3", date(2026, 6, 10), "FY", date(2026, 3, 31), sales=1500, op=150, np=75)
    body = get(client)
    assert [r["per_type"] for r in body["quarterly"]] == ["FY", per_type, per_type]
    for r in body["quarterly"][1:]:
        assert r["sales_yoy"]["kind"] == r["op_yoy"]["kind"] == r["np_yoy"]["kind"] == "none"
    assert body["quarterly"][1]["period_label"] == f"2026年3月期 {per_type}"
    assert [r["per_type"] for r in body["annual"]] == ["FY"]


def set_split(conn: psycopg.Connection[DictRow], code: str, day: date, factor: str) -> None:
    """その日に株式分割があったことにする(調整係数を入れる)。"""
    conn.execute(
        "UPDATE daily_price SET adj_factor = %s WHERE code = %s AND trade_date = %s", (Decimal(factor), code, day)
    )


def test_financials_eps_split(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """EPS は、開示日より後の調整係数で株式分割を補正する(開示日より前・同じ日の分割は掛けない)。"""
    days = price_days()
    split_day = days[-30]
    set_split(sample, CODE, split_day, "0.5")
    add_fin(sample, CODE, "e1", days[-60], "FY", date(2025, 3, 31), sales=1000, op=100, np=50, eps=200)
    add_fin(sample, CODE, "e2", split_day, "1Q", date(2026, 3, 31), sales=300, op=30, np=15, eps=40)
    add_fin(sample, CODE, "e3", days[-10], "2Q", date(2026, 3, 31), sales=600, op=60, np=30, eps=80)
    rows = {r["per_type"]: r for r in get(client)["quarterly"]}
    assert rows["FY"]["eps"] == pytest.approx(100)  # 分割の前の開示: 0.5 倍
    assert rows["1Q"]["eps"] == pytest.approx(40)  # 分割の日の開示: そのまま
    assert rows["2Q"]["eps"] == pytest.approx(80)  # 分割の後の開示: そのまま


# =====================================================================
# 会社予想と修正、修正の履歴、予想の配当利回り
# =====================================================================


def items(forecast: dict[str, Any], side: str) -> dict[str, dict[str, Any]]:
    return {i["key"]: i for i in forecast[side]}


def test_forecast_and_revisions(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """今の事業年度の一番新しい予想と、直前の違う値と比べた修正の向き、修正の履歴。"""
    fy = date(2027, 3, 31)
    # 前の事業年度の通期の決算短信(来期の予想が、今の事業年度の期初の予想になる)
    add_fin(sample, CODE, "f0", date(2026, 5, 10), "FY", date(2026, 3, 31), sales=900, op=90, np=45,
            nx_f_sales=1000, nx_f_op=100, nx_f_np=50, nx_f_div_ann=20)  # fmt: skip
    add_fin(sample, CODE, "f1", date(2026, 8, 5), "1Q", fy, sales=250, op=25, np=12,
            f_sales=1000, f_op=100, f_np=50, f_eps=25, f_div_ann=20)  # fmt: skip
    # 業績予想の修正(売上高は上げ、営業利益は下げ、純利益は同じ)
    add_fin(sample, CODE, "f2", date(2026, 10, 1), "FY", fy, doc_type="EarnForecastRevision",
            f_sales=1100, f_op=90, f_np=50, f_eps=25)  # fmt: skip
    # 配当予想の修正(配当だけ)
    add_fin(sample, CODE, "f3", date(2026, 10, 20), "FY", fy, doc_type="DividendForecastRevision", f_div_ann=24)
    # 修正と同じ値の決算短信(修正の向きは、さかのぼって最初の違う値と比べる)
    add_fin(sample, CODE, "f4", date(2026, 11, 5), "2Q", fy, sales=520, op=40, np=24,
            f_sales=1100, f_op=90, f_np=50, f_eps=25, f_div_ann=24)  # fmt: skip
    body = get(client)
    fc = body["forecast"]
    assert fc["fy_end"] == "2027-03-31" and fc["period_label"] == "2027年3月期"
    assert fc["next_period_label"] == "2028年3月期" and fc["disc_date"] == "2026-11-05"
    cur = items(fc, "current")
    assert cur["sales"] == {
        "key": "sales", "value": 1100, "prev_value": 1000, "direction": "up", "revised_date": "2026-10-01",
    }  # fmt: skip
    assert cur["op"]["direction"] == "down" and cur["op"]["prev_value"] == 100
    assert cur["np"] == {"key": "np", "value": 50, "prev_value": None, "direction": None, "revised_date": None}
    assert cur["eps"]["value"] == 25 and cur["eps"]["direction"] is None
    assert cur["div"]["value"] == 24 and cur["div"]["direction"] == "up" and cur["div"]["revised_date"] == "2026-10-20"
    # 来期の予想は、今の事業年度の開示にはない。1株あたり利益はいつも None
    assert all(i["value"] is None for i in fc["next"])
    assert [i["key"] for i in fc["next"]] == ["sales", "op", "np", "eps", "div"]
    # 修正の履歴(新しい順)。最初の予想(期初の予想)と、値が変わった開示だけ
    # (期初の予想と同じ値の第1四半期の決算短信、配当だけの修正、同じ値の決算短信は出ない)
    assert body["revisions"] == [
        {
            "disc_date": "2026-10-01", "doc_kind": "earn_revision", "sales": 1100, "op": 90, "np": 50,
            "sales_direction": "up", "op_direction": "down", "np_direction": None, "direction": "mixed",
        },
        {
            "disc_date": "2026-05-10", "doc_kind": "initial", "sales": 1000, "op": 100, "np": 50,
            "sales_direction": None, "op_direction": None, "np_direction": None, "direction": None,
        },
    ]  # fmt: skip


@pytest.mark.parametrize(("f_sales", "f_op", "direction"), [(1100, 120, "up"), (900, 80, "down")])
def test_initial_forecast_revised_at_1q(
    client: TestClient, sample: psycopg.Connection[DictRow], f_sales: int, f_op: int, direction: str
) -> None:
    """第1四半期の決算で期初の予想を上げた・下げたときは、期初の予想と比べて修正として出す。EPS は比べない。"""
    fy = date(2027, 3, 31)
    add_fin(sample, CODE, "i0", date(2026, 5, 10), "FY", date(2026, 3, 31), sales=900, op=90, np=45,
            nx_f_sales=1000, nx_f_op=100, nx_f_np=50, nx_f_div_ann=20)  # fmt: skip
    add_fin(sample, CODE, "i1", date(2026, 8, 5), "1Q", fy, sales=250, op=25, np=12,
            f_sales=f_sales, f_op=f_op, f_np=50, f_eps=25, f_div_ann=20)  # fmt: skip
    body = get(client)
    cur = items(body["forecast"], "current")
    assert cur["sales"] == {
        "key": "sales", "value": f_sales, "prev_value": 1000, "direction": direction, "revised_date": "2026-08-05",
    }  # fmt: skip
    assert cur["op"]["direction"] == direction and cur["op"]["prev_value"] == 100
    assert cur["np"]["direction"] is None and cur["div"]["direction"] is None
    assert cur["eps"] == {"key": "eps", "value": 25, "prev_value": None, "direction": None, "revised_date": None}
    assert body["revisions"] == [
        {
            "disc_date": "2026-08-05", "doc_kind": "statement", "sales": f_sales, "op": f_op, "np": 50,
            "sales_direction": direction, "op_direction": direction, "np_direction": None, "direction": direction,
        },
        {
            "disc_date": "2026-05-10", "doc_kind": "initial", "sales": 1000, "op": 100, "np": 50,
            "sales_direction": None, "op_direction": None, "np_direction": None, "direction": None,
        },
    ]  # fmt: skip


def test_initial_forecast_same_value(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """期初の予想と同じ値なら修正にしない。履歴は期初の予想の1行だけ。訂正があれば一番新しい決算短信を使う。"""
    fy = date(2027, 3, 31)
    add_fin(sample, CODE, "s0", date(2026, 5, 10), "FY", date(2026, 3, 31), sales=900, op=90, np=45,
            nx_f_sales=1200, nx_f_op=120, nx_f_np=60)  # fmt: skip
    # 通期の決算短信の訂正(来期の予想を直した)。期初の予想はこちら
    add_fin(sample, CODE, "s0b", date(2026, 5, 20), "FY", date(2026, 3, 31), sales=900, op=90, np=45,
            nx_f_sales=1000, nx_f_op=100, nx_f_np=50)  # fmt: skip
    add_fin(sample, CODE, "s1", date(2026, 8, 5), "1Q", fy, sales=250, op=25, np=12, f_sales=1000, f_op=100, f_np=50)
    body = get(client)
    cur = items(body["forecast"], "current")
    assert all(cur[k]["direction"] is None and cur[k]["prev_value"] is None for k in ("sales", "op", "np"))
    assert [(r["disc_date"], r["doc_kind"], r["sales"]) for r in body["revisions"]] == [("2026-05-20", "initial", 1000)]


def test_initial_forecast_not_found(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """前の事業年度の通期の決算短信がない(2年前の決算短信、前の事業年度の業績予想の修正だけ)ときは、
    今までどおり今の事業年度の開示だけで比べる。来期の予想が全て NULL の決算短信も使わない。"""
    fy = date(2027, 3, 31)
    add_fin(sample, CODE, "u0", date(2025, 5, 10), "FY", date(2025, 3, 31), sales=800, nx_f_sales=900)
    add_fin(sample, CODE, "u1", date(2026, 2, 1), "FY", date(2026, 3, 31), doc_type="EarnForecastRevision",
            f_sales=950)  # fmt: skip
    add_fin(sample, CODE, "u2", date(2026, 8, 5), "1Q", fy, sales=250, op=25, np=12, f_sales=1000, f_op=100, f_np=50)
    body = get(client)
    assert items(body["forecast"], "current")["sales"]["direction"] is None
    assert [(r["disc_date"], r["doc_kind"]) for r in body["revisions"]] == [("2026-08-05", "statement")]
    # 前の事業年度の通期の決算短信があっても、来期の予想が全て NULL なら使わない
    add_fin(sample, CODE, "u3", date(2026, 5, 10), "FY", date(2026, 3, 31), sales=900, op=90, np=45)
    body = get(client)
    assert [(r["disc_date"], r["doc_kind"]) for r in body["revisions"]] == [("2026-08-05", "statement")]


def test_initial_forecast_dividend_split(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """期初の予想の配当も、開示日より後の分割を補正してから比べる。"""
    days = price_days()
    set_split(sample, CODE, days[-30], "0.5")
    fy = date(2027, 3, 31)
    # 分割の前の期初の予想(40円 → 分割の後の基準では 20円)
    add_fin(sample, CODE, "v0", days[-60], "FY", date(2026, 3, 31), sales=900, nx_f_sales=1000, nx_f_div_ann=40)
    add_fin(sample, CODE, "v1", days[-10], "1Q", fy, sales=250, f_sales=1000, f_div_ann=20)
    cur = items(get(client)["forecast"], "current")
    assert cur["div"]["value"] == pytest.approx(20) and cur["div"]["direction"] is None
    add_fin(sample, CODE, "v2", days[-5], "FY", fy, doc_type="DividendForecastRevision", f_div_ann=18)
    cur = items(get(client)["forecast"], "current")
    assert cur["div"]["direction"] == "down" and cur["div"]["prev_value"] == pytest.approx(20)
    # 今の事業年度に配当の予想がなければ、期初の予想(補正した値)で配当利回りを出す
    sample.execute("DELETE FROM fin_summary WHERE disc_no IN ('v1', 'v2')")
    add_fin(sample, CODE, "v3", days[-10], "1Q", fy, sales=250, f_sales=1000)
    body = get(client)
    assert body["dividend"]["dividend"] == pytest.approx(20) and body["dividend"]["is_next"] is False
    assert body["dividend"]["disc_date"] == days[-60].isoformat()


def test_find_initial_forecast_closest(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """候補の期が複数(決算期の変更など)なら、12か月前に一番近い期。範囲(11〜13か月前)の外は使わない。"""
    add_fin(sample, CODE, "c0", date(2025, 6, 1), "FY", date(2025, 12, 31), sales=1, nx_f_sales=700)  # 15か月前
    add_fin(sample, CODE, "c1", date(2026, 3, 1), "FY", date(2026, 2, 28), sales=1, nx_f_sales=800)  # 13か月前
    add_fin(sample, CODE, "c2", date(2026, 5, 1), "FY", date(2026, 3, 31), sales=1, nx_f_sales=900)  # 12か月前
    add_fin(sample, CODE, "c3", date(2026, 8, 5), "1Q", date(2027, 3, 31), sales=250, f_sales=900)
    rev = get(client)["revisions"]
    assert [(r["disc_date"], r["doc_kind"], r["sales"]) for r in rev] == [("2026-05-01", "initial", 900)]


@pytest.mark.parametrize(("f_op", "direction"), [(120, "up"), (80, "down")])
def test_revision_direction(client: TestClient, sample: psycopg.Connection[DictRow], f_op: int, direction: str) -> None:
    """変わった項目が全て同じ向きなら、行の向きもその向き。値がない項目は比べない。"""
    fy = date(2027, 3, 31)
    add_fin(sample, CODE, "r1", date(2026, 8, 5), "1Q", fy, sales=250, op=25, np=12, f_sales=1000, f_op=100, f_np=50)
    add_fin(sample, CODE, "r2", date(2026, 10, 1), "FY", fy, doc_type="EarnForecastRevision", f_op=f_op)
    # 予想が全て NULL の修正(半期の予想の修正など)は履歴に出ない
    add_fin(sample, CODE, "r3", date(2026, 10, 2), "2Q", fy, doc_type="EarnForecastRevision")
    rev = get(client)["revisions"]
    assert len(rev) == 2
    assert rev[0]["direction"] == direction and rev[0]["op_direction"] == direction
    assert rev[0]["sales"] is None and rev[0]["sales_direction"] is None


def test_forecast_after_fy_statement(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """通期の決算が出た後は、終わった年度の予想を今期の予想に出さず、来期の予想を出す。配当利回りは来期の予想を使う。"""
    fy = date(2026, 3, 31)
    add_fin(sample, CODE, "g1", date(2026, 2, 5), "3Q", fy, sales=750, op=75, np=37,
            f_sales=1000, f_op=100, f_np=50, f_eps=25, f_div_ann=20)  # fmt: skip
    add_fin(sample, CODE, "g2", date(2026, 5, 10), "FY", fy, sales=1010, op=101, np=51,
            nx_f_sales=1200, nx_f_op=130, nx_f_np=60, nx_f_div_ann=30)  # fmt: skip
    # 通期の決算の訂正(来期の予想を下げた)
    add_fin(sample, CODE, "g3", date(2026, 5, 20), "FY", fy, sales=1010, op=101, np=51,
            nx_f_sales=1200, nx_f_op=120, nx_f_np=60, nx_f_div_ann=30)  # fmt: skip
    body = get(client)
    fc = body["forecast"]
    assert fc["period_label"] == "2026年3月期" and fc["disc_date"] == "2026-05-20"
    assert all(i["value"] is None for i in fc["current"])
    nxt = items(fc, "next")
    assert nxt["sales"]["value"] == 1200 and nxt["sales"]["direction"] is None
    assert nxt["op"] == {
        "key": "op",
        "value": 120,
        "prev_value": 130,
        "direction": "down",
        "revised_date": "2026-05-20",
    }
    assert nxt["eps"]["value"] is None
    assert body["revisions"] == []
    row = sample.execute(
        "SELECT close FROM daily_price WHERE code = %s AND trade_date = %s", (CODE, LAST_DAY)
    ).fetchone()
    assert row is not None
    close = float(row["close"])
    assert body["dividend"] == {
        "dividend": 30, "price": close, "price_date": LAST_DAY.isoformat(), "disc_date": "2026-05-20", "is_next": True,
    }  # fmt: skip
    assert body["dividend_yield"] == pytest.approx(30 / close)


def test_dividend_yield_split(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """年間配当の予想と1株あたり利益の予想は、開示日より後の調整係数で分割を補正する。修正の向きも補正してから比べる。"""
    days = price_days()
    set_split(sample, CODE, days[-30], "0.5")
    fy = date(2027, 3, 31)
    # 分割の前の予想(配当 40円・EPS 100円 → 分割の後の基準では 20円・50円)
    add_fin(sample, CODE, "d1", days[-60], "1Q", fy, sales=250, op=25, np=12, f_div_ann=40, f_eps=100)
    body = get(client)
    close = body["dividend"]["price"]
    assert body["dividend"]["dividend"] == pytest.approx(20) and body["dividend"]["is_next"] is False
    assert body["dividend_yield"] == pytest.approx(20 / close)
    cur = items(body["forecast"], "current")
    assert cur["div"]["value"] == pytest.approx(20) and cur["eps"]["value"] == pytest.approx(50)
    # 分割の後に、同じ水準(20円)の予想を出し直しても、修正にはしない。22円にしたら上方修正
    add_fin(sample, CODE, "d2", days[-10], "2Q", fy, sales=500, op=50, np=25, f_div_ann=20, f_eps=50)
    cur = items(get(client)["forecast"], "current")
    assert cur["div"]["direction"] is None and cur["eps"]["direction"] is None
    add_fin(sample, CODE, "d3", days[-5], "FY", fy, doc_type="DividendForecastRevision", f_div_ann=22)
    cur = items(get(client)["forecast"], "current")
    assert cur["div"] == {
        "key": "div", "value": 22, "prev_value": pytest.approx(20), "direction": "up",
        "revised_date": days[-5].isoformat(),
    }  # fmt: skip


def test_dividend_none(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """配当の予想がなければ、配当利回りは None。株価がない銘柄も None。"""
    add_fin(sample, CODE, "n1", date(2026, 8, 5), "1Q", date(2027, 3, 31), sales=250, op=25, np=12, f_sales=1000)
    body = get(client)
    assert body["dividend_yield"] is None and body["dividend"] is None
    sample.execute(
        "INSERT INTO stock_master (code, name, market_code, applied_date) VALUES ('22220', '株価なし', '0111', %s)",
        (LAST_DAY,),
    )
    add_fin(sample, "22220", "n2", date(2026, 8, 5), "1Q", date(2027, 3, 31), sales=250, f_div_ann=10)
    body = get(client, "22220")
    assert body["dividend_yield"] is None and len(body["quarterly"]) == 1


def test_dividend_without_prediction(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """予測が1件もないときは、一番新しい終値で配当利回りを出す。"""
    sample.execute("DELETE FROM prediction")
    add_fin(sample, CODE, "w1", date(2026, 8, 5), "1Q", date(2027, 3, 31), sales=250, f_div_ann=10)
    body = get(client)
    assert body["dividend"]["price_date"] == LAST_DAY.isoformat()


def test_no_fiscal_year(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """事業年度の終了日がない開示だけのときは、表も予想も出さない。"""
    add_fin(sample, CODE, "x1", date(2026, 8, 5), None, None, sales=250, f_sales=1000, f_div_ann=10)
    body = get(client)
    assert body["annual"] == [] and body["quarterly"] == []
    assert body["forecast"] is None and body["revisions"] == [] and body["dividend"] is None
