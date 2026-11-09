// バッチの状態(/api/status)を読み、画面全体に渡す。
// 画面を開いたとき、画面を移ったとき、設定の間隔(status_refresh_sec)ごとに読み直す
import { type ReactNode, useCallback, useEffect, useState } from "react";
import { useLocation } from "react-router";

import { apiGet, toMessage } from "../api/client";
import type { Status } from "../api/types";
import { StatusContext, useSettings } from "./contexts";

export default function StatusProvider({ children }: { children: ReactNode }) {
  const intervalSec = useSettings().web.status_refresh_sec;
  const { pathname } = useLocation();
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 読み直しの合図(数が変わると読み直す)
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    const ctrl = new AbortController();
    apiGet<Status>("/api/status", undefined, ctrl.signal)
      .then((s) => {
        setStatus(s);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!ctrl.signal.aborted) setError(toMessage(e));
      });
    // 次の読み直しを予約する
    const timer = window.setTimeout(reload, intervalSec * 1000);
    return () => {
      ctrl.abort();
      window.clearTimeout(timer);
    };
  }, [pathname, tick, intervalSec, reload]);

  return <StatusContext value={{ status, error, reload }}>{children}</StatusContext>;
}
