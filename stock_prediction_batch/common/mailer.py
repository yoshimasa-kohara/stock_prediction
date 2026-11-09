"""メール送信。

Gmail のSMTP(smtp.gmail.com、587、STARTTLS)で送る。認証はアプリパスワード。
HTMLとテキストの両方を持つ形式(multipart/alternative、UTF-8)で送る。

- 送る前に、テキスト版の内容(件名+本文)を logs の 通知 フォルダのファイルに書く。送れなくてもファイルは残す。
- 宛先が複数のときは、To を差し替えて1人ずつ送る(他の宛先を知らせないため。To に並べず、Cc・Bcc も使わない)。
"""

import html
import re
import smtplib
from datetime import date
from email.message import EmailMessage
from pathlib import Path

import const
from common.env import get_env, resolve_path
from common.errors import MailSendError
from common.logger import get_logger

# SMTPのクラス(テストで差し替える)
smtp_class: type[smtplib.SMTP] = smtplib.SMTP

# メールの内容のファイルで、件名を囲む区切り線
TEXT_FILE_SEPARATOR: str = "=" * 80
# ログに出す処理の名前
_LOG_LABEL: str = "メール送信"


def _html_to_text(html_body: str) -> str:
    """テキスト形式の本文がない場合に、HTMLからタグを取り除いて作る。"""
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", html_body)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|h[1-6]|li|table)>", "\n", text)
    text = re.sub(r"(?i)</t[dh]>", "\t", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line.strip() != "") + "\n"


def format_text_file(subject: str, text_body: str) -> str:
    """メールの内容のファイルに書く文字列(区切り線・件名・本文)を返す。"""
    return f"{TEXT_FILE_SEPARATOR}\n件名: {subject}\n{TEXT_FILE_SEPARATOR}\n{text_body.rstrip()}\n\n"


def notify_file_path(target_date: date, kind: str = const.MAIL_KIND_NORMAL) -> Path:
    """メールの内容のファイルの場所を返す。日付は実行日ではなく対象日。"""
    name = f"通知_{target_date.isoformat()}{const.NOTIFY_FILE_SUFFIXES[kind]}.txt"
    return resolve_path(const.LOG_DIR) / const.NOTIFY_FILE_DIR / name


def write_notify_file(subject: str, text_body: str, target_date: date, kind: str = const.MAIL_KIND_NORMAL) -> Path:
    """メールの内容をファイルに書き、場所を返す。同じ名前のファイルがあれば上書きする。"""
    path = notify_file_path(target_date, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_text_file(subject, text_body), encoding="utf-8", newline="\n")
    return path


def parse_recipients(value: str) -> list[str]:
    """カンマ区切りの宛先を分ける。前後の空白を除き、重複は1つにまとめる(大文字・小文字は区別しない)。"""
    recipients: dict[str, str] = {}
    for item in value.split(","):
        address = item.strip()
        if address != "":
            recipients.setdefault(address.casefold(), address)
    return list(recipients.values())


def resolve_recipients(recipient_type: str = const.MAIL_RECIPIENT_NORMAL) -> list[str]:
    """宛先の種類から、送り先を返す。

    通常: MAIL_TO → GMAIL_ADDRESS の順。管理者: ADMIN_MAIL_TO → MAIL_TO → GMAIL_ADDRESS の順。
    """
    keys = ["MAIL_TO"] if recipient_type == const.MAIL_RECIPIENT_NORMAL else ["ADMIN_MAIL_TO", "MAIL_TO"]
    for key in keys:
        recipients = parse_recipients(get_env(key, required=False))
        if recipients:
            return recipients
    return [get_env("GMAIL_ADDRESS")]


def send_mail(
    subject: str,
    html_body: str,
    text_body: str | None = None,
    *,
    target_date: date,
    kind: str = const.MAIL_KIND_NORMAL,
    recipient_type: str = const.MAIL_RECIPIENT_NORMAL,
) -> None:
    """内容をファイルに書いてから、Gmail のSMTPで宛先ごとに1人ずつ送る。ログインは1回。

    Args:
        subject: 件名
        html_body: 本文のHTML
        text_body: 本文のテキスト。省略した場合は HTML から作る
        target_date: 対象日(メールの内容のファイル名に使う)
        kind: メールの種類(通常、失敗、警告。ファイル名を決める)
        recipient_type: 宛先の種類(通常、管理者)

    Raises:
        MailSendError: 宛先のどれかに送れなかった場合(残りの宛先に送り切ってから投げる)
        smtplib.SMTPException, OSError: 接続・ログインに失敗した場合
    """
    logger = get_logger(_LOG_LABEL, target_date)
    text = text_body if text_body is not None else _html_to_text(html_body)
    try:
        path = write_notify_file(subject, text, target_date, kind)
        logger.info("メールの内容をファイルに書きました(%s)", path)
    except OSError as e:
        # ファイルに書けなくても、メールは送る
        logger.warning("メールの内容をファイルに書けませんでした: %s", e)

    address = get_env("GMAIL_ADDRESS")
    password = get_env("GMAIL_APP_PASSWORD")
    recipients = resolve_recipients(recipient_type)

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = address
    message.set_content(text, charset="utf-8")
    message.add_alternative(html_body, subtype="html", charset="utf-8")

    sent: list[str] = []
    failed: dict[str, str] = {}
    try:
        with smtp_class(const.SMTP_HOST, const.SMTP_PORT, timeout=60) as smtp:
            smtp.starttls()
            smtp.login(address, password)
            for to in recipients:
                del message["To"]
                message["To"] = to
                try:
                    smtp.send_message(message)
                    sent.append(to)
                except (smtplib.SMTPException, OSError) as e:
                    # 残りの宛先にも送り切る
                    failed[to] = str(e)
    except (smtplib.SMTPException, OSError) as e:
        # 接続・ログインの失敗。送れていない宛先は全て送れなかったものとしてログに出す
        logger.error("メールを送れませんでした(%s。宛先: %s): %s", subject, ", ".join(recipients), e)
        raise

    if sent:
        logger.info("メールを送りました(%s。宛先: %s)", subject, ", ".join(sent))
    if failed:
        for to, reason in failed.items():
            logger.error("メールを送れませんでした(%s。宛先: %s): %s", subject, to, reason)
        raise MailSendError(sent, failed)
