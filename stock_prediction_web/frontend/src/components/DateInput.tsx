// 日付の入力欄。文字で入れるか、右のボタンからカレンダーを開いて選ぶ。書式は YYYY-MM-DD。
// enabledDates を渡すと、それ以外の日(予測がない土日・祝日など)を薄く表示して選べないようにする
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { DayPicker } from "react-day-picker";
import { ja } from "react-day-picker/locale";
import "react-day-picker/style.css";

import { parseIsoDate, toIsoDate } from "../lib/format";

/** 形が違うときのメッセージ */
export const MSG_BAD_FORMAT = "日付は YYYY-MM-DD の形で入れてください";
/** 選べない日を入れたときのメッセージ */
export const MSG_NOT_ENABLED = "この日の予測はありません(土日・祝日、またはバッチが動いていない日)";

interface Props {
  /** 今の値(YYYY-MM-DD。空なら未入力) */
  value: string;
  /** 正しい日付が決まったときに呼ぶ */
  onChange: (iso: string) => void;
  /** 選べる日(YYYY-MM-DD)。省略すると全ての日を選べる */
  enabledDates?: string[];
  /** 画面の読み上げ用の名前(例: 予測日) */
  label: string;
}

export default function DateInput({ value, onChange, enabledDates, label }: Props) {
  const [text, setText] = useState(value);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLSpanElement>(null);
  const errorId = useId();
  const enabled = useMemo(() => (enabledDates ? new Set(enabledDates) : null), [enabledDates]);

  // 外から値が変わったら(「前の日」ボタンなど)、入力欄も合わせる
  const [prevValue, setPrevValue] = useState(value);
  if (prevValue !== value) {
    setPrevValue(value);
    setText(value);
    setError(null);
  }

  // カレンダーの外を押したら閉じる
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  /** 入力した文字を確かめ、正しければ onChange を呼ぶ */
  function commit(input: string) {
    const trimmed = input.trim();
    if (trimmed === value) {
      setError(null);
      return;
    }
    const d = parseIsoDate(trimmed);
    if (!d) {
      setError(MSG_BAD_FORMAT);
      return;
    }
    const iso = toIsoDate(d);
    if (enabled && !enabled.has(iso)) {
      setError(MSG_NOT_ENABLED);
      return;
    }
    setError(null);
    setText(iso);
    onChange(iso);
  }

  const selected = parseIsoDate(value) ?? undefined;
  // カレンダーで最初に出す月: 選んでいる日、なければ選べる最後の日
  const lastEnabled = enabledDates?.at(-1);
  const defaultMonth = selected ?? (lastEnabled ? (parseIsoDate(lastEnabled) ?? undefined) : undefined);

  return (
    <span className="dateinput" ref={boxRef}>
      <input
        className="date"
        value={text}
        aria-label={label}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
        placeholder="YYYY-MM-DD"
        inputMode="numeric"
        onChange={(e) => setText(e.target.value)}
        onBlur={() => commit(text)}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit(text);
          if (e.key === "Escape") setOpen(false);
        }}
      />
      <button
        type="button"
        className="calbtn"
        aria-label={`${label}をカレンダーで選ぶ`}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        📅
      </button>
      {error && (
        <span className="err" id={errorId}>
          {error}
        </span>
      )}
      {open && (
        <div className="cal" role="dialog" aria-label={`${label}のカレンダー`}>
          <DayPicker
            mode="single"
            locale={ja}
            selected={selected}
            defaultMonth={defaultMonth}
            disabled={enabled ? (d: Date) => !enabled.has(toIsoDate(d)) : undefined}
            modifiers={{ sun: { dayOfWeek: [0] }, sat: { dayOfWeek: [6] } }}
            modifiersClassNames={{ sun: "sun", sat: "sat" }}
            onSelect={(d) => {
              if (!d) return;
              const iso = toIsoDate(d);
              setText(iso);
              setError(null);
              setOpen(false);
              if (iso !== value) onChange(iso);
            }}
          />
          {enabled && <div className="ft">予測がない日(土日・祝日など)は薄く表示し、選べない</div>}
        </div>
      )}
    </span>
  );
}
