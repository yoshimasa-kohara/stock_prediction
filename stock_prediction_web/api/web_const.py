"""株価予測システムの画面(stock_prediction_web)の設定値。

画面の数値・文字列の設定値は、このファイルにまとめる(5章 No.15)。
画面(React)には数値を直接書かず、起動したときに /api/settings で受け取る。
バッチの設定値(予測日数、閾値など)は、バッチの const.py をそのまま読む。
名前は const.py にしない(バッチの const と名前がぶつかるため)。
"""

from typing import Final

# =====================================================================
# Web サーバー
# =====================================================================

# 待ち受けるアドレス。0.0.0.0 は、全てのネットワークから受け付ける
# (どこから接続できるかは、Windows のファイアウォールで絞る)
HOST: Final[str] = "0.0.0.0"
# 待ち受けるポート
PORT: Final[int] = 8080
# 画面をビルドした静的ファイルのフォルダ(stock_prediction_web からの相対パス)
FRONTEND_DIST_DIR: Final[str] = "frontend/dist"

# =====================================================================
# ログ(api/logger.py)
# =====================================================================

# ログファイルを置くフォルダ(stock_prediction_web からの相対パス)。日ごとに web_YYYYMMDD.log を作る
LOG_DIR: Final[str] = "logs"
# ログファイルを残す日数(今日を含む。バッチの RETENTION_LOG_DAYS と同じ30日)
LOG_KEEP_DAYS: Final[int] = 30
# ログのレベル。API の開始・終了(処理時間)は INFO で出す
LOG_LEVEL: Final[str] = "INFO"

# =====================================================================
# 全画面に共通
# =====================================================================

# 画面が /api/status を読み直す間隔(秒)。バッチ実行中の表示を新しくするため
STATUS_REFRESH_SEC: Final[int] = 60
# 上のメニューの銘柄の検索で出す候補の最大件数
STOCK_SEARCH_LIMIT: Final[int] = 10
# 時価総額の単位の換算(DB の daily_valuation.market_cap は百万円。API は円で返す)
MARKET_CAP_UNIT_YEN: Final[int] = 1_000_000

# =====================================================================
# 画面ごと
# =====================================================================

# トップに出す買い候補の銘柄数
TOP_CANDIDATE_COUNT: Final[int] = 5
# 買い候補と実際の株価: 期間の初期値(最新の予測日の何か月前から)と、1回に表示できる最大の月数
RESULTS_DEFAULT_MONTHS: Final[int] = 1
RESULTS_MAX_MONTHS: Final[int] = 3
# 銘柄詳細: チャートの期間の初期値(1M・3M・6M・1Y・ALL のどれか)
CHART_DEFAULT_PERIOD: Final[str] = "6M"
# 的中率: 推移のグラフの期間の初期値(直近の月数)
ACCURACY_DAILY_DEFAULT_MONTHS: Final[int] = 3
# 的中率: 買い候補 順位別 的中率
# 期間(この順にタブを出す)と、直近の月数(None は全期間)。境界はバッチの verify_period_summary の 1M・3M と同じ
ACCURACY_RANK_PERIODS: Final[dict[str, int | None]] = {"1M": 1, "3M": 3, "6M": 6, "1Y": 12, "2Y": 24, "ALL": None}
# 期間の初期値
ACCURACY_RANK_DEFAULT_PERIOD: Final[str] = "1M"
# 順位の帯(この順に列を出す。(最初の順位, 最後の順位))。最初の帯は全体
ACCURACY_RANK_BANDS: Final[list[tuple[int, int]]] = [(1, 20), (1, 5), (6, 10), (11, 15), (16, 20)]
# 差を出す2つの帯(前 − 後)
ACCURACY_RANK_DIFF_BANDS: Final[tuple[tuple[int, int], tuple[int, int]]] = ((1, 5), (16, 20))
# 精度の比較: 買い候補の業種の内訳に出す上位の業種の数
COMPARE_SECTOR_TOP_N: Final[int] = 5
# 実行履歴: 対象の日付の初期値(直近の日数)と、1回に表示する件数
RUNS_DEFAULT_DAYS: Final[int] = 7
RUNS_PAGE_SIZE: Final[int] = 100
# 銘柄の検索: 1回に表示する件数
SCREEN_PAGE_SIZE: Final[int] = 50
# 銘柄の検索: 全銘柄の備考を API のメモリに残す予測日の数(古いものから消す)
NOTE_CACHE_DAYS: Final[int] = 5
# 銘柄詳細: 過去の予測と答え合わせの表に出す最大の行数(新しい順)
STOCK_PREDICTION_HISTORY_LIMIT: Final[int] = 500
# 保有銘柄: メモの最大の文字数(holding.note は VARCHAR(500))
HOLDING_NOTE_MAX_LENGTH: Final[int] = 500

# =====================================================================
# 表示する文字
# =====================================================================

# 実行履歴の状態(run_history.status)の日本語
RUN_STATUS_LABELS: Final[dict[str, str]] = {
    "SUCCESS": "成功",
    "EMPTY": "空振り",
    "FAILED": "失敗",
    "RUNNING": "実行中",
}
# 起動のしかた(run_history.batch_type)の日本語
BATCH_TYPE_LABELS: Final[dict[str, str]] = {
    "DAILY": "日次バッチ",
    "WEEKLY": "週次バッチ",
    "SINGLE": "単独で起動",
}

# =====================================================================
# エラーのメッセージ
# =====================================================================

# DB に接続できないとき
MSG_DB_UNAVAILABLE: Final[str] = "DB に接続できません"
# 想定していないエラー
MSG_INTERNAL_ERROR: Final[str] = "サーバーでエラーが発生しました。ログを確認してください"
# API のパスが見つからないとき
MSG_NOT_FOUND: Final[str] = "指定された API はありません"
# 入力の値が正しくないとき(後ろに、どの項目かを付ける)
MSG_INVALID_INPUT: Final[str] = "入力の値が正しくありません"
# 画面がビルドされていないとき
MSG_FRONTEND_NOT_BUILT: Final[str] = (
    "画面がビルドされていません。stock_prediction_web\\frontend で npm run build を実行してください"
)
