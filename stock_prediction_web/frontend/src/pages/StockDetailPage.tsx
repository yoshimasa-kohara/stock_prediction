// 銘柄詳細。株価の動き、会社の数字、予測と答え合わせの結果をまとめて見る
import { useParams } from "react-router";

import type { HistoryRow, PredictionsResponse, StockInfo } from "../api/types";
import { useApi } from "../api/useApi";
import ErrorMessage from "../components/ErrorMessage";
import NotesText from "../components/NotesText";
import ChartPanel from "../components/ChartPanel";
import { useSettings } from "../context/contexts";
import {
  displayCode,
  formatDateWithWeekday,
  formatMonthDay,
  formatOku,
  formatPercent,
  formatPrice,
  formatSignedNumber,
  formatSignedPercent,
  formatTimes,
  signClass,
} from "../lib/format";
import { verifyLabel } from "../lib/labels";

function InfoCards({ info }: { info: StockInfo }) {
  return (
    <div className="cards">
      <div className="card">
        <div className="lbl">終値({info.price_date ?? "-"})</div>
        <div className="val">{formatPrice(info.close)}</div>
        <div className={`sub ${signClass(info.prev_change)}`}>
          前日比 {formatSignedNumber(info.prev_change, 1)}({formatSignedPercent(info.prev_change_rate)})
        </div>
      </div>
      <div className="card">
        <div className="lbl">PER(予想) ／ PBR</div>
        <div className="val">
          {formatTimes(info.fwd_per)} ／ {info.pbr === null ? "-" : `${info.pbr.toFixed(2)}倍`}
        </div>
        <div className="sub">同じ業種の予想PERの中央値 {formatTimes(info.sector_median_per)}</div>
      </div>
      <div className="card">
        <div className="lbl">ROE(実績) ／ 予想EPS</div>
        <div className="val">
          {formatPercent(info.roe)} ／ {formatPrice(info.fwd_eps)}
        </div>
        <div className="sub">時価総額 {formatOku(info.market_cap)}</div>
      </div>
      <div className="card">
        <div className="lbl">次の決算発表予定日</div>
        <div className="val">{info.earnings_date ?? "-"}</div>
        <div className="sub">
          {info.earnings_date
            ? `${info.earnings_name ?? ""}(あと${info.earnings_days ?? "-"}営業日)`
            : "予定日はまだ公表されていません"}
        </div>
      </div>
    </div>
  );
}

/** 株価のチャート(操作・凡例は ChartPanel) */
function StockChart({ code }: { code: string }) {
  return (
    <div className="panel">
      <ChartPanel
        path={`/api/stocks/${encodeURIComponent(code)}/prices`}
        note="株価は株式分割を調整した値。マウス(スマホは指で押した所)の日の値を表示"
        emptyMessage="株価がありません"
      />
    </div>
  );
}

/** 過去の予測の表の1行(予測日ごとに、予測日数ごとの値をまとめたもの) */
interface HistoryDay {
  predDate: string;
  basePrice: number;
  buyRank: number | null;
  byHorizon: Map<number, HistoryRow>;
}

/** 過去の予測(予測日 × 予測日数の行)を、予測日ごとの1行にまとめる(新しい順のまま) */
function groupHistory(rows: HistoryRow[]): HistoryDay[] {
  const days = new Map<string, HistoryDay>();
  for (const r of rows) {
    let day = days.get(r.pred_date);
    if (!day) {
      day = { predDate: r.pred_date, basePrice: r.base_price, buyRank: null, byHorizon: new Map() };
      days.set(r.pred_date, day);
    }
    day.byHorizon.set(r.horizon, r);
    if (day.buyRank === null && r.buy_rank !== null) day.buyRank = r.buy_rank;
  }
  return [...days.values()];
}

/** 答え合わせの文字の色のクラス */
function verifyClass(label: string): string {
  if (label === "的中") return "ok";
  if (label === "外れ") return "ng";
  return "eq";
}

/** 最新の予測(見出しの行とデータの1行の表)。市場比・予測株価・対象日は、マウスを置くと出す */
function LatestTable({ data, horizons, main }: { data: PredictionsResponse; horizons: number[]; main: number }) {
  return (
    <div className="panel latest">
      <h2>最新の予測</h2>
      {data.latest.length === 0 || data.latest_date === null ? (
        <span className="info">予測はありません</span>
      ) : (
        <>
          <div className="wrap">
            <table>
              <thead>
                <tr>
                  <th className="fix">予測日</th>
                  <th>予測日の終値</th>
                  <th>買い候補の順位</th>
                  {horizons.flatMap((h) =>
                    ["上昇確率", "見込み"].map((n) => (
                      <th key={`${h}-${n}`} className={h === main ? "main" : undefined}>
                        {h}日後 {n}
                      </th>
                    )),
                  )}
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className="fix">{formatDateWithWeekday(data.latest_date)}</td>
                  <td className="n">{formatPrice(data.latest_base_price)}</td>
                  <td className="n">{data.latest_buy_rank ?? ""}</td>
                  {horizons.flatMap((h) => {
                    const p = data.latest.find((x) => x.horizon === h);
                    const b = h === main ? "n b" : "n";
                    if (!p) {
                      return [
                        <td key={h} colSpan={2} className="dash">
                          -
                        </td>,
                      ];
                    }
                    const tip = `対象日 ${formatMonthDay(p.target_date)} ／ 市場比 ${formatPercent(p.rel_prob)} ／ 予測株価 ${formatPrice(p.pred_price)}`;
                    return [
                      <td key={`${h}-u`} className={`${b} tip`} title={tip}>
                        {formatPercent(p.up_prob)}
                      </td>,
                      <td key={`${h}-e`} className={`${b} tip ${signClass(p.expected)}`} title={tip}>
                        {formatSignedPercent(p.expected)}
                      </td>,
                    ];
                  })}
                </tr>
              </tbody>
            </table>
          </div>
          <p className="info">見込み = 予測株価 ÷ 予測日の終値 − 1。マウスを置くと、市場比・予測株価・対象日を出す</p>
        </>
      )}
    </div>
  );
}

/** 過去の予測と答え合わせ(予測日を1行にまとめ、予測日数ごとの列を横に並べた表) */
function HistoryTable({ data, horizons, main }: { data: PredictionsResponse; horizons: number[]; main: number }) {
  const days = groupHistory(data.history);
  return (
    <div className="panel">
      <h2>過去の予測と答え合わせ</h2>
      <div className="wrap">
        <table>
          <thead>
            <tr>
              <th rowSpan={2} className="fix">
                予測日
              </th>
              <th rowSpan={2}>予測時の株価</th>
              <th rowSpan={2}>買い候補の順位</th>
              {horizons.map((h) => (
                <th key={h} colSpan={4} className={h === main ? "main" : undefined}>
                  {h}日後
                </th>
              ))}
            </tr>
            <tr>
              {horizons.flatMap((h) =>
                ["上昇確率", "実際の株価", "値上がり率", "答え合わせ"].map((n) => (
                  <th key={`${h}-${n}`} className={h === main ? "main" : undefined}>
                    {n}
                  </th>
                )),
              )}
            </tr>
          </thead>
          <tbody>
            {days.length === 0 && (
              <tr>
                <td colSpan={3 + horizons.length * 4} className="c">
                  予測はありません
                </td>
              </tr>
            )}
            {days.map((d) => (
              <tr key={d.predDate}>
                <td className="fix">{formatDateWithWeekday(d.predDate)}</td>
                <td className="n">{formatPrice(d.basePrice)}</td>
                <td className="n">{d.buyRank ?? ""}</td>
                {horizons.flatMap((h) => {
                  const r = d.byHorizon.get(h);
                  const b = h === main ? "n b" : "n";
                  if (!r) {
                    return [
                      <td key={h} colSpan={4} className="dash">
                        -
                      </td>,
                    ];
                  }
                  const label = verifyLabel(r.verify_status, r.is_hit);
                  return [
                    <td key={`${h}-u`} className={b}>
                      {formatPercent(r.up_prob)}
                    </td>,
                    ...(r.actual_price === null
                      ? [
                          <td key={`${h}-a`} colSpan={2} className="dash">
                            -
                          </td>,
                        ]
                      : [
                          <td key={`${h}-a`} className="n">
                            {formatPrice(r.actual_price)}
                          </td>,
                          <td key={`${h}-r`} className={`n ${signClass(r.change_rate)}`}>
                            {formatSignedPercent(r.change_rate)}
                          </td>,
                        ]),
                    <td key={`${h}-v`} className={`c ${verifyClass(label)}`}>
                      {label}
                    </td>,
                  ];
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="info">
        予測日の新しい順。値上がり率 = 実際の株価 ÷ 予測時の株価 − 1(赤字 = 上がった、青字 = 下がった)。答え合わせの日が来ていない日数は「-」
        {data.history.length >= data.history_limit ? `。新しい順に ${data.history_limit} 件(予測日 × 予測日数)まで表示しています` : ""}
      </p>
    </div>
  );
}

/** 最新の予測と、過去の予測と答え合わせ(縦に並べる) */
function PredictionPanels({ code }: { code: string }) {
  const settings = useSettings();
  const { main_horizon: main, predict_horizons: horizons } = settings.batch;
  const res = useApi<PredictionsResponse>(`/api/stocks/${encodeURIComponent(code)}/predictions`);
  if (res.error) return <ErrorMessage message={res.error} />;
  if (!res.data) return <p className="loading">読み込み中…</p>;
  return (
    <>
      <LatestTable data={res.data} horizons={horizons} main={main} />
      <HistoryTable data={res.data} horizons={horizons} main={main} />
    </>
  );
}

export default function StockDetailPage() {
  const { code = "" } = useParams();
  const info = useApi<StockInfo>(`/api/stocks/${encodeURIComponent(code)}`);
  if (info.error) {
    return (
      <>
        <h1>銘柄詳細 {displayCode(code)}</h1>
        <ErrorMessage message={info.error} />
      </>
    );
  }
  if (!info.data) return <p className="loading">読み込み中…</p>;
  const s = info.data;
  return (
    <>
      <h1>
        {displayCode(s.code)} {s.name}{" "}
        <span className="h1sub">{[s.market, s.sector33, s.scale_category].filter(Boolean).join(" ／ ")}</span>
      </h1>
      <InfoCards info={s} />
      <StockChart code={s.code} />
      <div className="panel">
        <h2>備考{s.notes_date ? `(${formatDateWithWeekday(s.notes_date)})` : ""}</h2>
        {s.notes.length > 0 ? <NotesText notes={s.notes} /> : <span className="info">備考はありません</span>}
      </div>
      {s.is_target ? (
        <PredictionPanels code={s.code} />
      ) : (
        <div className="panel">
          <p className="info">予測の対象ではありません(予測するのはプライム・スタンダード・グロースの銘柄だけです)</p>
        </div>
      )}
    </>
  );
}
