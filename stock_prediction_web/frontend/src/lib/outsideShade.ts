// 保有銘柄の売却済みのチャートで、持っていなかった期間(購入日より前と、売却日より後)の背景を塗る。
// Lightweight Charts v5 のプリミティブ(series に付ける部品。attachPrimitive)で、線やローソク足の下(背景)に描く
import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesPrimitive,
  Logical,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";

/** 描く先(fancy-canvas の CanvasRenderingTarget2D。lightweight-charts の型から取る) */
type Target = Parameters<IPrimitivePaneRenderer["draw"]>[0];

/** 持っていた期間(最初と最後の行の日付)。"all" は表示する行に持っていた期間がない(全てを塗る) */
export type HeldRange = { first: string; last: string } | "all";

export class OutsideShade implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private readonly views: IPrimitivePaneView[];

  constructor(
    private readonly held: HeldRange,
    private readonly color: string,
  ) {
    const renderer: IPrimitivePaneRenderer = {
      draw: () => undefined,
      drawBackground: (target) => this.paint(target),
    };
    this.views = [{ zOrder: () => "bottom", renderer: () => renderer }];
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this.chart = param.chart;
  }

  detached(): void {
    this.chart = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this.views;
  }

  /** 持っていた期間の外を塗る。境目は、持っていた最初の日・最後の日のローソク足と隣の日の真ん中 */
  private paint(target: Target): void {
    const chart = this.chart;
    if (!chart) return;
    const ts = chart.timeScale();
    const held = this.held;
    target.useBitmapCoordinateSpace(({ context, bitmapSize, horizontalPixelRatio }) => {
      const width = bitmapSize.width;
      const height = bitmapSize.height;
      context.fillStyle = this.color;
      if (held === "all") {
        context.fillRect(0, 0, width, height);
        return;
      }
      // 日付のローソク足の中心から、棒の間隔(barSpacing)の半分 × side だけずらした所の x(ピクセル)。チャートにない日付なら null。
      // logicalToCoordinate は小数の位置を渡すと 0 を返すので、整数の位置の x に半分の間隔を足し引きする
      const half = ts.options().barSpacing / 2;
      const xOf = (time: string, side: -1 | 1): number | null => {
        const index = ts.timeToIndex(time as Time, false);
        if (index === null) return null;
        const x = ts.logicalToCoordinate(index as unknown as Logical);
        return x === null ? null : (x + side * half) * horizontalPixelRatio;
      };
      const left = xOf(held.first, -1);
      const right = xOf(held.last, 1);
      if (left !== null && left > 0) context.fillRect(0, 0, Math.min(left, width), height);
      if (right !== null && right < width) {
        const x = Math.max(right, 0);
        context.fillRect(x, 0, width - x, height);
      }
    });
  }
}
