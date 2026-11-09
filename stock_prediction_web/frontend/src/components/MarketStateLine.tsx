// 相場の状態(強い・普通・弱い)の表示。メールと同じ文言。相場の状態には色を付けない(赤・青を使わない)。
// 予測ではなく、今の相場の状態を事実として示す。計算できない日(market_state が null)は「-」
// 次の行に、日経平均の向き(10・20・60日の回帰直線の傾き)を出す。出さない日(index_trend が null)は行ごと出さない。
// 向きの「上向き」は赤、「下向き」は青、「横ばい」は色なし(画面のほかの所と同じ。メールは色を付けない)
import type { IndexTrendItem, MarketState } from "../api/types";
import { CHART_COLORS } from "../lib/chart";
import { formatPercent, formatSignedPercent } from "../lib/format";

const NOTE = "※今の相場の状態を表すもので、この先の上げ下げの予測ではありません";

/** 1行目の文言(例: 相場の状態: 弱い(25日移動平均より上の銘柄 32.1%、直近20営業日の騰落率の中央値 -2.1%)) */
function marketStateText(state: MarketState | null): string {
  if (!state) return "相場の状態: -";
  return (
    `相場の状態: ${state.state}(25日移動平均より上の銘柄 ${formatPercent(state.above25)}、` +
    `直近20営業日の騰落率の中央値 ${formatSignedPercent(state.ret20)})`
  );
}

/** 割合を符号付きの小数2桁の % にする(0.0021 → "+0.21%")。小数2桁で 0 になる値は "+0.00%"(バッチと同じ) */
function signedRate(rate: number): string {
  const text = (Math.abs(rate) * 100).toFixed(2);
  return `${rate < 0 && text !== "0.00" ? "-" : "+"}${text}%`;
}

/** 向きの文字の色(上向きは赤、下向きは青、横ばいは色なし) */
function directionColor(direction: IndexTrendItem["direction"]): string | undefined {
  if (direction === "上向き") return CHART_COLORS.up;
  if (direction === "下向き") return CHART_COLORS.down;
  return undefined;
}

/** 2行目(例: 日経平均の向き: 10日 上向き(+0.21%/日)/20日 横ばい(+0.03%/日)/60日 下向き(-0.08%/日)) */
function IndexTrendLine({ items }: { items: IndexTrendItem[] }) {
  return (
    <div>
      日経平均の向き:{" "}
      {items.map((t, i) => (
        <span key={t.days}>
          {i > 0 && "/"}
          {t.days}日 <span style={{ color: directionColor(t.direction) }}>{t.direction}</span>({signedRate(t.slope)}/日)
        </span>
      ))}
    </div>
  );
}

export default function MarketStateLine({ state }: { state: MarketState | null }) {
  const trend = state?.index_trend;
  return (
    <div className="mstate">
      <div>{marketStateText(state)}</div>
      {trend && <IndexTrendLine items={trend} />}
      <div className="sub">{NOTE}</div>
    </div>
  );
}
