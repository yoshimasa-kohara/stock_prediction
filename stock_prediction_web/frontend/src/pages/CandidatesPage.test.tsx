import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import type { CandidatesResponse } from "../api/types";
import {
  CANDIDATES,
  INDEX_PRICES,
  INDEX_TREND_TEXT,
  MARKET_STATE_WITH_TREND,
  PRED_DATES,
} from "../test/apiData";
import { resetChart } from "../test/chartMock";
import { type Handler, renderApp, urlsOf } from "../test/renderApp";

vi.mock("lightweight-charts", async () =>
  (await import("../test/chartMock")).createChartMock(),
);

const RANGE = "/api/candidates/range";

/**
 * 日経平均のブロックに必要な API も返す。ブロックは、トップと同じく /api/candidates(日付なし)の
 * 最新の予測日の内容と、日経平均(/api/index/prices)を使う
 */
function withIndex(
  handler: Handler,
  index: unknown = INDEX_PRICES,
  latest: CandidatesResponse = CANDIDATES,
): Handler {
  return (p, q, m, b) =>
    p === "/api/index/prices"
      ? { body: index }
      : p === "/api/candidates"
        ? { body: latest }
        : handler(p, q, m, b);
}

/** 予測日1日分の返り値(日付以外は CANDIDATES と同じ) */
function day(
  pred_date: string,
  over: Partial<CandidatesResponse> = {},
): CandidatesResponse {
  return { ...CANDIDATES, pred_date, ...over };
}

/** 予測日の一覧と、期間の API(from〜to の日だけを古い順に返す)を返すハンドラー */
function rangeHandler(
  all: CandidatesResponse[],
  over: (
    d: CandidatesResponse,
    kind: string,
  ) => Partial<CandidatesResponse> = () => ({}),
): Handler {
  return (p, q) => {
    if (p === "/api/prediction-dates")
      return { body: { dates: all.map((d) => d.pred_date) } };
    if (p === RANGE) {
      const kind = q.get("kind") ?? "top";
      const from = q.get("from") ?? "";
      const to = q.get("to") ?? "";
      return {
        body: all
          .filter((d) => d.pred_date >= from && d.pred_date <= to)
          .map((d) => ({ ...d, kind, ...over(d, kind) })),
      };
    }
    return undefined;
  };
}

/** 分かれた要素にまたがる文言(日経平均の向きの行は、向きの文字だけ色を付けるため) */
const wholeText = (text: string) => (_: string, el: Element | null) =>
  el?.tagName === "DIV" && el.textContent === text;

const NOTE =
  "※今の相場の状態を表すもので、この先の上げ下げの予測ではありません";

const THREE = ["2026-07-17", "2026-07-21", "2026-07-22"].map((d) => day(d));
/** 11/3 は祝日で予測なし。6日分 */
const SIX = [
  "2026-10-30",
  "2026-11-02",
  "2026-11-04",
  "2026-11-05",
  "2026-11-06",
  "2026-11-09",
].map((d) => day(d));

describe("買い候補", () => {
  beforeEach(() => resetChart());

  it("期間の指定がなければ、最新の予測日を終了日にして、予測がある日を数えて5日分を出す(祝日は飛ばす)", async () => {
    const calls = renderApp("/candidates", rangeHandler(SIX));
    expect(
      await screen.findByText(/2026-11-09\(月\) の買い候補/),
    ).toBeInTheDocument();
    expect(urlsOf(calls, RANGE)).toEqual([
      `${RANGE}?from=2026-11-02&to=2026-11-09`,
    ]);
    expect(screen.getByRole("textbox", { name: "予測日(開始)" })).toHaveValue(
      "2026-11-02",
    );
    expect(screen.getByRole("textbox", { name: "予測日(終了)" })).toHaveValue(
      "2026-11-09",
    );
    expect(
      screen.getAllByRole("heading", { level: 2, name: /の買い候補/ }),
    ).toHaveLength(5);
    expect(
      screen.queryByRole("button", { name: "◀ 前の日" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "次の日 ▶" }),
    ).not.toBeInTheDocument();
  });

  it("予測日が5日に満たなければ、一番古い予測日から", async () => {
    const calls = renderApp("/candidates", rangeHandler(THREE));
    await screen.findByText(/2026-07-22\(水\) の買い候補/);
    expect(urlsOf(calls, RANGE)).toEqual([
      `${RANGE}?from=2026-07-17&to=2026-07-22`,
    ]);
  });

  it("開始日だけ指定したら、終了日は最新の予測日", async () => {
    const calls = renderApp("/candidates?from=2026-07-21", rangeHandler(THREE));
    await screen.findByText(/2026-07-22\(水\) の買い候補/);
    expect(urlsOf(calls, RANGE)).toEqual([
      `${RANGE}?from=2026-07-21&to=2026-07-22`,
    ]);
  });

  it("date は受け付けない(無視して初期値の期間)", async () => {
    const calls = renderApp("/candidates?date=2026-07-21", rangeHandler(THREE));
    await screen.findByText(/2026-07-22\(水\) の買い候補/);
    expect(urlsOf(calls, RANGE)).toEqual([
      `${RANGE}?from=2026-07-17&to=2026-07-22`,
    ]);
  });

  it("予測日は新しい日が上(目次も表も降順)。目次のリンクは各ブロックへ。全ての予測日数の列を出す", async () => {
    renderApp("/candidates", rangeHandler(THREE));
    await screen.findByRole("heading", {
      level: 2,
      name: /2026-07-22\(水\) の買い候補/,
    });
    const links = within(
      screen.getByRole("navigation", { name: "目次" }),
    ).getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual([
      "#d2026-07-22",
      "#d2026-07-21",
      "#d2026-07-17",
    ]);
    expect(
      screen
        .getAllByRole("heading", { level: 2, name: /の買い候補/ })
        .map((h) => h.id),
    ).toEqual(["d2026-07-22", "d2026-07-21", "d2026-07-17"]);
    expect(
      screen.getAllByText("モデル 20260718", { exact: false }),
    ).toHaveLength(3);
    expect(screen.getAllByText(/予測した銘柄 3,961 件/)).toHaveLength(3);
    expect(screen.getAllByText("5日後")).toHaveLength(3);
    expect(screen.getAllByText("ストップ高")[0]).toHaveClass("r");
    expect(screen.getAllByText("流動性注意")[0]).toHaveClass("g");
    expect(screen.getAllByText("短期デッドクロス発生")[0]).toHaveClass("bl");
    expect(screen.getAllByText("4,189.2")).toHaveLength(3);
  });

  it("期間を変えて「表示」を押すと、読み直す", async () => {
    const user = userEvent.setup();
    const calls = renderApp("/candidates", rangeHandler(THREE));
    const from = await screen.findByRole("textbox", { name: "予測日(開始)" });
    await waitFor(() => expect(from).toHaveValue("2026-07-17"));
    await user.clear(from);
    await user.type(from, "2026-07-21{Enter}");
    await user.click(screen.getByRole("button", { name: "表示" }));
    await waitFor(() =>
      expect(
        screen.getAllByRole("heading", { level: 2, name: /の買い候補/ }),
      ).toHaveLength(2),
    );
    expect(urlsOf(calls, RANGE).at(-1)).toBe(
      `${RANGE}?from=2026-07-21&to=2026-07-22`,
    );
  });

  it.each(["予測日(開始)", "予測日(終了)"])(
    "%s のカレンダーでは、予測がない日は選べず、予測がある日は選べる",
    async (label) => {
      const user = userEvent.setup();
      renderApp(
        "/candidates?from=2026-07-17&to=2026-07-22",
        withIndex(rangeHandler(THREE)),
      );
      await screen.findAllByRole("heading", { level: 2, name: /の買い候補/ });
      await user.click(
        screen.getByRole("button", { name: `${label}をカレンダーで選ぶ` }),
      );
      const cal = screen.getByRole("dialog", { name: `${label}のカレンダー` });
      // 20日は予測なし、21日は予測あり
      expect(within(cal).getByText("20").closest("button")).toBeDisabled();
      expect(within(cal).getByText("21").closest("button")).toBeEnabled();
    },
  );

  it("期間が1か月を超えたら、API のメッセージを出し、表を出さない", async () => {
    renderApp(
      "/candidates?from=2026-06-01&to=2026-07-22",
      withIndex((p) => {
        if (p === "/api/prediction-dates") return { body: PRED_DATES };
        if (p === RANGE)
          return {
            status: 400,
            body: { detail: "期間は1か月以内にしてください" },
          };
        return undefined;
      }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "期間は1か月以内にしてください",
    );
    expect(
      screen.queryByRole("heading", { level: 2, name: /の買い候補/ }),
    ).not.toBeInTheDocument();
    // 表の所はエラーの表示のまま。日経平均のブロックは出す
    expect(await screen.findByTestId("market-block")).toBeInTheDocument();
  });

  it("開始日が終了日より後なら、API のメッセージを出す", async () => {
    renderApp("/candidates?from=2026-07-22&to=2026-07-01", (p) => {
      if (p === RANGE)
        return {
          status: 400,
          body: { detail: "開始日が終了日より後になっています" },
        };
      return p === "/api/prediction-dates" ? { body: PRED_DATES } : undefined;
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "開始日が終了日より後になっています",
    );
  });

  it("期間の中に予測がある日が1日もなければ「指定した期間に予測はありません」。日経平均のブロックは出す", async () => {
    renderApp(
      "/candidates?from=2026-07-25&to=2026-07-31",
      withIndex(rangeHandler(THREE)),
    );
    expect(
      await screen.findByText("指定した期間に予測はありません"),
    ).toBeInTheDocument();
    expect(await screen.findByTestId("market-block")).toHaveTextContent(
      "日経平均(2026-07-22)",
    );
    expect(
      screen.queryByRole("navigation", { name: "目次" }),
    ).not.toBeInTheDocument();
  });

  it("予測が1件もなければ(予測日の一覧が空)、表を読まずに同じ文を出す", async () => {
    const calls = renderApp("/candidates", rangeHandler([]));
    expect(
      await screen.findByText("指定した期間に予測はありません"),
    ).toBeInTheDocument();
    expect(urlsOf(calls, RANGE)).toEqual([]);
  });

  it("予測日の一覧を読めなければエラーを出す。買い候補が0件の日は、その旨を日ごとに出す", async () => {
    renderApp(
      "/candidates?from=2026-07-21&to=2026-07-22",
      withIndex((p, q) => {
        if (p === "/api/prediction-dates")
          return {
            status: 500,
            body: { detail: "サーバーでエラーが発生しました" },
          };
        if (p === RANGE)
          return {
            body: [day("2026-07-21"), day("2026-07-22", { candidates: [] })],
          };
        return rangeHandler(THREE)(p, q, "GET", undefined);
      }),
    );
    expect(await screen.findByText("買い候補はありません")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "サーバーでエラーが発生しました",
    );
    // 0件の日(22日)だけ。21日の表は出ている
    expect(screen.getAllByText("買い候補はありません")).toHaveLength(1);
    expect(screen.getAllByText("サンプル銀行A")).toHaveLength(1);
  });

  it("買い候補(業種分散)に切り替えても、期間はそのまま。業種の列を出す", async () => {
    const user = userEvent.setup();
    const calls = renderApp(
      "/candidates?from=2026-07-21&to=2026-07-22",
      rangeHandler(THREE),
    );
    await screen.findByText(/2026-07-21\(火\) の買い候補/);
    expect(screen.getAllByRole("columnheader", { name: "業種" })).toHaveLength(
      2,
    );
    expect(screen.getAllByText("卸売業").length).toBeGreaterThan(0);
    expect(screen.getByRole("tab", { name: "買い候補" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await user.click(screen.getByRole("tab", { name: "買い候補(業種分散)" }));
    expect(
      await screen.findByText(/2026-07-21\(火\) の買い候補\(業種分散\)/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/同じ33業種の銘柄は上から3銘柄までにする/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/同じ33業種は3銘柄まで、上位30銘柄。メールと同じ内容/),
    ).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "予測日(開始)" })).toHaveValue(
      "2026-07-21",
    );
    expect(screen.getByRole("textbox", { name: "予測日(終了)" })).toHaveValue(
      "2026-07-22",
    );
    await user.click(screen.getByRole("tab", { name: "買い候補" }));
    await waitFor(() =>
      expect(urlsOf(calls, RANGE)).toEqual([
        `${RANGE}?from=2026-07-21&to=2026-07-22`,
        `${RANGE}?from=2026-07-21&to=2026-07-22&kind=sector`,
        `${RANGE}?from=2026-07-21&to=2026-07-22`,
      ]),
    );
  });

  it("期間を指定していなくても、タブを切り替えた後の期間は同じ(初期値の期間のまま)", async () => {
    const user = userEvent.setup();
    const calls = renderApp("/candidates", rangeHandler(SIX));
    await screen.findByText(/2026-11-09\(月\) の買い候補/);
    await user.click(screen.getByRole("tab", { name: /業種分散/ }));
    await screen.findAllByText(/の買い候補\(業種分散\)/);
    expect(urlsOf(calls, RANGE)).toEqual([
      `${RANGE}?from=2026-11-02&to=2026-11-09`,
      `${RANGE}?from=2026-11-02&to=2026-11-09&kind=sector`,
    ]);
  });

  it("上昇確率の条件を置き換えた日だけ、その日の見出しの下に注記を出す", async () => {
    const note =
      "本日は上昇確率50%以上の銘柄が足りないため、上昇確率の上位25%で選びました";
    renderApp(
      "/candidates",
      rangeHandler(THREE, (d) => ({
        fallback_note: d.pred_date === "2026-07-21" ? note : null,
      })),
    );
    const warn = await screen.findByText(note);
    expect(warn).toHaveClass("warn");
    expect(screen.getAllByText(note)).toHaveLength(1);
    const heading = screen.getByRole("heading", {
      level: 2,
      name: /2026-07-21\(火\)/,
    });
    expect(
      heading.compareDocumentPosition(warn) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      screen
        .getByRole("heading", { level: 2, name: /2026-07-17\(金\)/ })
        .compareDocumentPosition(warn),
    ).toBe(Node.DOCUMENT_POSITION_PRECEDING);
  });

  it("日経平均のブロックは、トップと同じ最新の日のもの。期間の指定の下、タブの上に出す", async () => {
    const calls = renderApp(
      "/candidates",
      withIndex(
        rangeHandler(THREE, () => ({
          market_state: {
            state: "強い" as const,
            above25: 0.7,
            ret20: 0.034,
            index_trend: null,
          },
        })),
      ),
    );
    const block = await screen.findByTestId("market-block");
    await screen.findByTestId("price-chart");
    expect(within(block).getByRole("heading", { level: 2 })).toHaveTextContent(
      "日経平均(2026-07-22)",
    );
    // 期間の中の各日の相場の状態(強い)ではなく、最新の日(CANDIDATES。弱い)の内容
    expect(
      within(block).getByText(
        "相場の状態: 弱い(25日移動平均より上の銘柄 32.1%、直近20営業日の騰落率の中央値 -2.1%)",
      ),
    ).toBeInTheDocument();
    expect(within(block).getByText(NOTE)).toBeInTheDocument();
    // 期間の指定 → 日経平均 → タブ → 表の説明
    const picker = screen.getByRole("textbox", { name: "予測日(開始)" });
    const tabs = screen.getByRole("tablist", { name: "買い候補の種類" });
    const info = screen.getByText(/予測日ごとの買い候補\(上位20銘柄/);
    expect(
      picker.compareDocumentPosition(block) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      block.compareDocumentPosition(tabs) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      tabs.compareDocumentPosition(info) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.getAllByText(/^相場の状態:/)).toHaveLength(1);
    expect(document.querySelectorAll(".mstate")).toHaveLength(1);
    expect(urlsOf(calls, "/api/index/prices")).toEqual([
      "/api/index/prices?date=2026-07-22&period=6M",
    ]);
  });

  it("過去の期間を指定しても、日経平均のブロックは最新の日のもの(日付・相場の状態・チャート)", async () => {
    const calls = renderApp(
      "/candidates?from=2026-07-17&to=2026-07-21",
      withIndex(rangeHandler(THREE)),
    );
    await screen.findByText(/2026-07-21\(火\) の買い候補/);
    const block = await screen.findByTestId("market-block");
    await screen.findByTestId("price-chart");
    expect(within(block).getByRole("heading", { level: 2 })).toHaveTextContent(
      "日経平均(2026-07-22)",
    );
    expect(within(block).getByText(/^相場の状態: 弱い/)).toBeInTheDocument();
    expect(urlsOf(calls, "/api/index/prices")).toEqual([
      "/api/index/prices?date=2026-07-22&period=6M",
    ]);
    // 最新の日は、期間とは別に日付なしで読む
    expect(urlsOf(calls, "/api/candidates")).toEqual(["/api/candidates"]);
  });

  it("日経平均の向きも最新の日のもの。期間やタブを切り替えても、日経平均のブロックは読み直さない", async () => {
    const user = userEvent.setup();
    const calls = renderApp(
      "/candidates",
      withIndex(
        rangeHandler(THREE),
        INDEX_PRICES,
        day("2026-07-22", { market_state: MARKET_STATE_WITH_TREND }),
      ),
    );
    await screen.findByTestId("price-chart");
    const before = screen.getByTestId("market-block").textContent;
    expect(screen.getByText(wholeText(INDEX_TREND_TEXT))).toBeInTheDocument();
    // タブの切り替え
    await user.click(screen.getByRole("tab", { name: /業種分散/ }));
    await screen.findAllByText(/の買い候補\(業種分散\)/);
    // 予測日の変更
    const from = screen.getByRole("textbox", { name: "予測日(開始)" });
    await user.clear(from);
    await user.type(from, "2026-07-21");
    await user.click(screen.getByRole("button", { name: "表示" }));
    await waitFor(() =>
      expect(urlsOf(calls, RANGE)).toContain(
        `${RANGE}?from=2026-07-21&to=2026-07-22&kind=sector`,
      ),
    );
    expect(screen.getByTestId("market-block").textContent).toBe(before);
    expect(screen.getByText(wholeText(INDEX_TREND_TEXT))).toBeInTheDocument();
    expect(screen.getAllByText(/^相場の状態:/)).toHaveLength(1);
    expect(urlsOf(calls, "/api/index/prices")).toEqual([
      "/api/index/prices?date=2026-07-22&period=6M",
    ]);
    expect(urlsOf(calls, "/api/candidates")).toEqual(["/api/candidates"]);
  });

  it("日経平均がない日は、相場の状態と「日経平均のデータがありません」を出し、向きの行は出さない", async () => {
    renderApp(
      "/candidates",
      withIndex(rangeHandler(THREE), {
        ...INDEX_PRICES,
        rows: [],
        trendlines: [],
      }),
    );
    expect(
      await screen.findByText("日経平均のデータがありません"),
    ).toBeInTheDocument();
    expect(screen.getByText(/^相場の状態: 弱い/)).toBeInTheDocument();
    expect(screen.queryByText(/日経平均の向き/)).not.toBeInTheDocument();
  });
});

describe("買い候補: 表の上の説明文への自動スクロール", () => {
  const scroll = vi.fn();
  /** scrollIntoView を呼ばれた要素の id */
  const scrolledIds = () =>
    scroll.mock.contexts.map((el) => (el as HTMLElement).id);

  beforeEach(() => {
    resetChart();
    scroll.mockClear();
    // jsdom には scrollIntoView がないのでモックする
    Element.prototype.scrollIntoView = scroll;
  });

  it("初期表示で、表の上の説明文へ1回だけスクロールする", async () => {
    renderApp("/candidates", withIndex(rangeHandler(THREE)));
    await screen.findByText(/2026-07-22\(水\) の買い候補/);
    await waitFor(() => expect(scroll).toHaveBeenCalledTimes(1));
    expect(scrolledIds()).toEqual(["candidates-top"]);
    expect(scroll).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
  });

  it("「表示」を押すと、新しい期間の一番新しい予測日へ。同じ期間で押し直しても動く", async () => {
    const user = userEvent.setup();
    renderApp("/candidates", withIndex(rangeHandler(THREE)));
    await waitFor(() => expect(scroll).toHaveBeenCalledTimes(1));
    const to = screen.getByRole("textbox", { name: "予測日(終了)" });
    await user.clear(to);
    await user.type(to, "2026-07-21{Enter}");
    await user.click(screen.getByRole("button", { name: "表示" }));
    await waitFor(() => expect(scroll).toHaveBeenCalledTimes(2));
    expect(scrolledIds().at(-1)).toBe("candidates-top");
    await user.click(screen.getByRole("button", { name: "表示" }));
    await waitFor(() => expect(scroll).toHaveBeenCalledTimes(3));
    expect(scrolledIds().at(-1)).toBe("candidates-top");
  });

  it("タブを切り替えてもスクロールしない", async () => {
    const user = userEvent.setup();
    renderApp("/candidates", withIndex(rangeHandler(THREE)));
    await waitFor(() => expect(scroll).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("tab", { name: "買い候補(業種分散)" }));
    await screen.findByText(/2026-07-22\(水\) の買い候補\(業種分散\)/);
    expect(scroll).toHaveBeenCalledTimes(1);
  });

  it("エラーのとき、予測がない期間のときはスクロールしない", async () => {
    renderApp(
      "/candidates?from=2026-07-25&to=2026-07-31",
      withIndex(rangeHandler(THREE)),
    );
    await screen.findByText("指定した期間に予測はありません");
    expect(scroll).not.toHaveBeenCalled();
  });

  it("読み込みがエラーのときはスクロールしない", async () => {
    renderApp(
      "/candidates?from=2026-07-22&to=2026-07-01",
      withIndex((p) =>
        p === RANGE
          ? {
              status: 400,
              body: { detail: "開始日が終了日より後になっています" },
            }
          : p === "/api/prediction-dates"
            ? { body: PRED_DATES }
            : undefined,
      ),
    );
    await screen.findByRole("alert");
    expect(scroll).not.toHaveBeenCalled();
  });
});
