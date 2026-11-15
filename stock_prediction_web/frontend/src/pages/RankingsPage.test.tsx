import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PRED_DATES, RANKINGS, RANKINGS_MORE, WATCH_CODES } from "../test/apiData";
import type { FakeResponse } from "../test/fixtures";
import { renderApp, urlsOf } from "../test/renderApp";

/** type・horizon を、呼ばれたクエリのとおりに返す */
function rankings(q: URLSearchParams, over: Partial<typeof RANKINGS> = {}): FakeResponse {
  const body = q.get("offset") ? RANKINGS_MORE : RANKINGS;
  return {
    body: {
      ...body,
      pred_date: q.get("date") ?? body.pred_date,
      type: q.get("type") ?? "rel",
      horizon: Number(q.get("horizon") ?? 20),
      ...over,
    },
  };
}

function handler(res: (q: URLSearchParams) => FakeResponse = (q) => rankings(q)) {
  return (p: string, q: URLSearchParams) => {
    if (p === "/api/prediction-dates") return { body: PRED_DATES };
    if (p === "/api/watch-stocks/codes") return { body: WATCH_CODES };
    if (p === "/api/rankings") return res(q);
    return undefined;
  };
}

const lastUrl = (calls: ReturnType<typeof renderApp>) => decodeURIComponent(urlsOf(calls, "/api/rankings").at(-1) ?? "");

/** 表の本文の、行ごとのセルの文字 */
function bodyCells(table: HTMLElement): string[][] {
  const rows = Array.from(table.querySelectorAll<HTMLTableRowElement>("tbody tr"));
  return rows.map((tr) => Array.from(tr.cells).map((td) => td.textContent ?? ""));
}

describe("ランキング", () => {
  it("初めは最新の予測日・市場比の確率・主となる日数。件数と表の列を出す。タブの title は「株価予測-ランキング」", async () => {
    const calls = renderApp("/rankings", handler());
    const table = await screen.findByRole("table", { name: "ランキング" });
    expect(screen.getByRole("heading", { level: 1, name: "ランキング" })).toBeInTheDocument();
    expect(document.title).toBe("株価予測-ランキング");
    // 上のメニューでは「銘柄検索」の右に出し、今の画面は色を変える
    const nav = within(screen.getByRole("navigation", { name: "メニュー" }));
    const labels = nav.getAllByRole("link").map((a) => a.textContent);
    expect(labels.indexOf("ランキング")).toBe(labels.indexOf("銘柄検索") + 1);
    expect(nav.getByRole("link", { name: "ランキング" })).toHaveClass("cur");
    // 絞り込みの初期値(流動性注意を除くは ON = API の初期値なので、クエリには入れない)
    expect(urlsOf(calls, "/api/rankings")).toEqual(["/api/rankings"]);
    expect(screen.getByRole("textbox", { name: "予測日" })).toHaveValue("2026-07-22");
    expect(screen.getByRole("checkbox", { name: "流動性注意を除く" })).toBeChecked();
    for (const m of ["プライム", "スタンダード", "グロース"]) expect(screen.getByRole("checkbox", { name: m })).toBeChecked();
    // 種類のタブ(7つ)と予測日数のタブ
    const types = screen.getByRole("tablist", { name: "ランキングの種類" });
    expect(within(types).getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "①市場比の確率", "②上昇確率", "③予測の値上がり率", "④下がりそうな銘柄", "⑤確率が急に上がった銘柄", "⑥全期間で高い銘柄",
      "⑦買い候補の常連",
    ]); // prettier-ignore
    expect(within(types).getByRole("tab", { name: "①市場比の確率" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "20日後(主)" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "20日後(主)" })).toBeEnabled();
    // 件数と説明
    expect(screen.getByText("3 件")).toBeInTheDocument();
    expect(screen.getByText(/予測した銘柄 3,961 件のうち/)).toBeInTheDocument();
    expect(screen.getByText("20日後の市場比の確率が高い順。50件ずつ表示")).toBeInTheDocument();
    // 列(主となる日数は緑の見出し・太字、ランキングの値は強調)
    const heads = within(table).getAllByRole("columnheader").map((th) => th.textContent);
    expect(heads.slice(0, 7)).toEqual(["順位", "コード", "銘柄名", "市場", "業種", "市場比(20日)", "予測日の株価"]);
    expect(heads.slice(7, 15)).toEqual([
      "5日後", "10日後", "20日後", "60日後", "PER(予想)", "PBR", "売買代金(20日平均・億円)", "備考",
    ]); // prettier-ignore
    expect(within(table).getByRole("columnheader", { name: "市場比(20日)" })).toHaveClass("act");
    expect(within(table).getByRole("columnheader", { name: "20日後" })).toHaveClass("main");
    expect(within(table).getByRole("columnheader", { name: "銘柄名" })).toHaveClass("fix");
    const cells = bodyCells(table);
    expect(cells[0]!.slice(0, 7)).toEqual(["1", "1111", "☆サンプル銀行A", "プライム", "銀行業", "87.8%", "3,945.0"]);
    expect(cells[0]!.slice(7, 10)).toEqual(["87.8%", "61.1%", "4,189.2"]);
    expect(cells[0]!.slice(-4)).toEqual(["7.9", "0.46", "12.3", "ストップ高"]);
    // 値がない欄は「-」
    expect(cells[1]!.slice(-4)).toEqual(["-", "-", "-", ""]);
    const firstRow = table.querySelector<HTMLTableRowElement>("tbody tr")!;
    expect(firstRow.cells[5]).toHaveClass("b");
    expect(firstRow.cells[2]).toHaveClass("fix");
    expect(firstRow.cells[13]).toHaveClass("b"); // 20日後の市場比
    expect(firstRow.cells[7]).not.toHaveClass("b");
    // 銘柄名は新しいタブで銘柄詳細を開く
    const link = within(table).getByRole("link", { name: "サンプル銀行A" });
    expect(link).toHaveAttribute("href", "/stocks/11110");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("種類・予測日数・予測日を変えると、URL(date・type・horizon)と API に入れる", async () => {
    const user = userEvent.setup();
    const calls = renderApp("/rankings", handler());
    await screen.findByRole("table", { name: "ランキング" });
    await user.click(screen.getByRole("tab", { name: "②上昇確率" }));
    await waitFor(() => expect(lastUrl(calls)).toBe("/api/rankings?type=up"));
    expect(await screen.findByRole("columnheader", { name: "上昇確率(20日)" })).toBeInTheDocument();
    expect(screen.getByText("20日後の上昇確率が高い順。50件ずつ表示")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "5日後" }));
    await waitFor(() => expect(lastUrl(calls)).toBe("/api/rankings?type=up&horizon=5"));
    expect(await screen.findByRole("columnheader", { name: "上昇確率(5日)" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "◀ 前の日" }));
    await waitFor(() => expect(lastUrl(calls)).toBe("/api/rankings?date=2026-07-21&type=up&horizon=5"));
    expect(await screen.findByRole("textbox", { name: "予測日" })).toHaveValue("2026-07-21");
  });

  it("URL の date・type・horizon で開く", async () => {
    const calls = renderApp("/rankings?date=2026-07-21&type=down&horizon=60", handler());
    await screen.findByRole("table", { name: "ランキング" });
    expect(lastUrl(calls)).toBe("/api/rankings?date=2026-07-21&type=down&horizon=60");
    expect(screen.getByRole("tab", { name: "④下がりそうな銘柄" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "60日後" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("columnheader", { name: "市場比(60日)" })).toBeInTheDocument();
    expect(screen.getByText("60日後の市場比の確率が低い順。50件ずつ表示")).toBeInTheDocument();
  });

  it("③ 予測の値上がり率は符号付きの %(プラスは赤)、⑤ は前の予測日との差をポイントで出す", async () => {
    const user = userEvent.setup();
    renderApp(
      "/rankings?type=ret",
      handler((q) =>
        rankings(q, {
          prev_pred_date: q.get("type") === "rise" ? "2026-07-21" : null,
          rows: [
            { ...RANKINGS.rows[0]!, value: 0.0704 },
            { ...RANKINGS.rows[1]!, value: -0.0123 },
          ],
        }),
      ),
    );
    const table = await screen.findByRole("table", { name: "ランキング" });
    expect(within(table).getByRole("columnheader", { name: "予測の値上がり率(20日)" })).toBeInTheDocument();
    expect(within(table).getByText("+7.0%")).toHaveClass("up");
    expect(within(table).getByText("-1.2%")).toHaveClass("dn");
    expect(screen.getByText(/予測株価 ÷ 予測日の株価 − 1/)).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "⑤確率が急に上がった銘柄" }));
    const rise = await screen.findByRole("columnheader", { name: "前の予測日との差(ポイント)" });
    const riseTable = rise.closest("table") as HTMLElement;
    expect(within(riseTable).getByText("+7.0")).toHaveClass("up");
    expect(within(riseTable).getByText("-1.2")).toHaveClass("dn");
    expect(screen.getByText(/前の予測日\(2026-07-21\)から大きく上がった順/)).toBeInTheDocument();
  });

  it("⑤ で前の予測日がないときは、その旨を出す", async () => {
    renderApp("/rankings?type=rise", handler((q) => rankings(q, { rows: [], total: 0, has_more: false })));
    expect(await screen.findByText("条件に合う銘柄はありません")).toBeInTheDocument();
    expect(screen.getByText("前の予測日がないので、比べられません。50件ずつ表示")).toBeInTheDocument();
    expect(screen.getByText("0 件")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "もっと見る" })).toBeNull();
  });

  it("⑥⑦ では予測日数のタブを薄くして選べないようにする。⑦ は日数と平均順位を出す", async () => {
    const user = userEvent.setup();
    const calls = renderApp(
      "/rankings?type=all",
      handler((q) =>
        rankings(q, {
          regular_days: 18,
          rows: [
            { ...RANKINGS.rows[0]!, value: q.get("type") === "regular" ? 19 : 0.6384, sub_value: q.get("type") === "regular" ? 3.6316 : null },
            { ...RANKINGS.rows[1]!, value: q.get("type") === "regular" ? 2 : 0.5, sub_value: null },
          ],
        }),
      ),
    );
    const table = await screen.findByRole("table", { name: "ランキング" });
    expect(within(table).getByRole("columnheader", { name: "4期間の最小" })).toBeInTheDocument();
    expect(bodyCells(table)[0]![5]).toBe("63.8%");
    const tabs = screen.getByRole("tablist", { name: "予測日数" });
    expect(tabs).toHaveAttribute("aria-disabled", "true");
    for (const t of within(tabs).getAllByRole("tab")) expect(t).toBeDisabled();
    expect(screen.getByText("このランキングでは、予測日数は選べません")).toBeInTheDocument();
    // 押しても変わらない
    await user.click(within(tabs).getByRole("tab", { name: "5日後" }));
    expect(lastUrl(calls)).toBe("/api/rankings?type=all");
    await user.click(screen.getByRole("tab", { name: "⑦買い候補の常連" }));
    const header = await screen.findByRole("columnheader", { name: "買い候補に入った日数(平均順位)" });
    const regular = header.closest("table") as HTMLElement;
    expect(bodyCells(regular).map((r) => r[5])).toEqual(["19日(3.6位)", "2日(-)"]);
    expect(screen.getByText(/直近20予測日\(今ある予測日は18日\)のうち、買い候補に入った日数が多い順/)).toBeInTheDocument();
    for (const t of within(screen.getByRole("tablist", { name: "予測日数" })).getAllByRole("tab")) expect(t).toBeDisabled();
    // ほかの種類に戻すと、選べる
    await user.click(screen.getByRole("tab", { name: "①市場比の確率" }));
    await waitFor(() => expect(screen.getByRole("tab", { name: "5日後" })).toBeEnabled());
    expect(screen.queryByText("このランキングでは、予測日数は選べません")).toBeNull();
  });

  it("絞り込みは「絞り込む」を押したときに API に入れる(URL には入れない)。「条件をクリア」で初期値に戻す", async () => {
    const user = userEvent.setup();
    const calls = renderApp("/rankings?type=up", handler());
    await screen.findByRole("table", { name: "ランキング" });
    await user.click(screen.getByRole("checkbox", { name: "グロース" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "33業種を選ぶ" }), "3050");
    await user.click(screen.getByRole("checkbox", { name: "流動性注意を除く" }));
    await user.type(screen.getByRole("textbox", { name: "売買代金の下限" }), "5");
    await user.type(screen.getByRole("textbox", { name: "PERの下限" }), "3");
    await user.type(screen.getByRole("textbox", { name: "PERの上限" }), "15");
    await user.type(screen.getByRole("textbox", { name: "PBRの下限" }), "0.3");
    await user.type(screen.getByRole("textbox", { name: "PBRの上限" }), "1");
    await user.type(screen.getByRole("textbox", { name: "ROEの下限" }), "8");
    // 押すまでは読み直さない
    expect(urlsOf(calls, "/api/rankings")).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "絞り込む" }));
    await waitFor(() =>
      expect(lastUrl(calls)).toBe(
        "/api/rankings?type=up&markets=プライム,スタンダード&sectors=3050&exclude_illiquid=false&min_turnover=5&per_min=3&per_max=15&pbr_min=0.3&pbr_max=1&min_roe=8",
      ),
    );
    await screen.findByRole("table", { name: "ランキング" });
    // 種類を変えても、絞り込みは残る
    await user.click(screen.getByRole("tab", { name: "①市場比の確率" }));
    await waitFor(() => expect(lastUrl(calls)).toContain("/api/rankings?type=rel&markets=プライム,スタンダード&sectors=3050"));
    // 外していた市場を戻すと、条件なし(全て)にする。数の欄は Enter でも絞り込む
    await user.click(screen.getByRole("checkbox", { name: "グロース" }));
    await user.clear(screen.getByRole("textbox", { name: "ROEの下限" }));
    await user.type(screen.getByRole("textbox", { name: "ROEの下限" }), "10{Enter}");
    await waitFor(() => expect(lastUrl(calls)).toContain("min_roe=10"));
    expect(lastUrl(calls)).not.toContain("markets=");
    await user.click(screen.getByRole("button", { name: "条件をクリア" }));
    await waitFor(() => expect(lastUrl(calls)).toBe("/api/rankings?type=rel"));
    expect(screen.getByRole("checkbox", { name: "流動性注意を除く" })).toBeChecked();
    expect(screen.getByRole("textbox", { name: "PERの上限" })).toHaveValue("");
    expect(screen.queryByText("銀行業", { selector: ".badge" })).toBeNull();
  });

  it("「もっと見る」で続きを出す(5桁目が 0 でない銘柄コードは5桁のまま)。読めなければエラーを出す", async () => {
    const user = userEvent.setup();
    let fail = false;
    const calls = renderApp(
      "/rankings",
      handler((q) => (fail && q.get("offset") ? { status: 503, body: { detail: "DB に接続できません" } } : rankings(q))),
    );
    const table = await screen.findByRole("table", { name: "ランキング" });
    fail = true;
    await user.click(screen.getByRole("button", { name: "もっと見る" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("DB に接続できません");
    fail = false;
    await user.click(screen.getByRole("button", { name: "もっと見る" }));
    await waitFor(() => expect(bodyCells(table)).toHaveLength(3));
    expect(lastUrl(calls)).toBe("/api/rankings?offset=2");
    expect(bodyCells(table)[2]!.slice(0, 2)).toEqual(["3", "11145"]);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button", { name: "もっと見る" })).toBeNull();
  });

  it("銘柄名の左の「☆」で検討中銘柄に追加する。追加済みは「★」", async () => {
    const user = userEvent.setup();
    const calls = renderApp("/rankings", (p, q, m) => {
      if (m === "PUT") return { body: { code: "11110", created: true, message: "検討中銘柄に追加しました" } };
      return handler()(p, q);
    });
    const table = await screen.findByRole("table", { name: "ランキング" });
    expect(await within(table).findByRole("button", { name: "サンプル商事Aの検討中銘柄のメモを編集" })).toHaveTextContent("★");
    const star = within(table).getByRole("button", { name: "サンプル銀行Aを検討中銘柄に追加" });
    expect(star).toHaveTextContent("☆");
    expect(star.closest("td")).toHaveClass("fix");
    await user.click(star);
    await user.click(within(screen.getByRole("dialog", { name: "検討中銘柄に追加" })).getByRole("button", { name: "追加" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PUT").map((c) => c.url)).toEqual(["/api/watch-stocks/11110"]));
  });

  it("予測がない日は、その旨を出す。予測日の一覧を読めないときもエラーを出す", async () => {
    renderApp("/rankings?date=2026-07-19", (p) => {
      if (p === "/api/rankings") {
        return { status: 404, body: { detail: "この日の予測はありません(土日・祝日、またはバッチが動いていない日)" } };
      }
      if (p === "/api/prediction-dates") return { status: 503, body: { detail: "DB に接続できません" } };
      return undefined;
    });
    await waitFor(() => expect(screen.getAllByRole("alert")).toHaveLength(2));
    expect(screen.getByText("この日の予測はありません(土日・祝日、またはバッチが動いていない日)")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "予測日" })).toHaveValue("2026-07-19");
    expect(screen.queryByRole("table", { name: "ランキング" })).toBeNull();
  });
});
