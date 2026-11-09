// 数字・日付・銘柄コードの書式。メールと同じ書式にそろえる

/** 値がないときに表に出す文字 */
export const EMPTY = "-";

/** 曜日の日本語(日曜が 0) */
const WEEKDAYS = ["日", "月", "火", "水", "木", "金", "土"];

/** 値がないか(null・undefined・NaN) */
function isMissing(value: number | null | undefined): value is null | undefined {
  return value === null || value === undefined || Number.isNaN(value);
}

/** 株価: 小数1桁・3桁区切り(例: 1234.5 → "1,234.5") */
export function formatPrice(value: number | null | undefined): string {
  if (isMissing(value)) return EMPTY;
  return value.toLocaleString("ja-JP", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}

/** 件数などの整数: 3桁区切り(例: 3961 → "3,961") */
export function formatInteger(value: number | null | undefined): string {
  if (isMissing(value)) return EMPTY;
  return Math.round(value).toLocaleString("ja-JP");
}

/** 確率・率: % で小数1桁(例: 0.634 → "63.4%") */
export function formatPercent(rate: number | null | undefined): string {
  if (isMissing(rate)) return EMPTY;
  return `${(rate * 100).toFixed(1)}%`;
}

/** 率の差: 符号付きの % で小数1桁(例: 0.064 → "+6.4%"、-0.003 → "-0.3%"、0 → "0.0%") */
export function formatSignedPercent(rate: number | null | undefined): string {
  if (isMissing(rate)) return EMPTY;
  const text = (rate * 100).toFixed(1);
  // 小数1桁にすると 0 になる値(例: 0.0004)は、符号を付けない
  if (Number(text) === 0) return "0.0%";
  return `${rate > 0 ? "+" : ""}${text}%`;
}

/** 上がった・下がったの色のクラス(赤 up・青 dn・灰色 eq) */
export function signClass(value: number | null | undefined): "up" | "dn" | "eq" | "" {
  if (isMissing(value)) return "";
  if (value > 0) return "up";
  if (value < 0) return "dn";
  return "eq";
}

/**
 * 画面に出す銘柄コード(バッチの common/stock_code.py の display_code と同じ)。
 * 5桁で5桁目が 0 なら4桁(13010 → 1301、130A0 → 130A)、それ以外(優先株式など)はそのまま(25935)。
 */
export function displayCode(code: string): string {
  return code.length === 5 && code.endsWith("0") ? code.slice(0, 4) : code;
}

/** 日付を YYYY-MM-DD の文字にする(画面の端末の日付として扱う) */
export function toIsoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** YYYY-MM-DD の文字を日付にする。形が違う・ない日(2026-02-30 など)は null */
export function parseIsoDate(text: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text.trim());
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const date = new Date(y, mo - 1, d);
  if (date.getFullYear() !== y || date.getMonth() !== mo - 1 || date.getDate() !== d) return null;
  return date;
}

/** 日付に曜日を付ける(例: "2026-07-22" → "2026-07-22(水)")。形が違うときはそのまま返す */
export function formatDateWithWeekday(iso: string | null | undefined): string {
  if (!iso) return EMPTY;
  const d = parseIsoDate(iso);
  if (!d) return iso;
  return `${iso}(${WEEKDAYS[d.getDay()]})`;
}

/** 日本時間の日時の部品(年・月・日・時・分) */
function jstParts(isoDateTime: string): Record<string, string> | null {
  const d = new Date(isoDateTime);
  if (Number.isNaN(d.getTime())) return null;
  const parts = new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(d);
  return Object.fromEntries(parts.map((p) => [p.type, p.value]));
}

/** 日時を日本時間の「YYYY-MM-DD HH:MM」にする(API は時刻帯付きの日時を返す) */
export function formatDateTime(isoDateTime: string | null | undefined): string {
  if (!isoDateTime) return EMPTY;
  const p = jstParts(isoDateTime);
  if (!p) return isoDateTime;
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`;
}

/** 日時を日本時間の「HH:MM」にする */
export function formatTime(isoDateTime: string | null | undefined): string {
  if (!isoDateTime) return EMPTY;
  const p = jstParts(isoDateTime);
  if (!p) return isoDateTime;
  return `${p.hour}:${p.minute}`;
}

/** 金額・差額: 符号付きの3桁区切り(例: 13270 → "+13,270"、小数は四捨五入)。digits を渡すと小数の桁数 */
export function formatSignedNumber(value: number | null | undefined, digits = 0): string {
  if (isMissing(value)) return EMPTY;
  const rounded = Number(value.toFixed(digits));
  const text = Math.abs(rounded).toLocaleString("ja-JP", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  if (rounded === 0) return text;
  return `${rounded > 0 ? "+" : "-"}${text}`;
}

/** 倍率(例: 7.94 → "7.9倍") */
export function formatTimes(value: number | null | undefined): string {
  if (isMissing(value)) return EMPTY;
  return `${value.toFixed(1)}倍`;
}

/** 時価総額などの大きな金額を「億円」で(例: 182000000000 → "1,820億円") */
export function formatOku(value: number | null | undefined): string {
  if (isMissing(value)) return EMPTY;
  return `${Math.round(value / 100_000_000).toLocaleString("ja-JP")}億円`;
}

/** 出来高の目盛り(例: 1380000 → "138万") */
export function formatVolume(value: number | null | undefined): string {
  if (isMissing(value)) return EMPTY;
  if (Math.abs(value) >= 10_000) return `${Math.round(value / 10_000).toLocaleString("ja-JP")}万`;
  return Math.round(value).toLocaleString("ja-JP");
}

/**
 * かかった時間(秒)を「1時間23分」「4分05秒」「12秒」の形にする。
 * 値がなければ(実行中など)空の文字
 */
export function formatElapsed(seconds: number | null | undefined): string {
  if (isMissing(seconds)) return "";
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}時間${m}分`;
  if (m > 0) return `${m}分${String(s).padStart(2, "0")}秒`;
  return `${s}秒`;
}

/** YYYY-MM-DD の日付を n 日ずらす */
export function addDays(iso: string, n: number): string {
  const d = parseIsoDate(iso);
  if (!d) return iso;
  d.setDate(d.getDate() + n);
  return toIsoDate(d);
}

/** YYYY-MM-DD の日付の月と日(例: "2026-07-29" → "7/29") */
export function formatMonthDay(iso: string | null | undefined): string {
  const d = iso ? parseIsoDate(iso) : null;
  return d ? `${d.getMonth() + 1}/${d.getDate()}` : EMPTY;
}

/** 長い文字を max 文字までにし、切ったら「…」を付ける */
export function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}
