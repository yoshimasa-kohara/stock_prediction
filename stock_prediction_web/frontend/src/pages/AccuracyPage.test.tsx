import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type ReactElement, cloneElement } from "react";
import { vi } from "vitest";

import { ACCURACY, ACCURACY_EMPTY, DAILY, RANK } from "../test/apiData";
import { type Handler, renderPage, urlsOf } from "../test/renderApp";
import AccuracyPage from "./AccuracyPage";

// jsdom では大きさが 0 になり、グラフを描かないので、決まった大きさで描く
vi.mock("recharts", async (importOriginal) => {
  const mod = await importOriginal<typeof import("recharts")>();
  return {
    ...mod,
    ResponsiveContainer: ({ children }: { children: ReactElement<{ width?: number; height?: number }> }) => (
      <div>{cloneElement(children, { width: 800, height: 280 })}</div>
    ),
  };
});

function handler(p: string, q: URLSearchParams) {
  if (p === "/api/accuracy") return { body: { ...ACCURACY, horizon: Number(q.get("horizon")) } };
  if (p === "/api/accuracy/daily") return { body: DAILY };
  if (p === "/api/accuracy/rank") return { body: RANK };
  return undefined;
}

/** 表の本文の、行ごとのセルの文字 */
function bodyCells(table: HTMLElement): string[][] {
  return within(table)
    .getAllByRole("row")
    .slice(1)
    .map((tr) => within(tr).getAllByRole("cell").map((td) => td.textContent ?? ""));
}

/** この画面だけを表示する(App の lazy を通さない。test/renderApp.tsx の renderPage) */
function renderAt(path: string, handler?: Handler) {
  return renderPage(path, "/accuracy", <AccuracyPage />, handler);
}

describe("的中率", () => {
  it("期間の集計(全銘柄・買い候補・上昇確率が高い銘柄・保有銘柄)を出す", async () => {
    renderAt("/accuracy", handler);
    expect(await screen.findByRole("heading", { name: "期間の集計(20日後)" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "20日後(主)" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("textbox", { name: "集計の基準日" })).toHaveValue("2026-07-22");
    expect(screen.getAllByText("53.8%(1,000)")).toHaveLength(3);
    expect(screen.getAllByText("62.0%(60)")).toHaveLength(3);
    expect(screen.getAllByText("70.0%(30)")).toHaveLength(2);
    expect(screen.getAllByText("+5.9%")[0]).toHaveClass("up");
    // 買い候補(業種分散)と業種比
    expect(screen.getAllByText("60.0%(90)")).toHaveLength(3);
    expect(screen.getByText("買い候補: 業種の中央値を上回った割合")).toBeInTheDocument();
    expect(screen.getAllByText("57.9%")).toHaveLength(3); // 買い候補の業種の中央値を上回った割合(3期間)
    expect(screen.getByText("買い候補(業種分散): 業種の中央値との差(平均)")).toBeInTheDocument();
    expect(screen.getAllByText("-1.6%")[0]).toHaveClass("dn");
    // 保有銘柄(1か月は保有していた銘柄がない)
    expect(screen.getAllByText("60.0%(20)")).toHaveLength(2);
    expect(screen.getAllByText("-1.2%")[0]).toHaveClass("dn");
  });

  it("上昇確率の確かさを、帯ごとに出し、注意の帯は赤い行にする。期間を切り替えられる", async () => {
    const user = userEvent.setup();
    renderAt("/accuracy", handler);
    const panel = within(
      (await screen.findByRole("heading", { name: "上昇確率の確かさ(20日後、直近3か月)" })).closest(".panel") as HTMLElement,
    );
    expect(panel.getByText("60〜70%").closest("tr")).toHaveClass("hl");
    expect(panel.getByText("-18.0")).toHaveClass("dn");
    expect(panel.getByText("+3.0")).toHaveClass("up");
    expect(panel.getByText("0.0")).toHaveClass("eq");
    expect(panel.getByText("80〜90%").closest("tr")?.lastChild).toHaveTextContent("-");
    expect(panel.getByText(/件数が500件以上で、ずれが5ポイントより大きい帯/)).toBeInTheDocument();
    await user.click(panel.getByRole("tab", { name: "全期間" }));
    expect(screen.getByRole("heading", { name: "上昇確率の確かさ(20日後、全期間)" })).toBeInTheDocument();
    expect(panel.queryByText("60〜70%")).toBeNull();
  });

  it("予測日ごとの推移のグラフを描き、期間を変えると読み直す", async () => {
    const user = userEvent.setup();
    const calls = renderAt("/accuracy", handler);
    expect(await screen.findByTestId("accuracy-chart")).toBeInTheDocument();
    expect(await screen.findByText("買い候補: 市場の中央値との差(左の目盛り)")).toBeInTheDocument();
    expect(screen.getByText("全銘柄: 的中率(破線、右の目盛り)")).toBeInTheDocument();
    const from = screen.getByRole("textbox", { name: "推移の期間(開始)" });
    expect(from).toHaveValue("2026-04-22");
    await user.clear(from);
    await user.type(from, "2026-06-01{Enter}");
    const to = screen.getByRole("textbox", { name: "推移の期間(終了)" });
    await user.clear(to);
    await user.type(to, "2026-07-01{Enter}");
    await waitFor(() =>
      expect(urlsOf(calls, "/api/accuracy/daily").at(-1)).toBe("/api/accuracy/daily?horizon=20&from=2026-06-01&to=2026-07-01"),
    );
  });

  it("予測日数・基準日を変えると、その条件で読み直す", async () => {
    const user = userEvent.setup();
    const calls = renderAt("/accuracy", handler);
    await screen.findByRole("heading", { name: "期間の集計(20日後)" });
    await user.click(screen.getByRole("tab", { name: "5日後" }));
    expect(await screen.findByRole("heading", { name: "期間の集計(5日後)" })).toBeInTheDocument();
    const date = screen.getByRole("textbox", { name: "集計の基準日" });
    await user.clear(date);
    await user.type(date, "2026-07-21{Enter}");
    await waitFor(() => expect(urlsOf(calls, "/api/accuracy").at(-1)).toBe("/api/accuracy?horizon=5&date=2026-07-21"));
    expect(urlsOf(calls, "/api/accuracy/daily")).toContain("/api/accuracy/daily?horizon=5");
  });

  it("買い候補 順位別 的中率: 直近1か月を初期に選び、予測日数ごとに帯の的中率と差を出す。件数0は「-」", async () => {
    renderAt("/accuracy", handler);
    expect(await screen.findByRole("heading", { name: "買い候補 順位別 的中率" })).toBeInTheDocument();
    const tabs = await screen.findByRole("tablist", { name: "買い候補 順位別 的中率の期間" });
    expect(within(tabs).getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "直近1か月",
      "直近3か月",
      "直近6か月",
      "直近1年",
      "直近2年",
      "全期間",
    ]);
    expect(within(tabs).getByRole("tab", { name: "直近1か月" })).toHaveAttribute("aria-selected", "true");
    const table = screen.getByRole("table", { name: "買い候補 順位別 的中率" });
    expect(within(table).getAllByRole("columnheader").map((th) => th.textContent)).toEqual([
      "予測日数",
      "1〜20位",
      "1〜5位",
      "6〜10位",
      "11〜15位",
      "16〜20位",
      "1〜5位と16〜20位の差",
    ]);
    // スマホでは「予測日数」の列を左に固定する
    expect(within(table).getByRole("columnheader", { name: "予測日数" })).toHaveClass("fix");
    expect(bodyCells(table)).toEqual([
      ["5日", "50.0%", "80.0%", "40.0%", "40.0%", "20.0%", "+60.0"],
      ["10日", "50.0%", "80.0%", "40.0%", "40.0%", "20.0%", "+60.0"],
      ["20日", "50.0%", "80.0%", "40.0%", "40.0%", "20.0%", "+60.0"],
      ["60日", "50.0%", "80.0%", "40.0%", "40.0%", "20.0%", "+60.0"],
    ]);
    expect(within(table).getAllByText("+60.0")[0]).toHaveClass("up");
    expect(screen.getByText(/^上昇の的中率: 予測日数後の株価が予測日の株価より高ければ的中/)).toBeInTheDocument();
    expect(screen.getByText("検証済みの件数(1〜20位): 5日 20件 / 10日 20件 / 20日 20件 / 60日 20件")).toBeInTheDocument();
  });

  it("買い候補 順位別 的中率: 全期間は、差のマイナス・件数0を「-」で出す", async () => {
    const user = userEvent.setup();
    renderAt("/accuracy", handler);
    const tabs = await screen.findByRole("tablist", { name: "買い候補 順位別 的中率の期間" });
    await user.click(within(tabs).getByRole("tab", { name: "全期間" }));
    const table = screen.getByRole("table", { name: "買い候補 順位別 的中率" });
    expect(bodyCells(table)).toEqual([
      ["5日", "61.9%", "63.6%", "63.6%", "60.0%", "60.0%", "+3.6"],
      ["10日", "50.1%", "50.0%", "50.0%", "50.0%", "50.4%", "-0.4"],
      ["20日", "50.0%", "60.0%", "33.3%", "50.0%", "-", "-"],
      ["60日", "-", "-", "-", "-", "-", "-"],
    ]);
    expect(within(table).getByText("+3.6")).toHaveClass("up");
    expect(within(table).getByText("-0.4")).toHaveClass("dn");
    expect(screen.getByText("検証済みの件数(1〜20位): 5日 42件 / 10日 1,500件 / 20日 10件 / 60日 0件")).toBeInTheDocument();
  });

  it("買い候補 順位別 的中率: 期間のタブで、API を読み直さずに切り替える。基準日を変えると読み直す", async () => {
    const user = userEvent.setup();
    const calls = renderAt("/accuracy", handler);
    const tabs = await screen.findByRole("tablist", { name: "買い候補 順位別 的中率の期間" });
    await user.click(within(tabs).getByRole("tab", { name: "直近3か月" }));
    expect(within(tabs).getByRole("tab", { name: "直近3か月" })).toHaveAttribute("aria-selected", "true");
    const table = screen.getByRole("table", { name: "買い候補 順位別 的中率" });
    // 直近3か月は 1〜20位が 21件で10件の的中、1〜5位 80.0% − 16〜20位 20.0%
    expect(bodyCells(table)[0]).toEqual(["5日", "47.6%", "80.0%", "40.0%", "40.0%", "20.0%", "+60.0"]);
    expect(screen.getByText("検証済みの件数(1〜20位): 5日 21件 / 10日 21件 / 20日 21件 / 60日 21件")).toBeInTheDocument();
    expect(urlsOf(calls, "/api/accuracy/rank")).toEqual(["/api/accuracy/rank"]);
    // 上の予測日数のタブを変えても、読み直さない(4行のまま)
    await user.click(screen.getByRole("tab", { name: "5日後" }));
    await screen.findByRole("heading", { name: "期間の集計(5日後)" });
    expect(urlsOf(calls, "/api/accuracy/rank")).toEqual(["/api/accuracy/rank"]);
    const date = screen.getByRole("textbox", { name: "集計の基準日" });
    await user.clear(date);
    await user.type(date, "2026-07-21{Enter}");
    await waitFor(() => expect(urlsOf(calls, "/api/accuracy/rank").at(-1)).toBe("/api/accuracy/rank?date=2026-07-21"));
  });

  it("買い候補 順位別 的中率: 検証結果がなければ、その旨を出す", async () => {
    renderAt("/accuracy", (p, q) =>
      p === "/api/accuracy/rank" ? { body: { ...RANK, summary_date: null, periods: [] } } : handler(p, q),
    );
    expect(await screen.findByText("検証結果はまだありません")).toBeInTheDocument();
  });

  it("買い候補 順位別 的中率を読めなければ、エラーを出す", async () => {
    renderAt("/accuracy", (p, q) =>
      p === "/api/accuracy/rank" ? { status: 503, body: { detail: "DB に接続できません" } } : handler(p, q),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("DB に接続できません");
  });

  it("検証結果がなければ、その旨を出す", async () => {
    renderAt("/accuracy", (p) => (p === "/api/accuracy" ? { body: ACCURACY_EMPTY } : undefined));
    expect(await screen.findByText("検証結果はまだありません")).toBeInTheDocument();
  });

  it("推移・確かさがなければ、その旨を出す。保有していた銘柄がなければ、その旨を出す", async () => {
    renderAt("/accuracy", (p) => {
      if (p === "/api/accuracy") {
        return {
          body: { ...ACCURACY, calibration: {}, periods: ACCURACY.periods.map((x) => ({ ...x, holding: null, top: null })) },
        };
      }
      if (p === "/api/accuracy/daily") return { body: { ...DAILY, points: [] } };
      return undefined;
    });
    expect(await screen.findByText("この期間に答え合わせが済んだ予測はありません")).toBeInTheDocument();
    expect(screen.getByText("集計はありません")).toBeInTheDocument();
    expect(screen.getByText("この期間に保有していた銘柄はありません")).toBeInTheDocument();
  });

  it("読めなければエラーを出す", async () => {
    renderAt("/accuracy", (p) =>
      p === "/api/accuracy" ? { status: 400, body: { detail: "予測日数は 5・10・20・60 のどれかを指定してください" } } : undefined,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("予測日数は 5・10・20・60 のどれかを指定してください");
  });

  it("推移を読めなければ、エラーを出す", async () => {
    renderAt("/accuracy", (p, q) =>
      p === "/api/accuracy/daily" ? { status: 400, body: { detail: "開始日が終了日より後になっています" } } : handler(p, q),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("開始日が終了日より後になっています");
  });
});
