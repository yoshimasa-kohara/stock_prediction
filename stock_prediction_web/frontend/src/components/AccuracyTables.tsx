// 的中率の表(トップと的中率の画面で使う)。
// 買い候補 順位別 的中率(RankAccuracyPanel。的中率の画面の一番下)も、ここに置く。
// 左の表: 全銘柄・買い候補(・買い候補(業種分散)・上昇確率が高い銘柄)。右の表: 保有銘柄だけ(保有していた銘柄がない期間はその旨を出す)
import { type ReactNode, useState } from "react";

import type { PeriodValues, RankBand, RankResponse, SegmentValues } from "../api/types";
import { useApi } from "../api/useApi";
import { formatInteger, formatPercent, formatSignedNumber, formatSignedPercent, signClass } from "../lib/format";
import { PERIOD_LABELS } from "../lib/labels";
import ErrorMessage from "./ErrorMessage";
import Tabs from "./Tabs";

interface Props {
  periods: PeriodValues[];
  horizon: number;
  /** 的中率の画面では行を増やす(件数、上昇確率が高い銘柄、予測株価の平均誤差率) */
  detailed?: boolean;
}

type Getter = (p: PeriodValues) => SegmentValues | null;

function rateWithCount(v: SegmentValues | null): string {
  if (!v || v.hit_rate === null) return "-";
  return `${formatPercent(v.hit_rate)}(${formatInteger(v.verified_count)})`;
}

type Row = { label: string; cell: (p: PeriodValues) => { text: string; cls?: string } };

/**
 * 買い候補の業種比の行(業種の中央値を上回った割合、差の平均)。
 * withMarket のときは、前に的中率と市場比の行も付ける(買い候補(業種分散))
 */
function candidateRows(name: string, get: Getter, withMarket: boolean): Row[] {
  const rows: Row[] = [];
  if (withMarket) {
    rows.push(
      { label: `${name}: 的中率(件数)`, cell: (p) => ({ text: rateWithCount(get(p)) }) },
      { label: `${name}: 市場の中央値を上回った割合`, cell: (p) => ({ text: formatPercent(get(p)?.beat_rate) }) },
      {
        label: `${name}: 市場の中央値との差(平均)`,
        cell: (p) => ({ text: formatSignedPercent(get(p)?.avg_excess_rate), cls: signClass(get(p)?.avg_excess_rate) }),
      },
    );
  }
  rows.push(
    { label: `${name}: 業種の中央値を上回った割合`, cell: (p) => ({ text: formatPercent(get(p)?.sector_beat_rate) }) },
    {
      label: `${name}: 業種の中央値との差(平均)`,
      cell: (p) => ({
        text: formatSignedPercent(get(p)?.avg_sector_excess_rate),
        cls: signClass(get(p)?.avg_sector_excess_rate),
      }),
    },
  );
  return rows;
}

export default function AccuracyTables({ periods, horizon, detailed = false }: Props) {
  const all: Getter = (p) => p.all;
  const top: Getter = (p) => p.top;
  const sector: Getter = (p) => p.top_sector;
  const rows: Row[] = detailed
    ? [
        { label: "全銘柄: 的中率(件数)", cell: (p) => ({ text: rateWithCount(all(p)) }) },
        { label: "買い候補: 的中率(件数)", cell: (p) => ({ text: rateWithCount(top(p)) }) },
        { label: "買い候補: 市場の中央値を上回った割合", cell: (p) => ({ text: formatPercent(top(p)?.beat_rate) }) },
        {
          label: "買い候補: 市場の中央値との差(平均)",
          cell: (p) => ({ text: formatSignedPercent(top(p)?.avg_excess_rate), cls: signClass(top(p)?.avg_excess_rate) }),
        },
        ...candidateRows("買い候補", top, false),
        ...candidateRows("買い候補(業種分散)", sector, true),
        { label: "上昇確率が高い銘柄: 的中率(件数)", cell: (p) => ({ text: rateWithCount(p.high_prob) }) },
        { label: "予測株価の平均誤差率", cell: (p) => ({ text: formatPercent(all(p)?.avg_price_error_rate) }) },
      ]
    : [
        { label: "全銘柄の的中率", cell: (p) => ({ text: formatPercent(all(p)?.hit_rate) }) },
        { label: "買い候補: 市場の中央値を上回った割合", cell: (p) => ({ text: formatPercent(top(p)?.beat_rate) }) },
        {
          label: "買い候補: 市場の中央値との差(平均)",
          cell: (p) => ({ text: formatSignedPercent(top(p)?.avg_excess_rate), cls: signClass(top(p)?.avg_excess_rate) }),
        },
      ];
  const hasHolding = periods.some((p) => p.holding !== null);
  return (
    <div className="accs">
      <div className="wrap">
        <table>
          <thead>
            <tr>
              <th>{detailed ? "区分" : `${horizon}日後`}</th>
              {periods.map((p) => (
                <th key={p.period}>{PERIOD_LABELS[p.period] ?? p.period}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.label}>
                <td>{r.label}</td>
                {periods.map((p) => {
                  const c = r.cell(p);
                  return (
                    <td key={p.period} className={`n ${c.cls ?? ""}`}>
                      {c.text}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="wrap">
        <table>
          <thead>
            <tr>
              <th>保有銘柄({horizon}日後)</th>
              {periods.map((p) => (
                <th key={p.period}>{PERIOD_LABELS[p.period] ?? p.period}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {!hasHolding ? (
              <tr>
                <td colSpan={periods.length + 1} className="dash">
                  この期間に保有していた銘柄はありません
                </td>
              </tr>
            ) : (
              <>
                <tr>
                  <td>的中率(件数)</td>
                  {periods.map((p) => (
                    <td key={p.period} className="n">
                      {p.holding ? rateWithCount(p.holding) : "-"}
                    </td>
                  ))}
                </tr>
                <tr>
                  <td>市場の中央値を上回った割合</td>
                  {periods.map((p) => (
                    <td key={p.period} className="n">
                      {formatPercent(p.holding?.beat_rate)}
                    </td>
                  ))}
                </tr>
                <tr>
                  <td>市場の中央値との差(平均)</td>
                  {periods.map((p) => (
                    <td key={p.period} className={`n ${signClass(p.holding?.avg_excess_rate)}`}>
                      {formatSignedPercent(p.holding?.avg_excess_rate)}
                    </td>
                  ))}
                </tr>
              </>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** 1つの帯の上昇の的中率(件数が0なら null) */
function bandRate(c: { verified_count: number; hit_count: number } | undefined): number | null {
  if (!c || c.verified_count === 0) return null;
  return c.hit_count / c.verified_count;
}

/** 順位の帯の見出し(例: 1〜5位) */
function bandLabel(b: RankBand | undefined): string {
  return b ? `${b.rank_from}〜${b.rank_to}位` : "";
}

/**
 * 買い候補 順位別 的中率。上の予測日数のタブにかかわらず、全ての予測日数を出す。
 * 期間は全て1回の API で受け取り、タブで切り替える
 */
export function RankAccuracyPanel({ date, fixedPeriod, children }: { date?: string; fixedPeriod?: string; children?: ReactNode }) {
  const res = useApi<RankResponse>("/api/accuracy/rank", { date });
  const data = res.data;
  const [chosen, setChosen] = useState<string | undefined>(undefined);
  // fixedPeriod を渡すと、その期間だけを出す(期間のタブは出さない。トップで使う)
  const period = fixedPeriod ?? chosen ?? data?.default_period ?? "";
  const table = data?.periods.find((p) => p.period === period);
  const same = (a: RankBand, b: RankBand | undefined) => b !== undefined && a.rank_from === b.rank_from && a.rank_to === b.rank_to;
  const hiIdx = data ? data.bands.findIndex((b) => same(b, data.diff_bands[0])) : -1;
  const loIdx = data ? data.bands.findIndex((b) => same(b, data.diff_bands[1])) : -1;
  return (
    <div className="panel">
      <h2>買い候補 順位別 的中率</h2>
      {res.error && <ErrorMessage message={res.error} />}
      {data && data.periods.length === 0 && <p className="info">検証結果はまだありません</p>}
      {data && data.periods.length > 0 && (
        <>
          {fixedPeriod === undefined && (
            <Tabs
              label="買い候補 順位別 的中率の期間"
              items={data.periods.map((p) => ({ value: p.period, label: PERIOD_LABELS[p.period] ?? p.period }))}
              value={period}
              onChange={setChosen}
            />
          )}
          <div className="wrap">
            <table aria-label="買い候補 順位別 的中率">
              <thead>
                <tr>
                  <th className="fix">予測日数</th>
                  {data.bands.map((b) => (
                    <th key={bandLabel(b)}>{bandLabel(b)}</th>
                  ))}
                  <th>
                    {data.diff_bands.map(bandLabel).join("と")}の差
                  </th>
                </tr>
              </thead>
              <tbody>
                {table?.rows.map((r) => {
                  const hi = bandRate(r.counts[hiIdx]);
                  const lo = bandRate(r.counts[loIdx]);
                  // 差はポイント(的中率の差 × 100)。どちらかが「-」なら「-」
                  const diff = hi === null || lo === null ? null : (hi - lo) * 100;
                  return (
                    <tr key={r.horizon}>
                      <td className="fix">{r.horizon}日</td>
                      {r.counts.map((c, i) => (
                        <td key={bandLabel(data.bands[i])} className="n">
                          {formatPercent(bandRate(c))}
                        </td>
                      ))}
                      <td className={`n ${signClass(diff === null ? null : Number(diff.toFixed(1)))}`}>
                        {formatSignedNumber(diff, 1)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="info">
            上昇の的中率: 予測日数後の株価が予測日の株価より高ければ的中(騰落率0%は外れ)。期間は答え合わせの日(対象日)で区切る。
          </p>
          <p className="info">
            検証済みの件数({bandLabel(data.bands[0])}):{" "}
            {table?.rows.map((r) => `${r.horizon}日 ${formatInteger(r.counts[0]?.verified_count)}件`).join(" / ")}
          </p>
        </>
      )}
      {children}
    </div>
  );
}
