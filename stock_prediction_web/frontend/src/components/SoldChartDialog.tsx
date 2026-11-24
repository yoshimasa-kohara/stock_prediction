// 保有銘柄の売却済みの1行の、購入日〜売却日のチャートのダイアログ。画面は移らない。
// 購入日に「買」、売却日に「売」の印(いつも出す)、売買の線(買値 → 売値の直線)と買値・売値の水平線(チェックで出し入れ)、持っていなかった期間(前後の余白と売却後)のグレーの背景を書き込む
import { useCallback, useMemo } from "react";

import type { PricesResponse, SoldRow } from "../api/types";
import { SOLD_CHART_HEIGHT_KEY } from "../lib/chart";
import { displayCode, formatPrice, formatSignedNumber, formatSignedPercent, signClass } from "../lib/format";
import { soldOverlay } from "../lib/soldChart";
import ChartPanel, { type ChartRange } from "./ChartPanel";
import Modal from "./Modal";
import type { ChartVisibility } from "./PriceChart";

/** 表示の切り替えの初期値: 売買の線だけ(トレンドラインは過去の期間では意味がずれるので OFF) */
const SOLD_VISIBLE: ChartVisibility = { ma: false, trend: false, bb: false, trade: true };

/** 凡例の後ろに出す注記 */
const SOLD_NOTE = "グレーの背景は持っていなかった期間";

interface Props {
  sold: SoldRow;
  onClose: () => void;
}

export default function SoldChartDialog({ sold, onClose }: Props) {
  // 期間のタブ: 保有期間(購入日〜売却日の前後に余白)と、購入後〜今日まで(購入日の前に余白。最新の日まで)
  const ranges = useMemo(() => {
    const held: ChartRange = { value: "held", label: "保有期間", query: { start: sold.buy_date, end: sold.sell_date } };
    const after: ChartRange = { value: "after", label: "購入後〜今日まで", query: { start: sold.buy_date } };
    return [held, after];
  }, [sold.buy_date, sold.sell_date]);
  const overlay = useCallback((data: PricesResponse) => soldOverlay(sold, data), [sold]);
  return (
    <Modal
      title={`${displayCode(sold.code)} ${sold.name}`}
      onClose={onClose}
      wide
      footer={
        <button type="button" className="btn sub" onClick={onClose}>
          閉じる
        </button>
      }
    >
      <div className="soldhead">
        <span>
          買値 {formatPrice(sold.buy_price)} → 売値 {formatPrice(sold.sell_price)}
        </span>
        <span>
          損益{" "}
          <span className={signClass(sold.pnl_rate)} data-testid="sold-chart-pnl">
            {formatSignedPercent(sold.pnl_rate)}({formatSignedNumber(sold.pnl_amount)}円)
          </span>
        </span>
        <span>保有した日数 {sold.held_days}日</span>
        <span>
          {sold.buy_date} 〜 {sold.sell_date}
        </span>
      </div>
      <ChartPanel
        path={`/api/stocks/${sold.code}/prices`}
        note={SOLD_NOTE}
        emptyMessage="この期間の株価はありません"
        heightKey={SOLD_CHART_HEIGHT_KEY}
        ranges={ranges}
        initialVisible={SOLD_VISIBLE}
        overlay={overlay}
      />
    </Modal>
  );
}
