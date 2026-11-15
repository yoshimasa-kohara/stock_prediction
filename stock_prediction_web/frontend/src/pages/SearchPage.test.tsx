import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PRED_DATES, SCREEN, SCREEN_MORE, WATCH_CODES } from "../test/apiData";
import type { FakeResponse } from "../test/fixtures";
import { renderApp, urlsOf } from "../test/renderApp";

function handler(screenRes: (q: URLSearchParams) => FakeResponse = () => ({ body: SCREEN })) {
  return (p: string, q: URLSearchParams) => {
    if (p === "/api/prediction-dates") return { body: PRED_DATES };
    if (p === "/api/screen") return screenRes(q);
    return undefined;
  };
}

describe("銘柄検索", () => {
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

  it("銘柄名の左の「☆」で検討中銘柄に追加する。追加済みは「★」で、押すとメモを編集する", async () => {
    const user = userEvent.setup();
    const memos = new Map(WATCH_CODES.items.map((i) => [i.code, i.memo]));
    let fail = true;
    const calls = renderApp("/search", (p, q, m, b) => {
      if (p === "/api/watch-stocks/codes") return { body: { items: [...memos].map(([code, memo]) => ({ code, memo })) } };
      if (m === "PUT") {
        if (fail) return { status: 404, body: { detail: "銘柄が見つかりません" } };
        const code = p.split("/").at(-1)!;
        const created = !memos.has(code);
        memos.set(code, (b as { memo: string }).memo);
        return { body: { code, created, message: "" } };
      }
      return handler()(p, q);
    });
    await screen.findByText("3 件");
    // 追加済みの銘柄(11120)は ★、それ以外は ☆。銘柄名と同じセル(左に固定する列)の中に出す
    const added = await screen.findByRole("button", { name: "サンプル商事Aの検討中銘柄のメモを編集" });
    expect(added).toHaveTextContent("★");
    const star = screen.getByRole("button", { name: "サンプル銀行Aを検討中銘柄に追加" });
    expect(star).toHaveTextContent("☆");
    expect(star.closest("td")).toHaveClass("fix");
    expect(star.closest("td")).toContainElement(screen.getByRole("link", { name: "サンプル銀行A" }));

    // 未追加: 「検討中銘柄に追加」。メモは空でもよい。「キャンセル」と Escape では追加しない
    await user.click(star);
    let dialog = screen.getByRole("dialog", { name: "検討中銘柄に追加" });
    expect(within(dialog).getByText("1111 サンプル銀行A")).toBeInTheDocument();
    expect(within(dialog).getByRole("textbox", { name: "メモ" })).toHaveValue("");
    await user.click(within(dialog).getByRole("button", { name: "キャンセル" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(star);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(0);

    // 保存できなければ、ダイアログにエラーを出す。保存できたら閉じて ★ にする
    await user.click(star);
    dialog = screen.getByRole("dialog", { name: "検討中銘柄に追加" });
    await user.type(within(dialog).getByRole("textbox", { name: "メモ" }), "押し目を待つ{Enter}2行目");
    await user.click(within(dialog).getByRole("button", { name: "追加" }));
    expect(await within(dialog).findByText("銘柄が見つかりません")).toBeInTheDocument();
    fail = false;
    await user.click(within(dialog).getByRole("button", { name: "追加" }));
    const edit = await screen.findByRole("button", { name: "サンプル銀行Aの検討中銘柄のメモを編集" });
    expect(edit).toHaveTextContent("★");
    expect(edit).toHaveClass("on");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls.filter((c) => c.method === "PUT").at(-1)).toEqual({
      url: "/api/watch-stocks/11110",
      method: "PUT",
      body: { memo: "押し目を待つ\n2行目" },
    });
    // 表の API(/api/screen)は読み直さない(☆・★ は /api/watch-stocks/codes で決める)
    expect(urlsOf(calls, "/api/screen")).toHaveLength(1);

    // 追加済み: 「検討中銘柄のメモを編集」。入力欄に登録済みのメモを入れておく
    await user.click(edit);
    dialog = screen.getByRole("dialog", { name: "検討中銘柄のメモを編集" });
    const box = within(dialog).getByRole("textbox", { name: "メモ" });
    expect(box).toHaveValue("押し目を待つ\n2行目");
    await user.clear(box);
    await user.click(within(dialog).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(calls.filter((c) => c.method === "PUT").at(-1)!.body).toEqual({ memo: "" });
    expect(screen.getByRole("button", { name: "サンプル銀行Aの検討中銘柄のメモを編集" })).toBeInTheDocument();
  });
});
