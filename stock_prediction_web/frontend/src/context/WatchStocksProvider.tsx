// 検討中銘柄に追加済みの銘柄(/api/watch-stocks/codes)を読み、画面全体に渡す。
// 画面を開いたとき、画面を移ったとき、追加・メモの編集・削除の後に読み直す。
// 読めなかったときは、追加済みの銘柄なし(全て ☆)として表示する(表の表示を止めない)
import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router";

import { apiGet } from "../api/client";
import type { WatchCodesResponse } from "../api/types";
import { WatchStocksContext } from "./contexts";

export default function WatchStocksProvider({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  const [memos, setMemos] = useState<ReadonlyMap<string, string>>(new Map());
  // 読み直しの合図(数が変わると読み直す)
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    const ctrl = new AbortController();
    apiGet<WatchCodesResponse>("/api/watch-stocks/codes", undefined, ctrl.signal)
      .then((res) => setMemos(new Map(res.items.map((i) => [i.code, i.memo]))))
      .catch(() => {
        // 読めなくても、前に読めた内容のまま表示を続ける
      });
    return () => ctrl.abort();
  }, [pathname, tick]);

  const value = useMemo(() => ({ memos, reload }), [memos, reload]);
  return <WatchStocksContext value={value}>{children}</WatchStocksContext>;
}
