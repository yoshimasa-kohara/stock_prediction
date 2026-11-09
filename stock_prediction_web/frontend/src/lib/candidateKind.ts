// 買い候補の種類(買い候補・買い候補(業種分散))の切り替え
import type { BatchSettings, CandidateKind } from "../api/types";
import type { TabItem } from "../components/Tabs";

/** URL の kind の値から、買い候補の種類を返す(sector 以外は買い候補) */
export function parseKind(value: string | null): CandidateKind {
  return value === "sector" ? "sector" : "top";
}

/** 切り替えのタブ */
export const KIND_TABS: TabItem<CandidateKind>[] = [
  { value: "top", label: "買い候補" },
  { value: "sector", label: "買い候補(業種分散)" },
];

/** 見出しに使う名前 */
export function kindLabel(kind: CandidateKind): string {
  return kind === "sector" ? "買い候補(業種分散)" : "買い候補";
}

/** 買い候補の中身の説明(例: 上位20銘柄 / 同じ33業種は3銘柄まで、上位30銘柄) */
export function kindDescription(kind: CandidateKind, batch: BatchSettings): string {
  return kind === "sector"
    ? `同じ33業種は${batch.sector_cap_per_sector}銘柄まで、上位${batch.buy_candidate_sector_count}銘柄`
    : `上位${batch.buy_candidate_count}銘柄`;
}

/** URL の検索条件に入れる kind(買い候補は省略する) */
export function kindParam(kind: CandidateKind): Record<string, string> {
  return kind === "sector" ? { kind } : {};
}
