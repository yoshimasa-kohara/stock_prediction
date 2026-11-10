"""保有銘柄の API。画面が DB に書き込むのは、この holding(保有銘柄)だけ。

GET  /api/holdings: 保有中の銘柄(銘柄ごとの合計の行と購入ごとの行)と判定・損益・備考、売却済みの行
POST /api/holdings: 登録(買い増しも、購入ごとに別の行として登録する)
PUT  /api/holdings/{holding_id}: 買値・買い株数・購入日・メモを直す(銘柄は変えられない)
POST /api/holdings/{holding_id}/sell: 売却(一部の売却は行を分ける。行は削除しない)

判定と損切りラインは、メールと同じく core/notify_core.py の judge_holding、hits_stop_loss を、平均買値で使う。
"""

from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

import const
from api import web_const
from api.common import HorizonValue, Note, horizon_values, resolve_pred_date, to_float, today
from api.deps import Conn
from api.errors import ApiError
from api.logger import log_api
from api.notes import signal_notes, to_notes
from core.notify_core import (
    average_buy_price,
    hits_stop_loss,
    holding_totals,
    judge_holding,
    stale_close_note,
    stop_loss_note,
)

router = APIRouter()


# =====================================================================
# 一覧
# =====================================================================


class Lot(BaseModel):
    """購入ごとの行(holding の1行)。"""

    holding_id: int
    buy_price: float
    quantity: int
    buy_date: date
    note: str | None
    pnl_rate: float | None
    pnl_amount: float | None
    # 買いの金額(買値 × 買い株数)と現在の金額(現在値 × 買い株数。現在値がなければ None)
    buy_amount: float
    current_amount: float | None


class HoldingGroup(BaseModel):
    """銘柄ごとの合計の行(同じ銘柄の購入ごとの行をまとめたもの)。"""

    code: str
    name: str
    market: str
    quantity: int
    avg_buy_price: float
    current: float | None
    current_date: date | None
    cost: float
    value: float | None
    # 買いの金額(平均買値 × 合計の株数)と現在の金額(現在値 × 合計の株数。現在値がなければ None)
    buy_amount: float
    current_amount: float | None
    pnl_rate: float | None
    pnl_amount: float | None
    horizons: list[HorizonValue]
    judgment: str
    notes: list[Note]
    lots: list[Lot]


class SoldRow(BaseModel):
    """売却済みの1行(購入ごとの行のまま。まとめない)。"""

    holding_id: int
    code: str
    name: str
    market: str
    buy_price: float
    quantity: int
    buy_date: date
    sell_price: float
    # 買いの金額(買値 × 売り株数)と売りの金額(売値 × 売り株数。売値がない行は買値で数える)
    buy_amount: float
    sell_amount: float
    sell_date: date
    current: float | None
    pnl_rate: float | None
    pnl_amount: float | None
    held_days: int
    note: str | None


class Totals(BaseModel):
    """保有中の銘柄の合計。現在値がない銘柄は、評価額と損益に含めない。"""

    cost: float
    value: float
    pnl_amount: float
    pnl_rate: float | None


class HoldingsResponse(BaseModel):
    """/api/holdings の返り値。judge_date は判定に使った予測日(最新の予測日。予測がなければ None)。"""

    judge_date: date | None
    groups: list[HoldingGroup]
    totals: Totals
    sold: list[SoldRow]
    sold_count: int
    sold_pnl_amount: float
    # 売却済みの合計。買値・売値の合計は (値 × 売り株数) の合計、損益(%)は損益 ÷ 買値の合計
    sold_buy_amount: float
    sold_sell_amount: float
    sold_pnl_rate: float | None


def _pnl(buy_price: Decimal, current: Decimal | None, quantity: int) -> tuple[float | None, float | None]:
    """損益(率、円)。現在値がなければ None。"""
    if current is None or buy_price <= 0:
        return None, None
    return float((current - buy_price) / buy_price), float((current - buy_price) * quantity)


def _latest_closes(conn: Conn, codes: list[str], until: date | None) -> dict[str, tuple[Decimal, date]]:
    """銘柄ごとの、until 以前で一番新しい終値と、その日(until が None なら全ての日で一番新しい終値)。"""
    if not codes:
        return {}
    rows = conn.execute(
        """
        SELECT DISTINCT ON (code) code, close, trade_date FROM daily_price
        WHERE code = ANY(%s) AND close IS NOT NULL AND (%s::date IS NULL OR trade_date <= %s::date)
        ORDER BY code, trade_date DESC
        """,
        (codes, until, until),
    ).fetchall()
    return {str(r["code"]): (Decimal(r["close"]), r["trade_date"]) for r in rows}


@router.get("/api/holdings", response_model=HoldingsResponse)
@log_api
def get_holdings(
    conn: Conn, target: Annotated[date | None, Query(alias="date")] = None, include_sold: bool = True
) -> HoldingsResponse:
    """保有銘柄の一覧。保有中は売却日がない行(購入日が判定の日より後の行も含む)。

    判定は判定の日(省略すると最新の予測日)の予測で、銘柄ごとに平均買値で1つ出す(メールと同じ)。
    現在値は、判定の日以前で一番新しい終値(調整前。メールと同じ)。
    """
    judge_date: date | None
    try:
        judge_date = resolve_pred_date(conn, target)
    except ApiError:
        if target is not None:
            raise
        judge_date = None  # 予測がまだないときも、保有銘柄の登録はできるようにする
    master = {
        str(r["code"]): r
        for r in conn.execute("SELECT code, name, COALESCE(market_name, '') AS market FROM stock_master").fetchall()
    }
    open_rows = conn.execute("SELECT * FROM holding WHERE sell_date IS NULL ORDER BY buy_date, holding_id").fetchall()
    codes = list(dict.fromkeys(str(r["code"]) for r in open_rows))
    closes = _latest_closes(conn, codes, judge_date)
    preds: dict[str, dict[int, Any]] = {}
    if judge_date is not None and codes:
        for p in conn.execute(
            "SELECT code, horizon, rel_prob, up_prob, pred_price FROM prediction"
            " WHERE pred_date = %s AND code = ANY(%s)",
            (judge_date, codes),
        ).fetchall():
            preds.setdefault(str(p["code"]), {})[int(p["horizon"])] = p
    signals = signal_notes(conn, judge_date, codes) if judge_date is not None else {}

    groups: list[HoldingGroup] = []
    total_items: list[tuple[Decimal, Decimal | None, int]] = []
    for code in codes:
        rows = [r for r in open_rows if str(r["code"]) == code]
        cost = sum((Decimal(r["buy_price"]) * int(r["quantity"]) for r in rows), Decimal(0))
        quantity = sum(int(r["quantity"]) for r in rows)
        avg = average_buy_price(cost, quantity)
        current, current_date = closes.get(code, (None, None))
        by_h = preds.get(code, {})
        main = by_h.get(const.MAIN_HORIZON)
        judgment = judge_holding(
            None if main is None else Decimal(main["rel_prob"]),
            None if main is None else Decimal(main["up_prob"]),
            avg,
            current,
        )
        # 現在値がその日の終値でないときは、何日の終値かを備考の最初に出す(メールと同じ)
        notes = [stale_close_note(current_date)] if current_date is not None and current_date != judge_date else []
        if hits_stop_loss(avg, current):
            notes.append(stop_loss_note())
        notes += signals.get(code, [])
        rate, amount = _pnl(avg, current, quantity)
        total_items.append((cost, current, quantity))
        info = master.get(code)
        groups.append(
            HoldingGroup(
                code=code,
                name=str(info["name"]) if info else "",
                market=str(info["market"]) if info else "",
                quantity=quantity,
                avg_buy_price=float(avg),
                current=to_float(current),
                current_date=current_date,
                cost=float(cost),
                value=None if current is None else float(current * quantity),
                buy_amount=float(cost),
                current_amount=None if current is None else float(current * quantity),
                pnl_rate=rate,
                pnl_amount=amount,
                horizons=horizon_values(by_h),
                judgment=judgment,
                notes=to_notes(notes),
                lots=[
                    Lot(
                        holding_id=int(r["holding_id"]),
                        buy_price=float(r["buy_price"]),
                        quantity=int(r["quantity"]),
                        buy_date=r["buy_date"],
                        note=r["note"],
                        buy_amount=float(Decimal(r["buy_price"]) * int(r["quantity"])),
                        current_amount=None if current is None else float(current * int(r["quantity"])),
                        pnl_rate=_pnl(Decimal(r["buy_price"]), current, int(r["quantity"]))[0],
                        pnl_amount=_pnl(Decimal(r["buy_price"]), current, int(r["quantity"]))[1],
                    )
                    for r in rows
                ],
            )
        )
    # 合計はメールの表の上の合計と同じ関数で求める
    t = holding_totals(total_items)
    totals = Totals(
        cost=float(t.cost),
        value=float(t.value),
        pnl_amount=float(t.pnl_amount),
        pnl_rate=None if t.pnl_rate is None else float(t.pnl_rate),
    )

    sold_rows = conn.execute(
        "SELECT * FROM holding WHERE sell_date IS NOT NULL ORDER BY sell_date DESC, holding_id DESC"
    ).fetchall()
    sold_closes = _latest_closes(conn, list({str(r["code"]) for r in sold_rows}), None)
    sold: list[SoldRow] = []
    sold_buy = Decimal(0)
    sold_sell = Decimal(0)
    for r in sold_rows:
        code = str(r["code"])
        buy = Decimal(r["buy_price"])
        sell = Decimal(r["sell_price"]) if r["sell_price"] is not None else buy
        qty = int(r["quantity"])
        # 損益は売値で計算する(損益(円)= (売値 − 買値) × 売り株数)
        rate, amount = _pnl(buy, sell, qty)
        # 一部の売却の行は、売った株数が quantity に入っている
        sold_buy += buy * qty
        sold_sell += sell * qty
        info = master.get(code)
        sold.append(
            SoldRow(
                holding_id=int(r["holding_id"]),
                code=code,
                name=str(info["name"]) if info else "",
                market=str(info["market"]) if info else "",
                buy_price=float(buy),
                quantity=qty,
                buy_date=r["buy_date"],
                sell_price=float(sell),
                buy_amount=float(buy * qty),
                sell_amount=float(sell * qty),
                sell_date=r["sell_date"],
                current=to_float(sold_closes.get(code, (None, None))[0]),
                pnl_rate=rate,
                pnl_amount=amount,
                held_days=(r["sell_date"] - r["buy_date"]).days,
                note=r["note"],
            )
        )
    return HoldingsResponse(
        judge_date=judge_date,
        groups=groups,
        totals=totals,
        sold=sold if include_sold else [],
        sold_count=len(sold),
        sold_pnl_amount=float(sold_sell - sold_buy),
        sold_buy_amount=float(sold_buy),
        sold_sell_amount=float(sold_sell),
        sold_pnl_rate=float((sold_sell - sold_buy) / sold_buy) if sold_buy > 0 else None,
    )


# =====================================================================
# 登録・編集・売却
# =====================================================================


class HoldingInput(BaseModel):
    """登録の入力。"""

    code: str
    buy_price: float
    quantity: int
    buy_date: date
    note: str | None = None


class HoldingUpdate(BaseModel):
    """編集の入力(銘柄は変えられない)。"""

    buy_price: float
    quantity: int
    buy_date: date
    note: str | None = None


class SellInput(BaseModel):
    """売却の入力。"""

    sell_date: date
    quantity: int
    sell_price: float


class HoldingSaved(BaseModel):
    """登録・編集・売却の返り値。sold_holding_id は一部の売却で足した行の ID。"""

    holding_id: int
    sold_holding_id: int | None = None
    message: str


def _check_lot(buy_price: float, quantity: int, buy_date: date, note: str | None) -> str | None:
    """買値・買い株数・購入日・メモを確かめる。メモは前後の空白を除いて返す。"""
    if not buy_price > 0:
        raise ApiError(400, "買値は0より大きい値を入れてください")
    if quantity <= 0:
        raise ApiError(400, "買い株数は1以上を入れてください")
    if buy_date > today():
        raise ApiError(400, "購入日は今日以前の日付を入れてください")
    text = (note or "").strip()
    if len(text) > web_const.HOLDING_NOTE_MAX_LENGTH:
        raise ApiError(400, f"メモは{web_const.HOLDING_NOTE_MAX_LENGTH}文字以内にしてください")
    return text or None


def _load_open(conn: Conn, holding_id: int) -> dict[str, Any]:
    """売却していない保有銘柄の行。なければ 404、売却済みなら 400。"""
    row = conn.execute("SELECT * FROM holding WHERE holding_id = %s", (holding_id,)).fetchone()
    if row is None:
        raise ApiError(404, "保有銘柄が見つかりません")
    if row["sell_date"] is not None:
        raise ApiError(400, "売却済みの行は変更できません")
    return dict(row)


@router.post("/api/holdings", response_model=HoldingSaved)
@log_api
def create_holding(conn: Conn, body: HoldingInput) -> HoldingSaved:
    """保有銘柄を1行登録する。"""
    code = body.code.strip().upper()
    if conn.execute("SELECT 1 FROM stock_master WHERE code = %s", (code,)).fetchone() is None:
        raise ApiError(400, "銘柄が見つかりません")
    note = _check_lot(body.buy_price, body.quantity, body.buy_date, body.note)
    row = conn.execute(
        """
        INSERT INTO holding (code, buy_price, quantity, buy_date, note, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, now(), now())
        RETURNING holding_id
        """,
        (code, Decimal(str(body.buy_price)), body.quantity, body.buy_date, note),
    ).fetchone()
    assert row is not None
    return HoldingSaved(holding_id=int(row["holding_id"]), message="登録しました")


@router.put("/api/holdings/{holding_id}", response_model=HoldingSaved)
@log_api
def update_holding(conn: Conn, holding_id: int, body: HoldingUpdate) -> HoldingSaved:
    """保有銘柄の行の、買値・買い株数・購入日・メモを直す。"""
    _load_open(conn, holding_id)
    note = _check_lot(body.buy_price, body.quantity, body.buy_date, body.note)
    conn.execute(
        """
        UPDATE holding SET buy_price = %s, quantity = %s, buy_date = %s, note = %s, updated_at = now()
        WHERE holding_id = %s
        """,
        (Decimal(str(body.buy_price)), body.quantity, body.buy_date, note, holding_id),
    )
    return HoldingSaved(holding_id=holding_id, message="保存しました")


@router.post("/api/holdings/{holding_id}/sell", response_model=HoldingSaved)
@log_api
def sell_holding(conn: Conn, holding_id: int, body: SellInput) -> HoldingSaved:
    """売却を登録する。全部の売却はその行に売却日と売値を入れ、一部の売却は1つのトランザクションで行を分ける。"""
    row = _load_open(conn, holding_id)
    held = int(row["quantity"])
    if body.sell_date < row["buy_date"] or body.sell_date > today():
        raise ApiError(400, "売却日は購入日以降で、今日以前の日付を入れてください")
    if not 1 <= body.quantity <= held:
        raise ApiError(400, f"売却株数は1〜{held}株にしてください")
    if not body.sell_price > 0:
        raise ApiError(400, "売値は0より大きい値を入れてください")
    price = Decimal(str(body.sell_price))
    if body.quantity == held:
        conn.execute(
            "UPDATE holding SET sell_date = %s, sell_price = %s, updated_at = now() WHERE holding_id = %s",
            (body.sell_date, price, holding_id),
        )
        return HoldingSaved(holding_id=holding_id, message="売却を登録しました")
    with conn.transaction():
        conn.execute(
            "UPDATE holding SET quantity = %s, updated_at = now() WHERE holding_id = %s",
            (held - body.quantity, holding_id),
        )
        new = conn.execute(
            """
            INSERT INTO holding (code, buy_price, quantity, buy_date, sell_date, sell_price, note,
                                 created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, now(), now())
            RETURNING holding_id
            """,
            (row["code"], row["buy_price"], body.quantity, row["buy_date"], body.sell_date, price, row["note"]),
        ).fetchone()
    assert new is not None
    return HoldingSaved(
        holding_id=holding_id,
        sold_holding_id=int(new["holding_id"]),
        message=f"{body.quantity}株の売却を登録しました(残り {held - body.quantity}株は保有中のままです)",
    )
