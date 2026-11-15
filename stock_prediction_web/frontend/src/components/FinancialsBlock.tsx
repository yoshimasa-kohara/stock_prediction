// 銘柄詳細の業績のブロック。
// 通期・四半期の表、会社予想と修正、今期の予想の修正の履歴、予想の配当利回り。
// 前年比・修正の向きの判定は API(/api/stocks/{code}/financials)が行い、ここでは文字と色を決めるだけ
import { useState } from "react";

import type { FinancialRow, FinancialsResponse, ForecastItem, RevisionRow, YoY } from "../api/types";
import { useApi } from "../api/useApi";
import { formatOkuNumber, formatPercent, formatPrice, formatSignedPercent, signClass } from "../lib/format";
import ErrorMessage from "./ErrorMessage";
import Tabs from "./Tabs";

type TableKind = "annual" | "quarterly";

/** 前年比の文字と色(% 以外。黒字化・赤字縮小は赤、赤字化・赤字拡大は青) */
const YOY_LABELS: Record<string, { text: string; cls: string }> = {
  to_profit: { text: "黒字化", cls: "up" },
  to_loss: { text: "赤字化", cls: "dn" },
  loss_shrink: { text: "赤字縮小", cls: "up" },
  loss_expand: { text: "赤字拡大", cls: "dn" },
};

/** 修正の向きの文字と色(上方修正は赤、下方修正は青) */
const DIRECTION_LABELS: Record<string, { text: string; cls: string }> = {
  up: { text: "上方修正", cls: "up" },
  down: { text: "下方修正", cls: "dn" },
  mixed: { text: "上方・下方修正", cls: "eq" },
};

/** 開示の種類の文字 */
const DOC_KIND_LABELS: Record<string, string> = {
  statement: "決算短信",
  initial: "決算短信(前期の通期)",
  earn_revision: "業績予想の修正",
  dividend_revision: "配当予想の修正",
  other: "その他",
};

/** 会社予想の項目の名前と、1株あたりの値(円)か */
const FORECAST_LABELS: Record<string, { label: string; perShare: boolean }> = {
  sales: { label: "売上高(億円)", perShare: false },
  op: { label: "営業利益(億円)", perShare: false },
  np: { label: "純利益(億円)", perShare: false },
  eps: { label: "1株あたり利益(円)", perShare: true },
  div: { label: "年間配当(円)", perShare: true },
};

function YoYCell({ yoy }: { yoy: YoY }) {
  if (yoy.kind === "percent") return <td className={`n ${signClass(yoy.rate)}`}>{formatSignedPercent(yoy.rate)}</td>;
  const label = YOY_LABELS[yoy.kind];
  if (!label) return <td className="dash">-</td>;
  return <td className={`c ${label.cls}`}>{label.text}</td>;
}

/** 通期・四半期の表。四半期は、見出しと注記に「累計」と書く */
function ResultTable({ rows, kind }: { rows: FinancialRow[]; kind: TableKind }) {
  const sum = kind === "quarterly" ? "累計・" : "";
  return (
    <div className="wrap">
      <table aria-label={kind === "annual" ? "業績(通期)" : "業績(四半期・累計)"}>
        <thead>
          <tr>
            <th className="fix">決算期</th>
            <th>開示日</th>
            <th>売上高({sum}億円)</th>
            <th>前年比</th>
            <th>営業利益({sum}億円)</th>
            <th>前年比</th>
            <th>純利益({sum}億円)</th>
            <th>前年比</th>
            <th>営業利益率</th>
            <th>EPS({sum}円)</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={10} className="c">
                {kind === "annual" ? "通期の決算はありません" : "決算はありません"}
              </td>
            </tr>
          )}
          {rows.map((r) => (
            <tr key={`${r.fy_end}-${r.per_type}`}>
              <td className="fix">{r.period_label}</td>
              <td>{r.disc_date}</td>
              <td className="n">{formatOkuNumber(r.sales)}</td>
              <YoYCell yoy={r.sales_yoy} />
              <td className="n">{formatOkuNumber(r.op)}</td>
              <YoYCell yoy={r.op_yoy} />
              <td className="n">{formatOkuNumber(r.np)}</td>
              <YoYCell yoy={r.np_yoy} />
              <td className="n">{formatPercent(r.op_margin)}</td>
              <td className="n">{formatPrice(r.eps)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function forecastValue(item: ForecastItem | undefined, value: number | null | undefined): string {
  return item && FORECAST_LABELS[item.key]?.perShare ? formatPrice(value) : formatOkuNumber(value);
}

/** 予想の値と、修正のセル(上方修正・下方修正と、前の値) */
function ForecastCells({ item }: { item: ForecastItem | undefined }) {
  const dir = item?.direction ? DIRECTION_LABELS[item.direction] : undefined;
  return (
    <>
      <td className="n b">{forecastValue(item, item?.value)}</td>
      {dir && item ? (
        <td>
          <span className={dir.cls}>{dir.text}</span>(前の値 {forecastValue(item, item.prev_value)}。{item.revised_date})
        </td>
      ) : (
        <td className="dash">-</td>
      )}
    </>
  );
}

/** 会社予想と修正(表の下。今期・来期) */
function ForecastTable({ data }: { data: FinancialsResponse }) {
  const fc = data.forecast;
  if (!fc) return null;
  const div = data.dividend;
  return (
    <>
      <h3>会社予想と修正(最新の開示 {fc.disc_date})</h3>
      <div className="wrap">
        <table aria-label="会社予想と修正">
          <thead>
            <tr>
              <th className="fix">項目</th>
              <th>今期の予想({fc.period_label})</th>
              <th>修正</th>
              <th>来期の予想({fc.next_period_label})</th>
              <th>修正</th>
            </tr>
          </thead>
          <tbody>
            {fc.current.map((item) => (
              <tr key={item.key}>
                <td className="fix">{FORECAST_LABELS[item.key]?.label ?? item.key}</td>
                <ForecastCells item={item} />
                <ForecastCells item={fc.next.find((x) => x.key === item.key)} />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="info">
        予想の配当利回り: <b>{formatPercent(data.dividend_yield)}</b>
        {div &&
          `(${div.is_next ? "来期" : "今期"}の年間配当の予想 ${formatPrice(div.dividend)}円 ÷ ${div.price_date} の株価 ${formatPrice(div.price)}円)`}
        。修正は、同じ事業年度の前の開示の予想(期初の予想 = 前期の通期の決算短信の来期の予想を含む)と比べたもの。1株あたりの値は、開示日より後の株式分割を補正している
      </p>
    </>
  );
}

function RevisionValue({ value, direction }: { value: number | null; direction: string | null }) {
  const cls = direction ? (DIRECTION_LABELS[direction]?.cls ?? "") : "";
  return <td className={`n ${cls}`}>{formatOkuNumber(value)}</td>;
}

/** 今の事業年度の中の、予想の修正の履歴(新しい順) */
function RevisionTable({ rows, label }: { rows: RevisionRow[]; label: string }) {
  if (rows.length === 0) return null;
  return (
    <>
      <h3>今期({label})の予想の修正の履歴</h3>
      <div className="wrap">
        <table aria-label="予想の修正の履歴">
          <thead>
            <tr>
              <th className="fix">開示日</th>
              <th>開示</th>
              <th>売上高の予想(億円)</th>
              <th>営業利益の予想(億円)</th>
              <th>純利益の予想(億円)</th>
              <th>修正の向き</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const dir = r.direction ? DIRECTION_LABELS[r.direction] : undefined;
              return (
                <tr key={`${r.disc_date}-${i}`}>
                  <td className="fix">{r.disc_date}</td>
                  <td>{DOC_KIND_LABELS[r.doc_kind] ?? r.doc_kind}</td>
                  <RevisionValue value={r.sales} direction={r.sales_direction} />
                  <RevisionValue value={r.op} direction={r.op_direction} />
                  <RevisionValue value={r.np} direction={r.np_direction} />
                  {dir ? <td className={`c ${dir.cls}`}>{dir.text}</td> : <td className="dash">-</td>}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="info">最初の予想(期初の予想があればそれ)と、売上高・営業利益・純利益の予想のどれかが変わった開示(赤字 = 上げた、青字 = 下げた)</p>
    </>
  );
}

export default function FinancialsBlock({ code }: { code: string }) {
  const res = useApi<FinancialsResponse>(`/api/stocks/${encodeURIComponent(code)}/financials`);
  const [kind, setKind] = useState<TableKind>("quarterly");
  const data = res.data;
  const empty = data !== null && data.annual.length === 0 && data.quarterly.length === 0 && data.forecast === null;
  return (
    <div className="panel">
      <h2>業績{kind === "quarterly" && data && !empty ? "(四半期・累計)" : ""}</h2>
      {res.error && <ErrorMessage message={res.error} />}
      {res.loading && <p className="loading">読み込み中…</p>}
      {empty && <span className="info">業績のデータがありません</span>}
      {data && !empty && (
        <>
          <Tabs
            label="業績の表"
            items={[
              { value: "quarterly", label: "四半期" },
              { value: "annual", label: "通期" },
            ]}
            value={kind}
            onChange={setKind}
          />
          <ResultTable rows={kind === "annual" ? data.annual : data.quarterly} kind={kind} />
          <p className="info">
            {kind === "quarterly" &&
              "四半期の値は、期首からの累計(3か月ごとの値ではない。決算短信と同じ)。前年比も、前年の同じ期の累計と比べる。"}
            金額は億円。前年比は、赤字 = 増えた・良くなった、青字 = 減った・悪くなった。EPS(1株あたり利益)は、開示日より後の株式分割を補正している
          </p>
          <ForecastTable data={data} />
          <RevisionTable rows={data.revisions} label={data.forecast?.period_label ?? ""} />
        </>
      )}
    </div>
  );
}
