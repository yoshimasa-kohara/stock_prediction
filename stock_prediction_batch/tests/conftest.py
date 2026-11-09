"""テストの共通の準備。

- DBは本番とは別のテスト用のDB(既定は stock_prediction_test)を使い、実際にSQLを実行して確かめる。
  接続先は .env の DB_HOST〜DB_PASSWORD を元にし、環境変数 TEST_DB_HOST、TEST_DB_PORT、TEST_DB_NAME、
  TEST_DB_USER、TEST_DB_PASSWORD があればそれを使う。テスト用のDBは先に作っておく。
    例: CREATE DATABASE stock_prediction_test OWNER <ユーザー名> ENCODING 'UTF8' TEMPLATE template0;
- テストを始めるときに、テスト用のDBのテーブルを作り直す(etc/db/sql/create_tables.sql を実行する)。
- J-Quants API、メールの送信、Yahoo Finance(yfinance。日経平均)は本物を呼ばず、代わりの部品に差し替える。
"""

import os
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

from dotenv import dotenv_values

_BASE_DIR = Path(__file__).resolve().parent.parent
_env_file = {k: (v or "") for k, v in dotenv_values(_BASE_DIR / ".env", encoding="utf-8").items()}

TEST_DB_NAME = os.environ.get("TEST_DB_NAME", "stock_prediction_test")
if TEST_DB_NAME == "stock_prediction":
    raise RuntimeError("テストに本番のDB(stock_prediction)は使えません。TEST_DB_NAME を変えてください")

# 他のモジュールが .env を読む前に、テスト用の値を環境変数に入れる(環境変数が優先される)
os.environ["DB_HOST"] = os.environ.get("TEST_DB_HOST", _env_file.get("DB_HOST") or "localhost")
os.environ["DB_PORT"] = os.environ.get("TEST_DB_PORT", _env_file.get("DB_PORT") or "5432")
os.environ["DB_NAME"] = TEST_DB_NAME
os.environ["DB_USER"] = os.environ.get("TEST_DB_USER", _env_file.get("DB_USER") or "postgres")
os.environ["DB_PASSWORD"] = os.environ.get("TEST_DB_PASSWORD", _env_file.get("DB_PASSWORD") or "")
os.environ["JQUANTS_API_KEY"] = "test-api-key"
os.environ["GMAIL_ADDRESS"] = "sender@example.com"
os.environ["GMAIL_APP_PASSWORD"] = "test-app-password"
os.environ["MAIL_TO"] = ""
os.environ["ADMIN_MAIL_TO"] = ""

import pandas as pd  # noqa: E402
import psycopg  # noqa: E402
import pytest  # noqa: E402
from psycopg.rows import DictRow  # noqa: E402

import const  # noqa: E402
from common import db, logger, mailer, rate_limit  # noqa: E402
from core import cleanup_core, index_collect_core  # noqa: E402


def fake_index_history(ticker: str, start: date, end: date) -> pd.DataFrame:
    """yfinance の代わり。start 〜 end の前日の平日に、終値 30,000 の行を返す(日付は東京の時刻帯付き)。"""
    days = pd.bdate_range(start, end - timedelta(days=1), tz="Asia/Tokyo")
    return pd.DataFrame({"Open": 30000.0, "High": 30100.0, "Low": 29900.0, "Close": 30000.0}, index=days)


class _NoSendSMTP:
    """本物のメールを送らないための、SMTP の代わり(差し替えていないテストで使われる)。"""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def __enter__(self) -> "_NoSendSMTP":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def starttls(self) -> None:
        pass

    def login(self, *args: object) -> None:
        pass

    def send_message(self, message: object) -> None:
        pass


_CREATE_SQL = _BASE_DIR / "etc" / "db" / "sql" / "create_tables.sql"

# テーブルの一覧
TABLES: list[str] = [
    "stock_master",
    "daily_price",
    "trading_calendar",
    "fetched_date",
    "model",
    "prediction",
    "verify_daily_summary",
    "verify_period_summary",
    "holding",
    "run_history",
    "daily_valuation",
    "fin_summary",
    "earnings_date",
    "verify_calibration_summary",
    "index_daily_price",
]


@pytest.fixture(scope="session", autouse=True)
def _create_tables() -> None:
    """テストを始めるときに、テスト用のDBのテーブルを作り直す。"""
    with db.connect() as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
        sql = _CREATE_SQL.read_text(encoding="utf-8")
        # psql 用の SET client_encoding は、そのまま実行しても問題ない
        conn.execute(sql.encode("utf-8"))


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """テストごとに、ログの出力先・待ち時間・リミッターを分ける。"""
    monkeypatch.setattr(const, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(const, "MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setattr(const, "BACKUP_DIR", str(tmp_path / "backup"))
    # 学習は小さい設定で、すぐ終わるようにする
    monkeypatch.setattr(const, "LGBM_PARAMS", {**const.LGBM_PARAMS, "min_data_in_leaf": 20, "num_leaves": 15})
    monkeypatch.setattr(const, "LGBM_MAX_ROUNDS", 30)
    monkeypatch.setattr(const, "LGBM_EARLY_STOPPING_ROUNDS", 5)
    monkeypatch.setattr(const, "BUSINESS_DAY_SOURCE", const.BUSINESS_DAY_SOURCE_RULE)
    # 本番は INFO にした。テストでは DEBUG のログ(銘柄ごとの出力など)も確かめるので DEBUG にする
    monkeypatch.setattr(const, "LOG_LEVEL", "DEBUG")
    # 本番は 0.5秒おき。テストでは1分あたりの上限だけを確かめるので、間隔は0にする
    monkeypatch.setattr(const, "API_REQUEST_INTERVAL_SEC", 0)
    # 本物のメールを送らず、やり直しでも待たない
    monkeypatch.setattr(mailer, "smtp_class", _NoSendSMTP)
    monkeypatch.setattr(const, "CLEANUP_DELETE_RETRY_WAIT_SEC", 0)
    monkeypatch.setattr(cleanup_core, "sleep", lambda seconds: None)
    # 本物の Yahoo Finance に接続しない。平日の行を返す偽物にする(日経平均の警告が出ないように)。待たない
    monkeypatch.setattr(index_collect_core, "fetch_index_history", fake_index_history)
    monkeypatch.setattr(index_collect_core, "sleep", lambda seconds: None)
    logger.reset_handlers()
    rate_limit.reset_limiter()
    # 待つ処理は実際には待たず、時計だけを進める(リミッターが待ち続けないように)
    now = [1000.0]

    def fake_sleep(seconds: float) -> None:
        now[0] += seconds

    monkeypatch.setattr(rate_limit, "sleep", fake_sleep)
    monkeypatch.setattr(rate_limit, "clock", lambda: now[0])
    yield
    logger.reset_handlers()
    rate_limit.reset_limiter()


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[DictRow]]:
    """全てのテーブルを空にしてから、テスト用のDBの接続を渡す。"""
    with db.connect() as c:
        c.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY")
        yield c
