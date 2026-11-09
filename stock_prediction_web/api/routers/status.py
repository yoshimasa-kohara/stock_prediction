"""GET /api/status: 最新の予測日とバッチの状態。
GET /api/prediction-dates: 予測がある日の一覧(日付の入力のカレンダー用)。

全画面の上の「バッチ実行中の表示」と、トップのカードに使う。
"""

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

import const
from api.deps import Conn
from api.errors import ApiError
from api.logger import log_api

router = APIRouter()


class RunningRun(BaseModel):
    """RUNNING(実行中)の実行履歴の行。"""

    run_id: int
    batch_type: str
    process_name: str
    target_date: date
    started_at: datetime


class LatestRun(BaseModel):
    """日次バッチ・週次バッチの最新の実行履歴。"""

    run_id: int
    process_name: str
    target_date: date
    status: str
    started_at: datetime
    finished_at: datetime | None


class StatusResponse(BaseModel):
    """/api/status の返り値。"""

    latest_pred_date: date | None
    model_version: str | None
    model_base_date: date | None
    running: list[RunningRun]
    latest_daily: LatestRun | None
    latest_weekly: LatestRun | None


class PredictionDatesResponse(BaseModel):
    """/api/prediction-dates の返り値。"""

    dates: list[date]


def load_running(conn: Conn) -> list[RunningRun]:
    """RUNNING(実行中)の行を、開始の古い順に返す。

    開始から const.RUNNING_STALE_HOURS(23時間)を過ぎた行は、途中で止まった残り(バッチが次に起動したときに
    失敗に更新する)なので、実行中としては扱わない。
    """
    rows = conn.execute(
        """
        SELECT run_id, batch_type, process_name, target_date, started_at
        FROM run_history
        WHERE status = %s AND started_at >= now() - make_interval(hours => %s)
        ORDER BY started_at
        """,
        (const.RUN_STATUS_RUNNING, const.RUNNING_STALE_HOURS),
    ).fetchall()
    return [RunningRun(**r) for r in rows]


def load_latest_run(conn: Conn, process_name: str) -> LatestRun | None:
    """指定した処理(DAILY_BATCH、WEEKLY_BATCH)の、開始が一番新しい行を返す。"""
    row = conn.execute(
        """
        SELECT run_id, process_name, target_date, status, started_at, finished_at
        FROM run_history
        WHERE process_name = %s
        ORDER BY started_at DESC, run_id DESC
        LIMIT 1
        """,
        (process_name,),
    ).fetchone()
    return LatestRun(**row) if row else None


def load_latest_prediction(conn: Conn) -> tuple[date | None, str | None, date | None]:
    """最新の予測日と、その日の予測に使ったモデルバージョン、学習の基準日を返す。"""
    row = conn.execute("SELECT max(pred_date) AS pred_date FROM prediction").fetchone()
    pred_date: date | None = row["pred_date"] if row else None
    if pred_date is None:
        return None, None, None
    row = conn.execute(
        "SELECT max(model_version) AS model_version FROM prediction WHERE pred_date = %s", (pred_date,)
    ).fetchone()
    model_version: str | None = row["model_version"] if row else None
    row = conn.execute(
        "SELECT min(base_date) AS base_date FROM model WHERE model_version = %s", (model_version,)
    ).fetchone()
    base_date: date | None = row["base_date"] if row else None
    return pred_date, model_version, base_date


@router.get("/api/status", response_model=StatusResponse)
@log_api
def get_status(conn: Conn) -> StatusResponse:
    """最新の予測日、実行中の処理、日次バッチ・週次バッチの最新の結果を返す。"""
    pred_date, model_version, base_date = load_latest_prediction(conn)
    return StatusResponse(
        latest_pred_date=pred_date,
        model_version=model_version,
        model_base_date=base_date,
        running=load_running(conn),
        latest_daily=load_latest_run(conn, const.PROCESS_DAILY_BATCH),
        latest_weekly=load_latest_run(conn, const.PROCESS_WEEKLY_BATCH),
    )


@router.get("/api/prediction-dates", response_model=PredictionDatesResponse)
@log_api
def get_prediction_dates(
    conn: Conn,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> PredictionDatesResponse:
    """予測がある日(prediction の pred_date)を、古い順に返す。

    予測日を選ぶカレンダーで、予測がない日(土日・祝日など)を選べないようにするために使う。
    pred_date の索引(idx_prediction_pred_date)を1日ずつたどるので、行が多くても速い。

    Args:
        date_from: この日以降(省略すると全て)。クエリの名前は from
        date_to: この日以前(省略すると全て)。クエリの名前は to
    """
    if date_from and date_to and date_from > date_to:
        raise ApiError(400, "開始日が終了日より後になっています")
    rows = conn.execute(
        """
        WITH RECURSIVE d AS (
            SELECT min(pred_date) AS v FROM prediction WHERE pred_date >= %(from)s
            UNION ALL
            SELECT (SELECT min(pred_date) FROM prediction WHERE pred_date > d.v)
            FROM d WHERE d.v IS NOT NULL AND d.v < %(to)s
        )
        SELECT v FROM d WHERE v IS NOT NULL AND v <= %(to)s ORDER BY v
        """,
        {"from": date_from or date.min, "to": date_to or date.max},
    ).fetchall()
    return PredictionDatesResponse(dates=[r["v"] for r in rows])
