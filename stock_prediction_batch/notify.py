"""通知の実行ファイル。

使い方:
    python notify.py                    今日(日本時間)の分
    python notify.py --date 2026-07-08  指定日の予測を通知する

単独で実行した場合は、補完した日の分として扱わない(件名に【補完分】を付けない)。
終了コード: 0 = 成功、1 = 失敗。単独で実行した場合は、失敗しても失敗メールを送らない。
"""

import sys

import const
from common.cli import run_single
from core.notify_core import run_notify


def main(argv: list[str]) -> int:
    """引数を受け取って通知の本体を呼ぶ。終了コードを返す。"""
    return run_single(
        argv, "notify.py", const.PROCESS_NOTIFY, lambda d, batch_type: run_notify(d, False, batch_type=batch_type)
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
