"""週次バッチ。

学習 → バックアップ → クリーニング を順に呼ぶ。どこかで失敗したら、後の処理を止めて失敗メールを送る。
バックアップが失敗した場合は、クリーニングを実行しない。
タスクスケジューラからは weekly_batch.bat 経由で、引数なしで起動する(土曜 19:00)。

使い方:
    python weekly_batch.py                    今日(日本時間)の分
    python weekly_batch.py --date 2026-07-04  指定日の分

終了コード: 0 = 成功・二重起動で何もしなかった、1 = 失敗。
"""

import sys
from datetime import date

import const
from common import run_history
from common.args import parse_date_args, today_jst
from common.batch_runner import prepare, send_failure_mail, to_batch_error
from common.errors import ArgError, BatchError
from common.logger import get_logger
from core.backup_core import run_backup
from core.cleanup_core import run_cleanup
from core.train_core import run_train

LABEL: str = const.PROCESS_LABELS[const.PROCESS_WEEKLY_BATCH]
# 失敗メールの「今の状況」に使う、処理の順番
STEPS: tuple[str, ...] = tuple(
    const.PROCESS_LABELS[p] for p in (const.PROCESS_TRAIN, const.PROCESS_BACKUP, const.PROCESS_CLEANUP)
)


def run_weekly(target_date: date) -> int:
    """週次バッチの手順2〜9を行い、終了コードを返す。"""
    logger = get_logger(LABEL, target_date)
    logger.info("処理を開始します")
    try:
        if not prepare(logger):
            return 0
        run_id = run_history.start_run(const.BATCH_TYPE_WEEKLY, const.PROCESS_WEEKLY_BATCH, target_date)
    except Exception as e:
        logger.error("週次バッチを始められませんでした: %s", e, exc_info=True)
        send_failure_mail(LABEL, to_batch_error(LABEL, e), target_date, logger, STEPS)
        return 1

    try:
        run_train(target_date, const.BATCH_TYPE_WEEKLY)
        run_backup(target_date, const.BATCH_TYPE_WEEKLY)
        # バックアップが成功した場合だけ、ここに来る
        run_cleanup(target_date, const.BATCH_TYPE_WEEKLY)
        run_history.finish_run(run_id, const.RUN_STATUS_SUCCESS)
        logger.info("処理を終了します")
        return 0
    except Exception as e:
        error = to_batch_error(LABEL, e)
        if not isinstance(e, BatchError):
            logger.error("想定外のエラーが発生しました: %s", e, exc_info=True)
        send_failure_mail(LABEL, error, target_date, logger, STEPS)
        try:
            run_history.finish_run(
                run_id, const.RUN_STATUS_FAILED, None, error.code, f"{error.process_name}: {error.message}"
            )
        except Exception as e2:
            logger.error("実行履歴を更新できませんでした: %s", e2, exc_info=True)
        logger.info("処理を終了します(失敗)")
        return 1


def main(argv: list[str]) -> int:
    """引数を受け取って週次バッチを実行する。終了コードを返す。"""
    try:
        args = parse_date_args(argv, prog="weekly_batch.py")
    except ArgError as e:
        get_logger(LABEL, today_jst()).error(str(e))
        return 1
    return run_weekly(args.target_date)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
