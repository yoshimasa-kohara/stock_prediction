import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { FINANCIALS, FINANCIALS_EMPTY } from "../test/apiData";
import { type FakeResponse, mockApi } from "../test/fixtures";
import FinancialsBlock from "./FinancialsBlock";

function renderBlock(res: FakeResponse) {
  const calls = mockApi((p) => (p === "/api/stocks/11110/financials" ? res : undefined));
  const view = render(<FinancialsBlock code="11110" />);
  return { calls, view };
}

/** 表の本文の、行ごとのセルの文字 */
function bodyCells(table: HTMLElement): string[][] {
  return within(table)
    .getAllByRole("row")
    .slice(1)
    .map((tr) => within(tr).getAllByRole("cell").map((td) => td.textContent ?? ""));
}

describe("FinancialsBlock(銘柄詳細の業績)", () => {
  it("通期のタブ: 金額は億円、前年比は % か、黒字化・赤字化・赤字縮小・赤字拡大・「-」", async () => {
    const user = userEvent.setup();
    const { calls } = renderBlock({ body: FINANCIALS });
    await screen.findByRole("table", { name: "業績(四半期・累計)" });
    await user.click(screen.getByRole("tab", { name: "通期" }));
    const table = screen.getByRole("table", { name: "業績(通期)" });
    expect(calls.map((c) => c.url)).toEqual(["/api/stocks/11110/financials"]);
    expect(screen.getByRole("heading", { name: "業績" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "通期" })).toHaveAttribute("aria-selected", "true");
    const heads = within(table).getAllByRole("columnheader").map((th) => th.textContent);
    expect(heads).toEqual([
      "決算期", "開示日", "売上高(億円)", "前年比", "営業利益(億円)", "前年比", "純利益(億円)", "前年比", "営業利益率", "EPS(円)",
    ]); // prettier-ignore
    expect(bodyCells(table)).toEqual([
      ["2026年3月期 通期", "2026-05-12", "520.0", "+8.3%", "31.0", "-2.1%", "21.0", "0.0%", "6.0%", "47.7"],
      ["2025年3月期 通期", "2025-05-13", "480.0", "黒字化", "31.0", "赤字化", "21.0", "赤字縮小", "6.0%", "47.7"],
      ["2024年3月期 通期", "2024-05-14", "-", "-", "-12.5", "赤字拡大", "-", "-", "-", "-"],
    ]);
    // 色: プラス・黒字化・赤字縮小は赤、マイナス・赤字化・赤字拡大は青、0 は灰色
    expect(within(table).getByText("+8.3%")).toHaveClass("up");
    expect(within(table).getByText("-2.1%")).toHaveClass("dn");
    expect(within(table).getByText("0.0%")).toHaveClass("eq");
    expect(within(table).getByText("黒字化")).toHaveClass("up");
    expect(within(table).getByText("赤字化")).toHaveClass("dn");
    expect(within(table).getByText("赤字縮小")).toHaveClass("up");
    expect(within(table).getByText("赤字拡大")).toHaveClass("dn");
    // スマホで決算期の列を左に固定する
    expect(within(table).getByRole("columnheader", { name: "決算期" })).toHaveClass("fix");
    expect(within(table).getByText("2026年3月期 通期")).toHaveClass("fix");
    expect(screen.queryByText(/期首からの累計/)).toBeNull();
  });

  it("初めは四半期の表を出す(タブは「四半期」「通期」の順)。見出しと注記に「累計」と書く。4Q の前年比は「-」", async () => {
    renderBlock({ body: FINANCIALS });
    const table = await screen.findByRole("table", { name: "業績(四半期・累計)" });
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["四半期", "通期"]);
    expect(screen.getByRole("tab", { name: "四半期" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("heading", { name: "業績(四半期・累計)" })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "売上高(累計・億円)" })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "EPS(累計・円)" })).toBeInTheDocument();
    expect(screen.getByText(/四半期の値は、期首からの累計/)).toBeInTheDocument();
    const rows = bodyCells(table);
    expect(rows.map((r) => r[0])).toEqual(["2027年3月期 1Q", "2026年3月期 通期", "2026年3月期 4Q"]);
    expect(rows[0]![2]).toBe("130.0");
    expect([rows[2]![3], rows[2]![5], rows[2]![7]]).toEqual(["-", "-", "-"]);
  });

  it("会社予想と修正: 今期・来期の予想と、上方修正(赤)・下方修正(青)と前の値を出す。来期の1株あたり利益は「-」", async () => {
    renderBlock({ body: FINANCIALS });
    const table = await screen.findByRole("table", { name: "会社予想と修正" });
    expect(screen.getByRole("heading", { name: "会社予想と修正(最新の開示 2026-08-07)" })).toBeInTheDocument();
    expect(within(table).getAllByRole("columnheader").map((th) => th.textContent)).toEqual([
      "項目", "今期の予想(2027年3月期)", "修正", "来期の予想(2028年3月期)", "修正",
    ]); // prettier-ignore
    expect(bodyCells(table)).toEqual([
      ["売上高(億円)", "550.0", "上方修正(前の値 540.0。2026-08-07)", "560.0", "-"],
      ["営業利益(億円)", "30.0", "下方修正(前の値 33.0。2026-08-07)", "-", "-"],
      ["純利益(億円)", "22.0", "-", "-", "-"],
      ["1株あたり利益(円)", "50.1", "-", "-", "-"],
      ["年間配当(円)", "26.0", "上方修正(前の値 24.0。2026-06-20)", "28.0", "-"],
    ]);
    expect(within(table).getAllByText("上方修正")[0]).toHaveClass("up");
    expect(within(table).getByText("下方修正")).toHaveClass("dn");
    // 予想の配当利回り(% で小数1桁)
    const note = screen.getByText(/予想の配当利回り/);
    expect(note).toHaveTextContent("予想の配当利回り: 0.7%(今期の年間配当の予想 26.0円 ÷ 2026-07-22 の株価 3,945.0円)");
  });

  it("修正の履歴: 開示日・開示の種類・予想・修正の向きを新しい順に出す", async () => {
    renderBlock({ body: FINANCIALS });
    const table = await screen.findByRole("table", { name: "予想の修正の履歴" });
    expect(screen.getByRole("heading", { name: "今期(2027年3月期)の予想の修正の履歴" })).toBeInTheDocument();
    expect(bodyCells(table)).toEqual([
      ["2026-08-07", "決算短信", "550.0", "30.0", "22.0", "上方・下方修正"],
      ["2026-07-01", "業績予想の修正", "540.0", "33.0", "-", "上方修正"],
      ["2026-06-01", "その他", "540.0", "32.0", "22.0", "下方修正"],
      ["2026-05-12", "決算短信(前期の通期)", "540.0", "32.5", "22.0", "-"],
    ]);
    const first = within(table).getAllByRole("row")[1]!;
    expect(within(first).getByText("550.0")).toHaveClass("up");
    expect(within(first).getByText("30.0")).toHaveClass("dn");
    expect(within(first).getByText("22.0")).not.toHaveClass("up");
    expect(within(table).getByText("上方修正")).toHaveClass("up");
    expect(within(table).getByText("下方修正")).toHaveClass("dn");
  });

  it("データがないときは「業績のデータがありません」", async () => {
    renderBlock({ body: FINANCIALS_EMPTY });
    expect(await screen.findByText("業績のデータがありません")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("tab")).toBeNull();
  });

  it("予想・修正の履歴・配当の予想がないときは、表だけを出す。通期の決算がなければその旨を出す", async () => {
    const user = userEvent.setup();
    renderBlock({
      body: { ...FINANCIALS, annual: [], forecast: null, revisions: [], dividend_yield: null, dividend: null },
    });
    await screen.findByRole("table", { name: "業績(四半期・累計)" });
    await user.click(screen.getByRole("tab", { name: "通期" }));
    expect(screen.getByText("通期の決算はありません")).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "会社予想と修正" })).toBeNull();
    expect(screen.queryByRole("table", { name: "予想の修正の履歴" })).toBeNull();
  });

  it("来期の配当の予想を使ったときは「来期の」と書く。配当の予想がなければ「-」。四半期の決算がなければその旨を出す", async () => {
    const { view } = renderBlock({
      body: { ...FINANCIALS, quarterly: [], revisions: [], dividend: { ...FINANCIALS.dividend!, is_next: true } },
    });
    expect(await screen.findByText(/来期の年間配当の予想 26.0円/)).toBeInTheDocument();
    expect(screen.getByText("決算はありません")).toBeInTheDocument();
    view.unmount();
    renderBlock({ body: { ...FINANCIALS, dividend_yield: null, dividend: null } });
    expect(await screen.findByText(/予想の配当利回り/)).toHaveTextContent("予想の配当利回り: -。");
  });

  it("読み込み中は「読み込み中…」、読めなければエラーを出す", async () => {
    renderBlock({ status: 503, body: { detail: "DB に接続できません" } });
    expect(screen.getByText("読み込み中…")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("DB に接続できません");
    expect(screen.queryByText("読み込み中…")).toBeNull();
  });
});
