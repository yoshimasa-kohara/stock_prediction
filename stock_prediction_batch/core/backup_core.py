"""バックアップの本体。

DBの中身(pg_dump のカスタム形式)と、最新の学習済みモデルと補正用データを、
NAS の BACKUP_DIR の下の指定日のフォルダに保存する。
当処理はDBを変更しない(読むだけ)。
"""

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import const
from common import db
from common.env import get_env, resolve_path
from common.errors import BatchError
from common.logger import BatchLogger, get_logger
from common.run_history import ProcessOutcome, run_with_history

LABEL: str = const.PROCESS_LABELS[const.PROCESS_BACKUP]

# pg_dump の実行(テストで差し替える)
run_command = subprocess.run


@dataclass(frozen=True)
class BackupResult:
    """バックアップの結果。"""

    folder: Path
    dump_size: int
    model_version: str | None


def dump_database(dump_path: Path) -> None:
    """pg_dump でDBの中身をファイルに書き出す。"""
    env = dict(os.environ)
    env["PGPASSWORD"] = get_env("DB_PASSWORD", required=False)
    command = [
        const.PG_DUMP_PATH,
        "-h", get_env("DB_HOST"),
        "-p", get_env("DB_PORT"),
        "-U", get_env("DB_USER"),
        "-d", get_env("DB_NAME"),
        "-F", "c",
        "-f", str(dump_path),
    ]  # fmt: skip
    try:
        completed = run_command(command, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError as e:
        raise BatchError(LABEL, f"pg_dump を実行できませんでした({const.PG_DUMP_PATH}): {e}") from e
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip()[:500]
        raise BatchError(LABEL, f"pg_dump が失敗しました(終了コード {completed.returncode}): {detail}")


def _copy_checked(src: Path, dst: Path) -> None:
    """ファイルをコピーし、大きさが同じことを確かめる(保存したファイルに対するチェック)。"""
    shutil.copyfile(src, dst)
    if dst.stat().st_size != src.stat().st_size:
        raise BatchError(LABEL, f"バックアップのコピーが正しくありません({dst.name})")


def latest_model_version(target_date: date) -> str | None:
    """学習の基準日が指定日以前で最も新しいモデルバージョン。"""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT model_version FROM model WHERE base_date <= %s ORDER BY base_date DESC, model_version DESC LIMIT 1",
            (target_date,),
        ).fetchone()
    return None if row is None else str(row["model_version"])


def backup(target_date: date, logger: BatchLogger) -> BackupResult:
    """バックアップの手順2〜8を行う。"""
    stamp = target_date.strftime("%Y%m%d")
    dump_name = f"stock_prediction_{stamp}.dump"
    dump_path = Path(tempfile.gettempdir()) / dump_name

    # 手順2: DBの中身を書き出す
    dump_database(dump_path)
    dump_size = dump_path.stat().st_size
    logger.info("DBを書き出しました(%s)", _human_size(dump_size))

    try:
        # 手順3: NAS に保存先のフォルダを作る(同じ名前があれば中身を削除して使う)
        folder = Path(const.BACKUP_DIR) / stamp
        try:
            if folder.exists():
                shutil.rmtree(folder)
            folder.mkdir(parents=True)
        except OSError as e:
            raise BatchError(LABEL, f"バックアップの保存先に書き込めません({folder}): {e}") from e

        # 手順4、手順6: DBのファイルをコピーして確かめる
        _copy_checked(dump_path, folder / dump_name)
        logger.info("NASにコピーしました(%s)", folder)

        # 手順5、手順6: 最新のモデルをコピーして確かめる
        version = latest_model_version(target_date)
        if version is not None:
            src_dir = resolve_path(const.MODEL_DIR) / version
            dst_dir = folder / "models" / version
            dst_dir.mkdir(parents=True)
            for src in sorted(src_dir.glob("*")) if src_dir.is_dir() else []:
                if src.is_file():
                    _copy_checked(src, dst_dir / src.name)
            logger.info("モデルをコピーしました(%s)", version)
        else:
            logger.info("コピーするモデルはありません")

        # 手順5、手順6: 補正用データをコピーして確かめる(バージョンに関係なく貯めているファイル)
        data_dir = resolve_path(const.MODEL_DIR) / const.CALIBRATION_DATA_DIR
        data_files = sorted(data_dir.glob("*.csv.gz")) if data_dir.is_dir() else []
        if data_files:
            dst_data_dir = folder / "models" / const.CALIBRATION_DATA_DIR
            dst_data_dir.mkdir(parents=True)
            for src in data_files:
                _copy_checked(src, dst_data_dir / src.name)
            logger.info("補正用データをコピーしました(%dファイル)", len(data_files))
    finally:
        # 手順7: 一時ファイルを削除する(削除できなくても成功のまま)
        try:
            dump_path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("一時ファイルを削除できませんでした(%s): %s", dump_path, e)
    return BackupResult(folder=folder, dump_size=dump_size, model_version=version)


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"  # pragma: no cover


def run_backup(target_date: date, batch_type: str = const.BATCH_TYPE_SINGLE) -> BackupResult:
    """バックアップを実行する。実行履歴とログの開始・終了も行う。

    Raises:
        BatchError: 失敗した場合
    """
    logger = get_logger(LABEL, target_date)

    def body() -> ProcessOutcome[BackupResult]:
        logger.info("処理を開始します")
        result = backup(target_date, logger)
        logger.info("処理を終了します")
        return ProcessOutcome(result=result, status=const.RUN_STATUS_SUCCESS, record_count=1)

    return run_with_history(batch_type, const.PROCESS_BACKUP, target_date, logger, body)
