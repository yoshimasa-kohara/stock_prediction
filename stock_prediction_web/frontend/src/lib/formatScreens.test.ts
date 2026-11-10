import { vi } from "vitest";

import { chartHeightPx, loadChartHeight, saveChartHeight } from "./chart";
import { judgmentClass, statusBadgeClass, statusTextClass, verifyLabel } from "./labels";
import {
  EMPTY,
  addDays,
  formatElapsed,
  formatMonthDay,
  formatOku,
  formatSignedNumber,
  formatTimes,
  formatVolume,
  truncate,
} from "./format";

describe("書式(全画面で追加したもの)", () => {
  it("かかった時間は「1時間23分」「4分05秒」「12秒」。値がなければ空", () => {
    expect(formatElapsed(4980)).toBe("1時間23分");
    expect(formatElapsed(245)).toBe("4分05秒");
    expect(formatElapsed(12.4)).toBe("12秒");
    expect(formatElapsed(-3)).toBe("0秒");
    expect(formatElapsed(null)).toBe("");
  });

  it("符号付きの数・倍率・億円・出来高", () => {
    expect(formatSignedNumber(13270)).toBe("+13,270");
    expect(formatSignedNumber(-9880)).toBe("-9,880");
    expect(formatSignedNumber(0.04, 1)).toBe("0.0");
    expect(formatSignedNumber(59.25, 1)).toBe("+59.3");
    expect(formatSignedNumber(null)).toBe(EMPTY);
    expect(formatTimes(7.94)).toBe("7.9倍");
    expect(formatTimes(undefined)).toBe(EMPTY);
    expect(formatOku(182_000_000_000)).toBe("1,820億円");
    expect(formatOku(null)).toBe(EMPTY);
    expect(formatVolume(1_380_000)).toBe("138万");
    expect(formatVolume(9_999.4)).toBe("9,999");
    expect(formatVolume(null)).toBe(EMPTY);
  });

  it("日付をずらす・月と日・長い文字を切る", () => {
    expect(addDays("2026-07-22", -6)).toBe("2026-07-16");
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(addDays("abc", 1)).toBe("abc");
    expect(formatMonthDay("2026-07-29")).toBe("7/29");
    expect(formatMonthDay(null)).toBe(EMPTY);
    expect(formatMonthDay("x")).toBe(EMPTY);
    expect(truncate("あいうえお", 3)).toBe("あいう…");
    expect(truncate("あいう", 3)).toBe("あいう");
  });

  it("判定・状態・答え合わせの表示", () => {
    expect(judgmentClass("売却(損切りライン)")).toBe("jr");
    expect(judgmentClass("保持(注意: 上昇予測だが下降しています)")).toBe("jo");
    expect(judgmentClass("乗り換えを検討")).toBe("jo");
    expect(judgmentClass("保持")).toBe("");
    expect(judgmentClass("保持(注意: 相場全体が下がる予測)")).toBe("jo");
    expect(judgmentClass("判定なし(本日の予測がありません)")).toBe("jg");
    expect(statusBadgeClass("SUCCESS")).toBe("badge b-ok");
    expect(statusBadgeClass("RUNNING")).toBe("badge b-run");
    expect(statusBadgeClass("EMPTY")).toBe("badge b-empty");
    expect(statusTextClass("RUNNING")).toBe("run");
    expect(statusTextClass("EMPTY")).toBe("");
    expect(verifyLabel("VERIFIED", true)).toBe("的中");
    expect(verifyLabel("VERIFIED", false)).toBe("外れ");
    expect(verifyLabel("UNVERIFIABLE", null)).toBe("検証不能");
    expect(verifyLabel("PENDING", null)).toBe("未検証");
  });
});

describe("チャートの高さ", () => {
  it("知らない値やブラウザーに残せないときは、小(440px)にする", () => {
    window.localStorage.setItem("stockChartHeight", "X");
    expect(loadChartHeight()).toBe("S");
    expect(chartHeightPx("M")).toBe(480);
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("使えない");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("使えない");
    });
    expect(loadChartHeight()).toBe("S");
    expect(() => saveChartHeight("L")).not.toThrow();
  });
});
