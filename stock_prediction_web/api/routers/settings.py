"""GET /api/settings: 画面の設定値。

画面を開いたときに1回だけ読む。画面の設定値(api/web_const.py)と、画面で使うバッチの設定値(const.py)を返す。
"""

from fastapi import APIRouter
from pydantic import BaseModel

import const
from api import web_const
from api.logger import log_api

router = APIRouter()


class WebSettings(BaseModel):
    """画面の設定値(api/web_const.py)。"""

    status_refresh_sec: int
    stock_search_limit: int
    top_candidate_count: int
    results_default_months: int
    results_max_months: int
    chart_default_period: str
    accuracy_daily_default_months: int
    compare_sector_top_n: int
    runs_default_days: int
    runs_page_size: int
    screen_page_size: int
    run_status_labels: dict[str, str]
    batch_type_labels: dict[str, str]


class BatchSettings(BaseModel):
    """画面で使うバッチの設定値(stock_prediction_batch の const.py)。"""

    predict_horizons: list[int]
    main_horizon: int
    buy_candidate_count: int
    buy_candidate_sector_count: int
    sector_cap_per_sector: int
    bb_days: int
    up_prob_threshold: float
    rel_prob_threshold: float
    high_prob_threshold: float
    calibration_min_count: int
    calibration_warn_gap: float
    process_labels: dict[str, str]


class SettingsResponse(BaseModel):
    """/api/settings の返り値。"""

    web: WebSettings
    batch: BatchSettings


def build_settings() -> SettingsResponse:
    """設定値を集める(テストから直接呼べるように、関数に分けている)。"""
    return SettingsResponse(
        web=WebSettings(
            status_refresh_sec=web_const.STATUS_REFRESH_SEC,
            stock_search_limit=web_const.STOCK_SEARCH_LIMIT,
            top_candidate_count=web_const.TOP_CANDIDATE_COUNT,
            results_default_months=web_const.RESULTS_DEFAULT_MONTHS,
            results_max_months=web_const.RESULTS_MAX_MONTHS,
            chart_default_period=web_const.CHART_DEFAULT_PERIOD,
            accuracy_daily_default_months=web_const.ACCURACY_DAILY_DEFAULT_MONTHS,
            compare_sector_top_n=web_const.COMPARE_SECTOR_TOP_N,
            runs_default_days=web_const.RUNS_DEFAULT_DAYS,
            runs_page_size=web_const.RUNS_PAGE_SIZE,
            screen_page_size=web_const.SCREEN_PAGE_SIZE,
            run_status_labels=dict(web_const.RUN_STATUS_LABELS),
            batch_type_labels=dict(web_const.BATCH_TYPE_LABELS),
        ),
        batch=BatchSettings(
            predict_horizons=list(const.PREDICT_HORIZONS),
            main_horizon=const.MAIN_HORIZON,
            buy_candidate_count=const.BUY_CANDIDATE_COUNT,
            buy_candidate_sector_count=const.BUY_CANDIDATE_SECTOR_COUNT,
            sector_cap_per_sector=const.SECTOR_CAP_PER_SECTOR,
            bb_days=const.BB_DAYS,
            up_prob_threshold=const.UP_PROB_THRESHOLD,
            rel_prob_threshold=const.REL_PROB_THRESHOLD,
            high_prob_threshold=const.HIGH_PROB_THRESHOLD,
            calibration_min_count=const.CALIBRATION_MIN_COUNT,
            calibration_warn_gap=const.CALIBRATION_WARN_GAP,
            process_labels=dict(const.PROCESS_LABELS),
        ),
    )


@router.get("/api/settings", response_model=SettingsResponse)
@log_api
def get_settings() -> SettingsResponse:
    """画面の設定値を返す。"""
    return build_settings()
