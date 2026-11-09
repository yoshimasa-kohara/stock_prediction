"""ログ出力。

1行の書式: 日時 レベル 処理の名前 指定日 [銘柄コード] - メッセージ
  例: 2026-07-08 19:00:31 INFO  収集 2026-07-08 - 株価を登録しました(4,412件)
      2026-07-08 19:00:31 DEBUG 予測 2026-07-08 11110 - 予測しました
LOG_DIR の下に、実行した日ごとに1つのファイル(batch_YYYYMMDD.log)に追記し、画面にも同じ内容を出す。
銘柄コードは、出力するときに extra={"code": "11110"} で渡す。
"""

import logging
import sys
from collections.abc import MutableMapping
from datetime import date
from pathlib import Path
from typing import Any

import const
from common.args import today_jst
from common.env import resolve_path

# 出力に使うロガーの名前(全ての処理で1つを共有する)
_LOGGER_NAME: str = "stock_prediction"
# 1行の書式
_FORMAT: str = "%(asctime)s %(levelname)-5s %(process_label)s %(target_label)s%(code_label)s - %(message)s"
_DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S"
# このモジュールが付けた出力先に付ける印
_OWN_HANDLER_MARK: str = "_stock_prediction_handler"


def log_file_path(run_date: date | None = None) -> Path:
    """ログファイルの場所を返す。run_date を省略した場合は今日(日本時間)。"""
    d = run_date or today_jst()
    return resolve_path(const.LOG_DIR) / f"batch_{d.strftime('%Y%m%d')}.log"


class BatchLogger(logging.LoggerAdapter[logging.Logger]):
    """処理の名前と指定日を、出力する行に自動で付けるロガー。"""

    def __init__(self, logger: logging.Logger, process_name: str, target_date: date) -> None:
        super().__init__(logger, {"process_label": process_name, "target_label": target_date.isoformat()})
        self.process_name: str = process_name
        self.target_date: date = target_date

    def process(self, msg: Any, kwargs: MutableMapping[str, Any]) -> tuple[Any, MutableMapping[str, Any]]:
        extra: dict[str, Any] = dict(self.extra or {})
        extra.update(kwargs.get("extra") or {})
        code = extra.get("code")
        extra["code_label"] = f" {code}" if code else ""
        kwargs["extra"] = extra
        return msg, kwargs

    def with_date(self, target_date: date) -> "BatchLogger":
        """指定日だけを変えたロガーを返す(期間を処理するとき、処理中の日付をログに出すため)。"""
        return BatchLogger(self.logger, self.process_name, target_date)


def _setup_base_logger() -> logging.Logger:
    """共有のロガーに、ファイルと画面の出力先を1回だけ付ける。"""
    base = logging.getLogger(_LOGGER_NAME)
    base.setLevel(getattr(logging, const.LOG_LEVEL.upper(), logging.DEBUG))
    base.propagate = False
    # 自分で付けた出力先があるかで判定する(テストの道具などが、ほかの出力先を付けることがあるため)
    if not any(getattr(h, _OWN_HANDLER_MARK, False) for h in base.handlers):
        formatter = logging.Formatter(_FORMAT, _DATE_FORMAT)
        path = log_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        for handler in (file_handler, console_handler):
            setattr(handler, _OWN_HANDLER_MARK, True)
            base.addHandler(handler)
    return base


def get_logger(process_name: str, target_date: date) -> BatchLogger:
    """ログを出力するためのオブジェクトを返す。

    Args:
        process_name: 処理の名前(日本語。例: 収集)
        target_date: 指定日
    """
    return BatchLogger(_setup_base_logger(), process_name, target_date)


def reset_handlers() -> None:
    """出力先を外す(テストで、ログの出力先を作り直すために使う)。"""
    base = logging.getLogger(_LOGGER_NAME)
    for handler in list(base.handlers):
        if not getattr(handler, _OWN_HANDLER_MARK, False):
            continue
        handler.close()
        base.removeHandler(handler)
