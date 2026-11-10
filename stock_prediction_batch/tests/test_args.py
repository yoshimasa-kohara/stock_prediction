"""common/args.py のテスト。"""

from datetime import date

import pytest

from common import args
from common.args import DateArgs, parse_date_args
from common.errors import ArgError


def test_省略した場合は今日の日付になる(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(args, "today_jst", lambda: date(2026, 7, 8))
    result = parse_date_args([])
    assert result == DateArgs(target_date=date(2026, 7, 8))
    assert result.is_range is False


def test_dateを指定した場合() -> None:
    assert parse_date_args(["--date", "2026-07-08"]).target_date == date(2026, 7, 8)


@pytest.mark.parametrize("value", ["2026/07/08", "20260708", "2026-02-30", "2026-7-8", "abc", ""])
def test_dateの形式が正しくない場合(value: str) -> None:
    with pytest.raises(ArgError, match=r"^日付の形式が正しくありません\(--date\)$"):
        parse_date_args(["--date", value])


def test_fromとtoを指定した場合は期間になり指定日はtoになる() -> None:
    result = parse_date_args(["--from", "2024-07-01", "--to", "2026-07-10"], allow_range=True)
    assert result.date_from == date(2024, 7, 1)
    assert result.date_to == date(2026, 7, 10)
    assert result.target_date == date(2026, 7, 10)
    assert result.is_range is True


def test_fromとtoが同じ日でもよい() -> None:
    result = parse_date_args(["--from", "2026-07-10", "--to", "2026-07-10"], allow_range=True)
    assert result.date_from == result.date_to == date(2026, 7, 10)


def test_fromとtoの形式が正しくない場合() -> None:
    with pytest.raises(ArgError, match=r"^日付の形式が正しくありません\(--from / --to\)$"):
        parse_date_args(["--from", "2024-13-01", "--to", "2026-07-10"], allow_range=True)
    with pytest.raises(ArgError, match=r"^日付の形式が正しくありません\(--from / --to\)$"):
        parse_date_args(["--from", "2024-01-01", "--to", "x"], allow_range=True)


@pytest.mark.parametrize("argv", [["--from", "2024-07-01"], ["--to", "2026-07-10"]])
def test_fromとtoの片方だけは不可(argv: list[str]) -> None:
    with pytest.raises(ArgError, match="^--from と --to は両方を指定してください$"):
        parse_date_args(argv, allow_range=True)


def test_fromがtoより後は不可() -> None:
    with pytest.raises(ArgError, match="^--from は --to 以前の日付を指定してください$"):
        parse_date_args(["--from", "2026-07-11", "--to", "2026-07-10"], allow_range=True)


def test_dateとfromtoは同時に指定できない() -> None:
    with pytest.raises(ArgError, match="^--date と --from / --to は同時に指定できません$"):
        parse_date_args(["--date", "2026-07-08", "--from", "2026-07-01", "--to", "2026-07-10"], allow_range=True)


def test_収集以外ではfromを受け取らない() -> None:
    with pytest.raises(ArgError, match="引数が正しくありません"):
        parse_date_args(["--from", "2026-07-01"])


def test_知らない引数は不可() -> None:
    with pytest.raises(ArgError, match="引数が正しくありません"):
        parse_date_args(["--unknown"])


def test_today_jstは日付を返す() -> None:
    assert isinstance(args.today_jst(), date)
