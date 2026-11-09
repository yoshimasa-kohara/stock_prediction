"""株価予測システムの画面(stock_prediction_web)の API と、画面の配信。

1つのプロセス・1つのポート(8080)で、/api/... の API と、画面をビルドした静的ファイル(frontend/dist)を返す
。/api/ 以外のパスは全て画面の index.html を返し、画面の中で React Router が表示を切り替える。
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from starlette.responses import Response

from api import web_const
from api.errors import error_response, register_error_handlers
from api.logger import log_api, setup_logging
from api.routers import accuracy, candidates, holdings, index, runs, screen, settings, status, stocks

# stock_prediction_web のフォルダ(このファイルの1つ上)
BASE_DIR: Path = Path(__file__).resolve().parent.parent

# API のパスの先頭
API_PREFIX: str = "/api"


def dist_dir() -> Path:
    """画面をビルドした静的ファイルのフォルダ(web_const.FRONTEND_DIST_DIR)。"""
    path = Path(web_const.FRONTEND_DIST_DIR)
    return path if path.is_absolute() else BASE_DIR / path


def _static_response(full_path: str) -> Response:
    """画面の静的ファイルを返す。ファイルがなければ index.html を返す(SPA の画面の URL のため)。"""
    root = dist_dir().resolve()
    index = root / "index.html"
    if not index.is_file():
        return error_response(503, web_const.MSG_FRONTEND_NOT_BUILT)
    if full_path:
        candidate = (root / full_path).resolve()
        # dist の外のファイル(..\ など)は返さない
        if candidate.is_relative_to(root) and candidate.is_file():
            return FileResponse(candidate)
    # index.html は毎回読み直させる(ビルドし直した画面がすぐ使われるように)。
    # assets の中のファイルは名前に内容のハッシュが付くので、キャッシュされても問題ない
    return FileResponse(index, headers={"Cache-Control": "no-cache"})


def create_app() -> FastAPI:
    """アプリを作る。テストからも呼ぶ。"""
    app = FastAPI(
        title="株価予測システム 画面 API",
        # API の一覧の画面(開発中の確認用)も /api/ の下に置く
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
    )
    setup_logging()
    register_error_handlers(app)
    for module in (settings, status, runs, candidates, stocks, index, holdings, accuracy, screen):
        app.include_router(module.router)

    # /api/ で始まる、存在しないパスは 404(画面の index.html を返さない)
    @app.api_route(API_PREFIX + "/{rest:path}", methods=["GET", "POST", "PUT", "DELETE"], include_in_schema=False)
    @log_api
    def api_not_found(rest: str) -> JSONResponse:
        return error_response(404, web_const.MSG_NOT_FOUND)

    # それ以外のパスは画面(登録の順番が大事。API より後に登録する)
    @app.get("/{full_path:path}", include_in_schema=False)
    def frontend(full_path: str) -> Response:
        return _static_response(full_path)

    return app


app: FastAPI = create_app()
