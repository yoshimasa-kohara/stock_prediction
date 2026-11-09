-- =====================================================================
-- 株価予測システム テーブル作成(CREATE TABLE)
--   15テーブルを作成する
--   手動で実行する
--     psql -U <ユーザー名> -d stock_prediction -f etc\db\sql\create_tables.sql
--
--   【注意】各テーブルの CREATE TABLE の前に DROP TABLE があるので、実行すると15テーブル全ての
--   データが消える(収集した株価なども含む)。J-Quants の契約プランの期間より古いデータは取り直せない。
--   データが入っているDBで実行する前に、必ずバックアップを取る。
--   テーブル定義を後から変える場合は、このファイルを最新の定義に直し、
--   既存のDBに当てる変更用のSQLを別のファイルとしてこのフォルダに追加する
--   外部キーは設定しない
-- =====================================================================

-- このファイルは UTF-8 で書いている。Windows の psql は既定の文字コードが違う場合があるので、明示する
SET client_encoding = 'UTF8';

BEGIN;

-- ---------------------------------------------------------------------
-- 1. stock_master(銘柄マスタ)
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS stock_master;
CREATE TABLE stock_master (
    code            VARCHAR(10)  NOT NULL,
    name            VARCHAR(500) NOT NULL,
    name_en         VARCHAR(500),
    sector17_code   VARCHAR(10),
    sector17_name   VARCHAR(100),
    sector33_code   VARCHAR(10),
    sector33_name   VARCHAR(100),
    scale_category  VARCHAR(100),
    market_code     VARCHAR(10),
    market_name     VARCHAR(100),
    applied_date    DATE         NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT pk_stock_master PRIMARY KEY (code)
);
COMMENT ON TABLE  stock_master                IS '銘柄マスタ';
COMMENT ON COLUMN stock_master.code           IS '銘柄コード';
COMMENT ON COLUMN stock_master.name           IS '会社名';
COMMENT ON COLUMN stock_master.name_en        IS '会社名(英語)';
COMMENT ON COLUMN stock_master.sector17_code  IS '17業種コード';
COMMENT ON COLUMN stock_master.sector17_name  IS '17業種の名前';
COMMENT ON COLUMN stock_master.sector33_code  IS '33業種コード';
COMMENT ON COLUMN stock_master.sector33_name  IS '33業種の名前';
COMMENT ON COLUMN stock_master.scale_category IS '規模コード';
COMMENT ON COLUMN stock_master.market_code    IS '市場区分コード';
COMMENT ON COLUMN stock_master.market_name    IS '市場区分の名前';
COMMENT ON COLUMN stock_master.applied_date   IS '情報の適用日';
COMMENT ON COLUMN stock_master.created_at     IS '登録日時';
COMMENT ON COLUMN stock_master.updated_at     IS '更新日時';

-- ---------------------------------------------------------------------
-- 2. daily_price(株価日足)
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS daily_price;
CREATE TABLE daily_price (
    trade_date        DATE           NOT NULL,
    code              VARCHAR(10)    NOT NULL,
    open              NUMERIC(14,4),
    high              NUMERIC(14,4),
    low               NUMERIC(14,4),
    close             NUMERIC(14,4),
    volume            NUMERIC(20,0),
    turnover          NUMERIC(20,0),
    adj_factor        NUMERIC(20,10) NOT NULL,
    upper_limit_flag  BOOLEAN,
    lower_limit_flag  BOOLEAN,
    created_at        TIMESTAMPTZ    NOT NULL DEFAULT now(),
    CONSTRAINT pk_daily_price PRIMARY KEY (trade_date, code)
);
CREATE INDEX idx_daily_price_code_date ON daily_price (code, trade_date);
COMMENT ON TABLE  daily_price                  IS '株価日足';
COMMENT ON COLUMN daily_price.trade_date       IS '日付';
COMMENT ON COLUMN daily_price.code             IS '銘柄コード';
COMMENT ON COLUMN daily_price.open             IS '始値(調整前)';
COMMENT ON COLUMN daily_price.high             IS '高値(調整前)';
COMMENT ON COLUMN daily_price.low              IS '安値(調整前)';
COMMENT ON COLUMN daily_price.close            IS '終値(調整前)';
COMMENT ON COLUMN daily_price.volume           IS '出来高';
COMMENT ON COLUMN daily_price.turnover         IS '売買代金(円)';
COMMENT ON COLUMN daily_price.adj_factor       IS '調整係数';
COMMENT ON COLUMN daily_price.upper_limit_flag IS 'ストップ高';
COMMENT ON COLUMN daily_price.lower_limit_flag IS 'ストップ安';
COMMENT ON COLUMN daily_price.created_at       IS '登録日時';

-- ---------------------------------------------------------------------
-- 3. trading_calendar(取引カレンダー)
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS trading_calendar;
CREATE TABLE trading_calendar (
    cal_date          DATE        NOT NULL,
    holiday_division  VARCHAR(10) NOT NULL,
    is_business_day   BOOLEAN     NOT NULL,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_trading_calendar PRIMARY KEY (cal_date)
);
COMMENT ON TABLE  trading_calendar                  IS '取引カレンダー';
COMMENT ON COLUMN trading_calendar.cal_date         IS '日付';
COMMENT ON COLUMN trading_calendar.holiday_division IS '休日区分:0：非営業日、1：営業日、2：東証半日立会日、3：非営業日(祝日取引あり)';
COMMENT ON COLUMN trading_calendar.is_business_day  IS '営業日かどうか';
COMMENT ON COLUMN trading_calendar.updated_at       IS '更新日時';

-- ---------------------------------------------------------------------
-- 4. fetched_date(取得済み日付)
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS fetched_date;
CREATE TABLE fetched_date (
    target_date   DATE        NOT NULL,
    data_type     VARCHAR(20) NOT NULL,
    status        VARCHAR(20) NOT NULL,
    record_count  INTEGER     NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_fetched_date PRIMARY KEY (target_date, data_type)
);
COMMENT ON TABLE  fetched_date              IS '取得済み日付';
COMMENT ON COLUMN fetched_date.target_date  IS '対象の日付';
COMMENT ON COLUMN fetched_date.data_type    IS 'データの種類:PRICE：株価日足、MASTER：銘柄マスタ、VALUATION：バリュエーション指標、FIN_SUMMARY：財務情報、EARNINGS_DATE：決算発表予定日';
COMMENT ON COLUMN fetched_date.status       IS '状態:FETCHED：取得済み、EMPTY：空振り';
COMMENT ON COLUMN fetched_date.record_count IS '取得した件数';
COMMENT ON COLUMN fetched_date.fetched_at   IS '取得した日時';

-- ---------------------------------------------------------------------
-- 5. model(モデル)
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS model;
CREATE TABLE model (
    model_version     VARCHAR(20)   NOT NULL,
    horizon           SMALLINT      NOT NULL,
    model_type        VARCHAR(20)   NOT NULL,
    base_date         DATE          NOT NULL,
    train_start_date  DATE          NOT NULL,
    train_end_date    DATE          NOT NULL,
    train_rows        INTEGER       NOT NULL,
    feature_names     JSONB         NOT NULL,
    valid_score       NUMERIC(10,6),
    file_path         VARCHAR(500)  NOT NULL,
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT pk_model PRIMARY KEY (model_version, horizon, model_type)
);
CREATE INDEX idx_model_base_date ON model (base_date);
COMMENT ON TABLE  model                  IS 'モデル';
COMMENT ON COLUMN model.model_version    IS 'モデルバージョン';
COMMENT ON COLUMN model.horizon          IS '期間:予測日数';
COMMENT ON COLUMN model.model_type       IS 'モデルの種類:UP_PROB：上昇確率用、PRICE：予測株価用、REL_PROB：市場比用';
COMMENT ON COLUMN model.base_date        IS '学習の基準日';
COMMENT ON COLUMN model.train_start_date IS '学習データの開始日';
COMMENT ON COLUMN model.train_end_date   IS '学習データの終了日';
COMMENT ON COLUMN model.train_rows       IS '学習に使った行数';
COMMENT ON COLUMN model.feature_names    IS '特徴量の名前の一覧';
COMMENT ON COLUMN model.valid_score      IS '確認用の期間の成績';
COMMENT ON COLUMN model.file_path        IS 'ファイルの場所';
COMMENT ON COLUMN model.created_at       IS '登録日時';

-- ---------------------------------------------------------------------
-- 6. prediction(予測・検証)
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS prediction;
CREATE TABLE prediction (
    code           VARCHAR(10)   NOT NULL,
    pred_date      DATE          NOT NULL,
    horizon        SMALLINT      NOT NULL,
    target_date    DATE          NOT NULL,
    up_prob        NUMERIC(5,4)  NOT NULL,
    rel_prob       NUMERIC(5,4)  NOT NULL,
    pred_price     NUMERIC(14,4) NOT NULL,
    base_price     NUMERIC(14,4) NOT NULL,
    model_version  VARCHAR(20)   NOT NULL,
    sector33_code  VARCHAR(10),
    buy_rank       SMALLINT,
    buy_rank_sector SMALLINT,
    actual_price   NUMERIC(14,4),
    change_rate    NUMERIC(10,6),
    is_hit         BOOLEAN,
    verify_status  VARCHAR(20)   NOT NULL DEFAULT 'PENDING',
    created_at     TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT pk_prediction PRIMARY KEY (code, pred_date, horizon)
);
CREATE INDEX idx_prediction_target_status ON prediction (target_date, verify_status);
CREATE INDEX idx_prediction_pred_date ON prediction (pred_date);
COMMENT ON TABLE  prediction               IS '予測・検証';
COMMENT ON COLUMN prediction.code          IS '銘柄コード';
COMMENT ON COLUMN prediction.pred_date     IS '予測日';
COMMENT ON COLUMN prediction.horizon       IS '期間:予測日数';
COMMENT ON COLUMN prediction.target_date   IS '対象日';
COMMENT ON COLUMN prediction.up_prob       IS '確度:上昇確率';
COMMENT ON COLUMN prediction.rel_prob      IS '市場比の確率:同じ日の全銘柄の騰落率の中央値より上がる確率';
COMMENT ON COLUMN prediction.pred_price    IS '予測株価';
COMMENT ON COLUMN prediction.base_price    IS '予測時の株価';
COMMENT ON COLUMN prediction.model_version IS 'モデルバージョン';
COMMENT ON COLUMN prediction.sector33_code IS '33業種コード:予測した時点の銘柄マスタの値。業種分散の順位と業種の中央値に使う';
COMMENT ON COLUMN prediction.buy_rank      IS '買い候補の順位';
COMMENT ON COLUMN prediction.buy_rank_sector IS '買い候補(業種分散)の順位';
COMMENT ON COLUMN prediction.actual_price  IS '実株価';
COMMENT ON COLUMN prediction.change_rate   IS '騰落率';
COMMENT ON COLUMN prediction.is_hit        IS '的中フラグ';
COMMENT ON COLUMN prediction.verify_status IS '検証状態:PENDING：未検証、VERIFIED：検証済み、UNVERIFIABLE：検証不能';
COMMENT ON COLUMN prediction.created_at    IS '登録日時';
COMMENT ON COLUMN prediction.updated_at    IS '更新日時';

-- ---------------------------------------------------------------------
-- 7. verify_daily_summary(検証結果(予測日ごとの集計))
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS verify_daily_summary;
CREATE TABLE verify_daily_summary (
    pred_date             DATE          NOT NULL,
    horizon               SMALLINT      NOT NULL,
    segment               VARCHAR(20)   NOT NULL,
    target_date           DATE          NOT NULL,
    model_version         VARCHAR(20)   NOT NULL,
    verified_count        INTEGER       NOT NULL,
    hit_count             INTEGER       NOT NULL,
    up_count              INTEGER       NOT NULL,
    beat_count            INTEGER       NOT NULL,
    sum_change_rate       NUMERIC(20,6) NOT NULL,
    sum_excess_rate       NUMERIC(20,6) NOT NULL,
    market_median         NUMERIC(10,6),
    sector_verified_count INTEGER       NOT NULL,
    sector_beat_count     INTEGER       NOT NULL,
    sum_sector_excess_rate NUMERIC(20,6) NOT NULL,
    sum_price_error_rate  NUMERIC(20,6) NOT NULL,
    unverifiable_count    INTEGER       NOT NULL,
    updated_at            TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT pk_verify_daily_summary PRIMARY KEY (pred_date, horizon, segment)
);
CREATE INDEX idx_verify_daily_summary_target ON verify_daily_summary (target_date);
COMMENT ON TABLE  verify_daily_summary                      IS '検証結果(予測日ごとの集計)';
COMMENT ON COLUMN verify_daily_summary.pred_date            IS '予測日';
COMMENT ON COLUMN verify_daily_summary.horizon              IS '期間:予測日数';
COMMENT ON COLUMN verify_daily_summary.segment              IS '対象区分:ALL：全銘柄、TOP：買い候補の上位20銘柄、TOP_SECTOR：買い候補(業種分散)、HIGH_PROB：上昇確率が高い銘柄';
COMMENT ON COLUMN verify_daily_summary.target_date          IS '対象日';
COMMENT ON COLUMN verify_daily_summary.model_version        IS 'モデルバージョン';
COMMENT ON COLUMN verify_daily_summary.verified_count       IS '検証した件数';
COMMENT ON COLUMN verify_daily_summary.hit_count            IS '的中した件数';
COMMENT ON COLUMN verify_daily_summary.up_count             IS '実際に上がった件数';
COMMENT ON COLUMN verify_daily_summary.beat_count           IS '市場の中央値を上回った件数';
COMMENT ON COLUMN verify_daily_summary.sum_change_rate      IS '騰落率の合計';
COMMENT ON COLUMN verify_daily_summary.sum_excess_rate      IS '市場の中央値との差の合計';
COMMENT ON COLUMN verify_daily_summary.market_median        IS '市場の中央値';
COMMENT ON COLUMN verify_daily_summary.sector_verified_count IS '業種比を出せた件数:検証済みで33業種がある件数';
COMMENT ON COLUMN verify_daily_summary.sector_beat_count    IS '業種の中央値を上回った件数';
COMMENT ON COLUMN verify_daily_summary.sum_sector_excess_rate IS '業種の中央値との差の合計';
COMMENT ON COLUMN verify_daily_summary.sum_price_error_rate IS '予測株価の誤差率の合計';
COMMENT ON COLUMN verify_daily_summary.unverifiable_count   IS '検証不能の件数';
COMMENT ON COLUMN verify_daily_summary.updated_at           IS '更新日時';

-- ---------------------------------------------------------------------
-- 8. verify_period_summary(検証結果(期間の集計))
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS verify_period_summary;
CREATE TABLE verify_period_summary (
    summary_date          DATE          NOT NULL,
    period_type           VARCHAR(20)   NOT NULL,
    horizon               SMALLINT      NOT NULL,
    segment               VARCHAR(20)   NOT NULL,
    verified_count        INTEGER       NOT NULL,
    hit_count             INTEGER       NOT NULL,
    hit_rate              NUMERIC(6,4),
    up_rate               NUMERIC(6,4),
    beat_rate             NUMERIC(6,4),
    avg_change_rate       NUMERIC(10,6),
    avg_excess_rate       NUMERIC(10,6),
    sector_beat_rate      NUMERIC(6,4),
    avg_sector_excess_rate NUMERIC(10,6),
    avg_price_error_rate  NUMERIC(10,6),
    created_at            TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT pk_verify_period_summary PRIMARY KEY (summary_date, period_type, horizon, segment)
);
COMMENT ON TABLE  verify_period_summary                      IS '検証結果(期間の集計)';
COMMENT ON COLUMN verify_period_summary.summary_date         IS '集計の基準日';
COMMENT ON COLUMN verify_period_summary.period_type          IS '期間:1M：直近1か月、3M：直近3か月、ALL：全期間';
COMMENT ON COLUMN verify_period_summary.horizon              IS '期間:予測日数';
COMMENT ON COLUMN verify_period_summary.segment              IS '対象区分:ALL：全銘柄、TOP：買い候補の上位20銘柄、TOP_SECTOR：買い候補(業種分散)、HIGH_PROB：上昇確率が高い銘柄';
COMMENT ON COLUMN verify_period_summary.verified_count       IS '検証した件数';
COMMENT ON COLUMN verify_period_summary.hit_count            IS '的中した件数';
COMMENT ON COLUMN verify_period_summary.hit_rate             IS '的中率';
COMMENT ON COLUMN verify_period_summary.up_rate              IS '実際に上がった割合';
COMMENT ON COLUMN verify_period_summary.beat_rate            IS '市場の中央値を上回った割合';
COMMENT ON COLUMN verify_period_summary.avg_change_rate      IS '平均の騰落率';
COMMENT ON COLUMN verify_period_summary.avg_excess_rate      IS '市場の中央値との差の平均';
COMMENT ON COLUMN verify_period_summary.sector_beat_rate     IS '業種の中央値を上回った割合';
COMMENT ON COLUMN verify_period_summary.avg_sector_excess_rate IS '業種の中央値との差の平均';
COMMENT ON COLUMN verify_period_summary.avg_price_error_rate IS '予測株価の平均誤差率';
COMMENT ON COLUMN verify_period_summary.created_at           IS '登録日時';

-- ---------------------------------------------------------------------
-- 9. holding(保有銘柄)
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS holding;
CREATE TABLE holding (
    holding_id  SERIAL        NOT NULL,
    code        VARCHAR(10)   NOT NULL,
    buy_price   NUMERIC(14,4) NOT NULL,
    quantity    INTEGER       NOT NULL,
    buy_date    DATE          NOT NULL,
    sell_date   DATE,
    sell_price  NUMERIC(14,4),
    note        VARCHAR(500),
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT pk_holding PRIMARY KEY (holding_id)
);
COMMENT ON TABLE  holding            IS '保有銘柄';
COMMENT ON COLUMN holding.holding_id IS '保有ID';
COMMENT ON COLUMN holding.code       IS '銘柄コード';
COMMENT ON COLUMN holding.buy_price  IS '買値';
COMMENT ON COLUMN holding.quantity   IS '株数';
COMMENT ON COLUMN holding.buy_date   IS '購入日';
COMMENT ON COLUMN holding.sell_date  IS '売却日';
COMMENT ON COLUMN holding.sell_price IS '売値';
COMMENT ON COLUMN holding.note       IS 'メモ';
COMMENT ON COLUMN holding.created_at IS '登録日時';
COMMENT ON COLUMN holding.updated_at IS '更新日時';

-- ---------------------------------------------------------------------
-- 10. run_history(実行履歴)
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS run_history;
CREATE TABLE run_history (
    run_id         BIGSERIAL    NOT NULL,
    batch_type     VARCHAR(20)  NOT NULL,
    process_name   VARCHAR(20)  NOT NULL,
    target_date    DATE         NOT NULL,
    status         VARCHAR(20)  NOT NULL,
    started_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    record_count   INTEGER,
    error_code     VARCHAR(10),
    error_message  TEXT,
    CONSTRAINT pk_run_history PRIMARY KEY (run_id)
);
CREATE INDEX idx_run_history_status ON run_history (status);
COMMENT ON TABLE  run_history               IS '実行履歴';
COMMENT ON COLUMN run_history.run_id        IS '実行ID';
COMMENT ON COLUMN run_history.batch_type    IS '起動のしかた:DAILY：日次バッチ、WEEKLY：週次バッチ、SINGLE：実行ファイルを単独で起動';
COMMENT ON COLUMN run_history.process_name  IS '処理の名前:COLLECT、TRAIN、PREDICT、VERIFY、NOTIFY、BACKUP、CLEANUP、DAILY_BATCH、WEEKLY_BATCH';
COMMENT ON COLUMN run_history.target_date   IS '対象の日付';
COMMENT ON COLUMN run_history.status        IS '状態:RUNNING：実行中、SUCCESS：成功、EMPTY：空振り、FAILED：失敗';
COMMENT ON COLUMN run_history.started_at    IS '開始日時';
COMMENT ON COLUMN run_history.finished_at   IS '終了日時';
COMMENT ON COLUMN run_history.record_count  IS '処理した件数';
COMMENT ON COLUMN run_history.error_code    IS 'エラー時の銘柄コード';
COMMENT ON COLUMN run_history.error_message IS 'エラーの内容';

-- ---------------------------------------------------------------------
-- 11. daily_valuation(バリュエーション指標)
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS daily_valuation;
CREATE TABLE daily_valuation (
    trade_date  DATE          NOT NULL,
    code        VARCHAR(10)   NOT NULL,
    eps         NUMERIC(20,4),
    fwd_eps     NUMERIC(20,4),
    bps         NUMERIC(20,4),
    roe         NUMERIC(12,6),
    fwd_roe     NUMERIC(12,6),
    per         NUMERIC(14,4),
    fwd_per     NUMERIC(14,4),
    pbr         NUMERIC(14,4),
    market_cap  NUMERIC(20,0),
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT pk_daily_valuation PRIMARY KEY (trade_date, code)
);
COMMENT ON TABLE  daily_valuation            IS 'バリュエーション指標';
COMMENT ON COLUMN daily_valuation.trade_date IS '日付';
COMMENT ON COLUMN daily_valuation.code       IS '銘柄コード';
COMMENT ON COLUMN daily_valuation.eps        IS '1株あたり利益(実績)';
COMMENT ON COLUMN daily_valuation.fwd_eps    IS '1株あたり利益(予想)';
COMMENT ON COLUMN daily_valuation.bps        IS '1株あたり純資産';
COMMENT ON COLUMN daily_valuation.roe        IS 'ROE(実績)';
COMMENT ON COLUMN daily_valuation.fwd_roe    IS 'ROE(予想)';
COMMENT ON COLUMN daily_valuation.per        IS 'PER(実績)';
COMMENT ON COLUMN daily_valuation.fwd_per    IS 'PER(予想)';
COMMENT ON COLUMN daily_valuation.pbr        IS 'PBR';
COMMENT ON COLUMN daily_valuation.market_cap IS '時価総額(百万円)';
COMMENT ON COLUMN daily_valuation.created_at IS '登録日時';

-- ---------------------------------------------------------------------
-- 12. fin_summary(財務情報)
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS fin_summary;
CREATE TABLE fin_summary (
    disc_no       VARCHAR(30)   NOT NULL,
    code          VARCHAR(10)   NOT NULL,
    disc_date     DATE          NOT NULL,
    disc_time     TIME,
    doc_type      VARCHAR(100)  NOT NULL,
    cur_per_type  VARCHAR(10),
    cur_per_st    DATE,
    cur_per_en    DATE,
    cur_fy_st     DATE,
    cur_fy_en     DATE,
    sales         NUMERIC(24,0),
    op            NUMERIC(24,0),
    odp           NUMERIC(24,0),
    np            NUMERIC(24,0),
    eps           NUMERIC(20,4),
    bps           NUMERIC(20,4),
    equity_ratio  NUMERIC(12,6),
    f_sales       NUMERIC(24,0),
    f_op          NUMERIC(24,0),
    f_odp         NUMERIC(24,0),
    f_np          NUMERIC(24,0),
    f_eps         NUMERIC(20,4),
    f_div_ann     NUMERIC(20,4),
    nx_f_sales    NUMERIC(24,0),
    nx_f_op       NUMERIC(24,0),
    nx_f_np       NUMERIC(24,0),
    nx_f_div_ann  NUMERIC(20,4),
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT pk_fin_summary PRIMARY KEY (disc_no, code)
);
CREATE INDEX idx_fin_summary_code_date ON fin_summary (code, disc_date);
COMMENT ON TABLE  fin_summary              IS '財務情報';
COMMENT ON COLUMN fin_summary.disc_no      IS '開示番号';
COMMENT ON COLUMN fin_summary.code         IS '銘柄コード';
COMMENT ON COLUMN fin_summary.disc_date    IS '開示日';
COMMENT ON COLUMN fin_summary.disc_time    IS '開示時刻';
COMMENT ON COLUMN fin_summary.doc_type     IS '開示書類の種類';
COMMENT ON COLUMN fin_summary.cur_per_type IS '会計期間の種類:1Q、2Q、3Q、4Q、5Q、FY';
COMMENT ON COLUMN fin_summary.cur_per_st   IS '会計期間の開始日';
COMMENT ON COLUMN fin_summary.cur_per_en   IS '会計期間の終了日';
COMMENT ON COLUMN fin_summary.cur_fy_st    IS '事業年度の開始日';
COMMENT ON COLUMN fin_summary.cur_fy_en    IS '事業年度の終了日';
COMMENT ON COLUMN fin_summary.sales        IS '売上高';
COMMENT ON COLUMN fin_summary.op           IS '営業利益';
COMMENT ON COLUMN fin_summary.odp          IS '経常利益';
COMMENT ON COLUMN fin_summary.np           IS '当期純利益';
COMMENT ON COLUMN fin_summary.eps          IS '1株あたり利益';
COMMENT ON COLUMN fin_summary.bps          IS '1株あたり純資産';
COMMENT ON COLUMN fin_summary.equity_ratio IS '自己資本比率';
COMMENT ON COLUMN fin_summary.f_sales      IS '売上高の予想(今期)';
COMMENT ON COLUMN fin_summary.f_op         IS '営業利益の予想(今期)';
COMMENT ON COLUMN fin_summary.f_odp        IS '経常利益の予想(今期)';
COMMENT ON COLUMN fin_summary.f_np         IS '当期純利益の予想(今期)';
COMMENT ON COLUMN fin_summary.f_eps        IS '1株あたり利益予想(今期)';
COMMENT ON COLUMN fin_summary.f_div_ann    IS '年間配当の予想(今期)';
COMMENT ON COLUMN fin_summary.nx_f_sales   IS '売上高の予想(来期)';
COMMENT ON COLUMN fin_summary.nx_f_op      IS '営業利益の予想(来期)';
COMMENT ON COLUMN fin_summary.nx_f_np      IS '当期純利益の予想(来期)';
COMMENT ON COLUMN fin_summary.nx_f_div_ann IS '年間配当の予想(来期)';
COMMENT ON COLUMN fin_summary.created_at   IS '登録日時';

-- ---------------------------------------------------------------------
-- 13. earnings_date(決算発表予定日)
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS earnings_date;
CREATE TABLE earnings_date (
    code        VARCHAR(10) NOT NULL,
    pub_date    DATE        NOT NULL,
    fq_name     VARCHAR(10) NOT NULL,
    fye         VARCHAR(4)  NOT NULL,
    sch_date    DATE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_earnings_date PRIMARY KEY (code, pub_date, fq_name, fye)
);
COMMENT ON TABLE  earnings_date            IS '決算発表予定日';
COMMENT ON COLUMN earnings_date.code       IS '銘柄コード';
COMMENT ON COLUMN earnings_date.pub_date   IS '公表日';
COMMENT ON COLUMN earnings_date.fq_name    IS '決算区分:1Q、2Q、3Q、FY';
COMMENT ON COLUMN earnings_date.fye        IS '決算期末:MMDD';
COMMENT ON COLUMN earnings_date.sch_date   IS '決算発表予定日';
COMMENT ON COLUMN earnings_date.created_at IS '登録日時';

-- ---------------------------------------------------------------------
-- 14. verify_calibration_summary(検証結果(上昇確率の確かさ))
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS verify_calibration_summary;
CREATE TABLE verify_calibration_summary (
    summary_date    DATE         NOT NULL,
    period_type     VARCHAR(20)  NOT NULL,
    horizon         SMALLINT     NOT NULL,
    prob_band       SMALLINT     NOT NULL,
    verified_count  INTEGER      NOT NULL,
    up_count        INTEGER      NOT NULL,
    avg_up_prob     NUMERIC(6,4),
    up_rate         NUMERIC(6,4),
    gap             NUMERIC(6,4),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT pk_verify_calibration_summary PRIMARY KEY (summary_date, period_type, horizon, prob_band)
);
COMMENT ON TABLE  verify_calibration_summary                IS '検証結果(上昇確率の確かさ)';
COMMENT ON COLUMN verify_calibration_summary.summary_date   IS '集計の基準日';
COMMENT ON COLUMN verify_calibration_summary.period_type    IS '期間:3M：直近3か月、ALL：全期間';
COMMENT ON COLUMN verify_calibration_summary.horizon        IS '期間:予測日数';
COMMENT ON COLUMN verify_calibration_summary.prob_band      IS '確率の帯:下限の%';
COMMENT ON COLUMN verify_calibration_summary.verified_count IS '検証した件数';
COMMENT ON COLUMN verify_calibration_summary.up_count       IS '実際に上がった件数';
COMMENT ON COLUMN verify_calibration_summary.avg_up_prob    IS '確率の平均';
COMMENT ON COLUMN verify_calibration_summary.up_rate        IS '実際に上がった割合';
COMMENT ON COLUMN verify_calibration_summary.gap            IS 'ずれ';
COMMENT ON COLUMN verify_calibration_summary.created_at     IS '登録日時';

-- ---------------------------------------------------------------------
-- 15. index_daily_price(指数日足)
--   日経平均など。yfinance で Yahoo Finance から取る(非公式。個人利用に限る)。表示だけに使い、予測には使わない
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS index_daily_price;
CREATE TABLE index_daily_price (
    index_code  VARCHAR(20)    NOT NULL,
    trade_date  DATE           NOT NULL,
    open        NUMERIC(14,4),
    high        NUMERIC(14,4),
    low         NUMERIC(14,4),
    close       NUMERIC(14,4),
    created_at  TIMESTAMPTZ    NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ    NOT NULL DEFAULT now(),
    CONSTRAINT pk_index_daily_price PRIMARY KEY (index_code, trade_date)
);
COMMENT ON TABLE  index_daily_price             IS '指数日足';
COMMENT ON COLUMN index_daily_price.index_code  IS '指数コード:Yahoo Finance のティッカー(例: ^N225)';
COMMENT ON COLUMN index_daily_price.trade_date  IS '日付:東京の日付';
COMMENT ON COLUMN index_daily_price.open        IS '始値';
COMMENT ON COLUMN index_daily_price.high        IS '高値';
COMMENT ON COLUMN index_daily_price.low         IS '安値';
COMMENT ON COLUMN index_daily_price.close       IS '終値';
COMMENT ON COLUMN index_daily_price.created_at  IS '登録日時';
COMMENT ON COLUMN index_daily_price.updated_at  IS '更新日時';

COMMIT;
