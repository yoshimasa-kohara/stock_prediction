// トレンドラインの描き方(lib/chart.ts の trendSegments)のテスト
import { describe, expect, it } from "vitest";

import type { TrendLine } from "../api/types";
import { CHART_COLORS, trendSegments } from "./chart";

/** 2026-01-01 から n 日分の日付(チャートの行) */
const days = (n: number) =>
  Array.from({ length: n }, (_, i) => new Date(Date.UTC(2026, 0, 1 + i)).toISOString().slice(0, 10));

const DATES = days(40);

const base: TrendLine = {
  kind: "SUPPORT",
  touches: 2,
  start_date: DATES[10]!,
  start_value: 1000,
  end_date: DATES[39]!,
  end_value: 1290,
  slope: 10,
  break_date: null,
  break_value: null,
};

describe("trendSegments", () => {
  it("抜けていない線は1本。名前は線の左端(古いほうの谷)に、下値支持線は線の下に出す", () => {
    const segs = trendSegments(base, DATES);
    expect(segs).toHaveLength(1);
    expect(segs[0]!.faded).toBe(false);
    expect(segs[0]!.points).toHaveLength(30);
    expect(segs[0]!.points[0]).toEqual({ time: DATES[10], value: 1000 });
    expect(segs[0]!.points.at(-1)).toEqual({ time: DATES[39], value: 1290 });
    expect(segs[0]!.markers).toEqual([
      {
        time: DATES[10],
        position: "atPriceBottom",
        shape: "circle",
        size: 0,
        color: CHART_COLORS.support,
        text: "下値支持線(接触2回)",
        price: 1000,
      },
    ]);
  });

  it("上値抵抗線の名前は線の上。線がチャートの左端から始まるときは、少し右に出す(左端で切れないため)", () => {
    const line: TrendLine = { ...base, kind: "RESIST", touches: 3, start_date: DATES[0]!, slope: -1, end_value: 961 };
    const [seg] = trendSegments(line, DATES);
    // 40行の 10% = 4行目
    expect(seg!.markers[0]).toMatchObject({ time: DATES[4], position: "atPriceTop", text: "上値抵抗線(接触3回)", price: 996 });
  });

  it("抜けた日に印と文字を出し、抜けた日より後は薄い線にする", () => {
    const line: TrendLine = { ...base, break_date: DATES[30]!, break_value: 1200 };
    const segs = trendSegments(line, DATES);
    expect(segs.map((s) => [s.faded, s.points[0]!.time, s.points.at(-1)!.time])).toEqual([
      [false, DATES[10], DATES[30]],
      [true, DATES[30], DATES[39]],
    ]);
    expect(segs[0]!.markers.map((m) => [m.time, m.text, m.shape, m.position])).toEqual([
      [DATES[10], "下値支持線(接触2回)", "circle", "atPriceBottom"],
      [DATES[30], "下抜け", "arrowDown", "belowBar"],
    ]);
    expect(segs[1]!.markers).toEqual([]);
    // 上値抵抗線は「上抜け」(赤)
    const resist = trendSegments({ ...line, kind: "RESIST" }, DATES);
    expect(resist[0]!.markers[1]).toMatchObject({ text: "上抜け", shape: "arrowUp", position: "aboveBar", color: CHART_COLORS.resist });
  });

  it("今日に抜けた線は、薄い線を描かない", () => {
    const segs = trendSegments({ ...base, break_date: DATES[39]!, break_value: 1290 }, DATES);
    expect(segs).toHaveLength(1);
    expect(segs[0]!.markers.map((m) => m.text)).toEqual(["下値支持線(接触2回)", "下抜け"]);
  });

  it("抜けた日が表示する期間より前なら、線の全てを薄い線にする(印は出さない)", () => {
    const line: TrendLine = { ...base, start_date: DATES[0]!, break_date: "2025-12-01", break_value: 900 };
    const segs = trendSegments(line, DATES);
    expect(segs).toHaveLength(1);
    expect(segs[0]!.faded).toBe(true);
    expect(segs[0]!.markers.map((m) => m.text)).toEqual(["下値支持線(接触2回)"]);
  });

  it("抜けた日が表示する期間の最初の日なら、薄い線の左端に印を出す", () => {
    const segs = trendSegments({ ...base, break_date: DATES[10]!, break_value: 1000 }, DATES);
    expect(segs).toHaveLength(1);
    expect(segs[0]!.faded).toBe(true);
    expect(segs[0]!.markers.map((m) => m.text)).toEqual(["下値支持線(接触2回)", "下抜け"]);
  });

  it("線の値は0円より下に描かない", () => {
    // 1000円から1日100円下がる線(API は0円になる日 = 20行目で止める)
    const line: TrendLine = { ...base, slope: -100, end_date: DATES[20]!, end_value: 0 };
    const [seg] = trendSegments(line, DATES);
    expect(seg!.points.at(-1)).toEqual({ time: DATES[20], value: 0 });
    expect(seg!.points.every((p) => p.value >= 0)).toBe(true);
    // 小数の位置で0円になる線も、右端は0円
    const [frac] = trendSegments({ ...line, slope: -95, end_date: DATES[21]! }, DATES);
    expect(frac!.points.at(-1)!.value).toBe(0);
  });

  it("線の両端の日がチャートの行にないときは描かない", () => {
    expect(trendSegments({ ...base, start_date: "2025-01-01" }, DATES)).toEqual([]);
    expect(trendSegments({ ...base, end_date: DATES[10]! }, DATES)).toEqual([]);
  });
});
