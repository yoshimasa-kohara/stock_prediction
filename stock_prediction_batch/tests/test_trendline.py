"""core/trendline.py と、トレンドラインに関係する features.py・tools/count_trend_notes.py のテスト。"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
import pytest
from psycopg.rows import DictRow

import const
from common import business_day
from common.env import resolve_path
from core import features, trendline
from tests.sample_data import SAMPLE_CODES, insert_sample_data
from tools import count_trend_notes as ct

N = 100  # テストの株価の日数
TODAY = N - 1  # 今日の位置。線を引く期間は 39〜96
TOUCH_DAYS = (45, 60, 75, 90)  # 線に触れる日


def rising_support(touch_days: tuple[int, ...] = TOUCH_DAYS, slope: float = 2.0) -> tuple[np.ndarray, ...]:
    """右上がりの下値支持線(1000 + slope × 位置)の上を動く株価。touch_days だけ安値が線に触れる。

    ほかの日は、安値が線より25円上を同じ傾きで動く(一直線に上がるので、谷にならない)。
    """
    t = np.arange(N, dtype=float)
    line = 1000 + slope * t
    close = line + 30
    high, low = close + 5, close - 5
    for d in touch_days:
        low[d] = line[d]
        close[d] = line[d] + 5
    return high, low, close, line


def falling_resist(touch_days: tuple[int, ...] = TOUCH_DAYS) -> tuple[np.ndarray, ...]:
    """右下がりの上値抵抗線(1300 − 2 × 位置)の下を動く株価。touch_days だけ高値が線に触れる。"""
    t = np.arange(N, dtype=float)
    line = 1300 - 2 * t
    close = line - 30
    high, low = close + 5, close - 5
    for d in touch_days:
        high[d] = line[d]
        close[d] = line[d] - 5
    return high, low, close, line


def support_line(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, today: int = TODAY
) -> trendline.TrendLine | None:
    return trendline.find_line(trendline.SUPPORT, high, low, close, trendline.pivot_flags(low, True), today)


# =====================================================================
# 線の引き方
# =====================================================================


def test_谷と山() -> None:
    values = np.array([5, 4, 3, 2, 3, 4, 5, 6, 7, 1, np.nan, 8], dtype=float)
    lows = trendline.pivot_flags(values, True)
    assert list(np.flatnonzero(lows)) == [3, 9]
    highs = trendline.pivot_flags(values, False)
    # 前後3日の中で一番高い日(配列の端では、ある分だけで比べる)。空の日は山にしない
    assert list(np.flatnonzero(highs)) == [0, 11]


def test_谷を結んで接触回数が一番多い線を選ぶ() -> None:
    high, low, close, line = rising_support()
    found = support_line(high, low, close)
    assert found is not None
    # 4つの谷はどれも同じ線の上にあるので、どの組み合わせでも接触4回。同じ回数なら新しい谷を使う線
    assert (found.kind, found.start, found.end, found.touches) == (trendline.SUPPORT, 75, 90, 4)
    assert found.slope == pytest.approx(2.0)
    assert found.value_at(TODAY) == pytest.approx(line[TODAY])


def test_続けて接触した日は1回と数える() -> None:
    high, low, close, line = rising_support()
    # 60 の次の日も線の近くにいる(1%上)が、谷ではない。接触は 60〜61 で1回
    low[61] = line[61] * 1.01
    close[61] = line[61] + 5
    found = support_line(high, low, close)
    assert found is not None and found.touches == 4


def test_はみ出しは結んだ2つの谷の間だけで判定する() -> None:
    high, low, close, line = rising_support()
    # 80 の終値が線より5%下(3%以上のはみ出し)。80 をまたぐ 75〜90 の線は使わない。
    # 谷は元の安値で決める(80 の安値を谷にしないため)
    flags = trendline.pivot_flags(low, True)
    close[80] = line[80] * 0.95
    low[80] = close[80] - 5
    found = trendline.find_line(trendline.SUPPORT, high, low, close, flags, TODAY)
    assert found is not None
    # 80 より前の谷を結んだ線は、外さずに「抜けた線」として残す。接触は抜けた日の前の日まで(45、60、75 の3回)
    # (60〜75 の線は 80 で抜けたが、80 は今日の19営業日前で古いので、候補から外れる)
    assert (found.start, found.end, found.touches, found.break_at) == (45, 80, 3, None)
    # はみ出しが3%未満なら、元の線のまま(抜けていない)
    high, low, close, line = rising_support()
    close[80] = line[80] * 0.98
    found = support_line(high, low, close)
    assert found is not None
    assert (found.start, found.end, found.touches, found.break_at) == (75, 90, 4, None)


def test_線を結んだ谷より前のはみ出しは判定しない() -> None:
    high, low, close, line = rising_support()
    # 50 の終値が線より5%下。75〜90 の線の前なので、この線を使う(接触は線を引く期間の中で数えたまま)
    close[50] = line[50] * 0.95
    low[50] = close[50] - 5
    found = support_line(high, low, close)
    assert found is not None
    assert (found.start, found.end, found.touches, found.break_at) == (75, 90, 4, None)


def test_抜けた日() -> None:
    high, low, close, line = rising_support()
    # 2つ目の谷(90)より後の 95 と 97 に、終値が線より5%下。抜けた日は初めて抜けた 95
    close[[95, 97]] = line[[95, 97]] * 0.95
    found = support_line(high, low, close)
    assert found is not None
    assert (found.start, found.end, found.touches, found.break_at) == (75, 90, 4, 95)
    # 抜けの幅を広げる(10%)と、抜けていない
    found = trendline.find_line(
        trendline.SUPPORT, high, low, close, trendline.pivot_flags(low, True), TODAY, break_rate=0.1
    )
    assert found is not None and found.break_at is None
    # 上値抵抗線は、終値が線より3%以上上に出た日
    high, low, close, line = falling_resist()
    close[96] = line[96] * 1.05
    found = trendline.find_line(trendline.RESIST, high, low, close, trendline.pivot_flags(high, False), TODAY)
    assert found is not None and found.break_at == 96


def _find_with_flags(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, flag_days: tuple[int, ...]
) -> trendline.TrendLine | None:
    """谷の位置を flag_days だけにして、下値支持線を引く(谷の判定の影響を受けずに、線の選び方だけを確かめる)。"""
    flags = np.zeros(N, dtype=bool)
    flags[list(flag_days)] = True
    return trendline.find_line(trendline.SUPPORT, high, low, close, flags, TODAY)


def test_直近に抜けた線の境目は備考の抜けの判定と同じ() -> None:
    keep = const.TREND_KEEP_BROKEN_DAYS
    assert keep == const.CROSS_NOTE_DAYS
    # 結んだ谷(75、90)より後に、終値が線より5%下に出た日を抜けた日とする。今日は TODAY(99)
    for broke, kept in ((TODAY, True), (TODAY - keep + 1, True), (TODAY - keep, False), (91, False)):
        high, low, close, line = rising_support()
        close[broke] = line[broke] * 0.95
        found = _find_with_flags(high, low, close, TOUCH_DAYS)
        assert (found is not None and found.break_at == broke) is kept
        # 備考の抜けの判定は、今日まで外側が続いているときだけ。続いている場合は、線が残る日数と同じ境目になる
        close[broke:] = line[broke:] * 0.95
        days_ago = trendline.break_days_ago(
            trendline.TrendLine(trendline.SUPPORT, 75, 90, float(low[75]), 2.0, 4, broke), close, TODAY
        )
        assert (days_ago is not None) is kept


def test_古く抜けた線は接触回数が多くても選ばれず抜けていない線が選ばれる() -> None:
    # 接触4回の線(谷 45、60、75、90)が 80 で抜けた(今日から19営業日前)。ほかに、抜けていない接触の少ない線がある
    high, low, close, line = rising_support()
    close[80] = line[80] * 0.95
    # 古く抜けた線だけの場合(谷 45、60、75、90)は、線を引かない
    assert _find_with_flags(high, low, close, TOUCH_DAYS) is None
    # 抜けていない線: 谷 85 と 95 を結ぶ傾き 2 の線(安値を線の24円下に置く。終値は線より上で、今日の値は終値に近い)
    other = line - 24
    low[[85, 95]] = other[[85, 95]]
    # 古く抜けた線と、抜けていない線の谷を全て渡す: 接触回数は古く抜けた線のほうが多いが、抜けていない線が選ばれる
    found = _find_with_flags(high, low, close, (*TOUCH_DAYS, 85, 95))
    assert found is not None and found.break_at is None
    assert found.touches < 4 or found.break_at is None
    assert (found.start, found.end) != (75, 90)


def test_古く抜けた線しかない場合は最後の手段の線も引かない() -> None:
    # 線全体が 80 で抜けている(今日の19営業日前)。最後の手段で結ぶ点も同じ線の上なので、引かない
    high, low, close, line = rising_support()
    close[80] = line[80] * 0.95
    assert support_line(high, low, close) is None
    # 直近(今日の2営業日前)に抜けた線は、今までどおり選ばれる
    high, low, close, line = rising_support()
    close[TODAY - 2] = line[TODAY - 2] * 0.95
    found = support_line(high, low, close)
    assert found is not None and (found.start, found.end, found.break_at) == (75, 90, TODAY - 2)


def test_最後の手段の線() -> None:
    # 谷が1つしかない。線を引く期間(39〜96)を前半(39〜67)と後半(68〜96)に分け、それぞれの安値が一番低い日を結ぶ
    high, low, close, line = rising_support(touch_days=(60,))
    found = support_line(high, low, close)
    assert found is not None
    assert (found.start, found.end, found.break_at) == (39, 68, None)
    assert found.start_value == pytest.approx(low[39]) and found.slope == pytest.approx(2.0)
    # 2つの谷が10営業日より近い場合も、最後の手段の線を引く
    h2, l2, c2, _ = rising_support(touch_days=(60, 65))
    found = support_line(h2, l2, c2)
    assert found is not None and (found.start, found.end) == (39, 68)
    # 上値抵抗線は、それぞれの高値が一番高い日を結ぶ
    high, low, close, line = falling_resist(touch_days=(60,))
    found = trendline.find_line(trendline.RESIST, high, low, close, trendline.pivot_flags(high, False), TODAY)
    assert found is not None and (found.start, found.end) == (39, 68)


def test_最後の手段の2つの点はTREND_PIVOT_MIN_GAP_DAYS以上離す() -> None:
    gap = const.TREND_PIVOT_MIN_GAP_DAYS
    # 前半の最後(9)と後半の最初(10)が一番安いが、隣り合うので使わない。離れた組のうち値の合計が一番低く、古い組を選ぶ
    points = np.full(20, 100.0)
    points[9] = points[10] = 50.0
    pair = trendline._fallback_points(points, (0, 19), True)
    assert pair == (0, 10) and pair[1] - pair[0] >= gap
    # 高い日を探す場合も同じ
    pair = trendline._fallback_points(200.0 - points, (0, 19), False)
    assert pair == (0, 10)
    # 2つが十分離れていれば、前半と後半それぞれで一番低い日(今までと同じ)
    points = np.full(40, 100.0)
    points[3], points[30] = 10.0, 20.0
    assert trendline._fallback_points(points, (0, 39), True) == (3, 30)
    # 空の日は使わない
    points[3] = np.nan
    pair = trendline._fallback_points(points, (0, 39), True)
    assert pair is not None and pair[0] != 3
    # 前半と後半の間が TREND_PIVOT_MIN_GAP_DAYS に届かない短い期間は、組がないので None(線を引かない)
    assert trendline._fallback_points(np.full(gap, 100.0), (0, gap - 1), True) is None
    # 半分が全て空なら None
    half_nan = np.full(40, 100.0)
    half_nan[:20] = np.nan
    assert trendline._fallback_points(half_nan, (0, 39), True) is None


def far_and_near_support() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """接触6回の水平な線(今日の値 1070)と、接触の少ない右上がりの線(今日の値 約1395)がある株価。今日の終値は 1400。"""
    t = np.arange(N, dtype=float)
    close = np.where(t <= 75, 1100.0, 1100.0 + (t - 75) * 12.5)
    high = close + 5
    low = close - 5
    valleys = (42, 49, 56, 63, 70, 75, 80, 90)
    for d in valleys:
        for k in (-3, -2, -1, 1, 2, 3):
            low[d + k] = close[d + k] + 60  # 谷の前後は高くして、谷を目立たせる
    for d in (42, 49, 56, 63, 70, 75):
        low[d] = 1070.0
    return high, low, close


def test_今の終値から離れた線は選ばない() -> None:
    high, low, close = far_and_near_support()
    found = support_line(high, low, close)
    assert found is not None
    # 水平な線(接触6回)は今日の値 1070 が終値 1400 から15%より離れているので選ばない
    assert found.touches < 6
    assert abs(found.value_at(TODAY) - close[TODAY]) / close[TODAY] <= const.TREND_MAX_DISTANCE_RATE
    # 上限を外せば、接触回数が一番多い水平な線(新しい谷 63 と 75。70 と 75 は10営業日未満)を選ぶ
    original = const.TREND_MAX_DISTANCE_RATE
    try:
        const.TREND_MAX_DISTANCE_RATE = 10.0  # type: ignore[misc]
        far = support_line(high, low, close)
    finally:
        const.TREND_MAX_DISTANCE_RATE = original  # type: ignore[misc]
    assert far is not None and (far.start, far.end, far.touches) == (63, 75, 6)


def test_全部離れている場合は最後の手段の線も引かない() -> None:
    high, low, close, _ = rising_support()
    close[TODAY] = 2000.0  # どの候補も、最後の手段の線(45 と 75)も、今日の値(約1198)が終値から15%より離れる
    assert support_line(high, low, close) is None
    # 今日の終値が空なら、離れ具合の条件をかけない(通常の候補が選ばれる)
    close[TODAY] = np.nan
    found = support_line(high, low, close)
    assert found is not None and (found.start, found.end) == (75, 90)


def test_最後の手段の線が15パーセントより離れていたら引かない() -> None:
    # 谷が1つしかない(通常の候補がない)株価で、最後の手段の線(39 と 68。今日の値 1223)だけを確かめる
    high, low, close, _ = rising_support(touch_days=(60,))
    value = low[39] + 2.0 * (TODAY - 39)
    close[TODAY] = value / (1 - const.TREND_MAX_DISTANCE_RATE)  # ちょうど15%なら引く
    found = support_line(high, low, close)
    assert found is not None and (found.start, found.end) == (39, 68)
    close[TODAY] = value / (1 - const.TREND_MAX_DISTANCE_RATE) * 1.01  # 15%より離れたら引かない
    assert support_line(high, low, close) is None
    # 上値抵抗線も同じ(線の今日の値 1077。終値 900 から約20%離れる)
    high, low, close, _ = falling_resist(touch_days=(60,))
    flags = trendline.pivot_flags(high, False)
    close[TODAY] = 900.0
    assert trendline.find_line(trendline.RESIST, high, low, close, flags, TODAY) is None


def test_ちょうど15パーセントの線は外さない() -> None:
    high, low, close, line = rising_support()
    close[TODAY] = line[TODAY] / (1 - const.TREND_MAX_DISTANCE_RATE)  # |線 − 終値| ÷ 終値 がちょうど15%
    found = support_line(high, low, close)
    assert found is not None and (found.start, found.end, found.touches) == (75, 90, 4)
    # 今日の終値が空なら、離れ具合の条件をかけない
    close[TODAY] = np.nan
    found = support_line(high, low, close)
    assert found is not None and (found.start, found.end) == (75, 90)


def test_データが少ない銘柄は期間を短くして引く() -> None:
    high, low, close, _ = rising_support(touch_days=(10, 20, 30))
    # 今日が 40 なら、線を引く期間は 3〜37(データがある一番古い日から谷・山の判定の3日分を除く)
    assert trendline.line_window(40) == (3, 37)
    found = support_line(high, low, close, today=40)
    assert found is not None
    assert (found.start, found.end, found.touches) == (20, 30, 3)


def test_線を引けない場合() -> None:
    high, low, close, _ = rising_support()
    # 線を引く期間が20営業日未満(3〜21 の19日)なら引かない。20営業日(3〜22)なら引く
    assert trendline.required_rows() == 25
    assert trendline.line_window(24) is None
    assert trendline.line_window(25) == (3, 22)
    assert support_line(high, low, close, today=trendline.required_rows() - 1) is None
    assert support_line(high, low, close, today=trendline.required_rows()) is not None
    # 安値が全て空の半分があり、谷も2つない
    empty = np.full(N, np.nan)
    assert support_line(high, empty, close) is None


def test_線を引く期間の終わりは3営業日前() -> None:
    assert const.TREND_WINDOW_END_DAYS == 3
    assert trendline.line_window(TODAY) == (TODAY - 60, TODAY - 3)


def test_3営業日前の山を線に使う() -> None:
    # 山は 70 と 96(今日の3営業日前)。96 の山は、前後3営業日(93〜99 = 今日)で判定できる
    high, low, close, line = falling_resist(touch_days=(70, TODAY - 3))
    flags = trendline.pivot_flags(high, False)
    assert flags[TODAY - 3]
    found = trendline.find_line(trendline.RESIST, high, low, close, flags, TODAY)
    assert found is not None and (found.start, found.end) == (70, TODAY - 3)
    # 2営業日前の山は、期間の外なので使わない
    high, low, close, line = falling_resist(touch_days=(70, TODAY - 2))
    found = trendline.find_line(trendline.RESIST, high, low, close, trendline.pivot_flags(high, False), TODAY)
    assert found is None or found.end != TODAY - 2


def test_今日より後の株価は使わない() -> None:
    # 今日を 80 とし、81 以降の株価を大きく変えても、線は今日までで切った配列と同じになる
    high, low, close, _ = rising_support(touch_days=(30, 45, 60, 77))
    today = 80
    for kind, values, lowest in ((trendline.SUPPORT, low, True), (trendline.RESIST, high, False)):
        cut = trendline.find_line(
            kind, high[: today + 1], low[: today + 1], close[: today + 1],
            trendline.pivot_flags(values[: today + 1], lowest), today,
        )  # fmt: skip
        h2, l2, c2 = high.copy(), low.copy(), close.copy()
        h2[today + 1 :], l2[today + 1 :], c2[today + 1 :] = 5000.0, 1.0, 3000.0  # 今日より後だけ変える
        v2 = l2 if lowest else h2
        full = trendline.find_line(kind, h2, l2, c2, trendline.pivot_flags(v2, lowest), today)
        assert full == cut
    # 77 の谷(今日の3営業日前)が線に使われ、今日より後の安値が低くても谷のまま
    line = trendline.find_line(trendline.SUPPORT, h2, l2, c2, trendline.pivot_flags(l2, True), today)
    assert line is not None and line.end == 77


def test_線の今日の値が0以下の線() -> None:
    t = np.arange(N, dtype=float)
    close = 1000 + t
    high, low = close + 5, close - 5
    # 50 と 70 の谷を結ぶ線(400円 → 100円)は、今日には0円より下になる(候補からも、最後の手段の線からも外れる)
    low[50], low[70] = 400.0, 100.0
    assert support_line(high, low, close) is None
    # 今日の終値が空の場合だけは離れ具合の条件をかけないので、0円より下になる線も引く(画面のチャートは0円で止める)
    close[TODAY] = np.nan
    found = support_line(high, low, close)
    assert found is not None
    assert (found.start, found.end) == (50, 70)
    assert found.value_at(TODAY) < 0
    # 備考・特徴量は出さない
    assert trendline.break_note(found, close, TODAY) is None
    assert np.isnan(trendline.gap_from_line(found, close, TODAY))


def test_上値抵抗線() -> None:
    high, low, close, line = falling_resist()
    found = trendline.find_line(trendline.RESIST, high, low, close, trendline.pivot_flags(high, False), TODAY)
    assert found is not None
    assert (found.kind, found.touches) == (trendline.RESIST, 4)
    assert found.slope == pytest.approx(-2.0)


def test_線からのかい離() -> None:
    high, low, close, line = rising_support()
    found = support_line(high, low, close)
    assert trendline.gap_from_line(found, close, TODAY) == pytest.approx(close[TODAY] / line[TODAY] - 1)
    assert np.isnan(trendline.gap_from_line(None, close, TODAY))
    # 線の値が0以下なら空
    below = trendline.TrendLine(trendline.SUPPORT, 0, 10, 10.0, -1.0, 3)
    assert np.isnan(trendline.gap_from_line(below, close, TODAY))


# =====================================================================
# 備考
# =====================================================================


def test_下値支持線の下抜け() -> None:
    high, low, close, line = rising_support()
    close[-3:] = line[-3:] * 0.95  # 3日前から線より5%下
    low[-3:] = close[-3:] - 5
    # 3日続けて外側(抜けた日は2営業日前)
    assert trendline.notes_for_series(high, low, close, TODAY) == [
        "下値支持線(接触4回)を下抜け(上昇から転換・2営業日前)"
    ]


def test_上値抵抗線の上抜け() -> None:
    high, low, close, line = falling_resist()
    close[-2:] = line[-2:] * 1.05
    high[-2:] = close[-2:] + 5
    assert trendline.notes_for_series(high, low, close, TODAY) == [
        "上値抵抗線(接触4回)を上抜け(下降から転換・1営業日前)"
    ]


def test_今日抜けた場合は本日() -> None:
    high, low, close, line = rising_support()
    close[-1] = line[-1] * 0.95
    low[-1] = close[-1] - 5
    assert trendline.notes_for_series(high, low, close, TODAY) == ["下値支持線(接触4回)を下抜け(上昇から転換・本日)"]


def test_抜けた日の営業日数() -> None:
    high, low, close, line = rising_support()
    found = support_line(high, low, close)
    assert found is not None
    close[-1] = line[-1] * 0.95
    assert trendline.break_days_ago(found, close, TODAY) == 0
    # 今日の分だけが内側に戻ったら None(だまし)
    close[-4:] = line[-4:] * 0.95
    assert trendline.break_days_ago(found, close, TODAY) == 3
    close[-1] = line[-1]
    assert trendline.break_days_ago(found, close, TODAY) is None
    # 途中で内側に戻って再び抜けたら、一番新しい抜けた日(1営業日前)を使う
    close[-4:] = line[-4:] * 0.95
    close[-3] = line[-3]
    assert trendline.break_days_ago(found, close, TODAY) == 1


def test_起きた日の言葉() -> None:
    assert trendline.days_ago_text(0) == "本日"
    assert trendline.days_ago_text(1) == "1営業日前"
    assert trendline.days_ago_text(4) == "4営業日前"


def test_抜けの判定() -> None:
    high, low, close, line = rising_support()
    found = support_line(high, low, close)
    assert found is not None
    # 今日の終値が内側に戻っていたら出さない
    close[-3:-1] = line[-3:-1] * 0.95
    assert not trendline.broke_recently(found, close, TODAY)
    # 抜けた日が直近5営業日より前(6日前から外側)なら出さない
    close[-6:] = line[-6:] * 0.95
    assert not trendline.broke_recently(found, close, TODAY)
    # ちょうど5日前から外側なら出す
    close[-6] = line[-6] + 5
    assert trendline.broke_recently(found, close, TODAY)
    # 抜けの幅より小さい(2%)なら出さない
    high, low, close, line = rising_support()
    close[-1] = line[-1] * 0.98
    assert not trendline.broke_recently(found, close, TODAY)
    # 幅を指定すると、その幅で判定する
    assert trendline.broke_recently(found, close, TODAY, break_rate=0.01)


def test_接触3回未満の線には出さない() -> None:
    high, low, close, line = rising_support(touch_days=(45, 90))
    close[-1] = line[-1] * 0.95
    found = support_line(high, low, close)
    assert found is not None and found.touches == 2
    assert trendline.notes_for_series(high, low, close, TODAY) == []


def test_トレンドの向きの言葉() -> None:
    def line(kind: str, slope: float) -> trendline.TrendLine:
        return trendline.TrendLine(kind, 0, 50, 1000.0, slope, 3)

    today = 60
    assert trendline.direction_suffix(line(trendline.RESIST, -2), today) == "(下降から転換)"
    assert trendline.direction_suffix(line(trendline.RESIST, 2), today) == "(上昇が加速)"
    assert trendline.direction_suffix(line(trendline.SUPPORT, 2), today) == "(上昇から転換)"
    assert trendline.direction_suffix(line(trendline.SUPPORT, -2), today) == "(下降が加速)"
    # ほぼ水平(傾き ÷ 線の値 が TREND_FLAT_SLOPE 未満)なら付けない
    assert trendline.direction_suffix(line(trendline.SUPPORT, 0.1), today) == ""
    high, low, close, flat = rising_support(slope=0.1)
    close[-1] = flat[-1] * 0.95
    assert trendline.notes_for_series(high, low, close, TODAY) == ["下値支持線(接触4回)を下抜け(本日)"]


# =====================================================================
# 特徴量(features.py)
# =====================================================================


def test_終値の回帰直線() -> None:
    rng = np.random.default_rng(3)
    close = pd.Series(np.concatenate([1000 + np.cumsum(rng.normal(0, 10, 80)), 500 + np.arange(70.0)]))
    by = pd.Series(["A"] * 80 + ["B"] * 70)
    r = features.regression_features(close, by)
    y20 = close.to_numpy()[60:80]
    slope20 = np.polyfit(np.arange(20), y20, 1)[0]
    assert r.loc[79, "trend_slope_20d"] == pytest.approx(slope20 / y20[-1])
    assert r.loc[79, "trend_r2_20d"] == pytest.approx(np.corrcoef(np.arange(20), y20)[0, 1] ** 2)
    y60 = close.to_numpy()[20:80]
    assert r.loc[79, "trend_slope_60d"] == pytest.approx(np.polyfit(np.arange(60), y60, 1)[0] / y60[-1])
    # 一直線に上がる銘柄は、当てはまりが 1。日数がそろわない間と、別の銘柄にまたがる窓は空
    assert r.loc[149, "trend_r2_20d"] == pytest.approx(1.0)
    assert r.loc[149, "trend_slope_20d"] == pytest.approx(1 / 569)
    slope20 = r["trend_slope_20d"].to_numpy()
    assert np.isnan(slope20[18]) and np.isnan(slope20[80 + 18])
    assert np.isnan(r["trend_slope_60d"].to_numpy()[80 + 58])


@pytest.fixture
def sample(conn: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    insert_sample_data(conn, date(2025, 1, 6), date(2025, 12, 26))
    return conn


def test_線の特徴量はwith_trendlineのときだけ計算する(sample: psycopg.Connection[DictRow]) -> None:
    codes = [c for c, _, _ in SAMPLE_CODES[:4]]
    plain = features.build_features(sample, date(2025, 12, 26), date(2025, 12, 1), codes)
    assert plain[features.TRENDLINE_FEATURES].isna().all().all()
    assert plain[["trend_slope_20d", "trend_slope_60d", "trend_r2_20d"]].notna().all().all()
    with_line = features.build_features(sample, date(2025, 12, 26), date(2025, 12, 1), codes, with_trendline=True)
    # 47個と、ほかの特徴量の候補は変わらない
    others = [c for c in with_line.columns if c not in features.TRENDLINE_FEATURES]
    pd.testing.assert_frame_equal(with_line[others], plain[others])
    assert with_line[features.TRENDLINE_FEATURES].notna().any().all()


def test_線の特徴量は1銘柄ずつ計算した値と同じ(sample: psycopg.Connection[DictRow]) -> None:
    code = SAMPLE_CODES[2][0]
    df = features.build_features(sample, date(2025, 12, 26), date(2025, 12, 22), [code], with_trendline=True)
    raw = features._load_prices(sample, date(2025, 1, 1), date(2025, 12, 26), [code])
    prices = raw.sort_values("trade_date").reset_index(drop=True)
    high, low, close = (prices[c].to_numpy(dtype=float) for c in ("high", "low", "close"))
    today = len(prices) - 1
    flags = trendline.pivot_flags(low, True)
    expected = trendline.gap_from_line(
        trendline.find_line(trendline.SUPPORT, high, low, close, flags, today), close, today
    )
    actual = df.loc[df["trade_date"] == pd.Timestamp(date(2025, 12, 26)), "trend_support_gap"].iloc[0]
    assert actual == pytest.approx(expected, nan_ok=True)


def _insert_prices(conn: psycopg.Connection[DictRow], code: str, days: list[date], high: np.ndarray,
                   low: np.ndarray, close: np.ndarray, factors: list[float] | None = None) -> None:  # fmt: skip
    for i, d in enumerate(days):
        conn.execute(
            "INSERT INTO daily_price (trade_date, code, open, high, low, close, volume, turnover, adj_factor,"
            " upper_limit_flag, lower_limit_flag) VALUES (%s, %s, %s, %s, %s, %s, 1000, 200000000, %s, false, false)",
            (d, code, Decimal(str(close[i])), Decimal(str(high[i])), Decimal(str(low[i])), Decimal(str(close[i])),
             Decimal(str(1.0 if factors is None else factors[i]))),
        )  # fmt: skip


def test_DBの株価で土日をはさむ場合は株価の行で数える(conn: psycopg.Connection[DictRow]) -> None:
    # 最後の3営業日が 金(7/24)・月(7/27)・火(7/28)。カレンダーでは4日前だが、2営業日前
    days = business_day.list_business_days(date(2026, 1, 5), date(2026, 7, 28))[-N:]
    assert [d.isoformat() for d in days[-3:]] == ["2026-07-24", "2026-07-27", "2026-07-28"]
    high, low, close, line = rising_support()
    close[-3:] = line[-3:] * 0.95
    low[-3:] = close[-3:] - 5
    _insert_prices(conn, "11110", days, high, low, close)
    notes = features.trendline_notes_on(conn, days[-1], ["11110"])
    assert notes == {"11110": ["下値支持線(接触4回)を下抜け(上昇から転換・2営業日前)"]}


def test_DBの株価からトレンドラインの備考を作る(conn: psycopg.Connection[DictRow]) -> None:
    days = business_day.list_business_days(date(2026, 1, 5), date(2026, 7, 31))[-N:]
    high, low, close, line = rising_support()
    close[-3:] = line[-3:] * 0.95
    low[-3:] = close[-3:] - 5
    # 11110 は、そのままの株価。22220 は、途中(30日目)で1株を2株にした株価(分割の前は2倍の値)
    _insert_prices(conn, "11110", days, high, low, close)
    before = np.arange(N) < 30
    scale = np.where(before, 2.0, 1.0)
    factors = [0.5 if i == 30 else 1.0 for i in range(N)]
    _insert_prices(conn, "22220", days, high * scale, low * scale, close * scale, factors)
    # 株価が足りない(線を引く期間が20営業日未満)
    rows = trendline.required_rows()
    _insert_prices(conn, "33330", days[-rows:], high[-rows:], low[-rows:], close[-rows:])
    notes = features.trendline_notes_on(conn, days[-1], ["11110", "22220", "33330", "44440"])
    expected = ["下値支持線(接触4回)を下抜け(上昇から転換・2営業日前)"]
    assert notes == {"11110": expected, "22220": expected}
    # 指定日の行がない日、銘柄の指定がない場合は何も返さない
    assert features.trendline_notes_on(conn, date(2026, 8, 1), ["11110"]) == {}
    assert features.trendline_notes_on(conn, days[-1], []) == {}
    # 抜けの幅を広げる(10%)と、出なくなる
    assert features.trendline_notes_on(conn, days[-1], ["11110"], break_rate=0.1) == {}


# =====================================================================
# 備考が付く割合を数える道具(tools/count_trend_notes.py)
# =====================================================================


def test_備考が付く割合を数える(conn: psycopg.Connection[DictRow]) -> None:
    days = business_day.list_business_days(date(2026, 1, 5), date(2026, 7, 31))[-N:]
    high, low, close, line = rising_support()
    close[-3:] = line[-3:] * 0.95
    low[-3:] = close[-3:] - 5
    _insert_prices(conn, "11110", days, high, low, close)
    _insert_prices(conn, "22220", days, high, low, line + 30)  # 抜けていない
    for code, rank in (("11110", 1), ("22220", 2)):
        conn.execute(
            "INSERT INTO prediction (code, pred_date, horizon, target_date, up_prob, rel_prob, pred_price, base_price,"
            " model_version, buy_rank) VALUES (%s, %s, %s, %s, 0.6, 0.7, 1050, 1000, '20260725', %s)",
            (code, days[-1], const.MAIN_HORIZON, days[-1], rank),
        )
    assert ct.main(["--from", "2026-07-01", "--to", "2026-07-31", "--touch", "0.02", "--break", "0.03,0.1"]) == 0
    files = list(Path(resolve_path(const.LOG_DIR)).glob("trend_notes_*.csv"))
    assert len(files) == 1
    result = pd.read_csv(files[0], encoding="utf-8-sig")
    assert list(result.columns) == ct.CSV_COLUMNS
    first, second = result.to_dict("records")
    assert (first["break_rate"], first["candidates"], first["support_count"], first["any_rate"]) == (0.03, 2, 1, 0.5)
    assert (first["resist_count"], first["turn_count"], first["accel_count"]) == (0, 1, 0)
    assert (second["break_rate"], second["any_count"]) == (0.1, 0)


def test_道具の引数のチェック() -> None:
    args = ct.parse_args(["--from", "2026-01-01", "--to", "2026-07-22"])
    assert (args.touch, args.break_rates) == ([const.TREND_TOUCH_TOLERANCE], [const.TREND_BREAK_RATE])
    for bad in (
        ["--from", "2026-07-23", "--to", "2026-07-22"],
        ["--from", "2026/01/01", "--to", "2026-07-22"],
        ["--from", "2026-01-01", "--to", "2026-07-22", "--touch", "abc"],
        ["--from", "2026-01-01", "--to", "2026-07-22", "--break", "1.5"],
    ):
        with pytest.raises(SystemExit):
            ct.parse_args(bad)


def test_期間に買い候補がない場合(conn: psycopg.Connection[DictRow]) -> None:
    assert ct.main(["--from", "2026-07-01", "--to", "2026-07-31"]) == 0
    result = pd.read_csv(next(Path(resolve_path(const.LOG_DIR)).glob("trend_notes_*.csv")), encoding="utf-8-sig")
    assert result["candidates"].tolist() == [0] and result["any_rate"].tolist() == [0.0]


def test_DBの失敗は1を返す(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_: object) -> dict[date, list[str]]:
        raise RuntimeError("壊れた")

    monkeypatch.setattr(ct, "load_candidates", broken)
    assert ct.main(["--from", "2026-07-01", "--to", "2026-07-31"]) == 1
