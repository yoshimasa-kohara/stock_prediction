// 予測日の選び方(買い候補・銘柄の検索)。「前の日」「次の日」は予測がある日だけを移る。
// カレンダーでは予測がない日を選べない
import DateInput from "./DateInput";

interface Props {
  value: string;
  dates: string[];
  onChange: (iso: string) => void;
  /** 「予測日」の文字を出すか(項目名を別に出している画面では false) */
  showLabel?: boolean;
}

export default function PredDatePicker({ value, dates, onChange, showLabel = true }: Props) {
  const index = dates.indexOf(value);
  const prev = index > 0 ? dates[index - 1] : undefined;
  const next = index >= 0 && index < dates.length - 1 ? dates[index + 1] : undefined;
  return (
    <>
      {showLabel && "予測日"}
      <button type="button" className="btn sm sub" disabled={!prev} onClick={() => prev && onChange(prev)}>
        ◀ 前の日
      </button>
      <DateInput label="予測日" value={value} onChange={onChange} enabledDates={dates} />
      <button type="button" className="btn sm sub" disabled={!next} onClick={() => next && onChange(next)}>
        次の日 ▶
      </button>
    </>
  );
}
