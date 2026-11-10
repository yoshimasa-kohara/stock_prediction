// テストで API が返す値(全て架空のもの)
import type {
  AccuracyResponse,
  CandidatesResponse,
  CompareResponse,
  DailyResponse,
  HoldingsResponse,
  HorizonValue,
  PredictionsResponse,
  PricesResponse,
  RankResponse,
  ResultsResponse,
  RunsResponse,
  ScreenResponse,
  StockInfo,
} from "../api/types";

/** 予測日数ごとの値を作る */
export function horizons(rel: number, up: number, price: number): HorizonValue[] {
  return [5, 10, 20, 60].map((h, i) => ({ horizon: h, rel_prob: rel, up_prob: up + i / 100, pred_price: price + i }));
}

export const PRED_DATES = { dates: ["2026-07-17", "2026-07-21", "2026-07-22"] };

/** 日経平均の向きがある日の相場の状態 */
export const MARKET_STATE_WITH_TREND: CandidatesResponse["market_state"] = {
  state: "弱い",
  above25: 0.321,
  ret20: -0.021,
  index_trend: [
    { days: 10, direction: "上向き", slope: 0.0021 },
    { days: 20, direction: "横ばい", slope: 0.0003 },
    { days: 60, direction: "下向き", slope: -0.0008 },
  ],
};
export const INDEX_TREND_TEXT = "日経平均の向き: 10日 上向き(+0.21%/日)/20日 横ばい(+0.03%/日)/60日 下向き(-0.08%/日)";

export const CANDIDATES: CandidatesResponse = {
  kind: "top",
  pred_date: "2026-07-22",
  model_version: "20260718",
  code_count: 3961,
  fallback_note: null,
  market_state: { state: "弱い", above25: 0.321, ret20: -0.021, index_trend: null },
  candidates: [
    {
      rank: 1,
      code: "11110",
      name: "サンプル銀行A",
      market: "プライム",
      sector: "銀行業",
      base_price: 3945,
      horizons: horizons(0.878, 0.611, 4189.2),
      notes: [
        { text: "ストップ高", color: "red" },
        { text: "流動性注意", color: "green" },
        { text: "短期デッドクロス発生", color: "blue" },
      ],
    },
    { rank: 2, code: "11120", name: "サンプル商事A", market: "スタンダード", sector: "卸売業", base_price: 443, horizons: horizons(0.866, 0.529, 456.1), notes: [] },
    { rank: 3, code: "11130", name: "サンプル電機A", market: "グロース", sector: "電気機器", base_price: 1868, horizons: horizons(0.841, 0.6, 1962.6), notes: [] },
  ],
};

export const RESULTS: ResultsResponse = {
  kind: "top",
  date_from: "2026-04-22",
  date_to: "2026-07-22",
  days: [
    {
      pred_date: "2026-04-20",
      model_version: "20260418",
      rows: [
        {
          rank: 1,
          code: "11110",
          name: "サンプル銀行A",
          market: "プライム",
          base_price: 1000,
          cells: [
            { horizon: 5, target_date: "2026-04-27", rel_prob: 0.75, up_prob: 0.63, pred_price: 1020, actual: 1100 },
            { horizon: 10, target_date: "2026-05-07", rel_prob: 0.81, up_prob: 0.57, pred_price: 1030, actual: 950 },
            { horizon: 20, target_date: "2026-05-19", rel_prob: 0.88, up_prob: 0.61, pred_price: 1040, actual: 1000 },
            { horizon: 60, target_date: "2026-07-14", rel_prob: 0.68, up_prob: 0.63, pred_price: 1050, actual: null },
          ],
          notes: [{ text: "ROE8%以上(稼ぐ力が高い)", color: "red" }],
        },
        {
          rank: 2,
          code: "11120",
          name: "サンプル商事A",
          market: "スタンダード",
          base_price: 500,
          cells: [{ horizon: 5, target_date: "2026-04-27", rel_prob: 0.74, up_prob: 0.68, pred_price: 510, actual: 490 }],
          notes: [],
        },
      ],
      summary: [
        { horizon: 5, market_median: 0.015, beat_count: 1 },
        { horizon: 10, market_median: null, beat_count: null },
      ],
    },
  ],
};

export const RUNS: RunsResponse = {
  date_from: "2026-07-16",
  date_to: "2026-07-22",
  total: 3,
  offset: 0,
  has_more: true,
  rows: [
    {
      run_id: 1314,
      batch_type: "DAILY",
      process_name: "NOTIFY",
      target_date: "2026-07-22",
      status: "SUCCESS",
      started_at: "2026-07-22T19:40:51+09:00",
      finished_at: "2026-07-22T19:41:10+09:00",
      elapsed_sec: 19,
      record_count: 25,
      error_code: null,
      error_message: null,
    },
    {
      run_id: 1300,
      batch_type: "SINGLE",
      process_name: "PREDICT",
      target_date: "2026-07-20",
      status: "FAILED",
      started_at: "2026-07-20T21:14:30+09:00",
      finished_at: "2026-07-20T21:15:02+09:00",
      elapsed_sec: 4980,
      record_count: 0,
      error_code: "11250",
      error_message: "DBへの接続がタイムアウトしました。サーバーの負荷が高い可能性があります。しばらく待ってからもう一度実行してください",
    },
  ],
};

export const RUNS_MORE: RunsResponse = {
  ...RUNS,
  offset: 2,
  has_more: false,
  rows: [
    {
      run_id: 1296,
      batch_type: "WEEKLY",
      process_name: "DAILY_BATCH",
      target_date: "2026-07-18",
      status: "RUNNING",
      started_at: "2026-07-18T19:00:06+09:00",
      finished_at: null,
      elapsed_sec: null,
      record_count: null,
      error_code: null,
      error_message: "短いエラー",
    },
  ],
};

export const STOCK: StockInfo = {
  code: "11110",
  name: "サンプル銀行A",
  market: "プライム",
  sector33: "銀行業",
  scale_category: "TOPIX Small 1",
  is_target: true,
  price_date: "2026-07-22",
  close: 3945,
  prev_change: 59.2,
  prev_change_rate: 0.015,
  valuation_date: "2026-07-22",
  fwd_per: 7.9,
  pbr: 0.46,
  sector_median_per: 11.8,
  roe: 0.086,
  fwd_eps: 274.1,
  market_cap: 182000000000,
  earnings_date: "2026-08-07",
  earnings_name: "1Q",
  earnings_days: 12,
  notes_date: "2026-07-22",
  notes: [{ text: "PBR0.5倍割れ(割安)", color: "red" }],
};

export const PRICES: PricesResponse = {
  code: "11110",
  period: "6M",
  bb_days: 20,
  rows: [
    { date: "2026-07-21", open: 3900, high: 3950, low: 3880, close: 3885.8, volume: 1380000, ma5: 3870, ma25: 3800, ma75: 3700, bb_mid: 3800, bb_up1: 3850, bb_up2: 3900, bb_dn1: 3750, bb_dn2: 3700 },
    { date: "2026-07-22", open: 3890, high: 3960, low: 3870, close: 3945, volume: 900000, ma5: 3900, ma25: 3810, ma75: 3710, bb_mid: 3810, bb_up1: 3860, bb_up2: 3910, bb_dn1: 3760, bb_dn2: 3710 },
    { date: "2026-07-23", open: null, high: null, low: null, close: null, volume: null, ma5: null, ma25: null, ma75: null, bb_mid: null, bb_up1: null, bb_up2: null, bb_dn1: null, bb_dn2: null },
  ],
  trendlines: [
    { kind: "RESIST", touches: 4, start_date: "2026-07-21", start_value: 3990, end_date: "2026-07-23", end_value: 3970, slope: -10, break_date: null, break_value: null },
    // 2026-07-22 に下抜けした線(抜けた日より後は薄い線)
    { kind: "SUPPORT", touches: 2, start_date: "2026-07-21", start_value: 3800, end_date: "2026-07-23", end_value: 3840, slope: 20, break_date: "2026-07-22", break_value: 3820 },
  ],
};

/** 日経平均のチャートの値(/api/index/prices。出来高は null) */
export const INDEX_PRICES: PricesResponse = {
  code: "^N225",
  period: "6M",
  bb_days: 20,
  rows: [
    { date: "2026-07-21", open: 39900, high: 40100, low: 39800, close: 40050.5, volume: null, ma5: 39800, ma25: 39500, ma75: 39000, bb_mid: 39500, bb_up1: 39900, bb_up2: 40300, bb_dn1: 39100, bb_dn2: 38700 },
    { date: "2026-07-22", open: 40050, high: 40300, low: 39950, close: 40210, volume: null, ma5: 39900, ma25: 39550, ma75: 39050, bb_mid: 39550, bb_up1: 39950, bb_up2: 40350, bb_dn1: 39150, bb_dn2: 38750 },
  ],
  trendlines: [
    { kind: "SUPPORT", touches: 3, start_date: "2026-07-21", start_value: 39700, end_date: "2026-07-22", end_value: 39750, slope: 50, break_date: null, break_value: null },
  ],
};

export const PREDICTIONS: PredictionsResponse = {
  code: "11110",
  latest_date: "2026-07-22",
  latest_buy_rank: 1,
  latest_base_price: 3945,
  latest: [
    { horizon: 5, target_date: "2026-07-29", rel_prob: 0.754, up_prob: 0.631, pred_price: 4026.3, expected: 0.0206 },
    { horizon: 20, target_date: "2026-08-19", rel_prob: 0.878, up_prob: 0.611, pred_price: 4189.2, expected: 0.0619 },
  ],
  history: [
    { pred_date: "2026-07-22", horizon: 20, target_date: "2026-08-19", up_prob: 0.611, rel_prob: 0.878, pred_price: 4189.2, base_price: 3945, actual_price: null, change_rate: null, is_hit: null, verify_status: "PENDING", buy_rank: 1 },
    { pred_date: "2026-07-15", horizon: 5, target_date: "2026-07-22", up_prob: 0.574, rel_prob: 0.7, pred_price: 3885.8, base_price: 3795.1, actual_price: 3945, change_rate: 0.04, is_hit: true, verify_status: "VERIFIED", buy_rank: 5 },
    { pred_date: "2026-07-01", horizon: 20, target_date: "2026-07-29", up_prob: 0.531, rel_prob: 0.6, pred_price: 3708.3, base_price: 3739.9, actual_price: 3672.8, change_rate: -0.018, is_hit: false, verify_status: "VERIFIED", buy_rank: null },
    { pred_date: "2026-06-30", horizon: 20, target_date: "2026-07-28", up_prob: 0.5, rel_prob: 0.5, pred_price: 3700, base_price: 3700, actual_price: null, change_rate: null, is_hit: null, verify_status: "UNVERIFIABLE", buy_rank: null },
  ],
  history_limit: 4,
};

export const HOLDINGS: HoldingsResponse = {
  judge_date: "2026-07-22",
  groups: [
    {
      code: "11340",
      name: "サンプル工業B",
      market: "プライム",
      quantity: 200,
      avg_buy_price: 1235.4,
      current: 1186,
      current_date: "2026-07-22",
      cost: 247080,
      value: 237200,
      buy_amount: 247080,
      current_amount: 237200,
      pnl_rate: -0.04,
      pnl_amount: -9880,
      horizons: horizons(0.726, 0.573, 1221.6),
      judgment: "保持(注意: 上昇予測だが下降しています)",
      notes: [{ text: "短期ゴールデンクロス発生", color: "red" }],
      lots: [
        { holding_id: 11, buy_price: 1309.5, quantity: 100, buy_date: "2026-06-01", note: "1回目", buy_amount: 130950, current_amount: 118600, pnl_rate: -0.094, pnl_amount: -12350 },
        { holding_id: 12, buy_price: 1161.3, quantity: 100, buy_date: "2026-06-29", note: null, buy_amount: 116130, current_amount: 118600, pnl_rate: 0.021, pnl_amount: 2470 },
      ],
    },
    {
      code: "11360",
      name: "サンプル商事C",
      market: "プライム",
      quantity: 300,
      avg_buy_price: 2498.9,
      current: 2219,
      current_date: "2026-07-22",
      cost: 749670,
      value: 665700,
      buy_amount: 749670,
      current_amount: 665700,
      pnl_rate: -0.112,
      pnl_amount: -83970,
      horizons: horizons(0.382, 0.431, 2174.6),
      judgment: "売却(下降予測、市場の中央値も下回る予測)",
      notes: [],
      lots: [{ holding_id: 13, buy_price: 2498.9, quantity: 300, buy_date: "2026-05-20", note: null, buy_amount: 749670, current_amount: 665700, pnl_rate: -0.112, pnl_amount: -83970 }],
    },
  ],
  totals: { cost: 996750, value: 902900, pnl_amount: -93850, pnl_rate: -0.094 },
  sold: [
    {
      holding_id: 9,
      code: "11300",
      name: "サンプル物産B",
      market: "グロース",
      buy_price: 1520,
      quantity: 100,
      buy_date: "2026-04-14",
      sell_price: 1688,
      buy_amount: 152000,
      sell_amount: 168800,
      sell_date: "2026-06-12",
      current: 1702,
      pnl_rate: 0.111,
      pnl_amount: 16800,
      held_days: 59,
      note: null,
    },
  ],
  sold_count: 1,
  sold_pnl_amount: 16800,
  sold_buy_amount: 152000,
  sold_sell_amount: 168800,
  sold_pnl_rate: 0.1105,
};

const SEG = {
  verified_count: 1000,
  hit_rate: 0.538,
  up_rate: 0.5,
  beat_rate: 0.785,
  avg_excess_rate: 0.059,
  avg_price_error_rate: 0.04,
  sector_beat_rate: 0.579,
  avg_sector_excess_rate: 0.0185,
};

export const ACCURACY: AccuracyResponse = {
  summary_date: "2026-07-22",
  summary_dates: ["2026-07-21", "2026-07-22"],
  horizon: 20,
  periods: ["1M", "3M", "ALL"].map((p) => ({
    period: p,
    all: SEG,
    top: { ...SEG, verified_count: 60, hit_rate: 0.62 },
    top_sector: { ...SEG, verified_count: 90, hit_rate: 0.6, sector_beat_rate: 0.544, avg_sector_excess_rate: -0.016 },
    high_prob: p === "ALL" ? null : { ...SEG, verified_count: 30, hit_rate: 0.7 },
    holding:
      p === "1M"
        ? null
        : { ...SEG, verified_count: 20, hit_rate: 0.6, avg_excess_rate: -0.012, sector_beat_rate: null, avg_sector_excess_rate: null },
  })),
  calibration: {
    "3M": [
      { prob_band: 50, verified_count: 800, avg_up_prob: 0.55, up_rate: 0.58, gap: 0.03, warn: false },
      { prob_band: 60, verified_count: 900, avg_up_prob: 0.634, up_rate: 0.454, gap: -0.18, warn: true },
      { prob_band: 70, verified_count: 10, avg_up_prob: 0.72, up_rate: 0.72, gap: 0, warn: false },
      { prob_band: 80, verified_count: 0, avg_up_prob: null, up_rate: null, gap: null, warn: false },
    ],
    ALL: [{ prob_band: 50, verified_count: 1800, avg_up_prob: 0.55, up_rate: 0.56, gap: 0.01, warn: false }],
  },
  warnings: [{ horizon: 20, prob_band: 60, verified_count: 30120, avg_up_prob: 0.634, up_rate: 0.454, gap: -0.18 }],
};

export const ACCURACY_EMPTY: AccuracyResponse = {
  summary_date: null,
  summary_dates: [],
  horizon: 20,
  periods: [],
  calibration: {},
  warnings: [],
};

export const DAILY: DailyResponse = {
  date_from: "2026-04-22",
  date_to: "2026-07-22",
  horizon: 20,
  points: [
    { pred_date: "2026-04-22", top_excess: 0.05, all_hit_rate: 0.52 },
    { pred_date: "2026-04-23", top_excess: -0.01, all_hit_rate: 0.55 },
  ],
};

/** 帯(1〜20・1〜5・6〜10・11〜15・16〜20位)ごとの (件数, 的中) から、1つの予測日数の行を作る */
function rankRow(horizon: number, counts: [number, number][]) {
  return { horizon, counts: counts.map(([n, hit]) => ({ verified_count: n, hit_count: hit })) };
}

/** GET /api/accuracy/rank。全期間: 5日は差がプラス、10日は差がマイナス、20日は16〜20位が0件、60日は全て0件 */
export const RANK: RankResponse = {
  summary_date: "2026-07-22",
  summary_dates: ["2026-07-21", "2026-07-22"],
  bands: [
    { rank_from: 1, rank_to: 20 },
    { rank_from: 1, rank_to: 5 },
    { rank_from: 6, rank_to: 10 },
    { rank_from: 11, rank_to: 15 },
    { rank_from: 16, rank_to: 20 },
  ],
  diff_bands: [
    { rank_from: 1, rank_to: 5 },
    { rank_from: 16, rank_to: 20 },
  ],
  default_period: "1M",
  periods: ["1M", "3M", "6M", "1Y", "2Y", "ALL"].map((period, i) => ({
    period,
    period_start: period === "ALL" ? null : "2026-06-22",
    rows:
      period === "ALL"
        ? [
            // 1〜5位 7/11 = 63.6%、16〜20位 6/10 = 60.0% → +3.6
            rankRow(5, [[42, 26], [11, 7], [11, 7], [10, 6], [10, 6]]),
            // 1〜5位 50.0%、16〜20位 50.4% → -0.4
            rankRow(10, [[1500, 752], [500, 250], [250, 125], [250, 125], [500, 252]]),
            rankRow(20, [[10, 5], [5, 3], [3, 1], [2, 1], [0, 0]]),
            rankRow(60, [[0, 0], [0, 0], [0, 0], [0, 0], [0, 0]]),
          ]
        : [5, 10, 20, 60].map((h) => rankRow(h, [[20 + i, 10], [5, 4], [5, 2], [5, 2], [5, 1]])),
  })),
};

export const SCREEN: ScreenResponse = {
  pred_date: "2026-07-22",
  code_count: 3961,
  total: 3,
  offset: 0,
  has_more: true,
  sort: "rel_20",
  order: "desc",
  rows: CANDIDATES.candidates.slice(0, 2).map((c) => ({ ...c, sector33: "銀行業" })),
  market_options: ["プライム", "スタンダード", "グロース"],
  sector_options: [
    { value: "3050", label: "銀行業" },
    { value: "3650", label: "電気機器" },
  ],
  note_options: [
    { value: "pbr", label: "PBR0.5倍割れ(割安)" },
    { value: "resist_break", label: "上値抵抗線を上抜け" },
  ],
  exclude_options: [
    { value: "low_liquidity", label: "流動性注意を除く" },
    { value: "deficit", label: "赤字予想(業績注意)を除く" },
  ],
};

export const SCREEN_MORE: ScreenResponse = {
  ...SCREEN,
  offset: 2,
  has_more: false,
  rows: [{ ...CANDIDATES.candidates[2]!, rank: null, sector33: "電気機器" }],
};

/** 精度の比較(GET /api/accuracy/compare) */
export const COMPARE: CompareResponse = {
  summary_date: "2026-07-22",
  summary_dates: ["2026-07-21", "2026-07-22"],
  period: "ALL",
  period_start: null,
  main_horizon: 20,
  rows: [5, 10, 20, 60].flatMap((h) =>
    (["ALL", "TOP", "TOP_SECTOR"] as const).map((seg, i) => ({
      horizon: h,
      segment: seg,
      verified_count: [420006, 2280, 3410][i]!,
      up_rate: [0.46, 0.747, 0.543][i]!,
      avg_change_rate: [0.0045, 0.062, 0.0146][i]!,
      beat_rate: [0.5, 0.813, 0.582][i]!,
      avg_excess_rate: [0, 0.0682, 0.0208][i]!,
      sector_beat_rate: [0.48, 0.579, 0.544][i]!,
      avg_sector_excess_rate: [-0.001, 0.0185, -0.016][i]!,
      first_pred_date: "2026-01-05",
      last_pred_date: h === 60 ? "2026-04-16" : "2026-06-23",
      code_count: [3961, 75, 410][i]!,
    })),
  ),
  sectors: [
    {
      segment: "TOP",
      total: 2662,
      sector_count: 9,
      top: [
        { sector: "銀行業", count: 2614, share: 0.982 },
        { sector: "電気機器", count: 16, share: 0.006 },
      ],
      other_count: 32,
      other_share: 0.012,
    },
    { segment: "TOP_SECTOR", total: 0, sector_count: 0, top: [], other_count: 0, other_share: null },
  ],
  monthly: [
    { month: "2026-01", segment: "TOP", verified_count: 380, up_rate: 0.989, avg_excess_rate: 0.1454, avg_sector_excess_rate: 0.0173 },
    { month: "2026-02", segment: "TOP_SECTOR", verified_count: 360, up_rate: 0.256, avg_excess_rate: -0.0137, avg_sector_excess_rate: null },
  ],
  models: [
    {
      model_version: "20260103",
      first_pred_date: "2026-01-05",
      last_pred_date: "2026-01-09",
      base_date: "2026-01-03",
      train_start_date: "2024-07-29",
      train_end_date: "2025-12-26",
    },
    { model_version: "20260110", first_pred_date: "2026-01-13", last_pred_date: "2026-01-16", base_date: null, train_start_date: null, train_end_date: null },
  ],
};
