"""common/run_history.py のテスト。"""

from datetime import date

import psycopg
import pytest
from psycopg.rows import DictRow

import const
from common import run_history as rh
from common.errors import BatchError
from common.logger import get_logger


def _row(conn: psycopg.Connection[DictRow], run_id: int) -> DictRow:
    row = conn.execute("SELECT * FROM run_history WHERE run_id = %s", (run_id,)).fetchone()
    assert row is not None
    return row


def test_開始と終了(conn: psycopg.Connection[DictRow]) -> None:
    run_id = rh.start_run("SINGLE", "COLLECT", date(2026, 7, 8))
    row = _row(conn, run_id)
    assert (row["status"], row["batch_type"], row["process_name"]) == ("RUNNING", "SINGLE", "COLLECT")
    assert row["target_date"] == date(2026, 7, 8)
    assert row["finished_at"] is None

    rh.finish_run(run_id, "SUCCESS", 4412)
    row = _row(conn, run_id)
    assert (row["status"], row["record_count"], row["error_code"]) == ("SUCCESS", 4412, None)
    assert row["finished_at"] is not None


def test_失敗はエラーの内容と銘柄コードを残す(conn: psycopg.Connection[DictRow]) -> None:
    run_id = rh.start_run("DAILY", "PREDICT", date(2026, 7, 8))
    rh.finish_run(run_id, "FAILED", None, "11110", "計算できません")
    row = _row(conn, run_id)
    assert (row["status"], row["error_code"], row["error_message"]) == ("FAILED", "11110", "計算できません")


def test_実行中の行を探す(conn: psycopg.Connection[DictRow]) -> None:
    assert rh.find_running() == []
    run_id = rh.start_run("DAILY", "DAILY_BATCH", date(2026, 7, 8))
    running = rh.find_running()
    assert [r["run_id"] for r in running] == [run_id]
    assert running[0]["process_name"] == "DAILY_BATCH"


def test_23時間を過ぎた実行中の行は失敗に更新する(conn: psycopg.Connection[DictRow]) -> None:
    old = rh.start_run("DAILY", "COLLECT", date(2026, 7, 7))
    new = rh.start_run("DAILY", "COLLECT", date(2026, 7, 8))
    conn.execute("UPDATE run_history SET started_at = now() - interval '24 hours' WHERE run_id = %s", (old,))
    assert rh.mark_stale_runs() == 1
    old_row = _row(conn, old)
    assert old_row["status"] == "FAILED"
    assert old_row["error_message"] == "途中で止まった実行履歴とみなして、失敗に更新しました"
    assert _row(conn, new)["status"] == "RUNNING"


def test_run_with_historyの成功(conn: psycopg.Connection[DictRow]) -> None:
    log = get_logger("収集", date(2026, 7, 8))
    result = rh.run_with_history(
        "SINGLE", "COLLECT", date(2026, 7, 8), log, lambda: rh.ProcessOutcome("結果", const.RUN_STATUS_EMPTY, 0)
    )
    assert result == "結果"
    row = conn.execute("SELECT * FROM run_history").fetchone()
    assert row is not None
    assert (row["status"], row["record_count"]) == ("EMPTY", 0)


def test_run_with_historyでBatchErrorはそのまま投げる(conn: psycopg.Connection[DictRow]) -> None:
    log = get_logger("予測", date(2026, 7, 8))

    def body() -> rh.ProcessOutcome[None]:
        raise BatchError("予測", "計算できません", "11110")

    with pytest.raises(BatchError) as e:
        rh.run_with_history("SINGLE", "PREDICT", date(2026, 7, 8), log, body)
    assert e.value.code == "11110"
    row = conn.execute("SELECT * FROM run_history").fetchone()
    assert row is not None
    assert (row["status"], row["error_code"], row["error_message"]) == ("FAILED", "11110", "計算できません")


def test_run_with_historyで想定外のエラーはBatchErrorにする(conn: psycopg.Connection[DictRow]) -> None:
    log = get_logger("収集", date(2026, 7, 8))

    def body() -> rh.ProcessOutcome[None]:
        raise ValueError("想定外")

    with pytest.raises(BatchError) as e:
        rh.run_with_history("SINGLE", "COLLECT", date(2026, 7, 8), log, body)
    assert (e.value.process_name, e.value.message, e.value.code) == ("収集", "想定外", None)
    row = conn.execute("SELECT * FROM run_history").fetchone()
    assert row is not None
    assert (row["status"], row["error_message"]) == ("FAILED", "想定外")


def test_実行履歴を登録できない場合もBatchErrorにする(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: object) -> int:
        raise psycopg.OperationalError("接続できません")

    monkeypatch.setattr(rh, "start_run", broken)
    log = get_logger("収集", date(2026, 7, 8))
    with pytest.raises(BatchError, match="実行履歴を登録できませんでした"):
        rh.run_with_history("SINGLE", "COLLECT", date(2026, 7, 8), log, lambda: rh.ProcessOutcome(None, "SUCCESS"))
