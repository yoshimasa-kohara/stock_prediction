"""予測の実行ファイル。

使い方:
    python predict.py                    今日(日本時間)の分
    python predict.py --date 2026-07-08  指定日を予測日にする

終了コード: 0 = 成功または空振り、1 = 失敗。単独で実行した場合は、失敗しても失敗メールを送らない。
"""

import sys

import const
from common.cli import run_single
from core.predict_core import run_predict


def main(argv: list[str]) -> int:
    """引数を受け取って予測の本体を呼ぶ。終了コードを返す。"""
    return run_single(
        argv, "predict.py", const.PROCESS_PREDICT, lambda d, batch_type: run_predict(d, batch_type=batch_type)
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
