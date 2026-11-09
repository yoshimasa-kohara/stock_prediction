"""期間を指定して、全ての処理を日付順にやり直す実行ファイル。

PCの故障などで、全ての処理をやり直すときに使う。
--from 〜 --to の日付を1日ずつ進め、曜日に応じて、タスクスケジューラと同じ処理を呼ぶ。

    月〜金曜: 収集 → 予測 → 検証 → 通知(株価を取得済みの日だけ。休場日は収集だけ)
    土曜:     学習 → バックアップ → クリーニング
    日曜:     何もしない

- 日次バッチ・週次バッチ(run_daily、run_weekly)は呼ばず、7つの処理をこのファイルの中で1つずつ呼ぶ。
  収集を行わない場合は、process_weekday の run_collect の行をコメントアウトする。
  予測する日は、収集の結果ではなく、DBの fetched_date(取得済み日付)で決めるので、収集を止めても動く。
- 通知のメールは送らず、ログに残すだけにする。失敗したときは失敗メールを、収集失敗の一覧に書いた日があれば
  警告メールを、管理者(.env の ADMIN_MAIL_TO)に送る。
  あわせて、メールのテキスト形式の本文を logs の 全処理_通知_<--from>_<--to>.txt に日付順に追記する。
- 収集は、取れない日をとばして進む(collect.py --from/--to と同じ動き)。
  ネットワークなどのエラーで取れなかった日は logs の 収集失敗_YYYYMMDD_HHMM.csv に書き、株価が取れなかった日は
  予測・検証・通知もとばす。収集失敗の一覧に書いた日があれば、最後まで処理してから終了コード 1 で終わる。
- --predict-from より前の日は、収集だけを行う(データが少ない時期は、学習に失敗するため)。
- 最初のモデルは、--predict-from 以降で最初の土曜の学習で作る。
  それまでの平日(使えるモデルがない日)は、収集だけを行い、予測・検証・通知は行わない。
- 学習は、最初の学習の土曜から数えて const.RANGE_TRAIN_INTERVAL_WEEKS 週ごとの土曜にだけ行う。
  学習しない土曜も、バックアップとクリーニングは行う。予測は、その日以前で最も新しいモデルを使う。
- 1日でも失敗したら(収集でとばした日を除く)、その日で止める。直してから、--from をその日にして、もう一度実行する。
- 実行履歴は、中で呼ぶ各処理が書く(起動のしかたは、平日の処理は DAILY、土曜の処理は WEEKLY)。
  このファイル自身は実行履歴を書かない(二重起動の確認で、自分自身を止めないため)。
- 個別に実行するので、終わってもPCはシャットダウンしない。

使い方(2026-11-02 に実行する場合の例。--from は10年と1か月前、予測は約2年後から約8年分):
    python range_date_full_process.py --from 2016-10-03 --to 2026-10-30 --predict-from 2018-10-01

終了コード: 0 = 成功、1 = 失敗(引数エラー、二重起動、収集失敗の一覧に書いた日がある場合を含む)。
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.rows import DictRow

import const
from common import db
from common.args import parse_range_args, today_jst
from common.batch_runner import BatchWarnings, prepare, send_failure_mail, send_warning_mail, to_batch_error
from common.env import resolve_path
from common.errors import ArgError, BatchError
from common.logger import BatchLogger, get_logger
from core.backup_core import run_backup
from core.cleanup_core import run_cleanup
from core.collect_core import RangeCollectState, failure_file_path, run_collect
from core.index_collect_core import INDEX_WARNING_MESSAGE
from core.notify_core import run_notify
from core.predict_core import run_predict
from core.train_core import run_train
from core.verify_core import run_verify

LABEL: str = const.RANGE_FULL_PROCESS_LABEL

# 曜日(date.weekday() の値)
_SATURDAY: int = 5
_SUNDAY: int = 6

# 失敗メールの「今の状況」に使う、処理の順番
_WEEKDAY_STEPS: tuple[str, ...] = tuple(
    const.PROCESS_LABELS[p]
    for p in (const.PROCESS_COLLECT, const.PROCESS_PREDICT, const.PROCESS_VERIFY, const.PROCESS_NOTIFY)
)
_SATURDAY_STEPS: tuple[str, ...] = tuple(
    const.PROCESS_LABELS[p] for p in (const.PROCESS_TRAIN, const.PROCESS_BACKUP, const.PROCESS_CLEANUP)
)


def is_price_fetched(conn: psycopg.Connection[DictRow], target: date) -> bool:
    """その日の株価を取得済み(FETCHED)なら True を返す。休場日や、取得できなかった日は False。"""
    row = conn.execute(
        "SELECT 1 FROM fetched_date WHERE target_date = %s AND data_type = %s AND status = %s",
        (target, const.DATA_TYPE_PRICE, const.FETCH_STATUS_FETCHED),
    ).fetchone()
    return row is not None


def has_model(conn: psycopg.Connection[DictRow], target: date) -> bool:
    """その日の予測に使えるモデル(学習の基準日がその日以前)があれば True を返す(予測と同じ条件)。"""
    row = conn.execute("SELECT 1 FROM model WHERE base_date <= %s LIMIT 1", (target,)).fetchone()
    return row is not None


def notify_file_path(date_from: date, date_to: date) -> Path:
    """通知の内容を追記するファイルの場所(logs の 全処理_通知_<--from>_<--to>.txt)。"""
    name = const.RANGE_NOTIFY_FILE.format(date_from=date_from.isoformat(), date_to=date_to.isoformat())
    return resolve_path(const.LOG_DIR) / name


def process_weekday(
    d: date, predict_from: date, logger: BatchLogger, collect_state: RangeCollectState, notify_path: Path
) -> None:
    """月〜金曜の処理(日次バッチと同じ処理)を行う。

    Args:
        d: 処理する日
        predict_from: --predict-from の日付
        logger: ログを出力するオブジェクト
        collect_state: 期間の収集の状態(取れなかった日の一覧など。実行の間、使い回す)
        notify_path: 通知の内容を追記するファイル

    Raises:
        BatchError: いずれかの処理が失敗した場合
    """
    # 収集。収集を行わない場合は、次の1行をコメントアウトする
    run_collect(d, batch_type=const.BATCH_TYPE_DAILY, state=collect_state)

    if d < predict_from:
        logger.info("--predict-from より前の日のため、収集だけを行いました")
        return

    with db.connect() as conn:
        fetched = is_price_fetched(conn, d)
        model_ready = has_model(conn, d)
    if not fetched:
        if d in collect_state.failed_price_dates:
            logger.warning("株価を取得できなかったため、予測・検証・通知は行いません(収集失敗の一覧に書きました)")
        else:
            logger.info("株価がない日(休場日など)のため、予測・検証・通知は行いません")
        return
    # 最初のモデルは、--predict-from 以降で最初の土曜に作る。それまでは予測できない
    if not model_ready:
        logger.info("使えるモデルがまだないため、予測・検証・通知は行いません(最初のモデルは土曜の学習で作ります)")
        return

    run_predict(d, const.BATCH_TYPE_DAILY)
    run_verify(d, const.BATCH_TYPE_DAILY)
    run_notify(d, False, const.BATCH_TYPE_DAILY, send_mail=False, text_path=notify_path)


def first_train_date(predict_from: date) -> date:
    """最初の学習の日(--predict-from 以降で最初の土曜)を返す。"""
    return predict_from + timedelta(days=(_SATURDAY - predict_from.weekday()) % 7)


def is_train_day(d: date, predict_from: date) -> bool:
    """その土曜に学習するなら True を返す。

    最初の学習の土曜から数えて、RANGE_TRAIN_INTERVAL_WEEKS 週ごとの土曜に学習する。
    --predict-from で数え始めるので、--from を変えてやり直しても、学習する土曜は変わらない。
    """
    weeks = (d - first_train_date(predict_from)).days // 7
    return weeks % const.RANGE_TRAIN_INTERVAL_WEEKS == 0


def process_saturday(d: date, predict_from: date, logger: BatchLogger) -> None:
    """土曜の処理(週次バッチと同じ処理)を行う。

    Raises:
        BatchError: いずれかの処理が失敗した場合
    """
    if d < predict_from:
        logger.info("--predict-from より前の日のため、学習・バックアップ・クリーニングは行いません")
        return
    if is_train_day(d, predict_from):
        run_train(d, const.BATCH_TYPE_WEEKLY)
    else:
        logger.info(
            "学習は%d週ごとのため、この土曜は学習しません(バックアップ・クリーニングだけ行います)",
            const.RANGE_TRAIN_INTERVAL_WEEKS,
        )
    run_backup(d, const.BATCH_TYPE_WEEKLY)
    run_cleanup(d, const.BATCH_TYPE_WEEKLY)


def _collect_warnings(collect_state: RangeCollectState) -> BatchWarnings:
    """収集失敗の一覧に書いたデータがあれば、警告にして返す。"""
    warnings = BatchWarnings()
    if collect_state.failure_count > 0:
        warnings.add(
            "収集でネットワークなどのエラーが再試行しても解消せず、取れなかったデータがあります",
            collect_state.failure_count,
            collect_state.failure_path,
        )
    if collect_state.index_warning_dates:
        # 日経平均を取得できなかった日(表示だけに使うため、処理は続けた)
        warnings.add(INDEX_WARNING_MESSAGE, len(collect_state.index_warning_dates), minor=True)
    return warnings


def _steps_of_day(d: date, predict_from: date) -> tuple[str, ...]:
    """失敗メールの「今の状況」に使う、その日の処理の順番を返す。学習しない土曜は学習を含めない。"""
    if d.weekday() != _SATURDAY:
        return _WEEKDAY_STEPS
    return _SATURDAY_STEPS if is_train_day(d, predict_from) else _SATURDAY_STEPS[1:]


def _report_failure(
    error: BatchError, d: date, date_to: date, predict_from: date, state: RangeCollectState, logger: BatchLogger
) -> None:
    """途中で止まったときに、失敗メールと、それまでの警告のメールを送る。"""
    note = f"{d.isoformat()} で停止。{date_to.isoformat()} までの残りの日は未実行"
    send_failure_mail(LABEL, error, d, logger, _steps_of_day(d, predict_from), note)
    warnings = _collect_warnings(state)
    if warnings:
        send_warning_mail(LABEL, date_to, warnings, note, logger)


def run_range(date_from: date, date_to: date, predict_from: date) -> int:
    """期間の全ての日付について、曜日に応じた処理を行い、終了コードを返す。"""
    logger = get_logger(LABEL, date_to)
    logger.info(
        "処理を開始します(期間: %s 〜 %s、予測の開始日: %s、学習の間隔: %d週ごと)",
        date_from.isoformat(),
        date_to.isoformat(),
        predict_from.isoformat(),
        const.RANGE_TRAIN_INTERVAL_WEEKS,
    )
    if const.RANGE_TRAIN_INTERVAL_WEEKS < 1:
        logger.error(
            "RANGE_TRAIN_INTERVAL_WEEKS の値が正しくありません(%d)。1以上にしてください",
            const.RANGE_TRAIN_INTERVAL_WEEKS,
        )
        send_failure_mail(
            LABEL,
            BatchError(LABEL, f"RANGE_TRAIN_INTERVAL_WEEKS の値が正しくありません({const.RANGE_TRAIN_INTERVAL_WEEKS})"),
            date_from,
            logger,
        )
        return 1
    try:
        # 途中で止まった実行履歴の片付けと、二重起動の確認
        if not prepare(logger):
            return 1
    except Exception as e:
        logger.error("処理を始められませんでした: %s", e, exc_info=True)
        send_failure_mail(LABEL, to_batch_error(LABEL, e), date_from, logger)
        return 1

    collect_state = RangeCollectState(failure_file_path(datetime.now()))
    notify_path = notify_file_path(date_from, date_to)
    d = date_from
    processed_days = 0
    while d <= date_to:
        day_logger = logger.with_date(d)
        try:
            weekday = d.weekday()
            if weekday == _SUNDAY:
                day_logger.debug("日曜のため、何もしません")
            elif weekday == _SATURDAY:
                day_logger.info("土曜の処理(学習・バックアップ・クリーニング)を開始します")
                process_saturday(d, predict_from, day_logger)
            else:
                day_logger.info("平日の処理(収集・予測・検証・通知)を開始します")
                process_weekday(d, predict_from, day_logger, collect_state, notify_path)
        except BatchError as e:
            # 各処理の中で、ログと実行履歴に記録済み
            day_logger.error("%s で失敗したため、ここで止めます(%s)", e.process_name, e.message)
            day_logger.info("直してから、--from %s を指定して、もう一度実行してください", d.isoformat())
            _report_failure(e, d, date_to, predict_from, collect_state, day_logger)
            return 1
        except Exception as e:
            day_logger.error("想定外のエラーが発生したため、ここで止めます: %s", e, exc_info=True)
            day_logger.info("直してから、--from %s を指定して、もう一度実行してください", d.isoformat())
            _report_failure(to_batch_error(LABEL, e), d, date_to, predict_from, collect_state, day_logger)
            return 1
        processed_days += 1
        d += timedelta(days=1)

    logger.info("処理を終了します(処理した日数: %d日)", processed_days)
    if collect_state.failure_count == 0 and collect_state.index_warning_dates:
        # 日経平均だけを取得できなかった場合は、警告メールを送り、終了コードは成功のままにする
        send_warning_mail(LABEL, date_to, _collect_warnings(collect_state), "処理は最後まで完了しました", logger)
    if collect_state.failure_count > 0:
        logger.warning(
            "収集で取れなかったデータが %d件あります。収集失敗の一覧(%s)のコマンドで取り直してから、"
            "その日の予測・検証を行ってください",
            collect_state.failure_count,
            collect_state.failure_path,
        )
        send_warning_mail(LABEL, date_to, _collect_warnings(collect_state), "処理は最後まで完了しました", logger)
        return 1
    return 0


def main(argv: list[str]) -> int:
    """引数を受け取って、期間の全処理を実行する。終了コードを返す。"""
    try:
        args = parse_range_args(argv, prog="range_date_full_process.py")
    except ArgError as e:
        # 引数エラーは、DBを変更せずに終了する
        get_logger(LABEL, today_jst()).error(str(e))
        return 1
    return run_range(args.date_from, args.date_to, args.predict_from)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
