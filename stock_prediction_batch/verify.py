"""検証の実行ファイル。

使い方:
    python verify.py                    今日(日本時間)の分
    python verify.py --date 2026-07-08   指定日を集計の基準日にする

終了コード: 0 = 成功または空振り、1 = 失敗。単独で実行した場合は、失敗しても失敗メールを送らない。
"""

import sys

import const
from common.cli import run_single
from core.verify_core import run_verify


def main(argv: list[str]) -> int:
    """引数を受け取って検証の本体を呼ぶ。終了コードを返す。"""
    return run_single(
        argv, "verify.py", const.PROCESS_VERIFY, lambda d, batch_type: run_verify(d, batch_type=batch_type)
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
