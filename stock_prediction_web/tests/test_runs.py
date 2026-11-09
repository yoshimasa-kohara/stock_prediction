"""GET /api/runs(実行履歴)のテスト。"""

from datetime import date, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow

import const
from api import web_const
from api.routers import runs


def add_run(
    conn: psycopg.Connection[DictRow],
    process: str,
    status: str,
    target: date,
    started_hours_ago: float,
    error: str | None = None,
) -> int:
    finished = "NULL" if status == const.RUN_STATUS_RUNNING else "now() - make_interval(secs => %(fin)s)"
    row = conn.execute(
        f"""
        INSERT INTO run_history (batch_type, process_name, target_date, status, started_at, finished_at,
                                 record_count, error_code, error_message)
        VALUES ('DAILY', %(p)s, %(t)s, %(s)s, now() - make_interval(secs => %(start)s), {finished}, 10,
                %(code)s, %(err)s)
        RETURNING run_id
        """,
        {
            "p": process,
            "t": target,
            "s": status,
            "start": started_hours_ago * 3600,
            "fin": started_hours_ago * 3600 - 83,
            "code": "11110" if error else None,
            "err": error,
        },
    ).fetchone()
    assert row is not None
    return int(row["run_id"])


def test_runs_filter_by_target_date(client: TestClient, conn: psycopg.Connection[DictRow]) -> None:
    """対象の日付(target_date)で絞り、開始日時の新しい順に返す。かかった時間は秒で返す。"""
    a = add_run(conn, const.PROCESS_COLLECT, const.RUN_STATUS_SUCCESS, date(2026, 7, 22), 3)
    b = add_run(conn, const.PROCESS_PREDICT, const.RUN_STATUS_FAILED, date(2026, 7, 21), 1, error="失敗しました")
    add_run(conn, const.PROCESS_VERIFY, const.RUN_STATUS_SUCCESS, date(2026, 7, 10), 2)
    body = client.get("/api/runs?from=2026-07-16&to=2026-07-22").json()
    assert body["total"] == 2
    assert body["has_more"] is False
    assert [r["run_id"] for r in body["rows"]] == [b, a]
    assert body["rows"][0]["error_message"] == "失敗しました"
    assert body["rows"][0]["error_code"] == "11110"
    assert body["rows"][1]["elapsed_sec"] == pytest.approx(83, abs=1)


def test_runs_filters(client: TestClient, conn: psycopg.Connection[DictRow]) -> None:
    """処理と状態で絞る。実行中の行は、かかった時間が null。"""
    add_run(conn, const.PROCESS_COLLECT, const.RUN_STATUS_SUCCESS, date(2026, 7, 22), 3)
    running = add_run(conn, const.PROCESS_COLLECT, const.RUN_STATUS_RUNNING, date(2026, 7, 22), 0.1)
    add_run(conn, const.PROCESS_PREDICT, const.RUN_STATUS_RUNNING, date(2026, 7, 22), 0.1)
    body = client.get("/api/runs?from=2026-07-22&to=2026-07-22&process=COLLECT&status=RUNNING").json()
    assert [r["run_id"] for r in body["rows"]] == [running]
    assert body["rows"][0]["elapsed_sec"] is None
    assert body["rows"][0]["finished_at"] is None


def test_runs_default_period(
    client: TestClient, conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    """期間を省略すると、今日までの RUNS_DEFAULT_DAYS 日。"""
    monkeypatch.setattr(runs, "today", lambda: date(2026, 7, 22))
    add_run(conn, const.PROCESS_COLLECT, const.RUN_STATUS_SUCCESS, date(2026, 7, 22) - timedelta(days=6), 3)
    add_run(conn, const.PROCESS_COLLECT, const.RUN_STATUS_SUCCESS, date(2026, 7, 22) - timedelta(days=7), 3)
    body = client.get("/api/runs").json()
    assert body["date_from"] == "2026-07-16"
    assert body["date_to"] == "2026-07-22"
    assert body["total"] == 1


def test_runs_paging(client: TestClient, conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> None:
    """RUNS_PAGE_SIZE 件ずつ返し、offset で続きを読む。"""
    monkeypatch.setattr(web_const, "RUNS_PAGE_SIZE", 2)
    for i in range(5):
        add_run(conn, const.PROCESS_COLLECT, const.RUN_STATUS_SUCCESS, date(2026, 7, 22), 5 - i)
    first = client.get("/api/runs?from=2026-07-22&to=2026-07-22").json()
    assert len(first["rows"]) == 2
    assert first["has_more"] is True
    last = client.get("/api/runs?from=2026-07-22&to=2026-07-22&offset=4").json()
    assert len(last["rows"]) == 1
    assert last["has_more"] is False


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("process=NOPE", "処理の指定が正しくありません(NOPE)"),
        ("status=NOPE", "状態の指定が正しくありません(NOPE)"),
        ("offset=-1", "offset は 0 以上を指定してください"),
        ("from=2026-07-23&to=2026-07-22", "開始日が終了日より後になっています"),
    ],
)
def test_runs_bad_input(client: TestClient, query: str, message: str) -> None:
    res = client.get(f"/api/runs?{query}")
    assert res.status_code == 400
    assert res.json() == {"detail": message}
