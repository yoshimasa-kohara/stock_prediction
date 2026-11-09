"""GET /api/runs: 実行履歴(7章 No.13)。

対象の日付の条件は、run_history の target_date(処理の対象の日付)で絞る(R1 の案A)。
開始日時の新しい順に、web_const.RUNS_PAGE_SIZE(100)件ずつ返す。
"""

from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

import const
from api import web_const
from api.common import check_range, today
from api.deps import Conn
from api.errors import ApiError
from api.logger import log_api

router = APIRouter()


class RunRow(BaseModel):
    """実行履歴の1行。elapsed_sec は終了 − 開始(秒)。実行中は None。"""

    run_id: int
    batch_type: str
    process_name: str
    target_date: date
    status: str
    started_at: datetime
    finished_at: datetime | None
    elapsed_sec: float | None
    record_count: int | None
    error_code: str | None
    error_message: str | None


class RunsResponse(BaseModel):
    """/api/runs の返り値。has_more が True なら、offset を足して続きを読める。"""

    date_from: date
    date_to: date
    total: int
    offset: int
    has_more: bool
    rows: list[RunRow]


@router.get("/api/runs", response_model=RunsResponse)
@log_api
def get_runs(
    conn: Conn,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    process: str | None = None,
    status: str | None = None,
    offset: int = 0,
) -> RunsResponse:
    """実行履歴を返す。

    Args:
        date_from: 対象の日付(target_date)がこの日以降。省略すると、終了日の web_const.RUNS_DEFAULT_DAYS − 1 日前
        date_to: 対象の日付がこの日以前。省略すると今日
        process: 処理の名前(COLLECT など)。省略すると全て
        status: 状態(SUCCESS など)。省略すると全て
        offset: 何件目から返すか(0 から)
    """
    end = date_to or today()
    start = date_from or end - timedelta(days=web_const.RUNS_DEFAULT_DAYS - 1)
    check_range(start, end)
    if process and process not in const.PROCESS_LABELS:
        raise ApiError(400, f"処理の指定が正しくありません({process})")
    if status and status not in web_const.RUN_STATUS_LABELS:
        raise ApiError(400, f"状態の指定が正しくありません({status})")
    if offset < 0:
        raise ApiError(400, "offset は 0 以上を指定してください")
    where = "target_date BETWEEN %(start)s AND %(end)s"
    params: dict[str, object] = {"start": start, "end": end, "limit": web_const.RUNS_PAGE_SIZE, "offset": offset}
    if process:
        where += " AND process_name = %(process)s"
        params["process"] = process
    if status:
        where += " AND status = %(status)s"
        params["status"] = status
    total_row = conn.execute(f"SELECT count(*) AS n FROM run_history WHERE {where}", params).fetchone()
    total = int(total_row["n"]) if total_row else 0
    rows = conn.execute(
        f"""
        SELECT run_id, batch_type, process_name, target_date, status, started_at, finished_at,
               EXTRACT(EPOCH FROM finished_at - started_at)::float8 AS elapsed_sec,
               record_count, error_code, error_message
        FROM run_history
        WHERE {where}
        ORDER BY started_at DESC, run_id DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    ).fetchall()
    return RunsResponse(
        date_from=start,
        date_to=end,
        total=total,
        offset=offset,
        has_more=offset + len(rows) < total,
        rows=[RunRow(**r) for r in rows],
    )
