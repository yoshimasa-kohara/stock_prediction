// 画面全体(設定値・状態・メニュー込み)を、指定した URL で表示するテストの部品
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router";

import App from "../App";
import SettingsProvider from "../context/SettingsProvider";
import StatusProvider from "../context/StatusProvider";
import { type FakeResponse, type FetchCall, SETTINGS, STATUS, mockApi } from "./fixtures";

/** 画面ごとの API の返り値を決める関数(undefined なら 404) */
export type Handler = (path: string, query: URLSearchParams, method: string, body: unknown) => FakeResponse | undefined;

/** fetch を差し替える。/api/settings と /api/status は決まった値を返し、それ以外は handler で決める */
function mockAll(handler: Handler): FetchCall[] {
  return mockApi((p, q, m, b) => {
    if (p === "/api/settings") return { body: SETTINGS };
    if (p === "/api/status") return { body: STATUS };
    return handler(p, q, m, b);
  });
}

/** 画面全体(App)を、指定した URL で表示する */
export function renderApp(path: string, handler: Handler = () => undefined): FetchCall[] {
  const calls = mockAll(handler);
  render(
    <MemoryRouter initialEntries={[path]}>
      <SettingsProvider>
        <App />
      </SettingsProvider>
    </MemoryRouter>,
  );
  return calls;
}

/** 呼ばれた API のうち、パスが合うものの URL */
export function urlsOf(calls: FetchCall[], path: string): string[] {
  return calls.filter((c) => c.url.split("?")[0] === path).map((c) => c.url);
}

/**
 * 1つの画面だけを表示する(上のメニューなし)。開いたときに読み込む画面(lazy。銘柄詳細、的中率)のテストで使う。
 * App を通すと、テストを並べて動かしたときに画面の読み込みが待ち時間を超えることがあるため(サーバーで失敗した)、
 * テストのファイルで画面を直接 import して、読み込みをテストの前に済ませる
 */
export function renderPage(
  path: string,
  routePath: string,
  page: ReactElement,
  handler: Handler = () => undefined,
): FetchCall[] {
  const calls = mockAll(handler);
  render(
    <MemoryRouter initialEntries={[path]}>
      <SettingsProvider>
        <StatusProvider>
          <Routes>
            <Route path={routePath} element={page} />
          </Routes>
        </StatusProvider>
      </SettingsProvider>
    </MemoryRouter>,
  );
  return calls;
}
