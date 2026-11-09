"""営業日の判定、N営業日後・N営業日前の計算。

判定のしかたは const.BUSINESS_DAY_SOURCE で切り替える。
  RULE    : 土日、jpholiday が祝日とする日、12月31日〜1月3日を休場日とする(無料プランの間)
  CALENDAR: trading_calendar(取引カレンダー)の is_business_day で判定する(有料プランに切り替えた後)
CALENDAR の場合は、DBの接続を引数 conn で渡す。
"""

from datetime import date, timedelta

import jpholiday
import psycopg
from psycopg.rows import DictRow

import const

# 年末年始の休場日(月, 日)
_NEW_YEAR_HOLIDAYS: frozenset[tuple[int, int]] = frozenset({(12, 31), (1, 1), (1, 2), (1, 3)})


class CalendarMissingError(Exception):
    """取引カレンダーに、判定する日の行がない。"""

    def __init__(self, target: date) -> None:
        super().__init__(f"取引カレンダーに日付がありません({target.isoformat()})")
        self.target: date = target


def _source() -> str:
    """判定のしかた。テストで切り替えられるよう、呼ぶたびに const を読む。"""
    source = const.BUSINESS_DAY_SOURCE
    if source not in (const.BUSINESS_DAY_SOURCE_RULE, const.BUSINESS_DAY_SOURCE_CALENDAR):
        raise ValueError(f"BUSINESS_DAY_SOURCE の値が正しくありません({source})")
    return source


def _is_business_day_rule(target: date) -> bool:
    """RULE の判定。"""
    if target.weekday() >= 5:
        return False
    if jpholiday.is_holiday(target):
        return False
    return (target.month, target.day) not in _NEW_YEAR_HOLIDAYS


def months_before(base: date, months: int) -> date:
    """base の months か月前の同じ日を返す。その月にその日がない場合は、その月の末日。"""
    total = base.year * 12 + (base.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    for day in range(base.day, 0, -1):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise ValueError(f"日付を計算できません({base}、{months}か月前)")  # pragma: no cover - 1日は必ずある


def years_before(base: date, years: int) -> date:
    """base の years 年前の同じ日を返す。2月29日で前の年にその日がない場合は、2月28日。"""
    return months_before(base, years * 12)


def list_business_days_by_rule(start: date, end: date) -> list[date]:
    """BUSINESS_DAY_SOURCE にかかわらず、RULE で判定した start〜end の営業日を返す。

    取引カレンダーがまだない未来の日(決算発表予定日まで何営業日か、など)を数えるときに使う。
    """
    if start > end:
        return []
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    return [d for d in days if _is_business_day_rule(d)]


def _require_conn(conn: psycopg.Connection[DictRow] | None) -> psycopg.Connection[DictRow]:
    if conn is None:
        raise ValueError("BUSINESS_DAY_SOURCE が CALENDAR の場合は、DBの接続を渡してください")
    return conn


def _calendar_map(conn: psycopg.Connection[DictRow], start: date, end: date) -> dict[date, bool]:
    """取引カレンダーの start〜end の行を読み、日付 → 営業日かどうか の辞書を返す。"""
    rows = conn.execute(
        "SELECT cal_date, is_business_day FROM trading_calendar WHERE cal_date BETWEEN %s AND %s",
        (start, end),
    ).fetchall()
    return {row["cal_date"]: bool(row["is_business_day"]) for row in rows}


def is_business_day(target: date, conn: psycopg.Connection[DictRow] | None = None) -> bool:
    """営業日なら True を返す。

    Raises:
        CalendarMissingError: CALENDAR で、取引カレンダーに日付がない場合(RULE には切り替えない)
    """
    if _source() == const.BUSINESS_DAY_SOURCE_RULE:
        return _is_business_day_rule(target)
    calendar = _calendar_map(_require_conn(conn), target, target)
    if target not in calendar:
        raise CalendarMissingError(target)
    return calendar[target]


def list_business_days(start: date, end: date, conn: psycopg.Connection[DictRow] | None = None) -> list[date]:
    """start〜end の営業日を古い順に並べたリストを返す。start と end を含む。"""
    if start > end:
        return []
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    if _source() == const.BUSINESS_DAY_SOURCE_RULE:
        return [d for d in days if _is_business_day_rule(d)]
    calendar = _calendar_map(_require_conn(conn), start, end)
    result: list[date] = []
    for d in days:
        if d not in calendar:
            raise CalendarMissingError(d)
        if calendar[d]:
            result.append(d)
    return result


def list_business_days_known(
    start: date, end: date, conn: psycopg.Connection[DictRow] | None = None
) -> tuple[list[date], list[date]]:
    """list_business_days と同じ。ただし、取引カレンダーに行がない日はエラーにせず、別のリストで返す。

    期間の収集で、契約プランの期間外などで取引カレンダーを取れなかった日をとばすために使う。
    RULE の場合は、行がない日はない(2つ目は必ず空)。

    Returns:
        (営業日のリスト, 取引カレンダーに行がない日のリスト)。どちらも古い順
    """
    if start > end:
        return [], []
    if _source() == const.BUSINESS_DAY_SOURCE_RULE:
        return list_business_days(start, end), []
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    calendar = _calendar_map(_require_conn(conn), start, end)
    return [d for d in days if calendar.get(d, False)], [d for d in days if d not in calendar]


def _step_business_days(base: date, n: int, direction: int, conn: psycopg.Connection[DictRow] | None) -> date:
    """base から direction(+1 または -1)の向きに、n 営業日目の日付を返す。base 自身は数えない。"""
    if n < 0:
        raise ValueError(f"営業日数には0以上を指定してください({n})")
    current = base
    count = 0
    while count < n:
        current += timedelta(days=direction)
        if is_business_day(current, conn):
            count += 1
    return current


def business_days_after(base: date, n: int, conn: psycopg.Connection[DictRow] | None = None) -> date:
    """base から n 営業日後の日付を返す。base 自身は数えない。"""
    return _step_business_days(base, n, 1, conn)


def business_days_before(base: date, n: int, conn: psycopg.Connection[DictRow] | None = None) -> date:
    """base から n 営業日前の日付を返す。base 自身は数えない。"""
    return _step_business_days(base, n, -1, conn)
