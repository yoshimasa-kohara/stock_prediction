"""銘柄コードの表示のテスト。"""

import pytest

from common.stock_code import display_code


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("13010", "1301"),  # 普通株は4桁
        ("130A0", "130A"),  # 英文字を含むコードも5桁目が 0 なら4桁
        ("25935", "25935"),  # 優先株式など5桁目が 0 以外は5桁のまま
        ("1301", "1301"),  # 5桁でないものはそのまま
        ("", ""),
    ],
)
def test_銘柄コードの表示(code: str, expected: str) -> None:
    assert display_code(code) == expected
