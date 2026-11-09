// バッチ実行中の表示。実行中の処理があれば、全画面の上に出す
import type { RunningRun } from "../api/types";
import { useSettings } from "../context/contexts";
import { formatTime } from "../lib/format";

/** 表示に使う行を選ぶ。日次バッチ・週次バッチの全体の行があればそれを、なければ一番古い行を使う */
function pickRun(running: RunningRun[]): RunningRun | undefined {
  return running.find((r) => r.process_name === "DAILY_BATCH" || r.process_name === "WEEKLY_BATCH") ?? running[0];
}

export default function RunningBanner({ running }: { running: RunningRun[] }) {
  const settings = useSettings();
  const run = pickRun(running);
  if (!run) return null;
  const label = settings.batch.process_labels[run.process_name] ?? run.process_name;
  return (
    <div className="banner" role="status">
      ⚠ バッチを実行中です({label} {formatTime(run.started_at)} 開始)。予測や検証の値が途中の可能性があります。
    </div>
  );
}
