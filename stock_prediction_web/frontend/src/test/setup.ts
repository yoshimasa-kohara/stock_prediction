// テストの共通の準備(Vitest)
import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach, vi } from "vitest";

/**
 * findBy・waitFor で待つ最大の時間(ミリ秒)。初期値の1秒では、テストを並べて動かしたときに
 * 最初の画面の表示(部品の読み込み)が間に合わないことがあるため(サーバーで的中率のテストが失敗した)
 */
export const ASYNC_TIMEOUT_MS = 5000;
configure({ asyncUtilTimeout: ASYNC_TIMEOUT_MS });

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

// jsdom には scrollIntoView がないので、何もしない関数を置く(買い候補の自動スクロール用。テストごとにモックで置き換えてよい)
Element.prototype.scrollIntoView = () => {};
