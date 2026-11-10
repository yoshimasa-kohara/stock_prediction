// 買い候補。予測日の期間(開始日・終了日)を指定して、期間の予測日ごとの買い候補をメールと同じ形で見る。
// 買い候補と買い候補(業種分散)をタブで切り替える
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";

import type {
  CandidateKind,
  CandidatesRangeResponse,
  CandidatesResponse,
  PredictionDates,
} from "../api/types";
import { useApi } from "../api/useApi";
import DateInput from "../components/DateInput";
import ErrorMessage from "../components/ErrorMessage";
import LatestMarketBlock from "../components/LatestMarketBlock";
import PredictionTable from "../components/PredictionTable";
import Tabs from "../components/Tabs";
import { useSettings } from "../context/contexts";
import { defaultFrom } from "../lib/candidateRange";
import {
  KIND_TABS,
  kindDescription,
  kindLabel,
  kindParam,
  parseKind,
} from "../lib/candidateKind";
import { formatDateWithWeekday, formatInteger } from "../lib/format";

/** 自動スクロールの行き先(表の上の説明文)の id。styles.css の scroll-margin-top の対象と合わせる */
const CANDIDATES_TOP_ID = "candidates-top";

/** 予測日1日分(見出し、予測の情報、注記、表) */
function DaySection({
  day,
  kind,
}: {
  day: CandidatesResponse;
  kind: CandidateKind;
}) {
  const settings = useSettings();
  const { main_horizon: main, predict_horizons: horizons } = settings.batch;
  return (
    <section>
      <h2 id={`d${day.pred_date}`}>
        {formatDateWithWeekday(day.pred_date)} の{kindLabel(kind)}
        <span className="h2sub">
          (モデル {day.model_version} ／ 予測した銘柄{" "}
          {formatInteger(day.code_count)} 件) <a href="#top">▲目次へ</a>
        </span>
      </h2>
      {day.fallback_note && <div className="warn">{day.fallback_note}</div>}
      <PredictionTable
        rows={day.candidates}
        horizons={horizons}
        mainHorizon={main}
        emptyMessage="買い候補はありません"
        showSector
      />
    </section>
  );
}

export default function CandidatesPage() {
  const settings = useSettings();
  const [params, setParams] = useSearchParams();
  const kind = parseKind(params.get("kind"));
  const query = {
    from: params.get("from") ?? undefined,
    to: params.get("to") ?? undefined,
  };
  const dates = useApi<PredictionDates>("/api/prediction-dates");

  // 期間: 指定がなければ、終了日 = 最新の予測日、開始日 = 予測がある日を新しい順に数えて N 日分になる日。
  // 予測日の一覧を読めるまでは決められないので、表は読まない
  const all = dates.data?.dates;
  const to = query.to ?? all?.at(-1);
  const from =
    query.from ??
    (all && to
      ? defaultFrom(all, to, settings.web.candidates_default_days)
      : undefined);
  const range = from && to ? { from, to } : null;
  const res = useApi<CandidatesRangeResponse>(
    range ? "/api/candidates/range" : null,
    range ? { ...range, ...kindParam(kind) } : undefined,
  );

  const [form, setForm] = useState<{ from: string; to: string } | null>(null);
  const current = form ?? { from: from ?? "", to: to ?? "" };
  const main = settings.batch.main_horizon;
  const label = kindLabel(kind);
  // 期間を指定していれば、タブを切り替えても同じ期間のまま
  const explicit = {
    ...(query.from ? { from: query.from } : {}),
    ...(query.to ? { to: query.to } : {}),
  };
  // 予測日は新しい日付が上(降順。API は古い順で返すので、画面で並べ替える。目次と表の両方)
  const days = [...(res.data ?? [])].sort((a, b) =>
    b.pred_date.localeCompare(a.pred_date),
  );
  const noPrediction =
    (res.data !== null && res.data.length === 0) ||
    (all !== undefined && all.length === 0 && !range);

  // 自動スクロール: 初期表示と「表示」ボタンの後、一覧が出たら表の上の説明文へ1回だけ動く。
  // pending: "initial" = 初期表示、期間 = 「表示」を押した期間、null = 待っていない。
  // タブの切り替えでは待ちを取り消す(スクロールしない)
  const pending = useRef<"initial" | { from: string; to: string } | null>(
    "initial",
  );
  // 同じ期間で「表示」を押し直したときも、再描画して下の effect を動かすための値
  const [showTick, setShowTick] = useState(0);
  const newest = days[0];
  const newestId = newest ? `d${newest.pred_date}` : null;
  useEffect(() => {
    const p = pending.current;
    if (p === null || res.loading || res.error || !range || newestId === null) {
      return;
    }
    // 「表示」の後は、押した期間の一覧に入れ替わってから(古い一覧では動かない)
    if (p !== "initial" && (p.from !== range.from || p.to !== range.to)) {
      return;
    }
    pending.current = null;
    // 行き先は表の上の説明文(目次のすぐ上)。上端が固定ヘッダーのすぐ下に来る
    document
      .getElementById(CANDIDATES_TOP_ID)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [res.loading, res.error, range?.from, range?.to, newestId, showTick]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <h1 id="top">買い候補</h1>
      <div className="cond">
        予測日
        <DateInput
          label="予測日(開始)"
          value={current.from}
          onChange={(v) => setForm({ ...current, from: v })}
          enabledDates={all}
        />
        〜
        <DateInput
          label="予測日(終了)"
          value={current.to}
          onChange={(v) => setForm({ ...current, to: v })}
          enabledDates={all}
        />
        <button
          type="button"
          className="btn"
          onClick={() => {
            pending.current = { from: current.from, to: current.to };
            setShowTick((t) => t + 1);
            setParams({
              from: current.from,
              to: current.to,
              ...kindParam(kind),
            });
            setForm(null);
          }}
        >
          表示
        </button>
        <span className="condinfo">
          1回に表示するのは最大{settings.web.candidates_range_max_months}か月
        </span>
      </div>
      {/* 日経平均(トップと同じ。選んでいる期間やタブに関係なく、いつも最新の日のもの) */}
      <LatestMarketBlock />
      <div className="cond">
        <Tabs
          label="買い候補の種類"
          items={KIND_TABS}
          value={kind}
          onChange={(k) => {
            pending.current = null; // タブの切り替えではスクロールしない
            setParams({ ...explicit, ...kindParam(k) });
            setForm(null);
          }}
        />
      </div>
      <p className="info" id={CANDIDATES_TOP_ID}>
        予測日ごとの{label}({kindDescription(kind, settings.batch)}
        。メールと同じ内容)。並べ方: 上昇確率が
        {Math.round(settings.batch.up_prob_threshold * 100)}
        %以上で見込みがプラスの銘柄の中で、主となる日数({main}
        日後、緑の見出し)の市場比の確率が高い順。
        {kind === "sector" &&
          `同じ33業種の銘柄は上から${settings.batch.sector_cap_per_sector}銘柄までにする(業種は予測した時点の33業種)。`}
        銘柄名を選ぶと銘柄詳細へ移る。予測のない日(土日・祝日など)は表がありません。
      </p>
      {dates.error && <ErrorMessage message={dates.error} />}
      {res.error && <ErrorMessage message={res.error} />}
      {(dates.loading || res.loading) && <p className="loading">読み込み中…</p>}
      {noPrediction && <p className="info">指定した期間に予測はありません</p>}
      {days.length > 0 && (
        <nav className="toc" aria-label="目次">
          目次:{" "}
          {days.map((d) => (
            <a key={d.pred_date} href={`#d${d.pred_date}`}>
              {formatDateWithWeekday(d.pred_date)}
            </a>
          ))}
        </nav>
      )}
      {days.map((d) => (
        <DaySection key={d.pred_date} day={d} kind={kind} />
      ))}
    </>
  );
}
