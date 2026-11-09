import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { PREDICTIONS, PRICES, STOCK } from "../test/apiData";
import { chartState, resetChart } from "../test/chartMock";
import { type Handler, renderPage, urlsOf } from "../test/renderApp";
import StockDetailPage from "./StockDetailPage";

vi.mock("lightweight-charts", async () => (await import("../test/chartMock")).createChartMock());

function handler(over: Partial<Record<string, unknown>> = {}) {
  return (p: string, q: URLSearchParams) => {
    if (p === "/api/stocks/11110") return { body: { ...STOCK, ...over } };
    if (p === "/api/stocks/11110/prices") return { body: { ...PRICES, period: q.get("period") } };
    if (p === "/api/stocks/11110/predictions") return { body: PREDICTIONS };
    return undefined;
  };
}

const lines = () => chartState.series.filter((s) => s.kind === "Line");

/** この画面だけを表示する(App の lazy を通さない。test/renderApp.tsx の renderPage) */
function renderAt(path: string, handler?: Handler) {
  return renderPage(path, "/stocks/:code", <StockDetailPage />, handler);
}

describe("銘柄詳細", () => {
  beforeEach(() => resetChart());

  it("銘柄の情報・数字のカード・備考・最新の予測・過去の予測を出す", async () => {
    renderAt("/stocks/11110", handler());
    expect(await screen.findByRole("heading", { level: 1, name: /1111 サンプル銀行A/ })).toHaveTextContent(
      "プライム ／ 銀行業 ／ TOPIX Small 1",
    );
    expect(screen.getByText("3,945.0")).toBeInTheDocument();
    expect(screen.getByText("前日比 +59.2(+1.5%)")).toHaveClass("up");
    expect(screen.getByText("7.9倍 ／ 0.46倍")).toBeInTheDocument();
    expect(screen.getByText("同じ業種の予想PERの中央値 11.8倍")).toBeInTheDocument();
    expect(screen.getByText("時価総額 1,820億円")).toBeInTheDocument();
    expect(screen.getByText("1Q(あと12営業日)")).toBeInTheDocument();
    expect(screen.getByText("PBR0.5倍割れ(割安)")).toBeInTheDocument();
    // 最新の予測は、見出しの行とデータの1行の表(主となる20日後は太字)。市場比・予測株価・対象日はマウスを置くと出す
    const latest = (await screen.findByRole("heading", { name: "最新の予測" })).closest(".panel") as HTMLElement;
    const heads = Array.from(latest.querySelectorAll("thead th")).map((th) => th.textContent);
    expect(heads).toEqual([
      "予測日",
      "予測日の終値",
      "買い候補の順位",
      "5日後 上昇確率",
      "5日後 見込み",
      "10日後 上昇確率",
      "10日後 見込み",
      "20日後 上昇確率",
      "20日後 見込み",
      "60日後 上昇確率",
      "60日後 見込み",
    ]);
    const latestRows = latest.querySelectorAll<HTMLTableRowElement>("tbody tr");
    expect(latestRows).toHaveLength(1);
    const cells = Array.from(latestRows[0]!.cells).map((c) => c.textContent);
    expect(cells).toEqual(["2026-07-22(水)", "3,945.0", "1", "63.1%", "+2.1%", "-", "61.1%", "+6.2%", "-"]);
    const up20 = latestRows[0]!.cells[6]!;
    expect(up20).toHaveClass("b");
    expect(up20).toHaveAttribute("title", "対象日 8/19 ／ 市場比 87.8% ／ 予測株価 4,189.2");
    expect(latestRows[0]!.cells[7]).toHaveClass("up");
    expect(latestRows[0]!.cells[3]).not.toHaveClass("b");
    // 過去の予測は、予測日を1行にまとめ、予測日数ごとの列を横に並べる
    const panel = screen.getByRole("heading", { name: "過去の予測と答え合わせ" }).closest(".panel") as HTMLElement;
    const history = within(panel);
    expect(history.queryByRole("tab")).toBeNull();
    const rows = Array.from(panel.querySelectorAll<HTMLTableRowElement>("tbody tr"));
    expect(rows.map((r) => r.cells[0]?.textContent)).toEqual([
      "2026-07-22(水)",
      "2026-07-15(水)",
      "2026-07-01(水)",
      "2026-06-30(火)",
    ]);
    // 2026-07-22: 20日後は未検証(5日後などの予測がない日数は「-」)
    expect(within(rows[0]!).getByText("未検証")).toHaveClass("eq");
    expect(rows[0]!.cells[2]).toHaveTextContent("1");
    // 2026-07-15: 5日後が的中(+4.0%)
    expect(within(rows[1]!).getByText("的中")).toHaveClass("ok");
    expect(within(rows[1]!).getByText("+4.0%")).toHaveClass("up");
    expect(within(rows[1]!).getByText("3,795.1")).toBeInTheDocument();
    // 2026-07-01: 20日後が外れ。2026-06-30: 検証不能
    expect(within(rows[2]!).getByText("外れ")).toHaveClass("ng");
    expect(within(rows[2]!).getByText("-1.8%")).toHaveClass("dn");
    expect(rows[2]!.cells[2]).toHaveTextContent("");
    expect(within(rows[3]!).getByText("検証不能")).toHaveClass("eq");
    expect(history.getByText(/新しい順に 4 件\(予測日 × 予測日数\)まで表示しています/)).toBeInTheDocument();
  });

  it("チャートに、ローソク足・移動平均・トレンドライン・ボリンジャーバンド・出来高を描く", async () => {
    renderAt("/stocks/11110", handler());
    await screen.findByTestId("price-chart");
    await waitFor(() => expect(chartState.series.length).toBeGreaterThan(0));
    const candle = chartState.series.find((s) => s.kind === "Candlestick")!;
    // 値がない日は飛ばす
    expect(candle.data).toHaveLength(2);
    // 移動平均3本、上値抵抗線1本、下値支持線2本(抜けた日の前と後)、ボリンジャーバンド5本。
    // トレンドラインの名前は、線の右端(目盛りの横)には出さない
    expect(lines().map((s) => s.options.title)).toEqual(["", "", "", "", "", "", "+2σ", "+1σ", "中心線", "−1σ", "−2σ"]);
    expect(lines().slice(3, 6).map((s) => [s.options.lineStyle, s.options.lineWidth, s.options.color])).toEqual([
      [2, 2, "#c00000"],
      [2, 2, "#0055cc"],
      [2, 1, "rgba(0, 85, 204, 0.35)"],
    ]);
    // 下値支持線は、抜けた日(07-22)までを濃い線、その後を薄い線で描く
    expect(lines()[4]!.data).toEqual([
      { time: "2026-07-21", value: 3800 },
      { time: "2026-07-22", value: 3820 },
    ]);
    expect(lines()[5]!.data).toEqual([
      { time: "2026-07-22", value: 3820 },
      { time: "2026-07-23", value: 3840 },
    ]);
    // 線の名前(左端の近く)と、抜けた日の印と文字
    expect(chartState.markers.map((m) => m.markers.map((x) => [x.time, x.text]))).toEqual([
      [["2026-07-22", "上値抵抗線(接触4回)"]],
      [
        ["2026-07-22", "下値支持線(接触2回)"],
        ["2026-07-22", "下抜け"],
      ],
      [],
    ]);
    // 初めはボリンジャーバンドを隠す
    expect(lines().slice(6).every((s) => s.options.visible === false)).toBe(true);
    expect(lines().slice(0, 6).every((s) => s.options.visible === true)).toBe(true);
    expect(chartState.series.find((s) => s.kind === "Histogram")!.data).toHaveLength(2);
    // 相場の日経平均のチャートと部品を共通にしても、銘柄詳細は今と同じ(出来高の段の見出し、株式分割の注記)
    expect(screen.getByText("出来高(株)")).toBeInTheDocument();
    expect(screen.getByText("株価は株式分割を調整した値。マウス(スマホは指で押した所)の日の値を表示")).toBeInTheDocument();
  });

  it("表示の切り替えで線を出す・隠す。期間を変えると、その期間で読み直す", async () => {
    const user = userEvent.setup();
    const calls = renderAt("/stocks/11110", handler());
    await screen.findByTestId("price-chart");
    await user.click(screen.getByRole("checkbox", { name: "ボリンジャーバンド" }));
    expect(screen.getByText("ボリンジャーバンド ±2σ(20日)")).toBeInTheDocument();
    expect(lines().slice(6).every((s) => s.options.visible === true)).toBe(true);
    await user.click(screen.getByRole("checkbox", { name: "移動平均" }));
    await user.click(screen.getByRole("checkbox", { name: "トレンドライン" }));
    expect(lines().slice(0, 6).every((s) => s.options.visible === false)).toBe(true);
    // トレンドラインを隠すと、線の名前と抜けた日の印も隠す
    expect(chartState.markers.every((m) => m.markers.length === 0)).toBe(true);
    expect(screen.queryByText("5日")).toBeNull();
    await user.click(screen.getByRole("tab", { name: "1年" }));
    await waitFor(() => expect(urlsOf(calls, "/api/stocks/11110/prices").at(-1)).toBe("/api/stocks/11110/prices?period=1Y"));
    expect(urlsOf(calls, "/api/stocks/11110/prices")[0]).toBe("/api/stocks/11110/prices?period=6M");
  });

  it("チャートの高さを小・中・大で切り替え、選んだ高さをブラウザーに残す", async () => {
    window.localStorage.removeItem("stockChartHeight");
    const user = userEvent.setup();
    renderAt("/stocks/11110", handler());
    const chart = await screen.findByTestId("price-chart");
    expect(chart.style.getPropertyValue("--chart-h")).toBe("440px");
    expect(screen.getByRole("tab", { name: "小" })).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByRole("tab", { name: "大" }));
    expect(screen.getByTestId("price-chart").style.getPropertyValue("--chart-h")).toBe("640px");
    expect(window.localStorage.getItem("stockChartHeight")).toBe("L");
    await user.click(screen.getByRole("tab", { name: "中" }));
    expect(screen.getByTestId("price-chart").style.getPropertyValue("--chart-h")).toBe("480px");
    window.localStorage.removeItem("stockChartHeight");
  });

  it("前に選んだ高さで開く", async () => {
    window.localStorage.setItem("stockChartHeight", "L");
    renderAt("/stocks/11110", handler());
    expect((await screen.findByTestId("price-chart")).style.getPropertyValue("--chart-h")).toBe("640px");
    window.localStorage.removeItem("stockChartHeight");
  });

  it("マウスを置いた日の値を枠で出す(右半分なら左に出す)。外れたら消す", async () => {
    renderAt("/stocks/11110", handler());
    await screen.findByTestId("price-chart");
    await waitFor(() => expect(chartState.move).not.toBeNull());
    await userEvent.setup().click(screen.getByRole("checkbox", { name: "ボリンジャーバンド" }));
    act(() => chartState.move?.({ time: "2026-07-22", point: { x: 10, y: 10 } }));
    expect(document.querySelector(".chart-tip")).toHaveTextContent("2026-07-22(水)");
    expect(screen.getByText("安値 3,870.0 ／ 終値 3,945.0")).toBeInTheDocument();
    expect(screen.getByText("移動平均 5日 3,900.0 ／ 25日 3,810.0 ／ 75日 3,710.0")).toBeInTheDocument();
    expect(screen.getByText(/ボリンジャーバンド\(20日\) \+2σ 3,910.0/)).toBeInTheDocument();
    expect(screen.getByText("出来高 900,000株")).toBeInTheDocument();
    act(() => chartState.move?.({ time: "2026-07-23", point: { x: -1, y: 10 } }));
    expect(screen.getByText("出来高 -株")).toBeInTheDocument();
    expect(document.querySelector(".chart-tip")).toHaveClass("right");
    act(() => chartState.move?.({}));
    expect(document.querySelector(".chart-tip")).toBeNull();
  });

  it("株価・トレンドラインがない、予測の対象ではない、決算の予定日がないときは、その旨を出す", async () => {
    renderAt("/stocks/11110", (p) => {
      if (p === "/api/stocks/11110") {
        return {
          body: {
            ...STOCK,
            is_target: false,
            earnings_date: null,
            notes: [],
            notes_date: null,
            pbr: null,
            prev_change: null,
            prev_change_rate: null,
            price_date: null,
          },
        };
      }
      if (p === "/api/stocks/11110/prices") return { body: { ...PRICES, rows: [] } };
      return undefined;
    });
    expect(await screen.findByText(/予測の対象ではありません/)).toBeInTheDocument();
    expect(screen.getByText("予定日はまだ公表されていません")).toBeInTheDocument();
    expect(screen.getByText("備考はありません")).toBeInTheDocument();
    expect(await screen.findByText("株価がありません")).toBeInTheDocument();
    expect(screen.getByText("終値(-)")).toBeInTheDocument();
  });

  it("トレンドラインがなければその旨を出す。予測がなければ「予測はありません」", async () => {
    renderAt("/stocks/11110", (p) => {
      if (p === "/api/stocks/11110") return { body: STOCK };
      if (p === "/api/stocks/11110/prices") return { body: { ...PRICES, trendlines: [] } };
      if (p === "/api/stocks/11110/predictions") {
        return { body: { ...PREDICTIONS, latest: [], history: [], latest_date: null, latest_buy_rank: null } };
      }
      return undefined;
    });
    expect(await screen.findByText("接触2回以上のトレンドラインはありません")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "最新の予測" })).toBeInTheDocument();
    expect(screen.getAllByText("予測はありません")).toHaveLength(2);
    expect(screen.queryByText(/まで表示しています/)).toBeNull();
  });

  it("銘柄がなければエラーを出す。予測・株価を読めなければ、それぞれエラーを出す", async () => {
    renderAt("/stocks/99990", (p) => (p === "/api/stocks/99990" ? { status: 404, body: { detail: "銘柄が見つかりません" } } : undefined));
    expect(await screen.findByRole("heading", { level: 1, name: "銘柄詳細 9999" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("銘柄が見つかりません");
  });

  it("予測・株価を読めなければ、それぞれエラーを出す", async () => {
    renderAt("/stocks/11110", (p) => (p === "/api/stocks/11110" ? { body: STOCK } : { status: 503, body: { detail: "DB に接続できません" } }));
    await waitFor(() => expect(screen.getAllByRole("alert")).toHaveLength(2));
  });
});
