// 銘柄別の的中率・業種別の的中率(的中率の画面)。
// 期間のタブと予想の向きは表ごとに別に持ち、切り替えたときに、その条件の分だけ読む(全期間は数秒かかる)
import { useState } from "react";

import { apiGet, toMessage } from "../api/client";
import type { SectorAccuracyResponse, SectorAccuracyValues, StockAccuracyResponse, StockAccuracyRow } from "../api/types";
import { useApi } from "../api/useApi";
import { useSettings } from "../context/contexts";
import { displayCode, formatInteger, formatPercent, formatSignedPercent, signClass } from "../lib/format";
import { type Order, sortSectorRows } from "../lib/accuracySort";
import { PERIOD_LABELS } from "../lib/labels";
import ErrorMessage from "./ErrorMessage";
import { SortableTh, type SortState } from "./PredictionTable";
import StockName from "./StockName";
import Tabs from "./Tabs";
import WatchStockButton from "./WatchStockButton";

interface PanelProps {
  /** 集計の基準日(省略すると最新) */
  date?: string;
  horizon: number;
}

/** 期間のタブ(直近1か月〜全期間。順位別の的中率と同じ並び) */
function PeriodTabs({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  // 画面だけを先にビルドし、API をまだ再起動していない間は、/api/settings にこの項目がない(画面を止めないように空にする)
  const periods: string[] = useSettings().web.accuracy_periods ?? [];
  return (
    <Tabs label={label} items={periods.map((p) => ({ value: p, label: PERIOD_LABELS[p] ?? p }))} value={value} onChange={onChange} />
  );
}

/** 予想の向きの切り替え(上昇予想・下降予想)。見た目は銘柄別の「対象」とそろえる */
function DirectionTabs({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  // API を再起動する前は、/api/settings にこの項目がない(タブを出さずに、画面は止めない)
  const directions = useSettings().web.accuracy_directions ?? {};
  return (
    <div className="cond flat">
      予想の向き
      <Tabs label={label} items={Object.entries(directions).map(([v, l]) => ({ value: v, label: l }))} value={value} onChange={onChange} />
    </div>
  );
}

/** 予想の向きごとの初期の並び(銘柄別は API の順位の順と同じになる。同じ値の行は順位の順のまま) */
const STOCK_INITIAL_SORT: Record<string, { key: string; order: Order }> = {
  up: { key: "beat_rate", order: "desc" },
  down: { key: "hit_rate", order: "desc" },
};
const SECTOR_INITIAL_SORT: Record<string, { key: string; order: Order }> = {
  up: { key: "all.beat_rate", order: "desc" },
  down: { key: "all.hit_rate", order: "desc" },
};

/** 予想の向きの初期値(API を再起動する前で設定がなければ、上昇予想) */
function useDefaultDirection(): string {
  return useSettings().web.accuracy_direction_default ?? "up";
}

/** 予想の向きの、表の下の説明(数え方と初期の並び) */
function directionNote(direction: string, threshold: number, target: "stocks" | "sectors"): string {
  const prefix = target === "sectors" ? "全銘柄の" : "";
  const order = target === "stocks" ? "初期の並びと順位" : "初期の並び";
  return direction === "down"
    ? `下がると予想した日(上昇確率が${formatPercent(threshold)}未満)だけで数える。${order}は${prefix}的中率の高い順(同じなら${prefix}騰落率の平均の低い順)。`
    : `上がると予想した日(上昇確率が${formatPercent(threshold)}以上)だけで数える。${order}は${prefix}市場の中央値を上回った割合の高い順(同じなら${prefix}騰落率の平均の高い順)。`;
}

/** 文字の項目は、最初に押したとき小さい順(それ以外は大きい順) */
const STOCK_ASC_FIRST = ["rank", "code", "name", "market", "sector33"];

/** 銘柄別の表の、並べ替えられる列(銘柄名は左に固定する) */
const STOCK_COLUMNS: { key: string; label: string; fix?: boolean }[] = [
  { key: "rank", label: "順位" },
  { key: "code", label: "コード" },
  { key: "name", label: "銘柄名", fix: true },
  { key: "market", label: "市場" },
  { key: "sector33", label: "業種" },
  { key: "verified_count", label: "件数" },
  { key: "hit_rate", label: "的中率" },
  { key: "beat_rate", label: "市場の中央値を上回った割合" },
  { key: "avg_change_rate", label: "騰落率の平均" },
  { key: "avg_price_error_rate", label: "予測株価の平均誤差率" },
];

/** 銘柄別の的中率 */
export function StockAccuracyPanel({ date, horizon }: PanelProps) {
  const { web, batch } = useSettings();
  const [period, setPeriod] = useState(web.accuracy_breakdown_default_period);
  const [segment, setSegment] = useState<"all" | "top">("all");
  const [direction, setDirection] = useState(useDefaultDirection());
  const [sort, setSort] = useState<{ key: string; order: Order }>(STOCK_INITIAL_SORT[direction] ?? STOCK_INITIAL_SORT.up!);
  const query = { date, horizon, period, segment, direction, sort: sort.key, order: sort.order };
  const key = JSON.stringify(query);
  const res = useApi<StockAccuracyResponse>("/api/accuracy/stocks", query);
  const [more, setMore] = useState<{ key: string; rows: StockAccuracyRow[]; hasMore: boolean } | null>(null);
  const [moreError, setMoreError] = useState<string | null>(null);
  const extra = more?.key === key ? more : null;
  const data = res.data;
  const rows = [...(data?.rows ?? []), ...(extra?.rows ?? [])];
  const hasMore = extra ? extra.hasMore : (data?.has_more ?? false);
  const minCount = segment === "top" ? web.accuracy_stock_min_count_top : web.accuracy_stock_min_count_all;

  function onSort(sortKey: string) {
    const order: Order = sortKey === sort.key ? (sort.order === "asc" ? "desc" : "asc") : STOCK_ASC_FIRST.includes(sortKey) ? "asc" : "desc";
    setSort({ key: sortKey, order });
  }

  /** 予想の向きを切り替えたら、並べ替えは初期の並びに戻す */
  function onDirection(d: string) {
    setDirection(d);
    setSort(STOCK_INITIAL_SORT[d] ?? STOCK_INITIAL_SORT.up!);
  }

  async function loadMore() {
    try {
      const r = await apiGet<StockAccuracyResponse>("/api/accuracy/stocks", { ...query, offset: rows.length });
      setMore({ key, rows: [...(extra?.rows ?? []), ...r.rows], hasMore: r.has_more });
      setMoreError(null);
    } catch (e) {
      setMoreError(toMessage(e));
    }
  }

  const sortState: SortState = { key: sort.key, order: sort.order, onSort };
  return (
    <div className="panel">
      <h2>
        銘柄別の的中率({horizon}日後、{PERIOD_LABELS[period] ?? period})
      </h2>
      <PeriodTabs label="銘柄別の的中率の期間" value={period} onChange={setPeriod} />
      <div className="cond flat">
        対象
        <Tabs
          label="銘柄別の的中率の対象"
          items={[
            { value: "all", label: "全銘柄" },
            { value: "top", label: "買い候補に入った日だけ" },
          ]}
          value={segment}
          onChange={setSegment}
        />
      </div>
      <DirectionTabs label="銘柄別の的中率の予想の向き" value={direction} onChange={onDirection} />
      <p className="info">
        件数が{formatInteger(minCount)}件以上の銘柄だけを出す
        {data && data.summary_date !== null ? `(${formatInteger(data.total)}銘柄)` : ""}。{web.accuracy_stock_page_size}
        件ずつ表示。列の見出しを押すと並べ替え(並べ替えても順位の数字は変わらない)
      </p>
      {res.error && <ErrorMessage message={res.error} />}
      {res.loading && <p className="loading">読み込み中…(全期間は数秒かかります)</p>}
      {data && (
        <div className="wrap">
          <table aria-label="銘柄別の的中率">
            <thead>
              <tr>
                {STOCK_COLUMNS.map((c) => (
                  <SortableTh key={c.key} sortKey={c.key} sort={sortState} className={c.fix ? "fix" : undefined}>
                    {c.label}
                  </SortableTh>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr>
                  <td colSpan={STOCK_COLUMNS.length} className="c">
                    条件に合う銘柄はありません
                  </td>
                </tr>
              )}
              {rows.map((r) => (
                <tr key={r.code}>
                  <td className="n">{r.rank}</td>
                  <td>{displayCode(r.code)}</td>
                  <td className="fix">
                    <WatchStockButton code={r.code} name={r.name} />
                    <StockName code={r.code} name={r.name} />
                  </td>
                  <td>{r.market}</td>
                  <td>{r.sector33}</td>
                  <td className="n">{formatInteger(r.verified_count)}</td>
                  <td className="n b">{formatPercent(r.hit_rate)}</td>
                  <td className="n">{formatPercent(r.beat_rate)}</td>
                  <td className={`n ${signClass(r.avg_change_rate)}`}>{formatSignedPercent(r.avg_change_rate)}</td>
                  <td className="n">{formatPercent(r.avg_price_error_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {moreError && <ErrorMessage message={moreError} />}
      {hasMore && (
        <p className="more">
          <button type="button" className="btn sub" onClick={() => void loadMore()}>
            もっと見る
          </button>
        </p>
      )}
      <p className="info">
        {directionNote(direction, batch.up_prob_threshold, "stocks")}
        検証済みの予測を、答え合わせの日(対象日)の期間で数える。的中率は予想が当たった割合(上昇予想は上がった、下降予想は上がらなかった割合)。
        予測株価の平均誤差率 = |予測株価 − 実際の株価| ÷ 実際の株価 の平均
      </p>
    </div>
  );
}

/** 業種別の表の値の列(全銘柄・買い候補で同じ並び) */
const SECTOR_VALUE_COLUMNS: { key: keyof SectorAccuracyValues; label: string }[] = [
  { key: "verified_count", label: "件数" },
  { key: "hit_rate", label: "的中率" },
  { key: "beat_rate", label: "市場の中央値を上回った割合" },
  { key: "avg_change_rate", label: "騰落率の平均" },
];

function SectorCells({ v }: { v: SectorAccuracyValues }) {
  // 件数が0のときは、件数も「-」にする(買い候補に入った銘柄がない業種)
  if (v.verified_count === 0) {
    return (
      <>
        {SECTOR_VALUE_COLUMNS.map((c) => (
          <td key={c.key} className="dash">
            -
          </td>
        ))}
      </>
    );
  }
  return (
    <>
      <td className="n">{formatInteger(v.verified_count)}</td>
      <td className="n b">{formatPercent(v.hit_rate)}</td>
      <td className="n">{formatPercent(v.beat_rate)}</td>
      <td className={`n ${signClass(v.avg_change_rate)}`}>{formatSignedPercent(v.avg_change_rate)}</td>
    </>
  );
}

/** 業種別の的中率 */
export function SectorAccuracyPanel({ date, horizon }: PanelProps) {
  const { web, batch } = useSettings();
  const [period, setPeriod] = useState(web.accuracy_breakdown_default_period);
  const [direction, setDirection] = useState(useDefaultDirection());
  // 並べ替えは画面で行う(33業種だけなので読み直さない)。初期値は API の並び(予想の向きで変わる)と同じになる
  const [sort, setSort] = useState<{ key: string; order: Order }>(SECTOR_INITIAL_SORT[direction] ?? SECTOR_INITIAL_SORT.up!);
  const res = useApi<SectorAccuracyResponse>("/api/accuracy/sectors", { date, horizon, period, direction });
  const data = res.data;
  const rows = data ? sortSectorRows(data.rows, sort.key, sort.order) : [];
  const sortState: SortState = {
    key: sort.key,
    order: sort.order,
    onSort: (k) =>
      setSort({ key: k, order: k === sort.key ? (sort.order === "asc" ? "desc" : "asc") : k === "sector33" ? "asc" : "desc" }),
  };
  return (
    <div className="panel">
      <h2>
        業種別の的中率({horizon}日後、{PERIOD_LABELS[period] ?? period})
      </h2>
      <PeriodTabs label="業種別の的中率の期間" value={period} onChange={setPeriod} />
      <DirectionTabs
        label="業種別の的中率の予想の向き"
        value={direction}
        onChange={(d) => {
          setDirection(d);
          setSort(SECTOR_INITIAL_SORT[d] ?? SECTOR_INITIAL_SORT.up!);
        }}
      />
      {res.error && <ErrorMessage message={res.error} />}
      {res.loading && <p className="loading">読み込み中…(全期間は数秒かかります)</p>}
      {data && (
        <div className="wrap">
          <table aria-label="業種別の的中率">
            <thead>
              <tr>
                <SortableTh sortKey="sector33" sort={sortState} className="fix" rowSpan={2}>
                  業種
                </SortableTh>
                <th colSpan={SECTOR_VALUE_COLUMNS.length}>全銘柄</th>
                <th colSpan={SECTOR_VALUE_COLUMNS.length} className="main">
                  買い候補
                </th>
              </tr>
              <tr>
                {(["all", "top"] as const).flatMap((side) =>
                  SECTOR_VALUE_COLUMNS.map((c) => (
                    <SortableTh
                      key={`${side}.${c.key}`}
                      sortKey={`${side}.${c.key}`}
                      sort={sortState}
                      className={side === "top" ? "main" : undefined}
                    >
                      {c.label}
                    </SortableTh>
                  )),
                )}
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr>
                  <td colSpan={1 + SECTOR_VALUE_COLUMNS.length * 2} className="c">
                    この期間の業種別の集計はありません
                  </td>
                </tr>
              )}
              {rows.map((r) => (
                <tr key={r.sector33_code ?? r.sector33}>
                  <td className="fix">{r.sector33}</td>
                  <SectorCells v={r.all} />
                  <SectorCells v={r.top} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="info">
        {directionNote(direction, batch.up_prob_threshold, "sectors")}
        業種は、予測した時点の33業種。買い候補は、買い候補(上位{batch.buy_candidate_count}
        銘柄)に入った日の予測だけを数える(入った銘柄がない業種は「-」)。列の見出しを押すと並べ替え
      </p>
    </div>
  );
}
