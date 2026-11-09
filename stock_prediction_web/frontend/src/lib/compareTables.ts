// 精度の比較の表の中身。
// 画面の表と、コピーする Markdown の両方をここから作る(画面と同じ内容をコピーするため)
import type { BatchSettings, CompareResponse, CompareRow } from "../api/types";
import { formatInteger, formatPercent, formatSignedPercent, signClass } from "./format";
import { PERIOD_LABELS } from "./labels";

/** 表の1つのセル(cls は画面の色のクラス) */
export interface Cell {
  text: string;
  cls?: string;
  /** 数字は右に寄せる */
  num?: boolean;
}

/** 1つの表 */
export interface Table {
  /** 見出し(Markdown の ### にも使う) */
  title: string;
  headers: string[];
  rows: Cell[][];
  /** 横にスクロールしても左に固定して見せる列の数(0〜2。省略は固定しない) */
  fixCols?: 1 | 2;
  /** 表の下の補足 */
  note?: string;
}

/** 1つのまとまり(① など。中に表が1つ以上) */
export interface Section {
  title: string;
  tables: Table[];
}

/** 対象区分の名前 */
export const SEGMENT_LABELS: Record<string, string> = {
  ALL: "全銘柄",
  TOP: "買い候補",
  TOP_SECTOR: "買い候補(業種分散)",
};

const num = (text: string, cls?: string): Cell => ({ text, cls, num: true });
const txt = (text: string): Cell => ({ text });
const pct = (v: number | null): Cell => num(formatPercent(v));
const signed = (v: number | null): Cell => num(formatSignedPercent(v), signClass(v) || undefined);
/** 率の差をポイントで出す(例: 0.0575 → "+5.8pt"。小数1桁で 0 になる値は "0.0pt")。どちらかがなければ "-" */
function point(v: number | null, base: number | null): Cell {
  if (v === null || base === null) return num("-");
  const diff = (v - base) * 100;
  const text = diff.toFixed(1);
  if (Number(text) === 0) return num("0.0pt", "eq");
  return num(`${diff > 0 ? "+" : ""}${text}pt`, signClass(diff) || undefined);
}
const label = (seg: string): string => SEGMENT_LABELS[seg] ?? seg;

/** 期間の説明(例: 全期間 / 直近3か月(対象日が 2026-04-22 より後〜2026-07-22)) */
export function periodText(data: CompareResponse): string {
  const name = PERIOD_LABELS[data.period] ?? data.period;
  if (data.period_start) return `${name}(対象日が ${data.period_start} より後〜${data.summary_date ?? ""})`;
  return `${name}(対象日が ${data.summary_date ?? ""} まで)`;
}

/** 条件と、指標の決め方(Markdown の頭と、画面の説明に出す) */
export function conditionLines(data: CompareResponse, batch: BatchSettings): string[] {
  return [
    `集計の基準日: ${data.summary_date ?? "-"} / 期間: ${periodText(data)}。期間は対象日(答え合わせの日)で区切る`,
    `買い候補: 上位${batch.buy_candidate_count}銘柄 / 買い候補(業種分散): 同じ33業種は${batch.sector_cap_per_sector}銘柄まで、上位${batch.buy_candidate_sector_count}銘柄。` +
      `どちらも主となる日数(${data.main_horizon}日後)の予測で選び、同じ銘柄を全ての予測日数で検証する`,
    "市場の中央値: 同じ予測日・予測日数の全銘柄(検証済み)の騰落率の中央値。業種の中央値: そのうち同じ33業種の銘柄の中央値。" +
      "どちらも、中央値と同じ値は勝っていない扱い。業種は予測した時点の33業種(33業種がない銘柄は業種比から除く)",
  ];
}

function rowsFor(data: CompareResponse): CompareRow[][] {
  const horizons = [...new Set(data.rows.map((r) => r.horizon))];
  return horizons.map((h) => data.rows.filter((r) => r.horizon === h));
}

/** ①・②: 比較表(全ての予測日数) */
export function compareTable(data: CompareResponse): Table {
  return {
    title: "比較表(全ての予測日数)",
    fixCols: 2,
    headers: [
      "予測日数",
      "区分",
      "検証件数",
      "上がった割合",
      "騰落率の平均",
      "市場の中央値に勝った割合",
      "市場の中央値との差の平均",
      "業種の中央値に勝った割合",
      "業種の中央値との差の平均",
      "上がった割合の全銘柄との差",
      "騰落率の平均の全銘柄との差",
      "業種の中央値との差の平均の全銘柄との差",
    ],
    rows: rowsFor(data).flatMap((group) => {
      // 同じ予測日数の全銘柄の行(全銘柄の行自身は差を出さない)
      const all = group.find((r) => r.segment === "ALL");
      return group.map((r) => [
        txt(`${r.horizon}日後${r.horizon === data.main_horizon ? "(主)" : ""}`),
        txt(label(r.segment)),
        num(formatInteger(r.verified_count)),
        pct(r.up_rate),
        signed(r.avg_change_rate),
        pct(r.beat_rate),
        signed(r.avg_excess_rate),
        pct(r.sector_beat_rate),
        signed(r.avg_sector_excess_rate),
        ...(r.segment === "ALL" || !all
          ? [num("-"), num("-"), num("-")]
          : [
              point(r.up_rate, all.up_rate),
              point(r.avg_change_rate, all.avg_change_rate),
              point(r.avg_sector_excess_rate, all.avg_sector_excess_rate),
            ]),
      ]);
    }),
  };
}

/** ③: 件数と期間 */
export function countTable(data: CompareResponse): Table {
  return {
    title: "件数と期間",
    headers: ["予測日数", "区分", "検証件数", "検証できた予測日(最初)", "検証できた予測日(最後)", "銘柄の数"],
    rows: data.rows.map((r) => [
      txt(`${r.horizon}日後`),
      txt(label(r.segment)),
      num(formatInteger(r.verified_count)),
      txt(r.first_pred_date ?? "-"),
      txt(r.last_pred_date ?? "-"),
      num(formatInteger(r.code_count)),
    ]),
  };
}

/** ④: 2つの買い候補の業種の内訳 */
export function sectorTables(data: CompareResponse, topN: number): Table[] {
  return data.sectors.map((s) => {
    const rows: Cell[][] = s.top.map((x, i) => [num(String(i + 1)), txt(x.sector), num(formatInteger(x.count)), pct(x.share)]);
    if (s.other_count > 0) rows.push([txt(""), txt("その他"), num(formatInteger(s.other_count)), pct(s.other_share)]);
    return {
      title: label(s.segment),
      headers: ["順位", "業種", "件数", "割合"],
      rows,
      note:
        s.total === 0
          ? "検証できた予測日がありません"
          : `上位${topN}業種。合計 ${formatInteger(s.total)}件(予測日 × 銘柄)、出てきた業種の数 ${formatInteger(s.sector_count)}`,
    };
  });
}

/** ⑤: 月ごと(主となる日数) */
export function monthlyTable(data: CompareResponse): Table {
  return {
    title: `月ごと(${data.main_horizon}日後。予測日の月)`,
    headers: ["予測月", "区分", "検証件数", "上がった割合", "市場の中央値との差の平均", "業種の中央値との差の平均"],
    rows: data.monthly.map((m) => [
      txt(m.month),
      txt(label(m.segment)),
      num(formatInteger(m.verified_count)),
      pct(m.up_rate),
      signed(m.avg_excess_rate),
      signed(m.avg_sector_excess_rate),
    ]),
  };
}

/** ⑥: 使ったモデル */
export function modelTable(data: CompareResponse): Table {
  return {
    title: "使ったモデル",
    headers: ["モデルバージョン", "使った予測日(最初)", "使った予測日(最後)", "学習の基準日", "学習データの期間"],
    rows: data.models.map((m) => [
      txt(m.model_version),
      txt(m.first_pred_date),
      txt(m.last_pred_date),
      txt(m.base_date ?? "-"),
      txt(m.train_start_date && m.train_end_date ? `${m.train_start_date}〜${m.train_end_date}` : "-"),
    ]),
  };
}

/** 画面に出す順のまとまり(番号は利用者の要望の ①〜⑥) */
export function compareSections(data: CompareResponse, topN: number): Section[] {
  return [
    { title: "① 比較表(② 5・10・20・60日後を全て出す)", tables: [compareTable(data)] },
    { title: "③ 件数と期間", tables: [countTable(data)] },
    { title: "④ 買い候補の業種の内訳", tables: sectorTables(data, topN) },
    { title: "⑤ 月ごと", tables: [monthlyTable(data)] },
    { title: "⑥ 使ったモデル", tables: [modelTable(data)] },
  ];
}

/** Markdown の表のセル(| と改行をそのまま出さない) */
function mdCell(text: string): string {
  return text.replace(/\|/g, "\\|").replace(/\n/g, " ");
}

/** 1つの表の Markdown */
function tableMarkdown(table: Table): string[] {
  const lines = [
    `| ${table.headers.map(mdCell).join(" | ")} |`,
    `| ${table.headers.map(() => "---").join(" | ")} |`,
    ...table.rows.map((r) => `| ${r.map((c) => mdCell(c.text)).join(" | ")} |`),
  ];
  if (table.rows.length === 0) lines.push(`| ${table.headers.map((_, i) => (i === 0 ? "(なし)" : "")).join(" | ")} |`);
  if (table.note) lines.push("", table.note);
  return lines;
}

/** 画面の全ての表を Markdown にする(⑦。Claude に貼って判断してもらうため) */
export function compareMarkdown(data: CompareResponse, batch: BatchSettings, topN: number): string {
  const lines: string[] = ["# 株価予測システム 精度の比較", "", ...conditionLines(data, batch).map((l) => `- ${l}`)];
  for (const section of compareSections(data, topN)) {
    lines.push("", `## ${section.title}`);
    for (const table of section.tables) {
      if (section.tables.length > 1) lines.push("", `### ${table.title}`);
      lines.push("", ...tableMarkdown(table));
    }
  }
  return `${lines.join("\n")}\n`;
}
