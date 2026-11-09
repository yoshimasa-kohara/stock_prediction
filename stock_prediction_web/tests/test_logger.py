"""API のログ(api/logger.py)のテスト。"""

import logging
from datetime import date
from pathlib import Path
from typing import Annotated

import psycopg
import pytest
from fastapi import FastAPI, Query
from fastapi.testclient import TestClient
from psycopg.rows import DictRow
from pydantic import BaseModel

from api import logger as web_logger
from api import web_const
from api.errors import ApiError, register_error_handlers
from api.logger import DailyFileHandler, delete_old_logs, log_api, log_file_path, setup_logging


def read_log(day: date | None = None) -> str:
    """ログファイルの中身(省略すると今日のファイル)。"""
    for h in web_logger.logger.handlers:
        h.flush()
    return log_file_path(day or web_logger.today_jst()).read_text(encoding="utf-8")


def test_log_dir_relative_to_web_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    """相対パスは stock_prediction_web のフォルダを起点にする。"""
    monkeypatch.setattr(web_const, "LOG_DIR", "logs")
    assert web_logger.log_dir() == web_logger.BASE_DIR / "logs"
    assert log_file_path(date(2026, 10, 27)).name == "web_20261027.log"


def test_api_call_writes_start_and_end(client: TestClient, conn: psycopg.Connection[DictRow]) -> None:
    """API を呼ぶと、開始(API とパラメータ)と終了(処理時間)を、logs の今日のファイルに出す。"""
    res = client.get("/api/prediction-dates?from=2026-07-01")
    assert res.status_code == 200
    lines = read_log().splitlines()
    start = [ln for ln in lines if "API開始" in ln]
    end = [ln for ln in lines if "API終了" in ln]
    assert len(start) == 1
    assert start[0].endswith(
        "INFO  - API開始 GET /api/prediction-dates 処理=status.get_prediction_dates 引数={from=2026-07-01, to=None}"
    )
    assert len(end) == 1
    assert "API終了 GET /api/prediction-dates 処理時間=" in end[0]
    assert end[0].endswith("秒")
    # DB の接続は引数に出さない
    assert "Connection" not in "\n".join(lines)


def test_api_without_args(client: TestClient) -> None:
    """引数がない API は、引数={} と出す。"""
    client.get("/api/settings")
    assert "API開始 GET /api/settings 処理=settings.get_settings 引数={}" in read_log()


def test_unknown_api_is_logged(client: TestClient) -> None:
    """存在しない API の呼び出しも、パスを付けて出す。"""
    client.post("/api/nothing/here")
    assert "API開始 POST /api/nothing/here 処理=main.api_not_found 引数={rest='nothing/here'}" in read_log()


class Item(BaseModel):
    """テスト用の入力の本文。"""

    code: str
    price: float


def make_app() -> FastAPI:
    """デコレーターを付けた API を持つ、テスト用のアプリ。"""
    setup_logging()
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/api/items/{item_id}")
    @log_api
    def post_item(item_id: int, item: Item, note: Annotated[str | None, Query()] = None) -> dict[str, int]:
        return {"item_id": item_id}

    @app.get("/api/fail")
    @log_api
    def fail() -> None:
        raise ApiError(400, "入力が正しくありません")

    return app


def test_body_and_path_params_are_logged() -> None:
    """パスの値、クエリ、入力の本文(項目の一覧)も出す。"""
    with TestClient(make_app()) as c:
        res = c.post("/api/items/7?note=メモ", json={"code": "13010", "price": 1234.5})
    assert res.json() == {"item_id": 7}
    assert (
        "API開始 POST /api/items/7 処理=test_logger.post_item "
        "引数={item_id=7, item={'code': '13010', 'price': 1234.5}, note='メモ'}"
    ) in read_log()


def test_error_also_logs_elapsed_time() -> None:
    """エラーで終わったときも、処理時間を出す(エラーはそのまま返す)。"""
    with TestClient(make_app()) as c:
        res = c.get("/api/fail")
    assert res.status_code == 400
    log = read_log()
    assert "API開始 GET /api/fail 処理=test_logger.fail 引数={}" in log
    assert "API終了(エラー) GET /api/fail 処理時間=" in log


def test_called_without_request_uses_function_name() -> None:
    """FastAPI を通さずに呼んだとき(リクエストがないとき)は、処理の名前を出す。"""
    setup_logging()

    @log_api
    def plain(x: int) -> int:
        return x * 2

    assert plain(x=3) == 6
    assert "API開始 test_logger.plain 処理=test_logger.plain 引数={x=3}" in read_log()


def test_setup_logging_twice_does_not_duplicate() -> None:
    """何度呼んでも出力先は2つ(ファイルとコンソール)のまま。上のロガーには流さない。"""
    setup_logging()
    setup_logging()
    own = [h for h in web_logger.logger.handlers if getattr(h, "_stock_prediction_web_handler", False)]
    assert len(own) == 2
    assert web_logger.logger.propagate is False
    assert web_logger.logger.level == logging.INFO


def test_file_switches_when_date_changes(tmp_path: Path) -> None:
    """日付が変わったら、次の行から新しい日のファイルに書く。"""
    days = [date(2026, 10, 27)]
    handler = DailyFileHandler(today=lambda: days[0])
    handler.setFormatter(logging.Formatter("%(message)s"))
    log = logging.getLogger("test_daily_file")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    try:
        log.info("1日目")
        days[0] = date(2026, 10, 28)
        log.info("2日目")
    finally:
        log.removeHandler(handler)
        handler.close()
    assert log_file_path(date(2026, 10, 27)).read_text(encoding="utf-8") == "1日目\n"
    assert log_file_path(date(2026, 10, 28)).read_text(encoding="utf-8") == "2日目\n"


def test_delete_old_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    """保持日数(今日を含めて30日)を過ぎた web_YYYYMMDD.log だけを削除する。"""
    folder = web_logger.log_dir()
    folder.mkdir(parents=True)
    today = date(2026, 10, 27)
    keep = [log_file_path(date(2026, 10, 27)), log_file_path(date(2026, 9, 28))]  # 今日と、29日前
    old = [log_file_path(date(2026, 9, 27)), log_file_path(date(2025, 1, 1))]  # 30日前より古い
    other = [folder / "web_abc.log", folder / "memo.txt"]
    for p in [*keep, *old, *other]:
        p.write_text("x", encoding="utf-8")
    assert delete_old_logs(today) == 2
    assert all(p.exists() for p in [*keep, *other])
    assert not any(p.exists() for p in old)


def test_delete_old_logs_without_folder() -> None:
    """ログのフォルダがまだなければ、何もしない。"""
    assert delete_old_logs(date(2026, 10, 27)) == 0


def test_format_args_extra_names_go_last() -> None:
    """関数の引数の一覧にない名前は、後ろに並べる。"""
    import inspect

    def f(a: int, b: int) -> None: ...

    assert web_logger.format_args(inspect.signature(f), {"z": 1, "b": 2, "a": 3}) == "{a=3, b=2, z=1}"
