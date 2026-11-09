// 買い候補と実際の株価。表の形・色・まとめ行は 画面フォーマット_買い候補と実際の株価.md のとおり。
// 買い候補と買い候補(業種分散)をタブで切り替える
import { type ReactNode, useState } from "react";
import { useSearchParams } from "react-router";

import type { ResultDay, ResultsResponse } from "../api/types";
import { useApi } from "../api/useApi";
import DateInput from "../components/DateInput";
import ErrorMessage from "../components/ErrorMessage";
import NotesText from "../components/NotesText";
import StockName from "../components/StockName";
import Tabs from "../components/Tabs";
import { useSettings } from "../context/contexts";
import { KIND_TABS, kindDescription, kindLabel, kindParam, parseKind } from "../lib/candidateKind";
import {
  displayCode,
  formatDateWithWeekday,
  formatMonthDay,
  formatPercent,
  formatPrice,
  formatSignedNumber,
  formatSignedPercent,
  signClass,
} from "../lib/format";

/** 上がった・同じ・下がったの矢印 */
function arrow(value: number): string {
  return value > 0 ? "↑" : value < 0 ? "↓" : "→";
}

/** 予測日数ごとの7つの列の見出し(画面フォーマットのとおり) */
function DayTable({ day, horizons, main }: { day: ResultDay; horizons: number[]; main: number }) {
  const targetOf = (h: number) => day.rows.flatMap((r) => r.cells).find((c) => c.horizon === h)?.target_date;
  return (
    <div className="wrap">
      <table>
        <thead>
          <tr>
            <th rowSpan={2}>順位</th>
            <th rowSpan={2}>コード</th>
            <th rowSpan={2} className="fix">
              銘柄名
            </th>
            <th rowSpan={2}>市場</th>
            <th rowSpan={2}>現在値</th>
            {horizons.map((h) => {
              const t = targetOf(h);
              return (
                <th key={h} colSpan={7} className={h === main ? "main" : undefined}>
                  {h}日後{t ? `(対象日 ${formatMonthDay(t)})` : ""}
                </th>
              );
            })}
            <th rowSpan={2}>備考</th>
          </tr>
          <tr>
            {horizons.flatMap((h) => [
              ...["市場比", "上昇確率", "予測株価"].map((n) => (
                <th key={`${h}-${n}`} className={h === main ? "main" : undefined}>
                  {n}
                </th>
              )),
              <th key={`${h}-a`} className="act">
                実際の株価
              </th>,
              <th key={`${h}-d`} className="act">
                差額
                <br />
                (実際の株価−現在値)
              </th>,
              <th key={`${h}-r`} className="act">
                値上がり率
                <br />
                (差額÷現在値)
              </th>,
              <th key={`${h}-u`} className="act">
                上昇
                <br />
                (↑/→/↓)
              </th>,
            ])}
          </tr>
        </thead>
        <tbody>
          {day.rows.map((r) => (
            <tr key={r.code}>
              <td className="n">{r.rank}</td>
              <td>{displayCode(r.code)}</td>
              <td className="fix">
                <StockName code={r.code} name={r.name} />
              </td>
              <td>{r.market}</td>
              <td className="n">{formatPrice(r.base_price)}</td>
              {horizons.flatMap((h) => {
                const c = r.cells.find((x) => x.horizon === h);
                const b = h === main ? "n b" : "n";
                if (!c) {
                  return [
                    <td key={h} colSpan={7} className="dash">
                      -
                    </td>,
                  ];
                }
                const head = [
                  <td key={`${h}-rel`} className={b}>
                    {formatPercent(c.rel_prob)}
                  </td>,
                  <td key={`${h}-up`} className={b}>
                    {formatPercent(c.up_prob)}
                  </td>,
                  <td key={`${h}-pred`} className={b}>
                    {formatPrice(c.pred_price)}
                  </td>,
                ];
                if (c.actual === null) {
                  return [
                    ...head,
                    <td key={`${h}-dash`} colSpan={4} className="dash">
                      -
                    </td>,
                  ];
                }
                const diff = c.actual - r.base_price;
                const rate = r.base_price ? diff / r.base_price : 0;
                const k = signClass(diff);
                return [
                  ...head,
                  <td key={`${h}-a`} className={`n ${k}`}>
                    {formatPrice(c.actual)}
                  </td>,
                  <td key={`${h}-d`} className={`n ${k}`}>
                    {formatSignedNumber(diff, 1)}
                  </td>,
                  <td key={`${h}-r`} className={`n ${k}`}>
                    {formatSignedPercent(rate)}
                  </td>,
                  <td key={`${h}-u`} className={`c ${k}`}>
                    {arrow(diff)}
                  </td>,
                ];
              })}
              <td>
                <NotesText notes={r.notes} />
              </td>
            </tr>
          ))}
        </tbody>
        <Summary day={day} horizons={horizons} />
      </table>
    </div>
  );
}

/** まとめ3行(平均の値上がり率と上がった銘柄 / 市場の中央値と中央値を上回った銘柄 / 中央値との差) */
function Summary({ day, horizons }: { day: ResultDay; horizons: number[] }) {
  const lines: ReactNode[][] = [
    [
      <td key="head" colSpan={5} rowSpan={3} className="fix">
        {day.rows.length}銘柄のまとめ
      </td>,
    ],
    [],
    [],
  ];
  const sumCell = (key: string, label: string, value: number) => (
    <td key={key} colSpan={5} className="n">
      {label} <span className={signClass(value)}>{formatSignedPercent(value)}</span>
    </td>
  );
  for (const h of horizons) {
    const rates = day.rows.flatMap((r) => {
      const c = r.cells.find((x) => x.horizon === h);
      return c && c.actual !== null && r.base_price ? [(c.actual - r.base_price) / r.base_price] : [];
    });
    const s = day.summary.find((x) => x.horizon === h);
    if (rates.length === 0) {
      lines.forEach((line, i) =>
        line.push(
          <td key={`${h}-${i}`} colSpan={7} className="dash">
            -
          </td>,
        ),
      );
      continue;
    }
    const avg = rates.reduce((a, b) => a + b, 0) / rates.length;
    const up = rates.filter((v) => v > 0).length;
    lines[0]?.push(
      sumCell(`${h}-avg`, "平均の値上がり率", avg),
      <td key={`${h}-up`} colSpan={2} className="c">
        上がった銘柄 ↑ {up} / {rates.length}
      </td>,
    );
    if (!s || s.market_median === null) {
      lines[1]?.push(
        <td key={`${h}-m`} colSpan={7} className="dash">
          -
        </td>,
      );
      lines[2]?.push(
        <td key={`${h}-x`} colSpan={7} className="dash">
          -
        </td>,
      );
      continue;
    }
    const beat = s.beat_count === null ? "-" : `${s.beat_count} / ${rates.length}`;
    lines[1]?.push(
      sumCell(`${h}-m`, "市場の中央値", s.market_median),
      <td key={`${h}-b`} colSpan={2} className="c">
        中央値を上回った銘柄 {beat}
      </td>,
    );
    lines[2]?.push(sumCell(`${h}-x`, "中央値との差", avg - s.market_median), <td key={`${h}-e`} colSpan={2} />);
  }
  lines[0]?.push(<td key="note" rowSpan={3} />);
  return (
    <tfoot>
      {lines.map((cells, i) => (
        <tr key={i}>{cells}</tr>
      ))}
    </tfoot>
  );
}

export default function ResultsPage() {
  const settings = useSettings();
  const [params, setParams] = useSearchParams();
  const kind = parseKind(params.get("kind"));
  const query = { from: params.get("from") ?? undefined, to: params.get("to") ?? undefined };
  const res = useApi<ResultsResponse>("/api/candidates/results", { ...query, ...kindParam(kind) });
  const [form, setForm] = useState<{ from: string; to: string } | null>(null);
  const current = form ?? { from: query.from ?? res.data?.date_from ?? "", to: query.to ?? res.data?.date_to ?? "" };
  const { main_horizon: main, predict_horizons: horizons } = settings.batch;
  const label = kindLabel(kind);
  // 期間を指定していれば、切り替えても同じ期間のまま
  const range = { ...(query.from ? { from: query.from } : {}), ...(query.to ? { to: query.to } : {}) };
  // 予測日は新しい日付が上(降順。API は古い順で返すので、画面で並べ替える。目次と表の両方)
  const days = [...(res.data?.days ?? [])].sort((a, b) => b.pred_date.localeCompare(a.pred_date));

  return (
    <>
      <h1 id="top">買い候補と実際の株価</h1>
      <div className="cond">
        期間
        <DateInput label="期間(開始)" value={current.from} onChange={(v) => setForm({ ...current, from: v })} />〜
        <DateInput label="期間(終了)" value={current.to} onChange={(v) => setForm({ ...current, to: v })} />
        <button
          type="button"
          className="btn"
          onClick={() => {
            setParams({ from: current.from, to: current.to, ...kindParam(kind) });
            setForm(null);
          }}
        >
          表示
        </button>
        <span className="condinfo">1回に表示するのは最大{settings.web.results_max_months}か月</span>
      </div>
      <div className="cond">
        <Tabs
          label="買い候補の種類"
          items={KIND_TABS}
          value={kind}
          onChange={(k) => {
            setParams({ ...range, ...kindParam(k) });
            setForm(null);
          }}
        />
      </div>
      <p className="info">
        予測日ごとの{label}({kindDescription(kind, settings.batch)}。メールと同じ内容)に、{horizons.join("・")}
        営業日後の実際の株価を並べたもの。主となる日数: {main}営業日後(緑の見出し)
        <br />
        実際の株価・差額・値上がり率・上昇: <span className="up">赤字 = 現在値より上がった</span>、
        <span className="dn">青字 = 下がった</span>、<span className="eq">灰色 = 同じ(→)</span>
        。答え合わせの日が来ていない日数は「-」。予測のない日(土日・祝日など)は表がありません
      </p>
      {res.error && <ErrorMessage message={res.error} />}
      {res.loading && <p className="loading">読み込み中…</p>}
      {res.data && res.data.days.length === 0 && <p className="info">この期間の{label}はありません。</p>}
      {days.length > 0 && (
        <nav className="toc" aria-label="目次">
          目次:{" "}
          {days.map((d) => (
            <a key={d.pred_date} href={`#d${d.pred_date}`}>
              {formatDateWithWeekday(d.pred_date)}
            </a>
          ))}
        </nav>
      )}
      {days.map((d) => (
        <section key={d.pred_date}>
          <h2 id={`d${d.pred_date}`}>
            {formatDateWithWeekday(d.pred_date)} の{label}
            <span className="h2sub">
              (予測に使ったモデル: {d.model_version}) <a href="#top">▲目次へ</a>
            </span>
          </h2>
          <DayTable day={d} horizons={horizons} main={main} />
        </section>
      ))}
    </>
  );
}
