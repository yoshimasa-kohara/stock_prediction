"""実行ファイルの引数(日付)の解釈。

全ての実行ファイルは --date YYYY-MM-DD を受け取り、指定がなければ現在の日付(日本時間)を使う。
collect.py だけは、期間を指定する --from、--to も受け取る。
range_date_full_process.py は --from、--to、--predict-from を受け取る(parse_range_args)。
"""

import argparse
from dataclasses import dataclass
from datetime import date, datetime
from typing import NoReturn
from zoneinfo import ZoneInfo

from common.errors import ArgError

# 日本時間
JST: ZoneInfo = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class DateArgs:
    """解釈した日付の引数。

    Attributes:
        target_date: 指定日。--from、--to の場合は --to の日付
        date_from: --from の日付。指定がなければ None
        date_to: --to の日付。指定がなければ None
    """

    target_date: date
    date_from: date | None = None
    date_to: date | None = None

    @property
    def is_range(self) -> bool:
        """期間(--from、--to)を指定したかどうか。"""
        return self.date_from is not None and self.date_to is not None


class _ArgParser(argparse.ArgumentParser):
    """エラーのときに終了せず、ArgError を投げる ArgumentParser。

    argparse は標準では終了コード 2 で終わるが、設計では引数エラーは終了コード 1 にするため。
    """

    def error(self, message: str) -> NoReturn:
        raise ArgError(f"引数が正しくありません({message})")


def today_jst() -> date:
    """現在の日付(日本時間)を返す。"""
    return datetime.now(JST).date()


def _parse_ymd(value: str, error_message: str) -> date:
    """YYYY-MM-DD の文字列を日付に直す。形式が違う、または実在しない日付の場合は ArgError。"""
    try:
        if len(value) != 10:
            raise ValueError(value)
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as e:
        raise ArgError(error_message) from e


def parse_date_args(argv: list[str], allow_range: bool = False, prog: str | None = None) -> DateArgs:
    """引数を解釈して、指定日を決める。

    Args:
        argv: 引数のリスト(sys.argv[1:])
        allow_range: True の場合は --from、--to も受け取る(collect.py だけ)
        prog: ヘルプに表示するプログラム名

    Returns:
        解釈した日付

    Raises:
        ArgError: 引数が正しくない場合
    """
    parser = _ArgParser(prog=prog, add_help=False)
    parser.add_argument("--date", dest="date", default=None)
    if allow_range:
        parser.add_argument("--from", dest="date_from", default=None)
        parser.add_argument("--to", dest="date_to", default=None)
    ns = parser.parse_args(argv)

    raw_date: str | None = ns.date
    raw_from: str | None = getattr(ns, "date_from", None)
    raw_to: str | None = getattr(ns, "date_to", None)

    if raw_date is not None and (raw_from is not None or raw_to is not None):
        raise ArgError("--date と --from / --to は同時に指定できません")

    if raw_from is not None or raw_to is not None:
        if raw_from is None or raw_to is None:
            raise ArgError("--from と --to は両方を指定してください")
        date_from = _parse_ymd(raw_from, "日付の形式が正しくありません(--from / --to)")
        date_to = _parse_ymd(raw_to, "日付の形式が正しくありません(--from / --to)")
        if date_from > date_to:
            raise ArgError("--from は --to 以前の日付を指定してください")
        return DateArgs(target_date=date_to, date_from=date_from, date_to=date_to)

    if raw_date is not None:
        return DateArgs(target_date=_parse_ymd(raw_date, "日付の形式が正しくありません(--date)"))

    return DateArgs(target_date=today_jst())


@dataclass(frozen=True)
class RangeArgs:
    """range_date_full_process.py の引数。

    Attributes:
        date_from: 処理する期間の開始日(--from)
        date_to: 処理する期間の終了日(--to)
        predict_from: 学習・予測・検証・通知・バックアップ・クリーニングを始める日(--predict-from)。
                      省略した場合は --from と同じ日
    """

    date_from: date
    date_to: date
    predict_from: date


def parse_range_args(argv: list[str], prog: str | None = None) -> RangeArgs:
    """range_date_full_process.py の引数を解釈する。

    Args:
        argv: 引数のリスト(sys.argv[1:])
        prog: ヘルプに表示するプログラム名

    Returns:
        解釈した日付

    Raises:
        ArgError: 引数が正しくない場合
    """
    parser = _ArgParser(prog=prog, add_help=False)
    parser.add_argument("--from", dest="date_from", default=None)
    parser.add_argument("--to", dest="date_to", default=None)
    parser.add_argument("--predict-from", dest="predict_from", default=None)
    ns = parser.parse_args(argv)

    raw_from: str | None = ns.date_from
    raw_to: str | None = ns.date_to
    raw_predict_from: str | None = ns.predict_from

    if raw_from is None or raw_to is None:
        raise ArgError("--from と --to は両方を指定してください")
    date_from = _parse_ymd(raw_from, "日付の形式が正しくありません(--from / --to)")
    date_to = _parse_ymd(raw_to, "日付の形式が正しくありません(--from / --to)")
    if date_from > date_to:
        raise ArgError("--from は --to 以前の日付を指定してください")

    if raw_predict_from is None:
        return RangeArgs(date_from=date_from, date_to=date_to, predict_from=date_from)
    predict_from = _parse_ymd(raw_predict_from, "日付の形式が正しくありません(--predict-from)")
    if not date_from <= predict_from <= date_to:
        raise ArgError("--predict-from は --from 〜 --to の間の日付を指定してください")
    return RangeArgs(date_from=date_from, date_to=date_to, predict_from=predict_from)
