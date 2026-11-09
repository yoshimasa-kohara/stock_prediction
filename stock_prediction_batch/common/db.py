"""DB接続。

psycopg 3 を使い、SQL文を直接書く。ORM は使わない。
接続は自動コミット(autocommit)にし、まとめて行う書き込みは `with conn.transaction():` で囲む。
こうすると、トランザクションの範囲がコードの見た目どおりになる。
"""

import psycopg
from psycopg.rows import DictRow, dict_row

from common.env import get_env

# 接続したときに設定するタイムゾーン
DB_TIMEZONE: str = "Asia/Tokyo"


def connect() -> psycopg.Connection[DictRow]:
    """DBに接続する。.env の DB_HOST〜DB_PASSWORD を使う。

    Returns:
        自動コミットの接続。行は辞書(カラム名 → 値)で返る
    """
    return psycopg.connect(
        host=get_env("DB_HOST"),
        port=get_env("DB_PORT"),
        dbname=get_env("DB_NAME"),
        user=get_env("DB_USER"),
        password=get_env("DB_PASSWORD", required=False),
        options=f"-c TimeZone={DB_TIMEZONE}",
        autocommit=True,
        row_factory=dict_row,
    )
