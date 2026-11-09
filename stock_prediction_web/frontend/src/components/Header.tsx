// 上のメニュー。左に「株価予測」、8画面へのリンク、右に銘柄の検索。
// スマホ(幅 700px 以下)では「☰」のボタンにまとめる
import { useState } from "react";
import { Link, NavLink } from "react-router";

import { MENU_ITEMS } from "../lib/menu";
import HeaderSearch from "./HeaderSearch";

export default function Header() {
  // スマホでメニューを開いているか
  const [open, setOpen] = useState(false);

  return (
    <header className={open ? "open" : undefined}>
      <Link to="/" className="logo" onClick={() => setOpen(false)}>
        株価予測
      </Link>
      <nav aria-label="メニュー">
        {MENU_ITEMS.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            // 「買い候補」は「買い候補と実際の株価」(/candidates/results)のときに色を変えない
            end
            className={({ isActive }) => (isActive ? "cur" : undefined)}
            onClick={() => setOpen(false)}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>
      <HeaderSearch onSelect={() => setOpen(false)} />
      <button
        type="button"
        className="menu"
        aria-label="メニューを開く"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        ☰
      </button>
    </header>
  );
}
