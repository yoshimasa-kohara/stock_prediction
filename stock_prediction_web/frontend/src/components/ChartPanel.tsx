// 株価チャートと、その操作(期間、高さ、表示の切り替え)、凡例、注記。
// 銘柄詳細の株価チャートと、トップ・買い候補の「相場」の日経平均のチャート、
// 保有銘柄の売却済みのチャートのダイアログ(期間のタブの代わりに ranges のタブを出す)で使う
import { useMemo, useState } from "react";

import type { PricesResponse } from "../api/types";
import { useApi } from "../api/useApi";
import { useSettings } from "../context/contexts";
import { CHART_COLORS, CHART_HEIGHTS, type ChartHeight, chartHeightPx, loadChartHeight, saveChartHeight } from "../lib/chart";
import ErrorMessage from "./ErrorMessage";
import PriceChart, { type ChartOverlay, type ChartVisibility } from "./PriceChart";
import Tabs from "./Tabs";

/** チャートの期間 */
const PERIODS = [
  { value: "1M", label: "1か月" },
  { value: "3M", label: "3か月" },
  { value: "6M", label: "6か月" },
  { value: "1Y", label: "1年" },
  { value: "ALL", label: "全期間" },
];

/** 期間の代わりに出すタブの1つ(API に渡す日付の範囲。保有銘柄の売却済みのチャート) */
export interface ChartRange {
  value: string;
  label: string;
  /** API に渡す値(start・end など)。period は渡さない */
  query: Record<string, string>;
}

interface Props {
  /** チャートの値の API(/api/stocks/{code}/prices、/api/index/prices) */
  path: string;
  /** API に period と一緒に渡す値(日経平均は予測日) */
  query?: Record<string, string>;
  /** 凡例の後ろに出す注記 */
  note: string;
  /** データが1行もないときの文言 */
  emptyMessage: string;
  /** 選んだ高さをブラウザーに残すときの名前(省略すると銘柄詳細と同じ) */
  heightKey?: string;
  /** 出来高を出すか(省略すると出す) */
  showVolume?: boolean;
  /** データが1行もないときは、操作と凡例を出さずに emptyMessage だけを出す */
  emptyOnly?: boolean;
  /** 期間(1か月〜全期間)のタブの代わりに出すタブ(省略すると期間のタブ。最初のものを初めに選ぶ) */
  ranges?: ChartRange[];
  /** 表示の切り替えの初期値(省略すると移動平均とトレンドラインだけ) */
  initialVisible?: ChartVisibility;
  /** チャートに書き込むもの(API の値から作る。省略すると何も書かない) */
  overlay?: (data: PricesResponse) => ChartOverlay;
}

/** 表示の切り替えの初期値: 移動平均とトレンドラインだけ(ボリンジャーバンドは OFF)。売買の線は overlay を渡す画面だけ */
const DEFAULT_VISIBLE: ChartVisibility = { ma: true, trend: true, bb: false, trade: false };

export default function ChartPanel({
  path,
  query,
  note,
  emptyMessage,
  heightKey,
  showVolume = true,
  emptyOnly = false,
  ranges,
  initialVisible = DEFAULT_VISIBLE,
  overlay,
}: Props) {
  const settings = useSettings();
  const [period, setPeriod] = useState(ranges?.[0]?.value ?? settings.web.chart_default_period);
  const [visible, setVisible] = useState<ChartVisibility>(initialVisible);
  // チャートの高さ(PC の画面だけ。前に選んだ高さをブラウザーから読む)
  const [height, setHeight] = useState<ChartHeight>(() => loadChartHeight(heightKey));
  const changeHeight = (value: ChartHeight) => {
    setHeight(value);
    saveChartHeight(value, heightKey);
  };
  // ranges を渡したときは、選んだタブの日付の範囲を渡す(period は渡さない)
  const range = ranges?.find((r) => r.value === period);
  const prices = useApi<PricesResponse>(path, range ? { ...query, ...range.query } : { ...query, period });
  const marks = useMemo(() => (overlay && prices.data ? overlay(prices.data) : undefined), [overlay, prices.data]);
  const empty = prices.data !== null && prices.data.rows.length === 0;
  if (emptyOnly && empty) return <p className="info">{emptyMessage}</p>;
  const check = (key: keyof ChartVisibility, label: string) => (
    <label className="check">
      <input type="checkbox" checked={visible[key]} onChange={(e) => setVisible({ ...visible, [key]: e.target.checked })} />
      {label}
    </label>
  );
  return (
    <>
      <div className="chartbar">
        <Tabs
          label="チャートの期間"
          items={ranges ? ranges.map((r) => ({ value: r.value, label: r.label })) : PERIODS}
          value={period}
          onChange={setPeriod}
        />
        <span className="pconly heightsel">
          高さ
          <Tabs
            label="チャートの高さ"
            items={CHART_HEIGHTS.map((h) => ({ value: h.value, label: h.label }))}
            value={height}
            onChange={changeHeight}
          />
        </span>
        <span className="checks">
          {overlay && check("trade", "売買の線")}
          {check("ma", "移動平均")}
          {check("trend", "トレンドライン")}
          {check("bb", "ボリンジャーバンド")}
        </span>
      </div>
      <div className="legend">
        {overlay && visible.trade && (
          <span>
            <i style={{ background: CHART_COLORS.up }} />
            <i style={{ background: CHART_COLORS.down }} />
            売買の線(上がり赤・下がり青)
          </span>
        )}
        {visible.ma && (
          <>
            <span>
              <i style={{ background: CHART_COLORS.ma5 }} />
              5日
            </span>
            <span>
              <i style={{ background: CHART_COLORS.ma25 }} />
              25日
            </span>
            <span>
              <i style={{ background: CHART_COLORS.ma75 }} />
              75日
            </span>
          </>
        )}
        {visible.trend && (
          <>
            <span>
              <i style={{ background: CHART_COLORS.resist }} />
              上値抵抗線(破線)
            </span>
            <span>
              <i style={{ background: CHART_COLORS.support }} />
              下値支持線(破線)
            </span>
          </>
        )}
        {visible.bb && (
          <>
            <span>
              <i style={{ background: CHART_COLORS.bb2 }} />
              ボリンジャーバンド ±2σ({prices.data?.bb_days ?? settings.batch.bb_days}日)
            </span>
            <span>
              <i style={{ background: CHART_COLORS.bb1 }} />
              ±1σ
            </span>
            <span>
              <i style={{ background: CHART_COLORS.bbMid }} />
              中心線
            </span>
          </>
        )}
        <span>{note}</span>
      </div>
      {prices.error && <ErrorMessage message={prices.error} />}
      {prices.loading && <p className="loading">読み込み中…</p>}
      {empty && <p className="info">{emptyMessage}</p>}
      {prices.data && prices.data.rows.length > 0 && (
        <PriceChart
          rows={prices.data.rows}
          trendlines={prices.data.trendlines}
          visible={visible}
          bbDays={prices.data.bb_days}
          height={chartHeightPx(height)}
          showVolume={showVolume}
          overlay={marks}
        />
      )}
      {prices.data && visible.trend && prices.data.rows.length > 0 && prices.data.trendlines.length === 0 && (
        <p className="info">接触2回以上のトレンドラインはありません</p>
      )}
    </>
  );
}
