"""検討中銘柄の API。

利用者が、買うか迷っている銘柄を自分で選んで残しておく(メモ付き)。画面が書き込むテーブルは、
holding(保有銘柄)と、この watch_stock(検討中銘柄)の2つ。メール・バッチの処理には影響しない。

GET    /api/watch-stocks/codes: 追加済みの銘柄のコードとメモ(軽い API。4画面の ☆・★ の判定と、メモの初期値に使う)
GET    /api/watch-stocks: 一覧(追加した日の予測と、最新の予測日の予測を並べる)
PUT    /api/watch-stocks/{code}: 追加する。追加済みならメモだけを直す
DELETE /api/watch-stocks/{code}: 削除する

予測の値・備考の作り方は、買い候補・銘柄検索と同じ関数(api/common.py の horizon_values、api/notes.py)を使う。
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from api import web_const
from api.common import HorizonValue, Note, horizon_values, latest_pred_date, to_float
from api.deps import Conn
from api.errors import ApiError
from api.logger import log_api
from api.notes import stock_notes, to_notes
from api.routers import screen
from api.routers.stocks import MSG_STOCK_NOT_FOUND

router = APIRouter()

# 検討中銘柄に入っていない銘柄を、削除しようとしたとき
MSG_NOT_WATCHED: str = "検討中銘柄に入っていません"


# =====================================================================
# コードとメモの一覧(☆・★ の判定用)
# =====================================================================


class WatchCode(BaseModel):
    """追加済みの1銘柄(コードとメモだけ)。"""

    code: str
    memo: str


class WatchCodesResponse(BaseModel):
    """/api/watch-stocks/codes の返り値。"""

    items: list[WatchCode]


@router.get("/api/watch-stocks/codes", response_model=WatchCodesResponse)
@log_api
def get_watch_codes(conn: Conn) -> WatchCodesResponse:
    """追加済みの銘柄のコードとメモ(コードの順)。"""
    rows = conn.execute("SELECT code, memo FROM watch_stock ORDER BY code").fetchall()
    return WatchCodesResponse(items=[WatchCode(code=str(r["code"]), memo=str(r["memo"])) for r in rows])


# =====================================================================
# 一覧
# =====================================================================


class WatchPoint(BaseModel):
    """ある予測日の時点の値(追加した日、または最新の予測日)。

    close はその予測日の終値(調整前)。has_prediction は、その予測日の予測にその銘柄があるか。
    ないときは horizons の値を全て None にする(行は返す)。予測日がないとき(予測が1件もないとき)は備考も空。
    """

    pred_date: date | None
    close: float | None
    has_prediction: bool
    horizons: list[HorizonValue]
    notes: list[Note]


class WatchStockRow(BaseModel):
    """検討中銘柄の1銘柄。change_rate は値上がり率(latest の終値 ÷ picked の終値 − 1。どちらかがなければ None)。"""

    code: str
    name: str
    market: str
    sector33: str
    memo: str
    created_at: datetime
    updated_at: datetime
    picked: WatchPoint
    latest: WatchPoint
    change_rate: float | None


class WatchStocksResponse(BaseModel):
    """/api/watch-stocks の返り値。latest_pred_date は最新の予測日(予測がなければ None)。"""

    latest_pred_date: date | None
    rows: list[WatchStockRow]


def _predictions(conn: Conn, pairs: set[tuple[date, str]]) -> dict[tuple[date, str], dict[int, Any]]:
    """(予測日, 銘柄コード) ごとの、予測日数 → 予測の行。"""
    result: dict[tuple[date, str], dict[int, Any]] = {}
    if not pairs:
        return result
    rows = conn.execute(
        """
        SELECT pred_date, code, horizon, rel_prob, up_prob, pred_price FROM prediction
        WHERE pred_date = ANY(%s) AND code = ANY(%s)
        """,
        (sorted({d for d, _ in pairs}), sorted({c for _, c in pairs})),
    ).fetchall()
    for r in rows:
        key = (r["pred_date"], str(r["code"]))
        if key in pairs:
            result.setdefault(key, {})[int(r["horizon"])] = r
    return result


def _closes(conn: Conn, target: date, codes: list[str]) -> dict[str, Decimal]:
    """その日の終値(調整前)。終値がない銘柄は入れない。"""
    rows = conn.execute(
        "SELECT code, close FROM daily_price WHERE trade_date = %s AND code = ANY(%s) AND close IS NOT NULL",
        (target, codes),
    ).fetchall()
    return {str(r["code"]): Decimal(r["close"]) for r in rows}


def _point(
    pred_date: date | None, close: Decimal | None, by_horizon: dict[int, Any] | None, notes: list[str]
) -> WatchPoint:
    return WatchPoint(
        pred_date=pred_date,
        close=to_float(close),
        has_prediction=bool(by_horizon),
        horizons=horizon_values(by_horizon or {}),
        notes=to_notes(notes),
    )


@router.get("/api/watch-stocks", response_model=WatchStocksResponse)
@log_api
def get_watch_stocks(conn: Conn) -> WatchStocksResponse:
    """検討中銘柄の一覧(追加した日時の新しい順)。

    追加した日(picked)は watch_stock の pred_date・base_price、最新(latest)は最新の予測日とその日の終値。
    備考はバッチの core で計算する(api/notes.py)。最新の予測日の備考は、銘柄検索と同じ入れ物に残す。
    """
    rows = conn.execute(
        """
        SELECT w.code, w.memo, w.pred_date, w.base_price, w.created_at, w.updated_at,
               COALESCE(m.name, '') AS name, COALESCE(m.market_name, '') AS market,
               COALESCE(m.sector33_name, '') AS sector33
        FROM watch_stock w LEFT JOIN stock_master m ON m.code = w.code
        ORDER BY w.created_at DESC, w.code
        """
    ).fetchall()
    latest = latest_pred_date(conn)
    codes = [str(r["code"]) for r in rows]
    pairs: set[tuple[date, str]] = {(r["pred_date"], str(r["code"])) for r in rows if r["pred_date"] is not None}
    if latest is not None:
        pairs |= {(latest, c) for c in codes}
    preds = _predictions(conn, pairs)
    latest_closes = _closes(conn, latest, codes) if latest is not None and codes else {}
    # 備考: 最新の予測日は銘柄検索と同じ入れ物(2回目からは計算しない)。追加した日は、その日ごとに計算する
    notes: dict[date, dict[str, list[str]]] = {}
    if latest is not None and codes:
        notes[latest] = screen.note_cache.get(conn, latest, codes)
    for d in sorted({r["pred_date"] for r in rows if r["pred_date"] is not None and r["pred_date"] != latest}):
        notes[d] = stock_notes(conn, d, [str(r["code"]) for r in rows if r["pred_date"] == d])

    result: list[WatchStockRow] = []
    for r in rows:
        code = str(r["code"])
        picked_date: date | None = r["pred_date"]
        picked_close: Decimal | None = None if r["base_price"] is None else Decimal(r["base_price"])
        latest_close = latest_closes.get(code)
        change = (
            float(latest_close / picked_close - 1)
            if latest_close is not None and picked_close is not None and picked_close > 0
            else None
        )
        result.append(
            WatchStockRow(
                code=code,
                name=str(r["name"]),
                market=str(r["market"]),
                sector33=str(r["sector33"]),
                memo=str(r["memo"]),
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                picked=_point(
                    picked_date,
                    picked_close,
                    None if picked_date is None else preds.get((picked_date, code)),
                    [] if picked_date is None else notes.get(picked_date, {}).get(code, []),
                ),
                latest=_point(
                    latest,
                    latest_close,
                    None if latest is None else preds.get((latest, code)),
                    [] if latest is None else notes.get(latest, {}).get(code, []),
                ),
                change_rate=change,
            )
        )
    return WatchStocksResponse(latest_pred_date=latest, rows=result)


# =====================================================================
# 追加・メモの編集・削除
# =====================================================================


class WatchInput(BaseModel):
    """追加・メモの編集の入力。メモは空でもよい。"""

    memo: str = ""


class WatchSaved(BaseModel):
    """追加・メモの編集・削除の返り値。created は、新しく追加したとき True。"""

    code: str
    created: bool
    message: str


@router.put("/api/watch-stocks/{code}", response_model=WatchSaved)
@log_api
def put_watch_stock(conn: Conn, code: str, body: WatchInput) -> WatchSaved:
    """検討中銘柄に追加する。追加済みなら、メモと更新日時だけを直す(pred_date・base_price は最初のまま)。

    追加するときの pred_date は、押した時点の最新の予測日(どの画面のどの予測日の行から押しても同じ)。
    base_price はその日の終値(終値がなければ NULL)。予測が1件もないときは、どちらも NULL。
    """
    code = code.strip().upper()
    if conn.execute("SELECT 1 FROM stock_master WHERE code = %s", (code,)).fetchone() is None:
        raise ApiError(404, MSG_STOCK_NOT_FOUND)
    memo = body.memo.strip()
    if len(memo) > web_const.WATCH_STOCK_MEMO_MAX_LENGTH:
        raise ApiError(400, f"メモは{web_const.WATCH_STOCK_MEMO_MAX_LENGTH}文字以内にしてください")
    pred_date = latest_pred_date(conn)
    base_price = None if pred_date is None else _closes(conn, pred_date, [code]).get(code)
    # 同じ銘柄を同時に2回押しても1行になるように、1つの SQL で追加か更新かを決める(xmax = 0 は新しく入った行)
    row = conn.execute(
        """
        INSERT INTO watch_stock (code, memo, pred_date, base_price, created_at, updated_at)
        VALUES (%s, %s, %s, %s, now(), now())
        ON CONFLICT (code) DO UPDATE SET memo = EXCLUDED.memo, updated_at = now()
        RETURNING (xmax = 0) AS created
        """,
        (code, memo, pred_date, base_price),
    ).fetchone()
    assert row is not None
    created = bool(row["created"])
    message = "検討中銘柄に追加しました" if created else "メモを保存しました"
    return WatchSaved(code=code, created=created, message=message)


@router.delete("/api/watch-stocks/{code}", response_model=WatchSaved)
@log_api
def delete_watch_stock(conn: Conn, code: str) -> WatchSaved:
    """検討中銘柄から削除する。入っていなければ 404。"""
    code = code.strip().upper()
    row = conn.execute("DELETE FROM watch_stock WHERE code = %s RETURNING code", (code,)).fetchone()
    if row is None:
        raise ApiError(404, MSG_NOT_WATCHED)
    return WatchSaved(code=code, created=False, message="検討中銘柄から削除しました")
