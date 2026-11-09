import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { CANDIDATES, INDEX_PRICES, INDEX_TREND_TEXT, MARKET_STATE_WITH_TREND, PRED_DATES } from "../test/apiData";
import { resetChart } from "../test/chartMock";
import { type Handler, renderApp, urlsOf } from "../test/renderApp";

vi.mock("lightweight-charts", async () => (await import("../test/chartMock")).createChartMock());

/** 相場のブロックの日経平均(/api/index/prices)も返す */
function withIndex(handler: Handler, index: unknown = INDEX_PRICES): Handler {
  return (p, q, m, b) => (p === "/api/index/prices" ? { body: index } : handler(p, q, m, b));
}

/** 分かれた要素にまたがる文言(日経平均の向きの行は、向きの文字だけ色を付けるため) */
const wholeText = (text: string) => (_: string, el: Element | null) => el?.tagName === "DIV" && el.textContent === text;

const NOTE = "※今の相場の状態を表すもので、この先の上げ下げの予測ではありません";

describe("買い候補", () => {
  beforeEach(() => resetChart());

  it("最新の予測日の買い候補を、全ての予測日数の列で出す", async () => {
    renderApp("/candidates", (p) => {
      if (p === "/api/prediction-dates") return { body: PRED_DATES };
      if (p === "/api/candidates") return { body: CANDIDATES };
      return undefined;
    });
    expect(await screen.findByText("モデル 20260718 ／ 予測した銘柄 3,961 件")).toBeInTheDocument();
    expect(screen.getByText(/2026-07-22\(水\) の買い候補\(上位20銘柄。メールと同じ内容\)/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "予測日" })).toHaveValue("2026-07-22");
    for (const h of ["5日後", "10日後", "20日後", "60日後"]) expect(screen.getByText(h)).toBeInTheDocument();
    // 備考の色(赤・橙・青)
    expect(screen.getByText("ストップ高")).toHaveClass("r");
    expect(screen.getByText("流動性注意")).toHaveClass("o");
    expect(screen.getByText("短期デッドクロス発生")).toHaveClass("bl");
    expect(screen.getByText("4,189.2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "次の日 ▶" })).toBeDisabled();
  });

  it("「前の日」で予測がある前の日に移り、その日の買い候補を読む", async () => {
    const user = userEvent.setup();
    const calls = renderApp("/candidates", (p, q) => {
      if (p === "/api/prediction-dates") return { body: PRED_DATES };
      if (p === "/api/candidates") return { body: { ...CANDIDATES, pred_date: q.get("date") ?? CANDIDATES.pred_date } };
      return undefined;
    });
    await screen.findByText(/2026-07-22\(水\) の買い候補/);
    await user.click(screen.getByRole("button", { name: "◀ 前の日" }));
    expect(await screen.findByText(/2026-07-21\(火\) の買い候補/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "◀ 前の日" }));
    await screen.findByText(/2026-07-17\(金\) の買い候補/);
    expect(screen.getByRole("button", { name: "◀ 前の日" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "次の日 ▶" }));
    await screen.findByText(/2026-07-21\(火\) の買い候補/);
    expect(urlsOf(calls, "/api/candidates")).toEqual([
      "/api/candidates",
      "/api/candidates?date=2026-07-21",
      "/api/candidates?date=2026-07-17",
      "/api/candidates?date=2026-07-21",
    ]);
  });

  it("買い候補が0件なら、その旨を出す。予測日の一覧を読めなければエラーを出す", async () => {
    renderApp(
      "/candidates?date=2026-07-22",
      withIndex((p) => {
        if (p === "/api/prediction-dates") return { status: 500, body: { detail: "サーバーでエラーが発生しました" } };
        if (p === "/api/candidates") return { body: { ...CANDIDATES, candidates: [] } };
        return undefined;
      }),
    );
    expect(await screen.findByText("買い候補はありません")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("サーバーでエラーが発生しました");
  });

  it("買い候補(業種分散)に切り替えると、同じ日の業種分散の買い候補を読む。業種の列を出す", async () => {
    const user = userEvent.setup();
    const calls = renderApp("/candidates?date=2026-07-21", (p, q) => {
      if (p === "/api/prediction-dates") return { body: PRED_DATES };
      if (p === "/api/candidates") {
        return { body: { ...CANDIDATES, pred_date: q.get("date") ?? CANDIDATES.pred_date, kind: q.get("kind") ?? "top" } };
      }
      return undefined;
    });
    await screen.findByText(/2026-07-21\(火\) の買い候補\(上位20銘柄/);
    expect(screen.getByRole("columnheader", { name: "業種" })).toBeInTheDocument();
    expect(screen.getByText("卸売業")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "買い候補" })).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByRole("tab", { name: "買い候補(業種分散)" }));
    expect(
      await screen.findByText(/2026-07-21\(火\) の買い候補\(業種分散\)\(同じ33業種は3銘柄まで、上位30銘柄。メールと同じ内容\)/),
    ).toBeInTheDocument();
    expect(screen.getByText(/同じ33業種の銘柄は上から3銘柄までにする/)).toBeInTheDocument();
    // 日付を変えても、業種分散のまま
    await user.click(screen.getByRole("button", { name: "◀ 前の日" }));
    await screen.findByText(/2026-07-17\(金\) の買い候補\(業種分散\)/);
    expect(urlsOf(calls, "/api/candidates")).toEqual([
      "/api/candidates?date=2026-07-21",
      "/api/candidates?date=2026-07-21&kind=sector",
      "/api/candidates?date=2026-07-17&kind=sector",
    ]);
    await user.click(screen.getByRole("tab", { name: "買い候補" }));
    await waitFor(() => expect(urlsOf(calls, "/api/candidates").at(-1)).toBe("/api/candidates?date=2026-07-17"));
  });

  it("上昇確率の条件を置き換えた日は、API の注記を出す。置き換えていない日は出さない", async () => {
    const note = "本日は上昇確率50%以上の銘柄が足りないため、上昇確率の上位25%で選びました";
    const user = userEvent.setup();
    renderApp("/candidates?date=2026-07-21", (p, q) => {
      if (p === "/api/prediction-dates") return { body: PRED_DATES };
      if (p === "/api/candidates") {
        const shown = q.get("date") ?? CANDIDATES.pred_date;
        return { body: { ...CANDIDATES, pred_date: shown, fallback_note: shown === "2026-07-21" ? note : null } };
      }
      return undefined;
    });
    expect(await screen.findByText(note)).toHaveClass("warn");
    await user.click(screen.getByRole("button", { name: "◀ 前の日" }));
    await screen.findByText(/2026-07-17\(金\) の買い候補/);
    expect(screen.queryByText(note)).not.toBeInTheDocument();
  });

  it("「相場」のブロックを、予測日の選択の下、タブの上に出す。表の上には相場の行を出さない", async () => {
    const calls = renderApp(
      "/candidates?date=2026-07-21",
      withIndex((p, q) => {
        if (p === "/api/prediction-dates") return { body: PRED_DATES };
        if (p === "/api/candidates") {
          const shown = q.get("date") ?? CANDIDATES.pred_date;
          const market_state =
            shown === "2026-07-21" ? CANDIDATES.market_state : { state: "強い" as const, above25: 0.7, ret20: 0.034, index_trend: null };
          return { body: { ...CANDIDATES, pred_date: shown, market_state } };
        }
        return undefined;
      }),
    );
    const block = await screen.findByTestId("market-block");
    await screen.findByTestId("price-chart");
    expect(within(block).getByRole("heading", { level: 2 })).toHaveTextContent("日経平均(2026-07-21)");
    expect(
      within(block).getByText("相場の状態: 弱い(25日移動平均より上の銘柄 32.1%、直近20営業日の騰落率の中央値 -2.1%)"),
    ).toBeInTheDocument();
    expect(within(block).getByText(NOTE)).toBeInTheDocument();
    // 予測日の選択 → 相場 → タブ → 表の説明
    const picker = screen.getByRole("textbox", { name: "予測日" });
    const tabs = screen.getByRole("tablist", { name: "買い候補の種類" });
    const info = screen.getByText(/の買い候補\(上位20銘柄/);
    expect(picker.compareDocumentPosition(block) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(block.compareDocumentPosition(tabs) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(tabs.compareDocumentPosition(info) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getAllByText(/^相場の状態:/)).toHaveLength(1);
    expect(document.querySelectorAll(".mstate")).toHaveLength(1);
    expect(urlsOf(calls, "/api/index/prices")).toEqual(["/api/index/prices?date=2026-07-21&period=6M"]);
    // 前の日に移ると、その日の相場の状態と、その日までの日経平均を出す
    await userEvent.setup().click(screen.getByRole("button", { name: "◀ 前の日" }));
    expect(
      await screen.findByText("相場の状態: 強い(25日移動平均より上の銘柄 70.0%、直近20営業日の騰落率の中央値 +3.4%)"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("market-block")).toHaveTextContent("日経平均(2026-07-17)");
    await waitFor(() =>
      expect(urlsOf(calls, "/api/index/prices").at(-1)).toBe("/api/index/prices?date=2026-07-17&period=6M"),
    );
  });

  it("買い候補(業種分散)のタブでも、相場のブロックは同じ内容(日経平均の向きも出す)。タブを切り替えても日経平均は読み直さない", async () => {
    const user = userEvent.setup();
    const calls = renderApp(
      "/candidates",
      withIndex((p, q) => {
        if (p === "/api/prediction-dates") return { body: PRED_DATES };
        if (p === "/api/candidates")
          return { body: { ...CANDIDATES, kind: q.get("kind") ?? "top", market_state: MARKET_STATE_WITH_TREND } };
        return undefined;
      }),
    );
    await screen.findByTestId("price-chart");
    const before = screen.getByTestId("market-block").textContent;
    expect(screen.getByText(wholeText(INDEX_TREND_TEXT))).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /業種分散/ }));
    await screen.findByText(/の買い候補\(業種分散\)/);
    expect(screen.getByTestId("market-block").textContent).toBe(before);
    expect(screen.getByText(wholeText(INDEX_TREND_TEXT))).toBeInTheDocument();
    expect(screen.getAllByText(/^相場の状態:/)).toHaveLength(1);
    expect(urlsOf(calls, "/api/index/prices")).toEqual(["/api/index/prices?date=2026-07-22&period=6M"]);
  });

  it("日経平均がない日は、相場の状態と「日経平均のデータがありません」を出し、向きの行は出さない", async () => {
    renderApp(
      "/candidates",
      withIndex(
        (p) => {
          if (p === "/api/prediction-dates") return { body: PRED_DATES };
          if (p === "/api/candidates") return { body: CANDIDATES };
          return undefined;
        },
        { ...INDEX_PRICES, rows: [], trendlines: [] },
      ),
    );
    expect(await screen.findByText("日経平均のデータがありません")).toBeInTheDocument();
    expect(screen.getByText(/^相場の状態: 弱い/)).toBeInTheDocument();
    expect(screen.queryByText(/日経平均の向き/)).not.toBeInTheDocument();
  });

  it("予測がない日は、API のメッセージを出す", async () => {
    renderApp("/candidates?date=2026-07-25", (p) => {
      if (p === "/api/prediction-dates") return { body: PRED_DATES };
      if (p === "/api/candidates") return { status: 404, body: { detail: "この日の予測はありません" } };
      return undefined;
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("この日の予測はありません");
    // 予測がない日は、相場のブロックも出さない
    expect(screen.queryByTestId("market-block")).toBeNull();
    expect(screen.getByRole("textbox", { name: "予測日" })).toHaveValue("2026-07-25");
  });
});
