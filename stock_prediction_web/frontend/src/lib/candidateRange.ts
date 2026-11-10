// 買い候補の期間の初期値

/**
 * 期間の初期値の開始日。終了日以前の予測日を新しい順に days 日分数えた、一番古い日。
 * 予測日が days 日に満たなければ一番古い予測日。終了日以前に予測日がなければ終了日。
 * @param dates 予測がある日(古い順。YYYY-MM-DD)
 */
export function defaultFrom(dates: string[], to: string, days: number): string {
  const upto = dates.filter((d) => d <= to);
  return upto[Math.max(0, upto.length - days)] ?? to;
}
