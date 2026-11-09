"""API のログ(利用者の指示で、logs フォルダのファイルに出す)。

- stock_prediction_web\\logs の下に、日ごとに1つのファイル(web_YYYYMMDD.log)に追記し、コンソールにも同じ内容を出す。
  日付が変わったら、次の行から新しい日のファイルに書く(Web サーバーは何日も動き続けるため)。
- 1行の書式: 日時 レベル - メッセージ(バッチのログと同じ並び)
  例: 2026-10-27 19:05:31 INFO  - API開始 GET /api/prediction-dates 処理=status.get_prediction_dates
        引数={from=2026-07-01, to=None}(実際は1行)
      2026-10-27 19:05:31 INFO  - API終了 GET /api/prediction-dates 処理時間=0.012秒
- log_api デコレーターを API の関数に付けると、最初に「どの API が、どの引数で呼ばれたか」を、最後に「処理時間」を出す。
- web_const.LOG_KEEP_DAYS(30日)を過ぎたログファイルは、起動したときと、日付が変わったときに削除する。
"""

import functools
import inspect
import logging
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from fastapi import Request

from api import web_const

# 出力に使うロガーの名前(API の全ての部品で1つを共有する。api/errors.py も同じ名前を使う)
LOGGER_NAME: str = "stock_prediction_web"
# 1行の書式
_FORMAT: str = "%(asctime)s %(levelname)-5s - %(message)s"
_DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S"
# ログファイルの名前(web_YYYYMMDD.log)
_FILE_PREFIX: str = "web_"
_FILE_SUFFIX: str = ".log"
# 日付の基準にする時刻帯
_JST: ZoneInfo = ZoneInfo("Asia/Tokyo")
# このモジュールが付けた出力先に付ける印(何度呼んでも二重に付けないため)
_OWN_HANDLER_MARK: str = "_stock_prediction_web_handler"
# デコレーターが付け足す引数の名前(FastAPI がリクエストを渡す。API の関数には渡さない)
_REQUEST_PARAM: str = "_log_request"

# stock_prediction_web のフォルダ(このファイルの1つ上)
BASE_DIR: Path = Path(__file__).resolve().parent.parent

logger: logging.Logger = logging.getLogger(LOGGER_NAME)


def today_jst() -> date:
    """日本時間の今日。"""
    return datetime.now(_JST).date()


def log_dir() -> Path:
    """ログファイルを置くフォルダ(web_const.LOG_DIR。相対パスは stock_prediction_web が起点)。"""
    path = Path(web_const.LOG_DIR)
    return path if path.is_absolute() else BASE_DIR / path


def log_file_path(day: date) -> Path:
    """指定した日のログファイルの場所。"""
    return log_dir() / f"{_FILE_PREFIX}{day.strftime('%Y%m%d')}{_FILE_SUFFIX}"


def delete_old_logs(today: date) -> int:
    """保持日数(web_const.LOG_KEEP_DAYS)を過ぎたログファイルを削除し、削除した数を返す。

    今日を含めて LOG_KEEP_DAYS 日分を残す(例: 30日なら、今日から29日前までを残す)。
    名前が web_YYYYMMDD.log の形でないファイルは消さない。
    """
    folder = log_dir()
    if not folder.is_dir():
        return 0
    oldest_kept = today - timedelta(days=web_const.LOG_KEEP_DAYS - 1)
    deleted = 0
    for path in folder.glob(f"{_FILE_PREFIX}*{_FILE_SUFFIX}"):
        stamp = path.name[len(_FILE_PREFIX) : -len(_FILE_SUFFIX)]
        try:
            day = datetime.strptime(stamp, "%Y%m%d").date()
        except ValueError:
            continue
        if day < oldest_kept:
            path.unlink(missing_ok=True)
            deleted += 1
    return deleted


class DailyFileHandler(logging.Handler):
    """日ごとのファイル(web_YYYYMMDD.log)に追記する出力先。日付が変わったら、次の行から新しいファイルに書く。

    ファイルの名前を変える方式(TimedRotatingFileHandler)は、Windows でファイルが使用中だと失敗することがあるので
    使わない。
    """

    def __init__(self, today: Callable[[], date] = today_jst) -> None:
        super().__init__()
        self._today = today
        self._day: date | None = None
        self._stream: Any = None

    def _open(self, day: date) -> None:
        """指定した日のファイルを開き直す。古いログファイルも削除する。"""
        self._close_stream()
        path = log_file_path(day)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = path.open("a", encoding="utf-8")
        self._day = day
        delete_old_logs(day)

    def _close_stream(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            day = self._today()
            if day != self._day:
                self._open(day)
            self._stream.write(self.format(record) + "\n")
            self._stream.flush()
        except Exception:  # pragma: no cover - ログを書けなくても API は止めない
            self.handleError(record)

    def close(self) -> None:
        self.acquire()
        try:
            self._close_stream()
        finally:
            self.release()
        super().close()


def setup_logging() -> logging.Logger:
    """ロガーに出力先(ファイルとコンソール)を付ける。何度呼んでも二重には付けない。"""
    if any(getattr(h, _OWN_HANDLER_MARK, False) for h in logger.handlers):
        return logger
    formatter = logging.Formatter(_FORMAT, _DATE_FORMAT)
    handlers: list[logging.Handler] = [DailyFileHandler(), logging.StreamHandler()]
    for h in handlers:
        h.setFormatter(formatter)
        setattr(h, _OWN_HANDLER_MARK, True)
        logger.addHandler(h)
    logger.setLevel(web_const.LOG_LEVEL)
    # uvicorn など、上のロガーに同じ内容を二重に出さない
    logger.propagate = False
    return logger


def reset_logging() -> None:
    """このモジュールが付けた出力先を外して閉じる(テストで使う)。"""
    for h in list(logger.handlers):
        if getattr(h, _OWN_HANDLER_MARK, False):
            logger.removeHandler(h)
            h.close()


def _format_value(value: Any) -> str:
    """ログに出す引数の値の書き方。日付は YYYY-MM-DD、入力の本文(pydantic)は項目の一覧にする。"""
    if hasattr(value, "model_dump"):
        return str(value.model_dump(mode="json"))
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return repr(value) if isinstance(value, str) else str(value)


def _query_name(sig: inspect.Signature, name: str) -> str:
    """引数の名前を、URL のクエリの名前にする(alias があればそれ。例: date_from → from)。"""
    param = sig.parameters.get(name)
    if param is not None:
        for meta in getattr(param.annotation, "__metadata__", ()):
            alias = getattr(meta, "alias", None)
            if alias:
                return str(alias)
    return name


def format_args(sig: inspect.Signature, kwargs: dict[str, Any]) -> str:
    """API の関数に渡す引数を、ログに出す文字にする。並びは関数の引数の順。DB の接続は出さない。"""
    order = [name for name in sig.parameters if name in kwargs]
    names = order + [name for name in kwargs if name not in order]
    parts = [
        f"{_query_name(sig, k)}={_format_value(kwargs[k])}"
        for k in names
        if not isinstance(kwargs[k], psycopg.Connection)
    ]
    return "{" + ", ".join(parts) + "}"


def log_api[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    """API の関数に付けるデコレーター。最初に呼ばれた API と引数を、最後に処理時間をログに出す。

    @router.get(...) の下(関数のすぐ上)に付ける。
    HTTP のメソッドとパスを出すため、FastAPI にリクエストも渡してもらう(関数の引数の一覧に _log_request を足す。
    API の関数には渡さない)。エラーで終わったときも処理時間を出し、エラーはそのまま上に伝える。
    """
    sig = inspect.signature(func)
    name = f"{func.__module__.rsplit('.', 1)[-1]}.{func.__name__}"

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        request = kwargs.pop(_REQUEST_PARAM, None)
        target = f"{request.method} {request.url.path}" if isinstance(request, Request) else name
        logger.info("API開始 %s 処理=%s 引数=%s", target, name, format_args(sig, dict(kwargs)))
        start = time.perf_counter()
        try:
            result = func(*args, **kwargs)
        except Exception:
            logger.info("API終了(エラー) %s 処理時間=%.3f秒", target, time.perf_counter() - start)
            raise
        logger.info("API終了 %s 処理時間=%.3f秒", target, time.perf_counter() - start)
        return result

    # FastAPI に、元の引数に加えてリクエストを渡してもらう
    request_param = inspect.Parameter(_REQUEST_PARAM, inspect.Parameter.KEYWORD_ONLY, annotation=Request)
    params = [p for p in sig.parameters.values() if p.kind != inspect.Parameter.VAR_KEYWORD]
    wrapper.__signature__ = sig.replace(parameters=[*params, request_param])  # type: ignore[attr-defined]
    return wrapper
