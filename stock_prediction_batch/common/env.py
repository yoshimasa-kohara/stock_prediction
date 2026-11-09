"""プロジェクトの場所と .env の読み込み。

.env は stock_prediction_batch の直下に置く。
同じ名前の環境変数がすでにある場合は、環境変数を優先する(テストで別のDBを使うため)。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# stock_prediction_batch のフォルダ(このファイルの1つ上)
BASE_DIR: Path = Path(__file__).resolve().parent.parent

# .env の場所
ENV_PATH: Path = BASE_DIR / ".env"

_loaded: bool = False


def _ensure_loaded() -> None:
    """.env を1回だけ読み込む。"""
    global _loaded
    if not _loaded:
        load_dotenv(ENV_PATH, override=False, encoding="utf-8")
        _loaded = True


def get_env(key: str, required: bool = True) -> str:
    """.env(または環境変数)の値を返す。

    Args:
        key: キーの名前
        required: True の場合、値がない(空を含む)ときにエラーにする

    Returns:
        値。required が False で値がない場合は空文字列
    """
    _ensure_loaded()
    value = os.environ.get(key, "").strip()
    if required and value == "":
        raise RuntimeError(f"{key} が .env に設定されていません")
    return value


def resolve_path(relative_or_absolute: str) -> Path:
    """const.py のフォルダの指定を、絶対パスに直す。

    相対パスの場合は stock_prediction_batch のフォルダを起点にする。
    batファイル以外(VSCode など)から起動しても、同じ場所を使うため。
    """
    path = Path(relative_or_absolute)
    if path.is_absolute():
        return path
    return BASE_DIR / path
