import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { RUNS, RUNS_MORE } from "../test/apiData";
import { renderApp, urlsOf } from "../test/renderApp";

describe("実行履歴", () => {
  it("対象の日付の期間・件数と、実行履歴の行を出す", async () => {
    renderApp("/runs", (p) => (p === "/api/runs" ? { body: RUNS } : undefined));
    expect(await screen.findByText("3 件(対象の日付 2026-07-16 〜 2026-07-22)。開始日時の新しい順")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "対象の日付(開始)" })).toHaveValue("2026-07-16");
    expect(screen.getByRole("textbox", { name: "対象の日付(終了)" })).toHaveValue("2026-07-22");
    const table = within(screen.getByRole("table"));
    expect(table.getByText("日次バッチ")).toBeInTheDocument();
    expect(table.getByText("単独で起動")).toBeInTheDocument();
    expect(table.getByText("通知")).toBeInTheDocument();
    expect(table.getByText("19秒")).toBeInTheDocument();
    expect(table.getByText("1時間23分")).toBeInTheDocument();
    expect(table.getByText("成功")).toHaveClass("b-ok");
    expect(table.getByText("失敗")).toHaveClass("b-ng");
    expect(table.getByText("2026-07-22 19:40")).toBeInTheDocument();
  });

  it("長いエラーの内容は40文字で切り、押すと全部を出す", async () => {
    const user = userEvent.setup();
    renderApp("/runs", (p) => (p === "/api/runs" ? { body: RUNS } : undefined));
    const full = RUNS.rows[1]!.error_message!;
    const btn = await screen.findByRole("button", { name: `${full.slice(0, 40)}…` });
    expect(btn.closest("td")).toHaveAttribute("title", full);
    await user.click(btn);
    expect(screen.getByRole("button", { name: full })).toHaveAttribute("aria-expanded", "true");
    await user.click(screen.getByRole("button", { name: full }));
    expect(screen.getByRole("button", { name: `${full.slice(0, 40)}…` })).toBeInTheDocument();
  });

  it("「もっと見る」で続きを読み、実行中の行は「実行中」と出す", async () => {
    const user = userEvent.setup();
    const calls = renderApp("/runs", (p, q) => {
      if (p !== "/api/runs") return undefined;
      return { body: q.get("offset") ? RUNS_MORE : RUNS };
    });
    await user.click(await screen.findByRole("button", { name: "もっと見る" }));
    const table = within(screen.getByRole("table"));
    expect(await table.findByText("週次バッチ")).toBeInTheDocument();
    expect(table.getByText("DAILY_BATCH")).toBeInTheDocument();
    expect(table.getAllByText("実行中")).toHaveLength(2);
    expect(table.getByText("短いエラー")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "もっと見る" })).toBeNull();
    expect(urlsOf(calls, "/api/runs")).toEqual(["/api/runs", "/api/runs?offset=2"]);
  });

  it("続きを読めなければ、エラーを出す", async () => {
    const user = userEvent.setup();
    renderApp("/runs", (p, q) => {
      if (p !== "/api/runs") return undefined;
      return q.get("offset") ? { status: 503, body: { detail: "DB に接続できません" } } : { body: RUNS };
    });
    await user.click(await screen.findByRole("button", { name: "もっと見る" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("DB に接続できません");
  });

  it("条件を変えて「表示」を押すと、その条件で読み直す。0件ならその旨を出す", async () => {
    const user = userEvent.setup();
    const calls = renderApp("/runs", (p, q) => {
      if (p !== "/api/runs") return undefined;
      return { body: q.get("status") ? { ...RUNS, rows: [], total: 0, has_more: false } : RUNS };
    });
    await screen.findByText(/3 件/);
    const from = screen.getByRole("textbox", { name: "対象の日付(開始)" });
    await user.clear(from);
    await user.type(from, "2026-07-01{Enter}");
    await user.selectOptions(screen.getByRole("combobox", { name: "処理" }), "PREDICT");
    await user.selectOptions(screen.getByRole("combobox", { name: "状態" }), "FAILED");
    await user.click(screen.getByRole("button", { name: "表示" }));
    expect(await screen.findByText("この条件の実行履歴はありません")).toBeInTheDocument();
    await waitFor(() =>
      expect(urlsOf(calls, "/api/runs").at(-1)).toBe(
        "/api/runs?from=2026-07-01&to=2026-07-22&process=PREDICT&status=FAILED",
      ),
    );
    expect(screen.getByRole("combobox", { name: "処理" })).toHaveValue("PREDICT");
  });

  it("読めなければエラーを出す", async () => {
    renderApp("/runs", (p) => (p === "/api/runs" ? { status: 400, body: { detail: "開始日が終了日より後になっています" } } : undefined));
    expect(await screen.findByRole("alert")).toHaveTextContent("開始日が終了日より後になっています");
  });
});
