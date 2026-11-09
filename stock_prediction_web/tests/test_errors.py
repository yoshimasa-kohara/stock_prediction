"""エラーの返し方(api/errors.py)のテスト。"""

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import web_const
from api.errors import ApiError, register_error_handlers
from common import db


def test_unknown_api_path_is_404(client: TestClient) -> None:
    """存在しない API は、画面の index.html ではなく 404 の JSON を返す。"""
    for method in ["get", "post", "put", "delete"]:
        res = client.request(method, "/api/not-exists/1")
        assert res.status_code == 404
        assert res.json() == {"detail": web_const.MSG_NOT_FOUND}


def test_post_to_read_only_api_is_404(client: TestClient) -> None:
    """読むだけの API に POST すると、存在しない API と同じ 404 にする。"""
    res = client.post("/api/settings")
    assert res.status_code == 404
    assert res.json() == {"detail": web_const.MSG_NOT_FOUND}


def test_db_unavailable_is_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """DB に接続できないときは 503 と「DB に接続できません」。"""

    def broken_connect() -> None:
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(db, "connect", broken_connect)
    res = client.get("/api/status")
    assert res.status_code == 503
    assert res.json() == {"detail": "DB に接続できません"}


def make_app() -> FastAPI:
    """エラーを起こすだけの API を持つ、テスト用のアプリ。"""
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/api/boom")
    def boom() -> None:
        raise RuntimeError("想定していない失敗")

    @app.get("/api/teapot")
    def teapot() -> None:
        raise ApiError(418, "お茶は入れられません")

    @app.get("/api/items/{item_id}")
    def item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    return app


def test_unexpected_error_is_500() -> None:
    """想定していないエラーは 500 と、ログを見るようにというメッセージ(詳しい内容は返さない)。"""
    with TestClient(make_app(), raise_server_exceptions=False) as c:
        res = c.get("/api/boom")
    assert res.status_code == 500
    assert res.json() == {"detail": web_const.MSG_INTERNAL_ERROR}


def test_api_error_uses_its_status_and_message() -> None:
    """ApiError は、その状態コードとメッセージで返す。"""
    with TestClient(make_app()) as c:
        res = c.get("/api/teapot")
    assert res.status_code == 418
    assert res.json() == {"detail": "お茶は入れられません"}


def test_validation_error_names_the_field() -> None:
    """パスの値の型が違うときも、日本語のメッセージに項目の名前を付ける。"""
    with TestClient(make_app()) as c:
        res = c.get("/api/items/abc")
    assert res.status_code == 422
    assert res.json() == {"detail": "入力の値が正しくありません(item_id)"}


def test_other_http_error_keeps_its_message() -> None:
    """404 以外の HTTP のエラー(405 など)は、HTTP の決まった文のまま返す。"""
    with TestClient(make_app()) as c:
        res = c.post("/api/teapot")
    assert res.status_code == 405
    assert res.json() == {"detail": "Method Not Allowed"}


def test_field_name() -> None:
    """入力チェックのエラーの場所から、項目の名前を取り出す。場所が本文全体なら「入力」。"""
    from api.errors import _field_name

    assert _field_name(("query", "date")) == "date"
    assert _field_name(("body", "items", 0, "price")) == "items.0.price"
    assert _field_name(("body",)) == "入力"
