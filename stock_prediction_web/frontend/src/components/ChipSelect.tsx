// 複数選べる条件(33業種、備考)。選んだものを札で出し、「×」で外す。「＋ 選ぶ」の一覧から足す
import type { Option } from "../api/types";

interface Props {
  label: string;
  options: Option[];
  values: string[];
  onChange: (values: string[]) => void;
}

export default function ChipSelect({ label, options, values, onChange }: Props) {
  const rest = options.filter((o) => !values.includes(o.value));
  return (
    <span className="chips">
      {values.map((v) => {
        const o = options.find((x) => x.value === v);
        return (
          <span key={v} className="badge b-empty">
            {o?.label ?? v}{" "}
            <button
              type="button"
              className="chipx"
              aria-label={`${o?.label ?? v}を外す`}
              onClick={() => onChange(values.filter((x) => x !== v))}
            >
              ×
            </button>
          </span>
        );
      })}
      <select
        aria-label={`${label}を選ぶ`}
        value=""
        onChange={(e) => {
          if (e.target.value) onChange([...values, e.target.value]);
        }}
      >
        <option value="">＋ 選ぶ</option>
        {rest.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </span>
  );
}
