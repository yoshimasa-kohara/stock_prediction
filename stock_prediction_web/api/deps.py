"""API の関数に渡す共通の部品(DB の接続など)。"""

from collections.abc import Iterator
from typing import Annotated

import psycopg
from fastapi import Depends
from psycopg.rows import DictRow

from common import db


def get_conn() -> Iterator[psycopg.Connection[DictRow]]:
    """リクエストごとに DB に接続し、終わったら閉じる。

    バッチの common.db.connect を使う(同じ .env、同じユーザー)。
    接続は自動コミット。書き込みを伴う API は `with conn.transaction():` で囲む。
    """
    with db.connect() as conn:
        yield conn


# API の関数の引数に書く型(例: def get_status(conn: Conn) -> ...)
Conn = Annotated[psycopg.Connection[DictRow], Depends(get_conn)]
