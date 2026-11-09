"""tools/evaluate_model.py(予測精度の改善案の評価)のテスト。"""

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
import pytest
from psycopg.rows import DictRow

import const
from common.env import resolve_path
from common.logger import log_file_path
from core import features
from tests.sample_data import insert_sample_data
from tools import evaluate_model as ev


def test_月の範囲() -> None:
    assert ev.month_ranges(date(2025, 11, 1), date(2026, 1, 1)) == [
        (date(2025, 11, 1), date(2025, 12, 1)),
        (date(2025, 12, 1), date(2026, 1, 1)),
        (date(2026, 1, 1), date(2026, 2, 1)),
    ]


def test_市場比の正解は同じ日の中央値より上がったら1() -> None:
    rows = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-01-05"] * 3 + ["2026-01-06"] * 2),
            "_target_rate": [0.03, 0.01, -0.02, -0.05, -0.01],
        }
    )
    assert ev.add_relative_target(rows)["_target_rel"].tolist() == [1, 0, 0, 0, 1]


def test_業種比_上位_等級の正解() -> None:
    rows = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-01-05"] * 5),
            "sector33": [1, 1, 2, 2, None],
            "_target_rate": [0.05, 0.01, -0.02, -0.04, 0.03],
        }
    )
    result = ev.add_comparison_targets(ev.add_relative_target(rows))
    # 業種1の中央値 0.03、業種2の中央値 -0.03。業種がない行は市場比(中央値 0.01 より上)と同じ
    assert result["_target_sector"].tolist() == [1, 0, 1, 0, 1]
    # 上位20%(5銘柄中1銘柄)
    assert result["_target_top"].tolist() == [1, 0, 0, 0, 0]
    # 5分位の等級(大きいほど上位)
    assert result["_target_grade"].tolist() == [4, 2, 1, 0, 3]


def test_隙間をあけると正解の日が確認用の初日より前の行だけを学習用にする() -> None:
    rows = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2025-06-25", "2025-06-27", "2025-06-30", "2025-07-01"]),
            "_target_date": pd.to_datetime(["2025-06-30", "2025-07-02", "2025-07-03", "2025-07-04"]),
        }
    )
    train, valid = ev.split_train_valid(rows, date(2025, 7, 1), purge=True)
    assert train["trade_date"].dt.day.tolist() == [25]
    assert valid["trade_date"].dt.day.tolist() == [1]
    train, _ = ev.split_train_valid(rows, date(2025, 7, 1), purge=False)
    assert train["trade_date"].dt.day.tolist() == [25, 27, 30]


def test_AUC() -> None:
    assert ev.auc_score(np.array([0, 0, 1, 1]), np.array([0.1, 0.2, 0.3, 0.4])) == 1.0
    assert ev.auc_score(np.array([0, 0, 1, 1]), np.array([0.4, 0.3, 0.2, 0.1])) == 0.0
    assert ev.auc_score(np.array([0, 1]), np.array([0.5, 0.5])) == 0.5
    assert np.isnan(ev.auc_score(np.array([1, 1]), np.array([0.1, 0.2])))


def test_1日分の指標() -> None:
    day = pd.DataFrame(
        {
            "score": [0.9, 0.8, 0.1, 0.2],
            "_target_rate": [0.05, -0.01, 0.0, 0.02],
            "_target_up": [1, 0, 0, 1],
            # 2番目の銘柄だけ、売買代金が少ない
            "turnover_20d_log": [20.0, 10.0, 20.0, 20.0],
        }
    )
    m = ev.daily_metrics(day, top_n=2)
    # 中央値は 0.01。上位2銘柄(0.05、-0.01)の平均は 0.02
    assert m["market_median"] == pytest.approx(0.01)
    assert m["top20_excess"] == pytest.approx(0.01)
    assert m["top20_beat"] == pytest.approx(0.5)
    # 流動性ありの上位2銘柄は 0.05、0.02
    assert m["top20_liquid_excess"] == pytest.approx(0.025)
    assert m["auc_daily"] == pytest.approx(0.75)


def test_絞り込んでから上位を選ぶ() -> None:
    day = pd.DataFrame(
        {
            "score": [0.9, 0.8, 0.7, 0.6],
            "_target_rate": [0.05, -0.01, 0.0, 0.02],
            "_target_up": [1, 0, 0, 1],
            "turnover_20d_log": [20.0, 20.0, 20.0, 20.0],
            # 1番目は上がる確率が50%未満、2番目は見込みがマイナス
            "up_prob": [0.45, 0.6, 0.55, 0.7],
            "pred_rate": [0.01, -0.02, 0.01, 0.03],
        }
    )
    # 絞り込まない: 1・2番目。上がる確率: 2・3番目。見込み: 1・3番目。両方: 3・4番目
    assert ev.candidate_pool(day, ev.FILTER_UP)["score"].tolist() == [0.8, 0.7, 0.6]
    assert ev.candidate_pool(day, ev.FILTER_PRICE)["score"].tolist() == [0.9, 0.7, 0.6]
    m = ev.daily_metrics(day, top_n=2, filter_=ev.FILTER_BOTH)
    assert m["cand_count"] == 2
    assert m["top20_return"] == pytest.approx(0.01)
    # ランクIC と AUC は全銘柄で計算するので、絞り込まない場合と同じ
    assert m["auc_daily"] == ev.daily_metrics(day, top_n=2)["auc_daily"]
    with pytest.raises(ValueError):
        ev.candidate_pool(day, "x")


def test_候補が0銘柄の日() -> None:
    day = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-01-05"] * 2 + ["2026-01-06"] * 2),
            "score": [0.9, 0.8, 0.7, 0.6],
            "_target_rate": [0.04, 0.0, -0.02, -0.04],
            "_target_up": [1, 0, 0, 0],
            "turnover_20d_log": [20.0] * 4,
            "up_prob": [0.6, 0.4, 0.3, 0.2],
            "pred_rate": [0.01] * 4,
        }
    )
    m = ev.summarize(day, ev.FILTER_UP)
    # 1日目は1銘柄(0.04)、2日目は0銘柄
    assert (m["cand_avg"], m["zero_days"]) == (0.5, 1.0)
    assert m["top20_return"] == pytest.approx(0.04)
    assert m["top20_return_cash"] == pytest.approx(0.02)


def test_改善案の設定() -> None:
    a = ev.lgbm_params(ev.VARIANTS["A"])
    assert (a["metric"], a["num_leaves"]) == ("binary_logloss", const.LGBM_PARAMS["num_leaves"])
    d = ev.lgbm_params(ev.VARIANTS["D"])
    assert (d["metric"], d["num_leaves"], d["lambda_l2"]) == ("auc", 31, 10.0)
    assert ev.VARIANTS["E"].target == ev.COL_TARGET_REL
    i = ev.lgbm_params(ev.VARIANTS["I"])
    assert (i["objective"], i["metric"], i["eval_at"]) == ("lambdarank", "ndcg", [const.BUY_CANDIDATE_COUNT])
    assert "eval_at" not in ev.lgbm_params(ev.VARIANTS["G"])
    # J・K・L は E と同じモデルを使い回す
    assert ev.VARIANTS["J"].model_key() == ev.VARIANTS["E"].model_key()
    assert ev.lgbm_params(ev.PRICE_MODEL)["objective"] == "regression"
    # M は L に特徴量の候補を足したもの。モデルは使い回さない
    m, l_ = ev.VARIANTS["M"], ev.VARIANTS["L"]
    assert m.model_key() != l_.model_key() and m.filter == l_.filter
    assert m.feature_names() == features.feature_names() + features.EXTRA_FEATURE_GROUPS["cross"]
    assert l_.feature_names() == features.feature_names()
    assert ev.helper_models(l_) == (ev.UP_MODEL, ev.PRICE_MODEL)
    up, price = ev.helper_models(m)
    assert up.extra == price.extra == ("cross",) and price.objective == "regression"
    # N は L に一目均衡表の特徴量を足したもの
    n = ev.VARIANTS["N"]
    assert n.feature_names() == features.feature_names() + features.EXTRA_FEATURE_GROUPS["ichimoku"]
    assert n.model_key() not in (l_.model_key(), m.model_key())
    assert ev.helper_models(n)[0].extra == ("ichimoku",)
    # P は L と同じモデルで並べ、絞り込みにだけ補正した上がる確率を使う
    p = ev.VARIANTS["P"]
    assert p.model_key() == l_.model_key() and p.filter == l_.filter
    up_cal, price_p = ev.helper_models(p)
    assert up_cal.calibrate and up_cal.model_key() != ev.UP_MODEL.model_key() and price_p == ev.PRICE_MODEL
    # R は L にボリンジャーバンドの収束の特徴量を足したもの
    r = ev.VARIANTS["R"]
    assert r.feature_names() == features.feature_names() + ["bb_width", "bb_squeeze"]
    assert r.model_key() not in (l_.model_key(), m.model_key(), n.model_key()) and r.filter == l_.filter
    assert ev.helper_models(r)[0].extra == ev.helper_models(r)[1].extra == ("bollinger",)
    # S は R と、収束を比べる平均の日数(120日)だけが違う
    s = ev.VARIANTS["S"]
    assert s.feature_names() == features.feature_names() + ["bb_width", "bb_squeeze_120"]
    # U は L の bb_pctb を、25日で計算した bb_pctb_25 に置き換えたもの。数と順番は同じ
    u = ev.VARIANTS["U"]
    names_u = u.feature_names()
    idx = features.feature_names().index("bb_pctb")
    assert len(names_u) == len(features.feature_names()) and names_u[idx] == "bb_pctb_25" and "bb_pctb" not in names_u
    assert u.model_key() != l_.model_key() and u.filter == l_.filter
    assert ev.helper_models(u)[0].replace == ev.helper_models(u)[1].replace == (("bb_pctb", "bb_pctb_25"),)
    assert s.model_key() != r.model_key() and s.filter == r.filter and s.target == r.target
    # O はトレンドラインからの位置、T は終値の回帰直線。2つは分けて比べる
    o, t = ev.VARIANTS["O"], ev.VARIANTS["T"]
    assert o.feature_names() == features.feature_names() + ["trend_support_gap", "trend_resist_gap"]
    assert t.feature_names() == features.feature_names() + ["trend_slope_20d", "trend_slope_60d", "trend_r2_20d"]
    assert o.model_key() != t.model_key() and o.filter == t.filter == l_.filter
    assert ev.helper_models(o)[0].extra == ("trendline",) and ev.helper_models(t)[1].extra == ("regression",)


def test_引数のチェック() -> None:
    args = ev.parse_args(["--from-month", "2026-01", "--to-month", "2026-06", "--variants", "A,C", "--horizons", "5"])
    assert (args.from_month, args.variant_keys, args.horizon_list) == (date(2026, 1, 1), ["A", "C"], [5])
    with pytest.raises(SystemExit):
        ev.parse_args(["--from-month", "2026-01", "--to-month", "2026-06", "--variants", "Z"])
    with pytest.raises(SystemExit):
        ev.parse_args(["--from-month", "2026-07", "--to-month", "2026-06"])
    with pytest.raises(SystemExit):
        ev.parse_args(["--from-month", "2026/01", "--to-month", "2026-06"])


def test_学習に使う期間の引数() -> None:
    # 省略した場合は全期間(None)だけ。指定した場合は、重なりを除いて指定の順
    base = ["--from-month", "2026-01", "--to-month", "2026-06"]
    assert ev.parse_args(base).train_years_list == [None]
    assert ev.parse_args([*base, "--train-years", "5,10,5"]).train_years_list == [5, 10]
    for wrong in ("5年", "0", "-1"):
        with pytest.raises(SystemExit):
            ev.parse_args([*base, "--train-years", wrong])
    assert (ev.train_years_label(None), ev.train_years_label(5)) == ("ALL", "5")
    assert (ev.train_years_text("ALL"), ev.train_years_text("10")) == ("全期間", "10年")


def test_学習に使う期間で行を絞る() -> None:
    rows = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2020-12-31", "2021-01-04", "2025-11-28", "2025-12-26"]),
            ev.COL_TARGET_DATE: pd.to_datetime(["2021-01-08", "2021-01-12", "2025-12-05", "2026-01-05"]),
        }
    )
    month = date(2026, 1, 1)
    # 全期間: 正解の日がその月の初日より前の行
    assert len(ev.train_rows(rows, month, None)) == 3
    # 直近5年: さらに、日付が 2021-01-01(5年前)以降の行だけ
    assert list(ev.train_rows(rows, month, 5)["trade_date"].dt.strftime("%Y-%m-%d")) == ["2021-01-04", "2025-11-28"]


def test_通しで動かすとCSVを出す(conn: psycopg.Connection[DictRow]) -> None:
    insert_sample_data(conn, date(2024, 7, 1), date(2025, 12, 26))
    assert (
        ev.main(
            [
                "--from-month",
                "2025-10",
                "--to-month",
                "2025-11",
                "--variants",
                "A,E,G,H,I,J,K,L,M,N,O,P,R,S,T",
                "--horizons",
                "5",
            ]
        )
        == 0
    )
    files = list(Path(resolve_path(const.LOG_DIR)).glob("evaluate_*.csv"))
    assert len(files) == 1
    result = pd.read_csv(files[0], encoding="utf-8-sig")
    assert list(result.columns) == ev.CSV_COLUMNS
    assert sorted(set(result["period"])) == ["2025-10", "2025-11", "ALL"]
    # --train-years を省略したので、全期間で学習した
    assert set(result["train_years"]) == {"ALL"}
    assert sorted(set(result["variant"])) == ["A", "E", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "R", "S", "T"]
    assert result["days"].gt(0).all()
    # O を選んだので、トレンドラインの線を引く計算も行った(線が引けない日は空なので、O の結果も出る)
    assert (result["variant"] == "O").any()
    # DB には何も書いていない
    count = conn.execute("SELECT count(*) AS n FROM model").fetchone()
    assert count is not None and count["n"] == 0


def test_学習に使う期間を比べるとCSVとログに期間を出す(conn: psycopg.Connection[DictRow]) -> None:
    insert_sample_data(conn, date(2024, 7, 1), date(2025, 12, 26))
    argv = ["--from-month", "2025-11", "--to-month", "2025-11", "--variants", "L", "--horizons", "5"]
    assert ev.main([*argv, "--train-years", "1,2"]) == 0
    files = list(Path(resolve_path(const.LOG_DIR)).glob("evaluate_*.csv"))
    result = pd.read_csv(files[0], encoding="utf-8-sig", dtype={"train_years": str})
    assert sorted(set(result["train_years"])) == ["1", "2"]
    assert sorted(zip(result["train_years"], result["period"], strict=True)) == [
        ("1", "2025-11"), ("1", "ALL"), ("2", "2025-11"), ("2", "ALL"),
    ]  # fmt: skip
    # 直近1年は、全期間(約1年4か月)より学習の行が少ないので、木の本数などが違ってよい。行は出ていること
    assert result["days"].gt(0).all()
    log = log_file_path().read_text(encoding="utf-8")
    assert "学習に使う期間: 1,2" in log
    assert "学習 1年 2025-11" in log and "学習 2年 2025-11" in log
    # ログの要約(全ての月)にも学習に使う期間を出す
    assert log.count("===== 5営業日後(全ての月)=====") == 1
    assert "学習 1年 上位20の超過" in log and "学習 2年 上位20の超過" in log


def test_DBには書き込めない(conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> None:
    def write(c: psycopg.Connection[DictRow], *args: object) -> pd.DataFrame:
        c.execute(
            "INSERT INTO trading_calendar (cal_date, holiday_division, is_business_day)"
            " VALUES ('2026-01-05', '1', true)"
        )
        return pd.DataFrame()

    monkeypatch.setattr(ev, "evaluate", write)
    assert ev.main(["--from-month", "2026-01", "--to-month", "2026-01"]) == 1
    count = conn.execute("SELECT count(*) AS n FROM trading_calendar").fetchone()
    assert count is not None and count["n"] == 0


def test_株価がない場合は失敗(conn: psycopg.Connection[DictRow]) -> None:
    assert ev.main(["--from-month", "2026-01", "--to-month", "2026-01"]) == 1
