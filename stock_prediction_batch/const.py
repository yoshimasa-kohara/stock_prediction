"""株価予測システム(Stock Prediction Batch)の設定値。

秘密にしないといけない値(APIキー、パスワード、DB接続情報)は .env に置き、ここには書かない。
"""

from typing import Final

# =====================================================================
# 予測・順位付け
# =====================================================================

# 予測する営業日後のリスト。予測・検証・メールの列に共通で使う
PREDICT_HORIZONS: Final[list[int]] = [5, 10, 20, 60]
# 順位付けと保有銘柄の判定に使う、主となる予測日数。メールでは、この日数の列の見出しの色と数字の太字で示す
MAIN_HORIZON: Final[int] = 20
# 買い候補として通知する上位の銘柄数
BUY_CANDIDATE_COUNT: Final[int] = 20
# 買い候補(業種分散)の銘柄数。並べ方は買い候補と同じで、同じ33業種は SECTOR_CAP_PER_SECTOR 銘柄までにする。
# 足切りを置き換えても銘柄が足りない日は、その件数だけ出す
BUY_CANDIDATE_SECTOR_COUNT: Final[int] = 30
# 買い候補が足りない日に、上昇確率の条件の代わりに使う「その日の全銘柄の上昇確率の上位の割合」(25%)
BUY_CANDIDATE_FALLBACK_TOP_RATE: Final[float] = 0.25
# 買い候補(業種分散)で、同じ33業種から選ぶ銘柄の上限。33業種がない銘柄は、銘柄ごとに別の業種として扱う
SECTOR_CAP_PER_SECTOR: Final[int] = 3
# 買い候補の順位付けの方式
#   R: 上昇確率が UP_PROB_THRESHOLD 以上、かつ見込み(予測株価 ÷ 予測時の株価 − 1)がプラスの中で、市場比の確率が高い順
#   A: 上昇確率が高い順。C: 上昇確率が RANKING_MIN_UP_PROB 以上の中で予測リターンが高い順
RANKING_METHOD: Final[str] = "R"
# 方式Cで足切りに使う上昇確率(60%)
RANKING_MIN_UP_PROB: Final[float] = 0.6
# 上昇予測とみなす上昇確率の境目(50%)
UP_PROB_THRESHOLD: Final[float] = 0.5
# 市場の中央値を上回る予測とみなす市場比の確率の境目(50%)。保有銘柄の判定に使う
REL_PROB_THRESHOLD: Final[float] = 0.5
# 的中率の集計で「上昇確率が高い銘柄」とみなす境目(70%)
HIGH_PROB_THRESHOLD: Final[float] = 0.7
# 上昇予測の保有銘柄に注意を出す、買値からの下落率(3%)
HOLD_WARNING_DROP_RATE: Final[float] = 0.03
# 損切りライン(%)。保有銘柄の現在値が、買値からこの割合以上下がったら、メールの備考に
# 「損切りライン10%の値を下回っています。」と表示する。0 なら表示しない。判定は変えない
STOP_LOSS_PERCENT: Final[int] = 10
# 割安さ・稼ぐ力の備考(買い候補と保有銘柄)。判定や順位は変えない。0 なら、その備考は表示しない
# PBR がこの値未満なら「PBR0.5倍割れ(割安)」
NOTE_PBR_MAX: Final[float] = 0.5
# 予想PER が、同じ33業種の予想PER の中央値のこの倍率以下なら「予想PERが業種平均より低い(割安)」(赤字予想は除く)
NOTE_PER_SECTOR_RATIO: Final[float] = 0.7
# 実績ROE(%)がこの値以上なら「ROE8%以上(稼ぐ力が高い)」
NOTE_ROE_MIN_PERCENT: Final[int] = 8
# トレンドライン(core/trendline.py)。
# 線は、特徴量の候補と、買い候補・保有銘柄の備考で同じものを使う。
# 線を引く期間: 今日から数えて TREND_WINDOW_START_DAYS 〜 TREND_WINDOW_END_DAYS 営業日前(60〜3営業日前)
# 終わりを3営業日前にした理由: 山・谷の判定は前後 TREND_PIVOT_DAYS(3営業日)で行うので、3営業日前の山・谷までなら
# 今日以前の株価だけで判定できる(未来の株価を使わない)。はみ出しの判定は「結んだ2点の間だけ」にしたので、
# 直近5営業日(CROSS_NOTE_DAYS)の抜けと期間が重なっても、抜けた線が外されることはない。
TREND_WINDOW_START_DAYS: Final[int] = 60
TREND_WINDOW_END_DAYS: Final[int] = 3
# 線を引く期間の最低の営業日数(20営業日)。株価が少なく、期間がこれより短くなる場合は線を引かない
# (期間の始まりは、60営業日前と、データがある一番古い日から TREND_PIVOT_DAYS 後の日の新しいほう)
TREND_MIN_WINDOW_DAYS: Final[int] = 20
# 谷(山)の判定: 前後この営業日数の中で、安値が一番低い(高値が一番高い)日
TREND_PIVOT_DAYS: Final[int] = 3
# 線を結ぶ2つの谷(山)は、この営業日数以上離れていること
TREND_PIVOT_MIN_GAP_DAYS: Final[int] = 10
# 接触とみなす幅(2%)。安値(高値)が線からこの割合以内まで近づき、終値は線の内側で終わった日を接触とする
TREND_TOUCH_TOLERANCE: Final[float] = 0.02
# 抜けとみなす幅(3%)。結んだ2つの谷(山)の間に終値がこれ以上はみ出した線は使わない。
# 2つ目の谷(山)より後に終値が初めてこれ以上抜けた日を「抜けた日」とする。備考は終値がこれ以上抜けたときに出す
TREND_BREAK_RATE: Final[float] = 0.03
# 線の今日の値と今日の終値の離れ具合の上限(0.15 = 15%)。
# |線の今日の値 − 今日の終値| ÷ 今日の終値 がこれより大きい線は選ばない。線の今日の値が0以下の線も選ばない
# (最後の手段の線にも、この条件をかける)
TREND_MAX_DISTANCE_RATE: Final[float] = 0.15
# 抜けた線を候補に残す期間(営業日数。株価の行で数える)。抜けた日(break_at)が今日から数えてこの日数より前の線は、
# 候補から外す(古く抜けた線が接触回数の多さで選ばれ続けるのを防ぐ)。
# 備考の「直近5営業日以内の抜け」(CROSS_NOTE_DAYS)と合わせた値。
# 数え方は core/features.py の trendline_notes_on の抜けの判定(trendline.break_days_ago)と同じ:
# 今日を含めてこの日数の中(今日から数えて 0〜TREND_KEEP_BROKEN_DAYS-1 営業日前)に抜けた線は残す
TREND_KEEP_BROKEN_DAYS: Final[int] = 5
# 備考を出す線の接触回数の下限(3回)。線を結んだ2つの点だけ(2回)の線には出さない
TREND_MIN_TOUCHES: Final[int] = 3
# 画面のチャートに表示する線の接触回数の下限(2回。線を結んだ2つの点だけの線も表示する)
TREND_CHART_MIN_TOUCHES: Final[int] = 2
# 線がほぼ水平とみなす傾き(1営業日あたりの傾き ÷ 線の今日の値。0.0005 = 60営業日で約3%)。
# これより小さい場合は、備考に「(転換)」「(加速)」を付けない
TREND_FLAT_SLOPE: Final[float] = 0.0005

# =====================================================================
# J-Quants API
# =====================================================================

# J-Quants APIのURLの共通部分
JQUANTS_BASE_URL: Final[str] = "https://api.jquants.com/v2"
# 1分あたりのリクエスト上限。公表値は無料プラン 5、ライトプラン 60
# 無料プランで公表値の5を設定してエラー(429)となったので、公表値より1つ少なくする
# (無料プランは 4、ライトプランは 59。2026-11-06 にスタンダードプランからライトプランに変更)
API_RATE_LIMIT_PER_MIN: Final[int] = 59
# 財務情報(/fins/summary)だけの1分あたりのリクエスト上限。公表値はプランに関係なく 60 で、1つ少なくする。
# API_RATE_LIMIT_PER_MIN にも数えられるので、両方の上限を守る
API_RATE_LIMIT_FINS_PER_MIN: Final[int] = 59
# リクエストとリクエストの間にあける最低の間隔(秒)。無料プランは 16(60 ÷ 4 + 1)、
# ライトプランは 0.5(1秒に2件以下に均す)
API_REQUEST_INTERVAL_SEC: Final[float] = 0.5
# 429エラーのときに、やり直すまで待つ秒数
API_RETRY_WAIT_SEC: Final[int] = 120
# 429エラーのときに、やり直す最大回数
API_MAX_RETRY: Final[int] = 5
# 通信エラーや5xxエラーのときに、やり直す最大回数
API_ERROR_RETRY: Final[int] = 3
# 通信エラーや5xxエラーのときに、やり直すまで待つ秒数
API_ERROR_WAIT_SEC: Final[int] = 30
# API呼び出し1回のタイムアウト秒数
API_TIMEOUT_SEC: Final[int] = 60

# 財務情報(/fins/summary)の、翌事業年度の予想の項目名(カラム名 → J-Quants の項目名)
# 2026-10-18 の初回の収集で、実際のレスポンスの項目名を確かめて直した(NxFNP → NxFNp)。
# この名前がレスポンスにない場合は WARNING を出し、NxF で始まる実際の項目名をログに出す。
JQUANTS_FIN_NEXT_YEAR_FIELDS: Final[dict[str, str]] = {
    "nx_f_sales": "NxFSales",
    "nx_f_op": "NxFOP",
    "nx_f_np": "NxFNp",
    "nx_f_div_ann": "NxFDivAnn",
}

# 財務情報のうち、決算短信(四半期・通期)とみなす開示書類の種類(DocType)に含まれる文字列
# 2026-10-19 に2年分の財務情報を集計して確かめた。
# 例: FYFinancialStatements_Consolidated_JP、1QFinancialStatements_Consolidated_JP。
# 業績予想の修正(EarnForecastRevision など)は含めない。
FIN_STATEMENT_DOC_TYPE_KEYWORD: Final[str] = "FinancialStatements"

# =====================================================================
# 営業日・対象の市場
# =====================================================================

# 営業日の判定方法の値
BUSINESS_DAY_SOURCE_RULE: Final[str] = "RULE"
BUSINESS_DAY_SOURCE_CALENDAR: Final[str] = "CALENDAR"
# 営業日の判定方法。RULE: jpholiday と年末年始のルール(無料プランの間)。CALENDAR: 取引カレンダー(有料プラン)
BUSINESS_DAY_SOURCE: Final[str] = BUSINESS_DAY_SOURCE_CALENDAR
# 学習と予測の対象にする市場区分コード(新コード: プライム・スタンダード・グロース、旧コード: 東証一部など)
TARGET_MARKET_CODES: Final[list[str]] = ["0111", "0112", "0113", "0101", "0102", "0104", "0106", "0107"]
# 相場の状態(強い・普通・弱い。core/market_state_core.py)。25日移動平均より上の銘柄の割合で決める
# この値以上なら「強い」
MARKET_STATE_STRONG: Final[float] = 0.60
# この値より小さければ「弱い」(間は「普通」)
MARKET_STATE_WEAK: Final[float] = 0.40
# 移動平均の日数、騰落率の営業日数
MARKET_STATE_MA_DAYS: Final[int] = 25
MARKET_STATE_RET_DAYS: Final[int] = 20
# 株価を読む日数(暦日。25日分と20営業日前の株価がそろうよう、余裕を持たせる)
MARKET_STATE_LOAD_DAYS: Final[int] = 80
# 収集のとき、指定日からさかのぼって取り逃しを確認する日数
COLLECT_BACKFILL_DAYS: Final[int] = 31
# 日経平均(指数日足 index_daily_price。core/index_collect_core.py)。表示だけに使い、予測には使わない。
# yfinance(ワイファイナンス)で Yahoo Finance から取る(非公式。個人利用に限る)。ティッカー(Yahoo の指数コード)
INDEX_CODE_N225: Final[str] = "^N225"
# 取得に失敗したときにやり直す回数(最初の1回とは別)と、やり直すまでに待つ秒数
INDEX_FETCH_RETRY_COUNT: Final[int] = 3
INDEX_FETCH_RETRY_WAIT_SECONDS: Final[float] = 10
# 日経平均の向き(core/market_state_core.py)。直近この営業日数の終値に回帰直線を引く。
# 傾き(1営業日あたり、終値に対する割合)が TREND_FLAT_SLOPE より大きければ上向き、-TREND_FLAT_SLOPE より小さければ下向き
INDEX_TREND_DAYS: Final[list[int]] = [10, 20, 60]
# 日経平均の向きの計算で、株価日足の営業日を読む日数(暦日。60営業日に年末年始・連休の分の余裕を持たせる)
INDEX_TREND_LOAD_DAYS: Final[int] = 120
# 収集した株価の件数が、前回の件数にこの割合を掛けた値より少ない場合は失敗にする
COLLECT_MIN_COUNT_RATIO: Final[float] = 0.5
# 期間の収集(collect.py --from/--to、range_date_full_process.py)で、ネットワークなどの一時的なエラーで
# 取れなかった営業日がこの日数続いたら止める
COLLECT_MAX_NETWORK_FAIL_DAYS: Final[int] = 3
# 期間の収集で、DBに株価が1日もないまま、契約プランの期間外(403)としてとばした営業日がこの日数を超えたら止める。
# APIキーが無効な場合に、全ての日をとばして最後まで進むのを防ぐ
COLLECT_MAX_OUT_OF_RANGE_DAYS: Final[int] = 60
# 期間の収集で取れなかった日の一覧のファイル名(LOG_DIR に置く。実行ごとに1つ。BOM付きUTF-8)。
# {stamp} は実行を始めた日時(YYYYMMDD_HHMM)
COLLECT_FAILURE_FILE: Final[str] = "収集失敗_{stamp}.csv"
# 予測と学習の対象にするために必要な、株価がある営業日数
MIN_HISTORY_DAYS: Final[int] = 80

# =====================================================================
# 学習
# =====================================================================

# ボリンジャーバンドの日数(中心線の移動平均と、標準偏差を計算する日数)。特徴量 bb_pctb と、画面のチャートで使う。
# 変えた場合は、必ず学習し直してから予測する(古いモデルは、この日数で計算した bb_pctb で学習していないため)。
# 25日にするかは、評価ツールの改善案 U で、20日(L)と比べてから決める
BB_DAYS: Final[int] = 20
# 学習に使う期間(直近N年)
TRAIN_PERIOD_YEARS: Final[int] = 5
# 学習のとき、精度の確認用に分けておく直近の期間(月数)
VALID_PERIOD_MONTHS: Final[int] = 6
# 学習済みモデルのファイルを置くフォルダ(stock_prediction_batch からの相対パス)
MODEL_DIR: Final[str] = "models"
# 予測株価用のモデルの正解(騰落率)を頭打ちにする割合(上下それぞれ)
PRICE_TARGET_CLIP_RATE: Final[float] = 0.01
# LightGBM の設定値。objective はモデルの種類ごとにプログラムで決める
LGBM_PARAMS: Final[dict[str, float | int | bool]] = {
    "learning_rate": 0.05,
    "num_leaves": 63,
    "min_data_in_leaf": 500,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "seed": 42,
    "deterministic": True,
}
# 学習で作る木の最大の本数
LGBM_MAX_ROUNDS: Final[int] = 2000
# 確認用の成績がこの回数続けて良くならなければ、学習を止める
LGBM_EARLY_STOPPING_ROUNDS: Final[int] = 100

# =====================================================================
# 検証
# =====================================================================

# 的中率を集計する期間(1M: 直近1か月、3M: 直近3か月、ALL: 全期間)
VERIFY_SUMMARY_PERIODS: Final[list[str]] = ["1M", "3M", "ALL"]
# 上昇確率のずれを知らせる境目(5ポイント)
CALIBRATION_WARN_GAP: Final[float] = 0.05
# ずれを判定する帯に必要な、検証した件数の最低
CALIBRATION_MIN_COUNT: Final[int] = 500
# 上昇確率の補正の対応表を作るときに、確認用の期間の行をモデルの確率の順に分ける組の数
CALIBRATION_BIN_COUNT: Final[int] = 200
# 上昇確率の補正の対応表を作るのに使う、確認用の期間の最後の日からさかのぼる週数(1年分)
CALIBRATION_HISTORY_WEEKS: Final[int] = 52
# 補正用データ(確認用の期間の答え合わせ)を置くフォルダの名前。MODEL_DIR の直下に置く
CALIBRATION_DATA_DIR: Final[str] = "補正用データ"
# 組ごとの上がった割合を全体の上昇率に近づける強さ。(上がった件数 + α × 基準率) ÷ (件数 + α) の α
CALIBRATION_SHRINK_ALPHA: Final[int] = 1000
# 確率の順に並べた組の一番上と一番下で、最低限ほしい件数。足りなければ隣の組とまとめる
CALIBRATION_EDGE_MIN_COUNT: Final[int] = 5000

# =====================================================================
# 通知
# =====================================================================

# 流動性の判定に使う、平均売買代金の対象営業日数
LIQUIDITY_AVG_DAYS: Final[int] = 20
# 流動性注意を表示する平均売買代金の基準(円)。1億円
LIQUIDITY_WARN_TURNOVER: Final[int] = 100_000_000
# ゴールデンクロス・デッドクロスを備考に表示する期間(営業日)。
# 直近この日数以内にクロスした銘柄に表示する
CROSS_NOTE_DAYS: Final[int] = 5
# 特徴量の候補 days_since_cross_*(クロスしてからの営業日数)の上限。
# 予測で読む株価の期間(約105営業日)でも、学習と同じ値になるように上限を付ける
CROSS_DAYS_CAP: Final[int] = 20
# メールの件名の先頭に付ける文字列
MAIL_SUBJECT_PREFIX: Final[str] = "【株価予測】"
# Gmail のSMTPの設定
SMTP_HOST: Final[str] = "smtp.gmail.com"
SMTP_PORT: Final[int] = 587
# メールの内容を書き出すフォルダ(LOG_DIR の下)
NOTIFY_FILE_DIR: Final[str] = "通知"
# メールの種類。ファイル名の最後に付ける文字列を決める(通常: 通知_yyyy-MM-dd.txt)
MAIL_KIND_NORMAL: Final[str] = "NORMAL"
MAIL_KIND_FAILURE: Final[str] = "FAILURE"
MAIL_KIND_WARNING: Final[str] = "WARNING"
NOTIFY_FILE_SUFFIXES: Final[dict[str, str]] = {
    MAIL_KIND_NORMAL: "",
    MAIL_KIND_FAILURE: "_失敗",
    MAIL_KIND_WARNING: "_警告",
}
# 宛先の種類。NORMAL: MAIL_TO(毎日の通知・補完分・空振り)、ADMIN: ADMIN_MAIL_TO(失敗・警告)
MAIL_RECIPIENT_NORMAL: Final[str] = "NORMAL"
MAIL_RECIPIENT_ADMIN: Final[str] = "ADMIN"

# =====================================================================
# バックアップ・保持期間
# =====================================================================

# バックアップの保存先(NAS)。ドライブ文字ではなくサーバー名から書く
BACKUP_DIR: Final[str] = r"【バックアップ先のパスを指定】"
# NASに残すバックアップの世代数
BACKUP_KEEP_GENERATIONS: Final[int] = 12
# Cドライブに残す学習済みモデルの世代数
MODEL_KEEP_GENERATIONS: Final[int] = 12
# pg_dump の実行ファイルの場所(PostgreSQL のインストール先に合わせる)
PG_DUMP_PATH: Final[str] = r"C:\Program Files\PostgreSQL\18\bin\pg_dump.exe"
# 株価日足・バリュエーション指標をDBに残す年数
RETENTION_PRICE_YEARS: Final[int] = 20
# 予測・検証のレコードをDBに残す日数(20年。株価日足の保持期間 RETENTION_PRICE_YEARS とそろえる)
RETENTION_PREDICTION_DAYS: Final[int] = 7300
# 実行履歴をDBに残す日数(1年)
RETENTION_RUN_HISTORY_DAYS: Final[int] = 365
# ログファイル(batch_YYYYMMDD.log)を残す日数。収集失敗_*.csv と 全処理_通知_*.txt は消さない
RETENTION_LOG_DAYS: Final[int] = 30
# メールの内容のファイル(logs の 通知 フォルダの 通知_yyyy-MM-dd*.txt)を残す日数(2年)
RETENTION_NOTIFY_DAYS: Final[int] = 730
# クリーニングでフォルダを削除するとき、アクセスが拒否された場合にやり直す回数と、待つ秒数(NAS のごみ箱との競合の対策)
CLEANUP_DELETE_RETRY_COUNT: Final[int] = 3
CLEANUP_DELETE_RETRY_WAIT_SEC: Final[int] = 10

# =====================================================================
# ログ・実行履歴
# =====================================================================

# ログのレベル。開発中は DEBUG(1銘柄ごとに出力)、本番運用では INFO
LOG_LEVEL: Final[str] = "INFO"
# ログファイルを置くフォルダ(stock_prediction_batch からの相対パス)
LOG_DIR: Final[str] = "logs"
# tools/make_candidates_html.py で作る「買い候補と実際の株価」の HTML の出力先。
# 相対パスは stock_prediction_batch のフォルダが起点
CANDIDATES_HTML_DIR: Final[str] = "【調査用の資料の出力先のパスを記載する】"
# 実行履歴の RUNNING の行を、途中で止まった残りとみなすまでの時間
RUNNING_STALE_HOURS: Final[int] = 23
# 全銘柄をまとめて処理する箇所で、銘柄コードの代わりにログに書く文字列
ALL_CODES_LABEL: Final[str] = "全銘柄の一括処理"

# =====================================================================
# 区分の値
# =====================================================================

# 検証状態(prediction.verify_status)
VERIFY_STATUS_PENDING: Final[str] = "PENDING"
VERIFY_STATUS_VERIFIED: Final[str] = "VERIFIED"
VERIFY_STATUS_UNVERIFIABLE: Final[str] = "UNVERIFIABLE"

# データの種類(fetched_date.data_type)
DATA_TYPE_PRICE: Final[str] = "PRICE"
DATA_TYPE_MASTER: Final[str] = "MASTER"
DATA_TYPE_VALUATION: Final[str] = "VALUATION"
DATA_TYPE_FIN_SUMMARY: Final[str] = "FIN_SUMMARY"
DATA_TYPE_EARNINGS_DATE: Final[str] = "EARNINGS_DATE"
# 取引カレンダー。収集失敗の一覧だけで使う(fetched_date には登録しない)
DATA_TYPE_CALENDAR: Final[str] = "CALENDAR"
# データの種類の日本語(ログ、収集失敗の一覧に使う)
DATA_TYPE_LABELS: Final[dict[str, str]] = {
    DATA_TYPE_PRICE: "株価四本値",
    DATA_TYPE_MASTER: "上場銘柄一覧",
    DATA_TYPE_VALUATION: "バリュエーション指標",
    DATA_TYPE_FIN_SUMMARY: "財務情報",
    DATA_TYPE_EARNINGS_DATE: "決算発表予定日",
    DATA_TYPE_CALENDAR: "取引カレンダー",
}

# 取得の状態(fetched_date.status)
FETCH_STATUS_FETCHED: Final[str] = "FETCHED"
FETCH_STATUS_EMPTY: Final[str] = "EMPTY"

# モデルの種類(model.model_type)
MODEL_TYPE_UP_PROB: Final[str] = "UP_PROB"
MODEL_TYPE_PRICE: Final[str] = "PRICE"
MODEL_TYPE_REL_PROB: Final[str] = "REL_PROB"  # 市場比用
# 学習・予測するモデルの種類(この順に学習する)
MODEL_TYPES: Final[list[str]] = [MODEL_TYPE_UP_PROB, MODEL_TYPE_PRICE, MODEL_TYPE_REL_PROB]

# 起動のしかた(run_history.batch_type)
BATCH_TYPE_DAILY: Final[str] = "DAILY"
BATCH_TYPE_WEEKLY: Final[str] = "WEEKLY"
BATCH_TYPE_SINGLE: Final[str] = "SINGLE"

# 処理の名前(run_history.process_name)
PROCESS_COLLECT: Final[str] = "COLLECT"
PROCESS_TRAIN: Final[str] = "TRAIN"
PROCESS_PREDICT: Final[str] = "PREDICT"
PROCESS_VERIFY: Final[str] = "VERIFY"
PROCESS_NOTIFY: Final[str] = "NOTIFY"
PROCESS_BACKUP: Final[str] = "BACKUP"
PROCESS_CLEANUP: Final[str] = "CLEANUP"
PROCESS_DAILY_BATCH: Final[str] = "DAILY_BATCH"
PROCESS_WEEKLY_BATCH: Final[str] = "WEEKLY_BATCH"

# 処理の名前の日本語(ログ、失敗メールに使う)
PROCESS_LABELS: Final[dict[str, str]] = {
    PROCESS_COLLECT: "収集",
    PROCESS_TRAIN: "学習",
    PROCESS_PREDICT: "予測",
    PROCESS_VERIFY: "検証",
    PROCESS_NOTIFY: "通知",
    PROCESS_BACKUP: "バックアップ",
    PROCESS_CLEANUP: "クリーニング",
    PROCESS_DAILY_BATCH: "日次バッチ",
    PROCESS_WEEKLY_BATCH: "週次バッチ",
}
# range_date_full_process.py のログに書く処理の名前。実行履歴には書かない(中で呼ぶ各処理が、それぞれ書く)
RANGE_FULL_PROCESS_LABEL: Final[str] = "全処理"
# range_date_full_process.py で学習する間隔(週。1以上)。1 = 毎週、4 = 4週ごと。
# 最初の学習(--predict-from 以降で最初の土曜)から数えて、この週数ごとの土曜にだけ学習する。
# 学習しない土曜も、バックアップとクリーニングは行う。日次バッチ・週次バッチ(本番の毎週の学習)には影響しない
# 本番の週次バッチと同じく毎週学習する。何年分もやり直すときは、時間を短くするために 4 などにしてよい
RANGE_TRAIN_INTERVAL_WEEKS: Final[int] = 1
# range_date_full_process.py の通知の内容(メールのテキスト形式の本文)を、日付順に追記するファイル名(LOG_DIR に置く)。
# {date_from}・{date_to} は --from・--to(YYYY-MM-DD)
RANGE_NOTIFY_FILE: Final[str] = "全処理_通知_{date_from}_{date_to}.txt"

# 実行履歴の状態(run_history.status)
RUN_STATUS_RUNNING: Final[str] = "RUNNING"
RUN_STATUS_SUCCESS: Final[str] = "SUCCESS"
RUN_STATUS_EMPTY: Final[str] = "EMPTY"
RUN_STATUS_FAILED: Final[str] = "FAILED"

# 対象区分(verify_daily_summary.segment、verify_period_summary.segment)
SEGMENT_ALL: Final[str] = "ALL"
SEGMENT_TOP: Final[str] = "TOP"
SEGMENT_TOP_SECTOR: Final[str] = (
    "TOP_SECTOR"  # 買い候補(業種分散)。prediction.buy_rank_sector がある銘柄
)
SEGMENT_HIGH_PROB: Final[str] = "HIGH_PROB"

# 買い候補の種類(tools/make_candidates_html.py の --kind と、画面の API の kind)
CANDIDATE_KIND_TOP: Final[str] = "top"  # 買い候補(prediction.buy_rank)
CANDIDATE_KIND_SECTOR: Final[str] = "sector"  # 買い候補(業種分散。prediction.buy_rank_sector)

# 期間(verify_period_summary.period_type、verify_calibration_summary.period_type)
PERIOD_1M: Final[str] = "1M"
PERIOD_3M: Final[str] = "3M"
PERIOD_ALL: Final[str] = "ALL"
