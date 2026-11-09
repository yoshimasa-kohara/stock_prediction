import {
  EMPTY,
  displayCode,
  formatDateTime,
  formatDateWithWeekday,
  formatInteger,
  formatPercent,
  formatPrice,
  formatSignedPercent,
  formatTime,
  parseIsoDate,
  signClass,
  toIsoDate,
} from "./format";

describe("formatPrice(株価: 小数1桁・3桁区切り)", () => {
  it.each([
    [1234.5, "1,234.5"],
    [3945, "3,945.0"],
    [0.04, "0.0"],
    [1234567.89, "1,234,567.9"],
    [-12.34, "-12.3"],
  ])("%s → %s", (v, expected) => {
    expect(formatPrice(v)).toBe(expected);
  });
  it("値がなければ -", () => {
    expect(formatPrice(null)).toBe(EMPTY);
    expect(formatPrice(undefined)).toBe(EMPTY);
    expect(formatPrice(Number.NaN)).toBe(EMPTY);
  });
});

describe("formatInteger(件数: 3桁区切り)", () => {
  it("3桁区切りの整数", () => {
    expect(formatInteger(3961)).toBe("3,961");
    expect(formatInteger(12.6)).toBe("13");
    expect(formatInteger(null)).toBe(EMPTY);
  });
});

describe("formatPercent(確率: % で小数1桁)", () => {
  it.each([
    [0.634, "63.4%"],
    [1, "100.0%"],
    [0, "0.0%"],
    [0.8786, "87.9%"],
  ])("%s → %s", (v, expected) => {
    expect(formatPercent(v)).toBe(expected);
  });
  it("値がなければ -", () => {
    expect(formatPercent(null)).toBe(EMPTY);
  });
});

describe("formatSignedPercent(率の差: 符号付き)", () => {
  it.each([
    [0.064, "+6.4%"],
    [-0.003, "-0.3%"],
    [0, "0.0%"],
    [0.0004, "0.0%"],
    [-0.0004, "0.0%"],
  ])("%s → %s", (v, expected) => {
    expect(formatSignedPercent(v)).toBe(expected);
  });
  it("値がなければ -", () => {
    expect(formatSignedPercent(undefined)).toBe(EMPTY);
  });
});

describe("signClass(上がった赤・下がった青・同じ灰色)", () => {
  it("符号で色を決める", () => {
    expect(signClass(1)).toBe("up");
    expect(signClass(-0.1)).toBe("dn");
    expect(signClass(0)).toBe("eq");
    expect(signClass(null)).toBe("");
  });
});

describe("displayCode(銘柄コードの表示。common/stock_code.py と同じ)", () => {
  it.each([
    ["13010", "1301"],
    ["130A0", "130A"],
    ["25935", "25935"],
    ["1301", "1301"],
    ["123456", "123456"],
  ])("%s → %s", (code, expected) => {
    expect(displayCode(code)).toBe(expected);
  });
});

describe("日付", () => {
  it("parseIsoDate は YYYY-MM-DD の正しい日だけを読む", () => {
    const d = parseIsoDate("2026-07-22");
    expect(d && toIsoDate(d)).toBe("2026-07-22");
    expect(parseIsoDate(" 2026-07-22 ")).not.toBeNull();
    expect(parseIsoDate("2026-02-30")).toBeNull();
    expect(parseIsoDate("2026-13-01")).toBeNull();
    expect(parseIsoDate("2026/07/22")).toBeNull();
    expect(parseIsoDate("20260722")).toBeNull();
    expect(parseIsoDate("")).toBeNull();
  });
  it("toIsoDate は月と日を2桁にする", () => {
    expect(toIsoDate(new Date(2026, 0, 5))).toBe("2026-01-05");
  });
  it("formatDateWithWeekday は曜日を付ける", () => {
    expect(formatDateWithWeekday("2026-07-22")).toBe("2026-07-22(水)");
    expect(formatDateWithWeekday("2026-07-26")).toBe("2026-07-26(日)");
    expect(formatDateWithWeekday("abc")).toBe("abc");
    expect(formatDateWithWeekday(null)).toBe(EMPTY);
  });
  it("formatDateTime・formatTime は日本時間で出す", () => {
    expect(formatDateTime("2026-07-22T19:00:31.123+09:00")).toBe("2026-07-22 19:00");
    expect(formatDateTime("2026-07-22T10:05:00Z")).toBe("2026-07-22 19:05");
    expect(formatDateTime("2026-07-22T15:30:00Z")).toBe("2026-07-23 00:30");
    expect(formatTime("2026-07-22T19:00:31+09:00")).toBe("19:00");
    expect(formatDateTime(null)).toBe(EMPTY);
    expect(formatTime(undefined)).toBe(EMPTY);
    expect(formatDateTime("abc")).toBe("abc");
    expect(formatTime("abc")).toBe("abc");
  });
});
