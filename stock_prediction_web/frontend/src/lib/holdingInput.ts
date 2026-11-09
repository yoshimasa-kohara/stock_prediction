// 保有銘柄の入力のチェック(API と同じ条件)
import { toIsoDate } from "./format";

/** 数の入力(3桁区切りのカンマも入れてよい)を数にする。数でなければ NaN */
export function parseNumber(text: string): number {
  const t = text.replace(/,/g, "").trim();
  return t === "" ? Number.NaN : Number(t);
}

/** 今日(画面の端末の日付) */
export function todayIso(): string {
  return toIsoDate(new Date());
}

/** 買値・買い株数・購入日の画面でのチェック(API と同じ条件)。問題がなければ null */
export function checkLot(price: number, quantity: number, buyDate: string): string | null {
  if (!(price > 0)) return "買値は0より大きい値を入れてください";
  if (!Number.isInteger(quantity) || quantity <= 0) return "買い株数は1以上の整数を入れてください";
  if (!buyDate) return "購入日を入れてください";
  if (buyDate > todayIso()) return "購入日は今日以前の日付を入れてください";
  return null;
}

