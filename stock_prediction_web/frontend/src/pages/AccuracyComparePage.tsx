// 精度の比較。全銘柄・買い候補・買い候補(業種分散)を、全ての予測日数で並べ、
// 精度を判断するための情報(件数と期間、業種の内訳、月ごと、使ったモデル)を出す。表は Markdown でコピーできる
import { useState } from "react";

import type { CompareResponse } from "../api/types";
import { useApi } from "../api/useApi";
import DateInput from "../components/DateInput";
import ErrorMessage from "../components/ErrorMessage";
import Tabs from "../components/Tabs";
import { useSettings } from "../context/contexts";
import { copyText } from "../lib/clipboard";
import { type Table, compareMarkdown, compareSections, conditionLines } from "../lib/compareTables";
import { PERIOD_LABELS } from "../lib/labels";

type Period = "1M" | "3M" | "ALL";

const PERIOD_TABS: { value: Period; label: string }[] = (["1M", "3M", "ALL"] as const).map((p) => ({
  value: p,
  label: PERIOD_LABELS[p] ?? p,
}));

/** 左に固定する列のクラス(1列目は fix1、2列目は fix2。styles.css) */
function fixClass(table: Table, col: number): string {
  if (table.fixCols === undefined || col >= table.fixCols) return "";
  return `fix fix${col + 1}`;
}

function TableView({ table, showTitle }: { table: Table; showTitle: boolean }) {
  return (
    <div>
      {showTitle && <h3>{table.title}</h3>}
      <div className="wrap">
        <table aria-label={table.title}>
          <thead>
            <tr>
              {table.headers.map((h, j) => (
                <th key={h} className={fixClass(table, j)}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.length === 0 && (
              <tr>
                <td colSpan={table.headers.length} className="dash">
                  -
                </td>
              </tr>
            )}
            {table.rows.map((row, i) => (
              <tr key={i}>
                {row.map((c, j) => (
                  <td key={j} className={[c.num ? "n" : "", c.cls ?? "", fixClass(table, j)].filter(Boolean).join(" ") || undefined}>
                    {c.text}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {table.note && <p className="info">{table.note}</p>}
    </div>
  );
}

export default function AccuracyComparePage() {
  const settings = useSettings();
  const [date, setDate] = useState<string | undefined>(undefined);
  const [period, setPeriod] = useState<Period>("1M"); // 初期値は直近1か月
  const res = useApi<CompareResponse>("/api/accuracy/compare", { date, period });
  const data = res.data;
  const topN = settings.web.compare_sector_top_n;
  // コピーの結果(コピーできなかったときは、手で選べるように Markdown を出す)
  const [copy, setCopy] = useState<{ ok: boolean; text: string } | null>(null);

  const onCopy = async () => {
    if (!data) return;
    const text = compareMarkdown(data, settings.batch, topN);
    setCopy({ ok: await copyText(text), text });
  };

  return (
    <>
      <h1>精度の比較</h1>
      <div className="cond">
        集計の基準日
        <DateInput
          label="集計の基準日"
          value={data?.summary_date ?? date ?? ""}
          onChange={(v) => {
            setDate(v);
            setCopy(null);
          }}
          enabledDates={data?.summary_dates}
        />
        <Tabs
          label="期間"
          items={PERIOD_TABS}
          value={period}
          onChange={(p) => {
            setPeriod(p);
            setCopy(null);
          }}
        />
        <button type="button" className="btn" disabled={!data || data.summary_date === null} onClick={() => void onCopy()}>
          Markdown でコピー
        </button>
        {copy?.ok && <span className="condinfo">コピーしました</span>}
      </div>
      {copy && !copy.ok && (
        <div className="panel">
          <p className="info">
            コピーできませんでした(ブラウザーが許可していません)。下の枠の中を全て選んでコピーしてください
          </p>
          <textarea aria-label="コピーする Markdown" className="mdcopy" readOnly value={copy.text} rows={12} />
        </div>
      )}
      {res.error && <ErrorMessage message={res.error} />}
      {res.loading && <p className="loading">読み込み中…</p>}
      {data && data.summary_date === null && <p className="info">検証結果はまだありません</p>}
      {data && data.summary_date !== null && (
        <>
          <ul className="info">
            {conditionLines(data, settings.batch).map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
          {compareSections(data, topN).map((s) => (
            <div className="panel" key={s.title}>
              <h2>{s.title}</h2>
              <div className={s.tables.length > 1 ? "accs" : undefined}>
                {s.tables.map((t) => (
                  <TableView key={t.title} table={t} showTitle={s.tables.length > 1} />
                ))}
              </div>
            </div>
          ))}
        </>
      )}
    </>
  );
}
