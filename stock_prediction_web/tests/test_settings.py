"""GET /api/settings のテスト。"""

import pytest
from fastapi.testclient import TestClient

import const
from api import web_const


def test_settings_returns_web_and_batch_values(client: TestClient) -> None:
    """画面の設定値(web_const)とバッチの設定値(const)を返す。"""
    res = client.get("/api/settings")
    assert res.status_code == 200
    body = res.json()
    web = body["web"]
    assert web["status_refresh_sec"] == web_const.STATUS_REFRESH_SEC
    assert web["stock_search_limit"] == web_const.STOCK_SEARCH_LIMIT
    assert web["top_candidate_count"] == web_const.TOP_CANDIDATE_COUNT
    assert web["results_default_end_offset_days"] == web_const.RESULTS_DEFAULT_END_OFFSET_DAYS
    assert web["results_default_span_days"] == web_const.RESULTS_DEFAULT_SPAN_DAYS
    assert web["results_max_months"] == web_const.RESULTS_MAX_MONTHS
    assert web["candidates_default_days"] == web_const.CANDIDATES_DEFAULT_DAYS
    assert web["candidates_range_max_months"] == web_const.CANDIDATES_RANGE_MAX_MONTHS
    assert web["chart_default_period"] == web_const.CHART_DEFAULT_PERIOD
    assert web["accuracy_daily_default_months"] == web_const.ACCURACY_DAILY_DEFAULT_MONTHS
    assert web["compare_sector_top_n"] == web_const.COMPARE_SECTOR_TOP_N
    assert web["runs_default_days"] == web_const.RUNS_DEFAULT_DAYS
    assert web["runs_page_size"] == web_const.RUNS_PAGE_SIZE
    assert web["screen_page_size"] == web_const.SCREEN_PAGE_SIZE
    assert web["run_status_labels"]["FAILED"] == "失敗"
    assert web["batch_type_labels"]["SINGLE"] == "単独で起動"
    batch = body["batch"]
    assert batch["predict_horizons"] == const.PREDICT_HORIZONS
    assert batch["main_horizon"] == const.MAIN_HORIZON
    assert batch["buy_candidate_count"] == const.BUY_CANDIDATE_COUNT
    assert batch["buy_candidate_sector_count"] == const.BUY_CANDIDATE_SECTOR_COUNT
    assert batch["sector_cap_per_sector"] == const.SECTOR_CAP_PER_SECTOR
    assert batch["bb_days"] == const.BB_DAYS
    assert batch["up_prob_threshold"] == const.UP_PROB_THRESHOLD
    assert batch["rel_prob_threshold"] == const.REL_PROB_THRESHOLD
    assert batch["high_prob_threshold"] == const.HIGH_PROB_THRESHOLD
    assert batch["calibration_min_count"] == const.CALIBRATION_MIN_COUNT
    assert batch["calibration_warn_gap"] == const.CALIBRATION_WARN_GAP
    assert batch["process_labels"]["DAILY_BATCH"] == "日次バッチ"


def test_settings_follows_batch_const(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """バッチの const.py の値を変えると、そのまま返す(画面に数値を直接書かないため)。"""
    monkeypatch.setattr(const, "BB_DAYS", 25)
    monkeypatch.setattr(const, "PREDICT_HORIZONS", [5, 20])
    body = client.get("/api/settings").json()
    assert body["batch"]["bb_days"] == 25
    assert body["batch"]["predict_horizons"] == [5, 20]
