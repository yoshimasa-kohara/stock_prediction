"""common/mailer.py のテスト。メールは本物を送らない。"""

from datetime import date
from email.message import EmailMessage
from typing import Any

import pytest

import const
from common import mailer
from common.errors import MailSendError
from common.logger import log_file_path

D = date(2026, 7, 8)


class FakeSMTP:
    """smtplib.SMTP の代わり。呼ばれた内容を記録する。"""

    instances: list["FakeSMTP"] = []
    # 送れないことにする宛先
    refuse: set[str] = set()

    def __init__(self, host: str, port: int, timeout: int) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self.calls: list[str] = []
        self.login_args: tuple[str, str] | None = None
        self.message: EmailMessage | None = None
        # send_message の時点の To(同じ EmailMessage を差し替えて使うので、その場で控える)
        self.to_list: list[str] = []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *args: Any) -> None:
        self.calls.append("quit")

    def starttls(self) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append("login")
        self.login_args = (user, password)

    def send_message(self, message: EmailMessage) -> None:
        self.calls.append("send")
        self.message = message
        to = str(message["To"])
        self.to_list.append(to)
        # 他の宛先が漏れないこと
        assert message.get_all("To") == [to] and message["Cc"] is None and message["Bcc"] is None
        if to in FakeSMTP.refuse:
            raise OSError("この宛先には送れません")


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.instances = []
    FakeSMTP.refuse = set()
    monkeypatch.setattr(mailer, "smtp_class", FakeSMTP)
    return FakeSMTP


def test_Gmailの設定で送る(smtp: type[FakeSMTP]) -> None:
    mailer.send_mail("【株価予測】件名", "<p>本文<br>2行目</p>", "テキストの本文", target_date=D)
    s = smtp.instances[0]
    assert (s.host, s.port) == ("smtp.gmail.com", 587)
    assert s.calls == ["starttls", "login", "send", "quit"]
    assert s.login_args == ("sender@example.com", "test-app-password")
    assert s.message is not None
    assert s.message["Subject"] == "【株価予測】件名"
    assert s.message["From"] == "sender@example.com"
    # 宛先を書かなければ、送信元と同じアドレス
    assert s.message["To"] == "sender@example.com"
    assert s.message.get_content_type() == "multipart/alternative"
    parts = list(s.message.iter_parts())
    assert [p.get_content_type() for p in parts] == ["text/plain", "text/html"]
    assert parts[0].get_content().strip() == "テキストの本文"


def test_宛先はMAIL_TOがあればそれを使う(smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAIL_TO", "to@example.com")
    mailer.send_mail("件名", "<p>本文</p>", target_date=D)
    assert smtp.instances[0].message is not None
    assert smtp.instances[0].message["To"] == "to@example.com"


def test_テキストの本文を省略するとHTMLから作る(smtp: type[FakeSMTP]) -> None:
    mailer.send_mail(
        "件名", "<style>p{}</style><table><tr><td>順位</td><td>1</td></tr></table><p>a &amp; b</p>", target_date=D
    )
    message = smtp.instances[0].message
    assert message is not None
    text = list(message.iter_parts())[0].get_content()
    assert "順位\t1" in text
    assert "a & b" in text
    assert "p{}" not in text


# =====================================================================
# メールの内容のファイル
# =====================================================================


@pytest.mark.parametrize(
    ("kind", "name"),
    [
        (const.MAIL_KIND_NORMAL, "通知_2026-07-08.txt"),
        (const.MAIL_KIND_FAILURE, "通知_2026-07-08_失敗.txt"),
        (const.MAIL_KIND_WARNING, "通知_2026-07-08_警告.txt"),
    ],
)
def test_送る前に内容をファイルに書く(smtp: type[FakeSMTP], kind: str, name: str) -> None:
    mailer.send_mail("件名", "<p>x</p>", "テキストの本文", target_date=D, kind=kind)
    path = log_file_path().parent / "通知" / name
    assert path.read_text(encoding="utf-8") == f"{'=' * 80}\n件名: 件名\n{'=' * 80}\nテキストの本文\n\n"


def test_同じ名前のファイルは上書きする(smtp: type[FakeSMTP]) -> None:
    mailer.send_mail("古い件名", "<p>x</p>", "古い本文", target_date=D)
    mailer.send_mail("新しい件名", "<p>x</p>", "新しい本文", target_date=D)
    text = mailer.notify_file_path(D).read_text(encoding="utf-8")
    assert "新しい本文" in text and "古い本文" not in text


def test_送信に失敗してもファイルは残る(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken(FakeSMTP):
        def login(self, user: str, password: str) -> None:
            raise OSError("ログインできません")

    monkeypatch.setattr(mailer, "smtp_class", Broken)
    with pytest.raises(OSError, match="ログインできません"):
        mailer.send_mail("件名", "<p>x</p>", "本文", target_date=D)
    assert "本文" in mailer.notify_file_path(D).read_text(encoding="utf-8")


def test_ファイルに書けなくてもメールは送る(smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any, **kwargs: Any) -> None:
        raise OSError("書けません")

    monkeypatch.setattr(mailer, "write_notify_file", broken)
    mailer.send_mail("件名", "<p>x</p>", target_date=D)
    assert smtp.instances[0].calls.count("send") == 1
    assert "メールの内容をファイルに書けませんでした" in log_file_path().read_text(encoding="utf-8")


# =====================================================================
# 宛先
# =====================================================================


def test_宛先はカンマで分けて空白を除き重複をまとめる() -> None:
    assert mailer.parse_recipients(" a@example.com, b@example.com ,a@example.com,,A@EXAMPLE.COM ") == [
        "a@example.com",
        "b@example.com",
    ]
    assert mailer.parse_recipients(" , ") == []


def test_宛先ごとにToを1つずつにして送りログインは1回(smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAIL_TO", "a@example.com, b@example.com,a@example.com")
    mailer.send_mail("件名", "<p>x</p>", target_date=D)
    assert len(smtp.instances) == 1
    s = smtp.instances[0]
    assert s.calls == ["starttls", "login", "send", "send", "quit"]
    assert s.to_list == ["a@example.com", "b@example.com"]


def test_一部の宛先が失敗しても残りに送ってから失敗になる(
    smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAIL_TO", "a@example.com,b@example.com,c@example.com")
    smtp.refuse = {"b@example.com"}
    with pytest.raises(MailSendError) as e:
        mailer.send_mail("件名", "<p>x</p>", target_date=D)
    assert smtp.instances[0].to_list == ["a@example.com", "b@example.com", "c@example.com"]
    assert e.value.partial is True
    assert e.value.sent == ["a@example.com", "c@example.com"]
    assert list(e.value.failed) == ["b@example.com"]
    log = log_file_path().read_text(encoding="utf-8")
    assert "宛先: a@example.com, c@example.com" in log and "宛先: b@example.com" in log


def test_全ての宛先に送れなければpartialではない(smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAIL_TO", "a@example.com")
    smtp.refuse = {"a@example.com"}
    with pytest.raises(MailSendError) as e:
        mailer.send_mail("件名", "<p>x</p>", target_date=D)
    assert e.value.partial is False


@pytest.mark.parametrize(
    ("admin", "mail_to", "expected"),
    [
        ("x@example.com,y@example.com", "to@example.com", ["x@example.com", "y@example.com"]),
        ("", "to@example.com", ["to@example.com"]),
        ("", "", ["sender@example.com"]),
    ],
)
def test_管理者の宛先の順番(monkeypatch: pytest.MonkeyPatch, admin: str, mail_to: str, expected: list[str]) -> None:
    monkeypatch.setenv("ADMIN_MAIL_TO", admin)
    monkeypatch.setenv("MAIL_TO", mail_to)
    assert mailer.resolve_recipients(const.MAIL_RECIPIENT_ADMIN) == expected


def test_通常の宛先はADMIN_MAIL_TOを使わない(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_MAIL_TO", "x@example.com")
    monkeypatch.setenv("MAIL_TO", "")
    assert mailer.resolve_recipients() == ["sender@example.com"]
