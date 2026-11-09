import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useParams } from "react-router";
import { vi } from "vitest";

import { mockApi } from "../test/fixtures";
import HeaderSearch, { SEARCH_DELAY_MS } from "./HeaderSearch";

const ITEMS = [
  { code: "11110", name: "サンプル銀行A", market: "プライム", sector33: "銀行業" },
  { code: "11120", name: "サンプル商事A", market: "スタンダード", sector33: "卸売業" },
];

function Detail() {
  const { code } = useParams();
  return <p>銘柄詳細 {code}</p>;
}

function renderSearch(onSelect = vi.fn()) {
  render(
    <MemoryRouter>
      <HeaderSearch onSelect={onSelect} />
      <Routes>
        <Route path="/" element={<p>トップ</p>} />
        <Route path="/stocks/:code" element={<Detail />} />
      </Routes>
    </MemoryRouter>,
  );
  return onSelect;
}

describe("HeaderSearch(上のメニューの銘柄の検索)", () => {
  it("入力してから少し待って検索し、候補を選ぶと銘柄詳細へ移る", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls = mockApi((p, q) => (p === "/api/stocks/search" ? { body: { items: q.get("q") === "サンプル" ? ITEMS : [] } } : undefined));
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const onSelect = renderSearch();
    const input = screen.getByRole("textbox", { name: "銘柄の検索" });
    await user.type(input, "サンプル");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SEARCH_DELAY_MS);
    });
    // 打っている途中では呼ばない(最後の文字だけで1回)
    expect(calls.map((c) => c.url)).toEqual([`/api/stocks/search?q=${encodeURIComponent("サンプル")}`]);
    const option = await screen.findByRole("option", { name: "1111 サンプル銀行A(プライム)" });
    await user.click(option);
    expect(await screen.findByText("銘柄詳細 11110")).toBeInTheDocument();
    expect(input).toHaveValue("");
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it("Enter で最初の候補へ移る。見つからなければ「見つかりません」。Escape で閉じる", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockApi((p, q) => (p === "/api/stocks/search" ? { body: { items: q.get("q") === "サンプル" ? ITEMS : [] } } : undefined));
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderSearch();
    const input = screen.getByRole("textbox", { name: "銘柄の検索" });
    await user.type(input, "zzz");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SEARCH_DELAY_MS);
    });
    expect(await screen.findByText("見つかりません")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).toBeNull();
    await user.clear(input);
    expect(screen.queryByRole("listbox")).toBeNull();
    await user.type(input, "サンプル");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SEARCH_DELAY_MS);
    });
    await screen.findByRole("option", { name: /サンプル商事A/ });
    await user.keyboard("{Enter}");
    expect(await screen.findByText("銘柄詳細 11110")).toBeInTheDocument();
  });

  it("検索に失敗したら候補を出さない。フォーカスが外れたら閉じる", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockApi(() => ({ status: 503, body: { detail: "DB に接続できません" } }));
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderSearch();
    const input = screen.getByRole("textbox", { name: "銘柄の検索" });
    await user.type(input, "1");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SEARCH_DELAY_MS);
    });
    expect(await screen.findByText("見つかりません")).toBeInTheDocument();
    await user.tab();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    expect(screen.queryByRole("listbox")).toBeNull();
    // 開いてから Enter を押しても、候補がなければ移らない
    await user.click(input);
    await user.keyboard("{Enter}");
    expect(screen.getByText("トップ")).toBeInTheDocument();
  });
});
