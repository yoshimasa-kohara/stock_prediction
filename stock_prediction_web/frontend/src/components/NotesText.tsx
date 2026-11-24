// 備考。色はメールと同じ(赤 = 良い、青 = 悪い、橙・緑 = 注意。太字 = 目立たせたい)、区切りは「／」
import { Fragment } from "react";

import type { Note } from "../api/types";

/** 備考の色のクラス(styles.css の .r・.bl・.o・.g) */
const COLOR_CLASS: Record<string, string> = { red: "r", blue: "bl", orange: "o", green: "g" };

/** 備考の区切り(全角のスラッシュ。メールと同じ) */
export const NOTE_SEPARATOR = "／";

export default function NotesText({ notes }: { notes: Note[] }) {
  return (
    <>
      {notes.map((n, i) => (
        <Fragment key={`${i}-${n.text}`}>
          {i > 0 && NOTE_SEPARATOR}
          <span className={`${COLOR_CLASS[n.color] ?? ""}${n.bold ? " bd" : ""}`.trim()}>{n.text}</span>
        </Fragment>
      ))}
    </>
  );
}
