"""バックアップの実行ファイル。

使い方:
    python backup.py                    今日(日本時間)の分
    python backup.py --date 2026-07-04   指定日のフォルダに保存する

終了コード: 0 = 成功または空振り、1 = 失敗。単独で実行した場合は、失敗しても失敗メールを送らない。
"""

import sys

import const
from common.cli import run_single
from core.backup_core import run_backup


def main(argv: list[str]) -> int:
    """引数を受け取ってバックアップの本体を呼ぶ。終了コードを返す。"""
    return run_single(
        argv, "backup.py", const.PROCESS_BACKUP, lambda d, batch_type: run_backup(d, batch_type=batch_type)
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
