"""銘柄コードの表示。

DB の銘柄コードは J-Quants の5桁(普通株は5桁目が 0。例: 13010)。
画面とメールでは、5桁目が 0 なら4桁(例: 1301、130A)、0 以外(優先株式など)なら5桁のまま(例: 25935)出す。
4桁に切ると、普通株と優先株式が同じコードになり見分けられないため。
"""

# 4桁にする5桁目
_COMMON_STOCK_SUFFIX: str = "0"


def display_code(code: str) -> str:
    """画面とメールに出す銘柄コード。5桁で5桁目が 0 なら4桁、それ以外はそのまま返す。"""
    if len(code) == 5 and code.endswith(_COMMON_STOCK_SUFFIX):
        return code[:4]
    return code
