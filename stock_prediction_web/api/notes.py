"""備考の計算。メールと同じ結果にするため、バッチの core の関数を使う。

備考の並びはメールと同じ:
  買い候補・銘柄の検索・銘柄詳細: ストップ高・ストップ安 → 流動性注意 → クロス → トレンドラインの抜け → 割安さ・稼ぐ力
  保有銘柄: 損切りライン → クロス → トレンドラインの抜け → 割安さ・稼ぐ力
"""

from dataclasses import dataclass
from datetime import date
from threading import Lock

import psycopg
from psycopg.rows import DictRow

import const
from api.common import Note
from core.features import cross_days_on, trendline_notes_on
from core.notify_core import (
    NOTE_DEAD_MID,
    NOTE_DEAD_SHORT,
    NOTE_DEFICIT,
    NOTE_GOLDEN_MID,
    NOTE_GOLDEN_SHORT,
    NOTE_LOW_LIQUIDITY,
    NOTE_PER_LOW,
    cross_notes,
    is_blue_note,
    is_stale_close_note,
    pbr_note,
    roe_note,
    valuation_notes_on,
)
from core.trendline import NOTE_SUPPORT_PREFIX

# ストップ高・ストップ安の備考(メールと同じ文言)
NOTE_UPPER_LIMIT: str = "ストップ高"
NOTE_LOWER_LIMIT: str = "ストップ安"
# 上値抵抗線の上抜けの備考の先頭(core/trendline.py の NOTE_RESIST_BREAK の先頭)
NOTE_RESIST_PREFIX: str = "上値抵抗線"


def to_note(text: str) -> Note:
    """備考の文言に、画面の色を付ける(メールと同じ。流動性注意は緑、「MM/DD の終値」は橙、
    デッドクロス・赤字予想・下抜けは青、それ以外は赤)。"""
    if text == NOTE_LOW_LIQUIDITY:
        return Note(text=text, color="green")
    if is_stale_close_note(text):
        return Note(text=text, color="orange")
    return Note(text=text, color="blue" if is_blue_note(text) else "red")


def to_notes(texts: list[str]) -> list[Note]:
    return [to_note(t) for t in texts]


def market_notes(conn: psycopg.Connection[DictRow], target: date, codes: list[str]) -> dict[str, list[str]]:
    """ストップ高・ストップ安と流動性注意(core/notify_core.py の load_notify_data の手順3 と同じ条件)。"""
    if not codes:
        return {}
    flags = {
        str(r["code"]): r
        for r in conn.execute(
            "SELECT code, upper_limit_flag, lower_limit_flag FROM daily_price WHERE trade_date = %s AND code = ANY(%s)",
            (target, codes),
        ).fetchall()
    }
    liquidity = conn.execute(
        """
        SELECT code, avg(COALESCE(turnover, 0)) AS avg_turnover
        FROM (
            SELECT code, turnover, row_number() OVER (PARTITION BY code ORDER BY trade_date DESC) AS n
            FROM daily_price WHERE code = ANY(%s) AND trade_date <= %s
        ) t
        WHERE n <= %s GROUP BY code
        """,
        (codes, target, const.LIQUIDITY_AVG_DAYS),
    ).fetchall()
    low = {str(r["code"]) for r in liquidity if r["avg_turnover"] < const.LIQUIDITY_WARN_TURNOVER}
    result: dict[str, list[str]] = {}
    for code in codes:
        notes: list[str] = []
        f = flags.get(code)
        if f is not None and f["upper_limit_flag"]:
            notes.append(NOTE_UPPER_LIMIT)
        if f is not None and f["lower_limit_flag"]:
            notes.append(NOTE_LOWER_LIMIT)
        if code in low:
            notes.append(NOTE_LOW_LIQUIDITY)
        result[code] = notes
    return result


def signal_notes(conn: psycopg.Connection[DictRow], target: date, codes: list[str]) -> dict[str, list[str]]:
    """クロス、トレンドラインの抜け、割安さ・稼ぐ力の備考(この順。メールと同じ)。"""
    result: dict[str, list[str]] = {code: [] for code in codes}
    if not codes:
        return result
    frame = cross_days_on(conn, target, codes, const.CROSS_DAYS_CAP)
    for code, short, mid in zip(
        frame["code"].tolist(),
        frame["days_since_cross_5_25"].astype(float).tolist(),
        frame["days_since_cross_25_75"].astype(float).tolist(),
        strict=True,
    ):
        result.setdefault(str(code), []).extend(cross_notes(short, mid))
    for code, notes in trendline_notes_on(conn, target, codes).items():
        result.setdefault(code, []).extend(notes)
    for code, notes in valuation_notes_on(conn, target, codes).items():
        result.setdefault(code, []).extend(notes)
    return result


def stock_notes(conn: psycopg.Connection[DictRow], target: date, codes: list[str]) -> dict[str, list[str]]:
    """買い候補と同じ並びの備考(銘柄の検索・銘柄詳細で使う)。備考がない銘柄は空のリスト。"""
    codes = sorted(set(codes))
    base = market_notes(conn, target, codes)
    signals = signal_notes(conn, target, codes)
    return {code: base.get(code, []) + signals.get(code, []) for code in codes}


class NoteCache:
    """予測日ごとに、全銘柄の備考を API のメモリに残す。

    同じ予測日の2回目からは計算しない。残すのは web_const.NOTE_CACHE_DAYS 日分まで(古いものから消す)。
    """

    def __init__(self, keep_days: int) -> None:
        self._keep_days = keep_days
        self._data: dict[date, dict[str, list[str]]] = {}
        self._lock = Lock()

    def get(self, conn: psycopg.Connection[DictRow], target: date, codes: list[str]) -> dict[str, list[str]]:
        """その予測日の、指定した銘柄の備考。まだ計算していない銘柄だけを計算して残す。"""
        with self._lock:
            cached = self._data.setdefault(target, {})
            missing = [c for c in codes if c not in cached]
            if missing:
                cached.update(stock_notes(conn, target, missing))
            # 古い予測日から消す
            while len(self._data) > self._keep_days:
                del self._data[min(self._data)]
            return {c: cached.get(c, []) for c in codes}

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


@dataclass(frozen=True)
class NoteFilter:
    """銘柄の検索で絞り込む備考の種類。

    文言が完全に一致するか、prefix で始まる備考を「付いている」とみなす(トレンドラインは接触回数で文言が変わるため)。
    """

    key: str
    label: str
    exact: str = ""
    prefix: str = ""

    def matches(self, notes: list[str]) -> bool:
        return any((self.exact and n == self.exact) or (self.prefix and n.startswith(self.prefix)) for n in notes)


def note_filters() -> list[NoteFilter]:
    """絞り込みに使える備考の一覧(const.py の閾値で文言が変わるものは、呼ぶたびに作る)。"""
    return [
        NoteFilter("upper_limit", NOTE_UPPER_LIMIT, exact=NOTE_UPPER_LIMIT),
        NoteFilter("lower_limit", NOTE_LOWER_LIMIT, exact=NOTE_LOWER_LIMIT),
        NoteFilter("low_liquidity", NOTE_LOW_LIQUIDITY, exact=NOTE_LOW_LIQUIDITY),
        # クロスは「(本日)」「(N営業日前)」が後ろに付くので、先頭の一致で判定する
        NoteFilter("golden_short", NOTE_GOLDEN_SHORT, prefix=NOTE_GOLDEN_SHORT),
        NoteFilter("dead_short", NOTE_DEAD_SHORT, prefix=NOTE_DEAD_SHORT),
        NoteFilter("golden_mid", NOTE_GOLDEN_MID, prefix=NOTE_GOLDEN_MID),
        NoteFilter("dead_mid", NOTE_DEAD_MID, prefix=NOTE_DEAD_MID),
        NoteFilter("resist_break", "上値抵抗線を上抜け", prefix=NOTE_RESIST_PREFIX),
        NoteFilter("support_break", "下値支持線を下抜け", prefix=NOTE_SUPPORT_PREFIX),
        NoteFilter("pbr", pbr_note(), exact=pbr_note()),
        NoteFilter("per_low", NOTE_PER_LOW, exact=NOTE_PER_LOW),
        NoteFilter("roe", roe_note(), exact=roe_note()),
        NoteFilter("deficit", NOTE_DEFICIT, exact=NOTE_DEFICIT),
    ]
