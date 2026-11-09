"""トレンドラインの計算。

人がチャートに線を引くときの手順を、計算に置き換える。

  1. 線を引く期間(TREND_WINDOW_START_DAYS〜TREND_WINDOW_END_DAYS 営業日前。初期値 60〜3営業日前)の中で、
     前後 TREND_PIVOT_DAYS(3営業日)の中で安値が一番低い日を「谷」、高値が一番高い日を「山」とする。
     期間の始まりは、60営業日前と「データがある一番古い日から TREND_PIVOT_DAYS 後の日」の新しいほう。
     期間が TREND_MIN_WINDOW_DAYS(20営業日)未満なら、線を引かない。
  2. 谷(山)を2つずつ結んで線の候補を作る。2つは TREND_PIVOT_MIN_GAP_DAYS(10営業日)以上離れた組み合わせだけ。
  3. 結んだ2つの谷(山)の間に、終値が線を TREND_BREAK_RATE(3%)以上はみ出した日がある候補は外す。
     2つ目の谷(山)より後に終値が3%以上抜けた候補は外さず、「抜けた線」として残す。
     抜けた日は、2つ目の谷(山)より後で、終値が初めて線を3%以上抜けた日(今日までの間で探す)。
  4. 接触回数は、線を引く期間の中で数える。抜けた日がある場合は、抜けた日の前の日までを数える。
  5. 3 を通った候補のうち、線の今日の値が今日の終値から TREND_MAX_DISTANCE_RATE(15%)より離れた候補、
     線の今日の値が0以下の候補は外す(今日の終値が空なら、離れ具合の条件はかけない)。
     抜けた日が今日から TREND_KEEP_BROKEN_DAYS(5)営業日より前の候補も外す。
     残った候補のうち、接触回数が一番多い線を選ぶ。同じ回数なら、新しい谷(山)を使っている線を選ぶ。
  6. 候補が作れない、または1つも残らない場合は、線を引く期間を前半と後半に分け、それぞれで安値が一番低い日
     (上値抵抗線は高値が一番高い日)を結んだ線を引く(最後の手段)。接触回数と抜けた日は 3、4 と同じに求める。
     最後の手段の線にも 5 の離れ具合と古く抜けた線の条件をかけ、外れた場合は線を引かない。

谷・山は「その日の前後3営業日」で決めるが、線を引く期間の最後は3営業日前なので、使う株価はいつも今日以前になる
(未来の株価は使わない)。find_line は、渡された配列のうち今日より後の行を使わない(関数の最初で今日までに切る)。
pivot_flags を今日より後の行も含む配列で求めても、線を引く期間(3営業日前まで)の谷・山の判定は今日までの株価で
決まるので、結果は変わらない。下値支持線は谷(安値)、上値抵抗線は山(高値)を結ぶ。
線の今日の値が0以下になる線は、5・6 の条件で外れる。ただし今日の終値が空の場合は条件をかけないので引くことがある
(画面のチャートは0円で止める。備考・特徴量は、線の今日の値が0以下なら出さない)。

ここにある関数は、DB を読まない(配列だけで計算する)。DB から読む部分は core/features.py にある。
株価は、株式分割を調整した値を渡す。
"""

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

import const

# 線の種類
SUPPORT: str = "SUPPORT"  # 下値支持線(谷を結ぶ)
RESIST: str = "RESIST"  # 上値抵抗線(山を結ぶ)

# 備考の文言。{n} は接触回数
NOTE_RESIST_BREAK: str = "上値抵抗線(接触{n}回)を上抜け"
NOTE_SUPPORT_BREAK: str = "下値支持線(接触{n}回)を下抜け"
NOTE_TODAY: str = "本日"
NOTE_DAYS_AGO: str = "{n}営業日前"
NOTE_SUPPORT_PREFIX: str = "下値支持線"  # 下抜けの備考は青で表示する(この文字列で始まる備考)
SUFFIX_FROM_DOWN: str = "(下降から転換)"
SUFFIX_UP_ACCEL: str = "(上昇が加速)"
SUFFIX_FROM_UP: str = "(上昇から転換)"
SUFFIX_DOWN_ACCEL: str = "(下降が加速)"


@dataclass(frozen=True)
class TrendLine:
    """選んだトレンドライン。位置(start、end、today)は、銘柄の株価の行の並びでの番号。"""

    kind: str  # SUPPORT または RESIST
    start: int  # 線を結んだ古いほうの谷(山)の位置
    end: int  # 線を結んだ新しいほうの谷(山)の位置
    start_value: float  # 古いほうの谷の安値(山の高値)
    slope: float  # 1営業日あたりの線の傾き(円)
    touches: int  # 接触回数(抜けた日がある場合は、抜けた日の前の日までの回数)
    break_at: int | None = None  # 抜けた日の位置(2つ目の谷(山)より後で、終値が初めて線を3%以上抜けた日)。なければ None

    def value_at(self, position: int) -> float:
        """その位置での線の値。"""
        return self.start_value + self.slope * (position - self.start)


def required_rows() -> int:
    """線を引くのに必要な、今日より前の株価の行の数(これより少ないと、線を引く期間が TREND_MIN_WINDOW_DAYS 未満になる)。

    線を引く期間の最初の日の前に、谷・山の判定に使う TREND_PIVOT_DAYS 分の行が要る。
    """
    return const.TREND_PIVOT_DAYS + const.TREND_WINDOW_END_DAYS + const.TREND_MIN_WINDOW_DAYS - 1


def line_window(today: int) -> tuple[int, int] | None:
    """線を引く期間(最初の位置、最後の位置)。期間が TREND_MIN_WINDOW_DAYS 営業日未満なら None。

    始まりは、TREND_WINDOW_START_DAYS 営業日前と、データがある一番古い日から TREND_PIVOT_DAYS 後の日の新しいほう。
    終わりは TREND_WINDOW_END_DAYS 営業日前。
    """
    first = max(today - const.TREND_WINDOW_START_DAYS, const.TREND_PIVOT_DAYS)
    last = today - const.TREND_WINDOW_END_DAYS
    if last - first + 1 < const.TREND_MIN_WINDOW_DAYS:
        return None
    return first, last


def pivot_flags(values: np.ndarray, lowest: bool) -> np.ndarray:
    """谷(lowest が True)または山の日に True を立てた配列を返す。

    前後 TREND_PIVOT_DAYS 営業日(配列の端では、ある分だけ)の中で一番低い(高い)日を谷(山)とする。
    同じ値の日が並んだ場合は、その全てが谷(山)になる。値が空の日は谷(山)にしない。
    """
    s = pd.Series(values, dtype=float)
    window = 2 * const.TREND_PIVOT_DAYS + 1
    roll = s.rolling(window, center=True, min_periods=1)
    extreme = roll.min() if lowest else roll.max()
    flags: np.ndarray = (s.notna() & (s == extreme)).to_numpy()
    return flags


@dataclass(frozen=True)
class _Measured:
    """線の候補ごとの計算結果(配列の並びは、候補の並び)。"""

    start_value: np.ndarray  # 古いほうの点の値(円)
    slope: np.ndarray  # 1営業日あたりの傾き(円)
    valid: np.ndarray  # 結んだ2つの点の間に、はみ出しがない
    break_at: np.ndarray  # 抜けた日の位置(なければ -1)
    touches: np.ndarray  # 接触回数


def _measure(
    kind: str,
    a: np.ndarray,
    b: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    window: tuple[int, int],
    today: int,
    tol: float,
    brk: float,
) -> _Measured:
    """位置 a と b の点を結んだ線の候補ごとに、はみ出し・抜けた日・接触回数を求める。"""
    first, last = window
    points = low if kind == SUPPORT else high
    va, vb = points[a], points[b]
    slope = (vb - va) / (b - a)
    idx = np.arange(first, today + 1)
    pos = idx[None, :]
    line = va[:, None] + slope[:, None] * (pos - a[:, None])
    c = close[idx][None, :]
    with np.errstate(invalid="ignore"):
        if kind == SUPPORT:
            # 終値が線より3%以上下の日が、はみ出し(抜け)
            outside = c < line * (1 - brk)
            # 安値が線から上に2%以内まで下がり、終値は線より上で終わった日が接触
            touch = (low[idx][None, :] <= line * (1 + tol)) & (c > line)
        else:
            outside = c > line * (1 + brk)
            touch = (high[idx][None, :] >= line * (1 - tol)) & (c < line)
    # 結んだ2つの点の間に、はみ出しがある線は外す
    between = (pos >= a[:, None]) & (pos <= b[:, None])
    valid = ~(outside & between).any(axis=1)
    # 2つ目の点より後で、初めて抜けた日(今日まで)
    after = outside & (pos > b[:, None])
    has_break = after.any(axis=1)
    break_at = np.where(has_break, idx[after.argmax(axis=1)], -1)
    # 線を結んだ2つの点は、必ず接触に数える
    touch = touch | (pos == a[:, None]) | (pos == b[:, None])
    # 接触は線を引く期間の中で数える。抜けた日がある場合は、抜けた日の前の日まで
    limit = np.where(has_break, np.minimum(last, break_at - 1), last)
    touch = touch & (pos <= limit[:, None])
    # 続けて接触した日は1回と数える(接触の始まりの日を数える)
    started = touch & ~np.concatenate([np.zeros((len(a), 1), dtype=bool), touch[:, :-1]], axis=1)
    return _Measured(va, slope, valid, break_at, started.sum(axis=1))


def _to_line(kind: str, start: int, end: int, m: _Measured, k: int) -> TrendLine:
    """候補 k の計算結果を TrendLine にする。"""
    brk_at = int(m.break_at[k])
    return TrendLine(
        kind, start, end, float(m.start_value[k]), float(m.slope[k]), int(m.touches[k]), None if brk_at < 0 else brk_at
    )


def _near_today(m: _Measured, a: np.ndarray, close: np.ndarray, today: int) -> np.ndarray:
    """候補ごとに、線の今日の値が今日の終値から TREND_MAX_DISTANCE_RATE 以内なら True。

    線の今日の値が0以下の候補は False。今日の終値が空の場合は、離れ具合の条件をかけない(全て True)。
    ちょうど上限の線は外さない(小数の誤差の分だけ余裕を持たせる)。
    """
    value = m.start_value + m.slope * (today - a)
    today_close = float(close[today])
    if math.isnan(today_close):
        return np.ones(len(a), dtype=bool)
    with np.errstate(invalid="ignore", divide="ignore"):
        distance = np.abs(value - today_close) / today_close
    near: np.ndarray = (value > 0) & (distance <= const.TREND_MAX_DISTANCE_RATE * (1 + 1e-9))
    return near


def _not_old_break(m: _Measured, today: int) -> np.ndarray:
    """候補ごとに、抜けていない、または抜けた日が直近 TREND_KEEP_BROKEN_DAYS 営業日以内なら True。

    数え方は break_days_ago(備考の抜けの判定)と同じで、株価の行で数える。今日を含めて TREND_KEEP_BROKEN_DAYS 行の中
    (今日から数えて 0〜TREND_KEEP_BROKEN_DAYS-1 営業日前)に抜けた線を残す。
    """
    ok: np.ndarray = (m.break_at < 0) | (today - m.break_at < const.TREND_KEEP_BROKEN_DAYS)
    return ok


def _fallback_points(points: np.ndarray, window: tuple[int, int], lowest: bool) -> tuple[int, int] | None:
    """最後の手段の線を結ぶ2つの位置(期間の前半と後半から1つずつ)。

    2つは TREND_PIVOT_MIN_GAP_DAYS 以上離す。条件を満たす組の中で、2つの値の合計が一番低い(高い)組を選ぶ
    (2つが十分離れていれば、前半と後半それぞれで一番低い(高い)日と同じ)。
    同じ値が並んだ場合は、古い日の組。値が空の日は使わない。条件を満たす組がなければ None(線を引かない)。
    """
    first, last = window
    mid = first + (last - first + 1) // 2
    head = np.arange(first, mid)
    tail = np.arange(mid, last + 1)
    if len(head) == 0 or len(tail) == 0:
        return None
    # 低い(高い)ほど小さくなる値にそろえる。空の日は選ばれないよう無限大にする
    sign = 1.0 if lowest else -1.0
    head_cost = np.where(np.isnan(points[head]), np.inf, sign * points[head])
    tail_cost = np.where(np.isnan(points[tail]), np.inf, sign * points[tail])
    cost = head_cost[:, None] + tail_cost[None, :]
    cost = np.where((tail[None, :] - head[:, None]) >= const.TREND_PIVOT_MIN_GAP_DAYS, cost, np.inf)
    if not np.isfinite(cost).any():
        return None
    row, col = np.unravel_index(int(np.argmin(cost)), cost.shape)
    return int(head[row]), int(tail[col])


def find_line(
    kind: str,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    flags: np.ndarray,
    today: int,
    touch_tolerance: float | None = None,
    break_rate: float | None = None,
) -> TrendLine | None:
    """今日(today の位置)の時点のトレンドラインを引く。引けない場合は None。

    Args:
        kind: SUPPORT(下値支持線)または RESIST(上値抵抗線)
        high、low、close: 1銘柄の調整済みの高値・安値・終値(日付の古い順)
        flags: pivot_flags の結果(SUPPORT なら安値の谷、RESIST なら高値の山)
        today: 今日の位置
        touch_tolerance: 接触とみなす幅。省略すると TREND_TOUCH_TOLERANCE(2%)
        break_rate: はみ出し・抜けとみなす幅。省略すると TREND_BREAK_RATE(3%)
    """
    tol = const.TREND_TOUCH_TOLERANCE if touch_tolerance is None else touch_tolerance
    brk = const.TREND_BREAK_RATE if break_rate is None else break_rate
    # 未来の株価を使わないように、今日より後の行は切る(評価ツールは、全ての期間の配列を渡す)
    high, low, close, flags = high[: today + 1], low[: today + 1], close[: today + 1], flags[: today + 1]
    window = line_window(today)
    if window is None:
        return None
    first, last = window
    pivots = np.flatnonzero(flags[first : last + 1]) + first
    i, j = np.triu_indices(len(pivots), k=1)
    a, b = pivots[i], pivots[j]
    keep = (b - a) >= const.TREND_PIVOT_MIN_GAP_DAYS
    a, b = a[keep], b[keep]
    if len(a) > 0:
        m = _measure(kind, a, b, high, low, close, window, today, tol, brk)
        usable = m.valid & _near_today(m, a, close, today) & _not_old_break(m, today)
        if usable.any():
            # 接触回数が多い順、同じなら新しい谷(山)を使っている順に選ぶ
            order = np.lexsort((a, b, m.touches))
            best = int(order[usable[order]][-1])
            return _to_line(kind, int(a[best]), int(b[best]), m, best)
    # 最後の手段: 期間の前半と後半で、安値が一番低い(高値が一番高い)日を結ぶ(2つは TREND_PIVOT_MIN_GAP_DAYS 以上離す)
    pair = _fallback_points(low if kind == SUPPORT else high, window, kind == SUPPORT)
    if pair is None:
        return None
    fa, fb = np.array([pair[0]]), np.array([pair[1]])
    fm = _measure(kind, fa, fb, high, low, close, window, today, tol, brk)
    # 最後の手段の線にも、通常の候補と同じ離れ具合の条件をかける(今日の値が0以下、15%より離れたら引かない)
    # 古く抜けた線も引かない(手順5と同じ)
    if not bool((_near_today(fm, fa, close, today) & _not_old_break(fm, today))[0]):
        return None
    return _to_line(kind, pair[0], pair[1], fm, 0)


def gap_from_line(line: TrendLine | None, close: np.ndarray, today: int) -> float:
    """終値 ÷ 線の今日の値 − 1(特徴量 trend_support_gap、trend_resist_gap)。線がない、線の値が0以下なら空(NaN)。"""
    if line is None:
        return math.nan
    value = line.value_at(today)
    if value <= 0 or math.isnan(close[today]):
        return math.nan
    return float(close[today] / value - 1)


def _outside(kind: str, close: float, value: float, break_rate: float) -> bool:
    """終値が線を幅 break_rate 以上抜けているか(SUPPORT なら下に、RESIST なら上に)。空の日は抜けていない扱い。"""
    if math.isnan(close):
        return False
    return close < value * (1 - break_rate) if kind == SUPPORT else close > value * (1 + break_rate)


def days_ago_text(days_ago: int) -> str:
    """起きた日が今日から何営業日前かを、備考に付ける言葉にする(0 なら「本日」、1 以上なら「N営業日前」)。

    営業日は株価の行で数える(土日・祝日は行がないので数えない)。
    """
    return NOTE_TODAY if days_ago <= 0 else NOTE_DAYS_AGO.format(n=days_ago)


def break_days_ago(line: TrendLine, close: np.ndarray, today: int, break_rate: float | None = None) -> int | None:
    """線を抜けた日が今日から何営業日前かを返す(0 なら今日)。直近 CROSS_NOTE_DAYS 営業日以内に抜けていなければ None。

    抜けた日は、前の日の終値が抜けの幅の内側で、その日の終値が外側に出た日。今日まで外側が続いていること。
    今日の終値が内側に戻っていたら None(だましを減らすため)。外側が続いているので、抜けた日は1つに決まる
    (直近に複数回抜けていても、今の外側の続きの始まり = 一番新しい抜けた日)。
    """
    brk = const.TREND_BREAK_RATE if break_rate is None else break_rate
    if line.value_at(today) <= 0:
        return None
    day = today
    while day >= 0 and _outside(line.kind, float(close[day]), line.value_at(day), brk):
        day -= 1
    run_start = day + 1
    if run_start > today or day < 0:
        return None
    if today - run_start + 1 > const.CROSS_NOTE_DAYS:
        return None
    return today - run_start


def broke_recently(line: TrendLine, close: np.ndarray, today: int, break_rate: float | None = None) -> bool:
    """直近 CROSS_NOTE_DAYS 営業日以内に終値が線を抜け、今日の終値も抜けたままなら True。"""
    return break_days_ago(line, close, today, break_rate) is not None


def direction_suffix(line: TrendLine, today: int) -> str:
    """線の傾きから、トレンドの向きを表す言葉を返す。ほぼ水平(傾き ÷ 線の今日の値 が TREND_FLAT_SLOPE 未満)なら空。"""
    value = line.value_at(today)
    if value <= 0 or abs(line.slope / value) < const.TREND_FLAT_SLOPE:
        return ""
    rising = line.slope > 0
    if line.kind == RESIST:
        return SUFFIX_UP_ACCEL if rising else SUFFIX_FROM_DOWN
    return SUFFIX_FROM_UP if rising else SUFFIX_DOWN_ACCEL


def break_note(line: TrendLine | None, close: np.ndarray, today: int, break_rate: float | None = None) -> str | None:
    """線を抜けた場合の備考の文言を返す。出さない場合は None。

    接触回数が TREND_MIN_TOUCHES(3回)以上の線だけを対象にする。
    """
    if line is None or line.touches < const.TREND_MIN_TOUCHES:
        return None
    days_ago = break_days_ago(line, close, today, break_rate)
    if days_ago is None:
        return None
    base = NOTE_RESIST_BREAK if line.kind == RESIST else NOTE_SUPPORT_BREAK
    # 例: (下降から転換・2営業日前)。転換・加速が付かない(ほぼ水平の)ときは (2営業日前)
    direction = direction_suffix(line, today).strip("()")
    parts = [direction, days_ago_text(days_ago)] if direction else [days_ago_text(days_ago)]
    return base.format(n=line.touches) + "(" + "・".join(parts) + ")"


def notes_for_series(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    today: int,
    touch_tolerance: float | None = None,
    break_rate: float | None = None,
) -> list[str]:
    """1銘柄の今日の、トレンドラインの抜けの備考(上値抵抗線 → 下値支持線の順)。"""
    notes: list[str] = []
    for kind, values, lowest in ((RESIST, high, False), (SUPPORT, low, True)):
        line = find_line(kind, high, low, close, pivot_flags(values, lowest), today, touch_tolerance, break_rate)
        note = break_note(line, close, today, break_rate)
        if note is not None:
            notes.append(note)
    return notes


def gaps_for_series(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, positions: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """1銘柄の、指定した位置ごとの trend_support_gap と trend_resist_gap を返す(特徴量の候補)。"""
    low_flags = pivot_flags(low, True)
    high_flags = pivot_flags(high, False)
    support = np.full(len(positions), np.nan)
    resist = np.full(len(positions), np.nan)
    for k, today in enumerate(positions):
        t = int(today)
        if t < required_rows():
            continue
        support[k] = gap_from_line(find_line(SUPPORT, high, low, close, low_flags, t), close, t)
        resist[k] = gap_from_line(find_line(RESIST, high, low, close, high_flags, t), close, t)
    return support, resist
