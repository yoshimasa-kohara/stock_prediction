"""common/business_day.py のテスト。"""

from datetime import date

import psycopg
import pytest
from psycopg.rows import DictRow

import const
from common import business_day as bd
from common.business_day import CalendarMissingError

# ---------------------------------------------------------------------
# RULE(jpholiday と年末年始)
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (date(2026, 7, 8), True),  # 水曜
        (date(2026, 7, 11), False),  # 土曜
        (date(2026, 7, 12), False),  # 日曜
        (date(2026, 7, 20), False),  # 海の日(月曜の祝日)
        (date(2026, 5, 6), False),  # 振替休日
        (date(2026, 12, 30), True),  # 大納会
        (date(2026, 12, 31), False),  # 年末
        (date(2027, 1, 1), False),  # 元日
        (date(2027, 1, 4), True),  # 大発会(月曜)
        (date(2028, 1, 3), False),  # 年始(月曜でも休場)
    ],
)
def test_RULEの判定(target: date, expected: bool) -> None:
    assert bd.is_business_day(target) is expected


def test_営業日の一覧は月をまたいでも古い順() -> None:
    days = bd.list_business_days(date(2026, 7, 29), date(2026, 8, 4))
    assert days == [date(2026, 7, 29), date(2026, 7, 30), date(2026, 7, 31), date(2026, 8, 3), date(2026, 8, 4)]


def test_営業日の一覧で開始日が終了日より後なら空() -> None:
    assert bd.list_business_days(date(2026, 7, 10), date(2026, 7, 9)) == []


def test_N営業日後は祝日と週末を飛ばす() -> None:
    # 2026-07-17(金) の1営業日後は、土日と海の日(07-20)を飛ばして 07-21(火)
    assert bd.business_days_after(date(2026, 7, 17), 1) == date(2026, 7, 21)
    assert bd.business_days_after(date(2026, 7, 8), 5) == date(2026, 7, 15)


def test_N営業日後は年末年始をまたぐ() -> None:
    assert bd.business_days_after(date(2026, 12, 30), 1) == date(2027, 1, 4)


def test_N営業日前() -> None:
    assert bd.business_days_before(date(2026, 7, 21), 1) == date(2026, 7, 17)
    assert bd.business_days_before(date(2027, 1, 4), 1) == date(2026, 12, 30)


def test_0営業日後は基準の日のまま() -> None:
    assert bd.business_days_after(date(2026, 7, 11), 0) == date(2026, 7, 11)


def test_営業日数がマイナスはエラー() -> None:
    with pytest.raises(ValueError):
        bd.business_days_after(date(2026, 7, 8), -1)


def test_判定のしかたが正しくない場合はエラー(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(const, "BUSINESS_DAY_SOURCE", "OTHER")
    with pytest.raises(ValueError, match="BUSINESS_DAY_SOURCE"):
        bd.is_business_day(date(2026, 7, 8))


# ---------------------------------------------------------------------
# CALENDAR(取引カレンダー)
# ---------------------------------------------------------------------


def _insert_calendar(conn: psycopg.Connection[DictRow], rows: list[tuple[date, str]]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO trading_calendar (cal_date, holiday_division, is_business_day) VALUES (%s, %s, %s)",
            [(d, div, div in ("1", "2")) for d, div in rows],
        )


@pytest.fixture
def calendar(conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> psycopg.Connection[DictRow]:
    monkeypatch.setattr(const, "BUSINESS_DAY_SOURCE", const.BUSINESS_DAY_SOURCE_CALENDAR)
    # 07-08(水) 営業日、07-09(木) 臨時休場とする、07-10(金) 半日立会、07-11〜12 休み、07-13(月) 営業日
    _insert_calendar(
        conn,
        [
            (date(2026, 7, 8), "1"),
            (date(2026, 7, 9), "0"),
            (date(2026, 7, 10), "2"),
            (date(2026, 7, 11), "0"),
            (date(2026, 7, 12), "0"),
            (date(2026, 7, 13), "1"),
        ],
    )
    return conn


def test_CALENDARの判定(calendar: psycopg.Connection[DictRow]) -> None:
    assert bd.is_business_day(date(2026, 7, 8), calendar) is True
    assert bd.is_business_day(date(2026, 7, 9), calendar) is False
    assert bd.is_business_day(date(2026, 7, 10), calendar) is True


def test_CALENDARの営業日の一覧(calendar: psycopg.Connection[DictRow]) -> None:
    assert bd.list_business_days(date(2026, 7, 8), date(2026, 7, 13), calendar) == [
        date(2026, 7, 8),
        date(2026, 7, 10),
        date(2026, 7, 13),
    ]


def test_CALENDARで行がない日を分けて返す(calendar: psycopg.Connection[DictRow]) -> None:
    days, missing = bd.list_business_days_known(date(2026, 7, 6), date(2026, 7, 14), calendar)
    assert days == [date(2026, 7, 8), date(2026, 7, 10), date(2026, 7, 13)]
    assert missing == [date(2026, 7, 6), date(2026, 7, 7), date(2026, 7, 14)]
    assert bd.list_business_days_known(date(2026, 7, 14), date(2026, 7, 13), calendar) == ([], [])


def test_RULEでは行がない日はない() -> None:
    days, missing = bd.list_business_days_known(date(2026, 7, 6), date(2026, 7, 12))
    assert days == bd.list_business_days(date(2026, 7, 6), date(2026, 7, 12))
    assert missing == []


def test_CALENDARのN営業日後(calendar: psycopg.Connection[DictRow]) -> None:
    assert bd.business_days_after(date(2026, 7, 8), 2, calendar) == date(2026, 7, 13)
    assert bd.business_days_before(date(2026, 7, 13), 1, calendar) == date(2026, 7, 10)


def test_CALENDARで日付がない場合はエラー(calendar: psycopg.Connection[DictRow]) -> None:
    with pytest.raises(CalendarMissingError, match=r"^取引カレンダーに日付がありません\(2026-07-14\)$"):
        bd.is_business_day(date(2026, 7, 14), calendar)
    with pytest.raises(CalendarMissingError):
        bd.list_business_days(date(2026, 7, 13), date(2026, 7, 14), calendar)
    with pytest.raises(CalendarMissingError):
        bd.business_days_after(date(2026, 7, 13), 1, calendar)


def test_CALENDARで接続がない場合はエラー(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(const, "BUSINESS_DAY_SOURCE", const.BUSINESS_DAY_SOURCE_CALENDAR)
    with pytest.raises(ValueError, match="DBの接続"):
        bd.is_business_day(date(2026, 7, 8))
