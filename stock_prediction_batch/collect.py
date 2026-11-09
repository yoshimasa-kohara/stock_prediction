"""収集の実行ファイル。

使い方:
    python collect.py                                    今日(日本時間)の分と、直近10日の取り逃し
    python collect.py --date 2026-07-08                  指定日の分と、その前10日の取り逃し
    python collect.py --from 2016-10-03 --to 2026-10-30  期間内の全ての営業日(取得済みの日も取り直す)

--from、--to の場合は、取れない日をとばして最後まで進む。
ネットワークなどのエラーで取れなかった日は logs の 収集失敗_YYYYMMDD_HHMM.csv に書く。

終了コード: 0 = 成功または空振り、1 = 失敗(--from、--to で、収集失敗の一覧に書いた日がある場合を含む)。
単独で実行した場合は、失敗しても失敗メールを送らない。
"""

import sys
from datetime import datetime

import const
from common.args import parse_date_args, today_jst
from common.errors import ArgError, BatchError
from common.logger import get_logger
from core.collect_core import LABEL, RangeCollectState, failure_file_path, run_collect


def main(argv: list[str]) -> int:
    """引数を受け取って収集の本体を呼ぶ。終了コードを返す。"""
    try:
        args = parse_date_args(argv, allow_range=True, prog="collect.py")
    except ArgError as e:
        # 引数エラーは、DBを変更せずに終了する(実行履歴も残さない)
        get_logger(LABEL, today_jst()).error(str(e))
        return 1
    # 期間の収集では、取れない日をとばして、収集失敗の一覧に書く
    state = RangeCollectState(failure_file_path(datetime.now())) if args.date_from is not None else None
    try:
        run_collect(args.target_date, args.date_from, args.date_to, const.BATCH_TYPE_SINGLE, state)
    except BatchError:
        # ログと実行履歴には、run_collect の中で記録済み
        return 1
    if state is not None and state.failure_count > 0:
        get_logger(LABEL, args.target_date).warning(
            "取れなかったデータが %d件あります。収集失敗の一覧(%s)のコマンドで取り直してください",
            state.failure_count,
            state.failure_path,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
