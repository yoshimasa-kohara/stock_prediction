// 画面全体で共有する値(設定値と、バッチの状態)
import { createContext, useContext } from "react";

import type { Settings, Status } from "../api/types";

/** 設定値(/api/settings)。SettingsProvider の中では必ず値がある */
export const SettingsContext = createContext<Settings | null>(null);

/** バッチの状態(/api/status) */
export interface StatusState {
  /** 最後に読めた状態。まだ読めていなければ null */
  status: Status | null;
  /** 最後に読んだときのエラー。読めたら null */
  error: string | null;
  /** すぐに読み直す */
  reload: () => void;
}

export const StatusContext = createContext<StatusState>({ status: null, error: null, reload: () => {} });

/** 設定値を使う(SettingsProvider の中で呼ぶ) */
export function useSettings(): Settings {
  const settings = useContext(SettingsContext);
  if (!settings) throw new Error("useSettings は SettingsProvider の中で使ってください");
  return settings;
}

/** バッチの状態を使う */
export function useStatus(): StatusState {
  return useContext(StatusContext);
}
