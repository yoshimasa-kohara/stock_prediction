// 保有銘柄の売却済みのチャートに書き込むもの
import type { PricesResponse, SoldRow } from "../api/types";
import type { ChartOverlay } from "../components/PriceChart";
import { CHART_COLORS } from "./chart";
import type { HeldRange } from "./outsideShade";

/**
 * チャートに書き込むもの(買・売の印、買値・売値の線、持っていた期間)を、API の値から作る。
 * 持っていた期間は、購入日以降で最初の行から、売却日以前で最後の行まで。
 * 買値・売値は、その日の行の倍率(multiplier)を掛けて、チャートの株価(株式分割を調整した値)にそろえる。
 */
export function soldOverlay(sold: SoldRow, data: PricesResponse): ChartOverlay {
  const rows = data.rows;
  const first = rows.findIndex((r) => r.date >= sold.buy_date);
  let last = -1;
  rows.forEach((r, i) => {
    if (r.date <= sold.sell_date) last = i;
  });
  const firstRow = first >= 0 && first <= last ? rows[first] : undefined;
  const lastRow = firstRow ? rows[last] : undefined;
  const held: HeldRange = firstRow && lastRow ? { first: firstRow.date, last: lastRow.date } : "all";
  const markers: ChartOverlay["markers"] =
    firstRow && lastRow
      ? [
          { time: firstRow.date, position: "belowBar", shape: "arrowUp", color: CHART_COLORS.buy, text: "買" },
          { time: lastRow.date, position: "aboveBar", shape: "arrowDown", color: CHART_COLORS.sell, text: "売" },
        ]
      : [];
  const buy = sold.buy_price * (firstRow?.multiplier ?? 1);
  const sell = sold.sell_price * (lastRow?.multiplier ?? 1);
  // 売買の線の色: 線の両端と同じ値(倍率を掛けた買値・売値)で決める。売値 > 買値 は赤、売値 < 買値 は青、同じ値は灰色
  const color = sell > buy ? CHART_COLORS.up : sell < buy ? CHART_COLORS.down : CHART_COLORS.tradeFlat;
  // 購入日と売却日が同じ行(同じ日)のときは、線は2点にならないので引かない
  const tradeLine: ChartOverlay["tradeLine"] =
    firstRow && lastRow && firstRow.date < lastRow.date
      ? { from: { time: firstRow.date, value: buy }, to: { time: lastRow.date, value: sell }, color }
      : undefined;
  return {
    markers,
    tradeLine,
    priceLines: [
      { price: buy, color: CHART_COLORS.buy, title: "買値" },
      { price: sell, color: CHART_COLORS.sell, title: "売値" },
    ],
    held,
  };
}
