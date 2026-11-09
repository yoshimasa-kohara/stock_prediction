import { COMPARE } from "../test/apiData";
import { SETTINGS } from "../test/fixtures";
import { compareMarkdown, compareSections, periodText } from "./compareTables";

describe("精度の比較の表と Markdown", () => {
  it("期間の説明", () => {
    expect(periodText(COMPARE)).toBe("全期間(対象日が 2026-07-22 まで)");
    expect(periodText({ ...COMPARE, period: "3M", period_start: "2026-04-22" })).toBe(
      "直近3か月(対象日が 2026-04-22 より後〜2026-07-22)",
    );
  });

  it("①〜⑥のまとまり。④は2つの買い候補の表", () => {
    const sections = compareSections(COMPARE, 5);
    expect(sections.map((s) => s.title.slice(0, 1))).toEqual(["①", "③", "④", "⑤", "⑥"]);
    expect(sections[2]?.tables.map((t) => t.title)).toEqual(["買い候補", "買い候補(業種分散)"]);
    const top = sections[2]?.tables[0];
    expect(top?.rows.at(-1)?.map((c) => c.text)).toEqual(["", "その他", "32", "1.2%"]);
  });

  it("全銘柄との差: 買い候補の行はポイントの差、全銘柄の行は「-」、値がなければ「-」", () => {
    const rows = compareSections(COMPARE, 5)[0]!.tables[0]!.rows;
    expect(rows[0]?.slice(9).map((c) => c.text)).toEqual(["-", "-", "-"]);
    const top = rows[1]!.slice(9);
    expect(top.map((c) => c.text)).toEqual(["+28.7pt", "+5.8pt", "+1.9pt"]);
    expect(top.map((c) => c.cls)).toEqual(["up", "up", "up"]);
    const sector = rows[2]!.slice(9);
    expect(sector.map((c) => c.text)).toEqual(["+8.3pt", "+1.0pt", "-1.5pt"]);
    expect(sector[2]?.cls).toBe("dn");
    const missing = { ...COMPARE, rows: COMPARE.rows.map((r) => (r.segment === "TOP" ? { ...r, avg_sector_excess_rate: null } : r)) };
    expect(compareSections(missing, 5)[0]!.tables[0]!.rows[1]?.[11]?.text).toBe("-");
  });

  it("Markdown: 条件、表、補足。| はエスケープし、行がない表は(なし)", () => {
    const data = { ...COMPARE, models: [{ ...COMPARE.models[0]!, model_version: "a|b" }], monthly: [] };
    const md = compareMarkdown(data, SETTINGS.batch, 5);
    expect(md).toContain("- 集計の基準日: 2026-07-22 / 期間: 全期間(対象日が 2026-07-22 まで)");
    expect(md).toContain("| 予測日数 | 区分 | 検証件数 | 上がった割合 | 騰落率の平均 |");
    expect(md).toContain("| --- | --- |");
    expect(md).toContain("| 5日後 | 全銘柄 | 420,006 | 46.0% | +0.4% | 50.0% | 0.0% | 48.0% | -0.1% | - | - | - |");
    expect(md).toContain("| 上がった割合の全銘柄との差 | 騰落率の平均の全銘柄との差 | 業種の中央値との差の平均の全銘柄との差 |");
    expect(md).not.toContain("市場の中央値との差の平均の全銘柄との差");
    expect(md).toContain("| a\\|b | 2026-01-05 |");
    expect(md).toContain("| (なし) |  |");
    expect(md).toContain("検証できた予測日がありません");
    expect(md.endsWith("\n")).toBe(true);
  });
});
