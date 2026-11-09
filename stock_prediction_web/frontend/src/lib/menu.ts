// 上のメニューの画面(5章 No.1。銘柄詳細はメニューに出さない)

export interface MenuItem {
  path: string;
  label: string;
}

export const MENU_ITEMS: MenuItem[] = [
  { path: "/", label: "トップ" },
  { path: "/candidates", label: "買い候補" },
  { path: "/candidates/results", label: "買い候補と実際の株価" },
  { path: "/search", label: "銘柄の検索" },
  { path: "/holdings", label: "保有銘柄" },
  { path: "/accuracy", label: "的中率" },
  { path: "/accuracy/compare", label: "精度の比較" },
  { path: "/runs", label: "実行履歴" },
];
