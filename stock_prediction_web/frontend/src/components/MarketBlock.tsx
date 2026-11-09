// 「日経平均」のブロック。トップ画面と買い候補画面で同じものを出す。
// 上から、見出し「日経平均(予測日)」、相場の状態の行、日経平均の向きの行、※の注意書き、日経平均のチャート。
// 相場の状態(全銘柄の集計)と日経平均の向きは別のもの。日経平均がない日も、相場の状態は出す
import { Suspense, lazy } from "react";

import type { MarketState } from "../api/types";
import { INDEX_CHART_HEIGHT_KEY } from "../lib/chart";
import MarketStateLine from "./MarketStateLine";

// チャートの部品(lightweight-charts)は、使うときに読み込む(トップの画面の最初の読み込みを軽くするため。銘柄詳細と同じ)
const ChartPanel = lazy(() => import("./ChartPanel"));

/** 日経平均のチャートの注記(銘柄詳細の「株価は株式分割を調整した値」は出さない) */
export const INDEX_CHART_NOTE = "日経平均(Yahoo Finance)。マウス(スマホは指で押した所)の日の値を表示";

/** 日経平均のデータが1行もないときの文言 */
export const INDEX_CHART_EMPTY = "日経平均のデータがありません";

interface Props {
  /** 予測日(YYYY-MM-DD)。チャートは、この日以前で日経平均がある最後の日までを出す */
  predDate: string;
  state: MarketState | null;
}

export default function MarketBlock({ predDate, state }: Props) {
  return (
    <div className="panel market" data-testid="market-block">
      <h2>日経平均({predDate})</h2>
      <MarketStateLine state={state} />
      <Suspense fallback={<p className="loading">読み込み中…</p>}>
        <ChartPanel
          path="/api/index/prices"
          query={{ date: predDate }}
          note={INDEX_CHART_NOTE}
          emptyMessage={INDEX_CHART_EMPTY}
          heightKey={INDEX_CHART_HEIGHT_KEY}
          showVolume={false}
          emptyOnly
        />
      </Suspense>
    </div>
  );
}
