"""画面の静的ファイルの配信(api/main.py)と、起動(api/__main__.py)のテスト。"""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api import __main__ as entry
from api import main, web_const


@pytest.fixture
def dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """テスト用の frontend/dist を作り、そこを配信するようにする。"""
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<html>画面</html>", encoding="utf-8")
    (root / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("見せない", encoding="utf-8")
    monkeypatch.setattr(web_const, "FRONTEND_DIST_DIR", str(root))
    return root


def test_dist_dir_relative_to_web_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    """相対パスは stock_prediction_web のフォルダを起点にする。"""
    monkeypatch.setattr(web_const, "FRONTEND_DIST_DIR", "frontend/dist")
    assert main.dist_dir() == main.BASE_DIR / "frontend" / "dist"


def test_not_built_is_503(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """画面がビルドされていないときは 503 と、ビルドのしかたのメッセージ。"""
    monkeypatch.setattr(web_const, "FRONTEND_DIST_DIR", str(tmp_path / "no-dist"))
    res = client.get("/")
    assert res.status_code == 503
    assert res.json() == {"detail": web_const.MSG_FRONTEND_NOT_BUILT}


def test_top_returns_index(client: TestClient, dist: Path) -> None:
    """/ は index.html を返す。index.html はキャッシュさせない。"""
    res = client.get("/")
    assert res.status_code == 200
    assert "画面" in res.text
    assert res.headers["cache-control"] == "no-cache"


def test_screen_urls_return_index(client: TestClient, dist: Path) -> None:
    """画面の URL(/candidates など)は、ファイルがないので index.html を返す(React Router が表示する)。"""
    for path in ["/candidates?date=2026-07-22", "/candidates/results", "/stocks/11110", "/holdings"]:
        res = client.get(path)
        assert res.status_code == 200
        assert "画面" in res.text


def test_asset_file(client: TestClient, dist: Path) -> None:
    """dist の中のファイルは、そのファイルを返す。"""
    res = client.get("/assets/index-abc123.js")
    assert res.status_code == 200
    assert res.text == "console.log(1)"


def test_outside_of_dist_is_not_returned(dist: Path) -> None:
    """dist の外のファイル(../secret.txt)は返さず、index.html を返す。"""
    res = main._static_response("../secret.txt")
    assert Path(str(getattr(res, "path", ""))).name == "index.html"


def test_main_starts_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    """python -m api は、web_const のアドレスとポートで起動する。"""
    calls: list[dict[str, Any]] = []

    def fake_run(app: str, **kwargs: Any) -> None:
        calls.append({"app": app, **kwargs})

    monkeypatch.setattr("uvicorn.run", fake_run)
    entry.main([])
    entry.main(["--reload"])
    assert calls[0] == {
        "app": "api.main:app",
        "host": web_const.HOST,
        "port": web_const.PORT,
        "reload": False,
        "reload_dirs": None,
    }
    assert calls[1]["reload"] is True
    assert calls[1]["reload_dirs"] == ["api"]
