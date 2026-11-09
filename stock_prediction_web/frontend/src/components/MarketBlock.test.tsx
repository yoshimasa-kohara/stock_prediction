import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import type { MarketState } from "../api/types";
import { INDEX_PRICES, INDEX_TREND_TEXT, MARKET_STATE_WITH_TREND } from "../test/apiData";
import { chartState, resetChart } from "../test/chartMock";
import { type Handler, renderPage, urlsOf } from "../test/renderApp";
import MarketBlock, { INDEX_CHART_EMPTY, INDEX_CHART_NOTE } from "./MarketBlock";

vi.mock("lightweight-charts", async () => (await import("../test/chartMock")).createChartMock());

const STATE_TEXT = "相場の状態: 弱い(25日移動平均より上の銘柄 32.1%、直近20営業日の騰落率の中央値 -2.1%)";
const NOTE = "※今の相場の状態を表すもので、この先の上げ下げの予測ではありません";

function handler(body: unknown = INDEX_PRICES): Handler {
  return (p, q) => (p === "/api/index/prices" ? { body: { ...(body as object), period: q.get("period") } } : undefined);
}

function renderBlock(state: MarketState | null = MARKET_STATE_WITH_TREND, h: Handler = handler()) {
  return renderPage("/", "/", <MarketBlock predDate="2026-07-22" state={state} />, h);
}

const block = () => screen.getByTestId("market-block");
const lines = () => chartState.series.filter((s) => s.kind === "Line");

describe("相場のブロック", () => {
  beforeEach(() => {
    resetChart();
    window.localStorage.removeItem("indexChartHeight");
    window.localStorage.removeItem("stockChartHeight");
  });

  it("見出し・相場の状態・日経平均の向き・注意書き・チャートの順に出す。予測日までの日経平均を読む", async () => {
    const calls = renderBlock();
    await screen.findByTestId("price-chart");
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("日経平均(2026-07-22)");
    const text = block().textContent ?? "";
    const order = ["日経平均(2026-07-22)", STATE_TEXT, INDEX_TREND_TEXT, NOTE, "1か月", INDEX_CHART_NOTE];
    const positions = order.map((t) => text.indexOf(t));
    expect(positions.every((p) => p >= 0)).toBe(true);
    expect([...positions].sort((a, b) => a - b)).toEqual(positions);
    expect(urlsOf(calls, "/api/index/prices")).toEqual(["/api/index/prices?date=2026-07-22&period=6M"]);
  });

  it("初期表示は銘柄詳細と同じ(6か月、高さは小、移動平均とトレンドラインは ON、ボリンジャーバンドは OFF)", async () => {
    renderBlock();
    expect((await screen.findByTestId("price-chart")).style.getPropertyValue("--chart-h")).toBe("440px");
    expect(screen.getByRole("tab", { name: "6か月" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "小" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("checkbox", { name: "移動平均" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "トレンドライン" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "ボリンジャーバンド" })).not.toBeChecked();
  });

  it("日経平均の向きの色: 上向きは赤、下向きは青、横ばいは色なし。相場の状態には色を付けない", async () => {
    renderBlock();
    await screen.findByTestId("price-chart");
    expect(screen.getByText("上向き")).toHaveStyle({ color: "#c00000" });
    expect(screen.getByText("下向き")).toHaveStyle({ color: "#0055cc" });
    expect(screen.getByText("横ばい").style.color).toBe("");
    expect(screen.getByText(STATE_TEXT).style.color).toBe("");
    expect(screen.getByText(STATE_TEXT)).not.toHaveClass("up", "dn");
  });

  it("出来高の段と、マウスの位置の枠の出来高の行を出さない。回帰直線は重ねない。注記は日経平均の文言", async () => {
    renderBlock();
    await screen.findByTestId("price-chart");
    await waitFor(() => expect(chartState.move).not.toBeNull());
    expect(chartState.series.some((s) => s.kind === "Histogram")).toBe(false);
    expect(screen.queryByText("出来高(株)")).toBeNull();
    // 移動平均3本、下値支持線1本、ボリンジャーバンド5本だけ(10・20・60日の回帰直線の線はない)
    expect(lines().map((s) => s.options.title)).toEqual(["", "", "", "", "+2σ", "+1σ", "中心線", "−1σ", "−2σ"]);
    act(() => chartState.move?.({ time: "2026-07-22", point: { x: 10, y: 10 } }));
    const tip = document.querySelector(".chart-tip");
    expect(tip).toHaveTextContent("2026-07-22(水)");
    expect(tip).toHaveTextContent("安値 39,950.0 ／ 終値 40,210.0");
    expect(tip).not.toHaveTextContent("出来高");
    expect(screen.getByText(INDEX_CHART_NOTE)).toBeInTheDocument();
    expect(screen.queryByText(/株式分割/)).toBeNull();
  });

  it("選んだ高さは、銘柄詳細とは別の名前(indexChartHeight)でブラウザーに残す", async () => {
    const user = userEvent.setup();
    renderBlock();
    await screen.findByTestId("price-chart");
    await user.click(screen.getByRole("tab", { name: "大" }));
    expect(screen.getByTestId("price-chart").style.getPropertyValue("--chart-h")).toBe("640px");
    expect(window.localStorage.getItem("indexChartHeight")).toBe("L");
    expect(window.localStorage.getItem("stockChartHeight")).toBeNull();
  });

  it("前に選んだ高さ(indexChartHeight)で開く。銘柄詳細の高さは使わない", async () => {
    window.localStorage.setItem("indexChartHeight", "M");
    window.localStorage.setItem("stockChartHeight", "L");
    renderBlock();
    expect((await screen.findByTestId("price-chart")).style.getPropertyValue("--chart-h")).toBe("480px");
  });

  it("期間を変えると、予測日はそのままで、その期間で読み直す", async () => {
    const user = userEvent.setup();
    const calls = renderBlock();
    await screen.findByTestId("price-chart");
    await user.click(screen.getByRole("tab", { name: "1年" }));
    await waitFor(() =>
      expect(urlsOf(calls, "/api/index/prices").at(-1)).toBe("/api/index/prices?date=2026-07-22&period=1Y"),
    );
  });

  it("日経平均の向きがない日(index_trend が null)も、相場の状態とチャートは出す。向きの行は出さない", async () => {
    renderBlock({ ...MARKET_STATE_WITH_TREND!, index_trend: null });
    expect(await screen.findByTestId("price-chart")).toBeInTheDocument();
    expect(screen.getByText(STATE_TEXT)).toBeInTheDocument();
    expect(screen.queryByText(/日経平均の向き/)).toBeNull();
  });

  it("相場の状態が計算できない日は「相場の状態: -」を出し、チャートは出す", async () => {
    renderBlock(null);
    expect(await screen.findByTestId("price-chart")).toBeInTheDocument();
    expect(screen.getByText("相場の状態: -")).toBeInTheDocument();
  });

  it("日経平均のデータが1行もなければ「日経平均のデータがありません」だけを出す(チャートの操作は出さない)", async () => {
    renderBlock(MARKET_STATE_WITH_TREND, handler({ ...INDEX_PRICES, rows: [], trendlines: [] }));
    expect(await screen.findByText(INDEX_CHART_EMPTY)).toBeInTheDocument();
    expect(screen.queryByTestId("price-chart")).toBeNull();
    expect(screen.queryByRole("tab", { name: "1か月" })).toBeNull();
    expect(screen.queryByText(INDEX_CHART_NOTE)).toBeNull();
    expect(screen.queryByText("接触2回以上のトレンドラインはありません")).toBeNull();
    // 相場の状態は出す
    expect(screen.getByText(STATE_TEXT)).toBeInTheDocument();
  });
});
