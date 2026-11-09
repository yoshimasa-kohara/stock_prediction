"""相場の状態(core/market_state_core.py)のテスト。"""

from datetime import date

import numpy as np
import pandas as pd
import psycopg
import pytest
from psycopg.rows import DictRow

import const
from core import market_state_core as ms
from tests.sample_data import insert_sample_data
from tools import evaluate_market_regime as ev


@pytest.mark.parametrize(
    ("above25", "expected"),
    [
        (0.60, ms.STATE_STRONG),  # 境目は「強い」
        (0.95, ms.STATE_STRONG),
        (0.5999, ms.STATE_NORMAL),
        (0.40, ms.STATE_NORMAL),  # 境目は「普通」
        (0.3999, ms.STATE_WEAK),
        (0.0, ms.STATE_WEAK),
    ],
)
def test_状態の判定(above25: float, expected: str) -> None:
    assert ms.judge_state(above25) == expected


def test_文言() -> None:
    assert (
        ms.market_state_text(ms.MarketState(ms.STATE_WEAK, 0.321, -0.021))
        == "相場の状態: 弱い(25日移動平均より上の銘柄 32.1%、直近20営業日の騰落率の中央値 -2.1%)"
    )
    assert ms.market_state_text(ms.MarketState(ms.STATE_STRONG, 0.7, 0.034)).endswith("中央値 +3.4%)")
    assert ms.market_state_text(None) == "相場の状態: -"
    assert "赤" not in ms.MARKET_STATE_NOTE
    assert ms.MARKET_STATE_NOTE == "※今の相場の状態を表すもので、この先の上げ下げの予測ではありません"


def test_株価が足りない日は計算しない() -> None:
    """株価が20日分もない場合は None(エラーにしない)。"""
    days = pd.bdate_range("2026-01-05", periods=10)
    wide = pd.DataFrame(np.linspace(100, 110, 10)[:, None] * np.ones((1, 5)), index=days, columns=list("abcde"))
    assert ms.state_on(ms.indicators(wide), days[-1]) is None
    assert ms.state_on(ms.indicators(wide), pd.Timestamp("2030-01-01")) is None


def test_上昇相場は強いと判定する() -> None:
    days = pd.bdate_range("2026-01-05", periods=40)
    rising = pd.DataFrame(
        np.linspace(100, 140, 40)[:, None] * np.linspace(1, 2, 6)[None, :], index=days, columns=list("abcdef")
    )
    got = ms.state_on(ms.indicators(rising), days[-1])
    assert got is not None
    assert got.state == ms.STATE_STRONG
    assert got.above25 == 1.0
    assert got.ret20 > 0


def test_株価がない日はNone(conn: psycopg.Connection[DictRow]) -> None:
    assert ms.load_market_state(conn, date(2026, 6, 30)) is None


def test_評価ツールと同じ値になる(conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> None:
    """同じ日について、評価ツール(evaluate_market_regime.price_indicators)と above25・ret20 が一致する。"""
    days = insert_sample_data(conn, date(2025, 7, 1), date(2025, 12, 26), split_date=date(2025, 10, 6))
    monkeypatch.setattr(ev, "PRICE_START", days[0])  # サンプルの最初の日から読む
    reference = ev.price_indicators(ev.load_prices(conn))
    checked = 0
    for d in days[-60:]:
        got = ms.load_market_state(conn, d)
        a25, r20 = float(reference["above25"].to_dict()[d]), float(reference["ret20"].to_dict()[d])
        assert got is not None
        assert got.above25 == pytest.approx(a25, abs=1e-12)
        assert got.ret20 == pytest.approx(r20, abs=1e-12)
        assert got.state == ms.judge_state(a25)
        checked += 1
    assert checked == 60
    assert const.MARKET_STATE_WEAK < const.MARKET_STATE_STRONG


# =====================================================================
# 日経平均の向き
# =====================================================================


def test_回帰の傾きは最小二乗法() -> None:
    # 1営業日に 10円ずつ上がる直線は、傾き 10
    assert ms.regression_slope([100.0 + 10 * i for i in range(10)]) == pytest.approx(10.0)
    # ばらつきがあっても最小二乗法の傾き(numpy と同じ)
    closes = [100.0, 103.0, 101.0, 106.0, 104.0]
    assert ms.regression_slope(closes) == pytest.approx(float(np.polyfit(np.arange(5), closes, 1)[0]))


@pytest.mark.parametrize(
    ("slope", "expected"),
    [
        (0.0006, ms.DIRECTION_UP),
        (0.0005, ms.DIRECTION_FLAT),  # 境目(TREND_FLAT_SLOPE ちょうど)は横ばい
        (0.0, ms.DIRECTION_FLAT),
        (-0.0005, ms.DIRECTION_FLAT),
        (-0.0006, ms.DIRECTION_DOWN),
    ],
)
def test_向きの判定(slope: float, expected: str) -> None:
    assert const.TREND_FLAT_SLOPE == 0.0005
    assert ms.judge_direction(slope) == expected


def test_傾きは予測日の終値に対する1営業日あたりの割合() -> None:
    # 古い40日は横ばい、直近20日は1日 30円ずつ上がり、最後の日の終値は 30,000円
    closes = [29430.0] * 40 + [29430.0 + 30 * i for i in range(1, 21)]
    assert closes[-1] == 30030.0
    trend = ms.compute_index_trend(closes)
    assert [t.days for t in trend.items] == [10, 20, 60]
    assert trend.items[0].slope == pytest.approx(30 / 30030.0)
    assert trend.items[0].direction == ms.DIRECTION_UP
    assert trend.items[2].slope == pytest.approx(ms.regression_slope(closes) / 30030.0)


def test_日経平均の向きの文言() -> None:
    trend = ms.IndexTrend(
        (
            ms.IndexTrendItem(10, ms.DIRECTION_UP, 0.0021),
            ms.IndexTrendItem(20, ms.DIRECTION_FLAT, 0.0003),
            ms.IndexTrendItem(60, ms.DIRECTION_DOWN, -0.0008),
        )
    )
    assert ms.index_trend_text(trend) == (
        "日経平均の向き: 10日 上向き(+0.21%/日)/20日 横ばい(+0.03%/日)/60日 下向き(-0.08%/日)"
    )
    assert ms.index_trend_text(None) is None
    # 小数2桁で 0 になる値は -0.00% にしない
    assert ms.signed_rate(-0.00001) == "+0.00%"
    assert ms.signed_rate(0.0) == "+0.00%"


def _days(n: int, end: date) -> list[date]:
    """end までの平日 n 日(古い順)。"""
    return [d.date() for d in pd.bdate_range(end=end, periods=n)]


def _seed_trend(
    conn: psycopg.Connection[DictRow], price_days: list[date], index_days: list[date], step: float = 30.0
) -> None:
    for d in price_days:
        conn.execute("INSERT INTO daily_price (trade_date, code, close, adj_factor) VALUES (%s, '11110', 100, 1)", (d,))
    for i, d in enumerate(index_days):
        conn.execute(
            "INSERT INTO index_daily_price (index_code, trade_date, close) VALUES (%s, %s, %s)",
            (const.INDEX_CODE_N225, d, 30000.0 + step * i),
        )


T_TREND = date(2026, 11, 6)  # 金


def test_60日分そろえば日経平均の向きを計算する(conn: psycopg.Connection[DictRow]) -> None:
    days = _days(70, T_TREND)
    _seed_trend(conn, days, days)
    trend = ms.load_index_trend(conn, T_TREND)
    assert trend is not None
    assert [(t.days, t.direction) for t in trend.items] == [(10, "上向き"), (20, "上向き"), (60, "上向き")]
    # 直近60営業日だけを使う(最後の日の終値で割る)
    last = 30000.0 + 30 * 69
    assert trend.items[2].slope == pytest.approx(30 / last)


def test_下向きと横ばい(conn: psycopg.Connection[DictRow]) -> None:
    days = _days(60, T_TREND)
    _seed_trend(conn, days, days, step=-30.0)
    trend = ms.load_index_trend(conn, T_TREND)
    assert trend is not None and {t.direction for t in trend.items} == {"下向き"}
    conn.execute("UPDATE index_daily_price SET close = 30000")
    trend = ms.load_index_trend(conn, T_TREND)
    assert trend is not None and {t.direction for t in trend.items} == {"横ばい"}


def test_予測日の日経平均がなければ出さない(conn: psycopg.Connection[DictRow]) -> None:
    days = _days(61, T_TREND)
    # 株価は 11/6 まであるのに、日経平均は 11/5 まで
    _seed_trend(conn, days, days[:-1])
    assert ms.load_index_trend(conn, T_TREND) is None
    # 前の日の時点なら出す(過去の日も、その日の時点で計算する)
    assert ms.load_index_trend(conn, days[-2]) is not None


def test_途中の営業日に日経平均がなければ出さない(conn: psycopg.Connection[DictRow]) -> None:
    days = _days(60, T_TREND)
    _seed_trend(conn, days, days[:30] + days[31:])
    assert ms.load_index_trend(conn, T_TREND) is None


def test_60日分そろっていなければ出さない(conn: psycopg.Connection[DictRow]) -> None:
    days = _days(59, T_TREND)
    _seed_trend(conn, days, days)
    assert ms.load_index_trend(conn, T_TREND) is None
    # 株価日足のない日(休場日)の日経平均は数えない。株価日足のある日で数える
    assert ms.load_index_trend(conn, date(2026, 11, 7)) is None


def test_株価日足が休みの日は営業日に数えない(conn: psycopg.Connection[DictRow]) -> None:
    days = _days(61, T_TREND)
    holiday = days[30]
    # 株価日足がない日(休場日)は、日経平均がなくても欠けとみなさない
    _seed_trend(conn, [d for d in days if d != holiday], [d for d in days if d != holiday])
    assert ms.load_index_trend(conn, T_TREND) is not None
