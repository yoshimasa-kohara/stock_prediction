import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";

import App from "../App";
import SettingsProvider from "../context/SettingsProvider";
import type { CandidatesResponse } from "../api/types";
import { ACCURACY, ACCURACY_EMPTY, CANDIDATES, RANK, HOLDINGS, INDEX_PRICES, INDEX_TREND_TEXT, MARKET_STATE_WITH_TREND } from "../test/apiData";
import { resetChart } from "../test/chartMock";
import { SETTINGS, STATUS, mockApi } from "../test/fixtures";
import { renderApp, urlsOf } from "../test/renderApp";

vi.mock("lightweight-charts", async () => (await import("../test/chartMock")).createChartMock());

const STATE_TEXT = "相場の状態: 弱い(25日移動平均より上の銘柄 32.1%、直近20営業日の騰落率の中央値 -2.1%)";

/** トップの API(相場のブロックの日経平均も返す) */
function renderTop(candidates: CandidatesResponse = CANDIDATES, index: unknown = INDEX_PRICES) {
  return renderApp("/", (p) => {
    if (p === "/api/candidates") return { body: candidates };
    if (p === "/api/holdings") return { body: HOLDINGS };
    if (p === "/api/accuracy") return { body: ACCURACY };
    if (p === "/api/accuracy/rank") return { body: RANK };
    if (p === "/api/index/prices") return { body: index };
    return undefined;
  });
}

/** 分かれた要素にまたがる文言(日経平均の向きの行は、向きの文字だけ色を付けるため) */
const wholeText = (text: string) => (_: string, el: Element | null) => el?.tagName === "DIV" && el.textContent === text;

describe("トップ", () => {
  beforeEach(() => resetChart());

  it("最新の予測日・バッチの結果・件数・上位5銘柄・保有銘柄・的中率を出す", async () => {
    const calls = renderApp("/", (p) => {
      if (p === "/api/candidates") return { body: CANDIDATES };
      if (p === "/api/holdings") return { body: HOLDINGS };
      if (p === "/api/accuracy") return { body: ACCURACY };
      if (p === "/api/accuracy/rank") return { body: RANK };
      return undefined;
    });
    expect(await screen.findByText("2026-07-22(水)")).toBeInTheDocument();
    expect(screen.getByText("モデル 20260718(学習の基準日 2026-07-18)")).toBeInTheDocument();
    expect(await screen.findByText("3 件 / 2 件")).toBeInTheDocument();
    expect(screen.getByText("売却の判定 1件、乗り換えを検討 0件")).toBeInTheDocument();
    expect(screen.getAllByText("まだ動いていません")).toHaveLength(2);
    // 上昇確率のずれの注意
    expect(screen.getByText(/20日後・60〜70%の帯: 確率の平均 63.4% \/ 実際に上がった割合 45.4%\(30,120件\)/)).toHaveTextContent(
      "ずれ -18.0 ポイント",
    );
    // 保有銘柄の表(判定の色)
    expect(screen.getByText("売却(下降予測、市場の中央値も下回る予測)")).toHaveClass("jr");
    expect(screen.getByText("保持(注意: 上昇予測だが下降しています)")).toHaveClass("jo");
    expect(screen.getAllByText("1134")[0]).toBeInTheDocument();
    // 的中率
    expect(await screen.findByText("全銘柄の的中率")).toBeInTheDocument();
    expect(urlsOf(calls, "/api/holdings")).toEqual(["/api/holdings?include_sold=false"]);
    expect(urlsOf(calls, "/api/accuracy")).toEqual(["/api/accuracy?horizon=20"]);
    expect(screen.getByRole("link", { name: "20銘柄すべてを見る ›" })).toHaveAttribute("href", "/candidates");
    // 順位別 的中率(直近1か月だけ。期間のタブは出さない)。的中率の画面へのリンクは2か所
    expect(await screen.findByRole("table", { name: "買い候補 順位別 的中率" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "直近1か月" })).not.toBeInTheDocument();
    expect(urlsOf(calls, "/api/accuracy/rank")).toEqual(["/api/accuracy/rank"]);
    const rank = screen.getByRole("heading", { name: "買い候補 順位別 的中率" }).closest(".panel") as HTMLElement;
    const acc = screen.getByRole("heading", { name: /^的中率\(主となる日数/ }).closest(".panel") as HTMLElement;
    for (const panel of [acc, rank]) {
      expect(within(panel).getByRole("link", { name: "的中率の画面で見る ›" })).toHaveAttribute("href", "/accuracy");
    }
    expect(acc.compareDocumentPosition(rank) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("「相場」のブロックを、上昇確率のずれの下、買い候補(上位5銘柄)の上に出す。最新の予測日の日経平均を読む", async () => {
    const calls = renderTop({ ...CANDIDATES, market_state: MARKET_STATE_WITH_TREND });
    const block = await screen.findByTestId("market-block");
    await screen.findByTestId("price-chart");
    expect(within(block).getByRole("heading", { level: 2 })).toHaveTextContent("日経平均(2026-07-22)");
    const warn = screen.getByText(/上昇確率のずれ\(直近3か月\)/);
    const candidates = screen.getByRole("heading", { name: "買い候補(上位5銘柄)" });
    expect(warn.compareDocumentPosition(block) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(block.compareDocumentPosition(candidates) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // 相場の状態・日経平均の向き・注意書きは、ブロックの中だけに出す(買い候補の表の上には出さない)
    expect(within(block).getByText(STATE_TEXT)).toBeInTheDocument();
    expect(within(block).getByText(wholeText(INDEX_TREND_TEXT))).toBeInTheDocument();
    expect(screen.getAllByText(/^相場の状態:/)).toHaveLength(1);
    expect(screen.getAllByText("※今の相場の状態を表すもので、この先の上げ下げの予測ではありません")).toHaveLength(1);
    expect(candidates.closest(".panel")?.querySelector(".mstate")).toBeNull();
    expect(urlsOf(calls, "/api/index/prices")).toEqual(["/api/index/prices?date=2026-07-22&period=6M"]);
  });

  it("日経平均の向きの色は、上向きは赤、下向きは青、横ばいは色なし", async () => {
    renderTop({ ...CANDIDATES, market_state: MARKET_STATE_WITH_TREND });
    expect(await screen.findByText("上向き")).toHaveStyle({ color: "#c00000" });
    expect(screen.getByText("下向き")).toHaveStyle({ color: "#0055cc" });
    expect(screen.getByText("横ばい").style.color).toBe("");
  });

  it("日経平均の向きが null の日は、向きの行を出さない(「-」も出さない)。相場の状態とチャートは出す", async () => {
    renderTop();
    await screen.findByTestId("price-chart");
    expect(screen.getByText(STATE_TEXT)).not.toHaveClass("up", "dn");
    expect(screen.queryByText(/日経平均の向き/)).not.toBeInTheDocument();
  });

  it("相場の状態が計算できない日は「相場の状態: -」を出す。日経平均のデータがなければ、その旨だけを出す", async () => {
    renderTop({ ...CANDIDATES, market_state: null }, { ...INDEX_PRICES, rows: [], trendlines: [] });
    expect(await screen.findByText("相場の状態: -")).toBeInTheDocument();
    expect(await screen.findByText("日経平均のデータがありません")).toBeInTheDocument();
    expect(screen.queryByTestId("price-chart")).toBeNull();
  });

  it("バッチの結果があれば、状態と時刻を出す。予測・保有・検証がなければ、その旨を出す", async () => {
    mockApi((p) => {
      if (p === "/api/settings") return { body: SETTINGS };
      if (p === "/api/status") {
        return {
          body: {
            ...STATUS,
            latest_pred_date: null,
            model_version: null,
            model_base_date: null,
            latest_daily: {
              run_id: 1,
              process_name: "DAILY_BATCH",
              target_date: "2026-07-22",
              status: "SUCCESS",
              started_at: "2026-07-22T19:00:00+09:00",
              finished_at: "2026-07-22T19:41:10+09:00",
            },
            latest_weekly: {
              run_id: 2,
              process_name: "WEEKLY_BATCH",
              target_date: "2026-07-18",
              status: "FAILED",
              started_at: "2026-07-18T19:00:00+09:00",
              finished_at: null,
            },
          },
        };
      }
      if (p === "/api/candidates") return { status: 404, body: { detail: "予測がまだありません" } };
      if (p === "/api/holdings") return { body: { ...HOLDINGS, groups: [] } };
      if (p === "/api/accuracy") return { body: ACCURACY_EMPTY };
      if (p === "/api/accuracy/rank") return { body: { ...RANK, periods: [] } };
      return undefined;
    });
    render(
      <MemoryRouter>
        <SettingsProvider>
          <App />
        </SettingsProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByText("予測はまだありません")).toBeInTheDocument();
    expect(await screen.findByText("19:00 開始 / 19:41 終了")).toBeInTheDocument();
    expect(screen.getByText("成功")).toHaveClass("ok");
    expect(screen.getByText("失敗")).toHaveClass("ng");
    expect(screen.getByText("19:00 開始")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("予測がまだありません");
    expect(await screen.findByText("保有銘柄はありません")).toBeInTheDocument();
    // 的中率と順位別 的中率の2か所
    expect(await screen.findAllByText("検証結果はまだありません")).toHaveLength(2);
    expect(screen.getByText("- 件 / 0 件")).toBeInTheDocument();
  });

  it("買い候補の表は、主となる日数だけを出し、銘柄名から銘柄詳細へ移れる", async () => {
    renderApp("/", (p) => (p === "/api/candidates" ? { body: CANDIDATES } : undefined));
    const link = await screen.findByRole("link", { name: "サンプル銀行A" });
    expect(link).toHaveAttribute("href", "/stocks/11110");
    const table = link.closest("table");
    expect(table).not.toBeNull();
    expect(within(table!).getByText("20日後 市場比")).toBeInTheDocument();
    expect(within(table!).queryByText("5日後")).toBeNull();
  });
});
