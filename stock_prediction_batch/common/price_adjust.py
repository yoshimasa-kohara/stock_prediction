"""調整済み株価の計算。

株式分割があると株価は見かけ上大きく下がるので、調整前の株価と調整係数から、分割の影響を取り除いた株価を計算する。
J-Quants の調整済み株価は、後の分割でさかのぼって変わるので保存せず、ここで基準日を決めて計算する。

ある日の調整済み株価 = その日の調整前の株価 ×(その日より後で、基準日以前の日の調整係数を全て掛けた値)
出来高は逆向きに、同じ値で割る。
"""

from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd

# 調整する列(調整前の列名 → 調整済みの列名)
PRICE_COLUMNS: dict[str, str] = {
    "open": "adj_open",
    "high": "adj_high",
    "low": "adj_low",
    "close": "adj_close",
}


def adjust_prices(prices: pd.DataFrame, base_date: date) -> pd.DataFrame:
    """引数の表に、調整済みの始値・高値・安値・終値・出来高の列を足して返す。

    Args:
        prices: daily_price の行。列 code、trade_date、open、high、low、close、volume、adj_factor
        base_date: 基準日。基準日より後の行は使わない(結果からも除く)

    Returns:
        列 adj_open、adj_high、adj_low、adj_close、adj_volume、adj_multiplier を足した表(code、trade_date の順)
    """
    df = prices.loc[pd.to_datetime(prices["trade_date"]) <= pd.Timestamp(base_date)].copy()
    df = df.sort_values(["code", "trade_date"]).reset_index(drop=True)
    factor = pd.to_numeric(df["adj_factor"], errors="coerce").astype(float).fillna(1.0)
    factor = factor.where(factor > 0, 1.0)
    # 銘柄ごとに、後ろから掛け合わせた値(その日以降の積)を、その日の係数で割ると「その日より後の積」になる
    reverse_cumprod = factor[::-1].groupby(df["code"][::-1]).cumprod()[::-1]
    multiplier = reverse_cumprod / factor
    df["adj_multiplier"] = multiplier.to_numpy()
    for raw, adj in PRICE_COLUMNS.items():
        df[adj] = pd.to_numeric(df[raw], errors="coerce").astype(float) * df["adj_multiplier"]
    df["adj_volume"] = pd.to_numeric(df["volume"], errors="coerce").astype(float) / df["adj_multiplier"]
    return df


def to_base_price(close: Decimal, factors: list[Decimal]) -> Decimal:
    """対象日の終値を、基準日の基準にそろえた値を返す(検証の実株価)。

    Args:
        close: 対象日の終値(調整前)
        factors: 基準日より後で、対象日以前の調整係数

    Returns:
        終値 ÷ 調整係数の積
    """
    product = Decimal(1)
    for f in factors:
        product *= f
    if product == 0:
        raise ValueError("調整係数の積が0です")
    return close / product


def cumulative_log_factor(prices: pd.DataFrame) -> pd.Series:
    """銘柄ごとの、調整係数の対数の累積和を返す(日付の古い順)。

    2つの日の差 exp(C(d2) − C(d1)) が、d1 より後で d2 以前の調整係数の積になる。
    配当予想を、開示日より後の分割で補正するときに使う。
    """
    factor = pd.to_numeric(prices["adj_factor"], errors="coerce").astype(float).fillna(1.0)
    factor = factor.where(factor > 0, 1.0)
    return pd.Series(np.log(factor.to_numpy()), index=prices.index).groupby(prices["code"]).cumsum()
