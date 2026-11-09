// トップ。最新の状態を1画面で見る。毎晩のメールの内容を短くまとめたもの
import { Link } from "react-router";

import type { AccuracyResponse, CandidatesResponse, HoldingsResponse, LatestRun } from "../api/types";
import { useApi } from "../api/useApi";
import AccuracyTables, { RankAccuracyPanel } from "../components/AccuracyTables";
import ErrorMessage from "../components/ErrorMessage";
import MarketBlock from "../components/MarketBlock";
import PredictionTable from "../components/PredictionTable";
import StockName from "../components/StockName";
import { useSettings, useStatus } from "../context/contexts";
import { calibrationLine, gapPoints } from "../lib/calibration";
import {
  displayCode,
  formatDateWithWeekday,
  formatPercent,
  formatPrice,
  formatSignedPercent,
  formatTime,
  signClass,
} from "../lib/format";
import { judgmentClass, statusTextClass } from "../lib/labels";

function BatchCard({ title, run }: { title: string; run: LatestRun | null }) {
  const settings = useSettings();
  return (
    <div className="card">
      <div className="lbl">
        {title}
        {run ? `(${run.target_date})` : ""}
      </div>
      <div className={`val ${run ? statusTextClass(run.status) : ""}`}>
        {run ? (settings.web.run_status_labels[run.status] ?? run.status) : "まだ動いていません"}
      </div>
      {run && (
        <div className="sub">
          {formatTime(run.started_at)} 開始{run.finished_at ? ` / ${formatTime(run.finished_at)} 終了` : ""}
        </div>
      )}
    </div>
  );
}

export default function TopPage() {
  const settings = useSettings();
  const { status } = useStatus();
  const main = settings.batch.main_horizon;
  const candidates = useApi<CandidatesResponse>("/api/candidates");
  const holdings = useApi<HoldingsResponse>("/api/holdings", { include_sold: false });
  const accuracy = useApi<AccuracyResponse>("/api/accuracy", { horizon: main });
  const groups = holdings.data?.groups ?? [];
  const sellCount = groups.filter((g) => g.judgment.startsWith("売却")).length;
  const switchCount = groups.filter((g) => g.judgment.startsWith("乗り換え")).length;
  const top = (candidates.data?.candidates ?? []).slice(0, settings.web.top_candidate_count);
  const warnings = accuracy.data?.warnings ?? [];

  return (
    <>
      <h1>トップ</h1>
      <div className="cards">
        <div className="card">
          <div className="lbl">最新の予測日</div>
          <div className="val">{status?.latest_pred_date ? formatDateWithWeekday(status.latest_pred_date) : "予測はまだありません"}</div>
          {status?.model_version && (
            <div className="sub">
              モデル {status.model_version}
              {status.model_base_date ? `(学習の基準日 ${status.model_base_date})` : ""}
            </div>
          )}
        </div>
        <BatchCard title="日次バッチ" run={status?.latest_daily ?? null} />
        <BatchCard title="週次バッチ" run={status?.latest_weekly ?? null} />
        <div className="card">
          <div className="lbl">買い候補 / 保有銘柄</div>
          <div className="val">
            {candidates.data?.candidates.length ?? "-"} 件 / {holdings.data ? groups.length : "-"} 件
          </div>
          <div className="sub">
            売却の判定 {sellCount}件、乗り換えを検討 {switchCount}件
          </div>
        </div>
      </div>
      {warnings.length > 0 && (
        <div className="warn" style={{ marginTop: 14 }}>
          ⚠ 上昇確率のずれ(直近3か月):{" "}
          {warnings.map((w) => (
            <span key={`${w.horizon}-${w.prob_band}`} className="warnline">
              {calibrationLine(w)}(ずれ {gapPoints(w.gap)} ポイント)
            </span>
          ))}{" "}
          <Link to="/accuracy">的中率の画面で見る</Link>
        </div>
      )}
      {/* 相場(最新の予測日の相場の状態・日経平均の向き・日経平均のチャート) */}
      {candidates.data && <MarketBlock predDate={candidates.data.pred_date} state={candidates.data.market_state} />}
      <div className="two">
        <div className="panel">
          <h2>買い候補(上位{settings.web.top_candidate_count}銘柄)</h2>
          {candidates.error && <ErrorMessage message={candidates.error} />}
          {candidates.data && (
            <PredictionTable rows={top} horizons={[main]} mainHorizon={main} emptyMessage="買い候補はありません" />
          )}
          <p className="more">
            <Link to="/candidates">{settings.batch.buy_candidate_count}銘柄すべてを見る ›</Link>
          </p>
        </div>
        <div className="panel">
          <h2>保有銘柄</h2>
          {holdings.error && <ErrorMessage message={holdings.error} />}
          {holdings.data && (
            <div className="wrap">
              <table>
                <thead>
                  <tr>
                    <th>コード</th>
                    <th className="fix">銘柄名</th>
                    <th>市場</th>
                    <th>現在値</th>
                    <th>損益(%)</th>
                    <th className="main">{main}日後 市場比</th>
                    <th className="main">{main}日後 上昇確率</th>
                    <th>判定</th>
                  </tr>
                </thead>
                <tbody>
                  {groups.length === 0 && (
                    <tr>
                      <td colSpan={8} className="c">
                        保有銘柄はありません
                      </td>
                    </tr>
                  )}
                  {groups.map((g) => {
                    const v = g.horizons.find((x) => x.horizon === main);
                    return (
                      <tr key={g.code}>
                        <td>{displayCode(g.code)}</td>
                        <td className="fix">
                          <StockName code={g.code} name={g.name} />
                        </td>
                        <td>{g.market}</td>
                        <td className="n">{formatPrice(g.current)}</td>
                        <td className={`n ${signClass(g.pnl_rate)}`}>{formatSignedPercent(g.pnl_rate)}</td>
                        <td className="n b">{formatPercent(v?.rel_prob)}</td>
                        <td className="n b">{formatPercent(v?.up_prob)}</td>
                        <td className={judgmentClass(g.judgment)}>{g.judgment}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <p className="more">
            <Link to="/holdings">保有銘柄の画面へ ›</Link>
          </p>
        </div>
      </div>
      <div className="panel">
        <h2>的中率(主となる日数: {main}営業日後)</h2>
        {accuracy.error && <ErrorMessage message={accuracy.error} />}
        {accuracy.data && accuracy.data.summary_date === null && <p className="info">検証結果はまだありません</p>}
        {accuracy.data && accuracy.data.summary_date !== null && (
          <AccuracyTables periods={accuracy.data.periods} horizon={main} />
        )}
        <p className="more">
          <Link to="/accuracy">的中率の画面で見る ›</Link>
        </p>
      </div>
      {/* 買い候補 順位別 的中率(直近1か月だけ。基準日は最新) */}
      <RankAccuracyPanel fixedPeriod="1M">
        <p className="more">
          <Link to="/accuracy">的中率の画面で見る ›</Link>
        </p>
      </RankAccuracyPanel>
    </>
  );
}
