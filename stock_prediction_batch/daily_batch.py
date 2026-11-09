"""日次バッチ。

収集 → 予測 → 検証 → 通知 を順に呼ぶ。取り逃した日があれば、その日の分も古い順に処理する。
どこかで失敗したら、後の処理を止めて失敗メールを送る。
一部の宛先にメールを送れなかった場合は、最後まで処理してから警告メールを1通送り、終了コード 1 で終わる。
日経平均を取得できなかった場合も警告メールに入れるが、それだけなら終了コードは 0 のままにする(表示だけに使うため)。
タスクスケジューラからは daily_batch.bat 経由で、引数なしで起動する(月〜金曜 19:00)。

使い方:
    python daily_batch.py                    今日(日本時間)の分
    python daily_batch.py --date 2026-07-08  指定日の分

終了コード: 0 = 成功・空振り・二重起動で何もしなかった、1 = 失敗。
"""

import sys
from datetime import date

import const
from common import business_day, db, mailer, run_history
from common.args import parse_date_args, today_jst
from common.batch_runner import BatchWarnings, prepare, send_failure_mail, send_warning_mail, to_batch_error
from common.errors import ArgError, BatchError, MailSendError
from common.logger import get_logger
from core.collect_core import run_collect
from core.notify_core import run_notify
from core.predict_core import run_predict
from core.verify_core import run_verify

LABEL: str = const.PROCESS_LABELS[const.PROCESS_DAILY_BATCH]
# 失敗メールの「今の状況」に使う、処理の順番
STEPS: tuple[str, ...] = tuple(
    const.PROCESS_LABELS[p]
    for p in (const.PROCESS_COLLECT, const.PROCESS_PREDICT, const.PROCESS_VERIFY, const.PROCESS_NOTIFY)
)


def empty_mail_text(target_date: date) -> str:
    """営業日に空振りした場合のメールの本文。"""
    d = target_date.isoformat()
    return (
        f"本日({d})は、株価データを取得できませんでした。\n"
        "予測・検証・通知は実行していません。\n"
        "取得できなかった日のデータは、次回以降の日次バッチで自動的に補完します。\n"
    )


def _is_business_day(target_date: date) -> bool:
    with db.connect() as conn:
        return business_day.is_business_day(target_date, conn)


def run_daily(target_date: date) -> int:
    """日次バッチの手順2〜9を行い、終了コードを返す。"""
    logger = get_logger(LABEL, target_date)
    logger.info("処理を開始します")
    try:
        # 手順2、手順3: 途中で止まった実行履歴の片付けと、二重起動の確認
        if not prepare(logger):
            return 0
        # 手順4: 日次バッチ全体の行
        run_id = run_history.start_run(const.BATCH_TYPE_DAILY, const.PROCESS_DAILY_BATCH, target_date)
    except Exception as e:
        logger.error("日次バッチを始められませんでした: %s", e, exc_info=True)
        send_failure_mail(LABEL, to_batch_error(LABEL, e), target_date, logger, STEPS)
        return 1

    processing_date = target_date
    processed = 0
    warnings = BatchWarnings()
    try:
        # 手順5: 収集
        collected = run_collect(target_date, batch_type=const.BATCH_TYPE_DAILY)
        # 日経平均を取得できなかった場合は、処理を続けて、警告メールに入れる(表示だけに使うため)
        if collected.index_warning is not None:
            warnings.add(collected.index_warning, 1, minor=True)

        # 手順6: 指定日の空振り(営業日の場合だけメールを送る)
        if collected.target_empty:
            try:
                is_business = _is_business_day(target_date)
            except Exception as e:
                raise BatchError(const.PROCESS_LABELS[const.PROCESS_COLLECT], f"営業日を判定できません: {e}") from e
            if is_business:
                subject = f"{const.MAIL_SUBJECT_PREFIX}本日はデータを取得できませんでした {target_date.isoformat()}"
                text = empty_mail_text(target_date)
                try:
                    mailer.send_mail(subject, "<pre>" + text + "</pre>", text, target_date=target_date)
                    logger.info("営業日に株価を取得できなかったため、メールを送りました")
                except MailSendError as e:
                    if not e.partial:
                        raise BatchError(LABEL, f"メールを送れませんでした: {e}") from e
                    warnings.add_partial_mail(subject, e)
                except Exception as e:
                    raise BatchError(LABEL, f"メールを送れませんでした: {e}") from e
            else:
                logger.info("休場日のため、株価はありませんでした")

        # 手順7: 日付ごとに、予測 → 検証 → 通知
        dates = collected.fetched_dates
        logger.info("処理する日付: %s", ", ".join(d.isoformat() for d in dates) if dates else "なし")
        for d in dates:
            processing_date = d
            is_backfill = d != target_date
            logger.info("%s の処理を開始します%s", d.isoformat(), "(補完分)" if is_backfill else "")
            run_predict(d, const.BATCH_TYPE_DAILY)
            run_verify(d, const.BATCH_TYPE_DAILY)
            run_notify(d, is_backfill, const.BATCH_TYPE_DAILY, warnings=warnings)
            processed += 1

        # 手順8: 日次バッチ全体の行を更新する
        status = const.RUN_STATUS_SUCCESS if processed else const.RUN_STATUS_EMPTY
        run_history.finish_run(run_id, status, processed)
        logger.info("処理を終了します(処理した日付: %d日)", processed)
        if warnings:
            send_warning_mail(LABEL, target_date, warnings, "処理は最後まで完了しました", logger)
            # 日経平均を取得できなかっただけの場合は、成功のまま終わる
            return 0 if warnings.only_minor else 1
        return 0
    except Exception as e:
        # 手順9: 後の処理を止めて、失敗メールを送る
        error = to_batch_error(LABEL, e)
        if not isinstance(e, BatchError):
            logger.error("想定外のエラーが発生しました: %s", e, exc_info=True)
        send_failure_mail(LABEL, error, processing_date, logger, STEPS, f"処理を終えた日付: {processed}日")
        # 失敗の前に出た警告も知らせる
        if warnings:
            send_warning_mail(LABEL, target_date, warnings, f"{error.process_name}で停止しました", logger)
        try:
            run_history.finish_run(
                run_id, const.RUN_STATUS_FAILED, processed, error.code, f"{error.process_name}: {error.message}"
            )
        except Exception as e2:
            logger.error("実行履歴を更新できませんでした: %s", e2, exc_info=True)
        logger.info("処理を終了します(失敗)")
        return 1


def main(argv: list[str]) -> int:
    """引数を受け取って日次バッチを実行する。終了コードを返す。"""
    try:
        args = parse_date_args(argv, prog="daily_batch.py")
    except ArgError as e:
        get_logger(LABEL, today_jst()).error(str(e))
        return 1
    return run_daily(args.target_date)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
