// 画面を開いたときに設定値(/api/settings)を1回だけ読み、画面全体に渡す
import { type ReactNode, useEffect, useState } from "react";

import { apiGet, toMessage } from "../api/client";
import type { Settings } from "../api/types";
import ErrorMessage from "../components/ErrorMessage";
import { SettingsContext } from "./contexts";

export default function SettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    apiGet<Settings>("/api/settings", undefined, ctrl.signal)
      .then(setSettings)
      .catch((e: unknown) => {
        if (!ctrl.signal.aborted) setError(toMessage(e));
      });
    return () => ctrl.abort();
  }, []);

  if (error) {
    return (
      <main>
        <ErrorMessage message={`画面の設定を読めませんでした。${error}`} />
      </main>
    );
  }
  if (!settings) return <main className="loading">読み込み中…</main>;
  return <SettingsContext value={settings}>{children}</SettingsContext>;
}
