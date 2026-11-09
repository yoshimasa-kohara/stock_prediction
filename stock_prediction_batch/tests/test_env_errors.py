"""common/env.py と common/errors.py のテスト。"""

from pathlib import Path

import pytest

from common import env
from common.errors import ApiError, BatchError, RateLimitError


def test_get_envは環境変数の値を返す(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_SAMPLE_KEY", " value ")
    assert env.get_env("TEST_SAMPLE_KEY") == "value"


def test_get_envで必須の値がない場合はエラー(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_MISSING_KEY", raising=False)
    with pytest.raises(RuntimeError, match="TEST_MISSING_KEY が .env に設定されていません"):
        env.get_env("TEST_MISSING_KEY")
    assert env.get_env("TEST_MISSING_KEY", required=False) == ""


def test_resolve_pathは相対パスをプロジェクトのフォルダから解決する(tmp_path: Path) -> None:
    assert env.resolve_path("logs") == env.BASE_DIR / "logs"
    assert env.resolve_path(str(tmp_path)) == tmp_path


def test_BatchErrorの文字列には銘柄コードが入る() -> None:
    e = BatchError("予測", "計算できません", "11110")
    assert (e.process_name, e.message, e.code) == ("予測", "計算できません", "11110")
    assert str(e) == "計算できません(銘柄コード: 11110)"
    assert str(BatchError("収集", "失敗")) == "失敗"


def test_ApiErrorはステータスコードを持つ() -> None:
    e = RateLimitError("上限", 429)
    assert isinstance(e, ApiError)
    assert e.status_code == 429
