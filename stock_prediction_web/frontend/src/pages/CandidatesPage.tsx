// 買い候補。日付を指定して、その日の買い候補をメールと同じ形で見る。
// 買い候補と買い候補(業種分散)をタブで切り替える
import { useState } from "react";
import { useSearchParams } from "react-router";

import type { CandidatesResponse, MarketState, PredictionDates } from "../api/types";
import { useApi } from "../api/useApi";
import ErrorMessage from "../components/ErrorMessage";
import MarketBlock from "../components/MarketBlock";
import PredDatePicker from "../components/PredDatePicker";
import PredictionTable from "../components/PredictionTable";
import Tabs from "../components/Tabs";
import { useSettings } from "../context/contexts";
import { KIND_TABS, kindDescription, kindLabel, kindParam, parseKind } from "../lib/candidateKind";
import { formatDateWithWeekday, formatInteger } from "../lib/format";

export default function CandidatesPage() {
  const settings = useSettings();
  const [params, setParams] = useSearchParams();
  const date = params.get("date") ?? undefined;
  const kind = parseKind(params.get("kind"));
  const dates = useApi<PredictionDates>("/api/prediction-dates");
  const res = useApi<CandidatesResponse>("/api/candidates", { date, ...kindParam(kind) });
  const data = res.data;
  const shown = data?.pred_date ?? date ?? "";
  const { main_horizon: main, predict_horizons: horizons } = settings.batch;
  const label = kindLabel(kind);
  // 相場のブロックの値(予測日ごと。買い候補と業種分散で同じ)。タブを切り替えて読み直す間も、ブロックを出したままにする
  const [market, setMarket] = useState<{ date: string; state: MarketState | null } | null>(null);
  if (data && (market?.date !== data.pred_date || market.state !== data.market_state)) {
    setMarket({ date: data.pred_date, state: data.market_state });
  }
  const showMarket = market !== null && market.date === shown && !res.error;

  return (
    <>
      <h1>買い候補</h1>
      <div className="cond">
        <PredDatePicker
          value={shown}
          dates={dates.data?.dates ?? []}
          onChange={(d) => setParams({ date: d, ...kindParam(kind) })}
        />
        {data && (
          <span className="condinfo">
            モデル {data.model_version} ／ 予測した銘柄 {formatInteger(data.code_count)} 件
          </span>
        )}
      </div>
      {/* 相場(選んでいる予測日の相場の状態・日経平均の向き・日経平均のチャート) */}
      {showMarket && <MarketBlock predDate={market.date} state={market.state} />}
      <div className="cond">
        <Tabs
          label="買い候補の種類"
          items={KIND_TABS}
          value={kind}
          onChange={(k) => setParams({ ...(shown ? { date: shown } : {}), ...kindParam(k) })}
        />
      </div>
      {dates.error && <ErrorMessage message={dates.error} />}
      {res.error && <ErrorMessage message={res.error} />}
      {res.loading && <p className="loading">読み込み中…</p>}
      {data && (
        <>
          {data.fallback_note && <div className="warn">{data.fallback_note}</div>}
          <p className="info">
            {formatDateWithWeekday(data.pred_date)} の{label}({kindDescription(kind, settings.batch)}
            。メールと同じ内容)。並べ方: 上昇確率が{Math.round(settings.batch.up_prob_threshold * 100)}
            %以上で見込みがプラスの銘柄の中で、主となる日数({main}日後、緑の見出し)の市場比の確率が高い順。
            {kind === "sector" &&
              `同じ33業種の銘柄は上から${settings.batch.sector_cap_per_sector}銘柄までにする(業種は予測した時点の33業種)。`}
            銘柄名を選ぶと銘柄詳細へ移る。
          </p>
          <PredictionTable
            rows={data.candidates}
            horizons={horizons}
            mainHorizon={main}
            emptyMessage="買い候補はありません"
            showSector
          />
        </>
      )}
    </>
  );
}
