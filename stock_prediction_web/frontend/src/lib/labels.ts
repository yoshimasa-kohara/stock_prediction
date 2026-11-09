// 画面の表示に使う、区分の値と見た目の対応

/** 判定の文字の色のクラス(売却は赤、注意・乗り換えは橙) */
export function judgmentClass(judgment: string): string {
  if (judgment.startsWith("売却")) return "jr";
  if (judgment.startsWith("保持(注意") || judgment.startsWith("乗り換え")) return "jo";
  return "";
}

/** 実行履歴の状態の印のクラス(画面見本の .badge) */
export function statusBadgeClass(status: string): string {
  switch (status) {
    case "SUCCESS":
      return "badge b-ok";
    case "FAILED":
      return "badge b-ng";
    case "RUNNING":
      return "badge b-run";
    default:
      return "badge b-empty";
  }
}

/** 状態の文字の色のクラス(トップのカード) */
export function statusTextClass(status: string): string {
  switch (status) {
    case "SUCCESS":
      return "ok";
    case "FAILED":
      return "ng";
    case "RUNNING":
      return "run";
    default:
      return "";
  }
}

/** 答え合わせの状態の日本語 */
export function verifyLabel(status: string, isHit: boolean | null): string {
  if (status === "VERIFIED") return isHit ? "的中" : "外れ";
  if (status === "UNVERIFIABLE") return "検証不能";
  return "未検証";
}

/** 的中率の期間の日本語 */
export const PERIOD_LABELS: Record<string, string> = {
  "1M": "直近1か月",
  "3M": "直近3か月",
  "6M": "直近6か月",
  "1Y": "直近1年",
  "2Y": "直近2年",
  ALL: "全期間",
};
