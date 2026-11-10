// テストで使う値と、fetch の差し替え
import { vi } from "vitest";

import type { Settings, Status } from "../api/types";

/** 設定値の例(api/web_const.py と const.py の今の値) */
export const SETTINGS: Settings = {
  web: {
    status_refresh_sec: 60,
    stock_search_limit: 10,
    top_candidate_count: 5,
    results_default_end_offset_days: 10,
    results_default_span_days: 10,
    results_max_months: 3,
    candidates_default_days: 5,
    candidates_range_max_months: 1,
    chart_default_period: "6M",
    accuracy_daily_default_months: 3,
    compare_sector_top_n: 5,
    runs_default_days: 7,
    runs_page_size: 100,
    screen_page_size: 50,
    run_status_labels: { SUCCESS: "成功", EMPTY: "空振り", FAILED: "失敗", RUNNING: "実行中" },
    batch_type_labels: { DAILY: "日次バッチ", WEEKLY: "週次バッチ", SINGLE: "単独で起動" },
  },
  batch: {
    predict_horizons: [5, 10, 20, 60],
    main_horizon: 20,
    buy_candidate_count: 20,
    buy_candidate_sector_count: 30,
    sector_cap_per_sector: 3,
    bb_days: 20,
    up_prob_threshold: 0.5,
    rel_prob_threshold: 0.5,
    high_prob_threshold: 0.7,
    calibration_min_count: 500,
    calibration_warn_gap: 0.05,
    process_labels: {
      COLLECT: "収集",
      TRAIN: "学習",
      PREDICT: "予測",
      VERIFY: "検証",
      NOTIFY: "通知",
      BACKUP: "バックアップ",
      CLEANUP: "クリーニング",
      DAILY_BATCH: "日次バッチ",
      WEEKLY_BATCH: "週次バッチ",
    },
  },
};

/** バッチの状態の例(実行中の処理なし) */
export const STATUS: Status = {
  latest_pred_date: "2026-07-22",
  model_version: "20260718",
  model_base_date: "2026-07-18",
  running: [],
  latest_daily: null,
  latest_weekly: null,
};

/** fetch の返り値(状態コードと本文) */
export interface FakeResponse {
  status?: number;
  body: unknown;
}

/** JSON を返す Response を作る */
export function jsonResponse({ status = 200, body }: FakeResponse): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/**
 * fetch を差し替える。パス(クエリを除く)ごとに返り値を決める。関数を渡すと、呼ばれるたびに計算する。
 * 決めていないパスは 404。呼ばれた URL の一覧を返す
 */
export function mockFetch(routes: Record<string, FakeResponse | (() => FakeResponse)>): string[] {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      const route = routes[url.split("?")[0] ?? url];
      if (!route) return jsonResponse({ status: 404, body: { detail: "指定された API はありません" } });
      return jsonResponse(typeof route === "function" ? route() : route);
    }),
  );
  return calls;
}

/** fetch の呼び出し(URL、メソッド、本文) */
export interface FetchCall {
  url: string;
  method: string;
  body: unknown;
}

/**
 * fetch を差し替える。handler に URL(パスとクエリ)とメソッドを渡し、返り値を決める。
 * handler が undefined を返したら 404。呼ばれた内容の一覧を返す
 */
export function mockApi(
  handler: (path: string, query: URLSearchParams, method: string, body: unknown) => FakeResponse | undefined,
): FetchCall[] {
  const calls: FetchCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const method = init?.method ?? "GET";
      const body: unknown = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ url: `${url.pathname}${url.search}`, method, body });
      const res = handler(url.pathname, url.searchParams, method, body);
      return jsonResponse(res ?? { status: 404, body: { detail: "指定された API はありません" } });
    }),
  );
  return calls;
}
