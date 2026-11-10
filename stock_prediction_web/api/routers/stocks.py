"""銘柄の API。

GET /api/stocks/search: 銘柄コード(前方一致)・銘柄名(部分一致)で探す
GET /api/stocks/{code}: 銘柄の情報、指標、次の決算発表予定日、備考
GET /api/stocks/{code}/prices: 株式分割を調整した株価、移動平均、ボリンジャーバンド、トレンドライン
GET /api/stocks/{code}/predictions: 最新の予測と、過去の予測と答え合わせ
"""

import math
from datetime import date, timedelta
from typing import Annotated, Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, Query
from pydantic import BaseModel

import const
from api import web_const
from api.common import Note, check_horizon, check_range, latest_pred_date, to_float
from api.deps import Conn
from api.errors import ApiError
from api.logger import log_api
from api.notes import stock_notes, to_notes
from common.business_day import list_business_days_by_rule
from common.price_adjust import adjust_prices
from core import trendline

router = APIRouter()

# 銘柄が見つからないときのメッセージ
MSG_STOCK_NOT_FOUND: str = "銘柄が見つかりません"
# チャートの期間 → さかのぼる日数(ALL は全て)
CHART_PERIOD_DAYS: dict[str, int | None] = {"1M": 31, "3M": 92, "6M": 183, "1Y": 366, "ALL": None}
# 移動平均の日数
MA_DAYS: list[int] = [5, 25, 75]


# =====================================================================
# 検索
# =====================================================================


class StockItem(BaseModel):
    """銘柄マスタの1銘柄。"""

    code: str
    name: str
    market: str
    sector33: str


class SearchResponse(BaseModel):
    """/api/stocks/search の返り値。"""

    items: list[StockItem]


def escape_like(text: str) -> str:
    """LIKE の特別な文字(%、_、\\)を、文字そのものとして探せるようにする。"""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/api/stocks/search", response_model=SearchResponse)
@log_api
def search_stocks(conn: Conn, q: str = "", scope: str = "target") -> SearchResponse:
    """銘柄コード(前方一致)か銘柄名(部分一致)で探し、web_const.STOCK_SEARCH_LIMIT 件まで返す。

    銘柄コードは4桁でも5桁でもよい(4桁なら 13010 のような5桁のコードも前方一致で見つかる)。

    Args:
        q: 探す文字
        scope: target は予測の対象の市場の銘柄だけ(上のメニュー)、all は銘柄マスタの全て(保有銘柄の登録)
    """
    text = q.strip()
    if not text:
        return SearchResponse(items=[])
    if scope not in ("target", "all"):
        raise ApiError(400, f"scope の指定が正しくありません({scope})")
    pattern = escape_like(text.upper())
    rows = conn.execute(
        """
        SELECT code, name, COALESCE(market_name, '') AS market, COALESCE(sector33_name, '') AS sector33
        FROM stock_master
        WHERE (code LIKE %(code)s OR name ILIKE %(name)s)
          AND (%(all)s OR market_code = ANY(%(markets)s))
        ORDER BY (code LIKE %(code)s) DESC, code
        LIMIT %(limit)s
        """,
        {
            "code": pattern + "%",
            "name": "%" + escape_like(text) + "%",
            "all": scope == "all",
            "markets": const.TARGET_MARKET_CODES,
            "limit": web_const.STOCK_SEARCH_LIMIT,
        },
    ).fetchall()
    return SearchResponse(items=[StockItem(**r) for r in rows])


# =====================================================================
# 銘柄の情報
# =====================================================================


class StockInfo(BaseModel):
    """/api/stocks/{code} の返り値。値がない項目は None。"""

    code: str
    name: str
    market: str
    sector33: str
    scale_category: str
    is_target: bool  # 予測の対象の市場か(ETF などは False)
    price_date: date | None  # 終値の日(株価がある最新の日)
    close: float | None
    prev_change: float | None  # 前日比(株式分割を調整した値)
    prev_change_rate: float | None
    valuation_date: date | None
    fwd_per: float | None
    pbr: float | None
    sector_median_per: float | None
    roe: float | None
    fwd_eps: float | None
    market_cap: float | None  # 時価総額(円。DB は百万円)
    earnings_date: date | None  # 次の決算発表予定日
    earnings_name: str | None  # 決算区分(例: 第1四半期)
    earnings_days: int | None  # あと何営業日か
    notes_date: date | None  # 備考を計算した日
    notes: list[Note]


def load_master(conn: Conn, code: str) -> dict[str, Any]:
    """銘柄マスタの行。なければ 404。"""
    row = conn.execute(
        """
        SELECT code, name, COALESCE(market_name, '') AS market, COALESCE(sector33_name, '') AS sector33,
               COALESCE(scale_category, '') AS scale_category, market_code, sector33_code
        FROM stock_master WHERE code = %s
        """,
        (code,),
    ).fetchone()
    if row is None:
        raise ApiError(404, MSG_STOCK_NOT_FOUND)
    return dict(row)


@router.get("/api/stocks/{code}", response_model=StockInfo)
@log_api
def get_stock(conn: Conn, code: str) -> StockInfo:
    """銘柄の情報。終値・指標は、その銘柄の株価がある最新の日の値。"""
    master = load_master(conn, code)
    prices = conn.execute(
        "SELECT trade_date, close, adj_factor FROM daily_price WHERE code = %s AND close IS NOT NULL"
        " ORDER BY trade_date DESC LIMIT 2",
        (code,),
    ).fetchall()
    price_date: date | None = prices[0]["trade_date"] if prices else None
    close = to_float(prices[0]["close"]) if prices else None
    prev_change = prev_rate = None
    if len(prices) == 2 and close is not None:
        # 前の日の終値を、今日の調整係数で今日の基準にそろえる(common/price_adjust.py と同じ考え方)
        prev = float(prices[1]["close"]) * float(prices[0]["adj_factor"] or 1)
        if prev > 0:
            prev_change = close - prev
            prev_rate = prev_change / prev
    val = conn.execute(
        """
        WITH v AS (
            SELECT * FROM daily_valuation WHERE code = %(code)s ORDER BY trade_date DESC LIMIT 1
        )
        SELECT v.trade_date, v.fwd_per, v.pbr, v.roe, v.fwd_eps, v.market_cap,
               (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY x.fwd_per)
                  FROM daily_valuation x JOIN stock_master m ON m.code = x.code
                 WHERE x.trade_date = v.trade_date AND m.sector33_code = %(sector)s
                   AND m.market_code = ANY(%(markets)s) AND x.fwd_per > 0) AS sector_median_per
        FROM v
        """,
        {
            "code": code,
            "sector": master["sector33_code"],
            "markets": const.TARGET_MARKET_CODES,
        },
    ).fetchone()
    base = price_date or date.today()
    earnings = conn.execute(
        """
        SELECT sch_date, fq_name FROM earnings_date
        WHERE code = %s AND pub_date <= %s AND sch_date >= %s
        ORDER BY pub_date DESC, sch_date LIMIT 1
        """,
        (code, base, base),
    ).fetchone()
    earnings_days = None
    if earnings is not None and earnings["sch_date"] is not None:
        earnings_days = len(list_business_days_by_rule(base + timedelta(days=1), earnings["sch_date"]))
    is_target = master["market_code"] in const.TARGET_MARKET_CODES
    # 備考は、予測の対象の銘柄なら最新の予測日、そうでなければ株価がある最新の日で計算する
    notes_date = (latest_pred_date(conn) if is_target else None) or price_date
    notes = stock_notes(conn, notes_date, [code]).get(code, []) if notes_date else []
    # 時価総額は DB の百万円を円に直す(None はそのまま)
    market_cap = to_float(val["market_cap"]) if val else None
    if market_cap is not None:
        market_cap *= web_const.MARKET_CAP_UNIT_YEN
    return StockInfo(
        code=master["code"],
        name=master["name"],
        market=master["market"],
        sector33=master["sector33"],
        scale_category=master["scale_category"],
        is_target=is_target,
        price_date=price_date,
        close=close,
        prev_change=prev_change,
        prev_change_rate=prev_rate,
        valuation_date=val["trade_date"] if val else None,
        fwd_per=to_float(val["fwd_per"]) if val else None,
        pbr=to_float(val["pbr"]) if val else None,
        sector_median_per=to_float(val["sector_median_per"]) if val else None,
        roe=to_float(val["roe"]) if val else None,
        fwd_eps=to_float(val["fwd_eps"]) if val else None,
        market_cap=market_cap,
        earnings_date=earnings["sch_date"] if earnings else None,
        earnings_name=earnings["fq_name"] if earnings else None,
        earnings_days=earnings_days,
        notes_date=notes_date,
        notes=to_notes(notes),
    )


# =====================================================================
# 株価のチャート
# =====================================================================


class PriceRow(BaseModel):
    """チャートの1日分(株式分割を調整した値)。移動平均・ボリンジャーバンドは、日数がそろわない日は None。"""

    date: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    ma5: float | None
    ma25: float | None
    ma75: float | None
    bb_mid: float | None
    bb_up1: float | None
    bb_up2: float | None
    bb_dn1: float | None
    bb_dn2: float | None


class TrendLineItem(BaseModel):
    """トレンドライン。start は線を結んだ古いほうの谷(山)、end はチャートの右端の日の線の値。

    線の値が0円になる場合、end は0円になる日(値は0)。break は抜けた日(抜けていなければ空)。
    抜けた日は、表示する期間の最初の日(start)より前のこともある。
    画面は、start から end までの各行の線の値を start_value + slope × (行の数) で求めて描く(0円より下は0円)。
    """

    kind: str  # SUPPORT(下値支持線)または RESIST(上値抵抗線)
    touches: int
    start_date: date
    start_value: float
    end_date: date
    end_value: float
    slope: float  # 1営業日(チャートの1行)あたりの線の傾き(円)
    break_date: date | None = None
    break_value: float | None = None


class PricesResponse(BaseModel):
    """/api/stocks/{code}/prices と /api/index/prices の返り値。code は銘柄コード(指数は指数コード)。"""

    code: str
    period: str
    bb_days: int
    rows: list[PriceRow]
    trendlines: list[TrendLineItem]


def _clean(value: float | None) -> float | None:
    return None if value is None or (isinstance(value, float) and math.isnan(value)) else float(value)


def load_adjusted_prices(conn: Conn, code: str) -> pd.DataFrame:
    """その銘柄の全ての株価を読み、株価がある最新の日を基準に株式分割を調整する(common/price_adjust.py)。"""
    rows = conn.execute(
        """
        SELECT code, trade_date, open::float8, high::float8, low::float8, close::float8, volume::float8,
               adj_factor::float8
        FROM daily_price WHERE code = %s ORDER BY trade_date
        """,
        (code,),
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return adjust_prices(df, df["trade_date"].max().date()).reset_index(drop=True)


def find_trendlines(df: pd.DataFrame, first_shown: int = 0) -> list[TrendLineItem]:
    """チャートの右端の日を基準に、上値抵抗線・下値支持線を引く(core/trendline.py。備考と同じ「人の引き方」)。

    接触 const.TREND_CHART_MIN_TOUCHES(2回)以上の線だけを返す。
    線の始まりが、表示する期間の最初の行(first_shown の位置)より前なら、その行の位置の値から始める。
    線の値が0円以下になる場合は、0円になる日で止める(表示する期間で全て0円以下の線は返さない)。
    """
    if len(df) == 0:
        return []
    high = df["adj_high"].to_numpy(dtype=float)
    low = df["adj_low"].to_numpy(dtype=float)
    close = df["adj_close"].to_numpy(dtype=float)
    today = len(df) - 1
    if today < trendline.required_rows():
        return []
    dates = df["trade_date"].dt.date.tolist()
    result: list[TrendLineItem] = []
    for kind, values, lowest in ((trendline.RESIST, high, False), (trendline.SUPPORT, low, True)):
        line = trendline.find_line(kind, high, low, close, trendline.pivot_flags(values, lowest), today)
        if line is None or line.touches < const.TREND_CHART_MIN_TOUCHES:
            continue
        start = max(line.start, min(first_shown, today))
        end = zero_stop(line, today)
        if line.value_at(start) <= 0 or end <= start:
            continue
        brk = line.break_at if line.break_at is not None and line.break_at <= end else None
        result.append(
            TrendLineItem(
                kind=kind,
                touches=line.touches,
                start_date=dates[start],
                start_value=line.value_at(start),
                end_date=dates[end],
                end_value=max(0.0, line.value_at(end)),
                slope=line.slope,
                break_date=None if brk is None else dates[brk],
                break_value=None if brk is None else line.value_at(brk),
            )
        )
    return result


def zero_stop(line: trendline.TrendLine, today: int) -> int:
    """線を止める位置。線の今日の値が0円より上なら today。0円以下なら、線の値が初めて0円以下になる日。"""
    if line.value_at(today) > 0 or line.slope >= 0:
        return today
    # 線の値が0円になる位置(小数)。その位置を含む日で止める(その日の値は0円にする)
    zero = line.start + line.start_value / -line.slope
    return min(today, math.ceil(zero))


def check_period(period: str) -> None:
    """チャートの期間の指定を確かめる。正しくなければ 400。"""
    if period not in CHART_PERIOD_DAYS:
        raise ApiError(400, f"期間の指定が正しくありません({period})")


def build_prices_response(df: pd.DataFrame, code: str, period: str) -> PricesResponse:
    """チャートの値を作る(銘柄の株価と指数日足で共通)。

    df は trade_date と adj_open・adj_high・adj_low・adj_close・adj_volume の列を持つ、日付の古い順の表。
    df の最後の行がチャートの右端の日で、トレンドラインもその日を基準に引く。
    移動平均とボリンジャーバンドは全ての期間で計算してから、期間の分だけを返す。
    ボリンジャーバンドは const.BB_DAYS 日の移動平均 ± 標準偏差の1倍・2倍(特徴量 bb_pctb と同じ計算)。
    df が空なら rows・trendlines を空にして返す。
    """
    if df.empty:
        return PricesResponse(code=code, period=period, bb_days=const.BB_DAYS, rows=[], trendlines=[])
    df = df.copy()
    c = df["adj_close"]
    for n in MA_DAYS:
        df[f"ma{n}"] = c.rolling(n, min_periods=n).mean()
    mid = c.rolling(const.BB_DAYS, min_periods=const.BB_DAYS).mean()
    sd = c.rolling(const.BB_DAYS, min_periods=const.BB_DAYS).std()
    df["bb_mid"], df["bb_up1"], df["bb_up2"] = mid, mid + sd, mid + 2 * sd
    df["bb_dn1"], df["bb_dn2"] = mid - sd, mid - 2 * sd
    days = CHART_PERIOD_DAYS[period]
    first_shown = 0
    if days is not None:
        first = df["trade_date"].max() - pd.Timedelta(days=days)
        first_shown = int((df["trade_date"] <= first).sum())
    lines = find_trendlines(df, first_shown)
    df = df.iloc[first_shown:]
    df = df.replace({np.inf: np.nan, -np.inf: np.nan})
    records: list[dict[str, Any]] = [{str(k): v for k, v in r.items()} for r in df.to_dict("records")]
    rows = [
        PriceRow(
            date=r["trade_date"].date(),
            open=_clean(r["adj_open"]),
            high=_clean(r["adj_high"]),
            low=_clean(r["adj_low"]),
            close=_clean(r["adj_close"]),
            volume=_clean(r["adj_volume"]),
            ma5=_clean(r["ma5"]),
            ma25=_clean(r["ma25"]),
            ma75=_clean(r["ma75"]),
            bb_mid=_clean(r["bb_mid"]),
            bb_up1=_clean(r["bb_up1"]),
            bb_up2=_clean(r["bb_up2"]),
            bb_dn1=_clean(r["bb_dn1"]),
            bb_dn2=_clean(r["bb_dn2"]),
        )
        for r in records
    ]
    return PricesResponse(code=code, period=period, bb_days=const.BB_DAYS, rows=rows, trendlines=lines)


@router.get("/api/stocks/{code}/prices", response_model=PricesResponse)
@log_api
def get_prices(conn: Conn, code: str, period: str = web_const.CHART_DEFAULT_PERIOD) -> PricesResponse:
    """銘柄のチャートの値(株式分割を調整した株価。計算は build_prices_response)。"""
    check_period(period)
    load_master(conn, code)
    return build_prices_response(load_adjusted_prices(conn, code), code, period)


# =====================================================================
# 予測と答え合わせ
# =====================================================================


class LatestPrediction(BaseModel):
    """最新の予測の、予測日数ごとの1行。expected は見込み(予測株価 ÷ 現在値 − 1)。"""

    horizon: int
    target_date: date
    rel_prob: float
    up_prob: float
    pred_price: float
    expected: float | None


class HistoryRow(BaseModel):
    """過去の予測と答え合わせの1行。"""

    pred_date: date
    horizon: int
    target_date: date
    up_prob: float
    rel_prob: float
    pred_price: float
    base_price: float
    actual_price: float | None
    change_rate: float | None
    is_hit: bool | None
    verify_status: str
    buy_rank: int | None


class PredictionsResponse(BaseModel):
    """/api/stocks/{code}/predictions の返り値。最新の予測がなければ latest_date は None。"""

    code: str
    latest_date: date | None
    latest_buy_rank: int | None
    latest_base_price: float | None
    latest: list[LatestPrediction]
    history: list[HistoryRow]
    history_limit: int


@router.get("/api/stocks/{code}/predictions", response_model=PredictionsResponse)
@log_api
def get_predictions(
    conn: Conn,
    code: str,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    horizon: int | None = None,
) -> PredictionsResponse:
    """最新の予測(全ての予測日数)と、過去の予測と答え合わせ。

    過去の予測は、予測日の新しい順に最大 web_const.STOCK_PREDICTION_HISTORY_LIMIT 行。
    """
    check_range(date_from, date_to)
    if horizon is not None:
        check_horizon(horizon)
    load_master(conn, code)
    latest_rows = conn.execute(
        """
        SELECT horizon, target_date, rel_prob, up_prob, pred_price, base_price, buy_rank, pred_date
        FROM prediction
        WHERE code = %(code)s AND pred_date = (SELECT max(pred_date) FROM prediction WHERE code = %(code)s)
        ORDER BY horizon
        """,
        {"code": code},
    ).fetchall()
    latest: list[LatestPrediction] = []
    for r in latest_rows:
        base = float(r["base_price"])
        latest.append(
            LatestPrediction(
                horizon=int(r["horizon"]),
                target_date=r["target_date"],
                rel_prob=float(r["rel_prob"]),
                up_prob=float(r["up_prob"]),
                pred_price=float(r["pred_price"]),
                expected=float(r["pred_price"]) / base - 1 if base > 0 else None,
            )
        )
    first = latest_rows[0] if latest_rows else None
    where = "code = %(code)s"
    params: dict[str, object] = {"code": code, "limit": web_const.STOCK_PREDICTION_HISTORY_LIMIT}
    if date_from:
        where += " AND pred_date >= %(from)s"
        params["from"] = date_from
    if date_to:
        where += " AND pred_date <= %(to)s"
        params["to"] = date_to
    if horizon is not None:
        where += " AND horizon = %(horizon)s"
        params["horizon"] = horizon
    history = conn.execute(
        f"""
        SELECT pred_date, horizon, target_date, up_prob, rel_prob, pred_price, base_price, actual_price,
               change_rate, is_hit, verify_status, buy_rank
        FROM prediction WHERE {where}
        ORDER BY pred_date DESC, horizon
        LIMIT %(limit)s
        """,
        params,
    ).fetchall()
    return PredictionsResponse(
        code=code,
        latest_date=first["pred_date"] if first else None,
        latest_buy_rank=first["buy_rank"] if first else None,
        latest_base_price=to_float(first["base_price"]) if first else None,
        latest=latest,
        history=[
            HistoryRow(
                pred_date=r["pred_date"],
                horizon=int(r["horizon"]),
                target_date=r["target_date"],
                up_prob=float(r["up_prob"]),
                rel_prob=float(r["rel_prob"]),
                pred_price=float(r["pred_price"]),
                base_price=float(r["base_price"]),
                actual_price=to_float(r["actual_price"]),
                change_rate=to_float(r["change_rate"]),
                is_hit=r["is_hit"],
                verify_status=r["verify_status"],
                buy_rank=r["buy_rank"],
            )
            for r in history
        ],
        history_limit=web_const.STOCK_PREDICTION_HISTORY_LIMIT,
    )
