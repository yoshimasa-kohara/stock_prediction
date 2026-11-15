// 検討中銘柄。買うか迷っている銘柄を、追加した日の予測と最新の予測を並べて見る。
// 1銘柄2行(上: 追加した日、下: 最新)。メモの編集と削除もこの画面で行う
import { useState } from "react";

import { apiSend, toMessage } from "../api/client";
import type { WatchPoint, WatchSaved, WatchStockRow, WatchStocksResponse } from "../api/types";
import { useApi } from "../api/useApi";
import ErrorMessage from "../components/ErrorMessage";
import Modal from "../components/Modal";
import NotesText from "../components/NotesText";
import StockName from "../components/StockName";
import { WatchStockDialog } from "../components/WatchStockButton";
import { useSettings, useWatchStocks } from "../context/contexts";
import { displayCode, formatDateTime, formatPercent, formatPrice, formatSignedPercent } from "../lib/format";
import { usePageTitle } from "../lib/usePageTitle";

/** 予測日数ごとの3つの列(市場比 → 上昇確率 → 予測株価。買い候補の表と同じ順) */
const COLUMNS: { key: "rel_prob" | "up_prob" | "pred_price"; label: string }[] = [
  { key: "rel_prob", label: "市場比" },
  { key: "up_prob", label: "上昇確率" },
  { key: "pred_price", label: "予測株価" },
];

/** 上がった・下がったの色のクラス(赤 up・青 dn。同じ・値がないときは通常の色) */
function colorClass(diff: number | null): string {
  if (diff === null || diff === 0) return "";
  return diff > 0 ? " up" : " dn";
}

/**
 * 最新の値を、追加した日の値と比べる(表に出す桁で比べる。どちらかがなければ null)。
 * scale は表に出す桁にするための倍率(株価は小数1桁 → 10、率は % の小数1桁 → 1000)
 */
function diffOf(now: number | null | undefined, base: number | null | undefined, scale: number): number | null {
  if (now === null || now === undefined || base === null || base === undefined) return null;
  return Math.round(now * scale) - Math.round(base * scale);
}

/** 行の区分(追加した日・最新)の文字 */
function pointLabel(name: string, point: WatchPoint): string {
  return `${name}(${point.pred_date ? `予測日 ${point.pred_date}` : "予測なし"})`;
}

/** 予測日の株価と、予測日数ごとの値のセル。base を渡すと、その値と比べて色を付ける(最新の行) */
function valueCells(point: WatchPoint, horizons: number[], main: number, base?: WatchPoint) {
  return [
    <td key="close" className={`n${colorClass(base ? diffOf(point.close, base.close, 10) : null)}`}>
      {formatPrice(point.close)}
    </td>,
    ...horizons.flatMap((h) => {
      const v = point.horizons.find((x) => x.horizon === h);
      const b = base?.horizons.find((x) => x.horizon === h);
      const bold = h === main ? "n b" : "n";
      return COLUMNS.map((c) => {
        const price = c.key === "pred_price";
        const diff = base ? diffOf(v?.[c.key], b?.[c.key], price ? 10 : 1000) : null;
        return (
          <td key={`${h}-${c.key}`} className={`${bold}${colorClass(diff)}`}>
            {price ? formatPrice(v?.[c.key]) : formatPercent(v?.[c.key])}
          </td>
        );
      });
    }),
  ];
}

type Dialog = { kind: "edit"; row: WatchStockRow } | { kind: "delete"; row: WatchStockRow };

/** 削除の確認のダイアログ */
function DeleteDialog({
  row,
  onClose,
  onDeleted,
}: {
  row: WatchStockRow;
  onClose: () => void;
  onDeleted: (message: string) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function remove() {
    setSaving(true);
    try {
      const res = await apiSend<WatchSaved>("DELETE", `/api/watch-stocks/${encodeURIComponent(row.code)}`);
      onDeleted(res.message);
    } catch (e) {
      setError(toMessage(e));
      setSaving(false);
    }
  }

  return (
    <Modal
      title="検討中銘柄の削除"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn sub" onClick={onClose}>
            キャンセル
          </button>
          <button type="button" className="btn" onClick={() => void remove()} disabled={saving}>
            削除
          </button>
        </>
      }
    >
      <p>
        {row.name || displayCode(row.code)}({displayCode(row.code)})を検討中銘柄から削除しますか?
      </p>
      {error && <p className="err">{error}</p>}
    </Modal>
  );
}

export default function WatchStocksPage() {
  usePageTitle("検討中銘柄");
  const settings = useSettings();
  const { main_horizon: main, predict_horizons: horizons } = settings.batch;
  const res = useApi<WatchStocksResponse>("/api/watch-stocks");
  const watch = useWatchStocks();
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const rows = res.data?.rows ?? [];

  /** 保存・削除できたら、ダイアログを閉じて一覧と ☆・★ を読み直す */
  function done(text: string) {
    setDialog(null);
    setMessage(text);
    res.reload();
    watch.reload();
  }

  return (
    <>
      <h1>検討中銘柄</h1>
      <p className="info">
        買うか迷っている銘柄を残しておく所。買い候補・買い候補と実際の株価・銘柄検索・銘柄詳細の「☆」で追加する。
        銘柄ごとに、上の行に追加した日の予測、下の行に最新の予測を出す。主となる日数: {main}日後(緑の見出し)
        <br />
        下の行(最新)の予測日の株価・市場比・上昇確率・予測株価: <span className="up">赤字 = 追加した日より上がった</span>、
        <span className="dn">青字 = 下がった</span>。値上がり率 = 最新の予測日の株価 ÷ 追加した日の予測日の株価 − 1。予測がない項目は「-」
      </p>
      {message && (
        <div className="okbox" role="status">
          {message}
        </div>
      )}
      {res.error && <ErrorMessage message={res.error} />}
      {res.loading && !res.data && <p className="loading">読み込み中…</p>}
      {res.data && rows.length === 0 && <p className="info">検討中銘柄はありません</p>}
      {rows.length > 0 && (
        <div className="wrap">
          <table className="watch">
            <thead>
              <tr>
                <th rowSpan={2}>コード</th>
                <th rowSpan={2} className="fix">
                  銘柄名
                </th>
                <th rowSpan={2}>市場</th>
                <th rowSpan={2}>業種</th>
                <th rowSpan={2}>区分</th>
                <th rowSpan={2}>予測日の株価</th>
                {horizons.map((h) => (
                  <th key={h} colSpan={COLUMNS.length} className={h === main ? "main" : undefined}>
                    {h}日後
                  </th>
                ))}
                <th rowSpan={2}>値上がり率</th>
                <th rowSpan={2}>備考</th>
                <th rowSpan={2}>メモ</th>
                <th rowSpan={2}>追加した日</th>
                <th rowSpan={2}>操作</th>
              </tr>
              <tr>
                {horizons.flatMap((h) =>
                  COLUMNS.map((c) => (
                    <th key={`${h}-${c.key}`} className={h === main ? "main" : undefined}>
                      {c.label}
                    </th>
                  )),
                )}
              </tr>
            </thead>
            {/* 1銘柄を1つの tbody にする(縞を銘柄ごとに付けるため) */}
            {rows.map((r) => (
              <tbody key={r.code}>
                <tr>
                  <td rowSpan={2}>{displayCode(r.code)}</td>
                  <td rowSpan={2} className="fix">
                    <StockName code={r.code} name={r.name} />
                  </td>
                  <td rowSpan={2}>{r.market}</td>
                  <td rowSpan={2}>{r.sector33}</td>
                  <td>{pointLabel("追加した日", r.picked)}</td>
                  {valueCells(r.picked, horizons, main)}
                  <td className="n" />
                  <td>
                    <NotesText notes={r.picked.notes} />
                  </td>
                  <td rowSpan={2} className="memo">
                    {r.memo && <div className="memotext">{r.memo}</div>}
                    <button
                      type="button"
                      className="btn sm sub"
                      aria-label={`${r.name || displayCode(r.code)}のメモを編集`}
                      onClick={() => setDialog({ kind: "edit", row: r })}
                    >
                      編集
                    </button>
                  </td>
                  <td rowSpan={2}>{formatDateTime(r.created_at).slice(0, 10)}</td>
                  <td rowSpan={2}>
                    <button
                      type="button"
                      className="btn sm sub"
                      aria-label={`${r.name || displayCode(r.code)}を削除`}
                      onClick={() => setDialog({ kind: "delete", row: r })}
                    >
                      削除
                    </button>
                  </td>
                </tr>
                <tr>
                  <td>{pointLabel("最新", r.latest)}</td>
                  {valueCells(r.latest, horizons, main, r.picked)}
                  <td className={`n${colorClass(r.change_rate)}`}>{formatSignedPercent(r.change_rate)}</td>
                  <td>
                    <NotesText notes={r.latest.notes} />
                  </td>
                </tr>
              </tbody>
            ))}
          </table>
        </div>
      )}
      {dialog?.kind === "edit" && (
        <WatchStockDialog
          code={dialog.row.code}
          name={dialog.row.name}
          memo={dialog.row.memo}
          onClose={() => setDialog(null)}
          onSaved={done}
        />
      )}
      {dialog?.kind === "delete" && <DeleteDialog row={dialog.row} onClose={() => setDialog(null)} onDeleted={done} />}
    </>
  );
}
