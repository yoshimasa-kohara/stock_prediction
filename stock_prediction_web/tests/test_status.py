"""GET /api/status と GET /api/prediction-dates のテスト。"""

from datetime import date

import psycopg
from fastapi.testclient import TestClient
from psycopg.rows import DictRow

import const


def add_prediction(conn: psycopg.Connection[DictRow], code: str, pred_date: date, model_version: str) -> None:
    """予測の行を1つ登録する(予測日数は主となる日数)。"""
    conn.execute(
        """
        INSERT INTO prediction (code, pred_date, horizon, target_date, up_prob, rel_prob, pred_price, base_price,
                                model_version)
        VALUES (%s, %s, %s, %s, 0.6, 0.7, 1100, 1000, %s)
        """,
        (code, pred_date, const.MAIN_HORIZON, pred_date, model_version),
    )


def add_model(conn: psycopg.Connection[DictRow], model_version: str, base_date: date, model_type: str) -> None:
    """モデルの行を1つ登録する。"""
    conn.execute(
        """
        INSERT INTO model (model_version, horizon, model_type, base_date, train_start_date, train_end_date,
                           train_rows, feature_names, file_path)
        VALUES (%s, %s, %s, %s, '2024-01-01', %s, 100, '[]', 'models/x.txt')
        """,
        (model_version, const.MAIN_HORIZON, model_type, base_date, base_date),
    )


def add_run(
    conn: psycopg.Connection[DictRow],
    process_name: str,
    status: str,
    started_hours_ago: float,
    batch_type: str = const.BATCH_TYPE_DAILY,
    target_date: date = date(2026, 7, 22),
) -> int:
    """実行履歴の行を1つ登録し、実行IDを返す。開始は今から started_hours_ago 時間前。"""
    finished = None if status == const.RUN_STATUS_RUNNING else "now()"
    row = conn.execute(
        f"""
        INSERT INTO run_history (batch_type, process_name, target_date, status, started_at, finished_at)
        VALUES (%s, %s, %s, %s, now() - make_interval(secs => %s), {finished or "NULL"})
        RETURNING run_id
        """,
        (batch_type, process_name, target_date, status, started_hours_ago * 3600),
    ).fetchone()
    assert row is not None
    return int(row["run_id"])


def test_status_empty_db(client: TestClient) -> None:
    """予測も実行履歴もないときは、値を null で返す(画面は「予測はありません」と出す)。"""
    res = client.get("/api/status")
    assert res.status_code == 200
    assert res.json() == {
        "latest_pred_date": None,
        "model_version": None,
        "model_base_date": None,
        "running": [],
        "latest_daily": None,
        "latest_weekly": None,
    }


def test_status_latest_prediction_and_model(client: TestClient, conn: psycopg.Connection[DictRow]) -> None:
    """最新の予測日と、その日のモデルバージョン、学習の基準日を返す。"""
    add_prediction(conn, "11110", date(2026, 7, 21), "20260711")
    add_prediction(conn, "11110", date(2026, 7, 22), "20260718")
    add_prediction(conn, "11120", date(2026, 7, 22), "20260718")
    add_model(conn, "20260711", date(2026, 7, 11), const.MODEL_TYPE_UP_PROB)
    add_model(conn, "20260718", date(2026, 7, 18), const.MODEL_TYPE_UP_PROB)
    add_model(conn, "20260718", date(2026, 7, 18), const.MODEL_TYPE_PRICE)
    body = client.get("/api/status").json()
    assert body["latest_pred_date"] == "2026-07-22"
    assert body["model_version"] == "20260718"
    assert body["model_base_date"] == "2026-07-18"


def test_status_model_row_missing(client: TestClient, conn: psycopg.Connection[DictRow]) -> None:
    """モデルの行がクリーニングで消えていても、予測日とモデルバージョンは返す。"""
    add_prediction(conn, "11110", date(2026, 7, 22), "20260718")
    body = client.get("/api/status").json()
    assert body["latest_pred_date"] == "2026-07-22"
    assert body["model_version"] == "20260718"
    assert body["model_base_date"] is None


def test_status_running_excludes_stale(client: TestClient, conn: psycopg.Connection[DictRow]) -> None:
    """実行中の行を返す。開始から23時間を過ぎた RUNNING の行(途中で止まった残り)は含めない。"""
    add_run(conn, const.PROCESS_COLLECT, const.RUN_STATUS_RUNNING, const.RUNNING_STALE_HOURS + 1)
    batch_id = add_run(conn, const.PROCESS_DAILY_BATCH, const.RUN_STATUS_RUNNING, 0.5)
    collect_id = add_run(conn, const.PROCESS_PREDICT, const.RUN_STATUS_RUNNING, 0.1)
    add_run(conn, const.PROCESS_VERIFY, const.RUN_STATUS_SUCCESS, 0.2)
    running = client.get("/api/status").json()["running"]
    assert [r["run_id"] for r in running] == [batch_id, collect_id]
    assert running[0]["process_name"] == const.PROCESS_DAILY_BATCH
    assert running[0]["batch_type"] == const.BATCH_TYPE_DAILY
    assert running[0]["target_date"] == "2026-07-22"
    assert running[0]["started_at"]


def test_status_latest_daily_and_weekly(client: TestClient, conn: psycopg.Connection[DictRow]) -> None:
    """日次バッチ・週次バッチは、開始が一番新しい行を返す。"""
    add_run(conn, const.PROCESS_DAILY_BATCH, const.RUN_STATUS_FAILED, 48)
    daily_id = add_run(conn, const.PROCESS_DAILY_BATCH, const.RUN_STATUS_SUCCESS, 24, target_date=date(2026, 7, 23))
    weekly_id = add_run(
        conn, const.PROCESS_WEEKLY_BATCH, const.RUN_STATUS_EMPTY, 72, batch_type=const.BATCH_TYPE_WEEKLY
    )
    body = client.get("/api/status").json()
    assert body["latest_daily"]["run_id"] == daily_id
    assert body["latest_daily"]["status"] == const.RUN_STATUS_SUCCESS
    assert body["latest_daily"]["target_date"] == "2026-07-23"
    assert body["latest_daily"]["finished_at"] is not None
    assert body["latest_weekly"]["run_id"] == weekly_id
    assert body["latest_weekly"]["status"] == const.RUN_STATUS_EMPTY


def test_status_latest_daily_running(client: TestClient, conn: psycopg.Connection[DictRow]) -> None:
    """実行中の日次バッチは、終了の時刻を null で返す。"""
    add_run(conn, const.PROCESS_DAILY_BATCH, const.RUN_STATUS_RUNNING, 0.1)
    body = client.get("/api/status").json()
    assert body["latest_daily"]["status"] == const.RUN_STATUS_RUNNING
    assert body["latest_daily"]["finished_at"] is None
    assert body["latest_weekly"] is None


def test_prediction_dates(client: TestClient, conn: psycopg.Connection[DictRow]) -> None:
    """予測がある日を、重複なしで古い順に返す。"""
    for d in [date(2026, 7, 22), date(2026, 7, 17), date(2026, 7, 21)]:
        add_prediction(conn, "11110", d, "20260711")
        add_prediction(conn, "11120", d, "20260711")
    res = client.get("/api/prediction-dates")
    assert res.status_code == 200
    assert res.json() == {"dates": ["2026-07-17", "2026-07-21", "2026-07-22"]}


def test_prediction_dates_range(client: TestClient, conn: psycopg.Connection[DictRow]) -> None:
    """from・to を指定すると、その範囲(両端を含む)だけを返す。"""
    for d in [date(2026, 7, 17), date(2026, 7, 21), date(2026, 7, 22), date(2026, 7, 23)]:
        add_prediction(conn, "11110", d, "20260711")
    assert client.get("/api/prediction-dates?from=2026-07-21&to=2026-07-22").json() == {
        "dates": ["2026-07-21", "2026-07-22"]
    }
    assert client.get("/api/prediction-dates?from=2026-07-22").json() == {"dates": ["2026-07-22", "2026-07-23"]}
    assert client.get("/api/prediction-dates?to=2026-07-18").json() == {"dates": ["2026-07-17"]}
    assert client.get("/api/prediction-dates?from=2026-08-01").json() == {"dates": []}


def test_prediction_dates_empty(client: TestClient) -> None:
    """予測がなければ空のリストを返す。"""
    assert client.get("/api/prediction-dates").json() == {"dates": []}


def test_prediction_dates_from_after_to(client: TestClient) -> None:
    """開始日が終了日より後なら 400 と日本語のメッセージ。"""
    res = client.get("/api/prediction-dates?from=2026-07-23&to=2026-07-22")
    assert res.status_code == 400
    assert res.json() == {"detail": "開始日が終了日より後になっています"}


def test_prediction_dates_invalid_date(client: TestClient) -> None:
    """日付の形が正しくなければ 422 と日本語のメッセージ(どの項目かを付ける)。"""
    res = client.get("/api/prediction-dates?from=2026-13-01")
    assert res.status_code == 422
    assert res.json() == {"detail": "入力の値が正しくありません(from)"}
