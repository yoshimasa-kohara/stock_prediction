"""例外クラス。"""

from datetime import date


class BatchError(Exception):
    """処理の中で発生したエラー。処理の名前と、処理中の銘柄コードを持つ。

    失敗メールとログに、どの処理の、どの銘柄で止まったかを出すために使う。
    """

    def __init__(self, process_name: str, message: str, code: str | None = None) -> None:
        """
        Args:
            process_name: 処理の名前(日本語。例: 収集)
            message: エラーの内容
            code: 処理中の銘柄コード。ない場合は None
        """
        super().__init__(message)
        self.process_name: str = process_name
        self.message: str = message
        self.code: str | None = code

    def __str__(self) -> str:
        if self.code:
            return f"{self.message}(銘柄コード: {self.code})"
        return self.message


class MailSendError(Exception):
    """宛先のどれかにメールを送れなかった場合のエラー。残りの宛先に送り切ってから投げる。"""

    def __init__(self, sent: list[str], failed: dict[str, str]) -> None:
        """
        Args:
            sent: 送れた宛先
            failed: 送れなかった宛先と、そのエラーの内容
        """
        super().__init__(f"送れなかった宛先があります(送れた宛先: {len(sent)}件、送れなかった宛先: {len(failed)}件)")
        self.sent: list[str] = sent
        self.failed: dict[str, str] = failed

    @property
    def partial(self) -> bool:
        """一部の宛先には送れた場合は True。"""
        return bool(self.sent)


class ArgError(Exception):
    """実行ファイルの引数が正しくない場合のエラー。メッセージは各実行ファイルの「引数チェック」のとおり。"""


class ApiError(Exception):
    """J-Quants API の呼び出しのエラー。やり直しても直らないもの(401・403 など)。"""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code: int | None = status_code


class PlanRangeError(ApiError):
    """契約プランの期間外の可能性があるエラー。やり直さない。

    403、決算発表予定日のAPIの 400、メッセージに契約期間が入った 400 が対象。

    期間の収集(collect.py --from/--to、range_date_full_process.py)では、データがない日としてとばすことがある。
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        subscription_range: bool = False,
        first_date: date | None = None,
    ) -> None:
        """
        Args:
            message: エラーの内容
            status_code: HTTP のステータスコード
            subscription_range: 400 で、メッセージに契約期間(Your subscription covers ...)が入っていた場合は True
            first_date: 契約で取れる最初の日。メッセージから読み取れなかった場合は None
        """
        super().__init__(message, status_code)
        self.subscription_range: bool = subscription_range
        self.first_date: date | None = first_date


class RateLimitError(ApiError):
    """J-Quants API が 429(リクエスト数の上限を超えた)を返した。待ってやり直す。"""


class TransientApiError(ApiError):
    """通信エラー、タイムアウト、5xx(サーバー側のエラー)。待ってやり直す。"""
