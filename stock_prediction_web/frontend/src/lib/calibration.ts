// 上昇確率のずれの注意の文(メールと同じ条件・同じ内容。core/notify_core.py の calibration_line)
import type { CalibrationWarning } from "../api/types";
import { formatInteger, formatPercent } from "./format";

/** 1つの帯の注意の文(例: 20日後・60〜70%の帯: 確率の平均 63.4% / 実際に上がった割合 45.4%(30,120件)) */
export function calibrationLine(w: CalibrationWarning): string {
  return (
    `${w.horizon}日後・${w.prob_band}〜${w.prob_band + 10}%の帯: 確率の平均 ${formatPercent(w.avg_up_prob)} / ` +
    `実際に上がった割合 ${formatPercent(w.up_rate)}(${formatInteger(w.verified_count)}件)`
  );
}

/** ずれ(ポイント。符号付き、小数1桁) */
export function gapPoints(gap: number | null): string {
  if (gap === null) return "-";
  const v = Number((gap * 100).toFixed(1));
  return v > 0 ? `+${v.toFixed(1)}` : v.toFixed(1);
}
