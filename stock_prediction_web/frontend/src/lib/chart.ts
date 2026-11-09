// 銘柄詳細の株価チャートの色と名前
import type { TrendLine } from "../api/types";

/** 色(画面見本と同じ) */
export const CHART_COLORS = {
  up: "#c00000",
  down: "#0055cc",
  ma5: "#e69500",
  ma25: "#2a9d4b",
  ma75: "#8a3ffc",
  resist: "#c00000",
  support: "#0055cc",
  /** 抜けた日より後のトレンドライン(薄い色) */
  resistFaded: "rgba(192, 0, 0, 0.35)",
  supportFaded: "rgba(0, 85, 204, 0.35)",
  bb2: "#d1477a",
  bb1: "#2bb3c0",
  bbMid: "#888888",
  volume: "#9aa5b8",
} as const;

/** トレンドラインの名前(例: 上値抵抗線(接触4回)) */
export function trendLabel(line: TrendLine): string {
  return `${line.kind === "RESIST" ? "上値抵抗線" : "下値支持線"}(接触${line.touches}回)`;
}

/** 線の名前を、線の左端から右にずらす最低の位置(チャートの行の数に対する割合)。左端で文字が切れないため */
export const TREND_LABEL_MIN_RATE = 0.1;

/** チャートに付ける文字・印(lightweight-charts の SeriesMarker の形) */
export type TrendMarker = {
  time: string;
  shape: "circle" | "arrowUp" | "arrowDown";
  color: string;
  text: string;
  /** 印の大きさ(0 なら文字だけ) */
  size?: number;
} & (
  | { /** 線の値(price)の上・下に出す */ position: "atPriceTop" | "atPriceBottom"; price: number }
  | { /** その日の線の値の上・下に出す */ position: "aboveBar" | "belowBar" }
);

/** トレンドラインの1本分の線(抜けた日の前と後で分ける) */
export interface TrendSegment {
  /** 抜けた日より後の線(薄い色・細い線で描く) */
  faded: boolean;
  points: { time: string; value: number }[];
  markers: TrendMarker[];
}

/**
 * トレンドラインを、チャートの行(dates)ごとの点にする。
 * - 線の値は start_value + slope × (行の数)。0円より下は描かない(API が0円になる日で止めている)
 * - 線の名前(例: 下値支持線(接触2回))は、線の左端(線を結んだ古いほうの谷・山の近く)に出す。
 *   上値抵抗線は線の上、下値支持線は線の下。チャートの左端で切れないように、少なくとも TREND_LABEL_MIN_RATE だけ右に出す
 * - 抜けた日には、印と文字(下値支持線は「下抜け」、上値抵抗線は「上抜け」)を出し、その日より後は薄い線にする
 * - 抜けた日が表示する期間より前なら、線の全てを薄い線にする(印は出さない)
 * 線の両端の日がチャートの行にない場合は、空の配列を返す。
 */
export function trendSegments(line: TrendLine, dates: string[]): TrendSegment[] {
  const start = dates.indexOf(line.start_date);
  const end = dates.indexOf(line.end_date);
  if (start < 0 || end <= start) return [];
  const resist = line.kind === "RESIST";
  const color = resist ? CHART_COLORS.resist : CHART_COLORS.support;
  const valueAt = (i: number) => Math.max(0, line.start_value + line.slope * (i - start));
  const point = (i: number) => ({ time: dates[i]!, value: valueAt(i) });
  const range = (from: number, to: number) => Array.from({ length: to - from + 1 }, (_, k) => point(from + k));

  // 抜けた日の位置(表示する期間より前なら start より前とみなす)
  let brk: number | null = null;
  if (line.break_date !== null) {
    const found = dates.indexOf(line.break_date);
    brk = found >= 0 ? found : line.break_date < line.start_date ? -1 : null;
  }
  const segments: TrendSegment[] = [];
  if (brk === null || brk > start) {
    const solidEnd = brk === null ? end : Math.min(brk, end);
    segments.push({ faded: false, points: range(start, solidEnd), markers: [] });
  }
  if (brk !== null && brk < end) {
    segments.push({ faded: true, points: range(Math.max(brk, start), end), markers: [] });
  }
  if (segments.length === 0) return [];

  // 線の名前(左端の近く)
  const labelAt = Math.min(end, Math.max(start, Math.ceil(dates.length * TREND_LABEL_MIN_RATE)));
  const label: TrendMarker = {
    time: dates[labelAt]!,
    position: resist ? "atPriceTop" : "atPriceBottom",
    shape: "circle",
    size: 0,
    color,
    text: trendLabel(line),
    price: valueAt(labelAt),
  };
  const labelSegment = segments.find((s) => s.points.some((p) => p.time === label.time)) ?? segments[0]!;
  labelSegment.markers.push(label);
  // 抜けた日の印と文字(濃い線の右端。抜けた日が表示する期間の最初の日なら、薄い線の左端)
  if (brk !== null && brk >= start && brk <= end) {
    segments[0]!.markers.push({
      time: dates[brk]!,
      position: resist ? "aboveBar" : "belowBar",
      shape: resist ? "arrowUp" : "arrowDown",
      color,
      text: resist ? "上抜け" : "下抜け",
    });
  }
  // 1本の線の印は、日付の古い順に並べる(lightweight-charts の決まり)
  segments.forEach((s) => s.markers.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0)));
  return segments;
}


/** チャートの高さの選択肢(PC の画面だけ。スマホは styles.css の 360px のまま) */
export const CHART_HEIGHTS = [
  { value: "S", label: "小", px: 440 },
  { value: "M", label: "中", px: 480 },
  { value: "L", label: "大", px: 640 },
] as const;

/** チャートの高さの選択肢の値 */
export type ChartHeight = (typeof CHART_HEIGHTS)[number]["value"];

/** 選んだ高さをブラウザーに残すときの名前(銘柄詳細のチャート) */
export const CHART_HEIGHT_KEY = "stockChartHeight";

/** 選んだ高さをブラウザーに残すときの名前(トップ・買い候補の「相場」の日経平均のチャート。銘柄詳細とは別) */
export const INDEX_CHART_HEIGHT_KEY = "indexChartHeight";

/** 下の段(出来高)の高さ(px)。チャートの高さを変えても、この高さのまま */
export const VOLUME_PANE_HEIGHT = 90;

/** ブラウザーに残した高さを読む。なければ(読めなければ)小 */
export function loadChartHeight(key: string = CHART_HEIGHT_KEY): ChartHeight {
  try {
    const v = window.localStorage.getItem(key);
    const found = CHART_HEIGHTS.find((h) => h.value === v);
    return found ? found.value : "S";
  } catch {
    return "S";
  }
}

/** 選んだ高さをブラウザーに残す(残せなくても画面は動く) */
export function saveChartHeight(value: ChartHeight, key: string = CHART_HEIGHT_KEY): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // ブラウザーの設定で残せないときは、何もしない
  }
}

/** 高さの値(px) */
export function chartHeightPx(value: ChartHeight): number {
  return CHART_HEIGHTS.find((h) => h.value === value)?.px ?? CHART_HEIGHTS[0].px;
}
