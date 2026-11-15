"""テスト用の DB への切り替え(バッチのコードと .env は変えない)。

バッチの common.env は、同じ名前の環境変数があれば .env より優先する。そこで、DB に接続する前に
環境変数 DB_NAME をテスト用の DB の名前にする(tests/conftest.py と同じ方法)。
接続先の名前は環境変数 TEST_DB_NAME(既定は stock_prediction_test)。TEST_DB_HOST・TEST_DB_PORT・TEST_DB_USER・
TEST_DB_PASSWORD があれば、それも使う(なければ .env の値)。
"""

import os

import psycopg
from psycopg.rows import DictRow

# 既定のテスト用の DB(バッチと web のテストと同じ)
DEFAULT_TEST_DB_NAME: str = "stock_prediction_test"
# 本番の DB の名前(この名前には切り替えない)
PRODUCTION_DB_NAME: str = "stock_prediction"


def use_test_db() -> str:
    """環境変数 DB_NAME などをテスト用の DB にし、その名前を返す。

    Raises:
        RuntimeError: TEST_DB_NAME に本番の DB の名前が指定されたとき
    """
    name = os.environ.get("TEST_DB_NAME", DEFAULT_TEST_DB_NAME)
    if name == PRODUCTION_DB_NAME:
        raise RuntimeError("画面イメージの撮影に本番のDB(stock_prediction)は使えません。TEST_DB_NAME を変えてください")
    os.environ["DB_NAME"] = name
    for key in ("HOST", "PORT", "USER", "PASSWORD"):
        value = os.environ.get(f"TEST_DB_{key}")
        if value is not None:
            os.environ[f"DB_{key}"] = value
    return name


def check_test_db(conn: psycopg.Connection[DictRow]) -> str:
    """接続している DB の名前を確かめ、本番の DB なら止める。名前を返す。"""
    row = conn.execute("SELECT current_database() AS name").fetchone()
    name = "" if row is None else str(row["name"])
    if name == PRODUCTION_DB_NAME:
        raise RuntimeError("本番のDB(stock_prediction)に接続しています。処理を止めます")
    return name
