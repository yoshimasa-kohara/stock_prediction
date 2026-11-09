import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, vi } from "vitest";

import { COMPARE } from "../test/apiData";
import { renderApp, urlsOf } from "../test/renderApp";

function handler(p: string, q: URLSearchParams) {
  if (p === "/api/accuracy/compare") return { body: { ...COMPARE, period: q.get("period") ?? "1M" } };
  return undefined;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("精度の比較", () => {
  it("①〜⑥の表を出す(全ての予測日数、件数と期間、業種の内訳、月ごと、使ったモデル)", async () => {
    renderApp("/accuracy/compare", handler);
    expect(await screen.findByRole("heading", { name: "① 比較表(② 5・10・20・60日後を全て出す)" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "集計の基準日" })).toHaveValue("2026-07-22");
    expect(screen.getByRole("tab", { name: "直近1か月" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText(/同じ33業種は3銘柄まで、上位30銘柄/)).toBeInTheDocument();
    // ①・②: 4つの予測日数 × 3つの区分
    const compare = within(screen.getByRole("table", { name: "比較表(全ての予測日数)" }));
    expect(compare.getAllByRole("row")).toHaveLength(1 + 12);
    expect(compare.getAllByText("20日後(主)")).toHaveLength(3);
    expect(compare.getAllByText("74.7%")).toHaveLength(4);
    expect(compare.getAllByText("+6.8%")[0]).toHaveClass("up");
    expect(compare.getAllByText("-1.6%")[0]).toHaveClass("dn");
    expect(compare.getAllByText("2,280")).toHaveLength(4);
    // 1・2列目(予測日数、区分)は横にスクロールしても左に固定する。ほかの列と、ほかの表は固定しない
    expect(compare.getByRole("columnheader", { name: "予測日数" })).toHaveClass("fix", "fix1");
    expect(compare.getByRole("columnheader", { name: "区分" })).toHaveClass("fix", "fix2");
    expect(compare.getAllByText("20日後(主)")[0]).toHaveClass("fix1");
    expect(compare.getByRole("columnheader", { name: "検証件数" })).not.toHaveClass("fix");
    expect(within(screen.getByRole("table", { name: "件数と期間" })).getByRole("columnheader", { name: "予測日数" })).not.toHaveClass("fix");
    // ③: 件数と期間
    const counts = within(screen.getByRole("table", { name: "件数と期間" }));
    expect(counts.getAllByText("2026-04-16")).toHaveLength(3);
    expect(counts.getAllByText("3,961")).toHaveLength(4);
    // ④: 2つの買い候補を並べる。0件なら、その旨
    expect(within(screen.getByRole("table", { name: "買い候補" })).getByText("銀行業")).toBeInTheDocument();
    expect(screen.getByText("その他")).toBeInTheDocument();
    expect(screen.getByText("上位5業種。合計 2,662件(予測日 × 銘柄)、出てきた業種の数 9")).toBeInTheDocument();
    expect(screen.getByText("検証できた予測日がありません")).toBeInTheDocument();
    // ⑤・⑥
    expect(screen.getByRole("heading", { name: "⑤ 月ごと" })).toBeInTheDocument();
    expect(within(screen.getByRole("table", { name: "月ごと(20日後。予測日の月)" })).getByText("+14.5%")).toHaveClass("up");
    const models = within(screen.getByRole("table", { name: "使ったモデル" }));
    expect(models.getByText("2024-07-29〜2025-12-26")).toBeInTheDocument();
    expect(models.getAllByText("-")).toHaveLength(2);
  });

  it("期間を切り替えると読み直す", async () => {
    const user = userEvent.setup();
    const calls = renderApp("/accuracy/compare", handler);
    await screen.findByRole("heading", { name: "③ 件数と期間" });
    await user.click(screen.getByRole("tab", { name: "直近3か月" }));
    await waitFor(() => expect(urlsOf(calls, "/api/accuracy/compare").at(-1)).toBe("/api/accuracy/compare?period=3M"));
    expect(urlsOf(calls, "/api/accuracy/compare")[0]).toBe("/api/accuracy/compare?period=1M");
  });

  it("「Markdown でコピー」で、表の内容を Markdown にしてコピーする", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn(() => Promise.resolve());
    vi.stubGlobal("isSecureContext", true);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    renderApp("/accuracy/compare", handler);
    await screen.findByRole("heading", { name: "⑥ 使ったモデル" });
    await user.click(screen.getByRole("button", { name: "Markdown でコピー" }));
    expect(await screen.findByText("コピーしました")).toBeInTheDocument();
    const text = (writeText.mock.calls[0] as unknown as [string])[0];
    expect(text).toContain("# 株価予測システム 精度の比較");
    expect(text).toContain("| 20日後(主) | 買い候補 | 2,280 | 74.7% | +6.2% | 81.3% | +6.8% | 57.9% | +1.8% | +28.7pt | +5.8pt | +1.9pt |");
    expect(text).toContain("### 買い候補(業種分散)");
  });

  it("コピーできなければ、手で選べるように Markdown を枠に出す", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("isSecureContext", false);
    document.execCommand = vi.fn(() => false);
    renderApp("/accuracy/compare", handler);
    await screen.findByRole("heading", { name: "⑥ 使ったモデル" });
    await user.click(screen.getByRole("button", { name: "Markdown でコピー" }));
    expect(await screen.findByText(/コピーできませんでした/)).toBeInTheDocument();
    expect((screen.getByRole("textbox", { name: "コピーする Markdown" }) as HTMLTextAreaElement).value).toContain(
      "## ① 比較表",
    );
    // 期間を変えると枠は消す
    await user.click(screen.getByRole("tab", { name: "直近1か月" }));
    expect(screen.queryByRole("textbox", { name: "コピーする Markdown" })).not.toBeInTheDocument();
  });

  it("検証結果がなければ、その旨を出し、コピーできない", async () => {
    renderApp("/accuracy/compare", (p) =>
      p === "/api/accuracy/compare"
        ? { body: { ...COMPARE, summary_date: null, summary_dates: [], rows: [], sectors: [], monthly: [], models: [] } }
        : undefined,
    );
    expect(await screen.findByText("検証結果はまだありません")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Markdown でコピー" })).toBeDisabled();
  });

  it("API のエラーを出す", async () => {
    renderApp("/accuracy/compare", (p) =>
      p === "/api/accuracy/compare" ? { status: 400, body: { detail: "期間は 1M・3M・ALL のどれかを指定してください" } } : undefined,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("期間は 1M・3M・ALL のどれかを指定してください");
  });
});
