import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";

import { MENU_ITEMS } from "../lib/menu";
import Header from "./Header";

function renderAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Header />
    </MemoryRouter>,
  );
}

describe("Header(上のメニュー)", () => {
  it("8画面へのリンクを、決めた順に出す", () => {
    renderAt("/");
    const nav = screen.getByRole("navigation", { name: "メニュー" });
    const links = Array.from(nav.querySelectorAll("a")).map((a) => [a.textContent, a.getAttribute("href")]);
    expect(links).toEqual(MENU_ITEMS.map((m) => [m.label, m.path]));
    expect(links).toHaveLength(8);
  });

  it.each([
    ["/", "トップ"],
    ["/candidates?from=2026-07-22&to=2026-07-22", "買い候補"],
    ["/candidates/results", "買い候補と実際の株価"],
    ["/search", "銘柄の検索"],
    ["/holdings", "保有銘柄"],
    ["/accuracy", "的中率"],
    ["/accuracy/compare", "精度の比較"],
    ["/runs", "実行履歴"],
  ])("%s では「%s」だけ色を変える", (path, label) => {
    renderAt(path);
    const nav = screen.getByRole("navigation", { name: "メニュー" });
    const current = Array.from(nav.querySelectorAll("a.cur")).map((a) => a.textContent);
    expect(current).toEqual([label]);
  });

  it("銘柄詳細では、どのメニューも色を変えない", () => {
    renderAt("/stocks/11110");
    expect(screen.getByRole("navigation", { name: "メニュー" }).querySelectorAll("a.cur")).toHaveLength(0);
  });

  it("☰ でメニューを開き、リンクを押すと閉じる", async () => {
    const user = userEvent.setup();
    renderAt("/");
    const btn = screen.getByRole("button", { name: "メニューを開く" });
    expect(btn).toHaveAttribute("aria-expanded", "false");
    await user.click(btn);
    expect(btn).toHaveAttribute("aria-expanded", "true");
    expect(document.querySelector("header")).toHaveClass("open");
    await user.click(screen.getByRole("link", { name: "的中率" }));
    expect(btn).toHaveAttribute("aria-expanded", "false");
    await user.click(btn);
    await user.click(screen.getByRole("link", { name: "株価予測" }));
    expect(btn).toHaveAttribute("aria-expanded", "false");
  });
});
