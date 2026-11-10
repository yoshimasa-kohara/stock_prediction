"""API のエラーの返し方。

エラーは、HTTP の状態コードと日本語のメッセージで返す。本文の形は全て {"detail": "メッセージ"}。
画面は detail をそのまま赤い枠に出す。
"""

import logging

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api import web_const
from api.logger import LOGGER_NAME

# API のログを出すロガー(logs フォルダのファイルとコンソール。api/logger.py)
logger: logging.Logger = logging.getLogger(LOGGER_NAME)


class ApiError(Exception):
    """画面にそのまま見せるエラー。入力の誤りや、データがないときに投げる。"""

    def __init__(self, status_code: int, message: str) -> None:
        """
        Args:
            status_code: HTTP の状態コード(400、404 など)
            message: 画面に出す日本語のメッセージ
        """
        super().__init__(message)
        self.status_code: int = status_code
        self.message: str = message


def error_response(status_code: int, message: str) -> JSONResponse:
    """エラーの本文 {"detail": "..."} を作る。"""
    return JSONResponse(status_code=status_code, content={"detail": message})


def _field_name(loc: tuple[int | str, ...]) -> str:
    """入力チェックのエラーの場所(例: ("query", "date"))から、項目の名前(date)を取り出す。"""
    names = [str(x) for x in loc if x not in ("query", "path", "body")]
    return ".".join(names) if names else "入力"


async def _api_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """ApiError を、そのメッセージで返す。"""
    assert isinstance(exc, ApiError)
    return error_response(exc.status_code, exc.message)


async def _validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """入力チェック(FastAPI が型で行うもの)のエラーを、日本語で返す。"""
    assert isinstance(exc, RequestValidationError)
    fields = sorted({_field_name(tuple(e.get("loc", ()))) for e in exc.errors()})
    return error_response(422, f"{web_const.MSG_INVALID_INPUT}({', '.join(fields)})")


async def _http_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """404 などの HTTP のエラーを、日本語で返す。"""
    assert isinstance(exc, StarletteHTTPException)
    if exc.status_code == 404:
        return error_response(404, web_const.MSG_NOT_FOUND)
    return error_response(exc.status_code, str(exc.detail))


async def _db_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """DB に接続できないとき(PostgreSQL が止まっている、.env の接続先が違うなど)。"""
    logger.error("DB に接続できません: %s", exc)
    return error_response(503, web_const.MSG_DB_UNAVAILABLE)


async def _unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """想定していないエラー。詳しい内容はログにだけ出す。"""
    logger.exception("想定していないエラーが発生しました: %s", exc)
    return error_response(500, web_const.MSG_INTERNAL_ERROR)


def register_error_handlers(app: FastAPI) -> None:
    """アプリにエラーの返し方を登録する。"""
    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(psycopg.OperationalError, _db_error_handler)
    app.add_exception_handler(Exception, _unexpected_error_handler)
