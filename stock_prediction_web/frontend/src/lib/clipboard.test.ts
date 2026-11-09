import { afterEach, vi } from "vitest";

import { copyText } from "./clipboard";

afterEach(() => {
  vi.unstubAllGlobals();
  Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
});

describe("クリップボードへのコピー", () => {
  it("https・localhost では navigator.clipboard を使う", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    vi.stubGlobal("isSecureContext", true);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    expect(await copyText("abc")).toBe(true);
    expect(writeText).toHaveBeenCalledWith("abc");
  });

  it("navigator.clipboard が失敗したら、古い方法(execCommand)でコピーする", async () => {
    vi.stubGlobal("isSecureContext", true);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: () => Promise.reject(new Error("許可されていません")) },
      configurable: true,
    });
    const exec = vi.fn(() => true);
    document.execCommand = exec;
    expect(await copyText("abc")).toBe(true);
    expect(exec).toHaveBeenCalledWith("copy");
    // 一時的に足したテキストの枠は消す
    expect(document.querySelector("textarea")).toBeNull();
  });

  it("http(Tailscale)では古い方法を使い、それも失敗したら false", async () => {
    vi.stubGlobal("isSecureContext", false);
    document.execCommand = vi.fn(() => {
      throw new Error("使えません");
    });
    expect(await copyText("abc")).toBe(false);
    expect(document.querySelector("textarea")).toBeNull();
  });
});
