// 銘柄の検索。条件に合う銘柄を、買い候補と同じ形の表で見る。
// 21位以下の銘柄にも、メールと同じ条件で備考を出す。条件は URL に入れる(戻るボタンで前の検索に戻れる)
import { useState } from "react";
import { useSearchParams } from "react-router";

import { apiGet, toMessage } from "../api/client";
import type { PredictionDates, ScreenResponse, ScreenRow } from "../api/types";
import { useApi } from "../api/useApi";
import ChipSelect from "../components/ChipSelect";
import ErrorMessage from "../components/ErrorMessage";
import PredDatePicker from "../components/PredDatePicker";
import PredictionTable from "../components/PredictionTable";
import { useSettings } from "../context/contexts";
import { formatInteger } from "../lib/format";

/** URL のクエリの名前(API と同じ) */
const KEYS = ["date", "q", "markets", "sectors", "min_rel", "min_up", "min_ret", "notes", "exclude", "sort", "order"];
/** 文字の項目は、最初に押したとき小さい順(それ以外は大きい順) */
const ASC_FIRST = ["code", "name", "market", "rank"];

interface Form {
  q: string;
  markets: string[];
  sectors: string[];
  min_rel: string;
  min_up: string;
  min_ret: string;
  notes: string[];
  exclude: string[];
}

function split(text: string | null): string[] {
  return (text ?? "").split(",").filter(Boolean);
}

function formFromParams(params: URLSearchParams): Form {
  return {
    q: params.get("q") ?? "",
    markets: split(params.get("markets")),
    sectors: split(params.get("sectors")),
    min_rel: params.get("min_rel") ?? "",
    min_up: params.get("min_up") ?? "",
    min_ret: params.get("min_ret") ?? "",
    notes: split(params.get("notes")),
    exclude: split(params.get("exclude")),
  };
}

export default function SearchPage() {
  const settings = useSettings();
  const main = settings.batch.main_horizon;
  const [params, setParams] = useSearchParams();
  const query = Object.fromEntries(KEYS.map((k) => [k, params.get(k) ?? undefined]));
  const key = JSON.stringify(query);
  const dates = useApi<PredictionDates>("/api/prediction-dates");
  const res = useApi<ScreenResponse>("/api/screen", query);
  const [form, setForm] = useState<{ key: string; value: Form } | null>(null);
  const current = form?.key === key ? form.value : formFromParams(params);
  const update = (patch: Partial<Form>) => setForm({ key, value: { ...current, ...patch } });
  const [more, setMore] = useState<{ key: string; rows: ScreenRow[]; hasMore: boolean } | null>(null);
  const [moreError, setMoreError] = useState<string | null>(null);
  const extra = more?.key === key ? more : null;
  const data = res.data;

  /** 条件を URL に入れる(API がもう一度呼ばれる) */
  function apply(patch: Record<string, string | undefined>) {
    const next = new URLSearchParams();
    const values: Record<string, string | undefined> = {
      date: params.get("date") ?? undefined,
      q: current.q.trim() || undefined,
      markets: current.markets.join(",") || undefined,
      sectors: current.sectors.join(",") || undefined,
      min_rel: current.min_rel.trim() || undefined,
      min_up: current.min_up.trim() || undefined,
      min_ret: current.min_ret.trim() || undefined,
      notes: current.notes.join(",") || undefined,
      exclude: current.exclude.join(",") || undefined,
      sort: params.get("sort") ?? undefined,
      order: params.get("order") ?? undefined,
      ...patch,
    };
    for (const [k, v] of Object.entries(values)) if (v) next.set(k, v);
    setParams(next);
    setForm(null);
  }

  function onSort(sortKey: string) {
    const nowKey = data?.sort ?? "";
    const nowOrder = data?.order ?? "desc";
    const order = sortKey === nowKey ? (nowOrder === "asc" ? "desc" : "asc") : ASC_FIRST.includes(sortKey) ? "asc" : "desc";
    apply({ sort: sortKey, order });
  }

  async function loadMore() {
    const loaded = (data?.rows.length ?? 0) + (extra?.rows.length ?? 0);
    try {
      const r = await apiGet<ScreenResponse>("/api/screen", { ...query, offset: loaded });
      setMore({ key, rows: [...(extra?.rows ?? []), ...r.rows], hasMore: r.has_more });
      setMoreError(null);
    } catch (e) {
      setMoreError(toMessage(e));
    }
  }

  const rows = [...(data?.rows ?? []), ...(extra?.rows ?? [])];
  const hasMore = extra ? extra.hasMore : (data?.has_more ?? false);
  const pct = (name: keyof Form, label: string) => (
    <>
      <label htmlFor={`sc-${name}`}>
        {main}日後の{label}
      </label>
      <span>
        <input
          id={`sc-${name}`}
          inputMode="decimal"
          style={{ width: 70 }}
          value={current[name] as string}
          onChange={(e) => update({ [name]: e.target.value })}
        />
        % 以上
      </span>
    </>
  );

  return (
    <>
      <h1>銘柄の検索</h1>
      <div className="panel">
        <h2>検索条件</h2>
        <div className="form wide">
          <span>予測日</span>
          <span className="inline">
            <PredDatePicker
              value={data?.pred_date ?? params.get("date") ?? ""}
              dates={dates.data?.dates ?? []}
              onChange={(d) => apply({ date: d })}
              showLabel={false}
            />
          </span>
          <label htmlFor="sc-q">銘柄コード・銘柄名</label>
          <input
            id="sc-q"
            placeholder="例: 7203、トヨタ"
            style={{ maxWidth: 260 }}
            value={current.q}
            onChange={(e) => update({ q: e.target.value })}
            onKeyDown={(e) => e.key === "Enter" && apply({})}
          />
          <span>市場</span>
          <span className="inline">
            {(data?.market_options ?? []).map((m) => (
              <label key={m} className="check">
                <input
                  type="checkbox"
                  checked={current.markets.length === 0 || current.markets.includes(m)}
                  onChange={(e) => {
                    const all = data?.market_options ?? [];
                    const base = current.markets.length === 0 ? all : current.markets;
                    const next = e.target.checked ? [...base, m] : base.filter((x) => x !== m);
                    update({ markets: next.length === all.length ? [] : next });
                  }}
                />
                {m}
              </label>
            ))}
          </span>
          <span>33業種</span>
          <ChipSelect
            label="33業種"
            options={data?.sector_options ?? []}
            values={current.sectors}
            onChange={(v) => update({ sectors: v })}
          />
          {pct("min_rel", "市場比")}
          {pct("min_up", "上昇確率")}
          {pct("min_ret", "見込み")}
          <span>備考</span>
          <ChipSelect label="備考" options={data?.note_options ?? []} values={current.notes} onChange={(v) => update({ notes: v })} />
          <span>除く</span>
          <span className="inline">
            {(data?.exclude_options ?? []).map((o) => (
              <label key={o.value} className="check">
                <input
                  type="checkbox"
                  checked={current.exclude.includes(o.value)}
                  onChange={(e) =>
                    update({
                      exclude: e.target.checked ? [...current.exclude, o.value] : current.exclude.filter((x) => x !== o.value),
                    })
                  }
                />
                {o.label}
              </label>
            ))}
          </span>
          <span />
          <span className="inline">
            <button type="button" className="btn" onClick={() => apply({})}>
              検索
            </button>
            <button
              type="button"
              className="btn sub"
              onClick={() => {
                const date = params.get("date");
                setParams(date ? { date } : {});
                setForm(null);
              }}
            >
              条件をクリア
            </button>
          </span>
        </div>
      </div>
      {dates.error && <ErrorMessage message={dates.error} />}
      {res.error && <ErrorMessage message={res.error} />}
      {res.loading && <p className="loading">検索しています…(備考で絞るときは、初回は数秒かかります)</p>}
      {data && (
        <>
          <div className="cond">
            検索結果 <b>{formatInteger(data.total)} 件</b>(予測した銘柄 {formatInteger(data.code_count)} 件のうち)
            <span className="condinfo">
              並べ方: 列の見出しを押すと並べ替え(初期値は{main}日後の市場比の確率が高い順)。
              {settings.web.screen_page_size}件ずつ表示
            </span>
          </div>
          <PredictionTable
            rows={rows}
            horizons={settings.batch.predict_horizons}
            mainHorizon={main}
            rankLabel="買い候補の順位"
            emptyMessage="条件に合う銘柄はありません"
            sort={{ key: data.sort, order: data.order === "asc" ? "asc" : "desc", onSort }}
          />
          <p className="info">
            「買い候補の順位」は、その予測日の買い候補(上位{settings.batch.buy_candidate_count}
            銘柄)に入っている銘柄だけに出す。備考は{settings.batch.buy_candidate_count + 1}
            位以下の銘柄にも、メールと同じ条件で出す。
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
