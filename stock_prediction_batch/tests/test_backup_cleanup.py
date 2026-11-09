"""core/backup_core.py、core/cleanup_core.py と、backup.py、cleanup.py のテスト。

pg_dump は、テストの環境にあれば本物を使う(なければ、そのテストは飛ばす)。NAS の代わりに一時フォルダを使う。
"""

import os
import shutil
import subprocess
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

import backup as backup_cli
import cleanup as cleanup_cli
import const
from common import run_history
from common.env import resolve_path
from common.errors import BatchError
from common.logger import get_logger, log_file_path
from core import backup_core, cleanup_core

D = date(2026, 7, 4)


def _pg_dump() -> str | None:
    found = shutil.which("pg_dump")
    for candidate in ([found] if found else []) + sorted(
        Path("/usr/lib/postgresql").glob("*/bin/pg_dump"), reverse=True
    ):
        if candidate and Path(str(candidate)).exists():
            return str(candidate)
    return None


PG_DUMP = _pg_dump()


def _model(conn: psycopg.Connection[DictRow], version: str, base: date) -> None:
    conn.execute(
        "INSERT INTO model (model_version, horizon, model_type, base_date, train_start_date, train_end_date,"
        " train_rows, feature_names, file_path) VALUES (%s, 5, 'UP_PROB', %s, %s, %s, 1, '[]', %s)",
        (version, base, base, base, f"{version}/上昇確率_5日後.txt"),
    )
    folder = resolve_path(const.MODEL_DIR) / version
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "上昇確率_5日後.txt").write_text("モデル", encoding="utf-8")


# =====================================================================
# バックアップ
# =====================================================================


@pytest.mark.skipif(PG_DUMP is None, reason="pg_dump がない")
def test_DBとモデルをNASに保存する(conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(const, "PG_DUMP_PATH", PG_DUMP)
    _model(conn, "20260627", date(2026, 6, 27))
    _model(conn, "20260704", D)
    _model(conn, "20260711", date(2026, 7, 11))  # 指定日より後のモデルはコピーしない
    folder = Path(const.BACKUP_DIR) / "20260704"
    folder.mkdir(parents=True)
    (folder / "古いファイル").write_text("x", encoding="utf-8")

    result = backup_core.run_backup(D)

    assert result.folder == folder and result.model_version == "20260704"
    assert sorted(p.name for p in folder.iterdir()) == ["models", "stock_prediction_20260704.dump"]
    assert (folder / "stock_prediction_20260704.dump").stat().st_size == result.dump_size > 0
    assert (folder / "models" / "20260704" / "上昇確率_5日後.txt").read_text(encoding="utf-8") == "モデル"
    assert not (Path(tempfile.gettempdir()) / "stock_prediction_20260704.dump").exists()
    row = conn.execute("SELECT status FROM run_history").fetchone()
    assert row is not None and row["status"] == "SUCCESS"


class FakeCompleted:
    def __init__(self, code: int, stderr: str = "") -> None:
        self.returncode = code
        self.stderr = stderr


def _fake_dump(code: int = 0, size: int = 10) -> Any:
    def run(command: list[str], **kwargs: Any) -> FakeCompleted:
        if code == 0:
            Path(command[command.index("-f") + 1]).write_bytes(b"x" * size)
        # DB のパスワード(テストでは conftest.py が環境変数 DB_PASSWORD に入れた値)を pg_dump に渡す
        assert kwargs["env"]["PGPASSWORD"] == os.environ["DB_PASSWORD"]
        return FakeCompleted(code, "pg_dump: error: 接続できません")

    return run


def test_補正用データもモデルと一緒にNASに保存する(
    conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backup_core, "run_command", _fake_dump())
    _model(conn, "20260704", D)
    data_dir = resolve_path(const.MODEL_DIR) / const.CALIBRATION_DATA_DIR
    data_dir.mkdir(parents=True)
    (data_dir / "上昇確率_5日後_確認用.csv.gz").write_bytes(b"data")
    (data_dir / "上昇確率_5日後_確認用.csv.gz.tmp").write_bytes(b"x")  # 書きかけの一時ファイルはコピーしない
    result = backup_core.run_backup(D)
    copied = result.folder / "models" / const.CALIBRATION_DATA_DIR
    assert [p.name for p in copied.iterdir()] == ["上昇確率_5日後_確認用.csv.gz"]
    assert (copied / "上昇確率_5日後_確認用.csv.gz").read_bytes() == b"data"
    assert (result.folder / "models" / "20260704" / "上昇確率_5日後.txt").is_file()


def test_モデルがなくてもDBは保存する(conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backup_core, "run_command", _fake_dump())
    result = backup_core.run_backup(D)
    assert result.model_version is None
    assert not (result.folder / "models").exists()


def test_pg_dumpが失敗したら失敗(conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backup_core, "run_command", _fake_dump(code=1))
    with pytest.raises(BatchError, match=r"pg_dump が失敗しました\(終了コード 1\): pg_dump: error"):
        backup_core.run_backup(D)


def test_pg_dumpがない場合は失敗(conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(const, "PG_DUMP_PATH", "/存在しない/pg_dump")
    monkeypatch.setattr(backup_core, "run_command", subprocess.run)
    with pytest.raises(BatchError, match="pg_dump を実行できませんでした"):
        backup_core.run_backup(D)


def test_NASに書き込めない場合は失敗(
    conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(backup_core, "run_command", _fake_dump())
    blocker = tmp_path / "ファイル"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(const, "BACKUP_DIR", str(blocker / "backup"))
    with pytest.raises(BatchError, match="バックアップの保存先に書き込めません"):
        backup_core.run_backup(D)


def test_コピーの大きさが違う場合は失敗(conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backup_core, "run_command", _fake_dump())
    monkeypatch.setattr(shutil, "copyfile", lambda src, dst: Path(dst).write_bytes(b"y"))
    with pytest.raises(BatchError, match=r"バックアップのコピーが正しくありません\(stock_prediction_20260704.dump\)"):
        backup_core.run_backup(D)


def test_一時ファイルを削除できなくても成功(conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backup_core, "run_command", _fake_dump())
    real_unlink = Path.unlink

    def broken_unlink(self: Path, missing_ok: bool = False) -> None:
        if self.name.endswith(".dump") and str(self).startswith(tempfile.gettempdir()):
            raise OSError("使用中")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", broken_unlink)
    backup_core.run_backup(D)
    from common.logger import log_file_path

    assert "一時ファイルを削除できませんでした" in log_file_path().read_text(encoding="utf-8")
    real_unlink(Path(tempfile.gettempdir()) / "stock_prediction_20260704.dump", missing_ok=True)


def test_大きさの表示() -> None:
    assert backup_core._human_size(500) == "500B"
    assert backup_core._human_size(2048) == "2.0KB"
    assert backup_core._human_size(5 * 1024**3) == "5.0GB"


def test_backup_pyの終了コード(conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backup_core, "run_command", _fake_dump())
    assert backup_cli.main(["--date", "2026-07-04"]) == 0
    monkeypatch.setattr(backup_core, "run_command", _fake_dump(code=1))
    assert backup_cli.main(["--date", "2026-07-04"]) == 1
    assert backup_cli.main(["--date", "x"]) == 1


# =====================================================================
# クリーニング
# =====================================================================


def _backup_succeeded(d: date) -> None:
    run_id = run_history.start_run("WEEKLY", "BACKUP", d)
    run_history.finish_run(run_id, "SUCCESS", 1)


def test_バックアップが成功していなければ何も削除しない(conn: psycopg.Connection[DictRow]) -> None:
    conn.execute("INSERT INTO daily_price (trade_date, code, adj_factor) VALUES ('1990-01-04', '11110', 1)")
    with pytest.raises(
        BatchError, match=r"同じ日付のバックアップが成功していないため、削除しません\(指定日: 2026-07-04\)"
    ):
        cleanup_core.run_cleanup(D)
    count = conn.execute("SELECT count(*) AS n FROM daily_price").fetchone()
    assert count is not None and count["n"] == 1


def test_古いデータとファイルを削除する(conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> None:
    _backup_succeeded(D)
    # 株価日足とバリュエーション指標と指数日足: 20年前(2006-07-04)より前を削除
    for d, code in ((date(2006, 7, 3), "1"), (date(2006, 7, 4), "2")):
        conn.execute("INSERT INTO daily_price (trade_date, code, adj_factor) VALUES (%s, %s, 1)", (d, code))
        conn.execute("INSERT INTO daily_valuation (trade_date, code) VALUES (%s, %s)", (d, code))
        conn.execute("INSERT INTO index_daily_price (index_code, trade_date, close) VALUES ('^N225', %s, 1)", (d,))
    # 予測・検証: 730日前(2024-07-04)より前で、検証済み・検証不能で、集計があるものだけ削除
    # (本番の保持期間は 7300日(20年)。ここでは削除の動きを確かめるため 730日にする)
    monkeypatch.setattr(const, "RETENTION_PREDICTION_DAYS", 730)
    old = date(2024, 7, 3)
    for code, status in (("A", "VERIFIED"), ("B", "UNVERIFIABLE"), ("C", "PENDING")):
        conn.execute(
            "INSERT INTO prediction (code, pred_date, horizon, target_date, up_prob, rel_prob, pred_price, base_price,"
            " model_version, verify_status) VALUES (%s, %s, 5, %s, 0.5, 0.5, 1, 1, 'v', %s)",
            (code, old, old, status),
        )
    conn.execute(
        "INSERT INTO prediction (code, pred_date, horizon, target_date, up_prob, rel_prob, pred_price, base_price,"
        " model_version, verify_status) VALUES ('D', %s, 10, %s, 0.5, 0.5, 1, 1, 'v', 'VERIFIED')",
        (old, old),
    )
    conn.execute(
        "INSERT INTO verify_daily_summary (pred_date, horizon, segment, target_date, model_version, verified_count,"
        " hit_count, up_count, beat_count, sum_change_rate, sum_excess_rate, sum_price_error_rate, unverifiable_count,"
        " sector_verified_count, sector_beat_count, sum_sector_excess_rate)"
        " VALUES (%s, 5, 'ALL', %s, 'v', 1, 1, 1, 1, 0, 0, 0, 1, 0, 0, 0)",
        (old, old),
    )
    # 実行履歴: 365日前より前を削除
    run_id = run_history.start_run("DAILY", "COLLECT", date(2025, 7, 1))
    conn.execute("UPDATE run_history SET started_at = %s WHERE run_id = %s", (datetime(2025, 7, 1, 19), run_id))
    # モデルとバックアップ: 指定日以前の12世代を残す(指定日より後のものは数えない)
    model_dir = resolve_path(const.MODEL_DIR)
    backup_dir = Path(const.BACKUP_DIR)
    stamps = [(D - timedelta(weeks=i)).strftime("%Y%m%d") for i in range(14)] + ["20260711", "メモ"]
    for parent in (model_dir, backup_dir):
        for s in stamps:
            (parent / s).mkdir(parents=True)
    # 補正用データのフォルダは、モデルの世代の削除で消えない
    calibration_dir = model_dir / const.CALIBRATION_DATA_DIR
    calibration_dir.mkdir()
    (calibration_dir / "上昇確率_5日後_確認用.csv.gz").write_bytes(b"data")
    # ログ: ファイル名の日付が30日前(2026-06-04)より前を削除
    log_dir = resolve_path(const.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    # 収集失敗の一覧と、全処理の通知の内容のファイルは、古くても消さない
    kept = ["収集失敗_20200101_2100.csv", "全処理_通知_2016-10-03_2026-10-30.txt"]
    for name in ("batch_20260603.log", "batch_20260604.log", "メモ.txt", *kept):
        (log_dir / name).write_text("x", encoding="utf-8")

    result = cleanup_core.run_cleanup(D)

    assert result.deleted_rows == {
        "株価日足": 1, "指数日足": 1, "予測・検証": 2, "実行履歴": 1, "バリュエーション指標": 1
    }  # fmt: skip
    index_left = conn.execute("SELECT trade_date FROM index_daily_price").fetchall()
    assert [r["trade_date"] for r in index_left] == [date(2006, 7, 4)]
    left = [r["code"] for r in conn.execute("SELECT code FROM prediction ORDER BY code").fetchall()]
    assert left == ["C", "D"]
    assert sorted(result.deleted_models) == sorted(stamps[12:14])
    assert sorted(result.deleted_backups) == sorted(stamps[12:14])
    assert (model_dir / "20260711").exists() and (model_dir / "メモ").exists()
    assert len([p for p in model_dir.iterdir()]) == 15  # 補正用データのフォルダの分を含む
    assert result.deleted_logs == 1
    assert sorted(p.name for p in log_dir.iterdir() if p.name != log_dir.name) == sorted(
        # 実行した日のログ(テストの実行中に書かれる)は残る。以前は日付を 2026-10-1x に決め打ちしていた
        [
            "batch_20260604.log",
            "メモ.txt",
            *kept,
            *[p.name for p in log_dir.iterdir() if p.name == log_file_path().name],
        ]
    )


def test_補正用データのフォルダはクリーニングで消えない(tmp_path: Path) -> None:
    (tmp_path / const.CALIBRATION_DATA_DIR).mkdir()
    (tmp_path / "20250101").mkdir()
    (tmp_path / "20250102").mkdir()
    assert cleanup_core.delete_old_generations(tmp_path, D, 1) == ["20250101"]
    assert (tmp_path / const.CALIBRATION_DATA_DIR).is_dir()


def test_フォルダがなくても成功(
    conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _backup_succeeded(D)
    monkeypatch.setattr(const, "LOG_DIR", str(tmp_path / "ない"))
    result = cleanup_core.run_cleanup(D)
    assert (result.deleted_models, result.deleted_backups, result.deleted_logs) == ([], [], 0)


def test_削除できない場合は失敗(conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> None:
    _backup_succeeded(D)
    for i in range(13):
        (resolve_path(const.MODEL_DIR) / (D - timedelta(weeks=i)).strftime("%Y%m%d")).mkdir(parents=True)

    def broken(path: object) -> None:
        raise OSError("使用中")

    monkeypatch.setattr(shutil, "rmtree", broken)
    with pytest.raises(BatchError, match="フォルダを削除できませんでした"):
        cleanup_core.run_cleanup(D)


def test_ログを削除できない場合は失敗(conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> None:
    _backup_succeeded(D)
    log_dir = resolve_path(const.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "batch_20200101.log").write_text("x", encoding="utf-8")
    real_unlink = Path.unlink

    def broken_unlink(self: Path, missing_ok: bool = False) -> None:
        if self.name == "batch_20200101.log":
            raise OSError("使用中")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", broken_unlink)
    with pytest.raises(BatchError, match="ログファイルを削除できませんでした"):
        cleanup_core.run_cleanup(D)


def test_cleanup_pyの終了コード(conn: psycopg.Connection[DictRow]) -> None:
    assert cleanup_cli.main(["--date", "2026-07-04"]) == 1
    _backup_succeeded(D)
    assert cleanup_cli.main(["--date", "2026-07-04"]) == 0


# =====================================================================
# メールの内容のファイルの削除
# =====================================================================


def test_730日より古いメールの内容のファイルを削除する(conn: psycopg.Connection[DictRow]) -> None:
    _backup_succeeded(D)
    folder = resolve_path(const.LOG_DIR) / const.NOTIFY_FILE_DIR
    folder.mkdir(parents=True)
    limit = D - timedelta(days=const.RETENTION_NOTIFY_DAYS)
    old = limit - timedelta(days=1)
    deleted = [f"通知_{old}.txt", f"通知_{old}_失敗.txt", f"通知_{old}_警告.txt"]
    kept = [f"通知_{limit}.txt", f"通知_{D}.txt", "メモ.txt", "通知_日付なし.txt"]
    for name in (*deleted, *kept):
        (folder / name).write_text("x", encoding="utf-8")

    result = cleanup_core.run_cleanup(D)

    assert result.deleted_notify_files == 3
    assert sorted(p.name for p in folder.iterdir()) == sorted(kept)
    assert "メールの内容のファイルを削除しました(3件)" in log_file_path().read_text(encoding="utf-8")


def test_30日のログの削除はメールの内容のファイルを消さない() -> None:
    folder = resolve_path(const.LOG_DIR) / const.NOTIFY_FILE_DIR
    folder.mkdir(parents=True)
    old = D - timedelta(days=const.RETENTION_LOG_DAYS + 100)
    path = folder / f"通知_{old}.txt"
    path.write_text("x", encoding="utf-8")
    assert cleanup_core.delete_old_logs(D) == 0
    assert path.exists()


# =====================================================================
# NAS のごみ箱との競合
# =====================================================================


def _generation_folders(parent: Path) -> Path:
    """残す世代が keep=1 のとき、削除される古いフォルダを作って返す。"""
    (parent / "20260704").mkdir(parents=True)
    old = parent / "20260627"
    old.mkdir()
    return old


def test_削除中に無くなったフォルダは削除済みとして扱う(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = _generation_folders(tmp_path / "gen")
    waits: list[float] = []
    real_rmtree = shutil.rmtree

    def denied(path: Path) -> None:
        raise PermissionError(5, "アクセスが拒否されました")

    def sleep(seconds: float) -> None:
        # 待っている間に、NAS がごみ箱へ移し終わった
        waits.append(seconds)
        real_rmtree(old)
        monkeypatch.setattr(shutil, "rmtree", real_rmtree)

    monkeypatch.setattr(shutil, "rmtree", denied)
    monkeypatch.setattr(cleanup_core, "sleep", sleep)
    logger = get_logger("クリーニング", D)
    assert cleanup_core.delete_old_generations(tmp_path / "gen", D, 1, logger) == ["20260627"]
    assert waits == [const.CLEANUP_DELETE_RETRY_WAIT_SEC]
    assert "削除中だったフォルダが無くなったため、削除済みとして扱いました" in log_file_path().read_text(
        encoding="utf-8"
    )


def test_やり直しで削除できれば成功(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = _generation_folders(tmp_path / "gen")
    real_rmtree = shutil.rmtree
    calls: list[Path] = []

    def once_denied(path: Path) -> None:
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError(5, "アクセスが拒否されました")
        real_rmtree(path)

    monkeypatch.setattr(shutil, "rmtree", once_denied)
    assert cleanup_core.delete_old_generations(tmp_path / "gen", D, 1) == ["20260627"]
    assert len(calls) == 2 and not old.exists()


def test_3回やり直しても残れば失敗(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = _generation_folders(tmp_path / "gen")
    calls: list[Path] = []

    def denied(path: Path) -> None:
        calls.append(path)
        raise PermissionError(5, "アクセスが拒否されました")

    monkeypatch.setattr(shutil, "rmtree", denied)
    with pytest.raises(BatchError, match="フォルダを削除できませんでした"):
        cleanup_core.delete_old_generations(tmp_path / "gen", D, 1)
    # 最初の1回 + やり直し3回
    assert len(calls) == 1 + const.CLEANUP_DELETE_RETRY_COUNT
    assert old.exists()


def test_PermissionError以外はやり直さない(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _generation_folders(tmp_path / "gen")
    calls: list[Path] = []

    def broken(path: Path) -> None:
        calls.append(path)
        raise OSError("ディスクのエラー")

    monkeypatch.setattr(shutil, "rmtree", broken)
    with pytest.raises(BatchError, match="ディスクのエラー"):
        cleanup_core.delete_old_generations(tmp_path / "gen", D, 1)
    assert len(calls) == 1
