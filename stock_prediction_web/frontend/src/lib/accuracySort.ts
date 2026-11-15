// 業種別の的中率の表の並べ替え(33業種だけなので、API を読み直さずに画面で並べ替える)
import type { SectorAccuracyRow, SectorAccuracyValues } from "../api/types";

/** 並べる向き(asc: 小さい順、desc: 大きい順) */
export type Order = "asc" | "desc";

/** 業種別の並べ替えの値(「all.hit_rate」のような形。業種は名前) */
function sectorValue(row: SectorAccuracyRow, sortKey: string): string | number | null {
  if (sortKey === "sector33") return row.sector33;
  const [side, name] = sortKey.split(".") as ["all" | "top", keyof SectorAccuracyValues];
  return row[side][name];
}

/** 業種別の行を並べ替える。値がない行は後ろ。同じ値の行は、API の並び(全銘柄の的中率の高い順)のまま */
export function sortSectorRows(rows: SectorAccuracyRow[], sortKey: string, order: Order): SectorAccuracyRow[] {
  const withValue = rows.filter((r) => sectorValue(r, sortKey) !== null);
  const without = rows.filter((r) => sectorValue(r, sortKey) === null);
  const sign = order === "asc" ? 1 : -1;
  withValue.sort((a, b) => {
    const [x, y] = [sectorValue(a, sortKey)!, sectorValue(b, sortKey)!];
    if (typeof x === "string" || typeof y === "string") return sign * String(x).localeCompare(String(y), "ja");
    return sign * (x - y);
  });
  return [...withValue, ...without];
}
