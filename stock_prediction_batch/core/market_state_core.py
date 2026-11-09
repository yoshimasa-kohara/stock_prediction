"""相場の状態(強い・普通・弱い)の計算と文言。

予測ではなく、予測日の終値までの株価から、今の相場の状態を事実として示す。メール・トップ画面・買い候補画面で同じ文言にする。
  above25: 終値が25日移動平均より上の銘柄の割合(25日分の株価がそろう銘柄だけで数える)
  ret20  : 直近20営業日の全銘柄の騰落率の中央値
状態は above25 で決める(MARKET_STATE_STRONG 以上: 強い、MARKET_STATE_WEAK より小さい: 弱い、それ以外: 普通)。
対象は学習・予測と同じ市場区分(TARGET_MARKET_CODES)の銘柄。テーブルは持たず、表示のたびに株価日足から計算する。
計算の仕方は tools/evaluate_market_regime.py(price_indicators)と同じ。当処理はDBを変更しない。

あわせて、日経平均の向き(直近10・20・60営業日の終値の回帰直線の傾き)を、相場の状態の次の行に出す(load_index_trend)。
日経平均は収集の最後に index_daily_price に登録している(core/index_collect_core.py)。これも表示のたびに計算する。
"""

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg
from psycopg.rows import DictRow, tuple_row

import const
from common.price_adjust import adjust_prices

STATE_STRONG: str = "強い"
STATE_NORMAL: str = "普通"
STATE_WEAK: str = "弱い"
# 状態の注記(メール・画面で同じ文言)
MARKET_STATE_NOTE: str = "※今の相場の状態を表すもので、この先の上げ下げの予測ではありません"


@dataclass(frozen=True)
class MarketState:
    """相場の状態。above25 と ret20 は割合(0.321 = 32.1%)。"""

    state: str
    above25: float
    ret20: float


def judge_state(above25: float) -> str:
    """above25 から状態を決める。"""
    if above25 >= const.MARKET_STATE_STRONG:
        return STATE_STRONG
    if above25 < const.MARKET_STATE_WEAK:
        return STATE_WEAK
    return STATE_NORMAL


def build_wide(prices: pd.DataFrame, base_date: date) -> pd.DataFrame:
    """株価(列 code、trade_date、close、adj_factor)から、日付 × 銘柄コードの調整済み終値の表を作る。

    市場全体で取引のない日(銘柄の大半が NULL の日)は除く。
    """
    df = prices.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    for c in ("open", "high", "low", "volume"):
        df[c] = np.nan
    adj = adjust_prices(df, base_date)
    wide = adj.pivot(index="trade_date", columns="code", values="adj_close").sort_index()
    cnt = wide.notna().sum(axis=1)
    return wide.loc[cnt >= cnt.median() * 0.5]


def indicators(wide: pd.DataFrame) -> pd.DataFrame:
    """日付(index)ごとの above25 と ret20(evaluate_market_regime.price_indicators と同じ式)。"""
    out = pd.DataFrame(index=wide.index)
    out["ret20"] = (wide / wide.shift(const.MARKET_STATE_RET_DAYS) - 1.0).median(axis=1)
    n = const.MARKET_STATE_MA_DAYS
    ma = wide.rolling(n, min_periods=n).mean()
    valid = ma.notna() & wide.notna()
    out["above25"] = ((wide > ma) & valid).sum(axis=1) / valid.sum(axis=1).replace(0, np.nan)
    return out


def state_on(ind: pd.DataFrame, target: pd.Timestamp) -> MarketState | None:
    """指標の表から、指定日の状態を取り出す。日がない、または値が計算できない場合は None。"""
    if target not in ind.index:
        return None
    pos = int(ind.index.get_loc(target))  # type: ignore[arg-type]
    above25, ret20 = float(ind["above25"].to_numpy()[pos]), float(ind["ret20"].to_numpy()[pos])
    if np.isnan(above25) or np.isnan(ret20):
        return None
    return MarketState(judge_state(above25), above25, ret20)


def load_market_state(conn: psycopg.Connection[DictRow], target_date: date) -> MarketState | None:
    """指定日の相場の状態を、株価日足から計算する。計算できない日(株価が足りないなど)は None(エラーにしない)。"""
    start = target_date - timedelta(days=const.MARKET_STATE_LOAD_DAYS)
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            "SELECT p.code, p.trade_date, p.close::float8, p.adj_factor::float8"
            " FROM daily_price p JOIN stock_master m ON m.code = p.code"
            " WHERE m.market_code = ANY(%s) AND p.trade_date BETWEEN %s AND %s",
            (const.TARGET_MARKET_CODES, start, target_date),
        )
        rows = cur.fetchall()
    if not rows:
        return None
    prices = pd.DataFrame(rows, columns=["code", "trade_date", "close", "adj_factor"])
    wide = build_wide(prices, target_date)
    return state_on(indicators(wide), pd.Timestamp(target_date))


def market_state_text(ms: MarketState | None) -> str:
    """表示の文言(1行目)。計算できない日は「相場の状態: -」。"""
    if ms is None:
        return "相場の状態: -"
    return (
        f"相場の状態: {ms.state}(25日移動平均より上の銘柄 {ms.above25 * 100:.1f}%、"
        f"直近{const.MARKET_STATE_RET_DAYS}営業日の騰落率の中央値 {ms.ret20 * 100:+.1f}%)"
    )


# =====================================================================
# 日経平均の向き(10・20・60日の回帰直線。表示だけに使い、予測には使わない)
# =====================================================================

DIRECTION_UP: str = "上向き"
DIRECTION_FLAT: str = "横ばい"
DIRECTION_DOWN: str = "下向き"


@dataclass(frozen=True)
class IndexTrendItem:
    """1つの期間の日経平均の向き。slope は1営業日あたりの傾きを、予測日の終値で割った割合(0.0021 = +0.21%/日)。"""

    days: int
    direction: str
    slope: float


@dataclass(frozen=True)
class IndexTrend:
    """日経平均の向き(INDEX_TREND_DAYS の期間ごと。短い順)。"""

    items: tuple[IndexTrendItem, ...]


def judge_direction(slope: float) -> str:
    """傾き(割合)から向きを決める。TREND_FLAT_SLOPE はトレンドラインと同じ単位(1営業日あたり、終値に対する割合)。"""
    if slope > const.TREND_FLAT_SLOPE:
        return DIRECTION_UP
    if slope < -const.TREND_FLAT_SLOPE:
        return DIRECTION_DOWN
    return DIRECTION_FLAT


def regression_slope(closes: list[float]) -> float:
    """終値(古い順。1営業日に1つ)に回帰直線(最小二乗法)を引き、1営業日あたりの傾き(円)を返す。"""
    x = np.arange(len(closes), dtype=float)
    slope, _ = np.polyfit(x, np.asarray(closes, dtype=float), 1)
    return float(slope)


def compute_index_trend(closes: list[float]) -> IndexTrend:
    """直近 max(INDEX_TREND_DAYS) 営業日の終値(古い順。最後が予測日)から、期間ごとの向きを計算する。"""
    last = closes[-1]
    items = []
    for n in sorted(const.INDEX_TREND_DAYS):
        slope = regression_slope(closes[-n:]) / last
        items.append(IndexTrendItem(n, judge_direction(slope), slope))
    return IndexTrend(tuple(items))


def load_index_trend(conn: psycopg.Connection[DictRow], target_date: date) -> IndexTrend | None:
    """予測日の日経平均の向きを計算する。

    営業日は株価日足のある日で数える。次のどれかに当てはまる日は None(出さない。エラーにしない):
      - 予測日の株価日足・日経平均の行がない
      - 直近 max(INDEX_TREND_DAYS) 営業日のうち、日経平均の行がない日がある(途中の欠け)
      - 株価日足の営業日が max(INDEX_TREND_DAYS) 日分そろっていない
    """
    need = max(const.INDEX_TREND_DAYS)
    start = target_date - timedelta(days=const.INDEX_TREND_LOAD_DAYS)
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            "SELECT DISTINCT trade_date FROM daily_price WHERE trade_date BETWEEN %s AND %s ORDER BY trade_date DESC"
            " LIMIT %s",
            (start, target_date, need),
        )
        days = sorted(r[0] for r in cur.fetchall())
        if len(days) < need or days[-1] != target_date:
            return None
        cur.execute(
            "SELECT trade_date, close::float8 FROM index_daily_price"
            " WHERE index_code = %s AND trade_date BETWEEN %s AND %s AND close IS NOT NULL",
            (const.INDEX_CODE_N225, days[0], target_date),
        )
        closes_by_day = {r[0]: float(r[1]) for r in cur.fetchall()}
    if any(d not in closes_by_day for d in days):
        return None
    return compute_index_trend([closes_by_day[d] for d in days])


def index_trend_text(trend: IndexTrend | None) -> str | None:
    """表示の文言(相場の状態の次の行)。出さない日は None。

    例: 日経平均の向き: 10日 上向き(+0.21%/日)/20日 横ばい(+0.03%/日)/60日 下向き(-0.08%/日)
    """
    if trend is None:
        return None
    parts = [f"{t.days}日 {t.direction}({signed_rate(t.slope)}/日)" for t in trend.items]
    return "日経平均の向き: " + "/".join(parts)


def signed_rate(rate: float) -> str:
    """割合を符号付きの小数2桁の % にする(0.0021 → +0.21%)。小数2桁で 0 になる値は +0.00%(-0.00% にしない)。"""
    text = f"{abs(rate) * 100:.2f}"
    return f"{'-' if rate < 0 and text != '0.00' else '+'}{text}%"
