"""クリーニングの本体。

決めた期間より古いデータとファイルを削除する。同じ指定日のバックアップが成功している場合だけ実行する。
世代は、指定日以前のものだけを数える(過去の日付を指定したときに、新しいものを消さないため)。
"""

import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import psycopg
from psycopg.rows import DictRow

import const
from common import db
from common.business_day import years_before
from common.env import resolve_path
from common.errors import BatchError
from common.logger import BatchLogger, get_logger
from common.run_history import ProcessOutcome, run_with_history

LABEL: str = const.PROCESS_LABELS[const.PROCESS_CLEANUP]

_DATE_FOLDER = re.compile(r"^\d{8}$")
_LOG_FILE = re.compile(r"^batch_(\d{8})\.log$")
# メールの内容のファイル。通知_yyyy-MM-dd.txt、通知_yyyy-MM-dd_失敗.txt、通知_yyyy-MM-dd_警告.txt
_NOTIFY_FILE = re.compile(r"^通知_(\d{4}-\d{2}-\d{2})(?:_失敗|_警告)?\.txt$")

# 待つ関数(テストで差し替える)
sleep: Callable[[float], None] = time.sleep


@dataclass(frozen=True)
class CleanupResult:
    """クリーニングの結果。"""

    deleted_rows: dict[str, int]
    deleted_models: list[str]
    deleted_backups: list[str]
    deleted_logs: int
    deleted_notify_files: int = 0


def check_backup_succeeded(conn: psycopg.Connection[DictRow], target_date: date) -> None:
    """同じ指定日のバックアップが成功しているかを確かめる。"""
    row = conn.execute(
        "SELECT 1 FROM run_history WHERE process_name = %s AND target_date = %s AND status = %s LIMIT 1",
        (const.PROCESS_BACKUP, target_date, const.RUN_STATUS_SUCCESS),
    ).fetchone()
    if row is None:
        raise BatchError(
            LABEL, f"同じ日付のバックアップが成功していないため、削除しません(指定日: {target_date.isoformat()})"
        )


def delete_old_rows(conn: psycopg.Connection[DictRow], target_date: date) -> dict[str, int]:
    """DBの古いデータを削除する。1つのトランザクションで行う。"""
    price_limit = years_before(target_date, const.RETENTION_PRICE_YEARS)
    prediction_limit = target_date - timedelta(days=const.RETENTION_PREDICTION_DAYS)
    history_limit = target_date - timedelta(days=const.RETENTION_RUN_HISTORY_DAYS)
    counts: dict[str, int] = {}
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM daily_price WHERE trade_date < %s", (price_limit,))
        counts["株価日足"] = cur.rowcount
        # 指数日足(日経平均)も、株価日足と同じ保持期間で消す
        cur.execute("DELETE FROM index_daily_price WHERE trade_date < %s", (price_limit,))
        counts["指数日足"] = cur.rowcount
        cur.execute(
            """
            DELETE FROM prediction p
            WHERE p.pred_date < %s AND p.verify_status IN (%s, %s)
              AND EXISTS (SELECT 1 FROM verify_daily_summary s
                          WHERE s.pred_date = p.pred_date AND s.horizon = p.horizon)
            """,
            (prediction_limit, const.VERIFY_STATUS_VERIFIED, const.VERIFY_STATUS_UNVERIFIABLE),
        )
        counts["予測・検証"] = cur.rowcount
        cur.execute("DELETE FROM run_history WHERE started_at < %s", (history_limit,))
        counts["実行履歴"] = cur.rowcount
        cur.execute("DELETE FROM daily_valuation WHERE trade_date < %s", (price_limit,))
        counts["バリュエーション指標"] = cur.rowcount
    return counts


def _is_gone(folder: Path) -> bool:
    """フォルダが本当に無くなっていれば True を返す。アクセスを拒否された場合は、まだあるものとして扱う。"""
    try:
        folder.stat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _delete_folder(folder: Path, logger: BatchLogger | None) -> None:
    """フォルダを削除する。アクセスが拒否された場合は、少し待ってやり直す(NAS のごみ箱との競合の対策)。

    NAS のごみ箱への移動が終わる前に、同じフォルダを削除しようとすると PermissionError になることがある。
    やり直しの間にフォルダが無くなったら、削除済みとして扱う。

    Raises:
        OSError: やり直しても削除できなかった場合
    """
    retries = const.CLEANUP_DELETE_RETRY_COUNT
    for attempt in range(retries + 1):
        try:
            shutil.rmtree(folder)
            return
        except (PermissionError, FileNotFoundError):
            if _is_gone(folder):
                if logger is not None:
                    logger.info("削除中だったフォルダが無くなったため、削除済みとして扱いました(%s)", folder)
                return
            if attempt >= retries:
                raise
            if logger is not None:
                logger.warning(
                    "フォルダを削除できなかったため、%d秒待ってやり直します(%d回目、%s)",
                    const.CLEANUP_DELETE_RETRY_WAIT_SEC,
                    attempt + 1,
                    folder,
                )
            sleep(const.CLEANUP_DELETE_RETRY_WAIT_SEC)


def delete_old_generations(parent: Path, target_date: date, keep: int, logger: BatchLogger | None = None) -> list[str]:
    """parent の下の日付(YYYYMMDD)のフォルダのうち、指定日以前のものを新しい順に keep 個残し、古いものを削除する。"""
    if not parent.is_dir():
        return []
    stamp = target_date.strftime("%Y%m%d")
    folders = sorted(
        (p for p in parent.iterdir() if p.is_dir() and _DATE_FOLDER.match(p.name) and p.name <= stamp),
        key=lambda p: p.name,
        reverse=True,
    )
    deleted: list[str] = []
    for old in folders[keep:]:
        try:
            _delete_folder(old, logger)
        except OSError as e:
            raise BatchError(LABEL, f"フォルダを削除できませんでした({old}): {e}") from e
        deleted.append(old.name)
    return deleted


def delete_old_logs(target_date: date) -> int:
    """ファイル名の日付が、指定日から RETENTION_LOG_DAYS 日前より前のログファイルを削除する。

    消すのは batch_YYYYMMDD.log だけ。収集失敗の一覧(収集失敗_*.csv)と、全処理の通知の内容(全処理_通知_*.txt)は、
    取り直しや確認に使うので消さない。評価の結果(evaluate_*.csv など)も消さない。
    メールの内容のファイルは LOG_DIR の下の「通知」フォルダにあり、ここでは消さない(delete_old_notify_files が消す)。
    """
    folder = resolve_path(const.LOG_DIR)
    if not folder.is_dir():
        return 0
    limit = (target_date - timedelta(days=const.RETENTION_LOG_DAYS)).strftime("%Y%m%d")
    count = 0
    for path in folder.iterdir():
        m = _LOG_FILE.match(path.name)
        if m and m.group(1) < limit:
            try:
                path.unlink()
            except OSError as e:
                raise BatchError(LABEL, f"ログファイルを削除できませんでした({path}): {e}") from e
            count += 1
    return count


def delete_old_notify_files(target_date: date) -> int:
    """ファイル名の日付が、指定日から RETENTION_NOTIFY_DAYS 日前より前のメールの内容のファイルを削除する。"""
    folder = resolve_path(const.LOG_DIR) / const.NOTIFY_FILE_DIR
    if not folder.is_dir():
        return 0
    limit = (target_date - timedelta(days=const.RETENTION_NOTIFY_DAYS)).isoformat()
    count = 0
    for path in folder.iterdir():
        m = _NOTIFY_FILE.match(path.name)
        if m and m.group(1) < limit:
            try:
                path.unlink()
            except OSError as e:
                raise BatchError(LABEL, f"メールの内容のファイルを削除できませんでした({path}): {e}") from e
            count += 1
    return count


def cleanup(conn: psycopg.Connection[DictRow], target_date: date, logger: BatchLogger) -> CleanupResult:
    """クリーニングの手順2〜7を行う。"""
    check_backup_succeeded(conn, target_date)
    logger.info("同じ日付のバックアップの成功を確認しました")

    rows = delete_old_rows(conn, target_date)
    logger.info("DBの行を削除しました(%s)", "、".join(f"{k} {v:,}件" for k, v in rows.items()))

    models = delete_old_generations(resolve_path(const.MODEL_DIR), target_date, const.MODEL_KEEP_GENERATIONS, logger)
    logger.info("モデルを削除しました(%d世代%s)", len(models), f": {', '.join(models)}" if models else "")

    backups = delete_old_generations(Path(const.BACKUP_DIR), target_date, const.BACKUP_KEEP_GENERATIONS, logger)
    logger.info("バックアップを削除しました(%d世代%s)", len(backups), f": {', '.join(backups)}" if backups else "")

    logs = delete_old_logs(target_date)
    logger.info("ログファイルを削除しました(%d件)", logs)

    notify_files = delete_old_notify_files(target_date)
    logger.info("メールの内容のファイルを削除しました(%d件)", notify_files)
    return CleanupResult(rows, models, backups, logs, notify_files)


def run_cleanup(target_date: date, batch_type: str = const.BATCH_TYPE_SINGLE) -> CleanupResult:
    """クリーニングを実行する。実行履歴とログの開始・終了も行う。

    Raises:
        BatchError: 失敗した場合
    """
    logger = get_logger(LABEL, target_date)

    def body() -> ProcessOutcome[CleanupResult]:
        logger.info("処理を開始します")
        with db.connect() as conn:
            result = cleanup(conn, target_date, logger)
        logger.info("処理を終了します")
        total = sum(result.deleted_rows.values())
        return ProcessOutcome(result=result, status=const.RUN_STATUS_SUCCESS, record_count=total)

    return run_with_history(batch_type, const.PROCESS_CLEANUP, target_date, logger, body)
