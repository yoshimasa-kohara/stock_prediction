// 切り替えのタブ(画面見本の .tabs)。選んでいるものは色を変える

export interface TabItem<T extends string> {
  value: T;
  label: string;
}

interface Props<T extends string> {
  items: TabItem<T>[];
  value: T;
  onChange: (value: T) => void;
  /** 画面の読み上げ用の名前 */
  label: string;
}

export default function Tabs<T extends string>({ items, value, onChange, label }: Props<T>) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {items.map((item) => (
        <button
          type="button"
          role="tab"
          key={item.value}
          aria-selected={item.value === value}
          className={item.value === value ? "cur" : undefined}
          onClick={() => onChange(item.value)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
