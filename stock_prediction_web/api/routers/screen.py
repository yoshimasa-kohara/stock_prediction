"""GET /api/screen: 銘柄検索。

条件に合う銘柄を、買い候補と同じ形(予測日数ごとの市場比・上昇確率・予測株価、備考)で返す。
備考は、表に出す web_const.SCREEN_PAGE_SIZE 件だけを計算する。備考で絞り込む・除くときは、予測した全銘柄を計算する。
計算した備考は、予測日ごとに API のメモリに残し、同じ予測日の2回目からは計算しない(api/notes.py の NoteCache)。
"""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

import const
from api import web_const
from api.common import HorizonValue, Note, horizon_values, resolve_pred_date, to_float
from api.deps import Conn
from api.errors import ApiError
from api.logger import log_api
from api.notes import NoteCache, note_filters, to_notes

router = APIRouter()

# 全銘柄の備考を残す入れ物(Web サーバーが動いている間、予測日ごとに残す)
note_cache: NoteCache = NoteCache(web_const.NOTE_CACHE_DAYS)

# 市場の選択肢の並び(この順。ほかの市場は後ろに名前の順)
MARKET_ORDER: list[str] = ["プライム", "スタンダード", "グロース"]
# 「除く」で選べる備考
EXCLUDE_KEYS: list[str] = ["low_liquidity", "deficit"]
# 並べ替えに使える項目(列の見出しを押したとき)。rel・up・pred・ret は「_予測日数」を付ける(例: rel_20)
SORT_FIXED: list[str] = ["rank", "code", "name", "market", "base_price"]
SORT_BY_HORIZON: list[str] = ["rel", "up", "pred", "ret"]


class Option(BaseModel):
    """条件の選択肢(値と表示名)。"""

    value: str
    label: str


class ScreenRow(BaseModel):
    """検索結果の1銘柄。rank は買い候補の順位(入っていなければ None)。"""

    rank: int | None
    code: str
    name: str
    market: str
    sector33: str
    base_price: float
    horizons: list[HorizonValue]
    notes: list[Note]


class ScreenResponse(BaseModel):
    """/api/screen の返り値。options は画面の条件の選択肢(市場、33業種、備考)。"""

    pred_date: date
    code_count: int
    total: int
    offset: int
    has_more: bool
    sort: str
    order: str
    rows: list[ScreenRow]
    market_options: list[str]
    sector_options: list[Option]
    note_options: list[Option]
    exclude_options: list[Option]


def load_pred_stocks(conn: Conn, pred_date: date) -> dict[str, dict[str, Any]]:
    """予測日の全ての銘柄(銘柄コード → 銘柄の情報と、by_h: 予測日数ごとの値、rank: 買い候補の順位)。

    銘柄検索(/api/screen)とランキング(/api/rankings)が共通で使う(同じ計算を2か所に作らない)。
    """
    preds = conn.execute(
        """
        SELECT p.code, p.horizon, p.rel_prob, p.up_prob, p.pred_price, p.base_price, p.buy_rank,
               COALESCE(m.name, '') AS name, COALESCE(m.market_name, '') AS market,
               COALESCE(m.sector33_code, '') AS sector33_code, COALESCE(m.sector33_name, '') AS sector33
        FROM prediction p LEFT JOIN stock_master m ON m.code = p.code
        WHERE p.pred_date = %s
        """,
        (pred_date,),
    ).fetchall()
    stocks: dict[str, dict[str, Any]] = {}
    for p in preds:
        code = str(p["code"])
        s = stocks.setdefault(code, {**dict(p), "by_h": {}, "rank": None})
        s["by_h"][int(p["horizon"])] = p
        if p["buy_rank"] is not None and int(p["horizon"]) == const.MAIN_HORIZON:
            s["rank"] = int(p["buy_rank"])
    return stocks


def market_options_of(stocks: dict[str, dict[str, Any]]) -> list[str]:
    """市場の選択肢(予測した銘柄にある市場。MARKET_ORDER の順、ほかの市場は後ろに名前の順)。"""
    found = {str(s["market"]) for s in stocks.values() if s["market"]}
    return [m for m in MARKET_ORDER if m in found] + sorted(found - set(MARKET_ORDER))


def load_sector_options(conn: Conn) -> list[Option]:
    """33業種の選択肢(予測の対象の市場の銘柄にある業種。業種コードの順)。"""
    rows = conn.execute(
        """
        SELECT DISTINCT sector33_code, sector33_name FROM stock_master
        WHERE market_code = ANY(%s) AND sector33_code IS NOT NULL ORDER BY sector33_code
        """,
        (const.TARGET_MARKET_CODES,),
    ).fetchall()
    return [Option(value=str(s["sector33_code"]), label=str(s["sector33_name"] or "")) for s in rows]


def split_csv(text: str | None) -> list[str]:
    """カンマ区切りの値をリストにする(空の値は除く)。"""
    return [t.strip() for t in (text or "").split(",") if t.strip()]


def stock_value(row: dict[str, Any], key: str, horizon: int) -> float | None:
    """銘柄の、その予測日数の値。key は rel(市場比の確率)・up(上昇確率)・pred(予測株価)・
    ret(見込み = 予測株価 ÷ 予測日の株価 − 1)。その予測日数の予測がなければ None。"""
    v = row["by_h"].get(horizon)
    if v is None:
        return None
    if key == "rel":
        return to_float(v["rel_prob"])
    if key == "up":
        return to_float(v["up_prob"])
    if key == "pred":
        return to_float(v["pred_price"])
    base = float(row["base_price"])
    return float(v["pred_price"]) / base - 1 if base > 0 else None


def _percent(value: float | None, name: str) -> float | None:
    """「○%以上」の入力を割合にする(例: 60 → 0.6)。"""
    if value is None:
        return None
    if not -100 <= value <= 1000:
        raise ApiError(400, f"{name}は -100〜1000 の数を入れてください")
    return value / 100


@router.get("/api/screen", response_model=ScreenResponse)
@log_api
def screen(
    conn: Conn,
    target: Annotated[date | None, Query(alias="date")] = None,
    q: str = "",
    markets: str | None = None,
    sectors: str | None = None,
    min_rel: float | None = None,
    min_up: float | None = None,
    min_ret: float | None = None,
    notes: str | None = None,
    exclude: str | None = None,
    sort: str = "",
    order: str = "desc",
    offset: int = 0,
) -> ScreenResponse:
    """条件に合う銘柄を返す。

    Args:
        target: 予測日(省略すると最新)
        q: 銘柄コード(前方一致。4桁でも可)か銘柄名(部分一致)
        markets: 市場の名前(カンマ区切り。例: プライム,グロース)。省略すると全て
        sectors: 33業種コード(カンマ区切り)。省略すると全て
        min_rel・min_up・min_ret: 主となる日数の市場比の確率・上昇確率・見込みの下限(%)
        notes: 付いている備考(api/notes.py の NoteFilter の key。カンマ区切り。全てが付いている銘柄)
        exclude: 除く備考(low_liquidity、deficit。カンマ区切り)
        sort: 並べ替えの項目(省略すると主となる日数の市場比。SORT_FIXED か「rel_20」のような形)
        order: asc(小さい順)か desc(大きい順)
        offset: 何件目から返すか
    """
    pred_date = resolve_pred_date(conn, target)
    main = const.MAIN_HORIZON
    sort = sort or f"rel_{main}"
    if order not in ("asc", "desc"):
        raise ApiError(400, "order は asc か desc を指定してください")
    if offset < 0:
        raise ApiError(400, "offset は 0 以上を指定してください")
    filters = {f.key: f for f in note_filters()}
    note_keys, exclude_keys = split_csv(notes), split_csv(exclude)
    for key in note_keys:
        if key not in filters:
            raise ApiError(400, f"備考の指定が正しくありません({key})")
    for key in exclude_keys:
        if key not in EXCLUDE_KEYS:
            raise ApiError(400, f"除く備考の指定が正しくありません({key})")
    rel_min, up_min, ret_min = _percent(min_rel, "市場比"), _percent(min_up, "上昇確率"), _percent(min_ret, "見込み")

    # 予測日の全ての銘柄(予測日数ごとの値)
    stocks = load_pred_stocks(conn, pred_date)
    code_count = len(stocks)

    # 条件で絞る(備考以外)
    text = q.strip()
    market_set, sector_set = set(split_csv(markets)), set(split_csv(sectors))
    rows = list(stocks.values())
    if text:
        prefix, part = text.upper(), text.lower()
        rows = [r for r in rows if str(r["code"]).startswith(prefix) or part in str(r["name"]).lower()]
    if market_set:
        rows = [r for r in rows if r["market"] in market_set]
    if sector_set:
        rows = [r for r in rows if r["sector33_code"] in sector_set]
    for key, limit in (("rel", rel_min), ("up", up_min), ("ret", ret_min)):
        if limit is not None:
            rows = [r for r in rows if (v := stock_value(r, key, main)) is not None and v >= limit]

    # 並べ替え
    reverse = order == "desc"
    if sort in SORT_FIXED:
        numeric = sort in ("rank", "base_price")
        with_value = [r for r in rows if r[sort] not in (None, "")]
        without = [r for r in rows if r[sort] in (None, "")]
        with_value.sort(key=lambda r: (float(r[sort]) if numeric else str(r[sort]), str(r["code"])), reverse=reverse)
        rows = with_value + without
    else:
        key, _, h = sort.partition("_")
        if key not in SORT_BY_HORIZON or not h.isdigit() or int(h) not in const.PREDICT_HORIZONS:
            raise ApiError(400, f"並べ替えの指定が正しくありません({sort})")
        horizon = int(h)
        with_value = [r for r in rows if stock_value(r, key, horizon) is not None]
        without = [r for r in rows if stock_value(r, key, horizon) is None]
        with_value.sort(key=lambda r: (stock_value(r, key, horizon) or 0.0, str(r["code"])), reverse=reverse)
        rows = with_value + without

    # 備考で絞る・除く(予測した全銘柄の備考を計算する。2回目からはメモリから読む)
    if note_keys or exclude_keys:
        all_notes = note_cache.get(conn, pred_date, list(stocks))
        rows = [
            r
            for r in rows
            if all(filters[k].matches(all_notes.get(r["code"], [])) for k in note_keys)
            and not any(filters[k].matches(all_notes.get(r["code"], [])) for k in exclude_keys)
        ]
    total = len(rows)
    page = rows[offset : offset + web_const.SCREEN_PAGE_SIZE]
    page_notes = note_cache.get(conn, pred_date, [str(r["code"]) for r in page])

    return ScreenResponse(
        pred_date=pred_date,
        code_count=code_count,
        total=total,
        offset=offset,
        has_more=offset + len(page) < total,
        sort=sort,
        order=order,
        rows=[
            ScreenRow(
                rank=r["rank"],
                code=str(r["code"]),
                name=str(r["name"]),
                market=str(r["market"]),
                sector33=str(r["sector33"]),
                base_price=float(r["base_price"]),
                horizons=horizon_values(r["by_h"]),
                notes=to_notes(page_notes.get(str(r["code"]), [])),
            )
            for r in page
        ],
        market_options=market_options_of(stocks),
        sector_options=load_sector_options(conn),
        note_options=[Option(value=f.key, label=f.label) for f in note_filters()],
        exclude_options=[Option(value=k, label=f"{filters[k].label}を除く") for k in EXCLUDE_KEYS],
    )
