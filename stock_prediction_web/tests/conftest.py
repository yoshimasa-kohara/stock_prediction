"""API のテストの共通の準備(バッチの tests/conftest.py と同じ考え方)。

- DB は本番とは別のテスト用の DB(既定は stock_prediction_test。バッチのテストと同じ)を使い、
  実際に SQL を実行して確かめる。
  接続先は stock_prediction_batch の .env の DB_HOST〜DB_PASSWORD を元にし、環境変数 TEST_DB_HOST、TEST_DB_PORT、
  TEST_DB_NAME、TEST_DB_USER、TEST_DB_PASSWORD があればそれを使う。
- テストを始めるときに、テスト用の DB のテーブルを作り直す(バッチの etc/db/sql/create_tables.sql を実行する)。
- このフォルダには __init__.py を置かない(バッチの tests パッケージと名前がぶつからないようにするため)。
"""

import os
from collections.abc import Iterator
from pathlib import Path

from dotenv import dotenv_values

# stock_prediction_batch のフォルダ(.pth で読み込むバッチの場所)
_BATCH_DIR = Path(__file__).resolve().parent.parent.parent / "stock_prediction_batch"
_env_file = {k: (v or "") for k, v in dotenv_values(_BATCH_DIR / ".env", encoding="utf-8").items()}

TEST_DB_NAME = os.environ.get("TEST_DB_NAME", "stock_prediction_test")
if TEST_DB_NAME == "stock_prediction":
    raise RuntimeError("テストに本番のDB(stock_prediction)は使えません。TEST_DB_NAME を変えてください")

# バッチの common.env が .env を読む前に、テスト用の値を環境変数に入れる(環境変数が優先される)
os.environ["DB_HOST"] = os.environ.get("TEST_DB_HOST", _env_file.get("DB_HOST") or "localhost")
os.environ["DB_PORT"] = os.environ.get("TEST_DB_PORT", _env_file.get("DB_PORT") or "5432")
os.environ["DB_NAME"] = TEST_DB_NAME
os.environ["DB_USER"] = os.environ.get("TEST_DB_USER", _env_file.get("DB_USER") or "postgres")
os.environ["DB_PASSWORD"] = os.environ.get("TEST_DB_PASSWORD", _env_file.get("DB_PASSWORD") or "")

import psycopg  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from psycopg.rows import DictRow  # noqa: E402

import const  # noqa: E402
from api import logger as web_logger  # noqa: E402
from api import web_const  # noqa: E402
from api.main import create_app  # noqa: E402
from common import db  # noqa: E402

_CREATE_SQL = _BATCH_DIR / "etc" / "db" / "sql" / "create_tables.sql"

# テーブルの一覧(バッチの tests/conftest.py と同じ)
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
    """テストを始めるときに、テスト用の DB のテーブルを作り直す。"""
    with db.connect() as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
        conn.execute(_CREATE_SQL.read_text(encoding="utf-8").encode("utf-8"))


@pytest.fixture(autouse=True)
def _isolate_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """テストごとに、ログの出力先を一時フォルダに分ける(stock_prediction_web\\logs に書かない)。"""
    web_logger.reset_logging()
    monkeypatch.setattr(web_const, "LOG_DIR", str(tmp_path / "logs"))
    # 架空のデータには取引カレンダーがないので、営業日は決まりで求める(バッチの tests/conftest.py と同じ)
    monkeypatch.setattr(const, "BUSINESS_DAY_SOURCE", const.BUSINESS_DAY_SOURCE_RULE)
    yield
    web_logger.reset_logging()


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[DictRow]]:
    """全てのテーブルを空にしてから、テスト用の DB の接続を渡す。"""
    with db.connect() as c:
        c.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY")
        yield c


@pytest.fixture
def client(conn: psycopg.Connection[DictRow]) -> Iterator[TestClient]:
    """API を呼ぶテスト用のクライアント(テーブルは空の状態から始める)。"""
    # 想定していないエラーも、テストでは例外にせず 500 の返り値として確かめる
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def sample(conn: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    """架空のデータ(tests/web_sample.py)を入れた接続。"""
    from web_sample import insert_web_sample

    insert_web_sample(conn)
    return conn
