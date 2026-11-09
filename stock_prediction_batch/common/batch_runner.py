"""日次バッチ・週次バッチ・全処理で共通の処理。

- 途中で止まった実行履歴を片付ける(RUNNING_STALE_HOURS を過ぎた RUNNING の行を FAILED にする)
- 二重起動を確かめる(RUNNING の行が1つでもあれば、何もせずに終わる)
- 失敗メール・警告メールを、管理者(.env の ADMIN_MAIL_TO)に送る(送れなかった場合は、ログに残して続ける)
"""

import html
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import const
from common import mailer, run_history
from common.args import JST
from common.errors import BatchError, MailSendError
from common.logger import BatchLogger, log_file_path


def prepare(logger: BatchLogger) -> bool:
    """途中で止まった実行履歴を片付け、二重起動を確かめる。起動してよければ True を返す。"""
    stale = run_history.mark_stale_runs()
    if stale:
        logger.warning("途中で止まった実行履歴を、失敗に更新しました(%d件)", stale)
    running = run_history.find_running()
    if running:
        r = running[0]
        logger.info(
            "実行中の処理があるため、起動しません(処理の名前: %s、開始日時: %s)",
            r["process_name"],
            r["started_at"].astimezone(JST).strftime("%Y-%m-%d %H:%M:%S"),
        )
        return False
    logger.info("実行中の処理はありません")
    return True


def to_batch_error(label: str, error: Exception) -> BatchError:
    """想定外のエラーを BatchError に直す。元のエラーの種類を失敗メールに出せるように、原因として持たせる。"""
    if isinstance(error, BatchError):
        return error
    batch_error = BatchError(label, str(error))
    batch_error.__cause__ = error
    return batch_error


def failure_subject(batch_label: str, process_label: str, target_date: date) -> str:
    """失敗メールの件名。例: 【株価予測】日次バッチ失敗(予測) 2026-07-08"""
    return f"{const.MAIL_SUBJECT_PREFIX}{batch_label}失敗({process_label}) {target_date.isoformat()}"


def warning_subject(batch_label: str, target_date: date) -> str:
    """警告メールの件名。例: 【株価予測】【警告】日次バッチ 2026-07-08"""
    return f"{const.MAIL_SUBJECT_PREFIX}【警告】{batch_label} {target_date.isoformat()}"


def status_text(steps: Sequence[str], failed: str, note: str | None = None) -> str:
    """失敗メールの「今の状況」を作る。例: 収集は完了、予測で停止、検証・通知は未実行

    Args:
        steps: そのバッチの処理の名前(実行する順)
        failed: 失敗した処理の名前
        note: 最後に付け足す説明
    """
    if failed in steps:
        i = steps.index(failed)
        parts = [f"{failed}で停止"]
        if i > 0:
            parts.insert(0, f"{'・'.join(steps[:i])}は完了")
        if i < len(steps) - 1:
            parts.append(f"{'・'.join(steps[i + 1 :])}は未実行")
        text = "、".join(parts)
    else:
        text = f"{failed}で停止(どこまで完了したかはログで確認してください)"
    return f"{text}。{note}" if note else text


def _one_line(text: str) -> str:
    """改行や続く空白を1つの空白にして、1行にする。"""
    return " ".join(text.split())


def _build_bodies(items: Sequence[tuple[str, str]]) -> tuple[str, str]:
    """「項目: 内容」の一覧から、メールのHTMLとテキストの本文を作る。"""
    text = "\n".join(f"{k}: {v}" for k, v in items) + "\n"
    rows = "".join(
        f'<tr><td style="padding:2px 12px 2px 0;color:#666666;">{html.escape(k)}</td>'
        f'<td style="padding:2px 0;">{html.escape(v)}</td></tr>'
        for k, v in items
    )
    body = (
        "<html><body style=\"font-family:'Meiryo','Yu Gothic',sans-serif;font-size:14px;color:#222222;\">"
        f'<table style="border-collapse:collapse;font-size:13px;">{rows}</table></body></html>'
    )
    return body, text


def send_failure_mail(
    batch_label: str,
    error: BatchError,
    processing_date: date,
    logger: BatchLogger,
    steps: Sequence[str] = (),
    note: str | None = None,
) -> None:
    """失敗メールを管理者に送る。送れなかった場合は、ログに残して終わる。

    Args:
        batch_label: バッチの種類(日次バッチ、週次バッチ、全処理)
        error: 失敗した処理の例外
        processing_date: 処理していた日付(補完した日の処理中なら、その日付)
        logger: ログを出力するオブジェクト
        steps: そのバッチの処理の名前(実行する順)。「今の状況」に使う
        note: 「今の状況」に付け足す説明
    """
    occurred_at = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    cause = error.__cause__
    items = [
        ("バッチの種類", batch_label),
        ("失敗した処理", error.process_name),
        ("対象の日付", processing_date.isoformat()),
        ("発生時刻", occurred_at),
        ("処理中の銘柄", error.code or "-"),
        ("エラーの種類", type(cause if cause is not None else error).__name__),
        ("エラーの内容", _one_line(error.message)),
        ("今の状況", status_text(steps, error.process_name, note)),
        ("ログファイル", str(log_file_path())),
        ("ログを探す目印", f"発生時刻 {occurred_at} の付近の行"),
    ]
    body, text = _build_bodies(items)
    subject = failure_subject(batch_label, error.process_name, processing_date)
    try:
        mailer.send_mail(
            subject,
            body,
            text,
            target_date=processing_date,
            kind=const.MAIL_KIND_FAILURE,
            recipient_type=const.MAIL_RECIPIENT_ADMIN,
        )
        logger.info("失敗メールを送りました(%s)", subject)
    except Exception as e:
        logger.error("失敗メールを送れませんでした: %s", e, exc_info=True)


@dataclass(frozen=True)
class BatchWarning:
    """処理は続けたが問題があったことの1件分。"""

    message: str
    count: int
    path: Path | None = None
    # 終了コードを失敗(1)にしない警告(日経平均を取得できなかったなど。表示だけに使うデータ)
    minor: bool = False


@dataclass
class BatchWarnings:
    """バッチの実行の間に出た警告を集める。処理が終わったときに、1通のメールにまとめて送る。"""

    items: list[BatchWarning] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.items)

    def add(self, message: str, count: int, path: Path | None = None, minor: bool = False) -> None:
        """警告を1つ加える。minor は、終了コードを失敗(1)にしない警告のとき True。"""
        self.items.append(BatchWarning(message, count, path, minor))

    @property
    def only_minor(self) -> bool:
        """全ての警告が、終了コードを失敗にしない警告なら True(警告がない場合も True)。"""
        return all(w.minor for w in self.items)

    def add_partial_mail(self, subject: str, error: MailSendError) -> None:
        """一部の宛先にメールを送れなかったことを加える。宛先そのものはログで確かめる(メールには書かない)。"""
        self.add(f"一部の宛先にメールを送れませんでした(件名: {subject})", len(error.failed))


def send_warning_mail(
    batch_label: str, target_date: date, warnings: BatchWarnings, status: str, logger: BatchLogger
) -> None:
    """警告メールを管理者に1通送る。送れなかった場合は、ログに残して終わる。

    Args:
        batch_label: バッチの種類(日次バッチ、全処理)
        target_date: 指定日(全処理は --to の日付)
        warnings: 集めた警告
        status: 今の状況
        logger: ログを出力するオブジェクト
    """
    items = [("バッチの種類", batch_label), ("対象の日付", target_date.isoformat())]
    for w in warnings.items:
        items.append(("警告の内容", f"{w.message}: {w.count}件"))
    items.append(("今の状況", status))
    items.append(("ログファイル", str(log_file_path())))
    for w in warnings.items:
        if w.path is not None:
            items.append(("一覧のファイル", str(w.path)))
    body, text = _build_bodies(items)
    subject = warning_subject(batch_label, target_date)
    try:
        mailer.send_mail(
            subject,
            body,
            text,
            target_date=target_date,
            kind=const.MAIL_KIND_WARNING,
            recipient_type=const.MAIL_RECIPIENT_ADMIN,
        )
        logger.info("警告メールを送りました(%s)", subject)
    except Exception as e:
        logger.error("警告メールを送れませんでした: %s", e, exc_info=True)
