"""GET /api/candidates: 指定日の買い候補。
GET /api/candidates/range: 予測日の期間の買い候補。
GET /api/candidates/results: 期間の買い候補と実際の株価。

どちらもメールと同じ内容にするため、バッチのコード(core/notify_core.py の load_notify_data、
tools/make_candidates_html.py の load_days)をそのまま使う。
kind で、買い候補(top。初期値)と買い候補(業種分散。sector)を切り替える。
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

import const
from api import web_const
from api.common import (
    HorizonValue,
    Note,
    check_range,
    horizon_values,
    latest_pred_date,
    resolve_pred_date,
    to_float,
    today,
)
from api.deps import Conn
from api.errors import ApiError
from api.logger import log_api
from api.notes import to_notes
from api.routers.status import list_prediction_dates
from common.business_day import business_days_before, months_before
from core.notify_core import candidate_fallback_note, load_notify_data
from tools.make_candidates_html import KINDS, load_days

router = APIRouter()


def check_kind(kind: str) -> None:
    """買い候補の種類(top か sector)を確かめる。正しくなければ 400。"""
    if kind not in KINDS:
        raise ApiError(400, f"kind は {' か '.join(KINDS)} を指定してください")


class CandidateRow(BaseModel):
    """買い候補の1銘柄。base_price は現在値(予測日の終値)。"""

    rank: int
    code: str
    name: str
    market: str
    sector: str
    base_price: float
    horizons: list[HorizonValue]
    notes: list[Note]


class IndexTrendModel(BaseModel):
    """日経平均の向きの1期間分。slope は1営業日あたりの傾きを終値で割った割合(0.0021 = +0.21%/日)。"""

    days: int
    direction: str
    slope: float


class MarketStateModel(BaseModel):
    """相場の状態。above25・ret20 は割合(0.321 = 32.1%)。

    index_trend は、日経平均の向き(10・20・60日。短い順)。出さない日は None。
    """

    state: str
    above25: float
    ret20: float
    index_trend: list[IndexTrendModel] | None


class CandidatesResponse(BaseModel):
    """/api/candidates の返り値。sector は業種(予測した時点の33業種の名前)。

    fallback_note は、上昇確率の条件を置き換えて選んだ日の注記。それ以外の日は None。
    market_state は、予測日の相場の状態。計算できない日は None。
    """

    kind: str
    pred_date: date
    model_version: str
    code_count: int
    fallback_note: str | None
    market_state: MarketStateModel | None
    candidates: list[CandidateRow]


@router.get("/api/candidates", response_model=CandidatesResponse)
@log_api
def get_candidates(
    conn: Conn,
    target: Annotated[date | None, Query(alias="date")] = None,
    kind: str = const.CANDIDATE_KIND_TOP,
) -> CandidatesResponse:
    """指定日(省略すると最新の予測日)の買い候補(kind が sector なら買い候補(業種分散))を、メールと同じ並びで返す。"""
    check_kind(kind)
    return build_candidates(conn, resolve_pred_date(conn, target), kind)


@router.get("/api/candidates/range", response_model=list[CandidatesResponse])
@log_api
def get_candidates_range(
    conn: Conn,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    kind: str = const.CANDIDATE_KIND_TOP,
) -> list[CandidatesResponse]:
    """予測日の期間の買い候補を、予測がある日ごとに、/api/candidates と同じ中身で返す(古い日が先)。

    from・to は必須。期間は web_const.CANDIDATES_RANGE_MAX_MONTHS か月以内。予測がない日は含めず、
    1日もなければ空の配列を返す。
    """
    check_kind(kind)
    if date_from is None or date_to is None:
        raise ApiError(400, "from と to を指定してください")
    check_range(date_from, date_to)
    if date_from < months_before(date_to, web_const.CANDIDATES_RANGE_MAX_MONTHS):
        raise ApiError(400, f"期間は{web_const.CANDIDATES_RANGE_MAX_MONTHS}か月以内にしてください")
    return [build_candidates(conn, d, kind) for d in list_prediction_dates(conn, date_from, date_to)]


def build_candidates(conn: Conn, pred_date: date, kind: str) -> CandidatesResponse:
    """予測日1日分の買い候補(/api/candidates と /api/candidates/range が共通で使う)。"""
    data = load_notify_data(conn, pred_date)
    candidates = data.candidates_sector if kind == const.CANDIDATE_KIND_SECTOR else data.candidates
    return CandidatesResponse(
        kind=kind,
        pred_date=pred_date,
        model_version=data.model_version,
        code_count=data.code_count,
        fallback_note=candidate_fallback_note(candidates),
        market_state=(
            None
            if data.market_state is None
            else MarketStateModel(
                state=data.market_state.state,
                above25=data.market_state.above25,
                ret20=data.market_state.ret20,
                index_trend=(
                    None
                    if data.index_trend is None
                    else [
                        IndexTrendModel(days=t.days, direction=t.direction, slope=t.slope)
                        for t in data.index_trend.items
                    ]
                ),
            )
        ),
        candidates=[
            CandidateRow(
                rank=c.rank,
                code=c.code,
                name=c.name,
                market=c.market,
                sector=c.sector,
                base_price=float(c.base_price),
                horizons=horizon_values(dict(c.by_horizon)),
                notes=to_notes(c.notes),
            )
            for c in candidates
        ],
    )


class ResultCell(BaseModel):
    """1銘柄・1予測日数の値。actual は答え合わせが済んでいなければ None。"""

    horizon: int
    target_date: date
    rel_prob: float
    up_prob: float
    pred_price: float
    actual: float | None


class ResultRow(BaseModel):
    """買い候補の1銘柄と、予測日数ごとの実際の株価。"""

    rank: int
    code: str
    name: str
    market: str
    base_price: float
    cells: list[ResultCell]
    notes: list[Note]


class ResultSummary(BaseModel):
    """予測日数ごとの、市場の中央値と、買い候補のうち中央値を上回った銘柄の数(まとめ行に使う)。"""

    horizon: int
    market_median: float | None
    beat_count: int | None


class ResultDay(BaseModel):
    """予測日1日分。"""

    pred_date: date
    model_version: str
    rows: list[ResultRow]
    summary: list[ResultSummary]


class ResultsResponse(BaseModel):
    """/api/candidates/results の返り値。"""

    kind: str
    date_from: date
    date_to: date
    days: list[ResultDay]


def default_results_end(conn: Conn) -> date:
    """終了日の初期値: 最新の株価の日の RESULTS_DEFAULT_END_OFFSET_DAYS 営業日前(株価がなければ最新の予測日か今日)。"""
    row = conn.execute("SELECT max(trade_date) AS d FROM daily_price").fetchone()
    latest: date | None = None if row is None else row["d"]
    if latest is None:
        return latest_pred_date(conn) or today()
    return business_days_before(latest, web_const.RESULTS_DEFAULT_END_OFFSET_DAYS, conn)


@router.get("/api/candidates/results", response_model=ResultsResponse)
@log_api
def get_candidate_results(
    conn: Conn,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    kind: str = const.CANDIDATE_KIND_TOP,
) -> ResultsResponse:
    """期間の予測日ごとの買い候補(kind が sector なら買い候補(業種分散))と実際の株価
    (画面フォーマット_買い候補と実際の株価.md の表の元)。

    省略した場合は、終了日 = 最新の株価の日の web_const.RESULTS_DEFAULT_END_OFFSET_DAYS 営業日前、
    開始日 = 終了日の web_const.RESULTS_DEFAULT_SPAN_DAYS 営業日前(営業日は株価がある日で数える)。
    期間は web_const.RESULTS_MAX_MONTHS か月以内。
    """
    check_kind(kind)
    end = date_to or default_results_end(conn)
    start = date_from or business_days_before(end, web_const.RESULTS_DEFAULT_SPAN_DAYS, conn)
    check_range(start, end)
    if start < months_before(end, web_const.RESULTS_MAX_MONTHS):
        raise ApiError(400, f"期間は{web_const.RESULTS_MAX_MONTHS}か月以内にしてください")
    days = load_days(conn, start, end, kind)
    return ResultsResponse(
        kind=kind,
        date_from=start,
        date_to=end,
        days=[
            ResultDay(
                pred_date=d.pred_date,
                model_version=d.model_version,
                rows=[
                    ResultRow(
                        rank=r.rank,
                        code=r.code,
                        name=r.name,
                        market=r.market,
                        base_price=float(r.base_price),
                        cells=[
                            ResultCell(
                                horizon=h,
                                target_date=c.target_date,
                                rel_prob=float(c.rel_prob),
                                up_prob=float(c.up_prob),
                                pred_price=float(c.pred_price),
                                actual=to_float(c.actual),
                            )
                            for h in const.PREDICT_HORIZONS
                            if (c := r.cells.get(h)) is not None
                        ],
                        notes=to_notes(r.notes),
                    )
                    for r in d.rows
                ],
                summary=[
                    ResultSummary(horizon=h, market_median=to_float(median), beat_count=beat)
                    for h, (median, beat) in sorted(d.summary.items())
                ],
            )
            for d in days
        ],
    )
