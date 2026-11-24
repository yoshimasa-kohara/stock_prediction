import type { PriceRow, PricesResponse, SoldRow } from "../api/types";
import { HOLDINGS, PRICES } from "../test/apiData";
import { CHART_COLORS } from "./chart";
import { soldOverlay } from "./soldChart";

const SOLD: SoldRow = HOLDINGS.sold[0]!;

function row(date: string, multiplier?: number | null): PriceRow {
  return { ...PRICES.rows[0]!, date, multiplier };
}

function prices(rows: PriceRow[]): PricesResponse {
  return { ...PRICES, rows, trendlines: [] };
}

describe("売買の線(soldOverlay の tradeLine)", () => {
  const rows = prices([row("2026-04-14", 0.5), row("2026-06-12", 1)]);

  it("売値 > 買値は赤、売値 < 買値は青、同じ値は灰色。分割の倍率を掛けた2点を結ぶ", () => {
    const up = soldOverlay({ ...SOLD, buy_price: 1520, sell_price: 1688 }, rows).tradeLine!;
    expect(up.color).toBe(CHART_COLORS.up);
    expect([up.from, up.to]).toEqual([
      { time: "2026-04-14", value: 760 },
      { time: "2026-06-12", value: 1688 },
    ]);
    expect(soldOverlay({ ...SOLD, buy_price: 4000, sell_price: 1520 }, rows).tradeLine!.color).toBe(CHART_COLORS.down);
    // 調整後の値(買値 3200 × 0.5 = 1600、売値 1600 × 1)が同じなら灰色
    expect(soldOverlay({ ...SOLD, buy_price: 3200, sell_price: 1600 }, rows).tradeLine!.color).toBe(CHART_COLORS.tradeFlat);
  });

  it("持っている間に分割があり、調整しない値では売値 < 買値でも、調整した値で売値 > 買値なら赤", () => {
    // 買値 3000 → 1500(× 0.5)、売値 1688 → 1688。調整しない値では 1688 < 3000
    const o = soldOverlay({ ...SOLD, buy_price: 3000, sell_price: 1688 }, rows);
    expect(o.tradeLine!.color).toBe(CHART_COLORS.up);
    expect(o.priceLines.map((l) => l.price)).toEqual([1500, 1688]);
  });

  it("売却日の行の倍率が 1 以外なら、売値と線の終点に掛かる。倍率が null なら 1 として扱う", () => {
    const split = prices([row("2026-04-14", 1), row("2026-06-12", 0.5)]);
    const o = soldOverlay({ ...SOLD, buy_price: 1000, sell_price: 1688 }, split);
    expect(o.tradeLine!.to.value).toBe(844);
    expect(o.priceLines[1]!.price).toBe(844);
    expect(o.tradeLine!.color).toBe(CHART_COLORS.down);
    const none = soldOverlay({ ...SOLD, buy_price: 1000, sell_price: 1688 }, prices([row("2026-04-14", null), row("2026-06-12", null)]));
    expect(none.tradeLine!.to.value).toBe(1688);
  });

  it("購入日と売却日が同じ行、持っていた期間の株価がないときは、線を引かない", () => {
    expect(soldOverlay(SOLD, prices([row("2026-04-14")])).tradeLine).toBeUndefined();
    expect(soldOverlay({ ...SOLD, sell_date: "2026-04-14" }, rows).tradeLine).toBeUndefined();
    expect(soldOverlay(SOLD, prices([row("2026-04-10")])).tradeLine).toBeUndefined();
  });
});

describe("売却済みのチャートに書き込むもの(soldOverlay)", () => {
  it("購入日・売却日が株価のない日なら、その後・前の株価の日に印を付ける。倍率がなければ買値・売値のまま", () => {
    // 購入日 2026-04-14、売却日 2026-06-12
    const o = soldOverlay(SOLD, prices([row("2026-04-10"), row("2026-04-15"), row("2026-06-11"), row("2026-06-15")]));
    expect(o.held).toEqual({ first: "2026-04-15", last: "2026-06-11" });
    expect(o.markers.map((m) => [m.time, m.text])).toEqual([
      ["2026-04-15", "買"],
      ["2026-06-11", "売"],
    ]);
    expect(o.priceLines.map((l) => [l.title, l.price])).toEqual([
      ["買値", 1520],
      ["売値", 1688],
    ]);
  });

  it("持っていた期間の株価がなければ、全てを塗り、印は付けない", () => {
    const o = soldOverlay(SOLD, prices([row("2026-04-10", 0.5), row("2026-06-15", 1)]));
    expect(o.held).toBe("all");
    expect(o.markers).toEqual([]);
    expect(o.priceLines.map((l) => l.price)).toEqual([1520, 1688]);
  });
});
