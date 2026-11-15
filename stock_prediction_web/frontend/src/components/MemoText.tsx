// 保有銘柄のメモ。長いメモは先頭だけを出し、押すと全文と短い表示を切り替える
import { useState } from "react";

/** 短い表示で出す文字数。これを超えるメモは、先頭のこの文字数に「…」を付けて出す */
export const MEMO_SHORT_LENGTH = 20;

export default function MemoText({ note }: { note: string | null }) {
  const [full, setFull] = useState(false);
  if (!note) return null;
  // 短い表示では、改行を空白にして1行で出す
  const flat = Array.from(note.replace(/\r?\n/g, " "));
  const short = flat.length > MEMO_SHORT_LENGTH ? `${flat.slice(0, MEMO_SHORT_LENGTH).join("")}…` : flat.join("");
  // 短い表示と全文が同じメモ(短くて改行もない)は、切り替えるものがないので文字だけを出す
  if (short === note) return <span title={note}>{note}</span>;
  return (
    <button
      type="button"
      className={full ? "memotgl full" : "memotgl"}
      title={note}
      aria-expanded={full}
      onClick={() => setFull(!full)}
    >
      {full ? note : short}
    </button>
  );
}
