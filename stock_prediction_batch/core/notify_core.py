"""通知の本体。

その日の予測から、買い候補(上位20銘柄)、買い候補(業種分散)、
保有銘柄の判定(保持・乗り換えを検討・売却)、的中率をまとめ、HTML形式のメールで送る。
見た目は、メール見本_毎日の通知.html を元にしている。
予測日数ごとの列は「市場比 → 上昇確率 → 予測株価」の順に並べ、主となる日数(MAIN_HORIZON)の列は、
見出しの背景を薄い緑にし、数字を太字にする。
Gmail では style 要素が消えることがあるので、色や罫線は各要素の style 属性に直接書く。
当処理はDBを変更しない。
"""

import html
import math
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import DictRow

import const
from common import db, mailer
from common.batch_runner import BatchWarnings
from common.errors import BatchError, MailSendError
from common.logger import BatchLogger, get_logger
from common.run_history import ProcessOutcome, run_with_history
from common.stock_code import display_code
from core.features import cross_days_on, trendline_notes_on
from core.market_state_core import (
    MARKET_STATE_NOTE,
    IndexTrend,
    MarketState,
    index_trend_text,
    load_index_trend,
    load_market_state,
    market_state_text,
)
from core.trendline import NOTE_SUPPORT_PREFIX, days_ago_text
from core.verify_core import find_calibration_warnings

LABEL: str = const.PROCESS_LABELS[const.PROCESS_NOTIFY]

_WEEKDAYS: str = "月火水木金土日"

# 見た目
_RED: str = "#c00000"
_BLUE: str = "#0055cc"
# 注意の箱(上昇確率の条件を置き換えた日の注記、上昇確率のずれの注意)の色
_ORANGE: str = "#b35900"
# 判定「保持(注意…)」「乗り換えを検討」と、備考「MM/DD の終値」の色(太字は判定だけ)
COLOR_CAUTION: str = "#f57c00"
# 判定「判定なし」と、備考「流動性注意」の色(太字にしない)
COLOR_QUIET: str = "#2e7d32"
_GRAY_TEXT: str = "#666666"
_TH: str = "border:1px solid #b8c4d6;padding:4px 8px;"
_TD: str = "border:1px solid #cccccc;padding:4px 8px;"
_TD_NUM: str = _TD + "text-align:right;"
# 折り返さないセル(市場。「スタンダード」が2行に分かれないようにする)
_TD_NOWRAP: str = _TD + "white-space:nowrap;"
_STRIPE: str = ' style="background:#f7f7f7;"'
_HEAD_ROW: str = ' style="background:#e8eef7;"'
# 主となる日数(MAIN_HORIZON)の見出しの背景
_MAIN_HEAD_BG: str = "#d9ead3"
_TABLE: str = '<table style="border-collapse:collapse;font-size:13px;">'
_SECTION: str = (
    '<div style="font-size:16px;font-weight:bold;border-left:4px solid #2f5d9e;padding-left:8px;margin:{m};">{t}</div>'
)
_FOOTER_LINES: list[str] = [
    "このメールは株価予測システムが自動で送信しています。売買の判断はご自身で行ってください。",
    "上昇確率は予測であり、値上がりを保証するものではありません。",
    "市場比は、N営業日後までの騰落率が、市場の中央値(対象の全銘柄の騰落率の中央値)を上回る確率です。",
]
_SEGMENT_LABELS: dict[str, str] = {
    const.SEGMENT_ALL: "全銘柄",
    const.SEGMENT_TOP: "買い候補(上位20銘柄)",
    const.SEGMENT_TOP_SECTOR: "買い候補(業種分散)",
    const.SEGMENT_HIGH_PROB: "上昇確率が高い銘柄(70%以上)",
}
_RANKING_LABELS: dict[str, str] = {
    "R": "市場比の確率が高い順(上昇確率50%以上・見込みプラス)",
    "A": "上昇確率が高い順",
    "C": "上昇確率で足切りして予測リターンが高い順",
}
# 主となる日数の値の前に付ける印(テキスト形式の本文)
_MAIN_MARK: str = "★"
# 保有銘柄の判定の文言
JUDGE_NONE: str = "判定なし(本日の予測がありません)"
JUDGE_HOLD: str = "保持"
JUDGE_HOLD_DROP: str = "保持(注意: 上昇予測だが下降しています)"
JUDGE_HOLD_MARKET_DOWN: str = "保持(注意: 相場全体が下がる予測)"
JUDGE_SWITCH: str = "乗り換えを検討(上昇予測だが市場の中央値を下回る予測)"
JUDGE_SELL: str = "売却(下降予測、市場の中央値も下回る予測)"

# 備考の文言
NOTE_LOW_LIQUIDITY: str = "流動性注意"
NOTE_GOLDEN_SHORT: str = "短期ゴールデンクロス発生"
NOTE_DEAD_SHORT: str = "短期デッドクロス発生"
NOTE_GOLDEN_MID: str = "中長期ゴールデンクロス発生"
NOTE_DEAD_MID: str = "中長期デッドクロス発生"
# 割安さ・稼ぐ力の備考。PBR と ROE の文言は、閾値から作る(pbr_note、roe_note)
NOTE_PER_LOW: str = "予想PERが業種平均より低い(割安)"
NOTE_DEFICIT: str = "赤字予想(業績注意)"
# 青で表示する備考(悪い材料)。流動性注意は緑、それ以外は赤(良い材料・目立たせたいもの)
BLUE_NOTES: frozenset[str] = frozenset({NOTE_DEAD_SHORT, NOTE_DEAD_MID, NOTE_DEFICIT})


def is_blue_note(note: str) -> bool:
    """青で表示する備考か。デッドクロス・赤字予想(BLUE_NOTES)と、下値支持線の下抜け。"""
    # デッドクロスは「中長期デッドクロス発生(本日)」のように起きた日が付くので、先頭で判定する
    return (
        note in BLUE_NOTES or note.startswith(NOTE_SUPPORT_PREFIX) or note.startswith((NOTE_DEAD_SHORT, NOTE_DEAD_MID))
    )


_STALE_CLOSE_RE = re.compile(r"^\d{2}/\d{2} の終値$")


def stale_close_note(close_date: date) -> str:
    """現在値がその日の終値でないときの備考(例: 11/05 の終値)。"""
    return f"{close_date:%m/%d} の終値"


def is_stale_close_note(note: str) -> bool:
    """「MM/DD の終値」の備考か。注意の色(COLOR_CAUTION)で表示する。"""
    return _STALE_CLOSE_RE.match(note) is not None


# 備考が複数ある場合の区切り文字(全角のスラッシュ)
NOTE_SEPARATOR: str = "／"


def pbr_note() -> str:
    """PBR の備考の文言(例: PBR0.5倍割れ(割安))。"""
    return f"PBR{const.NOTE_PBR_MAX:g}倍割れ(割安)"


def roe_note() -> str:
    """ROE の備考の文言(例: ROE8%以上(稼ぐ力が高い))。"""
    return f"ROE{const.NOTE_ROE_MIN_PERCENT}%以上(稼ぐ力が高い)"


def _num(value: float | None) -> float | None:
    """値がない(None、NaN)なら None。"""
    return None if value is None or math.isnan(value) else value


def valuation_notes(
    pbr: float | None, fwd_per: float | None, fwd_eps: float | None, roe: float | None, sector_per: float | None
) -> list[str]:
    """割安さ・稼ぐ力の備考を返す。値がない項目の備考は出さない。

    Args:
        pbr: PBR(倍)
        fwd_per: 予想PER(倍)
        fwd_eps: 予想EPS(円)
        roe: 実績ROE(小数。0.08 = 8%)
        sector_per: 同じ33業種の予想PER の中央値(予想PER がプラスの銘柄だけで計算)
    """
    pbr, fwd_per, fwd_eps, roe, sector_per = (_num(v) for v in (pbr, fwd_per, fwd_eps, roe, sector_per))
    notes: list[str] = []
    if const.NOTE_PBR_MAX > 0 and pbr is not None and 0 < pbr < const.NOTE_PBR_MAX:
        notes.append(pbr_note())
    deficit = fwd_eps is not None and fwd_eps < 0
    if (
        const.NOTE_PER_SECTOR_RATIO > 0
        and not deficit
        and fwd_per is not None
        and sector_per is not None
        and 0 < fwd_per <= sector_per * const.NOTE_PER_SECTOR_RATIO
    ):
        notes.append(NOTE_PER_LOW)
    if const.NOTE_ROE_MIN_PERCENT > 0 and roe is not None and roe * 100 >= const.NOTE_ROE_MIN_PERCENT:
        notes.append(roe_note())
    if deficit:
        notes.append(NOTE_DEFICIT)
    return notes


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def valuation_notes_on(conn: psycopg.Connection[DictRow], target_date: date, codes: list[str]) -> dict[str, list[str]]:
    """指定日の、銘柄ごとの割安さ・稼ぐ力の備考。daily_valuation の行がない銘柄は含まない。

    業種の予想PER の中央値は、指定日の対象の市場(TARGET_MARKET_CODES)の、予想PER がプラスの銘柄で計算する。
    """
    if not codes:
        return {}
    rows = conn.execute(
        """
        WITH sector_per AS (
            SELECT m.sector33_code, percentile_cont(0.5) WITHIN GROUP (ORDER BY v.fwd_per) AS median_per
            FROM daily_valuation v
            JOIN stock_master m ON m.code = v.code
            WHERE v.trade_date = %s AND m.market_code = ANY(%s) AND v.fwd_per > 0
            GROUP BY m.sector33_code
        )
        SELECT v.code, v.pbr, v.fwd_per, v.fwd_eps, v.roe, s.median_per
        FROM daily_valuation v
        LEFT JOIN stock_master m ON m.code = v.code
        LEFT JOIN sector_per s ON s.sector33_code = m.sector33_code
        WHERE v.trade_date = %s AND v.code = ANY(%s)
        """,
        (target_date, const.TARGET_MARKET_CODES, target_date, codes),
    ).fetchall()
    return {
        str(r["code"]): valuation_notes(
            _float(r["pbr"]), _float(r["fwd_per"]), _float(r["fwd_eps"]), _float(r["roe"]), _float(r["median_per"])
        )
        for r in rows
    }


def stop_loss_note() -> str:
    """損切りラインの備考の文言。"""
    return f"損切りライン{const.STOP_LOSS_PERCENT}%の値を下回っています。"


def hits_stop_loss(buy_price: Decimal, current: Decimal | None) -> bool:
    """現在値が、買値から STOP_LOSS_PERCENT %以上下がっていれば True(ちょうどの場合も含む)。

    STOP_LOSS_PERCENT が 0 以下の場合と、現在値がない場合(売買が成立しなかった日など)は False。
    """
    if const.STOP_LOSS_PERCENT <= 0 or current is None or buy_price <= 0:
        return False
    return (current - buy_price) / buy_price <= -Decimal(const.STOP_LOSS_PERCENT) / 100


# 予測日数ごとの値: (市場比の確率, 上昇確率, 予測株価)
HorizonValues = tuple[Decimal, Decimal, Decimal]


@dataclass
class Candidate:
    """買い候補の1銘柄。"""

    rank: int
    code: str
    name: str
    base_price: Decimal
    by_horizon: dict[int, HorizonValues] = field(default_factory=dict)  # 予測日数 → (市場比, 上昇確率, 予測株価)
    notes: list[str] = field(default_factory=list)
    market: str = ""  # 市場(銘柄マスタの市場区分名。例: プライム)
    sector: str = ""  # 業種(予測した時点の33業種コードの名前。例: 銀行業)


@dataclass
class HoldingLot:
    """保有銘柄の購入ごとの行(holding の1行)。"""

    buy_price: Decimal
    quantity: int
    buy_date: date


@dataclass
class Holding:
    """保有銘柄の1行。同じ銘柄を複数回買った場合(買い増し)は、銘柄ごとに1行にまとめる。"""

    code: str
    name: str
    buy_price: Decimal  # 買値。複数回買った場合は平均買値(買値 × 株数の合計 ÷ 合計の株数)
    current: Decimal | None
    by_horizon: dict[int, HorizonValues] = field(default_factory=dict)
    judgment: str = ""
    notes: list[str] = field(default_factory=list)
    market: str = ""  # 市場(銘柄マスタの市場区分名)
    quantity: int = 0  # 株数(まとめた行の合計)
    current_date: date | None = None  # 現在値(終値)の日。判定の日以前で一番新しい終値の日
    lots: list[HoldingLot] = field(default_factory=list)  # 購入ごとの行(購入日の古い順)


@dataclass
class NotifyData:
    """メールに載せる内容。"""

    target_date: date
    model_version: str
    code_count: int
    candidates: list[Candidate]
    holdings: list[Holding]
    accuracy: list[dict[str, Any]]
    calibration_warnings: list[dict[str, Any]]
    # 買い候補(業種分散)。順位は prediction.buy_rank_sector
    candidates_sector: list[Candidate] = field(default_factory=list)
    # 相場の状態(強い・普通・弱い)。計算できない日は None
    market_state: MarketState | None = None
    # 日経平均の向き(10・20・60日)。出さない日は None
    index_trend: IndexTrend | None = None


# =====================================================================
# 書式
# =====================================================================


def _round(value: Decimal | float, places: str = "1") -> Decimal:
    return Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def fmt_price(value: Decimal | float | None) -> str:
    """株価は3けたごとにカンマを入れ、整数で表示する(四捨五入)。"""
    return "-" if value is None else f"{_round(value):,}"


def fmt_prob(value: Decimal | float | None) -> str:
    """上昇確率は % の整数で表示する(四捨五入)。"""
    return "-" if value is None else f"{_round(Decimal(str(value)) * 100)}%"


def fmt_rate(value: Decimal | float | None, sign: bool = False) -> str:
    """割合は % の小数1桁で表示する。sign が True なら符号を付ける。"""
    if value is None:
        return "-"
    pct = _round(Decimal(str(value)) * 100, "0.1")
    return f"{pct:+}%" if sign else f"{pct}%"


def fmt_date(d: date) -> str:
    return f"{d.isoformat()}({_WEEKDAYS[d.weekday()]})"


def _e(text: object) -> str:
    return html.escape(str(text))


# =====================================================================
# データの読み込み
# =====================================================================


def cross_notes(days_5_25: float | None, days_25_75: float | None) -> list[str]:
    """クロスからの日数(features.cross_days の値)から、備考に出す文言を返す。

    直近 CROSS_NOTE_DAYS 営業日以内(1〜CROSS_NOTE_DAYS)にクロスした場合だけ出す。プラスはゴールデンクロス、
    マイナスはデッドクロス。値 1(-1)はクロスした日(指定日)なので「本日」、2 は「1営業日前」、N は「N-1営業日前」。
    例: 「中長期デッドクロス発生(本日)」「短期ゴールデンクロス発生(3営業日前)」
    """
    notes: list[str] = []
    for days, golden, dead in (
        (days_5_25, NOTE_GOLDEN_SHORT, NOTE_DEAD_SHORT),
        (days_25_75, NOTE_GOLDEN_MID, NOTE_DEAD_MID),
    ):
        if days is None or math.isnan(days) or not 1 <= abs(days) <= const.CROSS_NOTE_DAYS:
            continue
        notes.append(f"{golden if days > 0 else dead}({days_ago_text(int(abs(days)) - 1)})")
    return notes


def add_cross_notes(
    conn: psycopg.Connection[DictRow], target_date: date, candidates: list[Candidate], holdings: list[Holding]
) -> None:
    """買い候補と保有銘柄の備考に、ゴールデンクロス・デッドクロスを足す。"""
    codes = sorted({c.code for c in candidates} | {h.code for h in holdings})
    if not codes:
        return
    frame = cross_days_on(conn, target_date, codes, const.CROSS_DAYS_CAP)
    by_code: dict[str, list[str]] = {
        str(code): cross_notes(short, mid)
        for code, short, mid in zip(
            frame["code"].tolist(),
            frame["days_since_cross_5_25"].astype(float).tolist(),
            frame["days_since_cross_25_75"].astype(float).tolist(),
            strict=True,
        )
    }
    for c in candidates:
        c.notes.extend(by_code.get(c.code, []))
    for h in holdings:
        h.notes.extend(by_code.get(h.code, []))


def add_trendline_notes(
    conn: psycopg.Connection[DictRow], target_date: date, candidates: list[Candidate], holdings: list[Holding]
) -> None:
    """買い候補と保有銘柄の備考に、トレンドラインの抜けを足す。"""
    codes = sorted({c.code for c in candidates} | {h.code for h in holdings})
    by_code = trendline_notes_on(conn, target_date, codes)
    for c in candidates:
        c.notes.extend(by_code.get(c.code, []))
    for h in holdings:
        h.notes.extend(by_code.get(h.code, []))


def add_valuation_notes(
    conn: psycopg.Connection[DictRow], target_date: date, candidates: list[Candidate], holdings: list[Holding]
) -> None:
    """買い候補と保有銘柄の備考に、割安さ・稼ぐ力(PBR、予想PER、ROE、赤字予想)を足す。"""
    codes = sorted({c.code for c in candidates} | {h.code for h in holdings})
    by_code = valuation_notes_on(conn, target_date, codes)
    for c in candidates:
        c.notes.extend(by_code.get(c.code, []))
    for h in holdings:
        h.notes.extend(by_code.get(h.code, []))


def average_buy_price(cost: Decimal, quantity: int) -> Decimal:
    """平均買値 = 買値 × 株数の合計 ÷ 合計の株数。株数が0以下なら0を返す。"""
    if quantity <= 0:
        return Decimal(0)
    return cost / quantity


@dataclass
class HoldingTotals:
    """保有中の銘柄の合計(画面の合計のカードとメールの表の上の合計)。"""

    cost: Decimal  # 買値の合計(全ての銘柄の 買値 × 株数)
    value: Decimal  # 今の評価額(現在値がある銘柄だけの 現在値 × 株数)
    pnl_amount: Decimal  # 損益(円)= 評価額 − 現在値がある銘柄の買値の合計
    pnl_rate: Decimal | None  # 損益(%)。現在値がある銘柄の買値の合計が0なら None


def holding_totals(items: list[tuple[Decimal, Decimal | None, int]]) -> HoldingTotals:
    """保有中の銘柄の合計を求める。現在値がない銘柄は、評価額と損益に入れない。

    Args:
        items: 銘柄ごとの (買値 × 株数の合計, 現在値, 株数の合計)
    """
    cost = value = priced_cost = Decimal(0)
    for item_cost, current, quantity in items:
        cost += item_cost
        if current is not None:
            value += current * quantity
            priced_cost += item_cost
    pnl = value - priced_cost
    return HoldingTotals(cost, value, pnl, pnl / priced_cost if priced_cost > 0 else None)


def judge_holding(
    rel_prob: Decimal | None, up_prob: Decimal | None, buy_price: Decimal, current: Decimal | None
) -> str:
    """保有銘柄を、主となる日数の市場比の確率と上昇確率で判定する。"""
    if rel_prob is None or up_prob is None:
        return JUDGE_NONE
    beat = rel_prob >= Decimal(str(const.REL_PROB_THRESHOLD))
    up = up_prob >= Decimal(str(const.UP_PROB_THRESHOLD))
    if not beat:
        return JUDGE_SWITCH if up else JUDGE_SELL
    if not up:
        return JUDGE_HOLD_MARKET_DOWN
    if current is not None and buy_price > 0:
        change = (current - buy_price) / buy_price
        if change <= -Decimal(str(const.HOLD_WARNING_DROP_RATE)):
            return JUDGE_HOLD_DROP
    return JUDGE_HOLD


def _predictions_by_code(conn: psycopg.Connection[DictRow], target_date: date) -> dict[str, dict[int, Any]]:
    rows = conn.execute(
        "SELECT code, horizon, up_prob, rel_prob, pred_price, base_price, buy_rank, buy_rank_sector, sector33_code,"
        " model_version FROM prediction WHERE pred_date = %s",
        (target_date,),
    ).fetchall()
    result: dict[str, dict[int, Any]] = {}
    for r in rows:
        result.setdefault(str(r["code"]), {})[int(r["horizon"])] = r
    return result


def _horizon_values(row: Any) -> HorizonValues:
    """prediction の1行から、(市場比の確率, 上昇確率, 予測株価)を取り出す。"""
    return (Decimal(row["rel_prob"]), Decimal(row["up_prob"]), Decimal(row["pred_price"]))


def load_notify_data(conn: psycopg.Connection[DictRow], target_date: date) -> NotifyData:
    """メールに載せる内容を読む。"""
    preds = _predictions_by_code(conn, target_date)
    if not preds:
        raise BatchError(LABEL, f"指定日の予測がありません(指定日: {target_date.isoformat()})")
    master = conn.execute("SELECT code, name, market_name, sector33_code, sector33_name FROM stock_master").fetchall()
    names = {str(r["code"]): str(r["name"]) for r in master}
    markets = {str(r["code"]): str(r["market_name"] or "") for r in master}
    sector_names = {str(r["sector33_code"]): str(r["sector33_name"] or "") for r in master if r["sector33_code"]}
    first = next(iter(next(iter(preds.values())).values()))
    model_version = str(first["model_version"])

    # 手順2: 買い候補と、買い候補(業種分散)。業種は予測した時点の33業種コードの名前
    def build(rank_column: str) -> list[Candidate]:
        result: list[Candidate] = []
        for code, by_h in preds.items():
            any_row = next(iter(by_h.values()))
            if any_row[rank_column] is None:
                continue
            c = Candidate(int(any_row[rank_column]), code, names.get(code, ""), Decimal(any_row["base_price"]))
            c.market = markets.get(code, "")
            c.sector = sector_names.get(str(any_row["sector33_code"]), "") if any_row["sector33_code"] else ""
            for h, r in by_h.items():
                c.by_horizon[h] = _horizon_values(r)
            result.append(c)
        return sorted(result, key=lambda c: c.rank)

    candidates = build("buy_rank")
    candidates_sector = build("buy_rank_sector")
    # 備考は2つの買い候補の両方に付ける(同じ銘柄は同じ備考)
    listed = candidates + candidates_sector

    # 手順3: 流動性とストップ高・ストップ安
    if listed:
        codes = sorted({c.code for c in listed})
        liquidity = conn.execute(
            """
            SELECT code, avg(COALESCE(turnover, 0)) AS avg_turnover
            FROM (
                SELECT code, turnover, row_number() OVER (PARTITION BY code ORDER BY trade_date DESC) AS n
                FROM daily_price WHERE code = ANY(%s) AND trade_date <= %s
            ) t
            WHERE n <= %s GROUP BY code
            """,
            (codes, target_date, const.LIQUIDITY_AVG_DAYS),
        ).fetchall()
        low = {str(r["code"]) for r in liquidity if r["avg_turnover"] < const.LIQUIDITY_WARN_TURNOVER}
        flags = {
            str(r["code"]): r
            for r in conn.execute(
                "SELECT code, upper_limit_flag, lower_limit_flag FROM daily_price"
                " WHERE trade_date = %s AND code = ANY(%s)",
                (target_date, codes),
            ).fetchall()
        }
        for c in listed:
            f = flags.get(c.code)
            if f is not None and f["upper_limit_flag"]:
                c.notes.append("ストップ高")
            if f is not None and f["lower_limit_flag"]:
                c.notes.append("ストップ安")
            if c.code in low:
                c.notes.append(NOTE_LOW_LIQUIDITY)

    # 手順4、手順5: 保有銘柄。同じ銘柄の行(買い増し)は銘柄ごとに1行にまとめ、平均買値で判定する
    holdings: list[Holding] = []
    rows = conn.execute(
        """
        SELECT h.holding_id, h.code, h.buy_price, h.quantity, h.buy_date, p.close, p.trade_date
        FROM holding h
        LEFT JOIN LATERAL (
            SELECT close, trade_date FROM daily_price
            WHERE code = h.code AND trade_date <= %s AND close IS NOT NULL
            ORDER BY trade_date DESC LIMIT 1
        ) p ON true
        WHERE h.buy_date <= %s AND (h.sell_date IS NULL OR h.sell_date > %s)
        ORDER BY h.buy_date, h.holding_id
        """,
        (target_date, target_date, target_date),
    ).fetchall()
    # 銘柄ごとに、買値 × 株数の合計と株数の合計を集める(並びは最初に買った行の順)
    lots: dict[str, tuple[Decimal, int, Decimal | None, date | None]] = {}
    lot_rows: dict[str, list[HoldingLot]] = {}
    for r in rows:
        code = str(r["code"])
        quantity = int(r["quantity"])
        current = None if r["close"] is None else Decimal(r["close"])
        cost, total, _, _ = lots.get(code, (Decimal(0), 0, current, r["trade_date"]))
        lots[code] = (cost + Decimal(r["buy_price"]) * quantity, total + quantity, current, r["trade_date"])
        lot_rows.setdefault(code, []).append(HoldingLot(Decimal(r["buy_price"]), quantity, r["buy_date"]))
    for code, (cost, total, current, current_date) in lots.items():
        buy_price = average_buy_price(cost, total)
        hd = Holding(
            code, names.get(code, ""), buy_price, current, market=markets.get(code, ""), quantity=total,
            current_date=current_date, lots=lot_rows[code],
        )  # fmt: skip
        for h, p in preds.get(code, {}).items():
            hd.by_horizon[h] = _horizon_values(p)
        main = hd.by_horizon.get(const.MAIN_HORIZON)
        hd.judgment = judge_holding(
            None if main is None else main[0], None if main is None else main[1], hd.buy_price, current
        )
        # 現在値がその日の終値でないとき(取引がない日の銘柄など)は、何日の終値かを備考の最初に出す
        if current_date is not None and current_date != target_date:
            hd.notes.append(stale_close_note(current_date))
        # 損切りライン(判定は変えず、備考に出す)
        if hits_stop_loss(hd.buy_price, current):
            hd.notes.append(stop_loss_note())
        holdings.append(hd)

    # 手順3、手順5: ゴールデンクロス・デッドクロス(買い候補と保有銘柄の備考)
    add_cross_notes(conn, target_date, listed, holdings)
    # 手順3、手順5: トレンドラインの抜け(買い候補と保有銘柄の備考)
    add_trendline_notes(conn, target_date, listed, holdings)
    # 手順3、手順5: 割安さ・稼ぐ力(買い候補と保有銘柄の備考)
    add_valuation_notes(conn, target_date, listed, holdings)

    # 手順6: 的中率と、上昇確率のずれ
    accuracy = [
        dict(r)
        for r in conn.execute(
            "SELECT period_type, horizon, segment, verified_count, hit_rate, up_rate, beat_rate, avg_excess_rate,"
            " sector_beat_rate, avg_sector_excess_rate FROM verify_period_summary WHERE summary_date = %s",
            (target_date,),
        ).fetchall()
    ]
    warnings = find_calibration_warnings(conn, target_date)
    return NotifyData(
        target_date, model_version, len(preds), candidates, holdings, accuracy, warnings,
        candidates_sector=candidates_sector, market_state=load_market_state(conn, target_date),
        index_trend=load_index_trend(conn, target_date),
    )  # fmt: skip


# =====================================================================
# メールの本文
# =====================================================================


# 予測日数ごとに並べる列の名前(この順)
_HORIZON_COLUMNS: list[str] = ["市場比", "上昇確率", "予測株価"]


def _horizon_headers() -> str:
    """予測日数ごとの見出し。主となる日数の見出しは、背景を薄い緑にする。"""
    cells = []
    for h in const.PREDICT_HORIZONS:
        bg = f"background:{_MAIN_HEAD_BG};" if h == const.MAIN_HORIZON else ""
        for name in _HORIZON_COLUMNS:
            cells.append(f'<th style="{_TH}{bg}">{h}日後<br>{name}</th>')
    return "".join(cells)


def _horizon_cells(by_horizon: dict[int, HorizonValues]) -> str:
    """予測日数ごとの値。主となる日数の値は太字にする。"""
    cells = []
    for h in const.PREDICT_HORIZONS:
        values = by_horizon.get(h)
        rel, prob, price = (None, None, None) if values is None else values
        bold = "font-weight:bold;" if h == const.MAIN_HORIZON else ""
        cells.append(f'<td style="{_TD_NUM}{bold}">{fmt_prob(rel)}</td>')
        cells.append(f'<td style="{_TD_NUM}{bold}">{fmt_prob(prob)}</td>')
        cells.append(f'<td style="{_TD_NUM}{bold}">{fmt_price(price)}</td>')
    return "".join(cells)


def _market_text(market: str) -> str:
    """テキストの本文の市場。銘柄名の後ろに「(プライム)」のように付ける。市場が分からなければ何も付けない。"""
    return f"({market})" if market else ""


def candidate_column_count() -> int:
    """買い候補の表の列の数(順位・コード・銘柄名・市場・業種・現在値 + 予測日数ごとの列 + 備考)。"""
    return 6 + len(const.PREDICT_HORIZONS) * len(_HORIZON_COLUMNS) + 1


def candidate_title() -> str:
    """買い候補の見出し。"""
    return f"買い候補(上位{const.BUY_CANDIDATE_COUNT}銘柄)"


def candidate_sector_title() -> str:
    """買い候補(業種分散)の見出し。"""
    return (
        f"買い候補(業種分散。同じ33業種は{const.SECTOR_CAP_PER_SECTOR}銘柄まで、"
        f"上位{const.BUY_CANDIDATE_SECTOR_COUNT}銘柄)"
    )


def candidate_fallback_note(candidates: list[Candidate]) -> str | None:
    """上昇確率の条件を置き換えて選んだ日の注記。置き換えていなければ None。

    置き換えたことは DB に持たない。買い候補の中に上昇確率が UP_PROB_THRESHOLD 未満の銘柄があれば、置き換えた日とする。
    """
    threshold = Decimal(str(const.UP_PROB_THRESHOLD))
    main = const.MAIN_HORIZON
    if not any(c.by_horizon[main][1] < threshold for c in candidates if main in c.by_horizon):
        return None
    return (
        f"本日は上昇確率{const.UP_PROB_THRESHOLD * 100:g}%以上の銘柄が足りないため、"
        f"上昇確率の上位{const.BUY_CANDIDATE_FALLBACK_TOP_RATE * 100:g}%で選びました"
    )


def _fallback_note_html(candidates: list[Candidate]) -> list[str]:
    """注記の行(HTML)。置き換えていない日は何も出さない。"""
    note = candidate_fallback_note(candidates)
    if note is None:
        return []
    return [f'<div style="font-size:12px;font-weight:bold;color:{_ORANGE};margin-bottom:6px;">{_e(note)}</div>']


def index_block_title(target_date: date) -> str:
    """「日経平均」のブロックの見出し(対象日つき。画面のブロックとそろえる)。"""
    return f"日経平均({target_date.isoformat()})"


def market_state_lines(market_state: MarketState | None, index_trend: IndexTrend | None) -> list[str]:
    """相場の状態の行、日経平均の向きの行(出さない日はなし)、注意書き(テキスト)。"""
    trend = index_trend_text(index_trend)
    return [market_state_text(market_state), *([trend] if trend is not None else []), MARKET_STATE_NOTE]


def _market_state_html(market_state: MarketState | None, index_trend: IndexTrend | None = None) -> list[str]:
    """相場の状態の行・日経平均の向きの行・注意書き(HTML)。色は付けない(良い・悪いとは書かない)。"""
    *lines, note = market_state_lines(market_state, index_trend)
    return [f'<div style="font-size:13px;margin-bottom:2px;">{_e(line)}</div>' for line in lines] + [
        f'<div style="font-size:12px;color:{_GRAY_TEXT};margin-bottom:6px;">{_e(note)}</div>'
    ]


def _note_html(notes: list[str]) -> str:
    """備考の色。流動性注意は緑(COLOR_QUIET)、「MM/DD の終値」は橙(COLOR_CAUTION)、
    デッドクロス・赤字予想・下値支持線の下抜けは青(is_blue_note)、
    それ以外(ストップ高・ストップ安・ゴールデンクロス・上値抵抗線の上抜け・損切りライン・割安・稼ぐ力)は赤。"""
    parts = []
    for n in notes:
        if n == NOTE_LOW_LIQUIDITY:
            color = COLOR_QUIET
        elif is_stale_close_note(n):
            color = COLOR_CAUTION
        else:
            color = _BLUE if is_blue_note(n) else _RED
        parts.append(f'<span style="color:{color};">{_e(n)}</span>')
    return NOTE_SEPARATOR.join(parts)


def _judgment_style(judgment: str) -> str:
    if judgment.startswith("売却"):
        return f"color:{_RED};font-weight:bold;"
    if judgment.startswith("保持(注意") or judgment.startswith("乗り換え"):
        return f"color:{COLOR_CAUTION};font-weight:bold;"
    if judgment == JUDGE_NONE:
        return f"color:{COLOR_QUIET};"
    return ""


def _accuracy_cell(row: dict[str, Any] | None) -> str:
    if row is None or row["hit_rate"] is None:
        return "-"
    return f"{fmt_rate(row['hit_rate'])}({int(row['verified_count']):,}件)"


# 的中率の表で、2つの買い候補の行の下に出す行: (表示の名前, verify_period_summary の列, 符号を付けるか)
# 全銘柄・上昇確率が高い銘柄の業種比は、メールには出さない(画面で見る)
_CANDIDATE_EXTRA_ROWS: list[tuple[str, str, bool]] = [
    ("市場の中央値を上回った割合", "beat_rate", False),
    ("市場の中央値との差(平均)", "avg_excess_rate", True),
    ("業種の中央値を上回った割合", "sector_beat_rate", False),
    ("業種の中央値との差(平均)", "avg_sector_excess_rate", True),
]
# 下の行の名前の頭に付ける区分の名前
_EXTRA_PREFIXES: dict[str, str] = {const.SEGMENT_TOP: "買い候補", const.SEGMENT_TOP_SECTOR: "買い候補(業種分散)"}
# 的中率の表に出す対象区分(この順)
_ACCURACY_SEGMENTS: list[str] = [
    const.SEGMENT_ALL, const.SEGMENT_TOP, const.SEGMENT_TOP_SECTOR, const.SEGMENT_HIGH_PROB,
]  # fmt: skip


def _rate_cell(row: dict[str, Any] | None, column: str, sign: bool) -> str:
    if row is None or row.get(column) is None:
        return "-"
    return fmt_rate(row[column], sign=sign)


def _candidate_table(candidates: list[Candidate]) -> list[str]:
    """買い候補の表(HTML)。0銘柄の日も表の見出しは出し、1行目を全ての列で連結して「買い候補はありません」と表示する。"""
    out = [_TABLE]
    out.append(
        f"<tr{_HEAD_ROW}>"
        + "".join(f'<th style="{_TH}">{h}</th>' for h in ("順位", "コード", "銘柄名", "市場", "業種", "現在値"))
        + _horizon_headers()
        + f'<th style="{_TH}">備考</th></tr>'
    )
    if not candidates:
        colspan = candidate_column_count()
        out.append(f'<tr><td colspan="{colspan}" style="{_TD}text-align:center;">買い候補はありません</td></tr>')
    for i, c in enumerate(candidates):
        out.append(
            f"<tr{_STRIPE if i % 2 else ''}>"
            f'<td style="{_TD_NUM}">{c.rank}</td><td style="{_TD}">{_e(display_code(c.code))}</td>'
            f'<td style="{_TD}">{_e(c.name)}</td><td style="{_TD_NOWRAP}">{_e(c.market)}</td>'
            f'<td style="{_TD_NOWRAP}">{_e(c.sector)}</td>'
            f'<td style="{_TD_NUM}">{fmt_price(c.base_price)}</td>'
            + _horizon_cells(c.by_horizon)
            + f'<td style="{_TD}">{_note_html(c.notes)}</td></tr>'
        )
    out.append("</table>")
    return out


def _holding_totals(holdings: list[Holding]) -> HoldingTotals:
    return holding_totals([(hd.buy_price * hd.quantity, hd.current, hd.quantity) for hd in holdings])


def _pnl_cells(buy_price: Decimal, current: Decimal | None, quantity: int) -> tuple[str, str, str]:
    """損益(%)と損益(円)の文字と、色の style(現在値がなければ「-」)。"""
    if current is None or buy_price == 0:
        return "-", "-", ""
    change = (current - buy_price) / buy_price
    style = f"color:{_RED};" if change > 0 else (f"color:{_BLUE};" if change < 0 else "")
    return fmt_rate(change, sign=True), f"{_round((current - buy_price) * quantity, '1'):+,.0f}", style


def _amount(price: Decimal | None, quantity: int) -> str:
    """金額(価格 × 株数)。3けたごとにカンマを入れた整数。価格がなければ「-」。"""
    return "-" if price is None else fmt_price(price * quantity)


def _purchase_date_text(hd: Holding) -> str:
    """購入日の列。買い増しの銘柄は「N回に分けて購入」(画面と同じ)。"""
    if len(hd.lots) > 1:
        return f"{len(hd.lots)}回に分けて購入"
    return hd.lots[0].buy_date.isoformat() if hd.lots else "-"


def holding_totals_text(holdings: list[Holding]) -> str:
    """表の上に出す合計(買値の合計、今の評価額、損益)。"""
    t = _holding_totals(holdings)
    return (
        f"買値の合計 {fmt_price(t.cost)}円 / 今の評価額 {fmt_price(t.value)}円 / "
        f"損益 {_round(t.pnl_amount):+,.0f}円({fmt_rate(t.pnl_rate, sign=True)})"
    )


def _holdings_html(holdings: list[Holding]) -> list[str]:
    """保有銘柄の合計と表(HTML)。買い増しの銘柄は、合計の行の下に購入ごとの行をいつも出す。"""
    out = [f'<div style="font-size:13px;margin-bottom:6px;">{_e(holding_totals_text(holdings))}</div>', _TABLE]
    heads = (
        "コード", "銘柄名", "市場", "買値", "買い株数", "買いの金額", "購入日", "現在値", "現在の金額",
        "損益(%)", "損益(円)",
    )  # fmt: skip
    out.append(
        f"<tr{_HEAD_ROW}>"
        + "".join(f'<th style="{_TH}">{h}</th>' for h in heads)
        + _horizon_headers()
        + f'<th style="{_TH}">判定</th><th style="{_TH}">備考</th></tr>'
    )
    blank_horizons = f'<td style="{_TD}"></td>' * (len(const.PREDICT_HORIZONS) * len(_HORIZON_COLUMNS))
    for i, hd in enumerate(holdings):
        rate, amount, pnl_style = _pnl_cells(hd.buy_price, hd.current, hd.quantity)
        stripe = _STRIPE if i % 2 else ""
        out.append(
            f"<tr{stripe}>"
            f'<td style="{_TD}">{_e(display_code(hd.code))}</td><td style="{_TD}">{_e(hd.name)}</td>'
            f'<td style="{_TD_NOWRAP}">{_e(hd.market)}</td>'
            f'<td style="{_TD_NUM}">{fmt_price(hd.buy_price)}</td>'
            f'<td style="{_TD_NUM}">{hd.quantity:,}</td>'
            f'<td style="{_TD_NUM}">{_amount(hd.buy_price, hd.quantity)}</td>'
            f'<td style="{_TD}">{_e(_purchase_date_text(hd))}</td>'
            f'<td style="{_TD_NUM}">{fmt_price(hd.current)}</td>'
            f'<td style="{_TD_NUM}">{_amount(hd.current, hd.quantity)}</td>'
            f'<td style="{_TD_NUM}{pnl_style}">{rate}</td>'
            f'<td style="{_TD_NUM}{pnl_style}">{amount}</td>'
            + _horizon_cells(hd.by_horizon)
            + f'<td style="{_TD}{_judgment_style(hd.judgment)}">{_e(hd.judgment)}</td>'
            + f'<td style="{_TD}">{_note_html(hd.notes)}</td></tr>'
        )
        if len(hd.lots) <= 1:
            continue
        for lot in hd.lots:
            lrate, lamount, lstyle = _pnl_cells(lot.buy_price, hd.current, lot.quantity)
            out.append(
                f"<tr{stripe}>"
                f'<td style="{_TD}"></td><td style="{_TD}">└</td><td style="{_TD}"></td>'
                f'<td style="{_TD_NUM}">{fmt_price(lot.buy_price)}</td>'
                f'<td style="{_TD_NUM}">{lot.quantity:,}</td>'
                f'<td style="{_TD_NUM}">{_amount(lot.buy_price, lot.quantity)}</td>'
                f'<td style="{_TD}">{lot.buy_date.isoformat()}</td>'
                f'<td style="{_TD}"></td>'
                f'<td style="{_TD_NUM}">{_amount(hd.current, lot.quantity)}</td>'
                f'<td style="{_TD_NUM}{lstyle}">{lrate}</td>'
                f'<td style="{_TD_NUM}{lstyle}">{lamount}</td>'
                + blank_horizons
                + f'<td style="{_TD}"></td><td style="{_TD}"></td></tr>'
            )
    out.append("</table>")
    return out


def build_html(data: NotifyData, subject: str, is_backfill: bool) -> str:
    """メールの本文(HTML)を作る。"""
    out: list[str] = [
        '<!doctype html><html lang="ja"><head><meta charset="utf-8">',
        f"<title>{_e(subject)}</title></head>",
        "<body style=\"margin:0;padding:16px;background:#ffffff;font-family:'Meiryo','Yu Gothic','Hiragino Sans',"
        "'Noto Sans CJK JP',sans-serif;font-size:14px;color:#222222;\">",
    ]
    if is_backfill:
        out.append(
            f'<div style="margin-bottom:12px;color:{_ORANGE};font-weight:bold;">'
            f"この通知は、取り逃した日の分を後から作成したものです。買い候補は {data.target_date.isoformat()} "
            "時点の株価に基づきます。</div>"
        )
    out.append('<table style="border-collapse:collapse;margin-bottom:16px;font-size:13px;">')
    for label, value in (
        ("対象日", fmt_date(data.target_date)),
        ("予測に使ったモデル", data.model_version),
        ("予測した銘柄数", f"{data.code_count:,}"),
    ):
        out.append(
            f'<tr><td style="padding:2px 12px 2px 0;color:{_GRAY_TEXT};">{label}</td>'
            f'<td style="padding:2px 0;">{_e(value)}</td></tr>'
        )
    out.append("</table>")

    # 日経平均(相場の状態・日経平均の向き・注意書き。画面の「日経平均」のブロックとそろえる)
    out.append(_SECTION.format(m="20px 0 4px", t=_e(index_block_title(data.target_date))))
    out += _market_state_html(data.market_state, data.index_trend)

    # 買い候補
    out.append(_SECTION.format(m="20px 0 4px", t=_e(candidate_title())))
    out += _fallback_note_html(data.candidates)
    method = _RANKING_LABELS.get(const.RANKING_METHOD, const.RANKING_METHOD)
    out.append(
        f'<div style="font-size:12px;color:{_GRAY_TEXT};margin-bottom:6px;">'
        f"主となる日数: {const.MAIN_HORIZON}営業日後 / 順位付け: {_e(method)}</div>"
    )
    out += _candidate_table(data.candidates)

    # 買い候補(業種分散)
    out.append(_SECTION.format(m="24px 0 4px", t=_e(candidate_sector_title())))
    out += _fallback_note_html(data.candidates_sector)
    out.append(
        f'<div style="font-size:12px;color:{_GRAY_TEXT};margin-bottom:6px;">'
        "並べ方は買い候補と同じ。同じ33業種の銘柄は上から"
        f"{const.SECTOR_CAP_PER_SECTOR}銘柄までにする(業種は予測した時点の33業種)</div>"
    )
    out += _candidate_table(data.candidates_sector)

    # 保有銘柄(画面の保有中の表と同じ列。「操作」は除く)
    out.append(_SECTION.format(m="24px 0 6px", t="保有銘柄"))
    if data.holdings:
        out += _holdings_html(data.holdings)
    else:
        out.append('<div style="font-size:13px;">保有銘柄はありません</div>')

    # 的中率
    out.append(_SECTION.format(m="24px 0 6px", t="的中率(検証結果)"))
    if data.accuracy:
        index = {(r["horizon"], r["segment"], r["period_type"]): r for r in data.accuracy}
        out.append(_TABLE)
        out.append(
            f"<tr{_HEAD_ROW}>"
            + "".join(f'<th style="{_TH}">{h}</th>' for h in ("予測日数", "対象", "直近1か月", "直近3か月", "全期間"))
            + "</tr>"
        )
        stripe = 0
        # 予測日数ごとの行数: 対象区分 + 2つの買い候補の下の行 + 参考の行
        rows_per_horizon = len(_ACCURACY_SEGMENTS) + len(_EXTRA_PREFIXES) * len(_CANDIDATE_EXTRA_ROWS) + 1
        for h in const.PREDICT_HORIZONS:
            for j, seg in enumerate(_ACCURACY_SEGMENTS):
                head = (
                    f'<td rowspan="{rows_per_horizon}" style="{_TD}vertical-align:top;">{h}日後</td>' if j == 0 else ""
                )
                cells = "".join(
                    f'<td style="{_TD_NUM}">{_accuracy_cell(index.get((h, seg, p)))}</td>'
                    for p in const.VERIFY_SUMMARY_PERIODS
                )
                out.append(
                    f"<tr{_STRIPE if stripe % 2 else ''}>{head}"
                    f'<td style="{_TD}">{_SEGMENT_LABELS[seg]}</td>{cells}</tr>'
                )
                stripe += 1
                prefix = _EXTRA_PREFIXES.get(seg)
                if prefix is None:
                    continue
                # 2つの買い候補の下に、市場の中央値・業種の中央値を上回った割合と差(平均)を出す
                for label, column, sign in _CANDIDATE_EXTRA_ROWS:
                    extra = "".join(
                        f'<td style="{_TD_NUM}">{_rate_cell(index.get((h, seg, p)), column, sign)}</td>'
                        for p in const.VERIFY_SUMMARY_PERIODS
                    )
                    out.append(
                        f'<tr{_STRIPE if stripe % 2 else ""}><td style="{_TD}">{_e(prefix)}: {label}</td>{extra}</tr>'
                    )
                    stripe += 1
            ref = "".join(
                f'<td style="{_TD_NUM}color:{_GRAY_TEXT};">'
                f"{fmt_rate(row['up_rate']) if (row := index.get((h, const.SEGMENT_ALL, p))) else '-'}</td>"
                for p in const.VERIFY_SUMMARY_PERIODS
            )
            out.append(
                f"<tr{_STRIPE if stripe % 2 else ''}>"
                f'<td style="{_TD}color:{_GRAY_TEXT};">参考: 実際に上がった銘柄の割合</td>{ref}</tr>'
            )
            stripe += 1
        out.append("</table>")
    else:
        out.append('<div style="font-size:13px;">検証結果はまだありません</div>')

    # 上昇確率のずれ(ある場合だけ)
    if data.calibration_warnings:
        out.append(
            f'<div style="border:1px solid {_ORANGE};background:#fff4e5;padding:8px 12px;margin-top:12px;'
            'font-size:13px;color:#222222;">'
            f'<div style="font-weight:bold;color:{_ORANGE};margin-bottom:4px;">{_e(calibration_title())}</div>'
        )
        for w in data.calibration_warnings:
            out.append(f"<div>{_e(calibration_line(w))}</div>")
        out.append("</div>")

    out.append(
        f'<div style="font-size:12px;color:{_GRAY_TEXT};margin-top:20px;border-top:1px solid #dddddd;padding-top:8px;">'
        + "<br>".join(_FOOTER_LINES)
        + "</div></body></html>"
    )
    return "\n".join(out)


def calibration_title() -> str:
    return "注意: 上昇確率が、実際に上がった割合とずれています(直近3か月)。確率の補正を検討してください。"


def calibration_line(w: dict[str, Any]) -> str:
    band = int(w["prob_band"])
    return (
        f"{w['horizon']}日後・{band}〜{band + 10}%の帯: 確率の平均 {fmt_rate(w['avg_up_prob'])} / "
        f"実際に上がった割合 {fmt_rate(w['up_rate'])}({int(w['verified_count']):,}件)"
    )


def build_text(data: NotifyData, is_backfill: bool) -> str:
    """HTMLを表示できないメールソフトのための、文字だけの本文を作る。"""
    lines: list[str] = []
    if is_backfill:
        lines += [
            f"この通知は、取り逃した日の分を後から作成したものです。買い候補は {data.target_date.isoformat()} "
            "時点の株価に基づきます。",
            "",
        ]
    lines += [
        f"対象日: {fmt_date(data.target_date)}",
        f"予測に使ったモデル: {data.model_version}",
        f"予測した銘柄数: {data.code_count:,}",
        "",
        f"■ {index_block_title(data.target_date)}",
        *market_state_lines(data.market_state, data.index_trend),
        "",
        f"■ 買い候補(上位{const.BUY_CANDIDATE_COUNT}銘柄。主となる日数: {const.MAIN_HORIZON}営業日後、"
        f"順位付け: {_RANKING_LABELS.get(const.RANKING_METHOD, const.RANKING_METHOD)})",
    ]

    def probs(by: dict[int, HorizonValues]) -> str:
        # 主となる日数の値の前に「★」を付ける(色や太字を使えないため)
        parts = []
        for h in const.PREDICT_HORIZONS:
            values = by.get(h)
            rel, p, pr = (None, None, None) if values is None else values
            mark = _MAIN_MARK if h == const.MAIN_HORIZON else ""
            parts.append(f"{mark}{h}日後 市場比 {fmt_prob(rel)} 上昇 {fmt_prob(p)} {fmt_price(pr)}円")
        return "、".join(parts)

    def candidate_lines(candidates: list[Candidate]) -> list[str]:
        if not candidates:
            return ["買い候補はありません"]
        result = []
        for c in candidates:
            note = f" [{NOTE_SEPARATOR.join(c.notes)}]" if c.notes else ""
            head = f"{c.rank}. {display_code(c.code)} {c.name}{_market_text(c.market)} 業種 {c.sector or '-'}"
            result.append(f"{head} 現在値 {fmt_price(c.base_price)} / {probs(c.by_horizon)}{note}")
        return result

    def fallback_lines(candidates: list[Candidate]) -> list[str]:
        note = candidate_fallback_note(candidates)
        return [] if note is None else [note]

    lines += fallback_lines(data.candidates)
    lines += candidate_lines(data.candidates)
    lines += ["", f"■ {candidate_sector_title()}(並べ方は買い候補と同じ)"]
    lines += fallback_lines(data.candidates_sector)
    lines += candidate_lines(data.candidates_sector)
    lines += ["", "■ 保有銘柄"]
    if not data.holdings:
        lines.append("保有銘柄はありません")
    if data.holdings:
        lines.append(holding_totals_text(data.holdings))
    for hd in data.holdings:
        rate, amount, _ = _pnl_cells(hd.buy_price, hd.current, hd.quantity)
        lines.append(
            f"{display_code(hd.code)} {hd.name}{_market_text(hd.market)}"
            f" / 買値 {fmt_price(hd.buy_price)} 買い株数 {hd.quantity:,}"
            f" 買いの金額 {_amount(hd.buy_price, hd.quantity)}"
            f" 購入日 {_purchase_date_text(hd)}"
            f" 現在値 {fmt_price(hd.current)} 現在の金額 {_amount(hd.current, hd.quantity)}"
            f" 損益 {rate}({amount}円)"
            f" / {probs(hd.by_horizon)} / 判定: {hd.judgment}"
            + (f" [{NOTE_SEPARATOR.join(hd.notes)}]" if hd.notes else "")
        )
        if len(hd.lots) > 1:
            for lot in hd.lots:
                lrate, lamount, _ = _pnl_cells(lot.buy_price, hd.current, lot.quantity)
                lines.append(
                    f"└ 買値 {fmt_price(lot.buy_price)} 買い株数 {lot.quantity:,}"
                    f" 買いの金額 {_amount(lot.buy_price, lot.quantity)} 購入日 {lot.buy_date.isoformat()}"
                    f" 現在の金額 {_amount(hd.current, lot.quantity)} 損益 {lrate}({lamount}円)"
                )
    lines += ["", "■ 的中率(検証結果。直近1か月 / 直近3か月 / 全期間)"]
    if not data.accuracy:
        lines.append("検証結果はまだありません")
    else:
        index = {(r["horizon"], r["segment"], r["period_type"]): r for r in data.accuracy}
        for h in const.PREDICT_HORIZONS:
            for seg in _ACCURACY_SEGMENTS:
                vals = " / ".join(_accuracy_cell(index.get((h, seg, p))) for p in const.VERIFY_SUMMARY_PERIODS)
                lines.append(f"{h}日後 {_SEGMENT_LABELS[seg]}: {vals}")
                prefix = _EXTRA_PREFIXES.get(seg)
                if prefix is None:
                    continue
                for label, column, sign in _CANDIDATE_EXTRA_ROWS:
                    extra = " / ".join(
                        _rate_cell(index.get((h, seg, p)), column, sign) for p in const.VERIFY_SUMMARY_PERIODS
                    )
                    lines.append(f"{h}日後 {prefix}: {label}: {extra}")
    if data.calibration_warnings:
        lines += ["", calibration_title()] + [calibration_line(w) for w in data.calibration_warnings]
    lines += ["", *_FOOTER_LINES]
    return "\n".join(lines) + "\n"


def mail_subject(target_date: date, is_backfill: bool) -> str:
    """件名。"""
    backfill = "【補完分】" if is_backfill else ""
    return f"{const.MAIL_SUBJECT_PREFIX}{backfill}買い候補・保有銘柄 {target_date.isoformat()}"


# 通知の内容のファイル(text_path)で、1日分の区切りに使う線
TEXT_FILE_SEPARATOR: str = mailer.TEXT_FILE_SEPARATOR


def append_text_file(path: Path, subject: str, text_body: str) -> None:
    """メールの件名とテキスト形式の本文を、ファイルの最後に追記する(range_date_full_process.py)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(mailer.format_text_file(subject, text_body))


def notify(
    conn: psycopg.Connection[DictRow],
    target_date: date,
    is_backfill: bool,
    logger: BatchLogger,
    send_mail: bool = True,
    text_path: Path | None = None,
    warnings: BatchWarnings | None = None,
) -> str:
    """通知の手順2〜8を行い、メールの件名を返す。

    send_mail が False の場合は、メールの内容を作るところまで行い、送らずにログに残す
    (range_date_full_process.py で、日数分のメールが送られないようにするため)。
    text_path を渡した場合は、メールのテキスト形式の本文を、そのファイルに追記する。
    warnings を渡した場合(日次バッチ)は、一部の宛先にだけ送れなかったとき、失敗にせず警告に加える。
    """
    data = load_notify_data(conn, target_date)
    subject = mail_subject(target_date, is_backfill)
    html_body = build_html(data, subject, is_backfill)
    text_body = build_text(data, is_backfill)
    if text_path is not None:
        append_text_file(text_path, subject, text_body)
    if not send_mail:
        logger.info(
            "メールは送りませんでした(%s。買い候補 %d件、買い候補(業種分散) %d件、保有銘柄 %d件)",
            subject,
            len(data.candidates),
            len(data.candidates_sector),
            len(data.holdings),
        )
        return subject
    try:
        mailer.send_mail(subject, html_body, text_body, target_date=target_date)
    except MailSendError as e:
        if warnings is None or not e.partial:
            raise
        warnings.add_partial_mail(subject, e)
        logger.warning("一部の宛先にメールを送れませんでした(%s。%d件)", subject, len(e.failed))
        return subject
    logger.info(
        "メールを送りました(%s。買い候補 %d件、買い候補(業種分散) %d件、保有銘柄 %d件)",
        subject, len(data.candidates), len(data.candidates_sector), len(data.holdings),
    )  # fmt: skip
    return subject


def run_notify(
    target_date: date,
    is_backfill: bool = False,
    batch_type: str = const.BATCH_TYPE_SINGLE,
    send_mail: bool = True,
    text_path: Path | None = None,
    warnings: BatchWarnings | None = None,
) -> str:
    """通知を実行する。実行履歴とログの開始・終了も行う。

    Args:
        target_date: 指定日
        is_backfill: 補完した日の分かどうか(日次バッチだけが渡す)
        batch_type: 起動のしかた
        send_mail: False の場合はメールを送らず、ログに残すだけにする(range_date_full_process.py が渡す)
        text_path: メールのテキスト形式の本文を追記するファイル(range_date_full_process.py が渡す)
        warnings: 一部の宛先にだけ送れなかった場合に、警告を加える先(日次バッチが渡す)

    Raises:
        BatchError: 失敗した場合
    """
    logger = get_logger(LABEL, target_date)

    def body() -> ProcessOutcome[str]:
        logger.info("処理を開始します")
        with db.connect() as conn:
            subject = notify(conn, target_date, is_backfill, logger, send_mail, text_path, warnings)
        logger.info("処理を終了します")
        return ProcessOutcome(result=subject, status=const.RUN_STATUS_SUCCESS, record_count=1)

    return run_with_history(batch_type, const.PROCESS_NOTIFY, target_date, logger, body)
