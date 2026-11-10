"""指数の API(トップ・買い候補の「相場」のブロック)。

GET /api/index/prices: 日経平均の日足、移動平均、ボリンジャーバンド、トレンドライン
"""

from datetime import date
from typing import Annotated

import numpy as np
import pandas as pd
from fastapi import APIRouter, Query

import const
from api import web_const
from api.common import latest_pred_date
from api.deps import Conn
from api.logger import log_api
from api.routers.stocks import PricesResponse, build_prices_response, check_period

router = APIRouter()


def load_index_prices(conn: Conn, index_code: str, until: date | None) -> pd.DataFrame:
    """指数日足(index_daily_price)の until 以前の行を、日付の古い順に読む(until が None なら全て)。

    列の名前は銘柄の株価(load_adjusted_prices)にそろえる。指数には株式分割の調整はなく、出来高もない(NaN)。
    """
    rows = conn.execute(
        """
        SELECT trade_date, open::float8 AS adj_open, high::float8 AS adj_high, low::float8 AS adj_low,
               close::float8 AS adj_close
        FROM index_daily_price
        WHERE index_code = %(code)s AND (%(until)s::date IS NULL OR trade_date <= %(until)s::date)
        ORDER BY trade_date
        """,
        {"code": index_code, "until": until},
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["adj_volume"] = np.nan
    return df


@router.get("/api/index/prices", response_model=PricesResponse)
@log_api
def get_index_prices(
    conn: Conn,
    period: str = web_const.CHART_DEFAULT_PERIOD,
    target: Annotated[date | None, Query(alias="date")] = None,
) -> PricesResponse:
    """日経平均(const.INDEX_CODE_N225)のチャートの値。計算は銘柄のチャートと同じ(build_prices_response)。

    date(予測日。省略すると最新の予測日。予測が1つもなければ全ての日)以前の行だけで計算する。
    チャートの右端は、date 以前で日経平均がある最後の日。データがなければ rows を空にして返す。出来高は None。
    """
    check_period(period)
    until = target or latest_pred_date(conn)
    df = load_index_prices(conn, const.INDEX_CODE_N225, until)
    return build_prices_response(df, const.INDEX_CODE_N225, period)
