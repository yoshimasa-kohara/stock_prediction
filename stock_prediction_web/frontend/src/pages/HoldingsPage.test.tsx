import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { HOLDINGS } from "../test/apiData";
import type { FakeResponse } from "../test/fixtures";
import { renderApp, urlsOf } from "../test/renderApp";

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
    const cards = Array.from(document.querySelectorAll(".card .val")).map((e) => e.textContent);
    expect(cards.slice(0, 2)).toEqual(["152,000", "168,800"]);
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
    // 読み直しでは、今出している売却年(API が返した 2026)を保つ
    await waitFor(() =>
      expect(urlsOf(calls.filter((c) => c.method === "GET"), "/api/holdings")).toEqual([
        "/api/holdings",
        "/api/holdings?sold_year=2026",
      ]),
    );
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

  it("保有中の表の列の並びと、買いの金額・現在の金額(合計の行と購入ごとの行)", async () => {
    const user = userEvent.setup();
    renderApp("/holdings", handler());
    await screen.findByText("判定の日: 2026-07-22(水)");
    const heads = Array.from(document.querySelectorAll("thead tr:first-child th")).map((e) => e.textContent);
    expect(heads.slice(0, 11)).toEqual([
      "コード",
      "銘柄名",
      "市場",
      "買値",
      "買い株数",
      "買いの金額",
      "購入日",
      "現在値",
      "現在の金額",
      "損益(%)",
      "損益(円)",
    ]);
    expect(heads.slice(-4)).toEqual(["判定", "備考", "メモ", "操作"]);
    const rowCells = (name: string) =>
      Array.from(screen.getByText(name).closest("tr")!.querySelectorAll("td")).map((e) => e.textContent);
    // 合計の行(買い増し): 平均買値 × 合計の株数、現在値 × 合計の株数
    const total = rowCells("サンプル工業B");
    expect(total[5]).toBe("247,080");
    expect(total[8]).toBe("237,200");
    // 1回の購入
    const single = rowCells("サンプル商事C");
    expect(single[5]).toBe("749,670");
    expect(single[8]).toBe("665,700");
    // 購入ごとの行
    await user.click(screen.getByRole("button", { name: "サンプル工業Bの購入ごとの行を開く" }));
    const lot1 = rowCells("└ 1回目の購入");
    expect([lot1[5], lot1[8]]).toEqual(["130,950", "118,600"]);
    const lot2 = rowCells("└ 2回目の購入");
    expect([lot2[5], lot2[8]]).toEqual(["116,130", "118,600"]);
  });

  it("現在値がない銘柄は、現在の金額を「-」にする", async () => {
    const [first, second] = HOLDINGS.groups;
    const noClose = {
      ...HOLDINGS,
      groups: [
        {
          ...second!,
          current: null,
          current_date: null,
          value: null,
          current_amount: null,
          lots: second!.lots.map((l) => ({ ...l, current_amount: null })),
        },
        first!,
      ],
    };
    renderApp("/holdings", (p) => (p === "/api/holdings" ? { body: noClose } : undefined));
    await screen.findByText("判定の日: 2026-07-22(水)");
    const cells = Array.from(screen.getByText("サンプル商事C").closest("tr")!.querySelectorAll("td")).map(
      (e) => e.textContent,
    );
    expect(cells[5]).toBe("749,670");
    expect(cells[7]).toBe("-");
    expect(cells[8]).toBe("-");
  });

  it("売却済みの表の列の並びと、買いの金額・売りの金額", async () => {
    const user = userEvent.setup();
    renderApp("/holdings", handler());
    await user.click(await screen.findByRole("tab", { name: "売却済み(1)" }));
    const heads = Array.from(document.querySelectorAll("thead th")).map((e) => e.textContent);
    expect(heads).toEqual([
      "コード",
      "銘柄名",
      "市場",
      "買値",
      "買い株数",
      "買いの金額",
      "購入日",
      "売値",
      "売り株数",
      "売りの金額",
      "売却日",
      "損益(%)",
      "損益(円)",
      "保有した日数",
      "メモ",
      "現在値",
    ]);
    const cells = Array.from(screen.getByText("サンプル物産B").closest("tr")!.querySelectorAll("td")).map(
      (e) => e.textContent,
    );
    expect(cells[5]).toBe("152,000");
    expect(cells[9]).toBe("168,800");
    // 保有した日数、メモ(なければ空)、現在値(一番右)
    expect(cells.slice(13)).toEqual(["59日", "", "1,702.0"]);
    // 現在の金額の列は出さない
    expect(heads).not.toContain("現在の金額");
  });

  it("売却年のコンボボックス(先頭は「全て」、初期値は API が返した年)。選び直すと sold_year を付けて読み直す", async () => {
    const user = userEvent.setup();
    const calls = renderApp("/holdings", (p, q) => {
      if (p !== "/api/holdings") return undefined;
      const year = q.get("sold_year");
      const base = { ...HOLDINGS, sold_count: 6, sold_years: [2026, 2025] };
      if (year === "2025") return { body: { ...base, sold_year: 2025, sold_shown_count: 2 } };
      if (year === "all") return { body: { ...base, sold_year: null, sold_shown_count: 6 } };
      return { body: { ...base, sold_year: 2026, sold_shown_count: 4 } };
    });
    // 保有中のタブには出さない
    await screen.findByText("判定の日: 2026-07-22(水)");
    expect(screen.queryByLabelText("売却年")).toBeNull();
    // タブの件数は全件、「表示 ○件」は絞り込んだ後の件数
    await user.click(screen.getByRole("tab", { name: "売却済み(6)" }));
    const select = screen.getByLabelText("売却年");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["全て", "2026", "2025"]);
    expect(select).toHaveValue("2026");
    expect(screen.getByText("表示 4件")).toBeInTheDocument();
    await user.selectOptions(select, "2025");
    expect(await screen.findByText("表示 2件")).toBeInTheDocument();
    expect(screen.getByLabelText("売却年")).toHaveValue("2025");
    expect(screen.getByRole("tab", { name: "売却済み(6)" })).toHaveAttribute("aria-selected", "true");
    await user.selectOptions(screen.getByLabelText("売却年"), "全て");
    expect(await screen.findByText("表示 6件")).toBeInTheDocument();
    expect(screen.getByLabelText("売却年")).toHaveValue("all");
    expect(urlsOf(calls, "/api/holdings")).toEqual([
      "/api/holdings",
      "/api/holdings?sold_year=2025",
      "/api/holdings?sold_year=all",
    ]);
  });

  it("API が返した売却年が null なら、コンボボックスの初期値は「全て」", async () => {
    const user = userEvent.setup();
    const body = { ...HOLDINGS, sold: [], sold_count: 0, sold_years: [], sold_year: null, sold_shown_count: 0 };
    renderApp("/holdings", (p) => (p === "/api/holdings" ? { body } : undefined));
    await user.click(await screen.findByRole("tab", { name: "売却済み(0)" }));
    const select = screen.getByLabelText("売却年");
    expect(select).toHaveValue("all");
    expect(within(select).getAllByRole("option")).toHaveLength(1);
    expect(screen.getByText("表示 0件")).toBeInTheDocument();
  });

  it("保有中の「表示 ○件」は購入ごとの行の数(合計の行を開いても変わらない)", async () => {
    const user = userEvent.setup();
    renderApp("/holdings", handler());
    // 2回に分けて購入した銘柄1つ + 1回の購入の銘柄1つ → 3件(タブの件数は銘柄の数)
    expect(await screen.findByText("表示 3件")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "保有中(2)" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "サンプル工業Bの購入ごとの行を開く" }));
    expect(screen.getByText("表示 3件")).toBeInTheDocument();
  });

  it("メモ: 20文字を超えると「…」付きで出し、title に全文を入れる。押すと全文と短い表示を切り替える", async () => {
    const user = userEvent.setup();
    const long = "決算が良ければ買い増す。\n悪ければ半分を売ってようすを見る";
    const short = "決算が良ければ買い増す。 悪ければ半分を…";
    const [first, second] = HOLDINGS.groups;
    const body = {
      ...HOLDINGS,
      groups: [first!, { ...second!, lots: second!.lots.map((l) => ({ ...l, note: long })) }],
      sold: HOLDINGS.sold.map((s) => ({ ...s, note: "あ".repeat(21) })),
    };
    renderApp("/holdings", (p) => (p === "/api/holdings" ? { body } : undefined));
    await screen.findByText("判定の日: 2026-07-22(水)");
    const cellsOf = (name: string) => Array.from(screen.getByText(name).closest("tr")!.querySelectorAll("td"));
    // 購入が1回だけの銘柄は、合計の行(備考と操作の間)にメモを出す
    const single = cellsOf("サンプル商事C");
    const memo = within(single[single.length - 2]!).getByRole("button");
    expect(memo).toHaveTextContent(short, { normalizeWhitespace: false });
    expect(memo).toHaveAttribute("title", long);
    expect(memo).toHaveAttribute("aria-expanded", "false");
    await user.click(memo);
    expect(memo).toHaveTextContent(long, { normalizeWhitespace: false });
    expect(memo).toHaveClass("full");
    await user.click(memo);
    expect(memo).toHaveTextContent(short, { normalizeWhitespace: false });
    expect(memo).not.toHaveClass("full");
    // 買い増しの銘柄は、合計の行は空で、購入ごとの行に出す(メモがない行は空)
    const total = cellsOf("サンプル工業B");
    expect(total[total.length - 2]!.textContent).toBe("");
    expect(screen.queryByText("1回目")).toBeNull();
    await user.click(screen.getByRole("button", { name: "サンプル工業Bの購入ごとの行を開く" }));
    const lot1 = cellsOf("└ 1回目の購入");
    expect(lot1[lot1.length - 3]!.textContent).toBe("(判定は合計の行)");
    expect(lot1[lot1.length - 2]!.textContent).toBe("1回目");
    expect(within(lot1[lot1.length - 2]!).getByText("1回目")).toHaveAttribute("title", "1回目");
    expect(within(lot1[lot1.length - 1]!).getByRole("button", { name: "編集" })).toBeInTheDocument();
    const lot2 = cellsOf("└ 2回目の購入");
    expect(lot2[lot2.length - 2]!.textContent).toBe("");
    // 売却済みの表も同じ出し方(20文字ちょうどまでは「…」を付けない)
    await user.click(screen.getByRole("tab", { name: "売却済み(1)" }));
    const sold = cellsOf("サンプル物産B");
    const soldMemo = within(sold[14]!).getByRole("button");
    expect(soldMemo).toHaveTextContent(`${"あ".repeat(20)}…`);
    expect(soldMemo).toHaveAttribute("title", "あ".repeat(21));
    await user.click(soldMemo);
    expect(soldMemo).toHaveTextContent("あ".repeat(21));
  });

  it("メモが20文字ちょうどなら「…」を付けない", async () => {
    const [first, second] = HOLDINGS.groups;
    const note = "い".repeat(20);
    const body = { ...HOLDINGS, groups: [first!, { ...second!, lots: second!.lots.map((l) => ({ ...l, note })) }] };
    renderApp("/holdings", (p) => (p === "/api/holdings" ? { body } : undefined));
    expect(await screen.findByText(note)).toHaveAttribute("title", note);
    expect(screen.queryByText(/…$/)).toBeNull();
  });

  it("「＋ 銘柄を登録」は保有中のタブだけに出す", async () => {
    const user = userEvent.setup();
    renderApp("/holdings", handler());
    expect(await screen.findByRole("button", { name: "＋ 銘柄を登録" })).toBeInTheDocument();
    // ボタンは一覧を読み込む前から出るので、タブの件数(読み込んだ後に出る)を待ってから押す
    await user.click(await screen.findByRole("tab", { name: "売却済み(1)" }));
    expect(screen.queryByRole("button", { name: "＋ 銘柄を登録" })).toBeNull();
    await user.click(screen.getByRole("tab", { name: "保有中(2)" }));
    expect(screen.getByRole("button", { name: "＋ 銘柄を登録" })).toBeInTheDocument();
  });

  it("買値・買い株数・買いの金額・購入日のヘッダーに色のクラス(buy)を付ける(保有中・売却済み)", async () => {
    const user = userEvent.setup();
    renderApp("/holdings", handler());
    await screen.findByText("判定の日: 2026-07-22(水)");
    const buyHeads = () => Array.from(document.querySelectorAll("th.buy")).map((e) => e.textContent);
    expect(buyHeads()).toEqual(["買値", "買い株数", "買いの金額", "購入日"]);
    await user.click(screen.getByRole("tab", { name: "売却済み(1)" }));
    expect(buyHeads()).toEqual(["買値", "買い株数", "買いの金額", "購入日"]);
    // 売りの列の色(act)は今のまま
    expect(Array.from(document.querySelectorAll("th.act")).map((e) => e.textContent)).toEqual([
      "売値",
      "売り株数",
      "売りの金額",
      "売却日",
    ]);
  });

  it("判定と備考の色のクラス(売却は赤、注意・乗り換えは橙、判定なしは緑、「MM/DD の終値」は橙、流動性注意は緑)", async () => {
    const [first, second] = HOLDINGS.groups;
    const judgments = [
      ["保持", ""],
      ["保持(注意: 上昇予測だが下降しています)", "jo"],
      ["保持(注意: 相場全体が下がる予測)", "jo"],
      ["乗り換えを検討(上昇予測だが市場の中央値を下回る予測)", "jo"],
      ["売却(下降予測、市場の中央値も下回る予測)", "jr"],
      ["判定なし(本日の予測がありません)", "jg"],
    ] as const;
    const groups = judgments.map(([judgment], i) => ({
      ...(i % 2 === 0 ? first! : second!),
      code: `1${i}000`,
      name: `銘柄${i}`,
      judgment,
      notes:
        i === 0
          ? [
              { text: "11/05 の終値", color: "orange" },
              { text: "流動性注意", color: "green" },
            ]
          : [],
    }));
    renderApp("/holdings", (p) => (p === "/api/holdings" ? { body: { ...HOLDINGS, groups } } : undefined));
    await screen.findByText("判定の日: 2026-07-22(水)");
    for (const [judgment, cls] of judgments) {
      const el = screen.getByText(judgment);
      if (cls) expect(el).toHaveClass(cls);
      else expect(el.className).toBe("");
    }
    expect(screen.getByText("11/05 の終値")).toHaveClass("o");
    expect(screen.getByText("流動性注意")).toHaveClass("g");
  });
});
