"""Web サーバーを起動する。

stock_prediction_web のフォルダで実行する:
    .venv\\Scripts\\python -m api            本番と同じ動かし方
    .venv\\Scripts\\python -m api --reload   開発中(ソースを保存すると自動で読み直す)
"""

import sys

import uvicorn

from api import web_const


def main(argv: list[str]) -> None:
    """uvicorn で api.main:app を起動する。"""
    reload = "--reload" in argv
    uvicorn.run(
        "api.main:app",
        host=web_const.HOST,
        port=web_const.PORT,
        reload=reload,
        # 開発中に読み直すのは web の api フォルダだけ(バッチのフォルダは対象外)
        reload_dirs=["api"] if reload else None,
    )


if __name__ == "__main__":  # pragma: no cover - 起動するだけ
    main(sys.argv[1:])
