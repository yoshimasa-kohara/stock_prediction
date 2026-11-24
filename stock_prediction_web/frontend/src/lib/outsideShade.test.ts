import type { SeriesAttachedParameter, Time } from "lightweight-charts";
import { vi } from "vitest";

import { OutsideShade } from "./outsideShade";

/** 日付 → 行の番号。行の番号 n の中心の x は n × 10(ピクセル) */
const INDEX: Record<string, number> = { "2026-04-13": 0, "2026-04-14": 1, "2026-06-12": 2, "2026-06-15": 3 };

function paint(shade: OutsideShade, pixelRatio = 1, width = 40) {
  const timeScale = {
    timeToIndex: (t: string) => INDEX[t] ?? null,
    logicalToCoordinate: (l: number) => l * 10,
    options: () => ({ barSpacing: 10 }),
  };
  shade.attached({ chart: { timeScale: () => timeScale } } as unknown as SeriesAttachedParameter<Time>);
  const fillRect = vi.fn();
  const context = { fillRect, fillStyle: "" };
  const target = {
    useBitmapCoordinateSpace: (f: (scope: unknown) => void) =>
      f({ context, bitmapSize: { width, height: 100 }, horizontalPixelRatio: pixelRatio }),
  };
  const view = shade.paneViews()[0]!;
  expect(view.zOrder?.()).toBe("bottom");
  const renderer = view.renderer()!;
  renderer.draw(target as never);
  renderer.drawBackground?.(target as never);
  return { fillRect, context };
}

describe("持っていなかった期間の背景(OutsideShade)", () => {
  it("持っていた最初の日の左と、最後の日の右を塗る(境目は隣の日との真ん中)", () => {
    const { fillRect, context } = paint(new OutsideShade({ first: "2026-04-14", last: "2026-06-12" }, "gray"));
    expect(context.fillStyle).toBe("gray");
    expect(fillRect.mock.calls).toEqual([
      [0, 0, 5, 100],
      [25, 0, 15, 100],
    ]);
  });

  it("画面の拡大率を掛ける。外にはみ出す所は塗らない", () => {
    const { fillRect } = paint(new OutsideShade({ first: "2026-04-13", last: "2026-06-15" }, "gray"), 2, 60);
    // 左端は -5 × 2 なので塗らない。右端は 35 × 2 = 70 で幅 60 より外なので塗らない
    expect(fillRect).not.toHaveBeenCalled();
  });

  it("持っていた期間がなければ全てを塗る。チャートにない日付なら、その側は塗らない。外したら何もしない", () => {
    expect(paint(new OutsideShade("all", "gray")).fillRect.mock.calls).toEqual([[0, 0, 40, 100]]);
    expect(paint(new OutsideShade({ first: "2026-01-01", last: "2026-06-12" }, "gray")).fillRect.mock.calls).toEqual([
      [25, 0, 15, 100],
    ]);
    const shade = new OutsideShade("all", "gray");
    shade.detached();
    const fillRect = vi.fn();
    shade.paneViews()[0]!.renderer()!.drawBackground?.({ useBitmapCoordinateSpace: fillRect } as never);
    expect(fillRect).not.toHaveBeenCalled();
  });
});
