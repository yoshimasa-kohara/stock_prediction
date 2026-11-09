"""common/logger.py のテスト。"""

import logging
import re
from datetime import date
from pathlib import Path

import pytest

import const
from common import logger as lg


def _read_log() -> str:
    return lg.log_file_path().read_text(encoding="utf-8")


def test_書式は日時_レベル_処理の名前_指定日_メッセージ() -> None:
    log = lg.get_logger("収集", date(2026, 7, 8))
    log.info("株価を登録しました(%s件)", "4,412")
    line = _read_log().splitlines()[-1]
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} INFO  収集 2026-07-08 - 株価を登録しました\(4,412件\)", line
    )


def test_銘柄コードを渡すと指定日の後ろに入る() -> None:
    log = lg.get_logger("予測", date(2026, 7, 8))
    log.debug("予測しました", extra={"code": "11110"})
    assert _read_log().splitlines()[-1].endswith("DEBUG 予測 2026-07-08 11110 - 予測しました")


def test_WARNINGは7文字でそろう() -> None:
    lg.get_logger("検証", date(2026, 7, 8)).warning("ずれています")
    assert " WARNING 検証 2026-07-08 - ずれています" in _read_log()


def test_with_dateは指定日だけを変える() -> None:
    log = lg.get_logger("収集", date(2026, 7, 8)).with_date(date(2026, 7, 7))
    assert log.process_name == "収集"
    log.info("補完")
    assert "収集 2026-07-07 - 補完" in _read_log()


def test_ログファイルの名前は実行した日(tmp_path: Path) -> None:
    path = lg.log_file_path(date(2026, 10, 10))
    assert path.name == "batch_20261010.log"
    assert path.parent == Path(const.LOG_DIR)


def test_LOG_LEVELがINFOならDEBUGは出ない(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(const, "LOG_LEVEL", "INFO")
    lg.reset_handlers()
    log = lg.get_logger("予測", date(2026, 7, 8))
    log.debug("1銘柄ごとの内容")
    log.info("件数")
    text = _read_log()
    assert "1銘柄ごとの内容" not in text
    assert "件数" in text
    assert logging.getLogger("stock_prediction").level == logging.INFO


def test_エラーはスタックトレースも出す() -> None:
    log = lg.get_logger("収集", date(2026, 7, 8))
    try:
        raise ValueError("こわれた")
    except ValueError:
        log.error("失敗しました", exc_info=True, extra={"code": "11110"})
    text = _read_log()
    assert "ERROR 収集 2026-07-08 11110 - 失敗しました" in text
    assert "Traceback" in text and "ValueError: こわれた" in text


def test_出力先は1回だけ付ける() -> None:
    lg.get_logger("収集", date(2026, 7, 8))
    lg.get_logger("予測", date(2026, 7, 8))
    own = [h for h in logging.getLogger("stock_prediction").handlers if getattr(h, "_stock_prediction_handler", False)]
    assert len(own) == 2
