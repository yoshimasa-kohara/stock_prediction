"""Gmail の送信設定を確かめる、調査用の実行ファイル(一時的なもの。原因がわかったら消す)。

.env の GMAIL_ADDRESS / GMAIL_APP_PASSWORD の形と、SMTPのどの段階で失敗するかを表示する。
パスワードそのものは表示しない(文字数と、問題のある文字の位置だけ)。
メールは送らない(ログインまでで止める)。

使い方:
    python check_mail.py
"""

import contextlib
import smtplib
import ssl

import const
from common.env import get_env


def check_env() -> tuple[str, str]:
    """.env の値の形を確かめて表示し、アドレスとパスワードを返す。"""
    address = get_env("GMAIL_ADDRESS")
    password = get_env("GMAIL_APP_PASSWORD")
    print(f"GMAIL_ADDRESS: {address}")
    print(f"GMAIL_APP_PASSWORD の文字数: {len(password)}(アプリパスワードは16文字。スペース入りなら19文字)")
    # 半角英字とスペース以外の文字(全角文字、引用符など)があれば、位置を表示する
    for i, ch in enumerate(password, start=1):
        if not (ch.isascii() and (ch.isalpha() or ch == " ")):
            print(f"  ※ {i}文字目に英字以外の文字があります(文字コード: U+{ord(ch):04X})")
    return address, password


def try_login(name: str, smtp: smtplib.SMTP, address: str, password: str, use_starttls: bool) -> None:
    """接続済みのSMTPで、STARTTLS とログインを試し、どの段階まで進めたかを表示する。"""
    try:
        code, _ = smtp.ehlo()
        print(f"[{name}] EHLO: {code}")
        if use_starttls:
            code, _ = smtp.starttls()
            print(f"[{name}] STARTTLS: {code}")
            smtp.ehlo()
        code, resp = smtp.login(address, password)
        print(f"[{name}] ログイン成功: {code} {resp.decode(errors='replace')}")
    except smtplib.SMTPAuthenticationError as e:
        error = e.smtp_error.decode(errors="replace") if isinstance(e.smtp_error, bytes) else e.smtp_error
        print(f"[{name}] ログイン失敗(認証エラー): {e.smtp_code} {error}")
    except Exception as e:
        print(f"[{name}] 失敗: {type(e).__name__}: {e}")
    finally:
        with contextlib.suppress(Exception):
            smtp.quit()


def main() -> None:
    """.env の確認と、587(STARTTLS)・465(SSL)の2通りのログインを試す。"""
    address, password = check_env()
    print()
    try:
        try_login("587 STARTTLS", smtplib.SMTP(const.SMTP_HOST, 587, timeout=30), address, password, True)
    except Exception as e:
        print(f"[587 STARTTLS] 接続できません: {type(e).__name__}: {e}")
    try:
        smtp_ssl = smtplib.SMTP_SSL(const.SMTP_HOST, 465, timeout=30, context=ssl.create_default_context())
        try_login("465 SSL", smtp_ssl, address, password, False)
    except Exception as e:
        print(f"[465 SSL] 接続できません: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
