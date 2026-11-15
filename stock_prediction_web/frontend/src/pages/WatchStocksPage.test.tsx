import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { WatchStocksResponse } from "../api/types";
import { WATCH_CODES, WATCH_STOCKS } from "../test/apiData";
import type { FakeResponse } from "../test/fixtures";
import { type Handler, renderApp, urlsOf } from "../test/renderApp";

const LIST = "/api/watch-stocks";

/** 一覧と、追加済みの銘柄のコードを返す。PUT・DELETE は send で決める */
function handler(
  list: () => WatchStocksResponse = () => WATCH_STOCKS,
  send: (method: string, path: string) => FakeResponse | undefined = () => undefined,
): Handler {
  return (p, _q, m) => {
    if (m !== "GET") return send(m, p);
    if (p === "/api/watch-stocks/codes") return { body: WATCH_CODES };
    if (p === LIST) return { body: list() };
    return undefined;
  };
}

/** 銘柄の2行(上: 追加した日、下: 最新)のセルの文字 */
function rowsOf(name: string): [HTMLElement[], HTMLElement[]] {
  const tbody = screen.getByRole("link", { name }).closest("tbody") as HTMLElement;
  const [top, bottom] = Array.from(tbody.querySelectorAll("tr"));
  return [Array.from(top!.querySelectorAll("td")), Array.from(bottom!.querySelectorAll("td"))];
}

describe("検討中銘柄", () => {
  it("1銘柄を2行(追加した日・最新)で出し、最新の行は追加した日と比べて色を付ける", async () => {
    renderApp("/watch-stocks", handler());
    expect(await screen.findByRole("heading", { level: 1, name: "検討中銘柄" })).toBeInTheDocument();
    await screen.findByRole("link", { name: "サンプル商事A" });
    // 見出し: 買い候補の表から順位を除き、区分・値上がり率・メモ・追加した日・操作を足す。主となる20日後は緑
    const heads = Array.from(document.querySelectorAll("thead tr:first-child th")).map((th) => th.textContent);
    expect(heads).toEqual([
      "コード", "銘柄名", "市場", "業種", "区分", "予測日の株価", "5日後", "10日後", "20日後", "60日後",
      "値上がり率", "備考", "メモ", "追加した日", "操作",
    ]); // prettier-ignore
    expect(screen.getByRole("columnheader", { name: "20日後" })).toHaveClass("main");
    expect(screen.getByRole("columnheader", { name: "銘柄名" })).toHaveClass("fix");

    const [top, bottom] = rowsOf("サンプル商事A");
    // 上の行: コード・銘柄名・市場・業種・メモ・追加した日・操作は2行をまとめる
    expect(top.map((td) => td.textContent).slice(0, 6)).toEqual([
      "1112", "サンプル商事A", "スタンダード", "卸売業", "追加した日(予測日 2026-07-17)", "400.0",
    ]); // prettier-ignore
    for (const i of [0, 1, 2, 3]) expect(top[i]).toHaveAttribute("rowspan", "2");
    expect(top[1]).toHaveClass("fix");
    expect(top[1]!.querySelector("a")).toHaveAttribute("href", "/stocks/11120");
    // 上の行の値は通常の色。主となる日数(20日後)は太字
    expect(top[6]).toHaveTextContent("80.0%");
    expect(top[6]).not.toHaveClass("up");
    expect(top[12]).toHaveClass("b");
    // メモは改行を保つ。追加した日は created_at の日付
    const memo = top.find((td) => td.classList.contains("memo"))!;
    expect(memo.querySelector(".memotext")!.textContent).toBe("決算の後に買う\n2行目");
    expect(memo).toHaveAttribute("rowspan", "2");
    expect(top.at(-2)).toHaveTextContent("2026-07-17");

    // 下の行: 現在値は上がった(赤)、市場比は同じ(通常の色)、上昇確率は下がった(青)、予測株価は上がった(赤)
    expect(bottom[0]).toHaveTextContent("最新(予測日 2026-07-22)");
    expect(bottom[1]).toHaveTextContent("443.0");
    expect(bottom[1]).toHaveClass("up");
    expect(bottom[2]).toHaveTextContent("80.0%");
    expect(bottom[2]).not.toHaveClass("up");
    expect(bottom[2]).not.toHaveClass("dn");
    expect(bottom[3]).toHaveTextContent("45.0%");
    expect(bottom[3]).toHaveClass("dn");
    expect(bottom[4]).toHaveTextContent("456.1");
    expect(bottom[4]).toHaveClass("up");
    expect(bottom[8]).toHaveClass("b"); // 20日後の市場比
    // 値上がり率は下の行に出す(プラスは赤)。備考は行ごと
    expect(bottom[14]).toHaveTextContent("+10.8%");
    expect(bottom[14]).toHaveClass("up");
    expect(top[18]).toHaveTextContent("");
    expect(bottom[15]).toHaveTextContent("流動性注意");
  });

  it("予測がない項目は「-」。値下がりは青。メモがなくても「編集」は出す", async () => {
    renderApp("/watch-stocks", handler());
    await screen.findByRole("link", { name: "サンプルETF" });
    const [top, bottom] = rowsOf("サンプルETF");
    expect(top[5]).toHaveTextContent("2,000.0");
    expect(top.slice(6, 18).map((td) => td.textContent)).toEqual(Array(12).fill("-"));
    expect(bottom[1]).toHaveTextContent("1,900.0");
    expect(bottom[1]).toHaveClass("dn");
    expect(bottom.slice(2, 14).map((td) => td.className)).toEqual(
      Array.from({ length: 12 }, (_, i) => (i >= 6 && i <= 8 ? "n b" : "n")),
    );
    expect(bottom[14]).toHaveTextContent("-5.0%");
    expect(bottom[14]).toHaveClass("dn");
    expect(top.find((td) => td.classList.contains("memo"))!.querySelector(".memotext")).toBeNull();
    expect(screen.getByRole("button", { name: "サンプルETFのメモを編集" })).toBeInTheDocument();
  });

  it("予測日・現在値・値上がり率がないときは「予測なし」「-」を出す(色は付けない)", async () => {
    const row = WATCH_STOCKS.rows[1]!;
    const none = { ...row.picked, pred_date: null, close: null };
    renderApp(
      "/watch-stocks",
      handler(() => ({ latest_pred_date: null, rows: [{ ...row, name: "", picked: none, latest: none, change_rate: null }] })),
    );
    // 銘柄名がないときは、リンクの文字を銘柄コードにする
    const tbody = (await screen.findByRole("link", { name: "13050" })).closest("tbody") as HTMLElement;
    expect(within(tbody).getByText("追加した日(予測なし)")).toBeInTheDocument();
    const bottom = Array.from(tbody.querySelectorAll("tr")[1]!.querySelectorAll("td"));
    expect(bottom[0]).toHaveTextContent("最新(予測なし)");
    expect(bottom[1]).toHaveTextContent("-");
    expect(bottom[1]!.className).toBe("n");
    expect(bottom[14]).toHaveTextContent("-");
    expect(bottom[14]!.className).toBe("n");
    expect(screen.getByRole("button", { name: "1305を削除" })).toBeInTheDocument();
  });

  it("1件もないときは「検討中銘柄はありません」と出す", async () => {
    const calls = renderApp("/watch-stocks", (p) => (p === LIST ? { body: { latest_pred_date: null, rows: [] } } : undefined));
    expect(await screen.findByText("検討中銘柄はありません")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    expect(urlsOf(calls, LIST)).toHaveLength(1);
  });

  it("一覧を読めなければ、赤い枠でエラーを出す", async () => {
    renderApp("/watch-stocks", (p) => (p === LIST ? { status: 503, body: { detail: "DB に接続できません" } } : undefined));
    expect(await screen.findByRole("alert")).toHaveTextContent("DB に接続できません");
  });

  it("「編集」でメモを直すと、一覧を読み直す。保存できなければダイアログにエラーを出す", async () => {
    const user = userEvent.setup();
    let memo = "決算の後に買う\n2行目";
    let fail = true;
    const calls = renderApp(
      "/watch-stocks",
      handler(
        () => ({ ...WATCH_STOCKS, rows: [{ ...WATCH_STOCKS.rows[0]!, memo }] }),
        () => {
          if (fail) return { status: 400, body: { detail: "メモは500文字以内にしてください" } };
          memo = "直したメモ";
          return { body: { code: "11120", created: false, message: "メモを保存しました" } };
        },
      ),
    );
    await user.click(await screen.findByRole("button", { name: "サンプル商事Aのメモを編集" }));
    const dialog = screen.getByRole("dialog", { name: "検討中銘柄のメモを編集" });
    expect(within(dialog).getByText("1112 サンプル商事A")).toBeInTheDocument();
    const box = within(dialog).getByRole("textbox", { name: "メモ" });
    expect(box).toHaveValue("決算の後に買う\n2行目");
    expect(box).toHaveAttribute("maxlength", "500");
    await user.clear(box);
    await user.type(box, "直したメモ");
    expect(within(dialog).getByText("5 / 500 文字(空でもよい)")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "保存" }));
    expect(await within(dialog).findByText("メモは500文字以内にしてください")).toBeInTheDocument();
    fail = false;
    await user.click(within(dialog).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByRole("status")).toHaveTextContent("メモを保存しました");
    expect(await screen.findByText("直したメモ")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "PUT").at(-1)).toEqual({
      url: "/api/watch-stocks/11120",
      method: "PUT",
      body: { memo: "直したメモ" },
    });
    expect(urlsOf(calls, LIST)).toHaveLength(2);
  });

  it("「削除」は確認のダイアログを出してから削除する。「キャンセル」では削除しない", async () => {
    const user = userEvent.setup();
    let rows = WATCH_STOCKS.rows;
    let fail = true;
    const calls = renderApp(
      "/watch-stocks",
      handler(
        () => ({ ...WATCH_STOCKS, rows }),
        () => {
          if (fail) return { status: 404, body: { detail: "検討中銘柄に入っていません" } };
          rows = rows.slice(1);
          return { body: { code: "11120", created: false, message: "検討中銘柄から削除しました" } };
        },
      ),
    );
    await user.click(await screen.findByRole("button", { name: "サンプル商事Aを削除" }));
    let dialog = screen.getByRole("dialog", { name: "検討中銘柄の削除" });
    expect(within(dialog).getByText("サンプル商事A(1112)を検討中銘柄から削除しますか?")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "キャンセル" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "サンプル商事Aを削除" }));
    dialog = screen.getByRole("dialog", { name: "検討中銘柄の削除" });
    await user.click(within(dialog).getByRole("button", { name: "削除" }));
    expect(await within(dialog).findByText("検討中銘柄に入っていません")).toBeInTheDocument();
    fail = false;
    await user.click(within(dialog).getByRole("button", { name: "削除" }));
    await waitFor(() => expect(screen.queryByRole("link", { name: "サンプル商事A" })).toBeNull());
    expect(screen.getByRole("status")).toHaveTextContent("検討中銘柄から削除しました");
    expect(screen.getByRole("link", { name: "サンプルETF" })).toBeInTheDocument();
    const del = calls.filter((c) => c.method === "DELETE").at(-1);
    expect(del).toEqual({ url: "/api/watch-stocks/11120", method: "DELETE", body: undefined });
  });
});
