"""実行履歴の登録と更新。

実行履歴は、処理本体とは別のトランザクションで書く。処理本体がロールバックしても、失敗の記録は残す。
そのため、ここの関数はそれぞれ自分でDBに接続する(自動コミット)。
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

import const
from common import db
from common.errors import BatchError
from common.logger import BatchLogger

# 途中で止まった行を失敗に更新するときのエラーの内容
STALE_MESSAGE: str = "途中で止まった実行履歴とみなして、失敗に更新しました"


def start_run(batch_type: str, process_name: str, target_date: date) -> int:
    """RUNNING(実行中)の行を登録し、実行IDを返す。"""
    with db.connect() as conn:
        row = conn.execute(
            """
            INSERT INTO run_history (batch_type, process_name, target_date, status)
            VALUES (%s, %s, %s, %s)
            RETURNING run_id
            """,
            (batch_type, process_name, target_date, const.RUN_STATUS_RUNNING),
        ).fetchone()
    if row is None:  # pragma: no cover - INSERT ... RETURNING は必ず1行返す
        raise RuntimeError("実行履歴を登録できませんでした")
    return int(row["run_id"])


def finish_run(
    run_id: int,
    status: str,
    record_count: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """行を SUCCESS・EMPTY・FAILED のどれかに更新する。"""
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE run_history
            SET status = %s, finished_at = now(), record_count = %s, error_code = %s, error_message = %s
            WHERE run_id = %s
            """,
            (status, record_count, error_code, error_message, run_id),
        )


def mark_stale_runs() -> int:
    """開始から RUNNING_STALE_HOURS を過ぎても RUNNING のままの行を、FAILED に更新する。"""
    with db.connect() as conn:
        cur = conn.execute(
            """
            UPDATE run_history
            SET status = %s, finished_at = now(), error_message = %s
            WHERE status = %s AND started_at < now() - make_interval(hours => %s)
            """,
            (const.RUN_STATUS_FAILED, STALE_MESSAGE, const.RUN_STATUS_RUNNING, const.RUNNING_STALE_HOURS),
        )
        return cur.rowcount


def find_running() -> list[dict[str, Any]]:
    """RUNNING(実行中)の行を返す。二重起動の確認に使う。"""
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT run_id, batch_type, process_name, target_date, started_at
            FROM run_history WHERE status = %s ORDER BY started_at
            """,
            (const.RUN_STATUS_RUNNING,),
        ).fetchall()
    return [dict(r) for r in rows]


@dataclass(frozen=True)
class ProcessOutcome[T]:
    """処理本体の結果。

    Attributes:
        result: 呼び出し元に返す値
        status: SUCCESS または EMPTY
        record_count: 処理した件数
    """

    result: T
    status: str
    record_count: int | None = None


def run_with_history[T](
    batch_type: str,
    process_name: str,
    target_date: date,
    logger: BatchLogger,
    body: Callable[[], ProcessOutcome[T]],
) -> T:
    """実行履歴の登録・更新を行いながら、処理本体を実行する。

    失敗した場合は、ログにエラーの詳細(処理の名前、指定日、銘柄コード、スタックトレース)を出し、
    実行履歴を FAILED にして、BatchError を投げる。

    Args:
        batch_type: 起動のしかた(DAILY、WEEKLY、SINGLE)
        process_name: 処理の名前(COLLECT など)
        target_date: 指定日
        logger: ログを出力するオブジェクト
        body: 処理本体
    """
    label = const.PROCESS_LABELS[process_name]
    try:
        run_id = start_run(batch_type, process_name, target_date)
    except Exception as e:
        # DBに接続できないなど。実行履歴は残せないので、ログにだけ残す
        logger.error("実行履歴を登録できませんでした: %s", e, exc_info=True)
        raise BatchError(label, f"実行履歴を登録できませんでした: {e}") from e
    try:
        outcome = body()
    except BatchError as e:
        logger.error("処理に失敗しました: %s", e.message, exc_info=True, extra={"code": e.code})
        finish_run(run_id, const.RUN_STATUS_FAILED, None, e.code, e.message)
        raise
    except Exception as e:
        logger.error("処理に失敗しました: %s", e, exc_info=True)
        finish_run(run_id, const.RUN_STATUS_FAILED, None, None, str(e))
        raise BatchError(label, str(e)) from e
    finish_run(run_id, outcome.status, outcome.record_count)
    return outcome.result
