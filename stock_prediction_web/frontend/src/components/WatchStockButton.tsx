// 検討中銘柄に追加するボタン(☆ = 未追加、★ = 追加済み)と、メモのダイアログ。
// 買い候補、買い候補と実際の株価、銘柄検索、ランキング、的中率(銘柄別)、銘柄詳細で使う。検討中銘柄の画面の「編集」も同じダイアログを使う
import { useId, useState } from "react";
import { createPortal } from "react-dom";

import { apiSend, toMessage } from "../api/client";
import type { WatchSaved } from "../api/types";
import { useSettings, useWatchStocks } from "../context/contexts";
import { displayCode } from "../lib/format";
import Modal from "./Modal";

interface DialogProps {
  code: string;
  name: string;
  /** 登録済みのメモ。まだ追加していない銘柄は null */
  memo: string | null;
  onClose: () => void;
  /** 保存できたら呼ぶ(メッセージを渡す) */
  onSaved: (message: string) => void;
}

/** メモを入れて、検討中銘柄に追加する(追加済みならメモを直す)ダイアログ */
export function WatchStockDialog({ code, name, memo, onClose, onSaved }: DialogProps) {
  const max = useSettings().web.watch_stock_memo_max_length;
  const added = memo !== null;
  const [text, setText] = useState(memo ?? "");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const id = useId();

  async function save() {
    setSaving(true);
    try {
      const res = await apiSend<WatchSaved>("PUT", `/api/watch-stocks/${encodeURIComponent(code)}`, { memo: text });
      onSaved(res.message);
    } catch (e) {
      setError(toMessage(e));
      setSaving(false);
    }
  }

  return (
    <Modal
      title={added ? "検討中銘柄のメモを編集" : "検討中銘柄に追加"}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn sub" onClick={onClose}>
            キャンセル
          </button>
          <button type="button" className="btn" onClick={() => void save()} disabled={saving}>
            {added ? "保存" : "追加"}
          </button>
        </>
      }
    >
      <div className="form">
        <span>銘柄</span>
        <span className="fixed">
          {displayCode(code)} {name}
        </span>
        <label htmlFor={id}>メモ</label>
        <textarea id={id} rows={5} maxLength={max} value={text} onChange={(e) => setText(e.target.value)} />
        <span />
        <span className="count">
          {text.length} / {max} 文字(空でもよい)
        </span>
      </div>
      {error && <p className="err">{error}</p>}
    </Modal>
  );
}

/** ☆・★ のボタン。押すとダイアログを開き、保存したら ☆・★ を新しくする */
export default function WatchStockButton({ code, name }: { code: string; name: string }) {
  const { memos, reload } = useWatchStocks();
  const [open, setOpen] = useState(false);
  const memo = memos.get(code) ?? null;
  const added = memo !== null;
  const label = `${name || displayCode(code)}${added ? "の検討中銘柄のメモを編集" : "を検討中銘柄に追加"}`;
  return (
    <>
      <button
        type="button"
        className={added ? "watchbtn on" : "watchbtn"}
        aria-label={label}
        title={added ? "検討中銘柄に追加済み(押すとメモを編集)" : "検討中銘柄に追加"}
        onClick={() => setOpen(true)}
      >
        {added ? "★" : "☆"}
      </button>
      {/* 表のセル(左に固定する列)の中に置くと、上のメニューなどの下に隠れるので、画面の一番外に出す */}
      {open &&
        createPortal(
          <WatchStockDialog
            code={code}
            name={name}
            memo={memo}
            onClose={() => setOpen(false)}
            onSaved={() => {
              setOpen(false);
              reload();
            }}
          />,
          document.body,
        )}
    </>
  );
}
