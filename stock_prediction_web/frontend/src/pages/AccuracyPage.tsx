// 的中率。予測がどのくらい当たっているかを、期間・予測日数ごとに見る
import { useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { AccuracyResponse, DailyResponse } from "../api/types";
import { useApi } from "../api/useApi";
import AccuracyTables, { RankAccuracyPanel } from "../components/AccuracyTables";
import DateInput from "../components/DateInput";
import ErrorMessage from "../components/ErrorMessage";
import Tabs from "../components/Tabs";
import { useSettings } from "../context/contexts";
import { gapPoints } from "../lib/calibration";
import { formatInteger, formatPercent, formatSignedPercent } from "../lib/format";

/** グラフの色(画面見本と同じ) */
const RED = "#c00000";
const NAVY = "#2f3e5c";

function DailyChart({ horizon }: { horizon: number }) {
  const [range, setRange] = useState<{ from?: string; to?: string }>({});
  const res = useApi<DailyResponse>("/api/accuracy/daily", { horizon, ...range });
  const data = res.data;
  return (
    <div className="panel">
      <h2>予測日ごとの推移({horizon}日後)</h2>
      <div className="cond flat">
        期間
        <DateInput label="推移の期間(開始)" value={range.from ?? data?.date_from ?? ""} onChange={(v) => setRange({ ...range, from: v })} />〜
        <DateInput label="推移の期間(終了)" value={range.to ?? data?.date_to ?? ""} onChange={(v) => setRange({ ...range, to: v })} />
      </div>
      {res.error && <ErrorMessage message={res.error} />}
      {data && data.points.length === 0 && <p className="info">この期間に答え合わせが済んだ予測はありません</p>}
      {data && data.points.length > 0 && (
        <div className="linechart" data-testid="accuracy-chart">
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={data.points} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
              <CartesianGrid stroke="#eeeeee" />
              <XAxis dataKey="pred_date" tick={{ fontSize: 11 }} minTickGap={24} />
              <YAxis yAxisId="left" tick={{ fontSize: 11 }} tickFormatter={(v: number) => formatSignedPercent(v)} />
              <YAxis
                yAxisId="right"
                orientation="right"
                domain={[0, 1]}
                tick={{ fontSize: 11 }}
                tickFormatter={(v: number) => formatPercent(v)}
              />
              <Tooltip
                formatter={(value, name) =>
                  name === "top_excess"
                    ? [formatSignedPercent(Number(value)), "買い候補: 市場の中央値との差"]
                    : [formatPercent(Number(value)), "全銘柄: 的中率"]
                }
              />
              <Legend
                formatter={(name: string) =>
                  name === "top_excess" ? "買い候補: 市場の中央値との差(左の目盛り)" : "全銘柄: 的中率(破線、右の目盛り)"
                }
              />
              <Line yAxisId="left" type="monotone" dataKey="top_excess" stroke={RED} dot={false} strokeWidth={2} />
              <Line
                yAxisId="right"
                type="monotone"
                dataKey="all_hit_rate"
                stroke={NAVY}
                dot={false}
                strokeDasharray="5 4"
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

export default function AccuracyPage() {
  const settings = useSettings();
  const { main_horizon: main, predict_horizons: horizons } = settings.batch;
  const [horizon, setHorizon] = useState(String(main));
  const [date, setDate] = useState<string | undefined>(undefined);
  const [period, setPeriod] = useState<"3M" | "ALL">("3M");
  const h = Number(horizon);
  const res = useApi<AccuracyResponse>("/api/accuracy", { horizon: h, date });
  const data = res.data;
  const bands = data?.calibration[period] ?? [];

  return (
    <>
      <h1>的中率</h1>
      <div className="cond">
        予測日数
        <Tabs
          label="予測日数"
          items={horizons.map((x) => ({ value: String(x), label: x === main ? `${x}日後(主)` : `${x}日後` }))}
          value={horizon}
          onChange={setHorizon}
        />
        <span className="condinfo">
          集計の基準日{" "}
          <DateInput
            label="集計の基準日"
            value={data?.summary_date ?? date ?? ""}
            onChange={setDate}
            enabledDates={data?.summary_dates}
          />
        </span>
      </div>
      {res.error && <ErrorMessage message={res.error} />}
      {res.loading && <p className="loading">読み込み中…</p>}
      {data && data.summary_date === null && <p className="info">検証結果はまだありません</p>}
      {data && data.summary_date !== null && (
        <>
          <div className="panel">
            <h2>期間の集計({h}日後)</h2>
            <AccuracyTables periods={data.periods} horizon={h} detailed />
            <p className="info">
              期間は対象日(答え合わせの日)で区切る。保有銘柄は、予測日に保有していた銘柄の予測(購入日〜売却日の前日)を数える。
            </p>
          </div>
          <DailyChart horizon={h} />
          <div className="panel">
            <h2>
              上昇確率の確かさ({h}日後、{period === "3M" ? "直近3か月" : "全期間"})
            </h2>
            <Tabs
              label="上昇確率の確かさの期間"
              items={[
                { value: "3M", label: "直近3か月" },
                { value: "ALL", label: "全期間" },
              ]}
              value={period}
              onChange={setPeriod}
            />
            <div className="wrap">
              <table>
                <thead>
                  <tr>
                    <th>確率の帯</th>
                    <th>検証した件数</th>
                    <th>確率の平均</th>
                    <th>実際に上がった割合</th>
                    <th>ずれ(ポイント)</th>
                  </tr>
                </thead>
                <tbody>
                  {bands.length === 0 && (
                    <tr>
                      <td colSpan={5} className="c">
                        集計はありません
                      </td>
                    </tr>
                  )}
                  {bands.map((b) => (
                    <tr key={b.prob_band} className={b.warn ? "hl" : undefined}>
                      <td>
                        {b.prob_band}〜{b.prob_band + 10}%
                      </td>
                      <td className="n">{formatInteger(b.verified_count)}</td>
                      <td className="n">{formatPercent(b.avg_up_prob)}</td>
                      <td className="n">{formatPercent(b.up_rate)}</td>
                      <td className={`n ${b.gap === null ? "" : b.gap > 0 ? "up" : b.gap < 0 ? "dn" : "eq"}`}>
                        {gapPoints(b.gap)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="info">
              赤い行: 件数が{formatInteger(settings.batch.calibration_min_count)}件以上で、ずれが
              {Math.round(settings.batch.calibration_warn_gap * 100)}ポイントより大きい帯(メールの注意と同じ条件)
            </p>
          </div>
        </>
      )}
      {/* 予測日数のタブを変えても読み直さず、期間のタブを残すため、上の表の読み込みとは別に置く */}
      {!res.error && data?.summary_date !== null && <RankAccuracyPanel date={date} />}
    </>
  );
}
