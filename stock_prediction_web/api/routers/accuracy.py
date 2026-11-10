"""的中率の API。

GET /api/accuracy: 期間(直近1か月・3か月・全期間)の集計と、保有銘柄だけの集計、上昇確率の確かさ
GET /api/accuracy/daily: 予測日ごとの推移(買い候補の市場の中央値との差、全銘柄の的中率)
GET /api/accuracy/compare: 精度の比較。全銘柄・買い候補・買い候補(業種分散)を並べる
GET /api/accuracy/rank: 買い候補 順位別 的中率(順位の帯ごとの、上昇の的中率)

期間は対象日(答え合わせの日)で区切る(バッチの core/verify_core.py の期間の集計と同じ)。
保有銘柄の集計は、集計テーブルに区分がないので、prediction と holding からその場で計算する。
"""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

import const
from api import web_const
from api.common import check_horizon, check_range, to_float
from api.deps import Conn
from api.errors import ApiError
from api.logger import log_api
from common.business_day import months_before
from core.verify_core import find_calibration_warnings, period_start

router = APIRouter()


class SegmentValues(BaseModel):
    """1つの区分・1つの期間の集計。値がなければ None。業種比(sector_*)は保有銘柄では None。"""

    verified_count: int
    hit_rate: float | None
    up_rate: float | None
    beat_rate: float | None
    avg_excess_rate: float | None
    avg_price_error_rate: float | None
    sector_beat_rate: float | None = None
    avg_sector_excess_rate: float | None = None


class PeriodValues(BaseModel):
    """1つの期間(1M・3M・ALL)の、区分ごとの集計。holding は保有銘柄だけ(保有していた銘柄がない期間は None)。
    top_sector は買い候補(業種分散)。"""

    period: str
    all: SegmentValues | None
    top: SegmentValues | None
    top_sector: SegmentValues | None
    high_prob: SegmentValues | None
    holding: SegmentValues | None


class CalibrationBand(BaseModel):
    """上昇確率の確かさの、確率の帯(例: 60 は 60〜70%)ごとの値。warn は注意の条件に当てはまるか。"""

    prob_band: int
    verified_count: int
    avg_up_prob: float | None
    up_rate: float | None
    gap: float | None
    warn: bool


class CalibrationWarning(BaseModel):
    """上昇確率のずれの注意(メールと同じ条件。直近3か月)。"""

    horizon: int
    prob_band: int
    verified_count: int
    avg_up_prob: float | None
    up_rate: float | None
    gap: float | None


class AccuracyResponse(BaseModel):
    """/api/accuracy の返り値。集計がまだなければ summary_date は None。"""

    summary_date: date | None
    summary_dates: list[date]
    horizon: int
    periods: list[PeriodValues]
    calibration: dict[str, list[CalibrationBand]]
    warnings: list[CalibrationWarning]


def _segment(row: dict[str, Any] | None) -> SegmentValues | None:
    if row is None:
        return None
    return SegmentValues(
        verified_count=int(row["verified_count"]),
        hit_rate=to_float(row["hit_rate"]),
        up_rate=to_float(row.get("up_rate")),
        beat_rate=to_float(row.get("beat_rate")),
        avg_excess_rate=to_float(row.get("avg_excess_rate")),
        avg_price_error_rate=to_float(row.get("avg_price_error_rate")),
        sector_beat_rate=to_float(row.get("sector_beat_rate")),
        avg_sector_excess_rate=to_float(row.get("avg_sector_excess_rate")),
    )


def holding_values(conn: Conn, summary_date: date, period: str, horizon: int) -> SegmentValues | None:
    """保有銘柄だけの集計。

    予測日にその銘柄を保有していた(購入日 ≦ 予測日 < 売却日。売却していなければ購入日 ≦ 予測日)、
    検証済みの予測を数える。
    同じ銘柄を複数の行(買い増し)で持っていても、その銘柄の予測の行は1回だけ数える。
    市場の中央値は verify_daily_summary の全銘柄(ALL)の行の market_median。保有していた銘柄がなければ None。
    """
    start = period_start(summary_date, period)
    row = conn.execute(
        """
        WITH held AS (
            SELECT DISTINCT p.code, p.pred_date, p.horizon, p.is_hit, p.change_rate
            FROM prediction p
            JOIN holding h ON h.code = p.code AND h.buy_date <= p.pred_date
                          AND (h.sell_date IS NULL OR p.pred_date < h.sell_date)
            WHERE p.horizon = %(horizon)s AND p.verify_status = %(verified)s AND p.target_date <= %(d)s
              AND (%(start)s::date IS NULL OR p.target_date > %(start)s::date)
        )
        SELECT count(*) AS verified_count,
               avg(CASE WHEN held.is_hit THEN 1.0 ELSE 0.0 END) AS hit_rate,
               avg(CASE WHEN held.change_rate > 0 THEN 1.0 ELSE 0.0 END) AS up_rate,
               avg(CASE WHEN held.change_rate > m.market_median THEN 1.0 ELSE 0.0 END)
                   FILTER (WHERE m.market_median IS NOT NULL) AS beat_rate,
               avg(held.change_rate - m.market_median) FILTER (WHERE m.market_median IS NOT NULL) AS avg_excess_rate
        FROM held
        LEFT JOIN verify_daily_summary m
          ON m.pred_date = held.pred_date AND m.horizon = held.horizon AND m.segment = %(all)s
        """,
        {
            "horizon": horizon,
            "verified": const.VERIFY_STATUS_VERIFIED,
            "d": summary_date,
            "start": start,
            "all": const.SEGMENT_ALL,
        },
    ).fetchone()
    if row is None or int(row["verified_count"]) == 0:
        return None
    return _segment(dict(row))


def resolve_summary_date(conn: Conn, target: date | None) -> tuple[date | None, list[date]]:
    """集計の基準日(省略すると最新。指定した日に集計がなければ、それより前で一番新しい日)と、集計がある日の一覧。"""
    dates = [r["summary_date"] for r in conn.execute(
        "SELECT DISTINCT summary_date FROM verify_period_summary ORDER BY summary_date"
    ).fetchall()]  # fmt: skip
    usable = [d for d in dates if target is None or d <= target]
    return (usable[-1] if usable else None), dates


@router.get("/api/accuracy", response_model=AccuracyResponse)
@log_api
def get_accuracy(
    conn: Conn,
    target: Annotated[date | None, Query(alias="date")] = None,
    horizon: int = const.MAIN_HORIZON,
) -> AccuracyResponse:
    """集計の基準日(省略すると最新。指定した日に集計がなければ、それより前で一番新しい日)の的中率。"""
    check_horizon(horizon)
    summary_date, dates = resolve_summary_date(conn, target)
    if summary_date is None:
        return AccuracyResponse(
            summary_date=None, summary_dates=dates, horizon=horizon, periods=[], calibration={}, warnings=[]
        )
    rows = conn.execute(
        """
        SELECT period_type, segment, verified_count, hit_rate, up_rate, beat_rate, avg_excess_rate,
               avg_price_error_rate, sector_beat_rate, avg_sector_excess_rate
        FROM verify_period_summary WHERE summary_date = %s AND horizon = %s
        """,
        (summary_date, horizon),
    ).fetchall()
    index = {(r["period_type"], r["segment"]): dict(r) for r in rows}
    periods = [
        PeriodValues(
            period=p,
            all=_segment(index.get((p, const.SEGMENT_ALL))),
            top=_segment(index.get((p, const.SEGMENT_TOP))),
            top_sector=_segment(index.get((p, const.SEGMENT_TOP_SECTOR))),
            high_prob=_segment(index.get((p, const.SEGMENT_HIGH_PROB))),
            holding=holding_values(conn, summary_date, p, horizon),
        )
        for p in const.VERIFY_SUMMARY_PERIODS
    ]
    calibration: dict[str, list[CalibrationBand]] = {}
    for p in (const.PERIOD_3M, const.PERIOD_ALL):
        bands = conn.execute(
            """
            SELECT prob_band, verified_count, avg_up_prob, up_rate, gap FROM verify_calibration_summary
            WHERE summary_date = %s AND period_type = %s AND horizon = %s ORDER BY prob_band
            """,
            (summary_date, p, horizon),
        ).fetchall()
        calibration[p] = [
            CalibrationBand(
                prob_band=int(b["prob_band"]),
                verified_count=int(b["verified_count"]),
                avg_up_prob=to_float(b["avg_up_prob"]),
                up_rate=to_float(b["up_rate"]),
                gap=to_float(b["gap"]),
                # メールの注意と同じ条件(件数 CALIBRATION_MIN_COUNT 以上、ずれが CALIBRATION_WARN_GAP より大きい)
                warn=int(b["verified_count"]) >= const.CALIBRATION_MIN_COUNT
                and b["gap"] is not None
                and abs(float(b["gap"])) > const.CALIBRATION_WARN_GAP,
            )
            for b in bands
        ]
    warnings = [
        CalibrationWarning(
            horizon=int(str(w["horizon"])),
            prob_band=int(str(w["prob_band"])),
            verified_count=int(str(w["verified_count"])),
            avg_up_prob=to_float(w["avg_up_prob"]),  # type: ignore[arg-type]
            up_rate=to_float(w["up_rate"]),  # type: ignore[arg-type]
            gap=to_float(w["gap"]),  # type: ignore[arg-type]
        )
        for w in find_calibration_warnings(conn, summary_date)
    ]
    return AccuracyResponse(
        summary_date=summary_date,
        summary_dates=dates,
        horizon=horizon,
        periods=periods,
        calibration=calibration,
        warnings=warnings,
    )


class DailyPoint(BaseModel):
    """予測日ごとの値。top_excess は買い候補の市場の中央値との差の平均、all_hit_rate は全銘柄の的中率。"""

    pred_date: date
    top_excess: float | None
    all_hit_rate: float | None


class DailyResponse(BaseModel):
    """/api/accuracy/daily の返り値。"""

    date_from: date
    date_to: date
    horizon: int
    points: list[DailyPoint]


@router.get("/api/accuracy/daily", response_model=DailyResponse)
@log_api
def get_accuracy_daily(
    conn: Conn,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    horizon: int = const.MAIN_HORIZON,
) -> DailyResponse:
    """予測日ごとの推移(グラフ用)。

    省略すると、検証済みの最新の予測日までの web_const.ACCURACY_DAILY_DEFAULT_MONTHS か月。
    """
    check_horizon(horizon)
    check_range(date_from, date_to)
    if date_to is None:
        row = conn.execute(
            "SELECT max(pred_date) AS d FROM verify_daily_summary WHERE horizon = %s AND verified_count > 0",
            (horizon,),
        ).fetchone()
        latest = conn.execute("SELECT max(pred_date) AS d FROM prediction").fetchone()
        date_to = (row["d"] if row else None) or (latest["d"] if latest else None) or date.today()
    if date_from is None:
        date_from = months_before(date_to, web_const.ACCURACY_DAILY_DEFAULT_MONTHS)
    check_range(date_from, date_to)
    rows = conn.execute(
        """
        SELECT a.pred_date,
               t.sum_excess_rate / NULLIF(t.verified_count, 0) AS top_excess,
               a.hit_count::numeric / NULLIF(a.verified_count, 0) AS all_hit_rate
        FROM verify_daily_summary a
        LEFT JOIN verify_daily_summary t ON t.pred_date = a.pred_date AND t.horizon = a.horizon AND t.segment = %s
        WHERE a.segment = %s AND a.horizon = %s AND a.pred_date BETWEEN %s AND %s AND a.verified_count > 0
        ORDER BY a.pred_date
        """,
        (const.SEGMENT_TOP, const.SEGMENT_ALL, horizon, date_from, date_to),
    ).fetchall()
    return DailyResponse(
        date_from=date_from,
        date_to=date_to,
        horizon=horizon,
        points=[
            DailyPoint(
                pred_date=r["pred_date"],
                top_excess=to_float(r["top_excess"]),
                all_hit_rate=to_float(r["all_hit_rate"]),
            )
            for r in rows
        ],
    )


# =====================================================================
# 精度の比較
# =====================================================================

# 比べる対象区分(この順)
COMPARE_SEGMENTS: list[str] = [const.SEGMENT_ALL, const.SEGMENT_TOP, const.SEGMENT_TOP_SECTOR]
# 業種の内訳を出す対象区分と、その順位のカラム(prediction)
_SECTOR_RANK_COLUMNS: dict[str, str] = {const.SEGMENT_TOP: "buy_rank", const.SEGMENT_TOP_SECTOR: "buy_rank_sector"}
# 33業種がない銘柄の業種の名前
SECTOR_UNKNOWN: str = "(不明)"


class CompareRow(BaseModel):
    """①・③: 1つの予測日数・1つの対象区分の集計と、件数・期間。値がなければ None。

    first_pred_date・last_pred_date は検証できた予測日の最初と最後、code_count は検証できた銘柄の数。
    """

    horizon: int
    segment: str
    verified_count: int
    up_rate: float | None
    avg_change_rate: float | None
    beat_rate: float | None
    avg_excess_rate: float | None
    sector_beat_rate: float | None
    avg_sector_excess_rate: float | None
    first_pred_date: date | None
    last_pred_date: date | None
    code_count: int


class SectorShare(BaseModel):
    """④: 1つの業種の件数と割合。"""

    sector: str
    count: int
    share: float


class SectorBreakdown(BaseModel):
    """④: 1つの買い候補の業種の内訳(上位 web_const.COMPARE_SECTOR_TOP_N 業種と、その他)。

    数えるのは、検証できた予測日(主となる日数の最初〜最後)の、主となる日数の行(予測日 × 銘柄)。
    """

    segment: str
    total: int
    sector_count: int
    top: list[SectorShare]
    other_count: int
    other_share: float | None


class MonthlyRow(BaseModel):
    """⑤: 予測月ごと・対象区分ごとの、主となる日数の集計。"""

    month: str
    segment: str
    verified_count: int
    up_rate: float | None
    avg_excess_rate: float | None
    avg_sector_excess_rate: float | None


class ModelRow(BaseModel):
    """⑥: 使ったモデル。予測日の最初と最後は、期間の中で検証できた予測のもの。学習期間は、そのバージョンの
    モデル(予測日数・種類)の学習データの最も早い開始日〜最も遅い終了日。"""

    model_version: str
    first_pred_date: date
    last_pred_date: date
    base_date: date | None
    train_start_date: date | None
    train_end_date: date | None


class CompareResponse(BaseModel):
    """/api/accuracy/compare の返り値。集計がまだなければ summary_date は None で、ほかは空。"""

    summary_date: date | None
    summary_dates: list[date]
    period: str
    period_start: date | None
    main_horizon: int
    rows: list[CompareRow]
    sectors: list[SectorBreakdown]
    monthly: list[MonthlyRow]
    models: list[ModelRow]


def _period_filter(column: str) -> str:
    """期間の条件(期間は対象日で区切る。バッチの期間の集計と同じ)。%(d)s と %(start)s を使う。"""
    return f"{column} <= %(d)s AND (%(start)s::date IS NULL OR {column} > %(start)s::date)"


def compare_rows(conn: Conn, params: dict[str, Any]) -> list[CompareRow]:
    """①・③: 予測日数・対象区分ごとの集計と、件数・期間。"""
    summary = {
        (int(r["horizon"]), str(r["segment"])): r
        for r in conn.execute(
            """
            SELECT horizon, segment, verified_count, up_rate, avg_change_rate, beat_rate, avg_excess_rate,
                   sector_beat_rate, avg_sector_excess_rate
            FROM verify_period_summary
            WHERE summary_date = %(d)s AND period_type = %(period)s AND segment = ANY(%(segments)s)
            """,
            params,
        ).fetchall()
    }
    spans = {
        (int(r["horizon"]), str(r["segment"])): r
        for r in conn.execute(
            f"""
            SELECT horizon, segment, min(pred_date) AS first_pred_date, max(pred_date) AS last_pred_date
            FROM verify_daily_summary
            WHERE segment = ANY(%(segments)s) AND verified_count > 0 AND {_period_filter("target_date")}
            GROUP BY horizon, segment
            """,
            params,
        ).fetchall()
    }
    # 銘柄の数は prediction から数える(期間の行を1回だけ読む)
    codes = {
        int(r["horizon"]): r
        for r in conn.execute(
            f"""
            SELECT horizon,
                   count(DISTINCT code) AS n_all,
                   count(DISTINCT code) FILTER (WHERE buy_rank IS NOT NULL) AS n_top,
                   count(DISTINCT code) FILTER (WHERE buy_rank_sector IS NOT NULL) AS n_top_sector
            FROM prediction
            WHERE verify_status = %(verified)s AND {_period_filter("target_date")}
            GROUP BY horizon
            """,
            params,
        ).fetchall()
    }
    code_columns = {const.SEGMENT_ALL: "n_all", const.SEGMENT_TOP: "n_top", const.SEGMENT_TOP_SECTOR: "n_top_sector"}
    rows: list[CompareRow] = []
    for h in const.PREDICT_HORIZONS:
        for seg in COMPARE_SEGMENTS:
            s = summary.get((h, seg))
            span = spans.get((h, seg))
            c = codes.get(h)
            rows.append(
                CompareRow(
                    horizon=h,
                    segment=seg,
                    verified_count=0 if s is None else int(s["verified_count"]),
                    up_rate=None if s is None else to_float(s["up_rate"]),
                    avg_change_rate=None if s is None else to_float(s["avg_change_rate"]),
                    beat_rate=None if s is None else to_float(s["beat_rate"]),
                    avg_excess_rate=None if s is None else to_float(s["avg_excess_rate"]),
                    sector_beat_rate=None if s is None else to_float(s["sector_beat_rate"]),
                    avg_sector_excess_rate=None if s is None else to_float(s["avg_sector_excess_rate"]),
                    first_pred_date=None if span is None else span["first_pred_date"],
                    last_pred_date=None if span is None else span["last_pred_date"],
                    code_count=0 if c is None else int(c[code_columns[seg]]),
                )
            )
    return rows


def sector_breakdowns(conn: Conn, rows: list[CompareRow]) -> list[SectorBreakdown]:
    """④: 2つの買い候補の業種の内訳。業種は予測した時点の33業種(prediction.sector33_code)。"""
    names = {
        str(r["sector33_code"]): str(r["sector33_name"] or r["sector33_code"])
        for r in conn.execute(
            "SELECT DISTINCT sector33_code, sector33_name FROM stock_master WHERE sector33_code IS NOT NULL"
        ).fetchall()
    }
    spans = {r.segment: r for r in rows if r.horizon == const.MAIN_HORIZON}
    result: list[SectorBreakdown] = []
    for seg, column in _SECTOR_RANK_COLUMNS.items():
        span = spans.get(seg)
        counts: list[tuple[str, int]] = []
        if span is not None and span.first_pred_date is not None:
            found = conn.execute(
                f"""
                SELECT sector33_code, count(*) AS n FROM prediction
                WHERE horizon = %s AND {column} IS NOT NULL AND pred_date BETWEEN %s AND %s
                GROUP BY sector33_code
                """,
                (const.MAIN_HORIZON, span.first_pred_date, span.last_pred_date),
            ).fetchall()
            merged: dict[str, int] = {}
            for r in found:
                name = (
                    SECTOR_UNKNOWN
                    if r["sector33_code"] is None
                    else names.get(str(r["sector33_code"]), str(r["sector33_code"]))
                )
                merged[name] = merged.get(name, 0) + int(r["n"])
            # 件数の多い順。同じ件数は業種の名前の順
            counts = sorted(merged.items(), key=lambda x: (-x[1], x[0]))
        total = sum(n for _, n in counts)
        top_n = counts[: web_const.COMPARE_SECTOR_TOP_N]
        other = total - sum(n for _, n in top_n)
        result.append(
            SectorBreakdown(
                segment=seg,
                total=total,
                sector_count=len(counts),
                top=[SectorShare(sector=s, count=n, share=n / total) for s, n in top_n],
                other_count=other,
                other_share=other / total if total else None,
            )
        )
    return result


def monthly_rows(conn: Conn, params: dict[str, Any]) -> list[MonthlyRow]:
    """⑤: 予測月ごとの、主となる日数の上がった割合・市場の中央値との差・業種の中央値との差。"""
    found = conn.execute(
        f"""
        SELECT to_char(date_trunc('month', pred_date), 'YYYY-MM') AS month, segment,
               sum(verified_count) AS verified_count,
               sum(up_count)::numeric / NULLIF(sum(verified_count), 0) AS up_rate,
               sum(sum_excess_rate) / NULLIF(sum(verified_count), 0) AS avg_excess_rate,
               sum(sum_sector_excess_rate) / NULLIF(sum(sector_verified_count), 0) AS avg_sector_excess_rate
        FROM verify_daily_summary
        WHERE horizon = %(main)s AND segment = ANY(%(segments)s) AND verified_count > 0
          AND {_period_filter("target_date")}
        GROUP BY 1, 2
        """,
        params,
    ).fetchall()
    order = {seg: i for i, seg in enumerate(COMPARE_SEGMENTS)}
    return [
        MonthlyRow(
            month=str(r["month"]),
            segment=str(r["segment"]),
            verified_count=int(r["verified_count"]),
            up_rate=to_float(r["up_rate"]),
            avg_excess_rate=to_float(r["avg_excess_rate"]),
            avg_sector_excess_rate=to_float(r["avg_sector_excess_rate"]),
        )
        for r in sorted(found, key=lambda r: (str(r["month"]), order[str(r["segment"])]))
    ]


def model_rows(conn: Conn, params: dict[str, Any]) -> list[ModelRow]:
    """⑥: 期間の中で検証できた予測に使ったモデル(古い順)。"""
    found = conn.execute(
        f"""
        WITH used AS (
            SELECT model_version, min(pred_date) AS first_pred_date, max(pred_date) AS last_pred_date
            FROM verify_daily_summary
            WHERE segment = %(all)s AND verified_count > 0 AND {_period_filter("target_date")}
            GROUP BY model_version
        )
        SELECT u.model_version, u.first_pred_date, u.last_pred_date,
               max(m.base_date) AS base_date, min(m.train_start_date) AS train_start_date,
               max(m.train_end_date) AS train_end_date
        FROM used u
        LEFT JOIN model m ON m.model_version = u.model_version
        GROUP BY u.model_version, u.first_pred_date, u.last_pred_date
        ORDER BY u.first_pred_date, u.model_version
        """,
        params,
    ).fetchall()
    return [
        ModelRow(
            model_version=str(r["model_version"]),
            first_pred_date=r["first_pred_date"],
            last_pred_date=r["last_pred_date"],
            base_date=r["base_date"],
            train_start_date=r["train_start_date"],
            train_end_date=r["train_end_date"],
        )
        for r in found
    ]


@router.get("/api/accuracy/compare", response_model=CompareResponse)
@log_api
def get_accuracy_compare(
    conn: Conn,
    target: Annotated[date | None, Query(alias="date")] = None,
    period: str = const.PERIOD_ALL,
) -> CompareResponse:
    """精度の比較。全銘柄・買い候補・買い候補(業種分散)を、全ての予測日数で並べる。

    集計の基準日は /api/accuracy と同じ決め方。期間(1M・3M・ALL。省略すると ALL)は対象日で区切る。
    """
    if period not in const.VERIFY_SUMMARY_PERIODS:
        raise ApiError(400, f"期間は {'・'.join(const.VERIFY_SUMMARY_PERIODS)} のどれかを指定してください")
    summary_date, dates = resolve_summary_date(conn, target)
    if summary_date is None:
        return CompareResponse(
            summary_date=None, summary_dates=dates, period=period, period_start=None,
            main_horizon=const.MAIN_HORIZON, rows=[], sectors=[], monthly=[], models=[],
        )  # fmt: skip
    start = period_start(summary_date, period)
    params: dict[str, Any] = {
        "d": summary_date,
        "start": start,
        "period": period,
        "segments": COMPARE_SEGMENTS,
        "verified": const.VERIFY_STATUS_VERIFIED,
        "main": const.MAIN_HORIZON,
        "all": const.SEGMENT_ALL,
    }
    rows = compare_rows(conn, params)
    return CompareResponse(
        summary_date=summary_date,
        summary_dates=dates,
        period=period,
        period_start=start,
        main_horizon=const.MAIN_HORIZON,
        rows=rows,
        sectors=sector_breakdowns(conn, rows),
        monthly=monthly_rows(conn, params),
        models=model_rows(conn, params),
    )


# =====================================================================
# 買い候補 順位別 的中率
# =====================================================================


class RankBand(BaseModel):
    """順位の帯(rank_from〜rank_to 位)。"""

    rank_from: int
    rank_to: int


class RankCount(BaseModel):
    """1つの帯の、検証済みの件数と、上昇の的中の件数(騰落率 > 0)。"""

    verified_count: int
    hit_count: int


class RankRow(BaseModel):
    """1つの予測日数の、帯ごとの件数(counts は bands と同じ順)。"""

    horizon: int
    counts: list[RankCount]


class RankPeriod(BaseModel):
    """1つの期間の表。period_start はこの日より後(全期間は None)。"""

    period: str
    period_start: date | None
    rows: list[RankRow]


class RankResponse(BaseModel):
    """/api/accuracy/rank の返り値。集計がまだなければ summary_date は None で、periods は空。

    diff_bands は差を出す2つの帯(前 − 後)。default_period はタブの初期値。
    """

    summary_date: date | None
    summary_dates: list[date]
    bands: list[RankBand]
    diff_bands: list[RankBand]
    default_period: str
    periods: list[RankPeriod]


def rank_period_start(summary_date: date, months: int | None) -> date | None:
    """期間の最初の日(この日より後が期間に入る)。バッチの core/verify_core.py の period_start と同じ決め方。"""
    return None if months is None else months_before(summary_date, months)


def rank_periods(conn: Conn, summary_date: date) -> list[RankPeriod]:
    """期間・予測日数・順位の帯ごとの、検証済みの件数と、上昇の的中(騰落率 > 0。0% は外れ)の件数。

    対象は buy_rank(買い候補の順位)が帯の中、検証済み、騰落率が NULL でない行(業種分散の順位は使わない)。
    prediction を1回だけ読み、順位ごとに全ての期間の件数を数えてから、帯ごとに足す。
    """
    starts = {p: rank_period_start(summary_date, m) for p, m in web_const.ACCURACY_RANK_PERIODS.items()}
    keys = list(starts)
    params: dict[str, Any] = {
        "d": summary_date,
        "verified": const.VERIFY_STATUS_VERIFIED,
        "rank_min": min(b[0] for b in web_const.ACCURACY_RANK_BANDS),
        "rank_max": max(b[1] for b in web_const.ACCURACY_RANK_BANDS),
        # 読む行の範囲(一番長い期間の最初の日。全期間があれば None)
        "from": None if any(v is None for v in starts.values()) else min(v for v in starts.values() if v is not None),
    }
    columns: list[str] = []
    for i, key in enumerate(keys):
        params[f"s{i}"] = starts[key]
        cond = f"%(s{i})s::date IS NULL OR target_date > %(s{i})s::date"
        columns.append(f"count(*) FILTER (WHERE {cond}) AS n{i}")
        columns.append(f"count(*) FILTER (WHERE ({cond}) AND change_rate > 0) AS h{i}")
    found = conn.execute(
        f"""
        SELECT horizon, buy_rank, {", ".join(columns)}
        FROM prediction
        WHERE buy_rank BETWEEN %(rank_min)s AND %(rank_max)s AND verify_status = %(verified)s
          AND change_rate IS NOT NULL AND target_date <= %(d)s
          AND (%(from)s::date IS NULL OR target_date > %(from)s::date)
        GROUP BY horizon, buy_rank
        """,
        params,
    ).fetchall()
    # (予測日数, 順位) → 期間ごとの (件数, 的中)
    by_rank: dict[tuple[int, int], list[tuple[int, int]]] = {
        (int(r["horizon"]), int(r["buy_rank"])): [(int(r[f"n{i}"]), int(r[f"h{i}"])) for i in range(len(keys))]
        for r in found
    }
    result: list[RankPeriod] = []
    for i, key in enumerate(keys):
        rows: list[RankRow] = []
        for h in const.PREDICT_HORIZONS:
            counts: list[RankCount] = []
            for lo, hi in web_const.ACCURACY_RANK_BANDS:
                cells = [by_rank[(h, k)][i] for k in range(lo, hi + 1) if (h, k) in by_rank]
                counts.append(RankCount(verified_count=sum(c[0] for c in cells), hit_count=sum(c[1] for c in cells)))
            rows.append(RankRow(horizon=h, counts=counts))
        result.append(RankPeriod(period=key, period_start=starts[key], rows=rows))
    return result


@router.get("/api/accuracy/rank", response_model=RankResponse)
@log_api
def get_accuracy_rank(conn: Conn, target: Annotated[date | None, Query(alias="date")] = None) -> RankResponse:
    """買い候補 順位別 的中率。集計の基準日は /api/accuracy と同じ決め方。全ての期間を1回で返す。

    集計テーブルには順位の帯の区分がないので、prediction からその場で数える。
    """
    summary_date, dates = resolve_summary_date(conn, target)
    return RankResponse(
        summary_date=summary_date,
        summary_dates=dates,
        bands=[RankBand(rank_from=lo, rank_to=hi) for lo, hi in web_const.ACCURACY_RANK_BANDS],
        diff_bands=[RankBand(rank_from=lo, rank_to=hi) for lo, hi in web_const.ACCURACY_RANK_DIFF_BANDS],
        default_period=web_const.ACCURACY_RANK_DEFAULT_PERIOD,
        periods=[] if summary_date is None else rank_periods(conn, summary_date),
    )
