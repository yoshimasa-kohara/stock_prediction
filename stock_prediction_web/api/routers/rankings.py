"""GET /api/rankings: ランキング。

予測日を選んで、7種類の決まった並べ方のランキングを返す。
予測の値・備考・予測日ごとのメモリの入れ物は、銘柄検索(api/routers/screen.py の /api/screen)と同じ関数を使う
(同じ計算を2か所に作らない)。備考は、表に出す web_const.RANKING_PAGE_SIZE 件だけを計算する。
流動性注意の判定は、備考と同じ条件(api/notes.py の avg_turnovers・is_low_liquidity)。
"""

from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

import const
from api import web_const
from api.common import HorizonValue, Note, check_horizon, horizon_values, resolve_pred_date, to_float
from api.deps import Conn
from api.errors import ApiError
from api.logger import log_api
from api.notes import avg_turnovers, is_low_liquidity, to_notes
from api.routers.screen import (
    Option,
    load_pred_stocks,
    load_sector_options,
    market_options_of,
    note_cache,
    split_csv,
    stock_value,
)

router = APIRouter()

# ランキングの種類(URL の type)
TYPE_REL: str = "rel"  # ① 市場比の確率(rel_prob の高い順)
TYPE_UP: str = "up"  # ② 上昇確率(up_prob の高い順)
TYPE_RET: str = "ret"  # ③ 予測の値上がり率(予測株価 ÷ 予測日の株価 − 1 の高い順)
TYPE_DOWN: str = "down"  # ④ 下がりそうな銘柄(rel_prob の低い順)
TYPE_RISE: str = "rise"  # ⑤ 確率が急に上がった銘柄(前の予測日との rel_prob の差の大きい順)
TYPE_ALL: str = "all"  # ⑥ 全期間で高い銘柄(全ての予測日数の rel_prob のうち一番低い値の高い順)
TYPE_REGULAR: str = "regular"  # ⑦ 買い候補の常連(直近の予測日のうち、買い候補に入った日数の多い順)
RANKING_TYPES: list[str] = [TYPE_REL, TYPE_UP, TYPE_RET, TYPE_DOWN, TYPE_RISE, TYPE_ALL, TYPE_REGULAR]
# 値の小さい順に並べる種類(ほかは大きい順)
_ASCENDING_TYPES: tuple[str, ...] = (TYPE_DOWN,)


class RankingRow(BaseModel):
    """ランキングの1銘柄。value はランキングの値(種類ごと。⑦ は買い候補に入った日数)。

    sub_value は ⑦ の、入った日の平均順位(ほかの種類は None)。avg_turnover は売買代金の20日平均(円)。
    """

    rank: int
    code: str
    name: str
    market: str
    sector33: str
    value: float
    sub_value: float | None
    base_price: float
    horizons: list[HorizonValue]
    fwd_per: float | None
    pbr: float | None
    avg_turnover: float | None
    notes: list[Note]


class RankingsResponse(BaseModel):
    """/api/rankings の返り値。

    prev_pred_date は ⑤ で比べた前の予測日(なければ None)。regular_days は ⑦ で数えた予測日の数。
    code_count は予測した銘柄の数、total は条件に合う銘柄の数。
    """

    pred_date: date
    type: str
    horizon: int
    prev_pred_date: date | None
    regular_days: int
    code_count: int
    total: int
    offset: int
    has_more: bool
    rows: list[RankingRow]
    market_options: list[str]
    sector_options: list[Option]


@dataclass(frozen=True)
class Filters:
    """絞り込みの条件。None・空は条件にしない。"""

    markets: frozenset[str]
    sectors: frozenset[str]
    exclude_illiquid: bool
    min_turnover: float | None  # 円
    per_min: float | None
    per_max: float | None
    pbr_min: float | None
    pbr_max: float | None
    min_roe: float | None  # 割合


def previous_pred_date(conn: Conn, pred_date: date) -> date | None:
    """その予測日の、1つ前の予測日(予測がある日)。なければ None。"""
    row = conn.execute("SELECT max(pred_date) AS d FROM prediction WHERE pred_date < %s", (pred_date,)).fetchone()
    return None if row is None else row["d"]


def recent_pred_dates(conn: Conn, pred_date: date, count: int) -> list[date]:
    """その予測日を含む、直近 count 日分の予測日(新しい順)。pred_date の索引を1日ずつたどる。"""
    rows = conn.execute(
        """
        WITH RECURSIVE d AS (
            SELECT max(pred_date) AS v, 1 AS n FROM prediction WHERE pred_date <= %(d)s
            UNION ALL
            SELECT (SELECT max(pred_date) FROM prediction WHERE pred_date < d.v), d.n + 1
            FROM d WHERE d.v IS NOT NULL AND d.n < %(count)s
        )
        SELECT v FROM d WHERE v IS NOT NULL ORDER BY v DESC
        """,
        {"d": pred_date, "count": count},
    ).fetchall()
    return [r["v"] for r in rows]


def rise_values(conn: Conn, stocks: dict[str, dict[str, Any]], prev: date | None, horizon: int) -> dict[str, float]:
    """⑤: 選んだ予測日の rel_prob − 前の予測日の rel_prob。前の予測日に予測がない銘柄は入れない。"""
    if prev is None:
        return {}
    before = {
        str(r["code"]): float(r["rel_prob"])
        for r in conn.execute(
            "SELECT code, rel_prob FROM prediction WHERE pred_date = %s AND horizon = %s", (prev, horizon)
        ).fetchall()
    }
    result: dict[str, float] = {}
    for code, s in stocks.items():
        now = stock_value(s, "rel", horizon)
        if now is not None and code in before:
            result[code] = now - before[code]
    return result


def all_horizon_values(stocks: dict[str, dict[str, Any]]) -> dict[str, float]:
    """⑥: 全ての予測日数(const.PREDICT_HORIZONS)の rel_prob のうち一番低い値。どれかがない銘柄は入れない。"""
    result: dict[str, float] = {}
    for code, s in stocks.items():
        values = [stock_value(s, "rel", h) for h in const.PREDICT_HORIZONS]
        if all(v is not None for v in values):
            result[code] = min(v for v in values if v is not None)
    return result


def regular_values(conn: Conn, dates: list[date]) -> dict[str, tuple[int, float]]:
    """⑦: 銘柄ごとの、買い候補(buy_rank が 1〜const.BUY_CANDIDATE_COUNT)に入った日数と、入った日の平均順位。

    主となる日数の行で数える(1日を1回だけ数えるため)。1日も入っていない銘柄は入れない。
    """
    rows = conn.execute(
        """
        SELECT code, count(*) AS days, avg(buy_rank) AS avg_rank FROM prediction
        WHERE pred_date = ANY(%s) AND horizon = %s AND buy_rank BETWEEN 1 AND %s
        GROUP BY code
        """,
        (dates, const.MAIN_HORIZON, const.BUY_CANDIDATE_COUNT),
    ).fetchall()
    return {str(r["code"]): (int(r["days"]), float(r["avg_rank"])) for r in rows}


def load_valuations(conn: Conn, pred_date: date) -> dict[str, dict[str, float | None]]:
    """予測日の daily_valuation(バリュエーション指標)の PER(予想)・PBR・ROE(実績)。"""
    rows = conn.execute(
        "SELECT code, fwd_per, pbr, roe FROM daily_valuation WHERE trade_date = %s", (pred_date,)
    ).fetchall()
    return {
        str(r["code"]): {"fwd_per": to_float(r["fwd_per"]), "pbr": to_float(r["pbr"]), "roe": to_float(r["roe"])}
        for r in rows
    }


def _in_range(value: float | None, low: float | None, high: float | None) -> bool:
    """下限・上限の条件に合うか。条件が1つでもあれば、値がない銘柄は合わない。"""
    if low is None and high is None:
        return True
    if value is None:
        return False
    return (low is None or value >= low) and (high is None or value <= high)


def passes(stock: dict[str, Any], valuation: dict[str, float | None], turnover: float | None, f: Filters) -> bool:
    """絞り込みの条件に合うか。

    - 流動性注意を除く: 備考と同じ条件(売買代金の平均が const.LIQUIDITY_WARN_TURNOVER 未満の銘柄を除く)
    - 売買代金の下限: 値がない銘柄は除く
    - PER・PBR・ROE: 条件を入れた指標に値がない銘柄は除く。PER の条件を入れたときは、PER がマイナスの銘柄も除く
    """
    if f.markets and stock["market"] not in f.markets:
        return False
    if f.sectors and stock["sector33_code"] not in f.sectors:
        return False
    if f.exclude_illiquid and turnover is not None and is_low_liquidity(turnover):
        return False
    if f.min_turnover is not None and (turnover is None or turnover < f.min_turnover):
        return False
    per = valuation.get("fwd_per")
    if (f.per_min is not None or f.per_max is not None) and per is not None and per < 0:
        return False
    return (
        _in_range(per, f.per_min, f.per_max)
        and _in_range(valuation.get("pbr"), f.pbr_min, f.pbr_max)
        and _in_range(valuation.get("roe"), f.min_roe, None)
    )


@router.get("/api/rankings", response_model=RankingsResponse)
@log_api
def get_rankings(
    conn: Conn,
    target: Annotated[date | None, Query(alias="date")] = None,
    ranking_type: Annotated[str, Query(alias="type")] = TYPE_REL,
    horizon: int = const.MAIN_HORIZON,
    markets: str | None = None,
    sectors: str | None = None,
    exclude_illiquid: bool = True,
    min_turnover: float | None = None,
    per_min: float | None = None,
    per_max: float | None = None,
    pbr_min: float | None = None,
    pbr_max: float | None = None,
    min_roe: float | None = None,
    offset: int = 0,
) -> RankingsResponse:
    """ランキングを返す。並びは種類ごとに決まっている。値が同じ銘柄は、銘柄コードの順。

    Args:
        target: 予測日(省略すると最新)
        ranking_type: 種類(rel・up・ret・down・rise・all・regular。クエリの名前は type)
        horizon: 予測日数(rel・up・ret・down・rise で使う。all・regular では使わない)
        markets: 市場の名前(カンマ区切り)。省略すると全て
        sectors: 33業種コード(カンマ区切り)。省略すると全て
        exclude_illiquid: 流動性注意の銘柄を除くか(省略すると除く)
        min_turnover: 売買代金の20日平均の下限(億円)
        per_min・per_max: PER(予想)の下限・上限(倍)
        pbr_min・pbr_max: PBR の下限・上限(倍)
        min_roe: ROE(実績)の下限(%)
        offset: 何件目から返すか
    """
    if ranking_type not in RANKING_TYPES:
        raise ApiError(400, f"ランキングの種類は {'・'.join(RANKING_TYPES)} のどれかを指定してください")
    check_horizon(horizon)
    if offset < 0:
        raise ApiError(400, "offset は 0 以上を指定してください")
    pred_date = resolve_pred_date(conn, target)
    filters = Filters(
        markets=frozenset(split_csv(markets)),
        sectors=frozenset(split_csv(sectors)),
        exclude_illiquid=exclude_illiquid,
        min_turnover=None if min_turnover is None else min_turnover * web_const.RANKING_TURNOVER_UNIT_YEN,
        per_min=per_min,
        per_max=per_max,
        pbr_min=pbr_min,
        pbr_max=pbr_max,
        min_roe=None if min_roe is None else min_roe / 100,
    )

    # 予測日の全ての銘柄(銘柄検索と同じ関数)と、ランキングの値
    stocks = load_pred_stocks(conn, pred_date)
    prev_date: date | None = None
    regular_days = 0
    sub_values: dict[str, float] = {}
    values: dict[str, float]
    if ranking_type == TYPE_RISE:
        prev_date = previous_pred_date(conn, pred_date)
        values = rise_values(conn, stocks, prev_date, horizon)
    elif ranking_type == TYPE_ALL:
        values = all_horizon_values(stocks)
    elif ranking_type == TYPE_REGULAR:
        dates = recent_pred_dates(conn, pred_date, web_const.RANKING_REGULAR_DAYS)
        regular_days = len(dates)
        regular = {c: v for c, v in regular_values(conn, dates).items() if c in stocks}
        values = {c: float(days) for c, (days, _) in regular.items()}
        sub_values = {c: avg for c, (_, avg) in regular.items()}
    else:
        key = "rel" if ranking_type == TYPE_DOWN else ranking_type
        values = {c: v for c, s in stocks.items() if (v := stock_value(s, key, horizon)) is not None}

    # 絞り込み(値は予測日のバリュエーション指標と、売買代金の20日平均)
    valuations = load_valuations(conn, pred_date)
    turnovers = avg_turnovers(conn, pred_date, list(values))
    codes = [c for c in values if passes(stocks[c], valuations.get(c, {}), turnovers.get(c), filters)]

    # 並べ替え(値が同じ銘柄は銘柄コードの順。⑦ は日数が同じなら平均順位の良い順)
    sign = 1 if ranking_type in _ASCENDING_TYPES else -1
    codes.sort(key=lambda c: (sign * values[c], sub_values.get(c, 0.0), c))
    total = len(codes)
    page = codes[offset : offset + web_const.RANKING_PAGE_SIZE]
    # 備考は表に出す行だけを計算する(銘柄検索と同じ、予測日ごとのメモリの入れ物)
    page_notes = note_cache.get(conn, pred_date, page)
    return RankingsResponse(
        pred_date=pred_date,
        type=ranking_type,
        horizon=horizon,
        prev_pred_date=prev_date,
        regular_days=regular_days,
        code_count=len(stocks),
        total=total,
        offset=offset,
        has_more=offset + len(page) < total,
        rows=[
            RankingRow(
                rank=offset + i + 1,
                code=c,
                name=str(stocks[c]["name"]),
                market=str(stocks[c]["market"]),
                sector33=str(stocks[c]["sector33"]),
                value=values[c],
                sub_value=sub_values.get(c),
                base_price=float(stocks[c]["base_price"]),
                horizons=horizon_values(stocks[c]["by_h"]),
                fwd_per=valuations.get(c, {}).get("fwd_per"),
                pbr=valuations.get(c, {}).get("pbr"),
                avg_turnover=turnovers.get(c),
                notes=to_notes(page_notes.get(c, [])),
            )
            for i, c in enumerate(page)
        ],
        market_options=market_options_of(stocks),
        sector_options=load_sector_options(conn),
    )
