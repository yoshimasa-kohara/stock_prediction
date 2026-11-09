// 銘柄詳細の株価チャート。Lightweight Charts(ライトウェイト・チャーツ。TradingView 社。Apache License 2.0)。
// 上の段: ローソク足(上がった日は赤、下がった日は青)、移動平均(5・25・75日)、トレンドライン(破線)、ボリンジャーバンド(5本線)。
// 下の段: 出来高(株。showVolume が false なら出さない。日経平均のチャート)。マウス(スマホは指で押した所)の日の値を枠で出す。
// チャートの作者が TradingView 社であることのロゴのリンク(attributionLogo)は出したままにする
import {
  CandlestickSeries,
  type IChartApi,
  type ISeriesApi,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type MouseEventParams,
  type Time,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  createChart,
  createSeriesMarkers,
} from "lightweight-charts";
import { type CSSProperties, useEffect, useMemo, useRef, useState } from "react";

import type { PriceRow, TrendLine } from "../api/types";
import { CHART_COLORS, VOLUME_PANE_HEIGHT, trendSegments } from "../lib/chart";
import { formatDateWithWeekday, formatPrice, formatVolume } from "../lib/format";

/** 表示の切り替え */
export interface ChartVisibility {
  ma: boolean;
  trend: boolean;
  bb: boolean;
}

interface Props {
  rows: PriceRow[];
  trendlines: TrendLine[];
  visible: ChartVisibility;
  bbDays: number;
  /** チャート全体の高さ(px。PC の画面だけで使う。スマホは styles.css の高さ) */
  height: number;
  /** 出来高の段と、マウスの位置の枠の出来高の行を出すか(省略すると出す。日経平均は出さない) */
  showVolume?: boolean;
}

/** マウスを置いた日の枠の中身 */
interface HoverInfo {
  row: PriceRow;
  /** 枠をマウスの左に出すか(マウスがチャートの右半分にあるとき) */
  left: boolean;
  x: number;
}

/** トレンドラインの1本分の、線の名前と抜けた日の印(表示の切り替えで出す・隠す) */
interface TrendMarkerSet {
  api: ISeriesMarkersPluginApi<Time>;
  markers: SeriesMarker<Time>[];
}

/** ボリンジャーバンドの5本(値の名前、札、色、太さ) */
const BB_LINES: { key: keyof PriceRow; label: string; color: string }[] = [
  { key: "bb_up2", label: "+2σ", color: CHART_COLORS.bb2 },
  { key: "bb_up1", label: "+1σ", color: CHART_COLORS.bb1 },
  { key: "bb_mid", label: "中心線", color: CHART_COLORS.bbMid },
  { key: "bb_dn1", label: "−1σ", color: CHART_COLORS.bb1 },
  { key: "bb_dn2", label: "−2σ", color: CHART_COLORS.bb2 },
];

/** 移動平均の3本 */
const MA_LINES: { key: keyof PriceRow; label: string; color: string }[] = [
  { key: "ma5", label: "5日", color: CHART_COLORS.ma5 },
  { key: "ma25", label: "25日", color: CHART_COLORS.ma25 },
  { key: "ma75", label: "75日", color: CHART_COLORS.ma75 },
];

/** 線の点(値がない日は飛ばす) */
function points(rows: PriceRow[], key: keyof PriceRow) {
  return rows.flatMap((r) => {
    const v = r[key];
    return typeof v === "number" ? [{ time: r.date as Time, value: v }] : [];
  });
}

export default function PriceChart({ rows, trendlines, visible, bbDays, height, showVolume = true }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const groups = useRef<{ ma: ISeriesApi<"Line">[]; trend: ISeriesApi<"Line">[]; bb: ISeriesApi<"Line">[] }>({
    ma: [],
    trend: [],
    bb: [],
  });
  const markersRef = useRef<TrendMarkerSet[]>([]);
  const [hover, setHover] = useState<HoverInfo | null>(null);
  const byDate = useMemo(() => new Map(rows.map((r) => [r.date, r])), [rows]);

  // チャートを作る(データが変わったら作り直す)
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { color: "#ffffff" }, textColor: "#333333", attributionLogo: true, fontSize: 11 },
      localization: { locale: "ja-JP", dateFormat: "yyyy-MM-dd" },
      rightPriceScale: { borderColor: "#cccccc" },
      // 左右の端より外は出さない(拡大・縮小しても、データのない空白を出さない)
      timeScale: { borderColor: "#cccccc", fixLeftEdge: true, fixRightEdge: true },
      // ドラッグ・指でなぞる・ホイールでは動かさない(スマホで指でなぞったときは、画面をスクロールする)
      handleScroll: { mouseWheel: false, pressedMouseMove: false, horzTouchDrag: false, vertTouchDrag: false },
      // 拡大・縮小は、マウスのホイールと2本指だけ。目盛りのドラッグでは変えない(ダブルクリックで元の大きさに戻す)
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: false, axisDoubleClickReset: true },
      grid: { vertLines: { color: "#f0f0f0" }, horzLines: { color: "#f0f0f0" } },
    });
    chartRef.current = chart;
    const candle = chart.addSeries(CandlestickSeries, {
      upColor: CHART_COLORS.up,
      downColor: CHART_COLORS.down,
      borderVisible: false,
      wickUpColor: CHART_COLORS.up,
      wickDownColor: CHART_COLORS.down,
      priceLineVisible: false,
    });
    candle.setData(
      rows.flatMap((r) =>
        r.open !== null && r.high !== null && r.low !== null && r.close !== null
          ? [{ time: r.date as Time, open: r.open, high: r.high, low: r.low, close: r.close }]
          : [],
      ),
    );
    const line = (color: string, title: string, style: LineStyle = LineStyle.Solid, width: 1 | 2 = 1) =>
      chart.addSeries(LineSeries, {
        color,
        lineWidth: width,
        lineStyle: style,
        title,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
    const dates = rows.map((r) => r.date);
    const trendMarkers: TrendMarkerSet[] = [];
    markersRef.current = trendMarkers;
    groups.current = {
      ma: MA_LINES.map((m) => {
        const s = line(m.color, "");
        s.setData(points(rows, m.key));
        return s;
      }),
      // 線を結んだ古いほうの谷(山)から、チャートの右端の日(0円になる日)まで。表示する期間より前から始まる線は、
      // API が期間の最初の日から返す。抜けた日より後は薄い細い線。線の名前は左端の近く、抜けた日には印と文字
      trend: trendlines.flatMap((t) =>
        trendSegments(t, dates).map((seg) => {
          const resist = t.kind === "RESIST";
          const color = seg.faded
            ? resist ? CHART_COLORS.resistFaded : CHART_COLORS.supportFaded
            : resist ? CHART_COLORS.resist : CHART_COLORS.support;
          const s = line(color, "", LineStyle.Dashed, seg.faded ? 1 : 2);
          s.setData(seg.points.map((p) => ({ time: p.time as Time, value: p.value })));
          const markers = seg.markers.map((m) => ({ ...m, time: m.time as Time }));
          trendMarkers.push({ api: createSeriesMarkers(s, markers), markers });
          return s;
        }),
      ),
      bb: BB_LINES.map((b) => {
        const s = line(b.color, b.label, LineStyle.Solid, 1);
        s.setData(points(rows, b.key));
        return s;
      }),
    };
    // 下の段: 出来高
    if (showVolume) {
      const volume = chart.addSeries(
        HistogramSeries,
        { color: CHART_COLORS.volume, priceFormat: { type: "custom", formatter: formatVolume }, priceLineVisible: false },
        1,
      );
      volume.setData(points(rows, "volume").map((p) => ({ ...p, color: CHART_COLORS.volume })));
      chart.panes()[1]?.setHeight(VOLUME_PANE_HEIGHT);
    }
    chart.timeScale().fitContent();

    const onMove = (param: MouseEventParams<Time>) => {
      const row = param.time ? byDate.get(String(param.time)) : undefined;
      if (!row || !param.point) {
        setHover(null);
        return;
      }
      setHover({ row, x: param.point.x, left: param.point.x > el.clientWidth / 2 });
    };
    chart.subscribeCrosshairMove(onMove);
    // 大きさが変わったら(高さの切り替え、画面の幅の変更)、下の段(出来高)の高さを戻す。
    // チャートは大きさが変わると段の高さを割合で広げるので、上の段(株価)だけを高くするため
    let frame = 0;
    const observer =
      !showVolume || typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(() => {
            window.cancelAnimationFrame(frame);
            frame = window.requestAnimationFrame(() => chart.panes()[1]?.setHeight(VOLUME_PANE_HEIGHT));
          });
    observer?.observe(el);
    return () => {
      observer?.disconnect();
      window.cancelAnimationFrame(frame);
      chart.unsubscribeCrosshairMove(onMove);
      chart.remove();
      chartRef.current = null;
      markersRef.current = [];
    };
  }, [rows, trendlines, byDate, showVolume]);

  // 表示の切り替え(作り直さずに、線を出す・隠す)
  useEffect(() => {
    groups.current.ma.forEach((s) => s.applyOptions({ visible: visible.ma }));
    groups.current.trend.forEach((s) => s.applyOptions({ visible: visible.trend }));
    // 線の名前と抜けた日の印も、線と一緒に出す・隠す
    markersRef.current.forEach((m) => m.api.setMarkers(visible.trend ? m.markers : []));
    groups.current.bb.forEach((s) => s.applyOptions({ visible: visible.bb }));
  }, [visible, rows, trendlines]);

  return (
    <div className="chartbox">
      <div ref={box} className="chart" data-testid="price-chart" style={{ "--chart-h": `${height}px` } as CSSProperties} />
      {showVolume && <div className="volume-title">出来高(株)</div>}
      {hover && (
        <div className={`chart-tip ${hover.left ? "left" : "right"}`} style={{ left: hover.left ? undefined : hover.x + 16, right: hover.left ? `calc(100% - ${hover.x - 16}px)` : undefined }}>
          <div className="b">{formatDateWithWeekday(hover.row.date)}</div>
          <div>始値 {formatPrice(hover.row.open)} ／ 高値 {formatPrice(hover.row.high)}</div>
          <div>安値 {formatPrice(hover.row.low)} ／ 終値 {formatPrice(hover.row.close)}</div>
          {visible.ma && (
            <div>
              移動平均 5日 {formatPrice(hover.row.ma5)} ／ 25日 {formatPrice(hover.row.ma25)} ／ 75日 {formatPrice(hover.row.ma75)}
            </div>
          )}
          {visible.bb && (
            <div>
              ボリンジャーバンド({bbDays}日) +2σ {formatPrice(hover.row.bb_up2)} ／ +1σ {formatPrice(hover.row.bb_up1)} ／ 中心線{" "}
              {formatPrice(hover.row.bb_mid)} ／ −1σ {formatPrice(hover.row.bb_dn1)} ／ −2σ {formatPrice(hover.row.bb_dn2)}
            </div>
          )}
          {showVolume && (
            <div>出来高 {hover.row.volume === null ? "-" : Math.round(hover.row.volume).toLocaleString("ja-JP")}株</div>
          )}
        </div>
      )}
    </div>
  );
}
