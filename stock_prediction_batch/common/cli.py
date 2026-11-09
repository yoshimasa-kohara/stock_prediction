"""実行ファイル(collect.py 以外)で共通の、引数の受け取りと終了コードの決め方。

実行ファイルは引数を受け取って core/ の本体を呼ぶだけにする。
単独で起動した場合は、失敗しても失敗メールを送らない。終了コードは、成功と空振りは 0、失敗は 1。
"""

from collections.abc import Callable
from datetime import date

import const
from common.args import parse_date_args, today_jst
from common.errors import ArgError, BatchError
from common.logger import get_logger


def run_single(argv: list[str], prog: str, process_name: str, body: Callable[[date, str], object]) -> int:
    """--date を受け取り、処理の本体を単独の起動(SINGLE)として呼ぶ。終了コードを返す。

    Args:
        argv: 引数のリスト(sys.argv[1:])
        prog: 実行ファイルの名前(例: train.py)
        process_name: 処理の名前(COLLECT など)
        body: 処理の本体。指定日と起動のしかたを受け取る
    """
    label = const.PROCESS_LABELS[process_name]
    try:
        args = parse_date_args(argv, prog=prog)
    except ArgError as e:
        # 引数エラーは、DBやファイルを変更せずに終了する(実行履歴も残さない)
        get_logger(label, today_jst()).error(str(e))
        return 1
    try:
        body(args.target_date, const.BATCH_TYPE_SINGLE)
    except BatchError:
        # ログと実行履歴には、本体の中で記録済み
        return 1
    return 0
