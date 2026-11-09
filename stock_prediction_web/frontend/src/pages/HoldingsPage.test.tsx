import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { HOLDINGS } from "../test/apiData";
import type { FakeResponse } from "../test/fixtures";
import { renderApp } from "../test/renderApp";

const STOCKS = [
  { code: "11340", name: "サンプル工業B", market: "プライム", sector33: "機械" },
  { code: "11345", name: "サンプル工業B(優先株式)", market: "プライム", sector33: "機械" },
  { code: "13050", name: "サンプルETF", market: "その他", sector33: "その他" },
];

type Send = (method: string, path: string, body: unknown) => FakeResponse | undefined;

function handler(send: Send = () => undefined) {
  return (p: string, q: URLSearchParams, method: string, body: unknown) => {
    if (method !== "GET") return send(method, p, body);
    if (p === "/api/holdings") return { body: HOLDINGS };
    if (p === "/api/stocks/search") {
      const text = (q.get("q") ?? "").toUpperCase();
      return { body: { items: STOCKS.filter((s) => s.code.startsWith(text)) } };
    }
    return undefined;
  };
}

function dialog() {
  return within(screen.getByRole("dialog"));
}

describe("保有銘柄", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-07-23T10:00:00+09:00"));
  });

  it("合計のカードと、銘柄ごとの表(買い増しは合計の行)を出し、▶ で購入ごとの行を開く", async () => {
    const user = userEvent.setup();
    renderApp("/holdings", handler());
    expect(await screen.findByText("判定の日: 2026-07-22(水)")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "保有中(2)" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("996,750")).toBeInTheDocument();
    expect(screen.getByText("-93,850(-9.4%)")).toHaveClass("dn");
    expect(screen.getByText("2回に分けて購入")).toBeInTheDocument();
    expect(screen.getByText("売却(下降予測、市場の中央値も下回る予測)")).toHaveClass("jr");
    expect(screen.getByText("短期ゴールデンクロス発生")).toBeInTheDocument();
    expect(screen.queryByText("└ 1回目の購入")).toBeNull();
    await user.click(screen.getByRole("button", { name: "サンプル工業Bの購入ごとの行を開く" }));
    expect(screen.getByText("└ 1回目の購入")).toBeInTheDocument();
    expect(screen.getByText("└ 2回目の購入")).toBeInTheDocument();
    expect(screen.getByText("+2,470")).toHaveClass("up");
    await user.click(screen.getByRole("button", { name: "サンプル工業Bの購入ごとの行を閉じる" }));
    expect(screen.queryByText("└ 1回目の購入")).toBeNull();
  });

  it("売却済みのタブに、売った銘柄と、買値・売値・損益の合計を出す", async () => {
    const user = userEvent.setup();
    renderApp("/holdings", handler());
    await user.click(await screen.findByRole("tab", { name: "売却済み(1)" }));
    // 合計のカードは 買値の合計 → 売値の合計 → 損益の合計 の順
    const labels = Array.from(document.querySelectorAll(".card .lbl")).map((e) => e.textContent);
    expect(labels).toEqual(["買値の合計", "売値の合計", "売却した銘柄の損益の合計"]);
    expect(screen.getByText("152,000")).toBeInTheDocument();
    expect(screen.getByText("168,800")).toBeInTheDocument();
    expect(screen.getByText("+16,800(+11.1%)")).toBeInTheDocument();
    // 表の行の損益
    expect(screen.getByText("+16,800")).toBeInTheDocument();
    expect(screen.getByText("59日")).toBeInTheDocument();
    expect(screen.getByText("1,688.0")).toBeInTheDocument();
    expect(screen.queryByText(/判定の日/)).toBeNull();
  });

  it("保有銘柄・売却済みがなければ、その旨を出す。予測がなければ判定の日にその旨を出す", async () => {
    const user = userEvent.setup();
    renderApp("/holdings", (p) =>
      p === "/api/holdings" ? { body: { ...HOLDINGS, judge_date: null, groups: [], sold: [], sold_count: 0 } } : undefined,
    );
    expect(await screen.findByText("判定の日: 予測がまだありません")).toBeInTheDocument();
    expect(screen.getByText(/保有銘柄はありません。/)).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "売却済み(0)" }));
    expect(screen.getByText("売却済みの銘柄はありません")).toBeInTheDocument();
  });

  it("売却済みの行がなければ、合計は保有中のタブと同じく 0 と「-」を出す", async () => {
    const user = userEvent.setup();
    const empty = { sold: [], sold_count: 0, sold_buy_amount: 0, sold_sell_amount: 0, sold_pnl_amount: 0, sold_pnl_rate: null };
    renderApp("/holdings", (p) => (p === "/api/holdings" ? { body: { ...HOLDINGS, ...empty } } : undefined));
    await user.click(await screen.findByRole("tab", { name: "売却済み(0)" }));
    const values = Array.from(document.querySelectorAll(".card .val")).map((e) => e.textContent);
    expect(values).toEqual(["0", "0", "0(-)"]);
  });

  it("読めなければエラーを出す", async () => {
    renderApp("/holdings", () => ({ status: 503, body: { detail: "DB に接続できません" } }));
    expect(await screen.findByRole("alert")).toHaveTextContent("DB に接続できません");
  });

  it("銘柄を登録する(候補が複数なら選ぶ)。保存できたらメッセージを出して読み直す", async () => {
    const user = userEvent.setup();
    const sent: unknown[] = [];
    const calls = renderApp(
      "/holdings",
      handler((method, p, body) => {
        sent.push([method, p, body]);
        return { body: { holding_id: 20, sold_holding_id: null, message: "登録しました" } };
      }),
    );
    await user.click(await screen.findByRole("button", { name: "＋ 銘柄を登録" }));
    expect(dialog().getByRole("textbox", { name: "購入日" })).toHaveValue("2026-07-23");
    // 銘柄を選ぶ前に登録すると止める
    await user.click(dialog().getByRole("button", { name: "登録する" }));
    expect(dialog().getByText("銘柄コードを入れて、銘柄を選んでください")).toBeInTheDocument();
    // 見つからない
    await user.type(dialog().getByRole("textbox", { name: "銘柄コード" }), "9999");
    await user.tab();
    expect(await dialog().findByText("銘柄が見つかりません")).toBeInTheDocument();
    // 候補が複数
    const code = dialog().getByRole("textbox", { name: "銘柄コード" });
    await user.clear(code);
    await user.type(code, "1134");
    await user.tab();
    expect(await dialog().findByText("「1134」の候補(2件)。選んでください")).toBeInTheDocument();
    await user.click(dialog().getByRole("option", { name: "1134 サンプル工業B(プライム)" }));
    expect(code).toHaveValue("1134");
    expect(dialog().getByText("サンプル工業B")).toBeInTheDocument();
    // 入力のチェック
    await user.type(dialog().getByRole("textbox", { name: "買値(1株)" }), "0");
    await user.click(dialog().getByRole("button", { name: "登録する" }));
    expect(dialog().getByText("買値は0より大きい値を入れてください")).toBeInTheDocument();
    await user.clear(dialog().getByRole("textbox", { name: "買値(1株)" }));
    await user.type(dialog().getByRole("textbox", { name: "買値(1株)" }), "1,234.5");
    await user.type(dialog().getByRole("textbox", { name: "買い株数" }), "1.5");
    await user.click(dialog().getByRole("button", { name: "登録する" }));
    expect(dialog().getByText("買い株数は1以上の整数を入れてください")).toBeInTheDocument();
    await user.clear(dialog().getByRole("textbox", { name: "買い株数" }));
    await user.type(dialog().getByRole("textbox", { name: "買い株数" }), "100");
    const buy = dialog().getByRole("textbox", { name: "購入日" });
    await user.clear(buy);
    await user.type(buy, "2026-07-24{Enter}");
    await user.click(dialog().getByRole("button", { name: "登録する" }));
    expect(dialog().getByText("購入日は今日以前の日付を入れてください")).toBeInTheDocument();
    await user.clear(buy);
    await user.type(buy, "2026-07-01{Enter}");
    await user.type(dialog().getByRole("textbox", { name: "メモ" }), " 3回目 ");
    await user.click(dialog().getByRole("button", { name: "登録する" }));
    expect(await screen.findByRole("status")).toHaveTextContent("登録しました");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(sent).toEqual([
      ["POST", "/api/holdings", { code: "11340", buy_price: 1234.5, quantity: 100, buy_date: "2026-07-01", note: "3回目" }],
    ]);
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/holdings" && c.method === "GET")).toHaveLength(2));
  });

  it("候補が1つなら自動で選ぶ。API のエラーはダイアログに出す。「やめる」で閉じる", async () => {
    const user = userEvent.setup();
    renderApp(
      "/holdings",
      handler(() => ({ status: 400, body: { detail: "銘柄が見つかりません(13050)" } })),
    );
    await user.click(await screen.findByRole("button", { name: "＋ 銘柄を登録" }));
    await user.type(dialog().getByRole("textbox", { name: "銘柄コード" }), "1305");
    await user.tab();
    expect(await dialog().findByText("サンプルETF")).toBeInTheDocument();
    await user.type(dialog().getByRole("textbox", { name: "買値(1株)" }), "2500");
    await user.type(dialog().getByRole("textbox", { name: "買い株数" }), "10");
    await user.click(dialog().getByRole("button", { name: "登録する" }));
    expect(await dialog().findByText("銘柄が見つかりません(13050)")).toBeInTheDocument();
    await user.click(dialog().getByRole("button", { name: "やめる" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("銘柄の検索に失敗したら、その旨を出す。Escape で閉じる", async () => {
    const user = userEvent.setup();
    renderApp("/holdings", (p) => {
      if (p === "/api/holdings") return { body: HOLDINGS };
      return { status: 503, body: { detail: "DB に接続できません" } };
    });
    await user.click(await screen.findByRole("button", { name: "＋ 銘柄を登録" }));
    await user.type(dialog().getByRole("textbox", { name: "銘柄コード" }), "1");
    await user.tab();
    expect(await dialog().findByText("DB に接続できません")).toBeInTheDocument();
    // 空にして離れると、何も探さない
    await user.clear(dialog().getByRole("textbox", { name: "銘柄コード" }));
    await user.tab();
    expect(dialog().queryByText("DB に接続できません")).toBeNull();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("購入ごとの行を編集する", async () => {
    const user = userEvent.setup();
    const sent: unknown[] = [];
    renderApp(
      "/holdings",
      handler((method, p, body) => {
        sent.push([method, p, body]);
        return { body: { holding_id: 12, sold_holding_id: null, message: "保存しました" } };
      }),
    );
    await user.click(await screen.findByRole("button", { name: "サンプル工業Bの購入ごとの行を開く" }));
    await user.click(screen.getAllByRole("button", { name: "編集" })[1]!);
    expect(dialog().getByText("1134 サンプル工業B(変更できません)")).toBeInTheDocument();
    expect(dialog().getByText("2回目の購入(購入ごとの行を直す)")).toBeInTheDocument();
    const price = dialog().getByRole("textbox", { name: "買値(1株)" });
    expect(price).toHaveValue("1161.3");
    await user.clear(price);
    await user.click(dialog().getByRole("button", { name: "保存する" }));
    expect(dialog().getByText("買値は0より大きい値を入れてください")).toBeInTheDocument();
    await user.type(price, "1160");
    await user.type(dialog().getByRole("textbox", { name: "メモ" }), "直した");
    await user.click(dialog().getByRole("button", { name: "保存する" }));
    expect(await screen.findByRole("status")).toHaveTextContent("保存しました");
    expect(sent).toEqual([["PUT", "/api/holdings/12", { buy_price: 1160, quantity: 100, buy_date: "2026-06-29", note: "直した" }]]);
  });

  it("編集で API のエラーが返れば、ダイアログに出す(購入が1回の銘柄は「何回目」を出さない)", async () => {
    const user = userEvent.setup();
    renderApp("/holdings", handler(() => ({ status: 400, body: { detail: "売却済みの行は変更できません" } })));
    const row = (await screen.findByRole("link", { name: "サンプル商事C" })).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: "編集" }));
    expect(dialog().queryByText(/回目の購入/)).toBeNull();
    expect(dialog().getByRole("textbox", { name: "メモ" })).toHaveValue("");
    await user.click(dialog().getByRole("button", { name: "保存する" }));
    expect(await dialog().findByText("売却済みの行は変更できません")).toBeInTheDocument();
  });

  it("一部を売却する(損益の見込みと、残る株数を出す)。入力のチェックをする", async () => {
    const user = userEvent.setup();
    const sent: unknown[] = [];
    renderApp(
      "/holdings",
      handler((method, p, body) => {
        sent.push([method, p, body]);
        return { body: { holding_id: 13, sold_holding_id: 21, message: "売却を登録しました" } };
      }),
    );
    const row = (await screen.findByRole("link", { name: "サンプル商事C" })).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: "売却" }));
    expect(screen.getByRole("dialog", { name: "売却を登録: 1136 サンプル商事C" })).toBeInTheDocument();
    expect(dialog().getByText("300株 ／ 買値 2,498.9(購入日 2026-05-20)")).toBeInTheDocument();
    expect(dialog().getByTestId("sell-pnl")).toHaveTextContent("-");
    // 売値がない
    await user.click(dialog().getByRole("button", { name: "売却を登録する" }));
    expect(dialog().getByText("売値は0より大きい値を入れてください")).toBeInTheDocument();
    // 株数が多すぎる
    const qty = dialog().getByRole("textbox", { name: "売却株数" });
    await user.clear(qty);
    await user.type(qty, "301");
    await user.click(dialog().getByRole("button", { name: "売却を登録する" }));
    expect(dialog().getByText("売却株数は1〜300株にしてください")).toBeInTheDocument();
    await user.click(dialog().getByRole("button", { name: "全部" }));
    expect(qty).toHaveValue("300");
    // 売却日が購入日より前
    const date = dialog().getByRole("textbox", { name: "売却日" });
    await user.clear(date);
    await user.type(date, "2026-05-01{Enter}");
    await user.click(dialog().getByRole("button", { name: "売却を登録する" }));
    expect(dialog().getByText("売却日は購入日以降で、今日以前の日付を入れてください")).toBeInTheDocument();
    await user.clear(date);
    await user.type(date, "2026-07-22{Enter}");
    await user.clear(qty);
    await user.type(qty, "100");
    await user.type(dialog().getByRole("textbox", { name: "売値(1株)" }), "2600");
    expect(dialog().getByTestId("sell-pnl")).toHaveTextContent("+10,110(+4.0%)");
    expect(dialog().getByText("一部の売却です。売った100株は「売却済み」に移り、残りの200株は保有中のまま残ります")).toBeInTheDocument();
    await user.click(dialog().getByRole("button", { name: "売却を登録する" }));
    expect(await screen.findByRole("status")).toHaveTextContent("売却を登録しました");
    expect(sent).toEqual([["POST", "/api/holdings/13/sell", { sell_date: "2026-07-22", quantity: 100, sell_price: 2600 }]]);
  });

  it("売却で API のエラーが返れば、ダイアログに出す", async () => {
    const user = userEvent.setup();
    renderApp("/holdings", handler(() => ({ status: 400, body: { detail: "売却済みの行は変更できません" } })));
    await user.click(await screen.findByRole("button", { name: "サンプル工業Bの購入ごとの行を開く" }));
    await user.click(screen.getAllByRole("button", { name: "売却" })[0]!);
    await user.type(dialog().getByRole("textbox", { name: "売値(1株)" }), "1200");
    await user.click(dialog().getByRole("button", { name: "売却を登録する" }));
    expect(await dialog().findByText("売却済みの行は変更できません")).toBeInTheDocument();
  });
});
