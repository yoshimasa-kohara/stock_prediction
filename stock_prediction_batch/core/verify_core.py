"""検証の本体。

過去の予測を実際の株価と比べて答え合わせをし、prediction の検証用のカラムを更新する。行は削除しない。
あわせて、予測日ごとの集計、期間の集計(1M・3M・ALL)、上昇確率の確かさの集計を作り直す。
集計には、市場の中央値(同じ予測日・予測日数の全銘柄の騰落率の中央値)を上回った件数と差も含める。
業種の中央値(同じ予測日・予測日数・同じ33業種の全銘柄の騰落率の中央値)を上回った件数と差も含める。
業種は prediction.sector33_code(予測した時点の33業種)を使い、33業種がない行は業種比の集計から除く。
手順6〜8は、1つのトランザクションで行う。
"""

from dataclasses import dataclass
from datetime import date

import psycopg
from psycopg.rows import DictRow

import const
from common import db
from common.business_day import months_before
from common.logger import BatchLogger, get_logger
from common.run_history import ProcessOutcome, run_with_history

LABEL: str = const.PROCESS_LABELS[const.PROCESS_VERIFY]


@dataclass(frozen=True)
class VerifyResult:
    """検証の結果。"""

    verified_count: int
    unverifiable_count: int
    daily_summary_rows: int
    period_summary_rows: int
    calibration_rows: int
    calibration_warnings: list[dict[str, object]]


# 手順2〜6(1): 答え合わせをする予測を選び、実株価・騰落率・的中フラグ・検証状態を更新する
_UPDATE_SQL: str = """
WITH selected AS (
    SELECT p.code, p.pred_date, p.horizon, p.target_date, p.up_prob, p.base_price
    FROM prediction p
    JOIN fetched_date f
      ON f.target_date = p.target_date AND f.data_type = %(price)s AND f.status = %(fetched)s
    WHERE (p.verify_status = %(pending)s AND p.target_date <= %(d)s) OR p.target_date = %(d)s
),
calc AS (
    SELECT s.*,
           dp.close AS target_close,
           -- 調整係数が空・0以下の日は 1 として扱う(common/price_adjust.py と同じ。ln(0) のエラーを防ぐ)
           (SELECT exp(sum(CASE WHEN a.adj_factor > 0 THEN ln(a.adj_factor) END))
              FROM daily_price a
             WHERE a.code = s.code AND a.trade_date > s.pred_date AND a.trade_date <= s.target_date) AS factor
    FROM selected s
    LEFT JOIN daily_price dp ON dp.code = s.code AND dp.trade_date = s.target_date
),
result AS (
    SELECT code, pred_date, horizon, up_prob, base_price,
           CASE WHEN target_close IS NULL THEN NULL
                ELSE round(target_close / COALESCE(factor, 1), 4) END AS actual
    FROM calc
)
UPDATE prediction p
SET actual_price = r.actual,
    change_rate = CASE WHEN r.actual IS NULL THEN NULL ELSE round((r.actual - r.base_price) / r.base_price, 6) END,
    is_hit = CASE WHEN r.actual IS NULL THEN NULL
                  WHEN r.up_prob >= %(threshold)s THEN r.actual > r.base_price
                  ELSE r.actual <= r.base_price END,
    verify_status = CASE WHEN r.actual IS NULL THEN %(unverifiable)s ELSE %(verified)s END,
    updated_at = now()
FROM result r
WHERE p.code = r.code AND p.pred_date = r.pred_date AND p.horizon = r.horizon
RETURNING p.pred_date, p.horizon, p.verify_status
"""

# 手順6(2): 予測日ごとの集計(対象区分ごと)
# 市場の中央値は、同じ予測日・予測日数の、検証済みの全ての行(対象区分に関係なく全銘柄)の騰落率の中央値。
# 業種の中央値は、そのうち同じ33業種の行の騰落率の中央値。
# どちらも、中央値と同じ騰落率は、上回っていない扱いにする。
_DAILY_SUMMARY_SQL: str = """
WITH v AS (
    SELECT change_rate, sector33_code
    FROM prediction
    WHERE pred_date = %(pred_date)s AND horizon = %(horizon)s AND verify_status = %(verified)s
),
med AS (
    SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY change_rate)::numeric AS market_median FROM v
),
sec AS (
    SELECT sector33_code, percentile_cont(0.5) WITHIN GROUP (ORDER BY change_rate)::numeric AS sector_median
    FROM v WHERE sector33_code IS NOT NULL GROUP BY sector33_code
)
INSERT INTO verify_daily_summary (pred_date, horizon, segment, target_date, model_version, verified_count,
    hit_count, up_count, beat_count, sum_change_rate, sum_excess_rate, market_median, sector_verified_count,
    sector_beat_count, sum_sector_excess_rate, sum_price_error_rate, unverifiable_count, updated_at)
SELECT p.pred_date, p.horizon, seg.segment, max(p.target_date), max(p.model_version),
       count(*) FILTER (WHERE p.verify_status = %(verified)s),
       count(*) FILTER (WHERE p.is_hit),
       count(*) FILTER (WHERE p.actual_price > p.base_price),
       count(*) FILTER (WHERE p.verify_status = %(verified)s AND p.change_rate > med.market_median),
       COALESCE(sum(p.change_rate) FILTER (WHERE p.verify_status = %(verified)s), 0),
       COALESCE(sum(p.change_rate - med.market_median) FILTER (WHERE p.verify_status = %(verified)s), 0),
       round(max(med.market_median), 6),
       count(*) FILTER (WHERE p.verify_status = %(verified)s AND s.sector_median IS NOT NULL),
       count(*) FILTER (WHERE p.verify_status = %(verified)s AND p.change_rate > s.sector_median),
       COALESCE(sum(p.change_rate - s.sector_median) FILTER (WHERE p.verify_status = %(verified)s), 0),
       COALESCE(sum(abs(p.pred_price - p.actual_price) / NULLIF(p.actual_price, 0))
                FILTER (WHERE p.verify_status = %(verified)s), 0),
       count(*) FILTER (WHERE p.verify_status = %(unverifiable)s),
       now()
FROM prediction p
CROSS JOIN med
LEFT JOIN sec s ON s.sector33_code = p.sector33_code
CROSS JOIN LATERAL (
    VALUES (%(seg_all)s, true),
           (%(seg_top)s, p.buy_rank IS NOT NULL),
           (%(seg_top_sector)s, p.buy_rank_sector IS NOT NULL),
           (%(seg_high)s, p.up_prob >= %(high)s)
) AS seg(segment, matched)
WHERE seg.matched AND p.pred_date = %(pred_date)s AND p.horizon = %(horizon)s
GROUP BY p.pred_date, p.horizon, seg.segment
"""

# 手順7: 期間の集計
_PERIOD_SUMMARY_SQL: str = """
INSERT INTO verify_period_summary (summary_date, period_type, horizon, segment, verified_count, hit_count,
    hit_rate, up_rate, beat_rate, avg_change_rate, avg_excess_rate, sector_beat_rate, avg_sector_excess_rate,
    avg_price_error_rate, created_at)
SELECT %(d)s, %(period)s, horizon, segment, sum(verified_count), sum(hit_count),
       sum(hit_count)::numeric / NULLIF(sum(verified_count), 0),
       sum(up_count)::numeric / NULLIF(sum(verified_count), 0),
       sum(beat_count)::numeric / NULLIF(sum(verified_count), 0),
       sum(sum_change_rate) / NULLIF(sum(verified_count), 0),
       sum(sum_excess_rate) / NULLIF(sum(verified_count), 0),
       sum(sector_beat_count)::numeric / NULLIF(sum(sector_verified_count), 0),
       sum(sum_sector_excess_rate) / NULLIF(sum(sector_verified_count), 0),
       sum(sum_price_error_rate) / NULLIF(sum(verified_count), 0),
       now()
FROM verify_daily_summary
WHERE target_date <= %(d)s AND (%(start)s::date IS NULL OR target_date > %(start)s::date)
GROUP BY horizon, segment
"""

# 手順8(2): 上昇確率の確かさ(確率の帯ごと)
_CALIBRATION_SQL: str = """
INSERT INTO verify_calibration_summary (summary_date, period_type, horizon, prob_band, verified_count, up_count,
    avg_up_prob, up_rate, gap, created_at)
SELECT %(d)s, %(period)s, horizon, band, count(*),
       count(*) FILTER (WHERE actual_price > base_price),
       avg(up_prob),
       (count(*) FILTER (WHERE actual_price > base_price))::numeric / count(*),
       (count(*) FILTER (WHERE actual_price > base_price))::numeric / count(*) - avg(up_prob),
       now()
FROM (
    SELECT horizon, up_prob, actual_price, base_price, LEAST(floor(up_prob * 10) * 10, 90)::smallint AS band
    FROM prediction
    WHERE verify_status = %(verified)s AND target_date <= %(d)s
      AND (%(start)s::date IS NULL OR target_date > %(start)s::date)
) t
GROUP BY horizon, band
"""

# 手順8(3): ずれの判定
_CALIBRATION_WARN_SQL: str = """
SELECT horizon, prob_band, verified_count, avg_up_prob, up_rate, gap
FROM verify_calibration_summary
WHERE summary_date = %s AND period_type = %s AND verified_count >= %s AND abs(gap) > %s
ORDER BY horizon, prob_band
"""


def period_start(target_date: date, period: str) -> date | None:
    """期間の最初の日(この日より後が期間に入る)。ALL は None。"""
    if period == const.PERIOD_1M:
        return months_before(target_date, 1)
    if period == const.PERIOD_3M:
        return months_before(target_date, 3)
    return None


def find_calibration_warnings(conn: psycopg.Connection[DictRow], summary_date: date) -> list[dict[str, object]]:
    """直近3か月で、件数が CALIBRATION_MIN_COUNT 以上で、ずれが CALIBRATION_WARN_GAP より大きい帯を返す。

    通知も、同じ条件で使う。
    """
    rows = conn.execute(
        _CALIBRATION_WARN_SQL, (summary_date, const.PERIOD_3M, const.CALIBRATION_MIN_COUNT, const.CALIBRATION_WARN_GAP)
    ).fetchall()
    return [dict(r) for r in rows]


def _params(target_date: date) -> dict[str, object]:
    """SQL に渡す共通のパラメータ。"""
    return {
        "d": target_date,
        "price": const.DATA_TYPE_PRICE,
        "fetched": const.FETCH_STATUS_FETCHED,
        "pending": const.VERIFY_STATUS_PENDING,
        "verified": const.VERIFY_STATUS_VERIFIED,
        "unverifiable": const.VERIFY_STATUS_UNVERIFIABLE,
        "threshold": const.UP_PROB_THRESHOLD,
        "high": const.HIGH_PROB_THRESHOLD,
        "seg_all": const.SEGMENT_ALL,
        "seg_top": const.SEGMENT_TOP,
        "seg_top_sector": const.SEGMENT_TOP_SECTOR,
        "seg_high": const.SEGMENT_HIGH_PROB,
    }


def rebuild_daily_summary(cur: psycopg.Cursor[DictRow], pred_date: date, horizon: int) -> int:
    """予測日・予測日数の組の、予測日ごとの集計を作り直す。作った行数を返す。"""
    cur.execute("DELETE FROM verify_daily_summary WHERE pred_date = %s AND horizon = %s", (pred_date, horizon))
    cur.execute(_DAILY_SUMMARY_SQL, {**_params(pred_date), "pred_date": pred_date, "horizon": horizon})
    return cur.rowcount


def rebuild_period_summary(cur: psycopg.Cursor[DictRow], summary_date: date) -> int:
    """集計の基準日の、期間の集計(1M・3M・ALL)を作り直す。作った行数を返す。"""
    cur.execute("DELETE FROM verify_period_summary WHERE summary_date = %s", (summary_date,))
    rows = 0
    for period in const.VERIFY_SUMMARY_PERIODS:
        cur.execute(
            _PERIOD_SUMMARY_SQL,
            {**_params(summary_date), "period": period, "start": period_start(summary_date, period)},
        )
        rows += cur.rowcount
    return rows


def verify(conn: psycopg.Connection[DictRow], target_date: date, logger: BatchLogger) -> VerifyResult:
    """検証の手順2〜9を行う。"""
    common_params = _params(target_date)
    with conn.transaction(), conn.cursor() as cur:
        # 手順2〜6(1)
        cur.execute(_UPDATE_SQL, common_params)
        updated = cur.fetchall()
        verified = sum(1 for r in updated if r["verify_status"] == const.VERIFY_STATUS_VERIFIED)
        unverifiable = len(updated) - verified
        pairs = sorted({(r["pred_date"], int(r["horizon"])) for r in updated})
        if updated:
            logger.info("答え合わせをする予測: %s件", f"{len(updated):,}")
        logger.info("検証済み %s件 / 検証不能 %s件", f"{verified:,}", f"{unverifiable:,}")

        # 手順6(2): 予測日ごとの集計を、更新した組ごとに作り直す
        daily_rows = 0
        for pred_date, horizon in pairs:
            daily_rows += rebuild_daily_summary(cur, pred_date, horizon)
        logger.info("予測日ごとの集計を作り直しました(%d行)", daily_rows)

        # 手順7: 期間の集計
        period_rows = rebuild_period_summary(cur, target_date)
        logger.info("期間の集計を作り直しました(%d行)", period_rows)

        # 手順8: 上昇確率の確かさ
        cur.execute("DELETE FROM verify_calibration_summary WHERE summary_date = %s", (target_date,))
        calibration_rows = 0
        for period in (const.PERIOD_3M, const.PERIOD_ALL):
            cur.execute(
                _CALIBRATION_SQL, {**common_params, "period": period, "start": period_start(target_date, period)}
            )
            calibration_rows += cur.rowcount
        logger.info("上昇確率の確かさの集計を作り直しました(%d行)", calibration_rows)

    warnings = find_calibration_warnings(conn, target_date)
    for w in warnings:
        band = int(str(w["prob_band"]))
        logger.warning(
            "上昇確率がずれています(直近3か月、%s日後、%d〜%d%%の帯: "
            "確率の平均 %.1f%% / 実際に上がった割合 %.1f%% / %s件)",
            w["horizon"], band, band + 10, float(str(w["avg_up_prob"])) * 100, float(str(w["up_rate"])) * 100,
            f"{int(str(w['verified_count'])):,}",
        )  # fmt: skip
    return VerifyResult(verified, unverifiable, daily_rows, period_rows, calibration_rows, warnings)


def run_verify(target_date: date, batch_type: str = const.BATCH_TYPE_SINGLE) -> VerifyResult:
    """検証を実行する。実行履歴とログの開始・終了も行う。

    Raises:
        BatchError: 失敗した場合
    """
    logger = get_logger(LABEL, target_date)

    def body() -> ProcessOutcome[VerifyResult]:
        logger.info("処理を開始します")
        with db.connect() as conn:
            result = verify(conn, target_date, logger)
        logger.info("処理を終了します")
        count = result.verified_count + result.unverifiable_count
        status = const.RUN_STATUS_SUCCESS if count > 0 else const.RUN_STATUS_EMPTY
        return ProcessOutcome(result=result, status=status, record_count=count)

    return run_with_history(batch_type, const.PROCESS_VERIFY, target_date, logger, body)
