import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";

import App from "./App";
import SettingsProvider from "./context/SettingsProvider";
import { SETTINGS, STATUS, mockFetch } from "./test/fixtures";

function renderApp(path = "/") {
  render(
    <MemoryRouter initialEntries={[path]}>
      <SettingsProvider>
        <App />
      </SettingsProvider>
    </MemoryRouter>,
  );
}

describe("App(画面全体)", () => {
  it("設定値を読むまでは「読み込み中」、読めたらメニューと画面を出す", async () => {
    mockFetch({ "/api/settings": { body: SETTINGS }, "/api/status": { body: STATUS } });
    renderApp("/");
    expect(screen.getByText("読み込み中…")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "トップ" })).toBeInTheDocument();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it.each([
    ["/runs", "実行履歴"],
    ["/candidates", "買い候補"],
    ["/candidates/results", "買い候補と実際の株価"],
    ["/search", "銘柄の検索"],
    ["/holdings", "保有銘柄"],
    ["/accuracy", "的中率"],
  ])("%s は「%s」の画面を出す", async (path, title) => {
    mockFetch({ "/api/settings": { body: SETTINGS }, "/api/status": { body: STATUS } });
    renderApp(path);
    expect(await screen.findByRole("heading", { level: 1, name: title })).toBeInTheDocument();
  });

  it("知らない URL は「画面が見つかりません」", async () => {
    mockFetch({ "/api/settings": { body: SETTINGS }, "/api/status": { body: STATUS } });
    renderApp("/nothing");
    expect(await screen.findByRole("heading", { name: "画面が見つかりません" })).toBeInTheDocument();
  });

  it("設定値を読めなければ、赤い枠でエラーを出す", async () => {
    mockFetch({ "/api/settings": { status: 503, body: { detail: "DB に接続できません" } } });
    renderApp("/");
    expect(await screen.findByRole("alert")).toHaveTextContent("画面の設定を読めませんでした。DB に接続できません");
  });

  it("バッチが実行中なら、全画面の上に表示する", async () => {
    mockFetch({
      "/api/settings": { body: SETTINGS },
      "/api/status": {
        body: {
          ...STATUS,
          running: [
            { run_id: 9, batch_type: "DAILY", process_name: "DAILY_BATCH", target_date: "2026-07-22", started_at: "2026-07-22T19:00:00+09:00" },
          ],
        },
      },
    });
    renderApp("/holdings");
    expect(await screen.findByRole("status")).toHaveTextContent("バッチを実行中です(日次バッチ 19:00 開始)");
  });

  it("状態を読めなければ、画面の上に赤い枠でエラーを出す(画面は表示する)", async () => {
    mockFetch({ "/api/settings": { body: SETTINGS }, "/api/status": { status: 503, body: { detail: "DB に接続できません" } } });
    renderApp("/runs");
    await waitFor(() => expect(screen.getAllByRole("alert")[0]).toHaveTextContent("DB に接続できません"));
    expect(screen.getByRole("heading", { name: "実行履歴" })).toBeInTheDocument();
  });

  it("画面を移ると、状態を読み直す", async () => {
    const calls = mockFetch({ "/api/settings": { body: SETTINGS }, "/api/status": { body: STATUS } });
    const user = userEvent.setup();
    renderApp("/");
    await screen.findByRole("heading", { name: "トップ" });
    await waitFor(() => expect(calls.filter((u) => u === "/api/status")).toHaveLength(1));
    await user.click(screen.getByRole("link", { name: "実行履歴" }));
    await waitFor(() => expect(calls.filter((u) => u === "/api/status")).toHaveLength(2));
  });

  it("設定の間隔ごとに状態を読み直し、実行中の表示を更新する", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let running = false;
    const calls = mockFetch({
      "/api/settings": { body: SETTINGS },
      "/api/status": () => ({
        body: {
          ...STATUS,
          running: running
            ? [{ run_id: 1, batch_type: "WEEKLY", process_name: "WEEKLY_BATCH", target_date: "2026-07-25", started_at: "2026-07-25T19:00:00+09:00" }]
            : [],
        },
      }),
    });
    renderApp("/");
    await screen.findByRole("heading", { name: "トップ" });
    await waitFor(() => expect(calls.filter((u) => u === "/api/status")).toHaveLength(1));
    expect(screen.queryByRole("status")).toBeNull();
    running = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SETTINGS.web.status_refresh_sec * 1000);
    });
    expect(await screen.findByRole("status")).toHaveTextContent("週次バッチ 19:00 開始");
  });
});
