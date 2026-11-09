// API を読む React のフック。query や deps が変わったら読み直す。前の読み込みは取り消す
import { useCallback, useEffect, useState } from "react";

import { type QueryValue, apiGet, toMessage } from "./client";

export interface ApiState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  /** すぐに読み直す */
  reload: () => void;
}

/**
 * GET の API を読む。path が null のときは読まない(条件がそろうまで待つとき)。
 * query はオブジェクトの中身で比べるので、毎回新しいオブジェクトを渡してよい
 */
export function useApi<T>(path: string | null, query?: Record<string, QueryValue>): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  const key = path === null ? null : `${path}?${JSON.stringify(query ?? {})}`;

  // 読む先が変わったら、前の結果を消して「読み込み中」にする
  const [prevKey, setPrevKey] = useState(key);
  if (prevKey !== key) {
    setPrevKey(key);
    setData(null);
    setError(null);
    setLoading(key !== null);
  }

  useEffect(() => {
    if (key === null || path === null) return;
    const ctrl = new AbortController();
    const q = JSON.parse(key.slice(key.indexOf("?") + 1)) as Record<string, QueryValue>;
    apiGet<T>(path, q, ctrl.signal)
      .then((d) => {
        setData(d);
        setError(null);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (ctrl.signal.aborted) return;
        setError(toMessage(e));
        setLoading(false);
      });
    return () => ctrl.abort();
  }, [key, path, tick]);

  return { data, error, loading, reload };
}
