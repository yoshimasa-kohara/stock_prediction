// ブラウザのタブの title を「株価予測-画面名」にする。画面名は各画面の h1 と同じ名前
import { useEffect } from "react";

/** タブの title の先頭につける名前(index.html の <title> と同じ) */
export const TITLE_PREFIX = "株価予測";

/** 画面名から、タブの title を作る(ハイフンの前後に空白なし) */
export function pageTitle(name: string): string {
  return `${TITLE_PREFIX}-${name}`;
}

/** 画面が表示されている間、タブの title を「株価予測-画面名」にする */
export function usePageTitle(name: string): void {
  useEffect(() => {
    document.title = pageTitle(name);
  }, [name]);
}
