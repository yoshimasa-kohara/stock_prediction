// 実行履歴。バッチがいつ動いて、成功したかを見る。
// 対象の日付は、処理の対象の日付(target_date)で絞る(R1 の案A)
import { useState } from "react";
import { useSearchParams } from "react-router";

import { apiGet, toMessage } from "../api/client";
import type { RunRow, RunsResponse } from "../api/types";
import { useApi } from "../api/useApi";
import DateInput from "../components/DateInput";
import ErrorMessage from "../components/ErrorMessage";
import { useSettings } from "../context/contexts";
import { formatDateTime, formatElapsed, formatInteger, truncate } from "../lib/format";
import { statusBadgeClass } from "../lib/labels";

/** エラーの内容を、表でそのまま出す最大の文字数(超えた分はマウスを置く・押すと出す) */
export const ERROR_PREVIEW_LENGTH = 40;

/** 処理の選択肢の並び */
const PROCESS_ORDER = ["COLLECT", "PREDICT", "VERIFY", "NOTIFY", "TRAIN", "BACKUP", "CLEANUP", "DAILY_BATCH", "WEEKLY_BATCH"];
/** 全体の処理は「〜全体」と出す */
const WHOLE_LABELS: Record<string, string> = { DAILY_BATCH: "日次バッチ全体", WEEKLY_BATCH: "週次バッチ全体" };

function ErrorCell({ text }: { text: string | null }) {
  const [open, setOpen] = useState(false);
  if (!text) return <td />;
  const long = text.length > ERROR_PREVIEW_LENGTH;
  return (
    <td className="ng errcell" title={text}>
      {long ? (
        <button type="button" className="linkbtn ng" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          {open ? text : truncate(text, ERROR_PREVIEW_LENGTH)}
        </button>
      ) : (
        text
      )}
    </td>
  );
}

export default function RunsPage() {
  const settings = useSettings();
  const [params, setParams] = useSearchParams();
  const query = {
    from: params.get("from") ?? undefined,
    to: params.get("to") ?? undefined,
    process: params.get("process") ?? undefined,
    status: params.get("status") ?? undefined,
  };
  const first = useApi<RunsResponse>("/api/runs", query);
  // 「もっと見る」で読んだ続きの行
  const [more, setMore] = useState<{ key: string; rows: RunRow[]; hasMore: boolean } | null>(null);
  const [moreError, setMoreError] = useState<string | null>(null);
  const key = JSON.stringify(query);
  const extra = more?.key === key ? more : null;

  // 条件の入力欄(「表示」を押すまで URL には入れない)
  const [form, setForm] = useState<{ from: string; to: string; process: string; status: string } | null>(null);
  const current = form ?? {
    from: query.from ?? first.data?.date_from ?? "",
    to: query.to ?? first.data?.date_to ?? "",
    process: query.process ?? "",
    status: query.status ?? "",
  };
  const update = (patch: Partial<typeof current>) => setForm({ ...current, ...patch });

  function show() {
    const next = new URLSearchParams();
    for (const [k, v] of Object.entries(current)) if (v) next.set(k, v);
    setParams(next);
    setForm(null);
  }

  async function loadMore() {
    const loaded = (first.data?.rows.length ?? 0) + (extra?.rows.length ?? 0);
    try {
      const res = await apiGet<RunsResponse>("/api/runs", { ...query, offset: loaded });
      setMore({ key, rows: [...(extra?.rows ?? []), ...res.rows], hasMore: res.has_more });
      setMoreError(null);
    } catch (e) {
      setMoreError(toMessage(e));
    }
  }

  const rows = [...(first.data?.rows ?? []), ...(extra?.rows ?? [])];
  const hasMore = extra ? extra.hasMore : (first.data?.has_more ?? false);
  const labels = settings.batch.process_labels;
  return (
    <>
      <h1>実行履歴</h1>
      <div className="cond">
        対象の日付
        <DateInput label="対象の日付(開始)" value={current.from} onChange={(v) => update({ from: v })} />〜
        <DateInput label="対象の日付(終了)" value={current.to} onChange={(v) => update({ to: v })} />
        処理
        <select aria-label="処理" value={current.process} onChange={(e) => update({ process: e.target.value })}>
          <option value="">すべて</option>
          {PROCESS_ORDER.map((p) => (
            <option key={p} value={p}>
              {WHOLE_LABELS[p] ?? labels[p] ?? p}
            </option>
          ))}
        </select>
        状態
        <select aria-label="状態" value={current.status} onChange={(e) => update({ status: e.target.value })}>
          <option value="">すべて</option>
          {Object.entries(settings.web.run_status_labels).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <button type="button" className="btn" onClick={show}>
          表示
        </button>
      </div>
      {first.error && <ErrorMessage message={first.error} />}
      {first.loading && <p className="loading">読み込み中…</p>}
      {first.data && (
        <>
          <p className="info">
            {formatInteger(first.data.total)} 件(対象の日付 {first.data.date_from} 〜 {first.data.date_to})。開始日時の新しい順
          </p>
          <div className="wrap">
            <table>
              <thead>
                <tr>
                  <th>実行ID</th>
                  <th>起動のしかた</th>
                  <th className="fix">処理</th>
                  <th>対象の日付</th>
                  <th>状態</th>
                  <th>開始</th>
                  <th>終了</th>
                  <th>かかった時間</th>
                  <th>件数</th>
                  <th>エラーの銘柄</th>
                  <th>エラーの内容</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={11} className="c">
                      この条件の実行履歴はありません
                    </td>
                  </tr>
                )}
                {rows.map((r) => (
                  <tr key={r.run_id}>
                    <td className="n">{r.run_id}</td>
                    <td>{settings.web.batch_type_labels[r.batch_type] ?? r.batch_type}</td>
                    <td className="fix">
                      {WHOLE_LABELS[r.process_name] ?? labels[r.process_name] ?? r.process_name}
                      <br />
                      <span className="sub">{r.process_name}</span>
                    </td>
                    <td>{r.target_date}</td>
                    <td className="c">
                      <span className={statusBadgeClass(r.status)}>
                        {settings.web.run_status_labels[r.status] ?? r.status}
                      </span>
                    </td>
                    <td>{formatDateTime(r.started_at)}</td>
                    <td>{r.finished_at ? formatDateTime(r.finished_at) : ""}</td>
                    <td className="n">{r.status === "RUNNING" ? "実行中" : formatElapsed(r.elapsed_sec)}</td>
                    <td className="n">{r.record_count === null ? "" : formatInteger(r.record_count)}</td>
                    <td>{r.error_code ?? ""}</td>
                    <ErrorCell text={r.error_message} />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
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
