// ランキング。予測日を選んで、7種類の決まった並べ方のランキングを見る。
// URL には date・type・horizon だけを入れる(絞り込みは入れない)。予測の値と備考は、銘柄検索と同じ計算(API)
import { useState } from "react";
import { useSearchParams } from "react-router";

import { apiGet, toMessage } from "../api/client";
import type { PredictionDates, RankingRow, RankingsResponse } from "../api/types";
import { useApi } from "../api/useApi";
import ChipSelect from "../components/ChipSelect";
import ErrorMessage from "../components/ErrorMessage";
import NotesText from "../components/NotesText";
import PredDatePicker from "../components/PredDatePicker";
import StockName from "../components/StockName";
import Tabs from "../components/Tabs";
import WatchStockButton from "../components/WatchStockButton";
import { useSettings } from "../context/contexts";
import { gapPoints } from "../lib/calibration";
import { displayCode, formatInteger, formatOkuNumber, formatPercent, formatPrice, formatSignedPercent, signClass } from "../lib/format";
import { usePageTitle } from "../lib/usePageTitle";

/** ランキングの種類(URL の type。この順にタブを出す) */
const TYPES = [
  { value: "rel", label: "①市場比の確率" },
  { value: "up", label: "②上昇確率" },
  { value: "ret", label: "③予測の値上がり率" },
  { value: "down", label: "④下がりそうな銘柄" },
  { value: "rise", label: "⑤確率が急に上がった銘柄" },
  { value: "all", label: "⑥全期間で高い銘柄" },
  { value: "regular", label: "⑦買い候補の常連" },
];
/** 初期の種類 */
const DEFAULT_TYPE = "rel";
/** 予測日数を使わない種類(予測日数のタブを薄くして、選べないようにする) */
const NO_HORIZON_TYPES = ["all", "regular"];

/** 絞り込みの条件(URL には入れない) */
interface Filters {
  markets: string[];
  sectors: string[];
  excludeIlliquid: boolean;
  minTurnover: string;
  perMin: string;
  perMax: string;
  pbrMin: string;
  pbrMax: string;
  minRoe: string;
}

/** 絞り込みの初期値(「流動性注意を除く」は ON) */
const DEFAULT_FILTERS: Filters = {
  markets: [],
  sectors: [],
  excludeIlliquid: true,
  minTurnover: "",
  perMin: "",
  perMax: "",
  pbrMin: "",
  pbrMax: "",
  minRoe: "",
};

/** 絞り込みの条件を、API のクエリにする(空の条件は入れない) */
function filterQuery(f: Filters): Record<string, string | undefined> {
  const text = (v: string) => v.trim() || undefined;
  return {
    markets: f.markets.join(",") || undefined,
    sectors: f.sectors.join(",") || undefined,
    exclude_illiquid: f.excludeIlliquid ? undefined : "false",
    min_turnover: text(f.minTurnover),
    per_min: text(f.perMin),
    per_max: text(f.perMax),
    pbr_min: text(f.pbrMin),
    pbr_max: text(f.pbrMax),
    min_roe: text(f.minRoe),
  };
}

/** ランキングの値の列の見出し(種類ごと) */
function valueLabel(type: string, horizon: number, horizonCount: number): string {
  switch (type) {
    case "up":
      return `上昇確率(${horizon}日)`;
    case "ret":
      return `予測の値上がり率(${horizon}日)`;
    case "rise":
      return "前の予測日との差(ポイント)";
    case "all":
      return `${horizonCount}期間の最小`;
    case "regular":
      return "買い候補に入った日数(平均順位)";
    default:
      return `市場比(${horizon}日)`;
  }
}

/** ランキングの値のセル(太字) */
function ValueCell({ type, row }: { type: string; row: RankingRow }) {
  if (type === "ret") return <td className={`n b ${signClass(row.value)}`}>{formatSignedPercent(row.value)}</td>;
  if (type === "rise") return <td className={`n b ${signClass(row.value)}`}>{gapPoints(row.value)}</td>;
  if (type === "regular") {
    return (
      <td className="n b">
        {formatInteger(row.value)}日({row.sub_value === null ? "-" : `${row.sub_value.toFixed(1)}位`})
      </td>
    );
  }
  return <td className="n b">{formatPercent(row.value)}</td>;
}

/** 種類ごとの説明(表の上に出す) */
function typeNote(type: string, data: RankingsResponse, horizon: number, regularDays: number): string {
  switch (type) {
    case "up":
      return `${horizon}日後の上昇確率が高い順`;
    case "ret":
      return `${horizon}日後の予測の値上がり率(予測株価 ÷ 予測日の株価 − 1)が高い順`;
    case "down":
      return `${horizon}日後の市場比の確率が低い順`;
    case "rise":
      return data.prev_pred_date
        ? `${horizon}日後の市場比の確率が、前の予測日(${data.prev_pred_date})から大きく上がった順。前の予測日に予測がない銘柄は出さない`
        : "前の予測日がないので、比べられません";
    case "all":
      return "全ての予測日数の市場比の確率のうち、一番低い値が高い順。どれかの予測日数の予測がない銘柄は出さない";
    case "regular":
      return `この予測日を含む直近${regularDays}予測日(今ある予測日は${data.regular_days}日)のうち、買い候補に入った日数が多い順。同じ日数なら、入った日の平均順位が良い順`;
    default:
      return `${horizon}日後の市場比の確率が高い順`;
  }
}

/** 数の欄の値(null は「-」。digits は小数の桁数) */
function fixed(value: number | null, digits: number): string {
  return value === null ? "-" : value.toFixed(digits);
}

export default function RankingsPage() {
  usePageTitle("ランキング");
  const settings = useSettings();
  const { main_horizon: main, predict_horizons: horizons } = settings.batch;
  const [params, setParams] = useSearchParams();
  const type = params.get("type") ?? DEFAULT_TYPE;
  const horizon = params.get("horizon") ?? String(main);
  const noHorizon = NO_HORIZON_TYPES.includes(type);
  // 入力中の条件(form)と、表に効いている条件(applied)。「絞り込む」を押したときに applied に入れる
  const [form, setForm] = useState<Filters>(DEFAULT_FILTERS);
  const [applied, setApplied] = useState<Filters>(DEFAULT_FILTERS);
  const update = (patch: Partial<Filters>) => setForm({ ...form, ...patch });
  const query = {
    date: params.get("date") ?? undefined,
    type: params.get("type") ?? undefined,
    horizon: params.get("horizon") ?? undefined,
    ...filterQuery(applied),
  };
  const key = JSON.stringify(query);
  const dates = useApi<PredictionDates>("/api/prediction-dates");
  const res = useApi<RankingsResponse>("/api/rankings", query);
  const [more, setMore] = useState<{ key: string; rows: RankingRow[]; hasMore: boolean } | null>(null);
  const [moreError, setMoreError] = useState<string | null>(null);
  const extra = more?.key === key ? more : null;
  const data = res.data;
  const rows = [...(data?.rows ?? []), ...(extra?.rows ?? [])];
  const hasMore = extra ? extra.hasMore : (data?.has_more ?? false);

  /** URL(date・type・horizon)を変える */
  function setUrl(patch: Record<string, string>) {
    const next = new URLSearchParams();
    const values: Record<string, string | null> = {
      date: params.get("date"),
      type: params.get("type"),
      horizon: params.get("horizon"),
      ...patch,
    };
    for (const [k, v] of Object.entries(values)) if (v) next.set(k, v);
    setParams(next);
  }

  async function loadMore() {
    try {
      const r = await apiGet<RankingsResponse>("/api/rankings", { ...query, offset: rows.length });
      setMore({ key, rows: [...(extra?.rows ?? []), ...r.rows], hasMore: r.has_more });
      setMoreError(null);
    } catch (e) {
      setMoreError(toMessage(e));
    }
  }

  /** 数の入力欄(下限・上限など) */
  const num = (name: keyof Filters, label: string, width = 64) => (
    <input
      aria-label={label}
      inputMode="decimal"
      style={{ width }}
      value={form[name] as string}
      onChange={(e) => update({ [name]: e.target.value })}
      onKeyDown={(e) => e.key === "Enter" && setApplied(form)}
    />
  );
  const marketOptions = data?.market_options ?? [];
  const h = Number(horizon);

  return (
    <>
      <h1>ランキング</h1>
      <div className="cond">
        <PredDatePicker
          value={data?.pred_date ?? params.get("date") ?? ""}
          dates={dates.data?.dates ?? []}
          onChange={(d) => setUrl({ date: d })}
        />
      </div>
      <div className="tabscroll">
        <Tabs label="ランキングの種類" items={TYPES} value={type} onChange={(v) => setUrl({ type: v })} />
      </div>
      <div className="cond">
        予測日数
        <Tabs
          label="予測日数"
          items={horizons.map((x) => ({ value: String(x), label: x === main ? `${x}日後(主)` : `${x}日後` }))}
          value={horizon}
          onChange={(v) => setUrl({ horizon: v })}
          disabled={noHorizon}
        />
        {noHorizon && <span className="condinfo">このランキングでは、予測日数は選べません</span>}
      </div>
      <div className="panel">
        <h2>絞り込み</h2>
        <div className="form wide">
          <span>市場</span>
          <span className="inline">
            {marketOptions.map((m) => (
              <label key={m} className="check">
                <input
                  type="checkbox"
                  checked={form.markets.length === 0 || form.markets.includes(m)}
                  onChange={(e) => {
                    const base = form.markets.length === 0 ? marketOptions : form.markets;
                    const next = e.target.checked ? [...base, m] : base.filter((x) => x !== m);
                    update({ markets: next.length === marketOptions.length ? [] : next });
                  }}
                />
                {m}
              </label>
            ))}
          </span>
          <span>33業種</span>
          <ChipSelect label="33業種" options={data?.sector_options ?? []} values={form.sectors} onChange={(v) => update({ sectors: v })} />
          <span>流動性</span>
          <span className="inline">
            <label className="check">
              <input type="checkbox" checked={form.excludeIlliquid} onChange={(e) => update({ excludeIlliquid: e.target.checked })} />
              流動性注意を除く
            </label>
          </span>
          <span>売買代金(20日平均)</span>
          <span>{num("minTurnover", "売買代金の下限")} 億円 以上</span>
          <span>PER(予想)</span>
          <span>
            {num("perMin", "PERの下限")} 倍 〜 {num("perMax", "PERの上限")} 倍
          </span>
          <span>PBR</span>
          <span>
            {num("pbrMin", "PBRの下限")} 倍 〜 {num("pbrMax", "PBRの上限")} 倍
          </span>
          <span>ROE(実績)</span>
          <span>{num("minRoe", "ROEの下限")} % 以上</span>
          <span />
          <span className="inline">
            <button type="button" className="btn" onClick={() => setApplied(form)}>
              絞り込む
            </button>
            <button
              type="button"
              className="btn sub"
              onClick={() => {
                setForm(DEFAULT_FILTERS);
                setApplied(DEFAULT_FILTERS);
              }}
            >
              条件をクリア
            </button>
          </span>
        </div>
        <p className="info">
          PER・PBR・ROE は予測日の値。条件を入れた指標に値がない銘柄は出さない(PER の条件を入れたときは、PER がマイナスの銘柄も出さない)
        </p>
      </div>
      {dates.error && <ErrorMessage message={dates.error} />}
      {res.error && <ErrorMessage message={res.error} />}
      {res.loading && <p className="loading">読み込み中…</p>}
      {data && (
        <>
          <div className="cond">
            <b>{formatInteger(data.total)} 件</b>(予測した銘柄 {formatInteger(data.code_count)} 件のうち)
            <span className="condinfo">
              {typeNote(type, data, h, settings.web.ranking_regular_days)}。{settings.web.ranking_page_size}件ずつ表示
            </span>
          </div>
          <div className="wrap">
            <table aria-label="ランキング">
              <thead>
                <tr>
                  <th rowSpan={2}>順位</th>
                  <th rowSpan={2}>コード</th>
                  <th rowSpan={2} className="fix">
                    銘柄名
                  </th>
                  <th rowSpan={2}>市場</th>
                  <th rowSpan={2}>業種</th>
                  <th rowSpan={2} className="act">
                    {valueLabel(type, h, horizons.length)}
                  </th>
                  <th rowSpan={2}>予測日の株価</th>
                  {horizons.map((x) => (
                    <th key={x} colSpan={3} className={x === main ? "main" : undefined}>
                      {x}日後
                    </th>
                  ))}
                  <th rowSpan={2}>PER(予想)</th>
                  <th rowSpan={2}>PBR</th>
                  <th rowSpan={2}>売買代金(20日平均・億円)</th>
                  <th rowSpan={2}>備考</th>
                </tr>
                <tr>
                  {horizons.flatMap((x) =>
                    ["市場比", "上昇確率", "予測株価"].map((n) => (
                      <th key={`${x}-${n}`} className={x === main ? "main" : undefined}>
                        {n}
                      </th>
                    )),
                  )}
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={11 + horizons.length * 3} className="c">
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
                    <ValueCell type={type} row={r} />
                    <td className="n">{formatPrice(r.base_price)}</td>
                    {horizons.flatMap((x) => {
                      const v = r.horizons.find((y) => y.horizon === x);
                      const b = x === main ? "n b" : "n";
                      return [
                        <td key={`${x}-rel`} className={b}>
                          {formatPercent(v?.rel_prob)}
                        </td>,
                        <td key={`${x}-up`} className={b}>
                          {formatPercent(v?.up_prob)}
                        </td>,
                        <td key={`${x}-pred`} className={b}>
                          {formatPrice(v?.pred_price)}
                        </td>,
                      ];
                    })}
                    <td className="n">{fixed(r.fwd_per, 1)}</td>
                    <td className="n">{fixed(r.pbr, 2)}</td>
                    <td className="n">{formatOkuNumber(r.avg_turnover)}</td>
                    <td>
                      <NotesText notes={r.notes} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="info">
            値が同じ銘柄は、銘柄コードの順。備考はメールと同じ条件。銘柄名の左の「☆」で検討中銘柄に追加する(追加済みは「★」)。
          </p>
          {moreError && <ErrorMessage message={moreError} />}
          {hasMore && (
            <p>
              <button type="button" className="btn sub" onClick={() => void loadMore()}>
                もっと見る
              </button>
            </p>
          )}
        </>
      )}
    </>
  );
}
