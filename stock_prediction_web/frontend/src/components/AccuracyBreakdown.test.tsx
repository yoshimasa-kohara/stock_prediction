import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";

import SettingsProvider from "../context/SettingsProvider";
import WatchStocksProvider from "../context/WatchStocksProvider";
import { SECTOR_ACCURACY, STOCK_ACCURACY, STOCK_ACCURACY_MORE, WATCH_CODES } from "../test/apiData";
import { type FakeResponse, type FetchCall, SETTINGS, mockApi } from "../test/fixtures";
import { sortSectorRows } from "../lib/accuracySort";
import { SectorAccuracyPanel, StockAccuracyPanel } from "./AccuracyBreakdown";

type Handler = (path: string, query: URLSearchParams) => FakeResponse | undefined;

function renderPanel(panel: "stocks" | "sectors", handler: Handler, props: { date?: string; horizon?: number } = {}): FetchCall[] {
  const calls = mockApi((p, q) => {
    if (p === "/api/settings") return { body: SETTINGS };
    if (p === "/api/watch-stocks/codes") return { body: WATCH_CODES };
    return handler(p, q);
  });
  const horizon = props.horizon ?? 20;
  render(
    <MemoryRouter>
      <SettingsProvider>
        <WatchStocksProvider>
          {panel === "stocks" ? (
            <StockAccuracyPanel date={props.date} horizon={horizon} />
          ) : (
            <SectorAccuracyPanel date={props.date} horizon={horizon} />
          )}
        </WatchStocksProvider>
      </SettingsProvider>
    </MemoryRouter>,
  );
  return calls;
}

function urls(calls: FetchCall[], path: string): string[] {
  return calls.filter((c) => c.url.split("?")[0] === path).map((c) => c.url);
}

/** 表の本文の、行ごとのセルの文字 */
function bodyCells(table: HTMLElement): string[][] {
  const rows = Array.from(table.querySelectorAll<HTMLTableRowElement>("tbody tr"));
  return rows.map((tr) => Array.from(tr.cells).map((td) => td.textContent ?? ""));
}

const stocks: Handler = (p, q) => {
  if (p !== "/api/accuracy/stocks") return undefined;
  const body = q.get("offset") ? STOCK_ACCURACY_MORE : STOCK_ACCURACY;
  return { body: { ...body, period: q.get("period"), segment: q.get("segment"), sort: q.get("sort"), order: q.get("order") } };
};

describe("銘柄別の的中率", () => {
  it("直近3か月・全銘柄を初期に選び、件数の下限を表の上に書く。列と書式", async () => {
    const calls = renderPanel("stocks", stocks);
    const table = await screen.findByRole("table", { name: "銘柄別の的中率" });
    expect(urls(calls, "/api/accuracy/stocks")).toEqual([
      "/api/accuracy/stocks?horizon=20&period=3M&segment=all&direction=up&sort=beat_rate&order=desc",
    ]);
    expect(screen.getByRole("heading", { name: "銘柄別の的中率(20日後、直近3か月)" })).toBeInTheDocument();
    const periodTabs = screen.getByRole("tablist", { name: "銘柄別の的中率の期間" });
    expect(within(periodTabs).getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "直近1か月", "直近3か月", "直近6か月", "直近1年", "直近2年", "全期間",
    ]); // prettier-ignore
    expect(within(periodTabs).getByRole("tab", { name: "直近3か月" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "全銘柄" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText(/件数が20件以上の銘柄だけを出す\(3銘柄\)。50件ずつ表示/)).toBeInTheDocument();
    expect(within(table).getAllByRole("columnheader").map((th) => th.textContent)).toEqual([
      "順位", "コード", "銘柄名", "市場", "業種", "件数", "的中率", "市場の中央値を上回った割合 ▼", "騰落率の平均",
      "予測株価の平均誤差率",
    ]); // prettier-ignore
    expect(bodyCells(table)).toEqual([
      ["1", "1111", "☆サンプル銀行A", "プライム", "銀行業", "61", "75.4%", "62.3%", "+3.1%", "4.6%"],
      ["2", "1112", "★サンプル商事A", "スタンダード", "卸売業", "1,234", "60.0%", "-", "-0.8%", "-"],
    ]);
    expect(within(table).getByText("+3.1%")).toHaveClass("up");
    expect(within(table).getByText("-0.8%")).toHaveClass("dn");
    // 予想の向きは上昇予想が初期値。数え方と初期の並びを表の下に書く
    const dirTabs = screen.getByRole("tablist", { name: "銘柄別の的中率の予想の向き" });
    expect(within(dirTabs).getAllByRole("tab").map((t) => t.textContent)).toEqual(["上昇予想", "下降予想"]);
    expect(within(dirTabs).getByRole("tab", { name: "上昇予想" })).toHaveAttribute("aria-selected", "true");
    expect(dirTabs.closest("div.cond")).toHaveTextContent(/^予想の向き/);
    expect(
      screen.getByText(
        /上がると予想した日\(上昇確率が50\.0%以上\)だけで数える。初期の並びと順位は市場の中央値を上回った割合の高い順\(同じなら騰落率の平均の高い順\)/,
      ),
    ).toBeInTheDocument();
    // 銘柄名は新しいタブで銘柄詳細を開く。スマホでは銘柄名の列を左に固定する
    const link = within(table).getByRole("link", { name: "サンプル銀行A" });
    expect(link).toHaveAttribute("href", "/stocks/11110");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.closest("td")).toHaveClass("fix");
    expect(within(table).getByRole("columnheader", { name: "銘柄名" })).toHaveClass("fix");
    // ☆・★(検討中銘柄に追加するボタン)
    expect(within(table).getByRole("button", { name: "サンプル銀行Aを検討中銘柄に追加" })).toHaveTextContent("☆");
    expect(within(table).getByRole("button", { name: "サンプル商事Aの検討中銘柄のメモを編集" })).toHaveTextContent("★");
  });

  it("期間のタブを開いたときに、その期間の分だけ読む。対象を切り替えると、件数の下限が変わる", async () => {
    const user = userEvent.setup();
    const calls = renderPanel("stocks", stocks, { date: "2026-07-21", horizon: 5 });
    await screen.findByRole("table", { name: "銘柄別の的中率" });
    expect(urls(calls, "/api/accuracy/stocks")).toHaveLength(1);
    await user.click(screen.getByRole("tab", { name: "全期間" }));
    await waitFor(() =>
      expect(urls(calls, "/api/accuracy/stocks").at(-1)).toBe(
        "/api/accuracy/stocks?date=2026-07-21&horizon=5&period=ALL&segment=all&direction=up&sort=beat_rate&order=desc",
      ),
    );
    expect(await screen.findByRole("heading", { name: "銘柄別の的中率(5日後、全期間)" })).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "買い候補に入った日だけ" }));
    await waitFor(() => expect(urls(calls, "/api/accuracy/stocks").at(-1)).toContain("period=ALL&segment=top"));
    expect(screen.getByText(/件数が5件以上の銘柄だけを出す/)).toBeInTheDocument();
    expect(urls(calls, "/api/accuracy/stocks")).toHaveLength(3);
  });

  it("予想の向きを切り替えると、その条件で読み直し、並べ替えは初期の並びに戻す", async () => {
    const user = userEvent.setup();
    const calls = renderPanel("stocks", stocks);
    await screen.findByRole("table", { name: "銘柄別の的中率" });
    await user.click(screen.getByRole("button", { name: "銘柄名" }));
    await waitFor(() => expect(urls(calls, "/api/accuracy/stocks").at(-1)).toContain("sort=name&order=asc"));
    await user.click(screen.getByRole("tab", { name: "下降予想" }));
    await waitFor(() =>
      expect(urls(calls, "/api/accuracy/stocks").at(-1)).toBe(
        "/api/accuracy/stocks?horizon=20&period=3M&segment=all&direction=down&sort=hit_rate&order=desc",
      ),
    );
    expect(await screen.findByRole("button", { name: "的中率 ▼" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "下降予想" })).toHaveAttribute("aria-selected", "true");
    expect(
      screen.getByText(/下がると予想した日\(上昇確率が50\.0%未満\)だけで数える。初期の並びと順位は的中率の高い順\(同じなら騰落率の平均の低い順\)/),
    ).toBeInTheDocument();
    // 上昇予想に戻すと、市場の中央値を上回った割合の高い順
    await user.click(screen.getByRole("tab", { name: "上昇予想" }));
    await waitFor(() => expect(urls(calls, "/api/accuracy/stocks").at(-1)).toContain("direction=up&sort=beat_rate&order=desc"));
  });

  it("見出しを押すと並べ替える(数は大きい順から、文字は小さい順から。もう一度押すと逆)", async () => {
    const user = userEvent.setup();
    const calls = renderPanel("stocks", stocks);
    await screen.findByRole("table", { name: "銘柄別の的中率" });
    await user.click(screen.getByRole("button", { name: "件数" }));
    await waitFor(() => expect(urls(calls, "/api/accuracy/stocks").at(-1)).toContain("sort=verified_count&order=desc"));
    const th = (await screen.findByRole("button", { name: "件数 ▼" })).closest("th");
    expect(th).toHaveAttribute("aria-sort", "descending");
    await user.click(screen.getByRole("button", { name: "件数 ▼" }));
    await waitFor(() => expect(urls(calls, "/api/accuracy/stocks").at(-1)).toContain("sort=verified_count&order=asc"));
    await user.click(await screen.findByRole("button", { name: "銘柄名" }));
    await waitFor(() => expect(urls(calls, "/api/accuracy/stocks").at(-1)).toContain("sort=name&order=asc"));
    expect(await screen.findByRole("button", { name: "銘柄名 ▲" })).toBeInTheDocument();
    // 的中率の見出しは、大きい順から
    await user.click(screen.getByRole("button", { name: "的中率" }));
    await waitFor(() => expect(urls(calls, "/api/accuracy/stocks").at(-1)).toContain("sort=hit_rate&order=desc"));
  });

  it("「もっと見る」で続きを出す。条件を変えると、続きは消える", async () => {
    const user = userEvent.setup();
    const calls = renderPanel("stocks", stocks);
    const table = await screen.findByRole("table", { name: "銘柄別の的中率" });
    await user.click(screen.getByRole("button", { name: "もっと見る" }));
    await waitFor(() => expect(bodyCells(table)).toHaveLength(3));
    expect(urls(calls, "/api/accuracy/stocks").at(-1)).toContain("offset=2");
    // 5桁目が 0 でない銘柄コードは5桁のまま
    expect(bodyCells(table)[2]!.slice(0, 2)).toEqual(["3", "11145"]);
    expect(within(table).getByText("0.0%")).toHaveClass("eq");
    expect(screen.queryByRole("button", { name: "もっと見る" })).toBeNull();
    await user.click(screen.getByRole("tab", { name: "直近1か月" }));
    await waitFor(() => expect(bodyCells(screen.getByRole("table", { name: "銘柄別の的中率" }))).toHaveLength(2));
    expect(screen.getByRole("button", { name: "もっと見る" })).toBeInTheDocument();
  });

  it("続きを読めなければ、エラーを出す", async () => {
    const user = userEvent.setup();
    renderPanel("stocks", (p, q) =>
      q.get("offset") ? { status: 503, body: { detail: "DB に接続できません" } } : stocks(p, q),
    );
    await screen.findByRole("table", { name: "銘柄別の的中率" });
    await user.click(screen.getByRole("button", { name: "もっと見る" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("DB に接続できません");
  });

  it("銘柄がなければその旨を出す。集計がまだないときは銘柄数を出さない。読めなければエラーを出す", async () => {
    renderPanel("stocks", () => ({ body: { ...STOCK_ACCURACY, summary_date: null, rows: [], total: 0, has_more: false } }));
    expect(await screen.findByText("条件に合う銘柄はありません")).toBeInTheDocument();
    expect(screen.queryByText(/銘柄\)。50件ずつ/)).toBeNull();
    expect(screen.queryByRole("button", { name: "もっと見る" })).toBeNull();
  });

  it("読めなければエラーを出す", async () => {
    renderPanel("stocks", () => ({ status: 400, body: { detail: "期間は 1M・3M のどれかを指定してください" } }));
    expect(await screen.findByRole("alert")).toHaveTextContent("期間は 1M・3M のどれかを指定してください");
    expect(screen.queryByRole("table")).toBeNull();
  });
});

describe("API を再起動する前(/api/settings に新しい項目がない)", () => {
  it("期間・予想の向きのタブは出ないが、画面は止まらない", async () => {
    const old = {
      ...SETTINGS,
      web: {
        ...SETTINGS.web,
        accuracy_periods: undefined,
        accuracy_breakdown_default_period: undefined,
        accuracy_directions: undefined,
        accuracy_direction_default: undefined,
      },
    };
    mockApi((p) => {
      if (p === "/api/settings") return { body: old };
      if (p === "/api/watch-stocks/codes") return { body: WATCH_CODES };
      return undefined;
    });
    render(
      <MemoryRouter>
        <SettingsProvider>
          <WatchStocksProvider>
            <StockAccuracyPanel horizon={20} />
          </WatchStocksProvider>
        </SettingsProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("指定された API はありません");
    expect(within(screen.getByRole("tablist", { name: "銘柄別の的中率の期間" })).queryAllByRole("tab")).toHaveLength(0);
    expect(within(screen.getByRole("tablist", { name: "銘柄別の的中率の予想の向き" })).queryAllByRole("tab")).toHaveLength(0);
  });
});

const sectors: Handler = (p, q) =>
  p === "/api/accuracy/sectors" ? { body: { ...SECTOR_ACCURACY, period: q.get("period") } } : undefined;

describe("業種別の的中率", () => {
  it("33業種ごとに、全銘柄と買い候補の値を出す。買い候補が0件の業種は「-」", async () => {
    const calls = renderPanel("sectors", sectors);
    const table = await screen.findByRole("table", { name: "業種別の的中率" });
    expect(urls(calls, "/api/accuracy/sectors")).toEqual(["/api/accuracy/sectors?horizon=20&period=3M&direction=up"]);
    expect(screen.getByRole("heading", { name: "業種別の的中率(20日後、直近3か月)" })).toBeInTheDocument();
    expect(within(table).getAllByRole("columnheader").map((th) => th.textContent)).toEqual([
      "業種", "全銘柄", "買い候補",
      "件数", "的中率", "市場の中央値を上回った割合 ▼", "騰落率の平均",
      "件数", "的中率", "市場の中央値を上回った割合", "騰落率の平均",
    ]); // prettier-ignore
    expect(bodyCells(table)).toEqual([
      ["銀行業", "5,200", "61.2%", "58.0%", "+2.1%", "310", "70.0%", "81.0%", "+6.4%"],
      ["卸売業", "9,800", "54.1%", "49.0%", "-0.4%", "-", "-", "-", "-"],
      ["(不明)", "12", "50.0%", "-", "0.0%", "3", "33.3%", "90.0%", "+10.0%"],
    ]);
    expect(within(table).getByText("銀行業")).toHaveClass("fix");
    expect(within(table).getByText("-0.4%")).toHaveClass("dn");
    expect(screen.getByText(/買い候補\(上位20銘柄\)に入った日の予測だけを数える/)).toBeInTheDocument();
    // 予想の向き(期間のタブの下。初期値は上昇予想)
    const dirTabs = screen.getByRole("tablist", { name: "業種別の的中率の予想の向き" });
    expect(within(dirTabs).getByRole("tab", { name: "上昇予想" })).toHaveAttribute("aria-selected", "true");
    const periodTabs = screen.getByRole("tablist", { name: "業種別の的中率の期間" });
    expect(periodTabs.compareDocumentPosition(dirTabs) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(
      screen.getByText(/上がると予想した日\(上昇確率が50\.0%以上\)だけで数える。初期の並びは全銘柄の市場の中央値を上回った割合の高い順/),
    ).toBeInTheDocument();
  });

  it("予想の向きを切り替えると、その条件で読み直し、並べ替えは初期の並び(下降予想は全銘柄の的中率の高い順)に戻す", async () => {
    const user = userEvent.setup();
    const calls = renderPanel("sectors", sectors);
    const table = await screen.findByRole("table", { name: "業種別の的中率" });
    await user.click(within(table).getByRole("button", { name: "業種" }));
    await user.click(screen.getByRole("tab", { name: "下降予想" }));
    await waitFor(() =>
      expect(urls(calls, "/api/accuracy/sectors").at(-1)).toBe("/api/accuracy/sectors?horizon=20&period=3M&direction=down"),
    );
    const t = await screen.findByRole("table", { name: "業種別の的中率" });
    expect(within(t).getAllByRole("button", { name: "的中率 ▼" })).toHaveLength(1);
    expect(screen.getByText(/下がると予想した日\(上昇確率が50\.0%未満\)だけで数える。初期の並びは全銘柄の的中率の高い順/)).toBeInTheDocument();
  });

  it("見出しを押すと、読み直さずに並べ替える。値がない業種は後ろ", async () => {
    const user = userEvent.setup();
    const calls = renderPanel("sectors", sectors);
    const table = await screen.findByRole("table", { name: "業種別の的中率" });
    const names = () => bodyCells(table).map((r) => r[0]);
    // 買い候補の的中率(大きい順 → 小さい順)。卸売業は値がないので後ろ
    const topHit = within(table).getAllByRole("button", { name: "的中率" })[1]!;
    await user.click(topHit);
    expect(names()).toEqual(["銀行業", "(不明)", "卸売業"]);
    await user.click(within(table).getByRole("button", { name: "的中率 ▼" }));
    expect(names()).toEqual(["(不明)", "銀行業", "卸売業"]);
    // 全銘柄の件数
    await user.click(within(table).getAllByRole("button", { name: "件数" })[0]!);
    expect(names()).toEqual(["卸売業", "銀行業", "(不明)"]);
    // 業種の名前(最初は小さい順)
    await user.click(within(table).getByRole("button", { name: "業種" }));
    expect(within(table).getByRole("button", { name: "業種 ▲" }).closest("th")).toHaveAttribute("aria-sort", "ascending");
    expect(names()).toEqual(sortSectorRows(SECTOR_ACCURACY.rows, "sector33", "asc").map((r) => r.sector33));
    expect(urls(calls, "/api/accuracy/sectors")).toHaveLength(1);
  });

  it("期間のタブを開いたときに、その期間の分だけ読む", async () => {
    const user = userEvent.setup();
    const calls = renderPanel("sectors", sectors, { date: "2026-07-21" });
    await screen.findByRole("table", { name: "業種別の的中率" });
    await user.click(screen.getByRole("tab", { name: "直近2年" }));
    await waitFor(() =>
      expect(urls(calls, "/api/accuracy/sectors").at(-1)).toBe("/api/accuracy/sectors?date=2026-07-21&horizon=20&period=2Y&direction=up"),
    );
    expect(await screen.findByRole("heading", { name: "業種別の的中率(20日後、直近2年)" })).toBeInTheDocument();
  });

  it("集計がなければその旨を出す。読めなければエラーを出す", async () => {
    const view = renderPanel("sectors", () => ({ body: { ...SECTOR_ACCURACY, rows: [] } }));
    expect(await screen.findByText("この期間の業種別の集計はありません")).toBeInTheDocument();
    expect(view.length).toBeGreaterThan(0);
  });

  it("読めなければエラーを出す", async () => {
    renderPanel("sectors", () => ({ status: 503, body: { detail: "DB に接続できません" } }));
    expect(await screen.findByRole("alert")).toHaveTextContent("DB に接続できません");
  });

  it("sortSectorRows: 同じ値の行は元の並びのまま。値がない行は、どちらの順でも後ろ", () => {
    const rows = SECTOR_ACCURACY.rows;
    expect(sortSectorRows(rows, "all.beat_rate", "desc").map((r) => r.sector33)).toEqual(["銀行業", "卸売業", "(不明)"]);
    expect(sortSectorRows(rows, "all.beat_rate", "asc").map((r) => r.sector33)).toEqual(["卸売業", "銀行業", "(不明)"]);
    expect(sortSectorRows(rows, "top.avg_change_rate", "desc").map((r) => r.sector33)).toEqual(["(不明)", "銀行業", "卸売業"]);
  });
});
