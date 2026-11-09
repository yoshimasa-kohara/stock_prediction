import { vi } from "vitest";

import { ApiError, MSG_NETWORK_ERROR, apiGet, buildUrl, toMessage } from "./client";

describe("buildUrl", () => {
  it("値があるクエリだけを付ける", () => {
    expect(buildUrl("/api/x")).toBe("/api/x");
    expect(buildUrl("/api/x", { date: "2026-07-22", n: 5, flag: false, a: undefined, b: null, c: "" })).toBe(
      "/api/x?date=2026-07-22&n=5&flag=false",
    );
    expect(buildUrl("/api/x", { q: "サンプル 銀行" })).toBe("/api/x?q=%E3%82%B5%E3%83%B3%E3%83%97%E3%83%AB+%E9%8A%80%E8%A1%8C");
  });
});

describe("apiGet", () => {
  it("JSON を返す", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: 1 }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(apiGet("/api/x", { a: 1 })).resolves.toEqual({ ok: 1 });
    expect(fetchMock).toHaveBeenCalledWith("/api/x?a=1", expect.objectContaining({ headers: { Accept: "application/json" } }));
  });

  it("エラーの返り値の detail をメッセージにする", async () => {
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ detail: "DB に接続できません" }), { status: 503 }));
    const err = await apiGet("/api/x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(503);
    expect((err as ApiError).message).toBe("DB に接続できません");
  });

  it("JSON でないエラーの返り値は、状態コードを入れた決まった文にする", async () => {
    vi.stubGlobal("fetch", async () => new Response("Bad Gateway", { status: 502 }));
    await expect(apiGet("/api/x")).rejects.toThrow("エラーが発生しました(状態コード 502)");
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ message: "x" }), { status: 500 }));
    await expect(apiGet("/api/x")).rejects.toThrow("エラーが発生しました(状態コード 500)");
  });

  it("サーバーに届かなければ、接続できないメッセージ", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("Failed to fetch");
    });
    const err = await apiGet("/api/x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(0);
    expect((err as ApiError).message).toBe(MSG_NETWORK_ERROR);
  });

  it("取り消したときは AbortError をそのまま投げる", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new DOMException("aborted", "AbortError");
    });
    await expect(apiGet("/api/x")).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("toMessage", () => {
  it("例外からメッセージを取り出す", () => {
    expect(toMessage(new ApiError(400, "入力の値が正しくありません"))).toBe("入力の値が正しくありません");
    expect(toMessage(new Error("失敗"))).toBe("失敗");
    expect(toMessage("文字")).toBe("文字");
  });
});
