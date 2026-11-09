// 上のメニューの銘柄の検索。銘柄コードか銘柄名の一部を入れると候補を出し、選ぶと銘柄詳細へ移る
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";

import { apiGet } from "../api/client";
import type { StockItem } from "../api/types";
import { displayCode } from "../lib/format";

/** 入力してから検索するまで待つ時間(ミリ秒)。打っている途中で何度も API を呼ばないため */
export const SEARCH_DELAY_MS = 300;

export default function HeaderSearch({ onSelect }: { onSelect?: () => void }) {
  const navigate = useNavigate();
  const [text, setText] = useState("");
  const [items, setItems] = useState<StockItem[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const q = text.trim();
    if (!q) return;
    const ctrl = new AbortController();
    const timer = window.setTimeout(() => {
      apiGet<{ items: StockItem[] }>("/api/stocks/search", { q }, ctrl.signal)
        .then((r) => {
          setItems(r.items);
          setOpen(true);
        })
        .catch(() => setItems([]));
    }, SEARCH_DELAY_MS);
    return () => {
      window.clearTimeout(timer);
      ctrl.abort();
    };
  }, [text]);

  function go(item: StockItem) {
    setText("");
    setItems([]);
    setOpen(false);
    onSelect?.();
    void navigate(`/stocks/${encodeURIComponent(item.code)}`);
  }

  return (
    <span className="search">
      <input
        placeholder="銘柄コード・銘柄名で検索"
        aria-label="銘柄の検索"
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          if (!e.target.value.trim()) setItems([]);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 200)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && items[0]) go(items[0]);
          if (e.key === "Escape") setOpen(false);
        }}
      />
      {open && text.trim() && (
        <div className="suggest" role="listbox" aria-label="銘柄の候補">
          {items.length === 0 && <div className="none">見つかりません</div>}
          {items.map((i) => (
            <button type="button" role="option" aria-selected={false} key={i.code} onMouseDown={(e) => e.preventDefault()} onClick={() => go(i)}>
              {displayCode(i.code)} {i.name}
              <span className="sub">({i.market})</span>
            </button>
          ))}
        </div>
      )}
    </span>
  );
}
