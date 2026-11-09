"""日経平均(指数日足)の収集。

収集(collect_core.collect)の最後、J-Quants の取得と登録が終わった後に呼ぶ。収集の単独起動でも、日次バッチでも動く。
yfinance(ワイファイナンス)で Yahoo Finance から日経平均(const.INDEX_CODE_N225)の日足を取り、
index_daily_price に登録する。
表示(日経平均の向き。core/market_state_core.py)だけに使い、予測には使わない。

  - 取る範囲: index_daily_price にその指数の行がないとき(最初の1回)は、株価日足の一番古い日付から指定日まで。
              行があるときは、指定日の COLLECT_BACKFILL_DAYS 日(暦日)前から指定日まで。株価日足が空のときは取らない
  - 指定日より後の行と、終値が空の行は登録しない。日付は東京の日付にする。あれば更新、なければ追加
  - 取得の失敗は INDEX_FETCH_RETRY_COUNT 回やり直す。やり直しても取れない場合と、指定日の行がない場合は、
    収集を失敗にせず、WARNING を出して警告の文言を返す(呼び出し元が警告メールに入れる)
"""

import time
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import psycopg
from psycopg.rows import DictRow

import const
from common.logger import BatchLogger

# 警告メールに入れる文言
INDEX_WARNING_MESSAGE: str = "日経平均を取得できなかった(表示だけに使うため、処理は続けました)"

_TOKYO: ZoneInfo = ZoneInfo("Asia/Tokyo")

# 待つ処理(テストで差し替える)
sleep: Callable[[float], None] = time.sleep


def fetch_index_history(ticker: str, start: date, end: date) -> pd.DataFrame:
    """yfinance で指数の日足を取る。end は含まれない(yfinance の仕様)。テストで差し替える。

    Raises:
        Exception: 取得に失敗した場合(yfinance の例外をそのまま投げる)
    """
    # テストでモックにしやすくするため、ここで import する
    import yfinance as yf

    # 失敗を空の表にせず、例外にする
    yf.config.debug.hide_exceptions = False
    df: pd.DataFrame = yf.Ticker(ticker).history(
        start=start.isoformat(), end=end.isoformat(), interval="1d", auto_adjust=False, actions=False
    )
    return df


def fetch_with_retry(ticker: str, start: date, end: date, logger: BatchLogger) -> pd.DataFrame | None:
    """やり直しを含めて取る。やり直しても取れなかった場合は None(WARNING を出す)。"""
    attempts = const.INDEX_FETCH_RETRY_COUNT + 1
    for i in range(1, attempts + 1):
        try:
            return fetch_index_history(ticker, start, end)
        except Exception as e:
            if i >= attempts:
                logger.warning("日経平均を取得できませんでした(%d回試しました): %s", attempts, e)
                return None
            logger.info(
                "日経平均を取得できませんでした。%s秒待ってやり直します(%d/%d回目): %s",
                const.INDEX_FETCH_RETRY_WAIT_SECONDS, i, attempts, e,
            )  # fmt: skip
            sleep(const.INDEX_FETCH_RETRY_WAIT_SECONDS)
    return None  # pragma: no cover


def _to_decimal(value: Any) -> Decimal | None:
    """表の値を Decimal に直す。空(NaN)は None。"""
    if value is None or pd.isna(value):
        return None
    return Decimal(str(round(float(value), 4)))


def _tokyo_date(ts: Any) -> date:
    """表の日時を東京の日付に直す。時刻帯がない場合は、そのままの日付。"""
    stamp = pd.Timestamp(ts)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert(_TOKYO)
    return stamp.date()


def to_rows(df: pd.DataFrame, index_code: str, target_date: date) -> list[tuple[Any, ...]]:
    """取った表を index_daily_price の行に直す。指定日より後の行と、終値が空の行は除く。日付の古い順。"""
    rows: dict[date, tuple[Any, ...]] = {}
    if df is None or df.empty:
        return []
    for ts, r in df.iterrows():
        d = _tokyo_date(ts)
        close = _to_decimal(r.get("Close"))
        if d > target_date or close is None:
            continue
        rows[d] = (
            index_code,
            d,
            _to_decimal(r.get("Open")),
            _to_decimal(r.get("High")),
            _to_decimal(r.get("Low")),
            close,
        )
    return [rows[d] for d in sorted(rows)]


def fetch_range(conn: psycopg.Connection[DictRow], index_code: str, target_date: date) -> tuple[date, date] | None:
    """取る範囲(開始日, 指定日)。株価日足が空のときは None(取らない)。"""
    has_index = conn.execute("SELECT 1 FROM index_daily_price WHERE index_code = %s LIMIT 1", (index_code,)).fetchone()
    if has_index is not None:
        return target_date - timedelta(days=const.COLLECT_BACKFILL_DAYS), target_date
    row = conn.execute("SELECT min(trade_date) AS oldest FROM daily_price").fetchone()
    if row is None or row["oldest"] is None:
        return None
    oldest: date = row["oldest"]
    return min(oldest, target_date), target_date


def save_index_prices(conn: psycopg.Connection[DictRow], rows: list[tuple[Any, ...]]) -> None:
    """指数日足を登録する(あれば更新、なければ追加)。"""
    if not rows:
        return
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO index_daily_price (index_code, trade_date, open, high, low, close, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, now(), now())
            ON CONFLICT (index_code, trade_date) DO UPDATE
            SET open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close,
                updated_at = now()
            """,
            rows,
        )


def collect_index(conn: psycopg.Connection[DictRow], target_date: date, logger: BatchLogger) -> str | None:
    """日経平均を取って登録する。

    Returns:
        警告の文言(やり直しても取れなかった場合と、指定日の行がなかった場合)。問題がなければ None
    """
    index_code = const.INDEX_CODE_N225
    period = fetch_range(conn, index_code, target_date)
    if period is None:
        logger.info("株価日足が空のため、日経平均は取りません")
        return None
    start, end = period
    df = fetch_with_retry(index_code, start, end + timedelta(days=1), logger)
    if df is None:
        return INDEX_WARNING_MESSAGE
    rows = to_rows(df, index_code, target_date)
    save_index_prices(conn, rows)
    logger.info("日経平均を登録しました(%s件、%s 〜 %s)", f"{len(rows):,}", start, end)
    if not rows or rows[-1][1] != target_date:
        logger.warning("日経平均に、指定日(%s)の行がありませんでした", target_date.isoformat())
        return INDEX_WARNING_MESSAGE
    return None
