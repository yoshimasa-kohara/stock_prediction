// lightweight-charts の差し替え(jsdom では canvas で描けないため)。作った線と、マウスの動きの関数を残す
import { vi } from "vitest";

export interface FakeSeries {
  kind: string;
  options: Record<string, unknown>;
  data: unknown[];
  setData: (d: unknown[]) => void;
  applyOptions: (o: Record<string, unknown>) => void;
}

/** 線に付けた文字・印(createSeriesMarkers) */
export interface FakeMarkers {
  series: FakeSeries;
  markers: Record<string, unknown>[];
}

export const chartState: {
  series: FakeSeries[];
  markers: FakeMarkers[];
  move: ((p: { time?: string; point?: { x: number; y: number } }) => void) | null;
  removed: number;
} = { series: [], markers: [], move: null, removed: 0 };

export function resetChart() {
  chartState.series = [];
  chartState.markers = [];
  chartState.move = null;
  chartState.removed = 0;
}

export function createChartMock() {
  return {
    CandlestickSeries: "Candlestick",
    LineSeries: "Line",
    HistogramSeries: "Histogram",
    LineStyle: { Solid: 0, Dotted: 1, Dashed: 2 },
    createSeriesMarkers: vi.fn((series: FakeSeries, markers: Record<string, unknown>[]) => {
      const m: FakeMarkers = { series, markers };
      chartState.markers.push(m);
      return {
        setMarkers(next: Record<string, unknown>[]) {
          m.markers = next;
        },
      };
    }),
    createChart: vi.fn(() => ({
      addSeries: (kind: string, options: Record<string, unknown>) => {
        const s: FakeSeries = {
          kind,
          options: { ...options },
          data: [],
          setData(d) {
            s.data = d;
          },
          applyOptions(o) {
            s.options = { ...s.options, ...o };
          },
        };
        chartState.series.push(s);
        return s;
      },
      panes: () => [{ setHeight: vi.fn() }, { setHeight: vi.fn() }],
      timeScale: () => ({ fitContent: vi.fn() }),
      subscribeCrosshairMove: (f: typeof chartState.move) => {
        chartState.move = f;
      },
      unsubscribeCrosshairMove: () => {
        chartState.move = null;
      },
      remove: () => {
        chartState.removed += 1;
      },
    })),
  };
}
