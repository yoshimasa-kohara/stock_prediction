"""画面イメージの撮影の道具(screenshot/)のテスト。

架空の値(make_sample_db.build)は、テスト用の DB に実際に入れて確かめる(全てのテーブルを作り直すので、十数秒かかる)。
撮る処理(capture.py)はブラウザーを使うので、ここでは画面の一覧などの部品だけを確かめる。
"""

import re
from datetime import date

import psycopg
import pytest
from psycopg.rows import DictRow

import const
from screenshot import capture, db_switch, make_sample_db


def test_use_test_db_sets_db_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_DB_NAME", raising=False)
    monkeypatch.setenv("DB_NAME", "x")
    assert db_switch.use_test_db() == db_switch.DEFAULT_TEST_DB_NAME
    import os

    assert os.environ["DB_NAME"] == db_switch.DEFAULT_TEST_DB_NAME


def test_use_test_db_refuses_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_DB_NAME", db_switch.PRODUCTION_DB_NAME)
    with pytest.raises(RuntimeError, match="本番のDB"):
        db_switch.use_test_db()


def test_check_test_db(conn: psycopg.Connection[DictRow]) -> None:
    assert db_switch.check_test_db(conn) != db_switch.PRODUCTION_DB_NAME


def test_tick_round() -> None:
    assert make_sample_db.tick_round(1234.4) == 1234.0
    assert make_sample_db.tick_round(3012.0) == 3010.0
    assert make_sample_db.tick_round(0.2) == 1.0


def test_display_code() -> None:
    assert capture.display_code("11110") == "1111"
    assert capture.display_code("11145") == "11145"


def test_build_shots_names() -> None:
    days = [date(2026, 1, 1)] * 70
    ctx = capture.CaptureContext(pred_days=days, detail_code="11110", lot_code="11120", lot_close=1000.0)
    shots = capture.build_shots(ctx)
    # 11画面(04 と 05 は2枚ずつ)
    assert sorted({s.no for s in shots}) == [f"{n:02d}" for n in range(1, 12)]
    assert len(shots) == 13
    assert "08_銘柄検索" in [s.name for s in shots]
    assert all(s.path().startswith("/") for s in shots)


@pytest.fixture(scope="module")
def built() -> make_sample_db.SampleData:
    """架空の値を1回だけ入れる(このファイルのテストで共通に使う)。"""
    from common import db

    with db.connect() as c:
        return make_sample_db.build(c)


def test_build_is_fictitious(built: make_sample_db.SampleData) -> None:
    """銘柄名は「サンプル」で始まり、銘柄コードは実在の銘柄と重ならない範囲(11110〜)。"""
    assert len(built.stocks) == len(make_sample_db.STOCK_DEFS)
    assert all(s.d.name.startswith("サンプル") for s in built.stocks)
    codes = [int(s.code) for s in built.stocks]
    assert min(codes) == 11110 and max(codes) < 13000
    assert len({s.d.name for s in built.stocks}) == len(built.stocks)
    assert built.pred_days[-1] == make_sample_db.BASE_DAY
    assert built.counts["prediction"] == len(built.stocks) * len(built.pred_days) * len(const.PREDICT_HORIZONS)


def test_build_has_no_private_text(built: make_sample_db.SampleData) -> None:
    """メモ・エラーの内容などに、PC のパス・メールアドレスを入れない(ポートフォリオに使うため)。"""
    from common import db

    pattern = re.compile(r"[A-Za-z]:\\|@|Users")
    with db.connect() as c:
        texts = [str(r["t"]) for r in c.execute("SELECT note AS t FROM holding WHERE note IS NOT NULL")]
        texts += [str(r["t"]) for r in c.execute("SELECT memo AS t FROM watch_stock")]
        texts += [str(r["t"]) for r in c.execute("SELECT error_message AS t FROM run_history WHERE error_message > ''")]
        texts += [str(r["t"]) for r in c.execute("SELECT name AS t FROM stock_master")]
    assert texts
    assert not [t for t in texts if pattern.search(t)]


def test_build_values_are_plausible(built: make_sample_db.SampleData) -> None:
    """的中率などが出来すぎの値にならない(50〜65% あたり)。上昇予想・下降予想の両方がある。"""
    from common import db

    with db.connect() as c:
        rates = [
            float(r["hit_rate"])
            for r in c.execute(
                "SELECT hit_rate FROM verify_period_summary WHERE summary_date = %s AND segment IN (%s, %s)"
                " AND verified_count >= 400",
                (make_sample_db.BASE_DAY, const.SEGMENT_ALL, const.SEGMENT_TOP),
            )
        ]
        up = c.execute(
            "SELECT avg((up_prob >= 0.5)::int) AS r FROM prediction WHERE horizon = %s", (const.MAIN_HORIZON,)
        ).fetchone()
        ranks = c.execute(
            "SELECT count(*) AS n FROM prediction WHERE pred_date = %s AND horizon = %s AND buy_rank IS NOT NULL",
            (make_sample_db.BASE_DAY, const.MAIN_HORIZON),
        ).fetchone()
        statuses = {str(r["status"]) for r in c.execute("SELECT DISTINCT status FROM run_history")}
    assert rates and all(0.4 <= r <= 0.7 for r in rates)
    assert up is not None and 0.35 <= float(up["r"]) <= 0.65
    assert ranks is not None and int(ranks["n"]) == const.BUY_CANDIDATE_COUNT
    assert {const.RUN_STATUS_SUCCESS, const.RUN_STATUS_FAILED, const.RUN_STATUS_EMPTY} <= statuses


def test_build_holdings_cover_judgments(built: make_sample_db.SampleData) -> None:
    """保有銘柄に、保有中・売却済み・買い増し(同じ銘柄の2回の購入)と、売却のダイアログの見本の300株がある。"""
    from common import db

    with db.connect() as c:
        rows = c.execute("SELECT code, quantity, sell_date FROM holding").fetchall()
    open_rows = [r for r in rows if r["sell_date"] is None]
    sold = [r for r in rows if r["sell_date"] is not None]
    assert sold and open_rows
    codes = [r["code"] for r in open_rows]
    assert any(codes.count(c) > 1 for c in codes)
    assert len([r for r in open_rows if r["quantity"] == 300]) == 1
