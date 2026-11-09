import { render, screen } from "@testing-library/react";

import type { RunningRun } from "../api/types";
import { SettingsContext } from "../context/contexts";
import { SETTINGS } from "../test/fixtures";
import RunningBanner from "./RunningBanner";

function run(process_name: string, started_at: string, run_id = 1): RunningRun {
  return { run_id, batch_type: "DAILY", process_name, target_date: "2026-07-22", started_at };
}

function renderBanner(running: RunningRun[]) {
  render(
    <SettingsContext value={SETTINGS}>
      <RunningBanner running={running} />
    </SettingsContext>,
  );
}

describe("RunningBanner(バッチ実行中の表示)", () => {
  it("実行中の処理がなければ何も出さない", () => {
    renderBanner([]);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("日次バッチの全体の行があれば、その名前と開始の時刻を出す", () => {
    renderBanner([run("COLLECT", "2026-07-22T18:59:00+09:00", 1), run("DAILY_BATCH", "2026-07-22T19:00:05+09:00", 2)]);
    expect(screen.getByRole("status")).toHaveTextContent(
      "⚠ バッチを実行中です(日次バッチ 19:00 開始)。予測や検証の値が途中の可能性があります。",
    );
  });

  it("単独で起動した処理だけなら、その処理の名前を出す", () => {
    renderBanner([run("TRAIN", "2026-07-25T10:00:00+09:00")]);
    expect(screen.getByRole("status")).toHaveTextContent("(学習 10:00 開始)");
  });

  it("知らない処理の名前は、そのまま出す", () => {
    renderBanner([run("NEW_PROCESS", "2026-07-25T10:00:00+09:00")]);
    expect(screen.getByRole("status")).toHaveTextContent("(NEW_PROCESS 10:00 開始)");
  });
});
