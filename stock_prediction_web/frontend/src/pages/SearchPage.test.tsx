import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PRED_DATES, SCREEN, SCREEN_MORE } from "../test/apiData";
import type { FakeResponse } from "../test/fixtures";
import { renderApp, urlsOf } from "../test/renderApp";

function handler(screenRes: (q: URLSearchParams) => FakeResponse = () => ({ body: SCREEN })) {
  return (p: string, q: URLSearchParams) => {
    if (p === "/api/prediction-dates") return { body: PRED_DATES };
    if (p === "/api/screen") return screenRes(q);
    return undefined;
  };
}

describe("銘柄の検索", () => {
  it("検索結果の件数と、買い候補と同じ形の表を出す", async () => {
    renderApp("/search", handler());
    expect(await screen.findByText("3 件")).toBeInTheDocument();
    expect(screen.getByText(/予測した銘柄 3,961 件のうち/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "予測日" })).toHaveValue("2026-07-22");
    // 並べている列(20日後の市場比)に印
    const th = screen.getByRole("button", { name: "市場比 ▼" }).closest("th");
    expect(th).toHaveAttribute("aria-sort", "descending");
    expect(screen.getByRole("columnheader", { name: "買い候補の順位" })).toBeInTheDocument();
    for (const m of ["プライム", "スタンダード", "グロース"]) expect(screen.getByRole("checkbox", { name: m })).toBeChecked();
  });

  it("条件を入れて「検索」を押すと、URL と API に条件を入れる", async () => {
    const user = userEvent.setup();
    const calls = renderApp("/search", handler());
    await screen.findByText("3 件");
    await user.type(screen.getByRole("textbox", { name: "銘柄コード・銘柄名" }), "サンプル");
    await user.click(screen.getByRole("checkbox", { name: "グロース" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "33業種を選ぶ" }), "3050");
    await user.selectOptions(screen.getByRole("combobox", { name: "33業種を選ぶ" }), "3650");
    await user.click(screen.getByRole("button", { name: "電気機器を外す" }));
    await user.type(screen.getByRole("textbox", { name: "20日後の市場比" }), "60");
    await user.type(screen.getByRole("textbox", { name: "20日後の上昇確率" }), "55");
    await user.type(screen.getByRole("textbox", { name: "20日後の見込み" }), "2");
    await user.selectOptions(screen.getByRole("combobox", { name: "備考を選ぶ" }), "pbr");
    await user.click(screen.getByRole("checkbox", { name: "流動性注意を除く" }));
    await user.click(screen.getByRole("checkbox", { name: "赤字予想(業績注意)を除く" }));
    await user.click(screen.getByRole("checkbox", { name: "赤字予想(業績注意)を除く" }));
    await user.click(screen.getByRole("button", { name: "検索" }));
    await waitFor(() =>
      expect(decodeURIComponent(urlsOf(calls, "/api/screen").at(-1)!)).toBe(
        "/api/screen?q=サンプル&markets=プライム,スタンダード&sectors=3050&min_rel=60&min_up=55&min_ret=2&notes=pbr&exclude=low_liquidity",
      ),
    );
    // 選んだ条件は札で出す
    expect(screen.getByText("銀行業", { selector: ".badge" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "グロース" })).not.toBeChecked();
    // 外していた市場を全部戻すと、条件なし(全て)にする
    await user.click(screen.getByRole("checkbox", { name: "グロース" }));
    await user.click(screen.getByRole("checkbox", { name: "スタンダード" }));
    await user.click(screen.getByRole("button", { name: "検索" }));
    await waitFor(() => expect(decodeURIComponent(urlsOf(calls, "/api/screen").at(-1)!)).toContain("markets=プライム,グロース"));
    // 条件をクリア
    await user.click(screen.getByRole("button", { name: "条件をクリア" }));
    await waitFor(() => expect(urlsOf(calls, "/api/screen").at(-1)).toBe("/api/screen"));
    expect(screen.getByRole("textbox", { name: "銘柄コード・銘柄名" })).toHaveValue("");
  });

  it("銘柄コード・銘柄名の欄で Enter を押すと検索する。予測日を変えると、その日で検索する", async () => {
    const user = userEvent.setup();
    const calls = renderApp("/search", handler((q) => ({ body: { ...SCREEN, pred_date: q.get("date") ?? SCREEN.pred_date } })));
    await screen.findByText("3 件");
    await user.type(screen.getByRole("textbox", { name: "銘柄コード・銘柄名" }), "1111{Enter}");
    await waitFor(() => expect(urlsOf(calls, "/api/screen").at(-1)).toBe("/api/screen?q=1111"));
    await user.click(screen.getByRole("button", { name: "◀ 前の日" }));
    await waitFor(() => expect(urlsOf(calls, "/api/screen").at(-1)).toBe("/api/screen?date=2026-07-21&q=1111"));
    await user.click(screen.getByRole("button", { name: "条件をクリア" }));
    await waitFor(() => expect(urlsOf(calls, "/api/screen").at(-1)).toBe("/api/screen?date=2026-07-21"));
  });

  it("列の見出しを押すと並べ替える(同じ列は逆の順、文字の列は最初は小さい順)", async () => {
    const user = userEvent.setup();
    const calls = renderApp(
      "/search",
      handler((q) => ({ body: { ...SCREEN, sort: q.get("sort") ?? "rel_20", order: q.get("order") ?? "desc" } })),
    );
    await screen.findByText("3 件");
    await user.click(screen.getByRole("button", { name: "市場比 ▼" }));
    await waitFor(() => expect(urlsOf(calls, "/api/screen").at(-1)).toBe("/api/screen?sort=rel_20&order=asc"));
    await user.click(await screen.findByRole("button", { name: "市場比 ▲" }));
    await waitFor(() => expect(urlsOf(calls, "/api/screen").at(-1)).toBe("/api/screen?sort=rel_20&order=desc"));
    await user.click(screen.getByRole("button", { name: "銘柄名" }));
    await waitFor(() => expect(urlsOf(calls, "/api/screen").at(-1)).toBe("/api/screen?sort=name&order=asc"));
    await user.click(screen.getAllByRole("button", { name: "予測株価" })[0]!);
    await waitFor(() => expect(urlsOf(calls, "/api/screen").at(-1)).toBe("/api/screen?sort=pred_5&order=desc"));
  });

  it("「もっと見る」で続きを読む。読めなければエラーを出す", async () => {
    const user = userEvent.setup();
    let fail = false;
    const calls = renderApp(
      "/search",
      handler((q) => {
        if (!q.get("offset")) return { body: SCREEN };
        return fail ? { status: 503, body: { detail: "DB に接続できません" } } : { body: SCREEN_MORE };
      }),
    );
    await screen.findByText("3 件");
    fail = true;
    await user.click(screen.getByRole("button", { name: "もっと見る" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("DB に接続できません");
    fail = false;
    await user.click(screen.getByRole("button", { name: "もっと見る" }));
    expect(await screen.findByRole("link", { name: "サンプル電機A" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "もっと見る" })).toBeNull();
    expect(urlsOf(calls, "/api/screen").at(-1)).toBe("/api/screen?offset=2");
  });

  it("条件に合う銘柄がなければ、その旨を出す。エラーはそのまま出す", async () => {
    renderApp(
      "/search?min_rel=2000",
      handler((q) =>
        q.get("min_rel") === "2000"
          ? { status: 400, body: { detail: "市場比は -100〜1000 の数を入れてください" } }
          : { body: { ...SCREEN, rows: [], total: 0, has_more: false } },
      ),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("市場比は -100〜1000 の数を入れてください");
  });

  it("0件なら「条件に合う銘柄はありません」と出す", async () => {
    renderApp("/search?q=zzz", handler(() => ({ body: { ...SCREEN, rows: [], total: 0, has_more: false } })));
    const table = await screen.findByRole("table");
    expect(within(table).getByText("条件に合う銘柄はありません")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "銘柄コード・銘柄名" })).toHaveValue("zzz");
  });
});
