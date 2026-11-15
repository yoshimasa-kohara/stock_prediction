"""銘柄詳細の業績の計算。

fin_summary(財務情報)の開示から、通期・四半期の表、会社予想と修正、修正の履歴、予想の配当利回りを作る。
パスの登録は api/routers/stocks.py(GET /api/stocks/{code}/financials)。

- 表の行に使う開示: 売上高・営業利益・純利益のどれかに値がある開示。同じ期(会計期間の種類と事業年度の終了日が同じ)の
  開示が複数ある(訂正)ときは、開示日・開示時刻が一番新しいもの(開示番号は順番を保証しない)
- 予想の修正だけの開示(doc_type が EarnForecastRevision・DividendForecastRevision など)は、売上高・営業利益・純利益が
  全て NULL なので、表の行には入らない(2026-11-15 に本番の DB で確かめた)。予想と修正の履歴には使う
- 1株あたりの値(EPS・配当)は、開示日より後の調整係数で株式分割を補正する
- 今の事業年度の最初の予想(期初の予想)は、前の事業年度の通期の決算短信の来期の予想(nx_f_*)。予想と修正の履歴では、
  これを今の事業年度の一番古い開示として扱う(決定事項)
"""

from dataclasses import dataclass
from datetime import date, time
from typing import Any

from pydantic import BaseModel

import const
from api import web_const
from api.common import latest_pred_date, to_float
from api.deps import Conn

# 会計期間の種類の並び(同じ事業年度の中の古い順)。通期は最後
PER_TYPE_ORDER: list[str] = ["1Q", "2Q", "3Q", "4Q", "5Q", "FY"]
# 通期
PER_TYPE_FY: str = "FY"

# 前年比の判定(画面は、この値で文字と色を決める)
YOY_PERCENT: str = "percent"  # 前年も今期もプラス(rate に前年比)
YOY_TO_PROFIT: str = "to_profit"  # 黒字化
YOY_TO_LOSS: str = "to_loss"  # 赤字化
YOY_LOSS_SHRINK: str = "loss_shrink"  # 赤字縮小
YOY_LOSS_EXPAND: str = "loss_expand"  # 赤字拡大
YOY_NONE: str = "none"  # 「-」

# 修正の向き
DIRECTION_UP: str = "up"  # 上方修正
DIRECTION_DOWN: str = "down"  # 下方修正
DIRECTION_MIXED: str = "mixed"  # 上げた項目と下げた項目がある

# 開示の種類(修正の履歴に出す)
DOC_KIND_STATEMENT: str = "statement"  # 決算短信
DOC_KIND_EARN_REVISION: str = "earn_revision"  # 業績予想の修正
DOC_KIND_DIVIDEND_REVISION: str = "dividend_revision"  # 配当予想の修正
DOC_KIND_INITIAL: str = "initial"  # 期初の予想(前の事業年度の通期の決算短信)
DOC_KIND_OTHER: str = "other"

# 予想の項目(キー, 今期の予想のカラム, 来期の予想のカラム。来期の1株あたり利益の予想は fin_summary にない)
FORECAST_ITEMS: list[tuple[str, str, str | None]] = [
    ("sales", "f_sales", "nx_f_sales"),
    ("op", "f_op", "nx_f_op"),
    ("np", "f_np", "nx_f_np"),
    ("eps", "f_eps", None),
    ("div", "f_div_ann", "nx_f_div_ann"),
]
# 1株あたりの値(株式分割を補正する項目)
PER_SHARE_KEYS: tuple[str, ...] = ("eps", "div")
# 期初の予想に使う、前の事業年度の通期の決算短信のカラム(今期の予想のカラム, 来期の予想のカラム)。
# 1株あたり利益は来期の予想がないので、期初の予想とは比べない
INITIAL_FORECAST_COLUMNS: list[tuple[str, str]] = [(cur, nxt) for _, cur, nxt in FORECAST_ITEMS if nxt is not None]
# 修正の履歴に出す項目(キー, カラム)
REVISION_ITEMS: list[tuple[str, str]] = [("sales", "f_sales"), ("op", "f_op"), ("np", "f_np")]
# 同じ値とみなす桁数(1株あたりの値は補正で小数が出るため)
_COMPARE_DIGITS: int = 4


class YoY(BaseModel):
    """前年比。kind は YOY_* のどれか。rate は kind が percent のときだけ入る(割合。0.123 は +12.3%)。"""

    kind: str
    rate: float | None = None


class FinancialRow(BaseModel):
    """通期・四半期の表の1行。金額は円(四半期は期首からの累計)。eps は株式分割を補正した値(円)。"""

    period_label: str  # 例: 2026年3月期 通期、2027年3月期 1Q
    per_type: str
    fy_end: date
    disc_date: date
    sales: float | None
    sales_yoy: YoY
    op: float | None
    op_yoy: YoY
    np: float | None
    np_yoy: YoY
    op_margin: float | None  # 営業利益率(営業利益 ÷ 売上高)
    eps: float | None


class ForecastItem(BaseModel):
    """予想の1項目。value がなければ None。直前の違う値(prev_value)があれば、direction に修正の向きを入れる。"""

    key: str  # sales・op・np・eps・div
    value: float | None
    prev_value: float | None = None
    direction: str | None = None  # up(上方修正)・down(下方修正)・None
    revised_date: date | None = None  # 今の値になった開示日(修正があったときだけ)


class Forecast(BaseModel):
    """会社予想。current は今期、next は来期(来期の1株あたり利益は fin_summary にないので value は None)。"""

    fy_end: date
    period_label: str  # 今期(例: 2027年3月期)
    next_period_label: str  # 来期(例: 2028年3月期)
    disc_date: date  # 今の事業年度の、一番新しい開示日
    current: list[ForecastItem]
    next: list[ForecastItem]


class RevisionRow(BaseModel):
    """今の事業年度の予想の履歴の1行(最初の予想と、値が変わった開示)。"""

    disc_date: date
    doc_kind: str  # DOC_KIND_* のどれか
    sales: float | None
    op: float | None
    np: float | None
    sales_direction: str | None
    op_direction: str | None
    np_direction: str | None
    direction: str | None  # 行の修正の向き(up・down・mixed。最初の予想は None)


class DividendInfo(BaseModel):
    """予想の配当利回りの元の値。is_next は、来期の予想(nx_f_div_ann)を使ったか。"""

    dividend: float  # 年間配当の予想(円。株式分割を補正した値)
    price: float
    price_date: date
    disc_date: date
    is_next: bool


class FinancialsResponse(BaseModel):
    """/api/stocks/{code}/financials の返り値。データがなければ配列は空、forecast・dividend_yield は None。"""

    code: str
    annual: list[FinancialRow]
    quarterly: list[FinancialRow]
    forecast: Forecast | None
    revisions: list[RevisionRow]
    dividend_yield: float | None
    dividend: DividendInfo | None


@dataclass(frozen=True)
class Disclosure:
    """fin_summary の1行(開示1件)。"""

    disc_no: str
    disc_date: date
    disc_time: time | None
    doc_type: str
    per_type: str | None
    fy_end: date | None
    values: dict[str, float | None]
    is_initial: bool = False  # 期初の予想(前の事業年度の通期の決算短信の来期の予想を、今期の予想のカラムに入れたもの)

    @property
    def order(self) -> tuple[date, time, str]:
        """開示の新しさ(開示日 → 開示時刻 → 開示番号。時刻がなければ 0時とみなす)。"""
        return (self.disc_date, self.disc_time or time.min, self.disc_no)

    def get(self, column: str) -> float | None:
        return self.values.get(column)

    @property
    def has_results(self) -> bool:
        """売上高・営業利益・純利益のどれかに値があるか(全て NULL の開示は表の行に使わない)。"""
        return any(self.get(c) is not None for c in ("sales", "op", "np"))


# fin_summary から読む数値のカラム
_VALUE_COLUMNS: list[str] = [
    "sales", "op", "np", "eps", "f_sales", "f_op", "f_np", "f_eps", "f_div_ann",
    "nx_f_sales", "nx_f_op", "nx_f_np", "nx_f_div_ann",
]  # fmt: skip


def to_disclosure(row: dict[str, Any]) -> Disclosure:
    return Disclosure(
        disc_no=str(row["disc_no"]),
        disc_date=row["disc_date"],
        disc_time=row["disc_time"],
        doc_type=str(row["doc_type"]),
        per_type=row["cur_per_type"],
        fy_end=row["cur_fy_en"],
        values={c: to_float(row[c]) for c in _VALUE_COLUMNS},
    )


def load_disclosures(conn: Conn, code: str) -> list[Disclosure]:
    """その銘柄の全ての開示(古い順)。"""
    rows = conn.execute(
        f"""
        SELECT disc_no, disc_date, disc_time, doc_type, cur_per_type, cur_fy_en, {", ".join(_VALUE_COLUMNS)}
        FROM fin_summary WHERE code = %s
        """,
        (code,),
    ).fetchall()
    return sorted((to_disclosure(dict(r)) for r in rows), key=lambda d: d.order)


def load_split_factors(conn: Conn, code: str) -> list[tuple[date, float]]:
    """株式分割などがあった日と調整係数(1 でない日だけ。0 以下は 1 とみなして除く。common/price_adjust.py と同じ)。"""
    rows = conn.execute(
        "SELECT trade_date, adj_factor::float8 AS f FROM daily_price"
        " WHERE code = %s AND adj_factor <> 1 AND adj_factor > 0 ORDER BY trade_date",
        (code,),
    ).fetchall()
    return [(r["trade_date"], float(r["f"])) for r in rows]


def split_multiplier(factors: list[tuple[date, float]], after: date, until: date | None = None) -> float:
    """after より後(until があれば until 以前)の調整係数を全て掛けた値。

    開示日の1株あたりの値にこれを掛けると、後の株式分割を補正した値になる(1:2 の分割なら 0.5 倍)。
    """
    result = 1.0
    for d, f in factors:
        if d > after and (until is None or d <= until):
            result *= f
    return result


def doc_kind(doc_type: str) -> str:
    """開示の種類(doc_type に含まれる文字列で見分ける)。"""
    if const.FIN_STATEMENT_DOC_TYPE_KEYWORD in doc_type:
        return DOC_KIND_STATEMENT
    if web_const.FIN_DIVIDEND_REVISION_DOC_TYPE_KEYWORD in doc_type:
        return DOC_KIND_DIVIDEND_REVISION
    if web_const.FIN_EARN_REVISION_DOC_TYPE_KEYWORD in doc_type:
        return DOC_KIND_EARN_REVISION
    return DOC_KIND_OTHER


def fy_label(fy_end: date) -> str:
    """事業年度の名前(例: 2026年3月期)。"""
    return f"{fy_end.year}年{fy_end.month}月期"


def period_label(fy_end: date, per_type: str) -> str:
    """決算期の名前(例: 2026年3月期 通期、2027年3月期 1Q)。"""
    return f"{fy_label(fy_end)} {'通期' if per_type == PER_TYPE_FY else per_type}"


def months_between(newer: date, older: date) -> int:
    """2つの日付の月の差(日は見ない。2026-03-31 と 2025-03-20 は 12)。"""
    return (newer.year * 12 + newer.month) - (older.year * 12 + older.month)


def judge_yoy(current: float | None, previous: float | None) -> YoY:
    """前年比の判定。

    - 前年も今期もプラス: %(今期 ÷ 前年 − 1)
    - 前年が0以下で今期がプラス: 黒字化
    - 前年がプラスで今期が0以下: 赤字化
    - 両方マイナス: 赤字が小さくなれば赤字縮小、大きくなれば赤字拡大(同じなら「-」)
    - どちらかがない、前年が0で今期も0以下、前年がマイナスで今期が0: 「-」
    """
    if current is None or previous is None:
        return YoY(kind=YOY_NONE)
    if current > 0:
        if previous > 0:
            return YoY(kind=YOY_PERCENT, rate=current / previous - 1)
        return YoY(kind=YOY_TO_PROFIT)
    if previous > 0:
        return YoY(kind=YOY_TO_LOSS)
    if current < 0 and previous < 0:
        if current > previous:
            return YoY(kind=YOY_LOSS_SHRINK)
        if current < previous:
            return YoY(kind=YOY_LOSS_EXPAND)
    return YoY(kind=YOY_NONE)


def latest_by_period(discs: list[Disclosure]) -> dict[tuple[str, date], Disclosure]:
    """期(会計期間の種類, 事業年度の終了日)ごとの、表に使う開示(開示日・開示時刻が一番新しいもの)。"""
    result: dict[tuple[str, date], Disclosure] = {}
    for d in discs:
        if d.per_type is None or d.fy_end is None or not d.has_results:
            continue
        key = (d.per_type, d.fy_end)
        if key not in result or result[key].order < d.order:
            result[key] = d
    return result


def find_previous(periods: dict[tuple[str, date], Disclosure], per_type: str, fy_end: date) -> Disclosure | None:
    """前年同期の開示(同じ会計期間の種類で、事業年度の終了日が 11〜13か月前)。なければ None。

    4Q・5Q(決算期を変えたとき)は、前年に同じ期間がないので None。
    候補が複数あるときは、12か月前に一番近いもの(同じ近さなら新しい期)。
    """
    if per_type in web_const.FINANCIAL_NO_YOY_PER_TYPES:
        return None
    found = [
        (abs(months_between(fy_end, e) - 12), -e.toordinal(), d)
        for (t, e), d in periods.items()
        if t == per_type
        and web_const.FINANCIAL_YOY_MIN_MONTHS <= months_between(fy_end, e) <= web_const.FINANCIAL_YOY_MAX_MONTHS
    ]
    return min(found, key=lambda x: x[:2])[2] if found else None


def _period_sort_key(d: Disclosure) -> tuple[date, int]:
    assert d.fy_end is not None and d.per_type is not None
    order = PER_TYPE_ORDER.index(d.per_type) if d.per_type in PER_TYPE_ORDER else -1
    return (d.fy_end, order)


def build_rows(
    discs: list[Disclosure], factors: list[tuple[date, float]]
) -> tuple[list[FinancialRow], list[FinancialRow]]:
    """通期の表(通期の行だけ)と、四半期の表(全ての期)。どちらも新しい順で、web_const の件数まで。"""
    periods = latest_by_period(discs)
    ordered = sorted(periods.values(), key=_period_sort_key, reverse=True)

    def row(d: Disclosure) -> FinancialRow:
        assert d.fy_end is not None and d.per_type is not None
        prev = find_previous(periods, d.per_type, d.fy_end)
        sales, op, eps = d.get("sales"), d.get("op"), d.get("eps")
        return FinancialRow(
            period_label=period_label(d.fy_end, d.per_type),
            per_type=d.per_type,
            fy_end=d.fy_end,
            disc_date=d.disc_date,
            sales=sales,
            sales_yoy=judge_yoy(sales, prev.get("sales") if prev else None),
            op=op,
            op_yoy=judge_yoy(op, prev.get("op") if prev else None),
            np=d.get("np"),
            np_yoy=judge_yoy(d.get("np"), prev.get("np") if prev else None),
            op_margin=op / sales if op is not None and sales is not None and sales > 0 else None,
            eps=None if eps is None else eps * split_multiplier(factors, d.disc_date),
        )

    annual = [row(d) for d in ordered if d.per_type == PER_TYPE_FY][: web_const.FINANCIAL_ANNUAL_LIMIT]
    quarterly = [row(d) for d in ordered[: web_const.FINANCIAL_QUARTERLY_LIMIT]]
    return annual, quarterly


def find_initial_forecast(discs: list[Disclosure], fy_end: date) -> Disclosure | None:
    """今の事業年度の期初の予想。前の事業年度の通期の決算短信の来期の予想(nx_f_*)を、今期の予想のカラムに入れた開示。

    前の事業年度は、事業年度の終了日が 11〜13か月前(前年同期と同じ数え方)。候補が複数の期なら 12か月前に一番近い期
    (同じ近さなら新しい期)、同じ期が複数(訂正)なら開示日・開示時刻が一番新しいもの。
    決算短信がない、または来期の予想が全て NULL なら None。
    """
    found = [
        d
        for d in discs
        if d.per_type == PER_TYPE_FY
        and d.fy_end is not None
        and doc_kind(d.doc_type) == DOC_KIND_STATEMENT
        and web_const.FINANCIAL_YOY_MIN_MONTHS <= months_between(fy_end, d.fy_end) <= web_const.FINANCIAL_YOY_MAX_MONTHS
    ]
    if not found:
        return None

    def key(d: Disclosure) -> tuple[int, date, tuple[date, time, str]]:
        assert d.fy_end is not None
        return (-abs(months_between(fy_end, d.fy_end) - 12), d.fy_end, d.order)

    src = max(found, key=key)
    values = {cur: src.get(nxt) for cur, nxt in INITIAL_FORECAST_COLUMNS}
    if all(v is None for v in values.values()):
        return None
    return Disclosure(
        disc_no=src.disc_no,
        disc_date=src.disc_date,
        disc_time=src.disc_time,
        doc_type=src.doc_type,
        per_type=src.per_type,
        fy_end=src.fy_end,
        values=values,
        is_initial=True,
    )


def current_year_disclosures(discs: list[Disclosure]) -> tuple[date | None, list[Disclosure]]:
    """今の事業年度(事業年度の終了日が一番新しいもの)と、その事業年度の開示(古い順)。

    その事業年度の通期の決算が出ていれば、それ以降の開示だけを返す(終わった年度の予想を「今期の予想」として
    出さないため。通期の決算短信は f_* が NULL で、来期の予想 nx_f_* を持つ)。
    通期の決算が出ていなければ、期初の予想(find_initial_forecast)を先頭に入れる(なければ入れない)。
    """
    with_fy = [d for d in discs if d.fy_end is not None]
    if not with_fy:
        return None, []
    fy_end = max(d.fy_end for d in with_fy if d.fy_end is not None)
    same = [d for d in with_fy if d.fy_end == fy_end]
    closed = [d for d in same if d.per_type == PER_TYPE_FY and d.has_results]
    if closed:
        first = min(d.order for d in closed)
        same = [d for d in same if d.order >= first]
    else:
        initial = find_initial_forecast(discs, fy_end)
        if initial is not None:
            same = [initial, *same]
    return fy_end, same


def _same(a: float, b: float) -> bool:
    return round(a, _COMPARE_DIGITS) == round(b, _COMPARE_DIGITS)


def forecast_item(
    key: str, column: str | None, same: list[Disclosure], factors: list[tuple[date, float]]
) -> ForecastItem:
    """予想の1項目。一番新しい値と、その前の違う値(直前の修正)を比べて、修正の向きを決める。

    同じ値の開示が続くとき(修正と同じ日の決算短信など)は、さかのぼって最初の違う値と比べる(期初の予想も含める)。
    1株あたりの値は、株式分割を補正してから比べる。
    """
    if column is None:
        return ForecastItem(key=key, value=None)
    per_share = key in PER_SHARE_KEYS
    series: list[tuple[date, float]] = []
    for d in same:
        v = d.get(column)
        if v is not None:
            series.append((d.disc_date, v * split_multiplier(factors, d.disc_date) if per_share else v))
    if not series:
        return ForecastItem(key=key, value=None)
    value = series[-1][1]
    revised = series[-1][0]
    for disc_date, v in reversed(series[:-1]):
        if not _same(v, value):
            return ForecastItem(
                key=key,
                value=value,
                prev_value=v,
                direction=DIRECTION_UP if value > v else DIRECTION_DOWN,
                revised_date=revised,
            )
        revised = disc_date
    return ForecastItem(key=key, value=value)


def build_forecast(discs: list[Disclosure], factors: list[tuple[date, float]]) -> Forecast | None:
    """会社予想(今期・来期)。開示がなければ None。"""
    fy_end, same = current_year_disclosures(discs)
    if fy_end is None:
        return None
    next_end = date(fy_end.year + 1, fy_end.month, 1)
    return Forecast(
        fy_end=fy_end,
        period_label=fy_label(fy_end),
        next_period_label=fy_label(next_end),
        disc_date=same[-1].disc_date,
        current=[forecast_item(key, cur, same, factors) for key, cur, _ in FORECAST_ITEMS],
        next=[forecast_item(key, nxt, same, factors) for key, _, nxt in FORECAST_ITEMS],
    )


def build_revisions(discs: list[Disclosure]) -> list[RevisionRow]:
    """今の事業年度の予想の履歴(新しい順)。最初の予想と、売上高・営業利益・純利益の予想のどれかが変わった開示。

    期初の予想があれば、それが最初の予想(開示の種類は DOC_KIND_INITIAL)。
    """
    _, same = current_year_disclosures(discs)
    known: dict[str, float] = {}
    rows: list[RevisionRow] = []
    for d in same:
        values = {key: d.get(column) for key, column in REVISION_ITEMS}
        if all(v is None for v in values.values()):
            continue
        directions: dict[str, str | None] = {}
        for key, v in values.items():
            before = known.get(key)
            directions[key] = (
                None if v is None or before is None or _same(v, before)
                else DIRECTION_UP if v > before else DIRECTION_DOWN
            )  # fmt: skip
        first = not known
        known.update({k: v for k, v in values.items() if v is not None})
        changed = {x for x in directions.values() if x is not None}
        if not first and not changed:
            continue
        rows.append(
            RevisionRow(
                disc_date=d.disc_date,
                doc_kind=DOC_KIND_INITIAL if d.is_initial else doc_kind(d.doc_type),
                sales=values["sales"],
                op=values["op"],
                np=values["np"],
                sales_direction=directions["sales"],
                op_direction=directions["op"],
                np_direction=directions["np"],
                direction=None if not changed else changed.pop() if len(changed) == 1 else DIRECTION_MIXED,
            )
        )
    return rows[::-1]


def build_dividend(
    conn: Conn, code: str, discs: list[Disclosure], factors: list[tuple[date, float]]
) -> DividendInfo | None:
    """予想の配当利回りの元の値。年間配当の予想 ÷ 最新の予測日の株価(終値)。

    年間配当は、今の事業年度の一番新しい予想(f_div_ann)。今期の予想がなければ(通期の決算の後)、来期の予想
    (nx_f_div_ann)を使う。開示日より後で、株価の日以前の調整係数で株式分割を補正する。
    株価は、最新の予測日以前で一番新しい終値(予測がなければ一番新しい終値)。
    """
    _, same = current_year_disclosures(discs)
    source: tuple[Disclosure, float, bool] | None = None
    for column, is_next in (("f_div_ann", False), ("nx_f_div_ann", True)):
        found = [(d, v) for d in same if (v := d.get(column)) is not None]
        if found:
            source = (*found[-1], is_next)
            break
    if source is None:
        return None
    price = conn.execute(
        """
        SELECT trade_date, close::float8 AS close FROM daily_price
        WHERE code = %(code)s AND close IS NOT NULL AND (%(d)s::date IS NULL OR trade_date <= %(d)s::date)
        ORDER BY trade_date DESC LIMIT 1
        """,
        {"code": code, "d": latest_pred_date(conn)},
    ).fetchone()
    if price is None or float(price["close"]) <= 0:
        return None
    d, dividend, is_next = source
    return DividendInfo(
        dividend=dividend * split_multiplier(factors, d.disc_date, price["trade_date"]),
        price=float(price["close"]),
        price_date=price["trade_date"],
        disc_date=d.disc_date,
        is_next=is_next,
    )


def build_financials(conn: Conn, code: str) -> FinancialsResponse:
    """業績のブロックの値を作る。開示がなければ空で返す。"""
    discs = load_disclosures(conn, code)
    if not discs:
        return FinancialsResponse(
            code=code, annual=[], quarterly=[], forecast=None, revisions=[], dividend_yield=None, dividend=None
        )
    factors = load_split_factors(conn, code)
    annual, quarterly = build_rows(discs, factors)
    dividend = build_dividend(conn, code, discs, factors)
    return FinancialsResponse(
        code=code,
        annual=annual,
        quarterly=quarterly,
        forecast=build_forecast(discs, factors),
        revisions=build_revisions(discs),
        dividend_yield=None if dividend is None else dividend.dividend / dividend.price,
        dividend=dividend,
    )
