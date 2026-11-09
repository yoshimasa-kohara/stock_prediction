import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { RESULTS } from "../test/apiData";
import { renderApp, urlsOf } from "../test/renderApp";

describe("買い候補と実際の株価", () => {
  it("予測日は新しい日付が上(目次も表も降順)。目次のリンクは各ブロックへ", async () => {
    const [first] = RESULTS.days;
    const body = { ...RESULTS, days: [first!, { ...first!, pred_date: "2026-04-21" }, { ...first!, pred_date: "2026-04-22" }] };
    renderApp("/candidates/results", (p) => (p === "/api/candidates/results" ? { body } : undefined));
    await screen.findByRole("heading", { level: 2, name: /2026-04-22\(水\) の買い候補/ });
    const links = within(screen.getByRole("navigation", { name: "目次" })).getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["#d2026-04-22", "#d2026-04-21", "#d2026-04-20"]);
    const ids = screen.getAllByRole("heading", { level: 2 }).map((h) => h.id);
    expect(ids).toEqual(["d2026-04-22", "d2026-04-21", "d2026-04-20"]);
  });

  it("予測日ごとの表に、実際の株価・差額・値上がり率・上昇と、まとめ3行を出す", async () => {
    renderApp("/candidates/results", (p) => (p === "/api/candidates/results" ? { body: RESULTS } : undefined));
    const heading = await screen.findByRole("heading", { level: 2, name: /2026-04-20\(月\) の買い候補/ });
    expect(heading).toHaveAttribute("id", "d2026-04-20");
    expect(screen.getByRole("navigation", { name: "目次" })).toHaveTextContent("目次: 2026-04-20(月)");
    expect(screen.getByRole("textbox", { name: "期間(開始)" })).toHaveValue("2026-04-22");
    const table = within(screen.getByRole("table"));
    expect(table.getByText("5日後(対象日 4/27)")).toBeInTheDocument();
    // 上がった(赤)、下がった(青)、同じ(灰)
    expect(table.getByText("1,100.0")).toHaveClass("up");
    expect(table.getByText("+100.0")).toHaveClass("up");
    expect(table.getByText("↑")).toHaveClass("up");
    expect(table.getByText("950.0")).toHaveClass("dn");
    expect(table.getAllByText("↓")[0]).toHaveClass("dn");
    expect(table.getByText("→")).toHaveClass("eq");
    // まとめ(5日後: 2銘柄、+10.0% と -2.0% の平均 +4.0%、中央値 +1.5%、差 +2.5%)
    expect(table.getByText("2銘柄のまとめ")).toBeInTheDocument();
    expect(table.getByText("上がった銘柄 ↑ 1 / 2")).toBeInTheDocument();
    expect(table.getByText("+4.0%")).toBeInTheDocument();
    expect(table.getByText("中央値を上回った銘柄 1 / 2")).toBeInTheDocument();
    expect(table.getByText("+2.5%")).toBeInTheDocument();
    expect(table.getByText("ROE8%以上(稼ぐ力が高い)")).toBeInTheDocument();
  });

  it("期間を変えて「表示」を押すと読み直す。買い候補がなければ、その旨を出す", async () => {
    const user = userEvent.setup();
    const calls = renderApp("/candidates/results", (p, q) => {
      if (p !== "/api/candidates/results") return undefined;
      return { body: q.get("from") ? { ...RESULTS, date_from: q.get("from"), days: [] } : RESULTS };
    });
    const from = await screen.findByRole("textbox", { name: "期間(開始)" });
    await waitFor(() => expect(from).toHaveValue("2026-04-22"));
    await user.clear(from);
    await user.type(from, "2026-07-01{Enter}");
    await user.click(screen.getByRole("button", { name: "表示" }));
    expect(await screen.findByText("この期間の買い候補はありません。")).toBeInTheDocument();
    expect(urlsOf(calls, "/api/candidates/results").at(-1)).toBe("/api/candidates/results?from=2026-07-01&to=2026-07-22");
  });

  it("買い候補(業種分散)に切り替える。期間を指定していれば、同じ期間のまま", async () => {
    const user = userEvent.setup();
    const calls = renderApp("/candidates/results?from=2026-04-01&to=2026-04-30", (p, q) =>
      p === "/api/candidates/results" ? { body: { ...RESULTS, kind: q.get("kind") ?? "top" } } : undefined,
    );
    await screen.findByRole("heading", { level: 2, name: /2026-04-20\(月\) の買い候補/ });
    await user.click(screen.getByRole("tab", { name: "買い候補(業種分散)" }));
    expect(await screen.findByRole("heading", { level: 2, name: /2026-04-20\(月\) の買い候補\(業種分散\)/ })).toBeInTheDocument();
    expect(screen.getByText(/予測日ごとの買い候補\(業種分散\)\(同じ33業種は3銘柄まで、上位30銘柄。メールと同じ内容\)/)).toBeInTheDocument();
    expect(urlsOf(calls, "/api/candidates/results").at(-1)).toBe(
      "/api/candidates/results?from=2026-04-01&to=2026-04-30&kind=sector",
    );
    // 「表示」を押しても業種分散のまま
    await user.click(screen.getByRole("button", { name: "表示" }));
    await waitFor(() =>
      expect(urlsOf(calls, "/api/candidates/results").at(-1)).toBe(
        "/api/candidates/results?from=2026-04-01&to=2026-04-30&kind=sector",
      ),
    );
  });

  it("期間を省略して切り替えると、期間は省略のまま", async () => {
    const user = userEvent.setup();
    const calls = renderApp("/candidates/results", (p) => (p === "/api/candidates/results" ? { body: { ...RESULTS, days: [] } } : undefined));
    await screen.findByText("この期間の買い候補はありません。");
    await user.click(screen.getByRole("tab", { name: "買い候補(業種分散)" }));
    expect(await screen.findByText("この期間の買い候補(業種分散)はありません。")).toBeInTheDocument();
    expect(urlsOf(calls, "/api/candidates/results").at(-1)).toBe("/api/candidates/results?kind=sector");
  });

  it("期間が長すぎるなどのエラーは、そのまま出す", async () => {
    renderApp("/candidates/results?from=2026-01-01&to=2026-07-22", (p) =>
      p === "/api/candidates/results" ? { status: 400, body: { detail: "期間は3か月以内にしてください" } } : undefined,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("期間は3か月以内にしてください");
  });
});
