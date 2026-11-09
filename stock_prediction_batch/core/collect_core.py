"""収集の本体。

J-Quants から株価四本値、上場銘柄一覧、バリュエーション指標、財務情報、決算発表予定日を取得し、DBに登録する。
指定日の分に加えて、直近 COLLECT_BACKFILL_DAYS 日以内に取り逃した営業日があれば、その日の分もあわせて取得する。
株価が0件の日は、何も登録せずに空振りとして記録する。
最後に、日経平均を yfinance で取得して登録する(core/index_collect_core.py。取れなくても失敗にしない)。

collect.py(単独で実行)と daily_batch.py(日次バッチ)の両方から run_collect を呼ぶ。

期間の収集(collect.py --from/--to と range_date_full_process.py)では、RangeCollectState を渡す。
その場合は、取れない日を止めずにとばす。
  - 期間外などでデータがない(0件、210、その日以前の株価をまだ取得できていない日の 403 と、契約期間の 400、
    決算発表予定日の 400): WARNING を出して、その日のそのデータの種類をとばす
  - その日以前の株価を取得できている日の 403(契約期間の 400 も同じ): APIキーか契約の問題として止める
  - ネットワークなどの一時的なエラー(やり直しても取れなかったもの): 収集失敗の一覧のファイルに書いて、
    その日の残りをとばす。COLLECT_MAX_NETWORK_FAIL_DAYS 日続いたら止める
日次バッチ(--date)は、今までどおり、どのエラーでも止める。
"""

import csv
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import DictRow

import const
from common import business_day, db, jquants_client
from common.env import resolve_path
from common.errors import ApiError, BatchError, PlanRangeError, RateLimitError, TransientApiError
from common.logger import BatchLogger, get_logger
from common.run_history import ProcessOutcome, run_with_history
from core.index_collect_core import collect_index

# 処理の名前(日本語)
LABEL: str = const.PROCESS_LABELS[const.PROCESS_COLLECT]


@dataclass(frozen=True)
class CollectResult:
    """収集の結果。

    Attributes:
        fetched_dates: 株価を登録できた日付(指定日と、補完できた取り逃しの日)。古い順
        target_empty: 指定日の株価が0件だった場合は True
        price_count: 登録した株価の件数の合計(実行履歴の処理した件数に使う)
        index_warning: 日経平均を取得できなかった場合の警告の文言。問題がなければ None
    """

    fetched_dates: list[date]
    target_empty: bool
    price_count: int = 0
    index_warning: str | None = None


# =====================================================================
# 値の変換
# =====================================================================


def to_decimal(value: Any) -> Decimal | None:
    """数値(数値または文字列)を Decimal に直す。None・空文字列・数値でない文字列は None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    if not result.is_finite():
        return None
    return result


def to_integer(value: Any) -> Decimal | None:
    """整数のカラム(NUMERIC(n,0))に入れる値。小数は四捨五入する。"""
    d = to_decimal(value)
    if d is None:
        return None
    return d.quantize(Decimal(1))


def to_date(value: Any) -> date | None:
    """YYYY-MM-DD(または YYYYMMDD)の文字列を日付に直す。空は None。"""
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, "%Y%m%d").date()
    return date.fromisoformat(text[:10])


def to_time(value: Any) -> time | None:
    """HH:MM:SS(または HH:MM)の文字列を時刻に直す。空は None。"""
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    return time.fromisoformat(text)


def to_flag(value: Any) -> bool | None:
    """J-Quants のフラグ("1" / "0")を真偽値に直す。値がない場合は None。"""
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip() == "1"


def to_text(value: Any) -> str | None:
    """文字列のカラムに入れる値。空文字列は None。"""
    if value is None:
        return None
    text = str(value).strip()
    return text if text != "" else None


# =====================================================================
# 期間の収集で取れない日の扱い
# =====================================================================

# 収集失敗の一覧の見出し
FAILURE_CSV_HEADER: list[str] = ["日付", "データの種類", "エラーの種類", "メッセージ", "時刻", "取り直すコマンド"]
# ネットワークのエラーが続いた日数に数えるデータの種類(財務情報・決算発表予定日・取引カレンダーは数えない)
_DAY_DATA_TYPES: frozenset[str] = frozenset({const.DATA_TYPE_PRICE, const.DATA_TYPE_MASTER, const.DATA_TYPE_VALUATION})


def failure_file_path(started: datetime) -> Path:
    """収集失敗の一覧のファイルの場所(logs の 収集失敗_YYYYMMDD_HHMM.csv)。started は実行を始めた日時。"""
    return resolve_path(const.LOG_DIR) / const.COLLECT_FAILURE_FILE.format(stamp=started.strftime("%Y%m%d_%H%M"))


def error_kind(e: ApiError) -> str:
    """収集失敗の一覧に書く、エラーの種類。"""
    if isinstance(e, RateLimitError):
        return "429(リクエスト数の上限)"
    if e.status_code is not None:
        return f"{e.status_code}(サーバー側のエラー)"
    return "通信エラー・タイムアウト"


def retry_command(date_from: date, date_to: date) -> str:
    """取り直すコマンド。"""
    return f"python collect.py --from {date_from.isoformat()} --to {date_to.isoformat()}"


class RangeCollectState:
    """期間の収集(collect.py --from/--to、range_date_full_process.py)の状態。

    range_date_full_process.py は1日ずつ run_collect を呼ぶので、1回の実行の間、1つを使い回す。
    """

    def __init__(self, failure_path: Path) -> None:
        """
        Args:
            failure_path: 収集失敗の一覧のファイル。失敗が1件以上あったときだけ作る
        """
        self.failure_path: Path = failure_path
        # 収集失敗の一覧に書いた件数
        self.failure_count: int = 0
        # ネットワークなどのエラーで、株価を取れなかった日(予測・検証もとばす)
        self.failed_price_dates: set[date] = set()
        # ネットワークなどのエラーで取れなかった営業日が続いた日数
        self.network_fail_streak: int = 0
        # DBに株価が1日もないまま、期間外(403、契約期間の 400)としてとばした日
        self.out_of_range_dates: set[date] = set()
        # この実行で取得した取引カレンダーの期間(同じ期間を何度も取得しないため)
        self.calendar_fetched: set[tuple[date, date]] = set()
        # 日経平均を取得できなかった日(警告メールに入れる)
        self.index_warning_dates: list[date] = []
        self._network_failed_today: bool = False

    def add_failure(
        self,
        target: date,
        data_type: str,
        error: ApiError,
        retry_from: date,
        retry_to: date,
        logger: BatchLogger,
    ) -> None:
        """取れなかったデータを、収集失敗の一覧のファイルに1行書く。"""
        if not self.failure_path.exists():
            self.failure_path.parent.mkdir(parents=True, exist_ok=True)
            # Excel で開けるように、BOM 付きの UTF-8 で作る
            with self.failure_path.open("w", encoding="utf-8-sig", newline="") as f:
                csv.writer(f).writerow(FAILURE_CSV_HEADER)
        label = const.DATA_TYPE_LABELS[data_type]
        with self.failure_path.open("a", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow(
                [
                    target.isoformat(),
                    label,
                    error_kind(error),
                    str(error),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    retry_command(retry_from, retry_to),
                ]
            )
        self.failure_count += 1
        if data_type == const.DATA_TYPE_PRICE:
            self.failed_price_dates.add(target)
        if data_type in _DAY_DATA_TYPES:
            self._network_failed_today = True
        logger.warning(
            "%sを取得できませんでした。収集失敗の一覧に書いて、とばします(%s): %s", label, self.failure_path, error
        )

    def begin_day(self) -> None:
        """1日分(株価・銘柄一覧・バリュエーション指標)の取得を始める前に呼ぶ。"""
        self._network_failed_today = False

    def end_day(self, logger: BatchLogger) -> None:
        """1日分の取得が終わったら呼ぶ。ネットワークのエラーが続いた日数を数え、上限に達したら止める。

        Raises:
            BatchError: ネットワークなどのエラーで取れなかった日が COLLECT_MAX_NETWORK_FAIL_DAYS 日続いた場合
        """
        if not self._network_failed_today:
            self.network_fail_streak = 0
            return
        self.network_fail_streak += 1
        if self.network_fail_streak >= const.COLLECT_MAX_NETWORK_FAIL_DAYS:
            raise BatchError(
                LABEL,
                f"ネットワークなどのエラーで取れなかった日が{self.network_fail_streak}日続いたため止めます。"
                f"ネットワークと J-Quants の状態を確かめてから、収集失敗の一覧({self.failure_path})のコマンドで"
                "取り直してください",
            )
        logger.warning("ネットワークなどのエラーで取れなかった日が%d日続いています", self.network_fail_streak)

    def add_out_of_range(self, target: date) -> None:
        """DBに株価が1日もないまま、期間外としてとばした日を数える。

        Raises:
            BatchError: その日数が COLLECT_MAX_OUT_OF_RANGE_DAYS を超えた場合(APIキーが無効な場合など)
        """
        self.out_of_range_dates.add(target)
        if len(self.out_of_range_dates) > const.COLLECT_MAX_OUT_OF_RANGE_DAYS:
            raise BatchError(
                LABEL,
                f"株価を1日も取得できないまま、期間外(403、400)としてとばした日が{const.COLLECT_MAX_OUT_OF_RANGE_DAYS}日を"
                "超えたため止めます。APIキーと契約を確かめてください",
            )


class _SkipDay(Exception):
    """ネットワークなどのエラーで取れなかった(収集失敗の一覧に書き済み)。その日の残りの取得をとばす。"""


def has_price_on_or_before(conn: psycopg.Connection[DictRow], target: date) -> bool:
    """対象の日付以前に、株価を取得済み(FETCHED)の日があれば True。"""
    row = conn.execute(
        "SELECT 1 FROM fetched_date WHERE data_type = %s AND status = %s AND target_date <= %s LIMIT 1",
        (const.DATA_TYPE_PRICE, const.FETCH_STATUS_FETCHED, target),
    ).fetchone()
    return row is not None


def has_any_price(conn: psycopg.Connection[DictRow]) -> bool:
    """株価を取得済み(FETCHED)の日が1日でもあれば True。"""
    row = conn.execute(
        "SELECT 1 FROM fetched_date WHERE data_type = %s AND status = %s LIMIT 1",
        (const.DATA_TYPE_PRICE, const.FETCH_STATUS_FETCHED),
    ).fetchone()
    return row is not None


def fetch_in_range(
    conn: psycopg.Connection[DictRow],
    fetch: Callable[[date], list[dict[str, Any]]],
    target: date,
    data_type: str,
    logger: BatchLogger,
    state: RangeCollectState,
) -> list[dict[str, Any]] | None:
    """期間の収集で、1日分の1種類のデータを取得する。

    Returns:
        取得したデータ。期間外などでデータがない場合は None(WARNING を出す。そのデータの種類をとばす)

    Raises:
        _SkipDay: ネットワークなどのエラーで取れなかった場合(収集失敗の一覧に書く)
        BatchError: その日以前の株価を取得できているのに 403 が返った場合、期間外の日が続いた場合
    """
    label = const.DATA_TYPE_LABELS[data_type]
    try:
        return fetch(target)
    except PlanRangeError as e:
        if data_type == const.DATA_TYPE_EARNINGS_DATE and e.status_code == 400:
            logger.warning("%sを取得できませんでした。期間外とみなして、とばします: %s", label, e)
            return None
        if has_price_on_or_before(conn, target):
            raise BatchError(
                LABEL, f"{e}。この日以前の株価は取得できているので、APIキーと契約を確かめてください"
            ) from e
        logger.warning(
            "%sを取得できませんでした。この日以前の株価をまだ取得できていないため、期間外とみなして、とばします: %s",
            label,
            e,
        )
        if data_type == const.DATA_TYPE_PRICE and not has_any_price(conn):
            state.add_out_of_range(target)
        return None
    except (TransientApiError, RateLimitError) as e:
        state.add_failure(target, data_type, e, target, target, logger)
        raise _SkipDay from e


def _fetch(
    conn: psycopg.Connection[DictRow],
    fetch: Callable[[date], list[dict[str, Any]]],
    target: date,
    data_type: str,
    logger: BatchLogger,
    state: RangeCollectState | None,
) -> list[dict[str, Any]] | None:
    """データを取得する。日次バッチ(state なし)では、エラーはそのまま投げる(止める)。"""
    if state is None:
        return fetch(target)
    return fetch_in_range(conn, fetch, target, data_type, logger, state)


# =====================================================================
# 処理対象の日付
# =====================================================================


def save_calendar(conn: psycopg.Connection[DictRow], rows: list[dict[str, Any]]) -> int:
    """取引カレンダーを登録し、件数を返す。"""
    params = []
    for r in rows:
        division = str(r.get("HolDiv", "")).strip()
        params.append((to_date(r.get("Date")), division, division in ("1", "2")))
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO trading_calendar (cal_date, holiday_division, is_business_day, updated_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (cal_date) DO UPDATE
            SET holiday_division = EXCLUDED.holiday_division,
                is_business_day = EXCLUDED.is_business_day,
                updated_at = now()
            """,
            params,
        )
    return len(params)


def calendar_period(oldest: date, newest: date) -> tuple[date, date]:
    """取引カレンダーを取得する期間。「最も古い日の年の1月1日」〜「最も新しい日の翌年の12月31日」。"""
    return date(oldest.year, 1, 1), date(newest.year + 1, 12, 31)


def collect_calendar(conn: psycopg.Connection[DictRow], oldest: date, newest: date, logger: BatchLogger) -> int:
    """取引カレンダーを取得して登録する。BUSINESS_DAY_SOURCE が CALENDAR の場合だけ呼ぶ。"""
    date_from, date_to = calendar_period(oldest, newest)
    count = save_calendar(conn, jquants_client.fetch_calendar(date_from, date_to))
    logger.info("取引カレンダーを登録しました(%s件、%s 〜 %s)", f"{count:,}", date_from, date_to)
    return count


def _month_end(d: date) -> date:
    """その月の末日。"""
    first_of_next = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return first_of_next - timedelta(days=1)


def _calendar_part(
    conn: psycopg.Connection[DictRow],
    part_from: date,
    part_to: date,
    logger: BatchLogger,
    state: RangeCollectState,
    retry_period: tuple[date, date],
) -> int | None:
    """期間の収集で、取引カレンダーの一部の期間を取得して登録する。データがない場合(期間外など)は None。

    Raises:
        _SkipDay: ネットワークなどのエラーで取れなかった場合(収集失敗の一覧に書く)
        BatchError: 期間の初日以前の株価を取得できているのに 403 が返った場合
    """
    try:
        rows = jquants_client.fetch_calendar(part_from, part_to)
    except PlanRangeError as e:
        first = e.first_date
        if first is not None and part_from < first:
            # 契約の最初の日より前から要求したので、契約の最初の日から取り直す
            if first > part_to:
                logger.debug("取引カレンダーが契約の期間外です(%s 〜 %s): %s", part_from, part_to, e)
                return None
            logger.info(
                "取引カレンダーの開始日を契約の最初の日(%s)にして取り直します(要求は %s から)", first, part_from
            )
            return _calendar_part(conn, first, part_to, logger, state, retry_period)
        if has_price_on_or_before(conn, part_from):
            raise BatchError(
                LABEL, f"{e}。この期間より前の株価は取得できているので、APIキーと契約を確かめてください"
            ) from e
        logger.debug("取引カレンダーを取得できませんでした(%s 〜 %s): %s", part_from, part_to, e)
        return None
    except (TransientApiError, RateLimitError) as e:
        state.add_failure(retry_period[0], const.DATA_TYPE_CALENDAR, e, retry_period[0], retry_period[1], logger)
        raise _SkipDay from e
    if not rows:
        return None
    return save_calendar(conn, rows)


def collect_calendar_in_range(
    conn: psycopg.Connection[DictRow], oldest: date, newest: date, logger: BatchLogger, state: RangeCollectState
) -> int:
    """期間の収集で、取引カレンダーを取得して登録する。

    1回で取れない場合(契約プランの期間外を含むなど)は年ごとに、それでも取れない年は月ごとに取り直す。
    取れなかった月は WARNING を出す。その月の日は、取引カレンダーがない日として、処理対象の日付からとばす。
    """
    date_from, date_to = calendar_period(oldest, newest)
    if (date_from, date_to) in state.calendar_fetched:
        return 0
    retry_period = (oldest, newest)
    try:
        count = _calendar_part(conn, date_from, date_to, logger, state, retry_period)
        if count is None:
            logger.info("取引カレンダーを1回で取得できなかったため、年ごと・月ごとに分けて取り直します")
            count = 0
            for year in range(date_from.year, date_to.year + 1):
                part = _calendar_part(conn, date(year, 1, 1), date(year, 12, 31), logger, state, retry_period)
                if part is not None:
                    count += part
                    continue
                for month in range(1, 13):
                    first = date(year, month, 1)
                    part = _calendar_part(conn, first, _month_end(first), logger, state, retry_period)
                    if part is None:
                        logger.warning(
                            "取引カレンダーを取得できませんでした(%s 〜 %s。契約プランの期間外の可能性があります)",
                            first, _month_end(first),
                        )  # fmt: skip
                    else:
                        count += part
    except _SkipDay:
        # 収集失敗の一覧に書き済み。登録済みの取引カレンダーで続ける(次に呼ばれたときに取り直す)
        return 0
    state.calendar_fetched.add((date_from, date_to))
    logger.info("取引カレンダーを登録しました(%s件、%s 〜 %s)", f"{count:,}", date_from, date_to)
    return count


def known_business_days(
    conn: psycopg.Connection[DictRow], start: date, end: date, logger: BatchLogger | None
) -> list[date]:
    """start〜end の営業日。期間の収集(logger あり)では、取引カレンダーがない日を WARNING を出してとばす。"""
    if logger is None:
        return business_day.list_business_days(start, end, conn)
    days, missing = business_day.list_business_days_known(start, end, conn)
    if missing:
        logger.warning(
            "取引カレンダーがない日をとばします(%s 〜 %s のうち %d日。契約プランの期間外の可能性があります)",
            missing[0], missing[-1], len(missing),
        )  # fmt: skip
    return days


def decide_target_dates(
    conn: psycopg.Connection[DictRow],
    target_date: date,
    date_from: date | None,
    date_to: date | None,
    logger: BatchLogger | None = None,
) -> list[date]:
    """処理対象の日付を、古い順に返す。

    --date の場合: 指定日から COLLECT_BACKFILL_DAYS 日前までの営業日のうち、株価を取得済みでない日と、指定日。
                   指定日は、取得済みかどうか・営業日かどうかにかかわらず必ず含める。
    --from、--to の場合: 期間内の営業日の全て。取得済みの日も取り直す。
    logger を渡した場合(期間の収集)は、取引カレンダーがない日を WARNING を出してとばす。
    """
    if date_from is not None and date_to is not None:
        return known_business_days(conn, date_from, date_to, logger)

    backfill_start = target_date - timedelta(days=const.COLLECT_BACKFILL_DAYS)
    backfill_end = target_date - timedelta(days=1)
    rows = conn.execute(
        """
        SELECT target_date FROM fetched_date
        WHERE data_type = %s AND status = %s AND target_date BETWEEN %s AND %s
        """,
        (const.DATA_TYPE_PRICE, const.FETCH_STATUS_FETCHED, backfill_start, backfill_end),
    ).fetchall()
    fetched = {r["target_date"] for r in rows}
    missing = [d for d in known_business_days(conn, backfill_start, backfill_end, logger) if d not in fetched]
    return sorted({*missing, target_date})


# =====================================================================
# 取得済み日付(fetched_date)
# =====================================================================


def record_fetched(conn: psycopg.Connection[DictRow], target: date, data_type: str, count: int) -> None:
    """取得済み日付を登録する。0件の場合は EMPTY(空振り)にする。

    すでに FETCHED(取得済み)の行は、EMPTY で上書きしない(取得済みのデータを空振りで上書きしないため)。
    """
    status = const.FETCH_STATUS_FETCHED if count > 0 else const.FETCH_STATUS_EMPTY
    conn.execute(
        """
        INSERT INTO fetched_date (target_date, data_type, status, record_count, fetched_at)
        VALUES (%s, %s, %s, %s, now())
        ON CONFLICT (target_date, data_type) DO UPDATE
        SET status = EXCLUDED.status, record_count = EXCLUDED.record_count, fetched_at = now()
        WHERE fetched_date.status <> %s OR EXCLUDED.status = %s
        """,
        (target, data_type, status, count, const.FETCH_STATUS_FETCHED, const.FETCH_STATUS_FETCHED),
    )


def previous_price_count(conn: psycopg.Connection[DictRow], target: date) -> int | None:
    """対象の日付より前で最も新しい、株価を取得済みの日の件数を返す。ない場合(初回)は None。"""
    row = conn.execute(
        """
        SELECT record_count FROM fetched_date
        WHERE data_type = %s AND status = %s AND target_date < %s
        ORDER BY target_date DESC LIMIT 1
        """,
        (const.DATA_TYPE_PRICE, const.FETCH_STATUS_FETCHED, target),
    ).fetchone()
    return None if row is None else int(row["record_count"])


# =====================================================================
# 株価四本値
# =====================================================================


def _price_row(r: dict[str, Any]) -> tuple[Any, ...]:
    """株価四本値のレスポンスの1件を、daily_price の1行に直す。"""
    code = to_text(r.get("Code"))
    try:
        adj_factor = to_decimal(r.get("AdjFactor"))
        return (
            to_date(r.get("Date")),
            code,
            to_decimal(r.get("O")),
            to_decimal(r.get("H")),
            to_decimal(r.get("L")),
            to_decimal(r.get("C")),
            to_integer(r.get("Vo")),
            to_integer(r.get("Va")),
            adj_factor if adj_factor is not None else Decimal(1),
            to_flag(r.get("UL")),
            to_flag(r.get("LL")),
        )
    except (ValueError, TypeError) as e:
        raise BatchError(LABEL, f"株価のデータを変換できませんでした: {e}", code) from e


def save_prices(conn: psycopg.Connection[DictRow], target: date, rows: list[dict[str, Any]]) -> None:
    """株価を登録する(手順6 (1)〜(3))。削除・登録・取得済み日付の登録を1つのトランザクションで行う。"""
    values = [_price_row(r) for r in rows]
    with conn.transaction():
        conn.execute("DELETE FROM daily_price WHERE trade_date = %s", (target,))
        with (
            conn.cursor() as cur,
            cur.copy(
                "COPY daily_price (trade_date, code, open, high, low, close, volume, turnover,"
                " adj_factor, upper_limit_flag, lower_limit_flag) FROM STDIN"
            ) as copy,
        ):
            for v in values:
                copy.write_row(v)
        record_fetched(conn, target, const.DATA_TYPE_PRICE, len(values))


def check_price_count(conn: psycopg.Connection[DictRow], target: date, count: int) -> None:
    """取得件数が、前回の件数の COLLECT_MIN_COUNT_RATIO 以上であることを確かめる(取得したデータに対するチェック)。"""
    previous = previous_price_count(conn, target)
    if previous is not None and count < previous * const.COLLECT_MIN_COUNT_RATIO:
        raise BatchError(LABEL, f"取得件数が少なすぎます(今回 {count:,}件 / 前回 {previous:,}件)")


# =====================================================================
# 上場銘柄一覧
# =====================================================================


def save_master(conn: psycopg.Connection[DictRow], target: date, rows: list[dict[str, Any]]) -> None:
    """銘柄マスタを登録する。登録済みの情報の適用日より古い情報では上書きしない。"""
    params = []
    for r in rows:
        params.append(
            (
                to_text(r.get("Code")),
                to_text(r.get("CoName")) or "",
                to_text(r.get("CoNameEn")),
                to_text(r.get("S17")),
                to_text(r.get("S17Nm")),
                to_text(r.get("S33")),
                to_text(r.get("S33Nm")),
                to_text(r.get("ScaleCat")),
                to_text(r.get("Mkt")),
                to_text(r.get("MktNm")),
                to_date(r.get("Date")) or target,
            )
        )
    with conn.transaction():
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO stock_master (code, name, name_en, sector17_code, sector17_name, sector33_code,
                    sector33_name, scale_category, market_code, market_name, applied_date, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
                ON CONFLICT (code) DO UPDATE
                SET name = EXCLUDED.name, name_en = EXCLUDED.name_en,
                    sector17_code = EXCLUDED.sector17_code, sector17_name = EXCLUDED.sector17_name,
                    sector33_code = EXCLUDED.sector33_code, sector33_name = EXCLUDED.sector33_name,
                    scale_category = EXCLUDED.scale_category,
                    market_code = EXCLUDED.market_code, market_name = EXCLUDED.market_name,
                    applied_date = EXCLUDED.applied_date, updated_at = now()
                WHERE stock_master.applied_date <= EXCLUDED.applied_date
                """,
                params,
            )
        record_fetched(conn, target, const.DATA_TYPE_MASTER, len(params))


# =====================================================================
# バリュエーション指標
# =====================================================================


def save_valuation(conn: psycopg.Connection[DictRow], target: date, rows: list[dict[str, Any]]) -> None:
    """バリュエーション指標を登録する。削除・登録・取得済み日付の登録を1つのトランザクションで行う。"""
    values = [
        (
            to_date(r.get("Date")) or target,
            to_text(r.get("Code")),
            to_decimal(r.get("EPS")),
            to_decimal(r.get("FwdEPS")),
            to_decimal(r.get("BPS")),
            to_decimal(r.get("ROE")),
            to_decimal(r.get("FwdROE")),
            to_decimal(r.get("PER")),
            to_decimal(r.get("FwdPER")),
            to_decimal(r.get("PBR")),
            to_integer(r.get("MktCap")),
        )
        for r in rows
    ]
    with conn.transaction():
        conn.execute("DELETE FROM daily_valuation WHERE trade_date = %s", (target,))
        with (
            conn.cursor() as cur,
            cur.copy(
                "COPY daily_valuation (trade_date, code, eps, fwd_eps, bps, roe, fwd_roe, per, fwd_per, pbr,"
                " market_cap) FROM STDIN"
            ) as copy,
        ):
            for v in values:
                copy.write_row(v)
        record_fetched(conn, target, const.DATA_TYPE_VALUATION, len(values))


# =====================================================================
# 財務情報
# =====================================================================

_FIN_COLUMNS: list[tuple[str, str]] = [
    # (カラム名, J-Quants の項目名)
    ("sales", "Sales"),
    ("op", "OP"),
    ("odp", "OdP"),
    ("np", "NP"),
    ("eps", "EPS"),
    ("bps", "BPS"),
    ("equity_ratio", "EqAR"),
    ("f_sales", "FSales"),
    ("f_op", "FOP"),
    ("f_odp", "FOdP"),
    ("f_np", "FNP"),
    ("f_eps", "FEPS"),
    ("f_div_ann", "FDivAnn"),
]
# 整数(NUMERIC(24,0))のカラム
_FIN_INTEGER_COLUMNS: frozenset[str] = frozenset(
    {"sales", "op", "odp", "np", "f_sales", "f_op", "f_odp", "f_np", "nx_f_sales", "nx_f_op", "nx_f_np"}
)


def check_next_year_fields(rows: list[dict[str, Any]], logger: BatchLogger) -> None:
    """翌事業年度の予想の項目名(const.JQUANTS_FIN_NEXT_YEAR_FIELDS)が、レスポンスにあるかを確かめる。

    項目名は公式ドキュメントで確認できていないため、ない場合は WARNING で
    NxF で始まる実際の項目名をログに出す。
    """
    if not rows:
        return
    keys: set[str] = set()
    for r in rows:
        keys.update(r.keys())
    missing = [name for name in const.JQUANTS_FIN_NEXT_YEAR_FIELDS.values() if name not in keys]
    if missing:
        actual = sorted(k for k in keys if k.startswith("NxF"))
        logger.warning(
            "財務情報に、翌事業年度の予想の項目がありません(const.py の項目名: %s / レスポンスの NxF の項目: %s)",
            ", ".join(missing),
            ", ".join(actual) if actual else "なし",
        )


def _fin_row(r: dict[str, Any]) -> tuple[Any, ...]:
    """財務情報のレスポンスの1件を、fin_summary の1行に直す。"""
    code = to_text(r.get("Code"))
    try:
        values: list[Any] = [
            to_text(r.get("DiscNo")),
            code,
            to_date(r.get("DiscDate")),
            to_time(r.get("DiscTime")),
            to_text(r.get("DocType")) or "",
            to_text(r.get("CurPerType")),
            to_date(r.get("CurPerSt")),
            to_date(r.get("CurPerEn")),
            to_date(r.get("CurFYSt")),
            to_date(r.get("CurFYEn")),
        ]
        for column, field in _FIN_COLUMNS:
            raw = r.get(field)
            values.append(to_integer(raw) if column in _FIN_INTEGER_COLUMNS else to_decimal(raw))
        for column, field in const.JQUANTS_FIN_NEXT_YEAR_FIELDS.items():
            raw = r.get(field)
            values.append(to_integer(raw) if column in _FIN_INTEGER_COLUMNS else to_decimal(raw))
        return tuple(values)
    except (ValueError, TypeError) as e:
        raise BatchError(LABEL, f"財務情報のデータを変換できませんでした: {e}", code) from e


def save_fin_summary(conn: psycopg.Connection[DictRow], disc_date: date, rows: list[dict[str, Any]]) -> None:
    """財務情報を登録する。行は削除しない。開示番号と銘柄コードが同じ行は上書きする。"""
    columns = (
        [
            "disc_no",
            "code",
            "disc_date",
            "disc_time",
            "doc_type",
            "cur_per_type",
            "cur_per_st",
            "cur_per_en",
            "cur_fy_st",
            "cur_fy_en",
        ]
        + [c for c, _ in _FIN_COLUMNS]
        + list(const.JQUANTS_FIN_NEXT_YEAR_FIELDS.keys())
    )
    placeholders = ", ".join(["%s"] * len(columns))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c not in ("disc_no", "code"))
    sql = (
        f"INSERT INTO fin_summary ({', '.join(columns)}, created_at) VALUES ({placeholders}, now()) "
        f"ON CONFLICT (disc_no, code) DO UPDATE SET {updates}"
    )
    params = [_fin_row(r) for r in rows]
    with conn.transaction():
        if params:
            with conn.cursor() as cur:
                cur.executemany(sql, params)
        record_fetched(conn, disc_date, const.DATA_TYPE_FIN_SUMMARY, len(params))


# =====================================================================
# 決算発表予定日
# =====================================================================


def save_earnings_date(conn: psycopg.Connection[DictRow], pub_date: date, rows: list[dict[str, Any]]) -> None:
    """決算発表予定日を登録する。行は削除しない。主キーが同じ行は上書きする。"""
    params = [
        (
            to_text(r.get("Code")),
            to_date(r.get("PubDate")) or pub_date,
            to_text(r.get("FQName")),
            to_text(r.get("FYE")),
            to_date(r.get("SchDate")),
        )
        for r in rows
    ]
    with conn.transaction():
        if params:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO earnings_date (code, pub_date, fq_name, fye, sch_date, created_at)
                    VALUES (%s, %s, %s, %s, %s, now())
                    ON CONFLICT (code, pub_date, fq_name, fye) DO UPDATE SET sch_date = EXCLUDED.sch_date
                    """,
                    params,
                )
        record_fetched(conn, pub_date, const.DATA_TYPE_EARNINGS_DATE, len(params))


# =====================================================================
# 全体の流れ
# =====================================================================


def disclosure_dates(
    conn: psycopg.Connection[DictRow],
    fetched_dates: Iterable[date],
    base_date: date,
    logger: BatchLogger | None = None,
) -> list[date]:
    """財務情報・決算発表予定日を取得する日付を返す。

    株価を登録できた日付と、指定日(--from、--to の場合は --to)の前営業日。古い順。
    logger を渡した場合(期間の収集)は、前営業日を取引カレンダーで判定できなければ、WARNING を出して前営業日を含めない。
    """
    try:
        previous = business_day.business_days_before(base_date, 1, conn)
    except business_day.CalendarMissingError as e:
        if logger is None:
            raise
        logger.warning("前営業日を判定できないため、前営業日の財務情報・決算発表予定日はとばします(%s)", e)
        return sorted(set(fetched_dates))
    return sorted({*fetched_dates, previous})


def collect(
    conn: psycopg.Connection[DictRow],
    target_date: date,
    date_from: date | None,
    date_to: date | None,
    logger: BatchLogger,
    state: RangeCollectState | None = None,
) -> CollectResult:
    """収集の手順2〜11を行う。state を渡した場合は、期間の収集として、取れない日をとばす。"""
    # 期間の収集では、取引カレンダーがない日などを WARNING で知らせる
    range_logger = logger if state is not None else None

    # 手順2: 取引カレンダー(CALENDAR の場合だけ)
    if const.BUSINESS_DAY_SOURCE == const.BUSINESS_DAY_SOURCE_CALENDAR:
        oldest = date_from if date_from is not None else target_date - timedelta(days=const.COLLECT_BACKFILL_DAYS)
        newest = date_to if date_to is not None else target_date
        if state is None:
            collect_calendar(conn, oldest, newest, logger)
        else:
            collect_calendar_in_range(conn, oldest, newest, logger, state)

    # 手順3: 処理対象の日付
    targets = decide_target_dates(conn, target_date, date_from, date_to, range_logger)
    if date_from is not None and date_to is not None and targets:
        # 期間の指定では日付が多いので、最初と最後と日数だけを出す
        logger.info("処理対象の日付: %s 〜 %s(%d日)", targets[0].isoformat(), targets[-1].isoformat(), len(targets))
    else:
        logger.info("処理対象の日付: %s", ", ".join(d.isoformat() for d in targets) if targets else "なし")

    fetched_dates: list[date] = []
    target_empty = False
    price_count = 0
    for d in targets:
        day_logger = logger.with_date(d)
        if state is not None:
            state.begin_day()
        try:
            count = collect_day(conn, d, day_logger, state)
        except _SkipDay:
            # 株価を取れなかった(収集失敗の一覧に書き済み)
            count = None
        if state is not None:
            state.end_day(day_logger)
        if count is None:
            continue
        if count == 0:
            if d == target_date:
                target_empty = True
            continue
        fetched_dates.append(d)
        price_count += count

    # 手順9、手順10: 財務情報と決算発表予定日(株価を登録できた日と、指定日の前営業日)
    for d in disclosure_dates(conn, fetched_dates, target_date, range_logger):
        day_logger = logger.with_date(d)
        try:
            fins = _fetch(conn, jquants_client.fetch_fin_summary, d, const.DATA_TYPE_FIN_SUMMARY, day_logger, state)
            if fins is not None:
                check_next_year_fields(fins, day_logger)
                save_fin_summary(conn, d, fins)
                day_logger.info("財務情報を登録しました(%s件)", f"{len(fins):,}")

            earnings = _fetch(
                conn, jquants_client.fetch_earnings_date, d, const.DATA_TYPE_EARNINGS_DATE, day_logger, state
            )
            if earnings is not None:
                save_earnings_date(conn, d, earnings)
                day_logger.info("決算発表予定日を登録しました(%s件)", f"{len(earnings):,}")
        except _SkipDay:
            # 収集失敗の一覧に書き済み。次の日付に進む
            continue

    # 手順12: 日経平均(表示だけに使う。取れなくても失敗にしない。J-Quants が空振りした日は取らない)
    index_warning = None if target_empty else collect_index(conn, target_date, logger)

    return CollectResult(
        fetched_dates=fetched_dates, target_empty=target_empty, price_count=price_count, index_warning=index_warning
    )


def collect_day(
    conn: psycopg.Connection[DictRow], d: date, logger: BatchLogger, state: RangeCollectState | None
) -> int | None:
    """1日分の株価四本値・上場銘柄一覧・バリュエーション指標を取得して登録する。

    Returns:
        登録した株価の件数。0件(空振り)なら 0。期間外などで株価をとばした場合は None

    Raises:
        _SkipDay: 期間の収集で、ネットワークなどのエラーで株価を取れなかった場合
    """
    # 手順4: 株価四本値を取得する(この時点ではDBを変更しない)
    rows = _fetch(conn, jquants_client.fetch_daily_bars, d, const.DATA_TYPE_PRICE, logger, state)
    if rows is None:
        return None
    logger.debug("株価四本値を取得しました(%s件、%dページ)", f"{len(rows):,}", jquants_client.last_page_count)

    # 手順5: 0件の場合は空振り。期間の収集では、営業日に0件なのは期間外などの可能性があるので WARNING にする
    if not rows:
        with conn.transaction():
            record_fetched(conn, d, const.DATA_TYPE_PRICE, 0)
        if state is None:
            logger.info("株価が0件でした(空振り)")
        else:
            logger.warning("株価が0件でした(空振り)。とばします")
        return 0

    # 手順6: 件数を確かめてから、株価を登録する
    check_price_count(conn, d, len(rows))
    save_prices(conn, d, rows)
    logger.info("株価を登録しました(%s件)", f"{len(rows):,}")

    try:
        # 手順7: 上場銘柄一覧
        master = _fetch(conn, jquants_client.fetch_master, d, const.DATA_TYPE_MASTER, logger, state)
        if master is not None:
            save_master(conn, d, master)
            _log_saved(logger, state, "銘柄マスタ", len(master))

        # 手順8: バリュエーション指標(0件でも失敗にしない)
        valuation = _fetch(conn, jquants_client.fetch_valuation, d, const.DATA_TYPE_VALUATION, logger, state)
        if valuation is not None:
            save_valuation(conn, d, valuation)
            _log_saved(logger, state, "バリュエーション指標", len(valuation))
    except _SkipDay:
        # 収集失敗の一覧に書き済み。株価は登録できたので、この日の件数を返す
        pass
    return len(rows)


def _log_saved(logger: BatchLogger, state: RangeCollectState | None, name: str, count: int) -> None:
    """登録した件数をログに出す。期間の収集で0件の場合は WARNING にする。"""
    if state is not None and count == 0:
        logger.warning("%sが0件でした(期間外などでデータがない可能性があります)", name)
    else:
        logger.info("%sを登録しました(%s件)", name, f"{count:,}")


def run_collect(
    target_date: date,
    date_from: date | None = None,
    date_to: date | None = None,
    batch_type: str = const.BATCH_TYPE_SINGLE,
    state: RangeCollectState | None = None,
) -> CollectResult:
    """収集を実行する。実行履歴とログの開始・終了も行う。

    Args:
        target_date: 指定日(--from、--to の場合は --to の日付)
        date_from: --from の日付
        date_to: --to の日付
        batch_type: 起動のしかた(DAILY、SINGLE)
        state: 期間の収集(collect.py --from/--to、range_date_full_process.py)の状態。
            渡した場合は、取れない日を止めずにとばす。日次バッチは渡さない

    Returns:
        収集の結果

    Raises:
        BatchError: 失敗した場合
    """
    logger = get_logger(LABEL, target_date)

    def body() -> ProcessOutcome[CollectResult]:
        logger.info("処理を開始します")
        with db.connect() as conn:
            result = collect(conn, target_date, date_from, date_to, logger, state)
        if state is not None and result.index_warning is not None:
            state.index_warning_dates.append(target_date)
        logger.info(
            "処理を終了します(取得できた日付: %d件、指定日の空振り: %s)",
            len(result.fetched_dates),
            "あり" if result.target_empty else "なし",
        )
        status = const.RUN_STATUS_SUCCESS if result.fetched_dates else const.RUN_STATUS_EMPTY
        return ProcessOutcome(result=result, status=status, record_count=result.price_count)

    return run_with_history(batch_type, const.PROCESS_COLLECT, target_date, logger, body)
