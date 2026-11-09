"""学習の実行ファイル。

使い方:
    python train.py                    今日(日本時間)の分
    python train.py --date 2026-07-04   指定日を学習の基準日にする

終了コード: 0 = 成功または空振り、1 = 失敗。単独で実行した場合は、失敗しても失敗メールを送らない。
"""

import sys

import const
from common.cli import run_single
from core.train_core import run_train


def main(argv: list[str]) -> int:
    """引数を受け取って学習の本体を呼ぶ。終了コードを返す。"""
    return run_single(argv, "train.py", const.PROCESS_TRAIN, lambda d, batch_type: run_train(d, batch_type=batch_type))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
