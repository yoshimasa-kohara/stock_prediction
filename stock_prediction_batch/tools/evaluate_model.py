"""予測精度の改善案を、同じ条件で比べる評価用のスクリプト(精度改善の段階0)。

本番のバッチからは呼ばない。DB は読み取り専用で使い、モデルのファイルも保存しない。

  1. DB にある全ての期間の特徴量を、本番と同じ features.build_features で1回だけ計算する。
  2. 評価する月ごとに、「正解の日(N営業日後)がその月の初日より前の行」だけで学習し、その月の各日を予測する
     (ウォークフォワード。未来のデータを学習に使わない)。
  3. 改善案(VARIANTS)ごと・予測日数ごとに、上昇確率用のモデルだけを学習して、買い候補(上位20銘柄)が
     市場(全銘柄の中央値)に勝ったかなどの指標を計算し、logs/evaluate_YYYYMMDD_HHMM.csv に出す。
  4. 改善案 J・K・L は、市場比の順位(E と同じモデル)で並べる前に、補助のモデル(上がる確率、予測株価)で
     買い候補を絞り込む。絞り込んだ結果、20銘柄に届かない日や0銘柄の日もある。
  5. 改善案 M・N・R は、本番の方式R(改善案 L と同じ)に、特徴量の候補(features.EXTRA_FEATURE_GROUPS)を足したもの。
     L と比べて、特徴量を足すかを決める。M: 移動平均のクロス、N: 一目均衡表、
     R・S: ボリンジャーバンドの収束(R は過去60日、S は過去120日の幅の平均と比べる)、
     O: トレンドラインからの位置、T: 終値の回帰直線。
     改善案の R と、買い候補の方式R(RANKING_METHOD)は別のもの。
     U は、L の特徴量 bb_pctb(ボリンジャーバンドの %b。20日)を、25日で計算した bb_pctb_25 に置き換えたもの。
     特徴量の数は L と同じ47個。
     O を選んだ場合だけ、トレンドラインの線を引く計算(重い)を行う(features.build_features の with_trendline)。
  6. 改善案 P は、L の買い候補の条件「上がる確率50%以上」に、補正した上がる確率を使うもの。
     補正の対応表は、本番と同じく、学習の1段階目のモデルで確認用の期間を予測した値と実際の結果から作る。
  7. --train-years 5,10 を指定すると、学習に使う期間(TRAIN_PERIOD_YEARS)を比べる。
     各月を「その月の初日より前の直近N年(trade_date がその月の初日のN年前以降)の行だけ」で学習した場合を、
     N ごとに評価する。CSV とログの要約の train_years の列に N を出す。省略した場合は今までと同じく全期間で学習し、
     train_years は ALL になる。

実行(stock_prediction_batch のフォルダで):
  python -m tools.evaluate_model --from-month 2026-01 --to-month 2026-06
  python -m tools.evaluate_model --from-month 2025-07 --to-month 2026-06 --variants L --train-years 5,10
"""

import argparse
import math
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
import psycopg
from psycopg.rows import DictRow

import const
from common import business_day, db
from common.args import today_jst
from common.env import resolve_path
from common.logger import BatchLogger, get_logger
from core import features
from core.calibration import fit_calibration
from core.features import CATEGORICAL_FEATURES
from core.train_core import COL_TARGET_RATE, COL_TARGET_UP, add_targets, target_codes

LABEL: str = "評価"

# 正解の列
COL_TARGET_REL: str = "_target_rel"  # 市場比: 同じ日の全銘柄の中央値より上がったか
COL_TARGET_SECTOR: str = "_target_sector"  # 業種比: 同じ日・同じ33業種の中央値より上がったか
COL_TARGET_TOP: str = "_target_top"  # 上位に入ったか: 同じ日の騰落率で上位 TOP_SHARE に入ったか
COL_TARGET_GRADE: str = "_target_grade"  # ランキング学習の等級: 同じ日の騰落率の5分位(0〜4。大きいほど上位)
# 「上位に入ったか」の上位の割合(20%)
TOP_SHARE: float = 0.2
# ランキング学習の等級の数
GRADE_COUNT: int = 5
# 予測株価用の正解(騰落率の極端な値を、境目の値に置き換えたもの。本番の予測株価用と同じ)
COL_TARGET_RATE_CLIPPED: str = "_target_rate_clipped"
# 正解の日(add_targets が付ける列)
COL_TARGET_DATE: str = "_target_date"
# 買い候補の銘柄数(本番の BUY_CANDIDATE_COUNT と同じ)
TOP_N: int = const.BUY_CANDIDATE_COUNT
# 流動性ありとみなす20日平均売買代金(本番の流動性注意の基準と同じ。1億円)
LIQUID_TURNOVER_LOG: float = math.log(const.LIQUIDITY_WARN_TURNOVER + 1)
# 控えめの設定(改善案 D、F)
CONSERVATIVE_PARAMS: dict[str, float | int | bool] = {"num_leaves": 31, "feature_fraction": 0.6, "lambda_l2": 10.0}
# 買い候補の絞り込み方(改善案 J、K、L)
FILTER_NONE: str = ""  # 絞り込まない
FILTER_UP: str = "up"  # 上がる確率が UP_PROB_THRESHOLD(50%)以上
FILTER_PRICE: str = "price"  # 見込み(予測株価 ÷ 今日の株価 − 1)がプラス
FILTER_BOTH: str = "both"  # 両方を満たす
# 補助のモデルの予測を入れる列
COL_UP_PROB: str = "up_prob"  # 上がる確率
COL_PRED_RATE: str = "pred_rate"  # 見込み(予測した騰落率)


@dataclass(frozen=True)
class Variant:
    """比べる改善案。"""

    key: str
    title: str
    target: str  # 正解の列(COL_TARGET_UP、COL_TARGET_REL など)
    metric: str  # 学習を止める基準(binary_logloss、auc、ndcg)
    purge: bool  # 学習用と確認用の間に隙間をあけるか
    params: dict[str, float | int | bool] = field(default_factory=dict)  # LGBM_PARAMS に上書きする設定
    objective: str = "binary"  # 学習の種類(binary: 二択、lambdarank: ランキング学習、regression: 回帰)
    filter: str = FILTER_NONE  # 買い候補の絞り込み方(FILTER_*)
    extra: tuple[str, ...] = ()  # 足す特徴量の候補のグループ(features.EXTRA_FEATURE_GROUPS のキー)
    calibrate: bool = False  # 予測した確率を、確認用の期間で作った対応表で補正するか(補助の上がる確率のモデル用)
    calibrate_up_filter: bool = False  # 買い候補の絞り込みに、補正した上がる確率を使うか(改善案 P)
    replace: tuple[tuple[str, str], ...] = ()  # 置き換える特徴量((本番の名前, 候補の名前)。改善案 U)

    def model_key(self) -> tuple[object, ...]:
        """学習するモデルの設定。同じ設定の改善案(E と J・K・L など)は、同じモデルを使い回す。"""
        return (
            self.target, self.metric, self.purge, tuple(sorted(self.params.items())), self.objective,
            self.extra, self.calibrate, self.replace,
        )  # fmt: skip

    def feature_names(self) -> list[str]:
        """学習と予測に使う特徴量の名前。"""
        swap = dict(self.replace)
        base = [swap.get(n, n) for n in features.feature_names()]
        return base + [n for g in self.extra for n in features.EXTRA_FEATURE_GROUPS[g]]


VARIANTS: dict[str, Variant] = {
    v.key: v
    for v in [
        Variant("A", "現行", COL_TARGET_UP, "binary_logloss", False),
        Variant("B", "AUC", COL_TARGET_UP, "auc", False),
        Variant("C", "AUC+隙間", COL_TARGET_UP, "auc", True),
        Variant("D", "AUC+隙間+控えめ", COL_TARGET_UP, "auc", True, CONSERVATIVE_PARAMS),
        Variant("E", "市場比+AUC+隙間", COL_TARGET_REL, "auc", True),
        Variant("F", "市場比+AUC+隙間+控えめ", COL_TARGET_REL, "auc", True, CONSERVATIVE_PARAMS),
        Variant("G", "業種比+AUC+隙間", COL_TARGET_SECTOR, "auc", True),
        Variant("H", "上位20%+AUC+隙間", COL_TARGET_TOP, "auc", True),
        Variant("I", "ランキング学習+隙間", COL_TARGET_GRADE, "ndcg", True, objective="lambdarank"),
        Variant("J", "市場比の順位+上がる確率50%以上", COL_TARGET_REL, "auc", True, filter=FILTER_UP),
        Variant("K", "市場比の順位+見込みプラス", COL_TARGET_REL, "auc", True, filter=FILTER_PRICE),
        Variant("L", "市場比の順位+両方", COL_TARGET_REL, "auc", True, filter=FILTER_BOTH),
        Variant("M", "L+クロスの特徴量", COL_TARGET_REL, "auc", True, filter=FILTER_BOTH, extra=("cross",)),
        Variant("N", "L+一目均衡表の特徴量", COL_TARGET_REL, "auc", True, filter=FILTER_BOTH, extra=("ichimoku",)),
        Variant("P", "L+上がる確率の補正", COL_TARGET_REL, "auc", True, filter=FILTER_BOTH, calibrate_up_filter=True),
        Variant(
            "R",
            "L+ボリンジャーバンドの収束(60日)",
            COL_TARGET_REL,
            "auc",
            True,
            filter=FILTER_BOTH,
            extra=("bollinger",),
        ),
        Variant(
            "S",
            "L+ボリンジャーバンドの収束(120日)",
            COL_TARGET_REL,
            "auc",
            True,
            filter=FILTER_BOTH,
            extra=("bollinger_120",),
        ),
        Variant(
            "O",
            "L+トレンドラインからの位置",
            COL_TARGET_REL,
            "auc",
            True,
            filter=FILTER_BOTH,
            extra=("trendline",),
        ),
        Variant(
            "T",
            "L+終値の回帰直線",
            COL_TARGET_REL,
            "auc",
            True,
            filter=FILTER_BOTH,
            extra=("regression",),
        ),
        Variant(
            "U",
            "L+ボリンジャーバンドを25日(bb_pctb を置き換え)",
            COL_TARGET_REL,
            "auc",
            True,
            filter=FILTER_BOTH,
            replace=(("bb_pctb", "bb_pctb_25"),),
        ),  # fmt: skip
    ]
}

# 絞り込みに使う補助のモデル(上がる確率は改善案 C と同じ設定。予測株価は本番と同じ回帰に、隙間をあけたもの)
UP_MODEL: Variant = Variant("UP", "補助: 上がる確率", COL_TARGET_UP, "auc", True)
PRICE_MODEL: Variant = Variant("PRICE", "補助: 予測株価", COL_TARGET_RATE_CLIPPED, "l2", True, objective="regression")


def helper_models(variant: Variant) -> tuple[Variant, Variant]:
    """絞り込みに使う補助のモデル(上がる確率、予測株価)。特徴量は、改善案と同じものを使う。"""
    if variant.calibrate_up_filter:
        return Variant("UP_CAL", "補助: 上がる確率(補正あり)", COL_TARGET_UP, "auc", True, calibrate=True), PRICE_MODEL
    if not variant.extra and not variant.replace:
        return UP_MODEL, PRICE_MODEL
    # 特徴量を足す・置き換える改善案は、補助のモデルも同じ特徴量で学習する(本番では3種類のモデルが同じ特徴量を使うため)
    return (
        Variant(
            "UP+", "補助: 上がる確率(特徴量の候補あり)", COL_TARGET_UP, "auc", True,
            extra=variant.extra, replace=variant.replace,
        ),
        Variant(
            "PRICE+", "補助: 予測株価(特徴量の候補あり)", COL_TARGET_RATE_CLIPPED, "l2", True,
            objective="regression", extra=variant.extra, replace=variant.replace,
        ),
    )  # fmt: skip


# CSV の列(1行 = 改善案 × 予測日数 × 期間(月、または ALL))
# 学習に使う期間を指定しない(全期間で学習する)場合の、train_years の列の値
TRAIN_YEARS_ALL: str = "ALL"

CSV_COLUMNS: list[str] = [
    "variant", "title", "horizon", "train_years", "period", "days", "rows", "trees", "score_std",
    "top20_excess", "top20_beat", "top20_return", "top20_liquid_excess", "market_median",
    "rank_ic", "auc_daily", "auc", "hit_rate", "up_rate",
    "cand_avg", "zero_days", "top20_return_cash",
]  # fmt: skip


# =====================================================================
# 正解と分割
# =====================================================================


def add_relative_target(rows: pd.DataFrame) -> pd.DataFrame:
    """市場比の正解(同じ日の全銘柄の騰落率の中央値より上がったら 1)を足す。"""
    median = rows.groupby("trade_date")[COL_TARGET_RATE].transform("median")
    return rows.assign(**{COL_TARGET_REL: (rows[COL_TARGET_RATE] > median).astype(int)})


def add_comparison_targets(rows: pd.DataFrame) -> pd.DataFrame:
    """市場比のほかに、業種比・上位に入ったか・ランキング学習の等級の正解を足す。

    業種比は、33業種コードがない行は市場比と同じにする。
    """
    rate = rows[COL_TARGET_RATE]
    sector_median = rows.groupby(["trade_date", "sector33"], dropna=False)[COL_TARGET_RATE].transform("median")
    sector_target = (rate > sector_median).astype(int).where(rows["sector33"].notna(), rows[COL_TARGET_REL])
    pct = rows.groupby("trade_date")[COL_TARGET_RATE].rank(pct=True, method="first")
    grade = (np.ceil(pct * GRADE_COUNT) - 1).clip(lower=0, upper=GRADE_COUNT - 1)
    return rows.assign(
        **{
            COL_TARGET_SECTOR: sector_target,
            COL_TARGET_TOP: (pct > 1 - TOP_SHARE).astype(int),
            COL_TARGET_GRADE: grade.astype(int),
        }
    )


def split_train_valid(train_all: pd.DataFrame, valid_start: date, purge: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    """学習用と確認用に分ける。

    purge が True の場合、学習用は「正解の日が確認用の初日より前」の行だけにする(隙間をあける)。
    False の場合は本番と同じく、日付が確認用の初日より前の行を全て学習用にする。
    """
    start = pd.Timestamp(valid_start)
    valid = train_all.loc[train_all["trade_date"] >= start]
    if purge:
        train = train_all.loc[train_all[COL_TARGET_DATE] < start]
    else:
        train = train_all.loc[train_all["trade_date"] < start]
    return train, valid


def month_ranges(from_month: date, to_month: date) -> list[tuple[date, date]]:
    """評価する月の(初日, 翌月の初日)のリスト。"""
    result: list[tuple[date, date]] = []
    current = from_month.replace(day=1)
    while current <= to_month:
        nxt = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
        result.append((current, nxt))
        current = nxt
    return result


# =====================================================================
# 学習
# =====================================================================


def lgbm_params(variant: Variant) -> dict[str, Any]:
    """改善案の LightGBM の設定。本番の LGBM_PARAMS に、改善案の設定を上書きする。"""
    params: dict[str, Any] = dict(const.LGBM_PARAMS)
    params.update(variant.params)
    params.update({"objective": variant.objective, "metric": variant.metric, "verbose": -1, "force_col_wise": True})
    if variant.objective == "lambdarank":
        # 上位20銘柄の並びの正しさ(NDCG@20)で、学習を止める本数を決める
        params["eval_at"] = [TOP_N]
    return params


def _dataset(rows: pd.DataFrame, variant: Variant) -> lgb.Dataset:
    """学習用のデータ。ランキング学習の場合は、日付ごとの行の数(グループ)も渡す。

    rows は日付の順に並んでいること(ランキング学習では、同じ日の行が続いている必要があるため)。
    """
    group = rows.groupby("trade_date", sort=False).size().to_numpy() if variant.objective == "lambdarank" else None
    return lgb.Dataset(
        rows[variant.feature_names()],
        label=rows[variant.target],
        group=group,
        categorical_feature=CATEGORICAL_FEATURES,
        free_raw_data=False,
        params={"verbose": -1},
    )


def fit_predict(
    train_all: pd.DataFrame, test: pd.DataFrame, variant: Variant, valid_start: date
) -> tuple[np.ndarray, int]:
    """本番と同じく、確認用の成績で木の本数を決めてから全ての行で学習し直し、test を予測する。

    Returns:
        (予測した値, 木の本数)
    """
    names = variant.feature_names()
    train_all = train_all.sort_values(["trade_date", "code"], kind="stable")
    if variant.target == COL_TARGET_RATE_CLIPPED:
        # 本番と同じく、学習に使う行の騰落率の上下1%を、境目の値に置き換える
        lower = float(train_all[COL_TARGET_RATE].quantile(const.PRICE_TARGET_CLIP_RATE))
        upper = float(train_all[COL_TARGET_RATE].quantile(1 - const.PRICE_TARGET_CLIP_RATE))
        train_all = train_all.assign(**{COL_TARGET_RATE_CLIPPED: train_all[COL_TARGET_RATE].clip(lower, upper)})
    train, valid = split_train_valid(train_all, valid_start, variant.purge)
    if train.empty or valid.empty:
        raise ValueError(f"学習用または確認用のデータがありません(確認用の初日: {valid_start})")
    params = lgbm_params(variant)
    first = lgb.train(
        params,
        _dataset(train, variant),
        num_boost_round=const.LGBM_MAX_ROUNDS,
        valid_sets=[_dataset(valid, variant)],
        callbacks=[lgb.early_stopping(const.LGBM_EARLY_STOPPING_ROUNDS, verbose=False)],
    )
    rounds = first.best_iteration if first.best_iteration > 0 else first.current_iteration()
    final = lgb.train(params, _dataset(train_all, variant), num_boost_round=rounds)
    predicted = np.asarray(final.predict(test[names]), dtype=float)
    if variant.calibrate:
        # 本番と同じく、1段階目のモデルで確認用の期間を予測した値と実際の結果から対応表を作る。
        # 組のまとめと基準率への縮小は fit_calibration の中で同じように効く。
        # 直近1年分の補正用データを貯める仕組み(同 A)は再現しない。ここでは今回の確認用の期間の行だけで作る
        on_valid = np.asarray(first.predict(valid[names], num_iteration=rounds), dtype=float)
        predicted = fit_calibration(on_valid, valid[variant.target].to_numpy()).apply(predicted)
    return predicted, int(rounds)


# 学習したモデルの予測の置き場所。キーは (モデルの設定, 確認用の初日)
PredictCache = dict[tuple[object, ...], tuple[np.ndarray, int]]


def cached_fit_predict(
    cache: PredictCache, variant: Variant, train_all: pd.DataFrame, test: pd.DataFrame, valid_start: date
) -> tuple[np.ndarray, int]:
    """fit_predict と同じ。同じ設定・同じ月のモデルは1回だけ学習し、2回目からは覚えておいた予測を返す。

    cache は予測日数ごとに作り直すこと(予測日数が違うと、正解も test も違うため)。
    """
    key = (*variant.model_key(), valid_start)
    if key not in cache:
        cache[key] = fit_predict(train_all, test, variant, valid_start)
    return cache[key]


# =====================================================================
# 指標
# =====================================================================


def auc_score(y: np.ndarray, score: np.ndarray) -> float:
    """AUC(上がる銘柄を上位に並べられているか。0.5 = でたらめ)。片方の値しかない場合は NaN。"""
    y = np.asarray(y, dtype=int)
    n_pos = int(y.sum())
    n_neg = len(y) - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = pd.Series(score).rank(method="average").to_numpy()
    return float((ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def candidate_pool(day: pd.DataFrame, filter_: str) -> pd.DataFrame:
    """買い候補になれる銘柄(絞り込み方 filter_ の条件を満たす銘柄)。"""
    if filter_ == FILTER_NONE:
        return day
    up_ok = day[COL_UP_PROB] >= const.UP_PROB_THRESHOLD
    price_ok = day[COL_PRED_RATE] > 0
    if filter_ == FILTER_UP:
        return day.loc[up_ok]
    if filter_ == FILTER_PRICE:
        return day.loc[price_ok]
    if filter_ == FILTER_BOTH:
        return day.loc[up_ok & price_ok]
    raise ValueError(f"絞り込み方が正しくありません({filter_})")


def daily_metrics(day: pd.DataFrame, top_n: int = TOP_N, filter_: str = FILTER_NONE) -> dict[str, float]:
    """1日分の指標。day の列: score、_target_rate、_target_up、turnover_20d_log(絞り込む場合は up_prob、pred_rate も)。

    買い候補の指標(top20_*)は、絞り込んだ銘柄の中で score の高い順に top_n 銘柄を選んで計算する。
    候補が0銘柄の日は NaN にする(平均には入れない)。top20_return_cash は、0銘柄の日を「買わない = 0%」とした騰落率。
    ランクIC と AUC は、絞り込まない全銘柄で計算する(モデルの並べ方の力を測るため)。
    """
    rate = day[COL_TARGET_RATE]
    median = float(rate.median())
    pool = candidate_pool(day, filter_)
    top = pool.nlargest(top_n, "score")
    liquid = pool.loc[pool["turnover_20d_log"] >= LIQUID_TURNOVER_LOG].nlargest(top_n, "score")
    nan = float("nan")
    top_return = float(top[COL_TARGET_RATE].mean()) if len(top) else nan
    return {
        "top20_excess": top_return - median if len(top) else nan,
        "top20_beat": float((top[COL_TARGET_RATE] > median).mean()) if len(top) else nan,
        "top20_return": top_return,
        "top20_liquid_excess": float(liquid[COL_TARGET_RATE].mean()) - median if len(liquid) else nan,
        "market_median": median,
        "rank_ic": float(day["score"].rank().corr(rate.rank())),
        "auc_daily": auc_score(day[COL_TARGET_UP].to_numpy(), day["score"].to_numpy()),
        "cand_count": float(len(top)),
        "top20_return_cash": top_return if len(top) else 0.0,
    }


def summarize(pred: pd.DataFrame, filter_: str = FILTER_NONE) -> dict[str, float]:
    """予測の表(複数日)から、日ごとの指標の平均と、全体の指標を求める。"""
    per_day = pd.DataFrame([daily_metrics(d, filter_=filter_) for _, d in pred.groupby("trade_date")])
    counts = per_day.pop("cand_count")
    result: dict[str, float] = {k: float(per_day[k].mean()) for k in per_day.columns}
    result["cand_avg"] = float(counts.mean())
    result["zero_days"] = float((counts == 0).sum())
    result.update(
        {
            "days": float(len(per_day)),
            "rows": float(len(pred)),
            "score_std": float(pred["score"].std()),
            "auc": auc_score(pred[COL_TARGET_UP].to_numpy(), pred["score"].to_numpy()),
            "hit_rate": float(((pred["score"] >= const.UP_PROB_THRESHOLD).astype(int) == pred[COL_TARGET_UP]).mean()),
            "up_rate": float(pred[COL_TARGET_UP].mean()),
        }
    )
    return result


# =====================================================================
# 全体の流れ
# =====================================================================


def data_range(conn: psycopg.Connection[DictRow]) -> tuple[date, date]:
    """株価がある最も古い日と、株価を取得済みの最も新しい日。"""
    row = conn.execute(
        """
        SELECT (SELECT min(trade_date) FROM daily_price) AS first_day,
               (SELECT max(target_date) FROM fetched_date WHERE data_type = %s AND status = %s) AS last_day
        """,
        (const.DATA_TYPE_PRICE, const.FETCH_STATUS_FETCHED),
    ).fetchone()
    if row is None or row["first_day"] is None or row["last_day"] is None:
        raise ValueError("株価がありません")
    return row["first_day"], row["last_day"]


def train_years_label(train_years: int | None) -> str:
    """CSV とログに出す、学習に使う期間の表示(5、10、全期間は ALL)。"""
    return TRAIN_YEARS_ALL if train_years is None else str(train_years)


def train_years_text(label: str) -> str:
    """ログに出す、学習に使う期間(5年、10年、全期間)。label は train_years_label の値。"""
    return "全期間" if label == TRAIN_YEARS_ALL else f"{label}年"


def train_rows(rows: pd.DataFrame, month_start: date, train_years: int | None) -> pd.DataFrame:
    """その月の学習に使う行。正解の日がその月の初日より前の行で、train_years を指定した場合は直近N年の行だけ。

    直近N年は、本番の学習(TRAIN_PERIOD_YEARS)と同じく、日付がその月の初日のN年前以降の行とする。
    """
    start_ts = pd.Timestamp(month_start)
    mask = rows[COL_TARGET_DATE] < start_ts
    if train_years is not None:
        mask &= rows["trade_date"] >= pd.Timestamp(business_day.years_before(month_start, train_years))
    return rows.loc[mask]


def evaluate(
    conn: psycopg.Connection[DictRow],
    months: list[tuple[date, date]],
    variant_keys: Sequence[str],
    horizons: Sequence[int],
    logger: BatchLogger,
    train_years_list: Sequence[int | None] = (None,),
) -> pd.DataFrame:
    """改善案 × 予測日数 × 学習に使う期間 × 月 の指標の表を返す。

    train_years_list は学習に使う期間(直近N年)のリスト。None は全期間。
    """
    first_day, last_day = data_range(conn)
    logger.info("株価の期間: %s 〜 %s", first_day, last_day)
    started = time.monotonic()
    # トレンドラインの線を引く計算は重いので、それを使う改善案(O)を選んだときだけ行う
    with_trendline = any("trendline" in VARIANTS[key].extra for key in variant_keys)
    data = features.build_features(conn, last_day, first_day, target_codes(conn), with_trendline=with_trendline)
    logger.info("特徴量を計算しました(%s行、%.0f秒)", f"{len(data):,}", time.monotonic() - started)

    out: list[dict[str, Any]] = []
    for horizon in horizons:
        rows = add_comparison_targets(add_relative_target(add_targets(data, horizon)))
        for train_years in train_years_list:
            out.extend(evaluate_horizon(rows, months, variant_keys, horizon, train_years, logger))
    return pd.DataFrame(out, columns=CSV_COLUMNS)


def evaluate_horizon(
    rows: pd.DataFrame,
    months: list[tuple[date, date]],
    variant_keys: Sequence[str],
    horizon: int,
    train_years: int | None,
    logger: BatchLogger,
) -> list[dict[str, Any]]:
    """1つの予測日数・1つの学習に使う期間について、改善案 × 月 の指標の行を返す。"""
    years = train_years_label(train_years)
    out: list[dict[str, Any]] = []
    # 学習したモデルの予測を、(モデルの設定, 月)ごとに覚えておき、同じ設定の改善案で使い回す
    # (学習に使う期間が違うとモデルも違うので、期間ごとに作り直す)
    cache: PredictCache = {}
    for key in variant_keys:
        variant = VARIANTS[key]
        preds: list[pd.DataFrame] = []
        trees: list[int] = []
        for month_start, month_end in months:
            begin = time.monotonic()
            start_ts, end_ts = pd.Timestamp(month_start), pd.Timestamp(month_end)
            test = rows.loc[(rows["trade_date"] >= start_ts) & (rows["trade_date"] < end_ts)]
            train_all = train_rows(rows, month_start, train_years)
            if test.empty:
                logger.warning("%d営業日後 %s %s: 正解の出ている行がないので飛ばします", horizon, key, month_start)
                continue
            valid_start = business_day.months_before(month_start, const.VALID_PERIOD_MONTHS)
            score, rounds = cached_fit_predict(cache, variant, train_all, test, valid_start)
            pred = test[["trade_date", "code", COL_TARGET_RATE, COL_TARGET_UP, "turnover_20d_log"]].assign(score=score)
            if variant.filter != FILTER_NONE:
                up_model, price_model = helper_models(variant)
                up_prob, _ = cached_fit_predict(cache, up_model, train_all, test, valid_start)
                pred_rate, _ = cached_fit_predict(cache, price_model, train_all, test, valid_start)
                pred = pred.assign(**{COL_UP_PROB: up_prob, COL_PRED_RATE: pred_rate})
            preds.append(pred)
            trees.append(rounds)
            m = summarize(pred, variant.filter)
            out.append({"variant": key, "title": variant.title, "horizon": horizon, "train_years": years,
                        "period": month_start.strftime("%Y-%m"), "trees": rounds, **m})  # fmt: skip
            logger.info(
                "%d営業日後 %s(%s) 学習 %s %s: 上位20の超過 %+.2f%% 勝率 %.1f%% ランクIC %+.3f 候補 %.1f銘柄/日"
                " 0銘柄の日 %d日 木 %d本(%.0f秒)",
                horizon, key, variant.title, train_years_text(years), month_start.strftime("%Y-%m"),
                m["top20_excess"] * 100, m["top20_beat"] * 100, m["rank_ic"], m["cand_avg"], m["zero_days"], rounds,
                time.monotonic() - begin,
            )  # fmt: skip
        if preds:
            m = summarize(pd.concat(preds, ignore_index=True), variant.filter)
            out.append({"variant": key, "title": variant.title, "horizon": horizon, "train_years": years,
                        "period": "ALL", "trees": float(np.mean(trees)), **m})  # fmt: skip
    return out


def log_summary(result: pd.DataFrame, logger: BatchLogger) -> None:
    """全期間(ALL)の結果を、予測日数ごとにログに出す。学習に使う期間(train_years)も出す。"""
    all_rows = result.loc[result["period"] == "ALL"]
    for horizon, group in all_rows.groupby("horizon"):
        logger.info("===== %d営業日後(全ての月)=====", horizon)
        for r in group.to_dict("records"):
            logger.info(
                "%s %-22s 学習 %s 上位20の超過 %+.2f%% 勝率 %.1f%% 流動性ありの超過 %+.2f%% ランクIC %+.3f AUC %.3f"
                " 木 %.0f本 候補 %.1f銘柄/日 0銘柄の日 %.0f日 騰落率(0銘柄の日は0%%) %+.2f%%",
                r["variant"], r["title"], train_years_text(str(r["train_years"])),
                float(r["top20_excess"]) * 100, float(r["top20_beat"]) * 100,
                float(r["top20_liquid_excess"]) * 100, float(r["rank_ic"]), float(r["auc_daily"]), float(r["trees"]),
                float(r["cand_avg"]), float(r["zero_days"]), float(r["top20_return_cash"]) * 100,
            )  # fmt: skip


def parse_month(text: str) -> date:
    """YYYY-MM を、その月の1日にする。"""
    try:
        return datetime.strptime(text, "%Y-%m").date()
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"月の形式が正しくありません({text})。YYYY-MM で指定してください") from e


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="予測精度の改善案を比べる(読み取り専用)")
    parser.add_argument("--from-month", type=parse_month, required=True, help="評価する最初の月(YYYY-MM)")
    parser.add_argument("--to-month", type=parse_month, required=True, help="評価する最後の月(YYYY-MM)")
    parser.add_argument("--variants", default=",".join(VARIANTS), help="比べる改善案(例: A,B,C)")
    parser.add_argument("--horizons", default=",".join(map(str, const.PREDICT_HORIZONS)), help="予測日数(例: 5,10)")
    parser.add_argument(
        "--train-years",
        default="",
        help="学習に使う期間(直近N年)を比べる(例: 5,10)。省略した場合は全期間で学習する",
    )
    args = parser.parse_args(argv)
    args.variant_keys = [v.strip() for v in args.variants.split(",") if v.strip()]
    unknown = [v for v in args.variant_keys if v not in VARIANTS]
    if unknown:
        parser.error(f"改善案の名前が正しくありません({','.join(unknown)})")
    args.horizon_list = [int(h) for h in args.horizons.split(",") if h.strip()]
    try:
        years = [int(y) for y in args.train_years.split(",") if y.strip()]
    except ValueError:
        parser.error(f"--train-years は 5,10 のように年数をカンマで区切って指定してください({args.train_years})")
    if any(y < 1 for y in years):
        parser.error(f"--train-years には1以上の年数を指定してください({args.train_years})")
    args.train_years_list = list(dict.fromkeys(years)) if years else [None]
    if args.from_month > args.to_month:
        parser.error("--from-month は --to-month 以前の月を指定してください")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    logger = get_logger(LABEL, today_jst())
    path: Path = resolve_path(const.LOG_DIR) / f"evaluate_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    logger.info(
        "処理を開始します(月: %s 〜 %s、改善案: %s、予測日数: %s、学習に使う期間: %s)",
        args.from_month.strftime("%Y-%m"), args.to_month.strftime("%Y-%m"), ",".join(args.variant_keys),
        ",".join(map(str, args.horizon_list)), ",".join(train_years_label(y) for y in args.train_years_list),
    )  # fmt: skip
    started = time.monotonic()
    try:
        with db.connect() as conn:
            # 読み取り専用にする。間違って書き込もうとしても、DB が拒否する
            conn.execute("SET default_transaction_read_only = on")
            result = evaluate(
                conn,
                month_ranges(args.from_month, args.to_month),
                args.variant_keys,
                args.horizon_list,
                logger,
                args.train_years_list,
            )
    except Exception:
        logger.exception("失敗しました")
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    # Excel で開けるように、BOM 付きの UTF-8 で書く
    result.to_csv(path, index=False, encoding="utf-8-sig", float_format="%.6f")
    log_summary(result, logger)
    logger.info("処理を終了します(結果: %s、%.0f分)", path, (time.monotonic() - started) / 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
