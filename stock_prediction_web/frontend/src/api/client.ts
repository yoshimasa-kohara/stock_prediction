// API の呼び出し。画面と API は同じ場所にあるので、パスは /api/... だけを書く

/** API のエラー。message は画面にそのまま出せる日本語 */
export class ApiError extends Error {
  /** HTTP の状態コード。サーバーに届かなかったときは 0 */
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** サーバーに届かなかったときのメッセージ */
export const MSG_NETWORK_ERROR =
  "サーバーに接続できません。Web サーバーが起動しているか(サーバーが起動しているか)確認してください";

/** クエリの値(undefined・null・空の文字は付けない) */
export type QueryValue = string | number | boolean | null | undefined;

/** パスとクエリから URL を作る(例: ("/api/candidates", {date: "2026-07-22"}) → "/api/candidates?date=2026-07-22") */
export function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === undefined || value === null || value === "") continue;
    params.append(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

/** エラーの返り値から、日本語のメッセージを取り出す(API は {"detail": "..."} を返す) */
async function errorMessage(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json();
    if (body && typeof body === "object" && "detail" in body && typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // JSON でない返り値(中継のエラーなど)は、下の決まった文にする
  }
  return `エラーが発生しました(状態コード ${res.status})`;
}

/** API を GET で呼び、JSON を返す。失敗したら ApiError を投げる */
export async function apiGet<T>(path: string, query?: Record<string, QueryValue>, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(buildUrl(path, query), { headers: { Accept: "application/json" }, signal });
  } catch (e) {
    // 画面を移ったなどで取り消したときは、そのまま投げる(呼び出し元で無視する)
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, MSG_NETWORK_ERROR);
  }
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  return (await res.json()) as T;
}

/** API を POST・PUT で呼び、JSON を返す。失敗したら ApiError を投げる */
export async function apiSend<T>(method: "POST" | "PUT", path: string, body: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, MSG_NETWORK_ERROR);
  }
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  return (await res.json()) as T;
}

/** 例外から、画面に出すメッセージを取り出す */
export function toMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}
