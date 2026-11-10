// 最新の日の「日経平均」のブロック。
// 見出しの日付・相場の状態・日経平均の向き・注意書き・チャートのすべてを、最新の予測日のものにする
// (トップ画面と同じデータ: GET /api/candidates の省略時)。表示する期間や種類を変えても、読み直さない。
// 予測がない(404)ときや読めないときは何も出さない(表の所のエラーは、各画面が出す)
import type { CandidatesResponse } from "../api/types";
import { useApi } from "../api/useApi";
import MarketBlock from "./MarketBlock";

export default function LatestMarketBlock() {
  const latest = useApi<CandidatesResponse>("/api/candidates");
  if (!latest.data) return null;
  return <MarketBlock predDate={latest.data.pred_date} state={latest.data.market_state} />;
}
