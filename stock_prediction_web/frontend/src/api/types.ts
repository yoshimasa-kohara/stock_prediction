// API の返り値の型(API の pydantic のモデルと同じ形)

/** 画面の設定値(api/web_const.py) */
export interface WebSettings {
  status_refresh_sec: number;
  stock_search_limit: number;
  top_candidate_count: number;
  results_default_end_offset_days: number;
  results_default_span_days: number;
  results_max_months: number;
  /** 買い候補: 期間の初期値(予測がある日を新しい順に数えた日数)と、1回に表示できる最大の月数 */
  candidates_default_days: number;
  candidates_range_max_months: number;
  chart_default_period: string;
  accuracy_daily_default_months: number;
  compare_sector_top_n: number;
  runs_default_days: number;
  runs_page_size: number;
  screen_page_size: number;
  run_status_labels: Record<string, string>;
  batch_type_labels: Record<string, string>;
}

/** 画面で使うバッチの設定値(const.py) */
export interface BatchSettings {
  predict_horizons: number[];
  main_horizon: number;
  buy_candidate_count: number;
  /** 買い候補(業種分散)の銘柄数と、同じ33業種の上限 */
  buy_candidate_sector_count: number;
  sector_cap_per_sector: number;
  bb_days: number;
  up_prob_threshold: number;
  rel_prob_threshold: number;
  high_prob_threshold: number;
  calibration_min_count: number;
  calibration_warn_gap: number;
  process_labels: Record<string, string>;
}

/** GET /api/settings */
export interface Settings {
  web: WebSettings;
  batch: BatchSettings;
}

/** 実行中の実行履歴の行 */
export interface RunningRun {
  run_id: number;
  batch_type: string;
  process_name: string;
  target_date: string;
  started_at: string;
}

/** 日次バッチ・週次バッチの最新の実行履歴 */
export interface LatestRun {
  run_id: number;
  process_name: string;
  target_date: string;
  status: string;
  started_at: string;
  finished_at: string | null;
}

/** GET /api/status */
export interface Status {
  latest_pred_date: string | null;
  model_version: string | null;
  model_base_date: string | null;
  running: RunningRun[];
  latest_daily: LatestRun | null;
  latest_weekly: LatestRun | null;
}

/** GET /api/prediction-dates */
export interface PredictionDates {
  dates: string[];
}

/** 備考の1つ(color は red・blue・orange・green) */
export interface Note {
  text: string;
  color: string;
}

/** 予測日数ごとの値 */
export interface HorizonValue {
  horizon: number;
  rel_prob: number | null;
  up_prob: number | null;
  pred_price: number | null;
}

/** GET /api/runs の1行 */
export interface RunRow {
  run_id: number;
  batch_type: string;
  process_name: string;
  target_date: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  elapsed_sec: number | null;
  record_count: number | null;
  error_code: string | null;
  error_message: string | null;
}

/** GET /api/runs */
export interface RunsResponse {
  date_from: string;
  date_to: string;
  total: number;
  offset: number;
  has_more: boolean;
  rows: RunRow[];
}

/** 買い候補の種類(top: 買い候補、sector: 買い候補(業種分散)) */
export type CandidateKind = "top" | "sector";

/** 買い候補の1銘柄(sector は予測した時点の33業種の名前) */
export interface CandidateRow {
  rank: number;
  code: string;
  name: string;
  market: string;
  sector: string;
  base_price: number;
  horizons: HorizonValue[];
  notes: Note[];
}

/** 日経平均の向きの1期間分。slope は1営業日あたりの傾きを終値で割った割合(0.0021 = +0.21%/日) */
export interface IndexTrendItem {
  days: number;
  direction: "上向き" | "横ばい" | "下向き";
  slope: number;
}

/** 相場の状態。above25・ret20 は割合(0.321 = 32.1%) */
export interface MarketState {
  state: "強い" | "普通" | "弱い";
  above25: number;
  ret20: number;
  /** 日経平均の向き(10・20・60日。短い順)。出さない日は null */
  index_trend: IndexTrendItem[] | null;
}

/** GET /api/candidates */
export interface CandidatesResponse {
  kind: CandidateKind;
  pred_date: string;
  model_version: string;
  code_count: number;
  /** 上昇確率の条件を置き換えて選んだ日の注記。それ以外の日は null */
  fallback_note: string | null;
  /** 予測日の相場の状態。計算できない日は null */
  market_state: MarketState | null;
  candidates: CandidateRow[];
}

/** GET /api/candidates/range(予測がある日ごと。古い日が先) */
export type CandidatesRangeResponse = CandidatesResponse[];

/** 買い候補と実際の株価の、1銘柄・1予測日数 */
export interface ResultCell {
  horizon: number;
  target_date: string;
  rel_prob: number;
  up_prob: number;
  pred_price: number;
  actual: number | null;
}

/** 買い候補と実際の株価の1銘柄 */
export interface ResultRow {
  rank: number;
  code: string;
  name: string;
  market: string;
  base_price: number;
  cells: ResultCell[];
  notes: Note[];
}

/** 予測日1日分 */
export interface ResultDay {
  pred_date: string;
  model_version: string;
  rows: ResultRow[];
  summary: { horizon: number; market_median: number | null; beat_count: number | null }[];
}

/** GET /api/candidates/results */
export interface ResultsResponse {
  kind: CandidateKind;
  date_from: string;
  date_to: string;
  days: ResultDay[];
}

/** 銘柄の検索の候補 */
export interface StockItem {
  code: string;
  name: string;
  market: string;
  sector33: string;
}

/** GET /api/stocks/{code} */
export interface StockInfo {
  code: string;
  name: string;
  market: string;
  sector33: string;
  scale_category: string;
  is_target: boolean;
  price_date: string | null;
  close: number | null;
  prev_change: number | null;
  prev_change_rate: number | null;
  valuation_date: string | null;
  fwd_per: number | null;
  pbr: number | null;
  sector_median_per: number | null;
  roe: number | null;
  fwd_eps: number | null;
  market_cap: number | null;
  earnings_date: string | null;
  earnings_name: string | null;
  earnings_days: number | null;
  notes_date: string | null;
  notes: Note[];
}

/** チャートの1日分 */
export interface PriceRow {
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  ma5: number | null;
  ma25: number | null;
  ma75: number | null;
  bb_mid: number | null;
  bb_up1: number | null;
  bb_up2: number | null;
  bb_dn1: number | null;
  bb_dn2: number | null;
}

/** トレンドライン */
export interface TrendLine {
  kind: "SUPPORT" | "RESIST";
  touches: number;
  start_date: string;
  start_value: number;
  end_date: string;
  /** 右端の日の線の値(線の値が0円になる場合は、0円になる日で止めて 0) */
  end_value: number;
  /** 1営業日(チャートの1行)あたりの線の傾き(円) */
  slope: number;
  /** 抜けた日(2つ目の谷・山より後で、終値が初めて線を3%以上抜けた日)。表示する期間より前のこともある */
  break_date: string | null;
  /** 抜けた日の線の値 */
  break_value: number | null;
}

/** GET /api/stocks/{code}/prices */
export interface PricesResponse {
  code: string;
  period: string;
  bb_days: number;
  rows: PriceRow[];
  trendlines: TrendLine[];
}

/** 過去の予測と答え合わせの1行(予測日 × 予測日数) */
export interface HistoryRow {
  pred_date: string;
  horizon: number;
  target_date: string;
  up_prob: number;
  rel_prob: number;
  pred_price: number;
  base_price: number;
  actual_price: number | null;
  change_rate: number | null;
  is_hit: boolean | null;
  verify_status: string;
  buy_rank: number | null;
}

/** GET /api/stocks/{code}/predictions */
export interface PredictionsResponse {
  code: string;
  latest_date: string | null;
  latest_buy_rank: number | null;
  latest_base_price: number | null;
  latest: { horizon: number; target_date: string; rel_prob: number; up_prob: number; pred_price: number; expected: number | null }[];
  history: HistoryRow[];
  history_limit: number;
}

/** 保有銘柄の購入ごとの行 */
export interface Lot {
  holding_id: number;
  buy_price: number;
  quantity: number;
  buy_date: string;
  note: string | null;
  /** 買いの金額(買値 × 買い株数)と現在の金額(現在値 × 買い株数。現在値がなければ null) */
  buy_amount: number;
  current_amount: number | null;
  pnl_rate: number | null;
  pnl_amount: number | null;
}

/** 保有銘柄の銘柄ごとの合計の行 */
export interface HoldingGroup {
  code: string;
  name: string;
  market: string;
  quantity: number;
  avg_buy_price: number;
  current: number | null;
  current_date: string | null;
  cost: number;
  value: number | null;
  /** 買いの金額(平均買値 × 合計の株数)と現在の金額(現在値 × 合計の株数。現在値がなければ null) */
  buy_amount: number;
  current_amount: number | null;
  pnl_rate: number | null;
  pnl_amount: number | null;
  horizons: HorizonValue[];
  judgment: string;
  notes: Note[];
  lots: Lot[];
}

/** 売却済みの行 */
export interface SoldRow {
  holding_id: number;
  code: string;
  name: string;
  market: string;
  buy_price: number;
  quantity: number;
  buy_date: string;
  sell_price: number;
  /** 買いの金額(買値 × 売り株数)と売りの金額(売値 × 売り株数) */
  buy_amount: number;
  sell_amount: number;
  sell_date: string;
  current: number | null;
  pnl_rate: number | null;
  pnl_amount: number | null;
  held_days: number;
  note: string | null;
}

/** GET /api/holdings */
export interface HoldingsResponse {
  judge_date: string | null;
  groups: HoldingGroup[];
  totals: { cost: number; value: number; pnl_amount: number; pnl_rate: number | null };
  sold: SoldRow[];
  sold_count: number;
  sold_pnl_amount: number;
  /** 売却済みの買値の合計・売値の合計(値 × 売り株数の合計)と、損益(%)= 損益 ÷ 買値の合計 */
  sold_buy_amount: number;
  sold_sell_amount: number;
  sold_pnl_rate: number | null;
}

/** 登録・編集・売却の返り値 */
export interface HoldingSaved {
  holding_id: number;
  sold_holding_id: number | null;
  message: string;
}

/** 的中率の、1つの区分・1つの期間 */
export interface SegmentValues {
  verified_count: number;
  hit_rate: number | null;
  up_rate: number | null;
  beat_rate: number | null;
  avg_excess_rate: number | null;
  avg_price_error_rate: number | null;
  /** 業種の中央値を上回った割合と、差の平均(保有銘柄は null) */
  sector_beat_rate: number | null;
  avg_sector_excess_rate: number | null;
}

/** 的中率の1つの期間(top_sector は買い候補(業種分散)) */
export interface PeriodValues {
  period: string;
  all: SegmentValues | null;
  top: SegmentValues | null;
  top_sector: SegmentValues | null;
  high_prob: SegmentValues | null;
  holding: SegmentValues | null;
}

/** 上昇確率の確かさの帯 */
export interface CalibrationBand {
  prob_band: number;
  verified_count: number;
  avg_up_prob: number | null;
  up_rate: number | null;
  gap: number | null;
  warn: boolean;
}

/** 上昇確率のずれの注意 */
export interface CalibrationWarning {
  horizon: number;
  prob_band: number;
  verified_count: number;
  avg_up_prob: number | null;
  up_rate: number | null;
  gap: number | null;
}

/** GET /api/accuracy */
export interface AccuracyResponse {
  summary_date: string | null;
  summary_dates: string[];
  horizon: number;
  periods: PeriodValues[];
  calibration: Record<string, CalibrationBand[]>;
  warnings: CalibrationWarning[];
}

/** 買い候補の順位の帯(rank_from〜rank_to 位) */
export interface RankBand {
  rank_from: number;
  rank_to: number;
}

/** 買い候補 順位別 的中率の、1つの期間の表(rows は予測日数ごと。counts は bands と同じ順) */
export interface RankPeriod {
  period: string;
  period_start: string | null;
  rows: { horizon: number; counts: { verified_count: number; hit_count: number }[] }[];
}

/** GET /api/accuracy/rank(diff_bands は差を出す2つの帯。前 − 後) */
export interface RankResponse {
  summary_date: string | null;
  summary_dates: string[];
  bands: RankBand[];
  diff_bands: RankBand[];
  default_period: string;
  periods: RankPeriod[];
}

/** GET /api/accuracy/daily */
export interface DailyResponse {
  date_from: string;
  date_to: string;
  horizon: number;
  points: { pred_date: string; top_excess: number | null; all_hit_rate: number | null }[];
}

/** 精度の比較の、1つの予測日数・1つの対象区分(①・③) */
export interface CompareRow {
  horizon: number;
  segment: string;
  verified_count: number;
  up_rate: number | null;
  avg_change_rate: number | null;
  beat_rate: number | null;
  avg_excess_rate: number | null;
  sector_beat_rate: number | null;
  avg_sector_excess_rate: number | null;
  first_pred_date: string | null;
  last_pred_date: string | null;
  code_count: number;
}

/** 精度の比較の、1つの買い候補の業種の内訳(④) */
export interface SectorBreakdown {
  segment: string;
  total: number;
  sector_count: number;
  top: { sector: string; count: number; share: number }[];
  other_count: number;
  other_share: number | null;
}

/** 精度の比較の、予測月ごと・対象区分ごと(⑤。主となる日数) */
export interface MonthlyRow {
  month: string;
  segment: string;
  verified_count: number;
  up_rate: number | null;
  avg_excess_rate: number | null;
  avg_sector_excess_rate: number | null;
}

/** 精度の比較の、使ったモデル(⑥) */
export interface ModelRow {
  model_version: string;
  first_pred_date: string;
  last_pred_date: string;
  base_date: string | null;
  train_start_date: string | null;
  train_end_date: string | null;
}

/** GET /api/accuracy/compare */
export interface CompareResponse {
  summary_date: string | null;
  summary_dates: string[];
  period: string;
  period_start: string | null;
  main_horizon: number;
  rows: CompareRow[];
  sectors: SectorBreakdown[];
  monthly: MonthlyRow[];
  models: ModelRow[];
}

/** 条件の選択肢 */
export interface Option {
  value: string;
  label: string;
}

/** 銘柄の検索の1銘柄 */
export interface ScreenRow {
  rank: number | null;
  code: string;
  name: string;
  market: string;
  sector33: string;
  base_price: number;
  horizons: HorizonValue[];
  notes: Note[];
}

/** GET /api/screen */
export interface ScreenResponse {
  pred_date: string;
  code_count: number;
  total: number;
  offset: number;
  has_more: boolean;
  sort: string;
  order: string;
  rows: ScreenRow[];
  market_options: string[];
  sector_options: Option[];
  note_options: Option[];
  exclude_options: Option[];
}
