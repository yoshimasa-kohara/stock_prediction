// 買い候補と同じ形の表(メールの買い候補の表と同じ列)。
// 買い候補、銘柄の検索、トップで使う。銘柄名の列は横スクロールのときも左に固定する
import type { ReactNode } from "react";

import type { HorizonValue, Note } from "../api/types";
import { displayCode, formatPercent, formatPrice } from "../lib/format";
import NotesText from "./NotesText";
import StockName from "./StockName";

/** 表の1行 */
export interface PredictionRow {
  rank: number | null;
  code: string;
  name: string;
  market: string;
  /** 業種(showSector のときだけ出す) */
  sector?: string;
  base_price: number;
  horizons: HorizonValue[];
  notes: Note[];
}

/** 並べ替え(銘柄の検索で使う) */
export interface SortState {
  key: string;
  order: "asc" | "desc";
  onSort: (key: string) => void;
}

interface Props {
  rows: PredictionRow[];
  /** 出す予測日数(この順) */
  horizons: number[];
  mainHorizon: number;
  /** 順位の列の見出し */
  rankLabel?: string;
  /** 行がないときに出す文 */
  emptyMessage?: string;
  sort?: SortState;
  /** 市場の次に業種の列を出す(買い候補の画面。メールと同じ) */
  showSector?: boolean;
}

/** 予測日数ごとの3つの列(市場比 → 上昇確率 → 予測株価。メールと同じ順) */
const COLUMNS: { key: "rel" | "up" | "pred"; label: string }[] = [
  { key: "rel", label: "市場比" },
  { key: "up", label: "上昇確率" },
  { key: "pred", label: "予測株価" },
];

function SortableTh({
  sortKey,
  sort,
  className,
  children,
  rowSpan,
}: {
  sortKey: string;
  sort?: SortState;
  className?: string;
  children: ReactNode;
  rowSpan?: number;
}) {
  if (!sort) {
    return (
      <th className={className} rowSpan={rowSpan}>
        {children}
      </th>
    );
  }
  const active = sort.key === sortKey;
  return (
    <th
      className={[className, "sortable"].filter(Boolean).join(" ")}
      rowSpan={rowSpan}
      aria-sort={active ? (sort.order === "asc" ? "ascending" : "descending") : undefined}
    >
      <button type="button" className="sortbtn" onClick={() => sort.onSort(sortKey)}>
        {children}
        {active ? (sort.order === "asc" ? " ▲" : " ▼") : ""}
      </button>
    </th>
  );
}

export default function PredictionTable({
  rows,
  horizons,
  mainHorizon,
  rankLabel = "順位",
  emptyMessage,
  sort,
  showSector = false,
}: Props) {
  const single = horizons.length === 1;
  const columnCount = (showSector ? 6 : 5) + horizons.length * COLUMNS.length + 1;
  return (
    <div className="wrap">
      <table>
        <thead>
          <tr>
            <SortableTh sortKey="rank" sort={sort} rowSpan={single ? 1 : 2}>
              {rankLabel}
            </SortableTh>
            <SortableTh sortKey="code" sort={sort} rowSpan={single ? 1 : 2}>
              コード
            </SortableTh>
            <SortableTh sortKey="name" sort={sort} className="fix" rowSpan={single ? 1 : 2}>
              銘柄名
            </SortableTh>
            <SortableTh sortKey="market" sort={sort} rowSpan={single ? 1 : 2}>
              市場
            </SortableTh>
            {showSector && <th rowSpan={single ? 1 : 2}>業種</th>}
            <SortableTh sortKey="base_price" sort={sort} rowSpan={single ? 1 : 2}>
              現在値
            </SortableTh>
            {single
              ? COLUMNS.map((c) => (
                  <SortableTh
                    key={c.key}
                    sortKey={`${c.key}_${horizons[0]}`}
                    sort={sort}
                    className={horizons[0] === mainHorizon ? "main" : undefined}
                  >
                    {horizons[0]}日後 {c.label}
                  </SortableTh>
                ))
              : horizons.map((h) => (
                  <th key={h} colSpan={3} className={h === mainHorizon ? "main" : undefined}>
                    {h}日後
                  </th>
                ))}
            <th rowSpan={single ? 1 : 2}>備考</th>
          </tr>
          {!single && (
            <tr>
              {horizons.flatMap((h) =>
                COLUMNS.map((c) => (
                  <SortableTh
                    key={`${h}-${c.key}`}
                    sortKey={`${c.key}_${h}`}
                    sort={sort}
                    className={h === mainHorizon ? "main" : undefined}
                  >
                    {c.label}
                  </SortableTh>
                )),
              )}
            </tr>
          )}
        </thead>
        <tbody>
          {rows.length === 0 && emptyMessage && (
            <tr>
              <td colSpan={columnCount} className="c">
                {emptyMessage}
              </td>
            </tr>
          )}
          {rows.map((r) => (
            <tr key={r.code}>
              <td className="n">{r.rank ?? ""}</td>
              <td>{displayCode(r.code)}</td>
              <td className="fix">
                <StockName code={r.code} name={r.name} />
              </td>
              <td>{r.market}</td>
              {showSector && <td>{r.sector ?? ""}</td>}
              <td className="n">{formatPrice(r.base_price)}</td>
              {horizons.flatMap((h) => {
                const v = r.horizons.find((x) => x.horizon === h);
                const b = h === mainHorizon ? "n b" : "n";
                return [
                  <td key={`${h}-rel`} className={b}>
                    {formatPercent(v?.rel_prob)}
                  </td>,
                  <td key={`${h}-up`} className={b}>
                    {formatPercent(v?.up_prob)}
                  </td>,
                  <td key={`${h}-pred`} className={b}>
                    {formatPrice(v?.pred_price)}
                  </td>,
                ];
              })}
              <td>
                <NotesText notes={r.notes} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
