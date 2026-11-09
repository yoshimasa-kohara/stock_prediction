"""学習の本体。

予測日数(PREDICT_HORIZONS)ごとに、上昇確率用(UP_PROB)、予測株価用(PRICE)、市場比用(REL_PROB)の
3種類のモデルを LightGBM で作る。
1つのモデルにつき2回学習する。
  (1) 学習用の行だけで学習し、確認用の行(直近 VALID_PERIOD_MONTHS か月)で成績を測る。保存しない。
      学習用は、正解の日(N営業日後)が確認用の初日より前の行だけにする(隙間をあける)。
  (2) 全ての行で、(1)で止まった木の本数のまま学習し直す。これを保存して予測に使う。
上昇確率用は(1)の確認用の期間の答え合わせを貯めて、直近1年分で補正の対応表を作る。
モデルは model_to_string で文字列にし、Python で UTF-8 のファイルに書く
(日本語のファイル名に対応するため)。
"""

import json
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
import psycopg
from psycopg.rows import DictRow

import const
from common import business_day, db
from common.env import resolve_path
from common.errors import BatchError
from common.logger import BatchLogger, get_logger
from common.run_history import ProcessOutcome, run_with_history
from core import features
from core.calibration import (
    COL_ACTUAL,
    COL_CODE,
    COL_DATE,
    COL_HORIZON,
    COL_RAW_PROB,
    Calibration,
    calibration_data_path,
    calibration_file_name,
    fit_calibration,
    load_calibration_data,
    merge_calibration_data,
    save_calibration,
    save_calibration_data,
)
from core.features import CATEGORICAL_FEATURES, COL_ADJ_CLOSE, COL_HIST_DAYS

LABEL: str = const.PROCESS_LABELS[const.PROCESS_TRAIN]

# モデルの種類ごとの、ファイル名の先頭
MODEL_FILE_PREFIX: dict[str, str] = {
    const.MODEL_TYPE_UP_PROB: "上昇確率",
    const.MODEL_TYPE_PRICE: "予測株価",
    const.MODEL_TYPE_REL_PROB: "市場比",
}
# モデルの種類ごとの、LightGBM の学習の種類
MODEL_OBJECTIVE: dict[str, str] = {
    const.MODEL_TYPE_UP_PROB: "binary",
    const.MODEL_TYPE_PRICE: "regression",
    const.MODEL_TYPE_REL_PROB: "binary",
}
# モデルの種類ごとの、学習を止めるかを判断する成績
MODEL_METRIC: dict[str, str] = {
    const.MODEL_TYPE_UP_PROB: "auc",
    const.MODEL_TYPE_PRICE: "l2",
    const.MODEL_TYPE_REL_PROB: "auc",
}
# モデルの種類ごとの、ログに出す名前
MODEL_KIND_LABELS: dict[str, str] = {
    const.MODEL_TYPE_UP_PROB: "上昇確率用",
    const.MODEL_TYPE_PRICE: "予測株価用",
    const.MODEL_TYPE_REL_PROB: "市場比用",
}
# 正解の列
COL_TARGET_UP: str = "_target_up"
COL_TARGET_RATE: str = "_target_rate"
COL_TARGET_RATE_CLIPPED: str = "_target_rate_clipped"
COL_TARGET_REL: str = "_target_rel"  # 市場比: 同じ日の騰落率の中央値より高ければ 1
COL_TARGET_DATE: str = "_target_date"  # 正解の日(N営業日後の日付)
# モデルの種類ごとの、正解の列
MODEL_LABEL_COLUMN: dict[str, str] = {
    const.MODEL_TYPE_UP_PROB: COL_TARGET_UP,
    const.MODEL_TYPE_PRICE: COL_TARGET_RATE_CLIPPED,
    const.MODEL_TYPE_REL_PROB: COL_TARGET_REL,
}


@dataclass(frozen=True)
class TrainedModel:
    """学習したモデル1つ分(model テーブルの1行)。"""

    horizon: int
    model_type: str
    booster: lgb.Booster
    train_start_date: date
    train_end_date: date
    train_rows: int
    valid_score: float | None
    # 上昇確率の補正の対応表(上昇確率用のモデルだけ)
    calibration: Calibration | None = None
    # ファイルに残す補正用データ(上昇確率用のモデルだけ)
    calibration_data: pd.DataFrame | None = None

    @property
    def file_name(self) -> str:
        return model_file_name(self.model_type, self.horizon)


def model_file_name(model_type: str, horizon: int) -> str:
    """モデルのファイル名(例: 上昇確率_5日後.txt)。"""
    return f"{MODEL_FILE_PREFIX[model_type]}_{horizon}日後.txt"


def save_booster(booster: lgb.Booster, path: Path) -> None:
    """モデルを文字列にして、UTF-8 のテキストファイルに書く。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(booster.model_to_string(), encoding="utf-8")


def load_booster(path: Path) -> lgb.Booster:
    """UTF-8 のテキストファイルを読み、モデルに戻す。"""
    return lgb.Booster(model_str=path.read_text(encoding="utf-8"))


# =====================================================================
# 学習データ
# =====================================================================


def target_codes(conn: psycopg.Connection[DictRow]) -> list[str]:
    """学習する銘柄。銘柄マスタの市場区分コードが TARGET_MARKET_CODES に含まれる銘柄。"""
    rows = conn.execute(
        "SELECT code FROM stock_master WHERE market_code = ANY(%s) ORDER BY code", (const.TARGET_MARKET_CODES,)
    ).fetchall()
    return [str(r["code"]) for r in rows]


def add_targets(df: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """各行に、N営業日後の終値(調整済み)から作る正解を足し、学習に使える行だけを返す。

    N営業日後は、株価がある日の並びで数える。N営業日後が指定日より後になる行と、その日か N営業日後の終値がない行、
    上場直後(終値がある営業日が MIN_HISTORY_DAYS 未満)の行は除く。
    市場比の正解(COL_TARGET_REL)は、残った行の騰落率の中央値を日ごとに求め、それより高ければ 1 にする。
    """
    days = np.sort(df["trade_date"].unique())
    position = np.searchsorted(days, df["trade_date"].to_numpy())
    future_pos = position + horizon
    valid = future_pos < len(days)
    target_date = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    target_date[valid] = days[future_pos[valid]]
    work = df.assign(_target_date=target_date)
    future = df[["code", "trade_date", COL_ADJ_CLOSE]].rename(
        columns={"trade_date": "_target_date", COL_ADJ_CLOSE: "_future_adj_close"}
    )
    work = work.merge(future, on=["code", "_target_date"], how="left")
    use = (
        work["_target_date"].notna()
        & work[COL_ADJ_CLOSE].notna()
        & work["_future_adj_close"].notna()
        & (work[COL_HIST_DAYS] >= const.MIN_HISTORY_DAYS)
    )
    work = work.loc[use].copy()
    work[COL_TARGET_RATE] = work["_future_adj_close"] / work[COL_ADJ_CLOSE] - 1
    work[COL_TARGET_UP] = (work["_future_adj_close"] > work[COL_ADJ_CLOSE]).astype(int)
    median = work.groupby("trade_date")[COL_TARGET_RATE].transform("median")
    work[COL_TARGET_REL] = (work[COL_TARGET_RATE] > median).astype(int)
    return work.reset_index(drop=True)


def clip_targets(rates: pd.Series, logger: BatchLogger, horizon: int) -> pd.Series:
    """予測株価用の正解(騰落率)の、上下 PRICE_TARGET_CLIP_RATE に入る極端な値を、境目の値に置き換える。"""
    lower = float(rates.quantile(const.PRICE_TARGET_CLIP_RATE))
    upper = float(rates.quantile(1 - const.PRICE_TARGET_CLIP_RATE))
    logger.info("%d営業日後 予測株価用の正解の境目: 下 %.4f / 上 %.4f", horizon, lower, upper)
    return rates.clip(lower=lower, upper=upper)


# =====================================================================
# 学習
# =====================================================================


def _params(model_type: str) -> dict[str, Any]:
    """LightGBM の設定値(const.LGBM_PARAMS + 学習の種類)。"""
    params: dict[str, Any] = dict(const.LGBM_PARAMS)
    params["objective"] = MODEL_OBJECTIVE[model_type]
    params["metric"] = MODEL_METRIC[model_type]
    params["verbose"] = -1
    # deterministic を true にした場合に、公式ドキュメントで指定が勧められている設定
    params["force_col_wise"] = True
    return params


def _dataset(x: pd.DataFrame, y: pd.Series) -> lgb.Dataset:
    return lgb.Dataset(
        x, label=y, categorical_feature=CATEGORICAL_FEATURES, free_raw_data=False, params={"verbose": -1}
    )


def valid_score(model_type: str, predicted: np.ndarray, valid: pd.DataFrame) -> float:
    """確認用の成績(手順7の表)。

    上昇確率用は的中率、市場比用は「市場の中央値を上回るか」の的中率、予測株価用は予測株価の平均誤差率。
    """
    if model_type == const.MODEL_TYPE_UP_PROB:
        predicted_up = predicted >= const.UP_PROB_THRESHOLD
        actual_up = valid[COL_TARGET_UP].to_numpy() == 1
        return float(np.mean(predicted_up == actual_up))
    if model_type == const.MODEL_TYPE_REL_PROB:
        predicted_beat = predicted >= const.REL_PROB_THRESHOLD
        actual_beat = valid[COL_TARGET_REL].to_numpy() == 1
        return float(np.mean(predicted_beat == actual_beat))
    # 予測株価 = 株価 ×(1 + 予測した騰落率)、実株価 = 株価 ×(1 + 実際の騰落率)。株価は割り算で消える
    actual = valid[COL_TARGET_RATE].to_numpy()
    return float(np.mean(np.abs(predicted - actual) / (1 + actual)))


def split_train_valid(data: pd.DataFrame, valid_start: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    """学習用と確認用に分ける。

    確認用は、日付が確認用の初日(valid_start)以降の行。学習用は、正解の日(N営業日後)が確認用の初日より前の行だけにし、
    学習用の正解が確認用の期間の値動きを含まないようにする(隙間をあける)。
    """
    start = pd.Timestamp(valid_start)
    return data.loc[data[COL_TARGET_DATE] < start], data.loc[data["trade_date"] >= start]


def build_calibration(
    predicted: np.ndarray, valid_part: pd.DataFrame, horizon: int, logger: BatchLogger
) -> tuple[Calibration, pd.DataFrame]:
    """貯めた補正用データと今回の確認用の期間の行から、補正の対応表を作る。

    Returns:
        (対応表, ファイルに残す補正用データ)。ファイルへの保存は、全てのモデルの学習が終わってから行う
    """
    file_name = model_file_name(const.MODEL_TYPE_UP_PROB, horizon)
    current = pd.DataFrame(
        {
            COL_HORIZON: horizon,
            COL_CODE: valid_part["code"].astype(str).to_numpy(),
            COL_DATE: valid_part["trade_date"].to_numpy(),
            COL_RAW_PROB: predicted,
            COL_ACTUAL: valid_part[COL_TARGET_UP].to_numpy(),
        }
    )
    kept, used = merge_calibration_data(load_calibration_data(calibration_data_path(file_name)), current)
    calibration = fit_calibration(used[COL_RAW_PROB].to_numpy(), used[COL_ACTUAL].to_numpy())
    kind = MODEL_KIND_LABELS[const.MODEL_TYPE_UP_PROB]
    logger.info(
        "%d営業日後 %s 補正の対応表を作りました(モデルの確率 %.0f%% → %.0f%%、%.0f%% → %.0f%%)",
        horizon, kind, 50, float(calibration.apply(np.array([0.5]))[0]) * 100,
        75, float(calibration.apply(np.array([0.75]))[0]) * 100,
    )  # fmt: skip
    logger.info(
        "%d営業日後 %s 補正に使った行: %s行、期間 %s 〜 %s、基準率 %.4f、補正後の最大 %.4f、最小 %.4f",
        horizon, kind, f"{len(used):,}", used[COL_DATE].min().date(), used[COL_DATE].max().date(),
        float(used[COL_ACTUAL].mean()), max(calibration.y), min(calibration.y),
    )  # fmt: skip
    return calibration, kept


def train_model(
    data: pd.DataFrame, horizon: int, model_type: str, valid_start: date, logger: BatchLogger
) -> TrainedModel:
    """1つのモデルを、(1) 学習用だけで学習して成績を測り、(2) 全ての行で学習し直す。"""
    names = features.feature_names()
    label_col = MODEL_LABEL_COLUMN[model_type]
    train_part, valid_part = split_train_valid(data, valid_start)
    kind = MODEL_KIND_LABELS[model_type]
    if train_part.empty or valid_part.empty:
        raise BatchError(LABEL, f"学習用または確認用のデータがありません(期間: {horizon}営業日後、{kind})")

    params = _params(model_type)
    # (1) 学習用の行だけで学習し、確認用の行で成績を測る
    first = lgb.train(
        params,
        _dataset(train_part[names], train_part[label_col]),
        num_boost_round=const.LGBM_MAX_ROUNDS,
        valid_sets=[_dataset(valid_part[names], valid_part[label_col])],
        callbacks=[lgb.early_stopping(const.LGBM_EARLY_STOPPING_ROUNDS, verbose=False)],
    )
    rounds = first.best_iteration if first.best_iteration > 0 else first.current_iteration()
    predicted = np.asarray(first.predict(valid_part[names], num_iteration=rounds), dtype=float)
    score = valid_score(model_type, predicted, valid_part)
    score_name = "平均誤差率" if model_type == const.MODEL_TYPE_PRICE else "的中率"
    logger.info(
        "%d営業日後 %s 確認用の%s %.4f(学習用 %s行で学習、木 %d本)",
        horizon, kind, score_name, score, f"{len(train_part):,}", rounds,
    )  # fmt: skip

    # 上昇確率用のモデルは、確認用の期間の答え合わせを貯めて、補正の対応表を作る
    calibration = None
    calibration_data = None
    if model_type == const.MODEL_TYPE_UP_PROB:
        calibration, calibration_data = build_calibration(predicted, valid_part, horizon, logger)

    # (2) 全ての行で、同じ設定・同じ本数で学習し直す
    final = lgb.train(params, _dataset(data[names], data[label_col]), num_boost_round=rounds)
    logger.info("%d営業日後 %s 全ての行で学習し直しました(%s行)", horizon, kind, f"{len(data):,}")
    return TrainedModel(
        horizon=horizon,
        model_type=model_type,
        booster=final,
        train_start_date=data["trade_date"].min().date(),
        train_end_date=data["trade_date"].max().date(),
        train_rows=len(data),
        valid_score=score,
        calibration=calibration,
        calibration_data=calibration_data,
    )


# =====================================================================
# 保存
# =====================================================================


def save_models(conn: psycopg.Connection[DictRow], version: str, base_date: date, models: list[TrainedModel]) -> Path:
    """モデルのファイルを保存し、model テーブルに登録する。同じモデルバージョンは削除して作り直す。

    Returns:
        モデルバージョンのフォルダ
    """
    folder = resolve_path(const.MODEL_DIR) / version
    # (1) 同じモデルバージョンのフォルダを削除する(行の削除は、(3)と同じトランザクションで行う)
    if folder.exists():
        shutil.rmtree(folder)
    # (2) ファイルを保存する
    try:
        for m in models:
            save_booster(m.booster, folder / m.file_name)
            if m.calibration is not None:
                save_calibration(m.calibration, folder / calibration_file_name(m.file_name))
        # モデルを全て書けてから、補正用データを置き換える
        for m in models:
            if m.calibration_data is not None:
                save_calibration_data(m.calibration_data, calibration_data_path(m.file_name))
    except Exception as e:
        shutil.rmtree(folder, ignore_errors=True)
        raise BatchError(LABEL, f"モデルのファイルを保存できませんでした: {e}") from e
    # (3) model テーブルに登録する
    names_json = json.dumps(features.feature_names(), ensure_ascii=False)
    try:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute("DELETE FROM model WHERE model_version = %s", (version,))
            cur.executemany(
                """
                INSERT INTO model (model_version, horizon, model_type, base_date, train_start_date, train_end_date,
                    train_rows, feature_names, valid_score, file_path, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                """,
                [
                    (version, m.horizon, m.model_type, base_date, m.train_start_date, m.train_end_date,
                     m.train_rows, names_json, m.valid_score, f"{version}/{m.file_name}")
                    for m in models
                ],
            )  # fmt: skip
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return folder


# =====================================================================
# 全体の流れ
# =====================================================================


def train(conn: psycopg.Connection[DictRow], base_date: date, logger: BatchLogger) -> list[TrainedModel]:
    """学習の手順2〜9を行う。"""
    # 手順2: 学習に使う期間とモデルバージョン
    start = business_day.years_before(base_date, const.TRAIN_PERIOD_YEARS)
    version = base_date.strftime("%Y%m%d")
    logger.info("学習の期間: %s 〜 %s", start, base_date)

    # 手順3、手順4: 学習する銘柄と、特徴量
    codes = target_codes(conn)
    data = features.build_features(conn, base_date, start, codes)
    logger.info("特徴量を計算しました(%s行)", f"{len(data):,}", extra={"code": const.ALL_CODES_LABEL})

    valid_start = business_day.months_before(base_date, const.VALID_PERIOD_MONTHS)
    models: list[TrainedModel] = []
    for horizon in const.PREDICT_HORIZONS:
        # 手順5: 正解を作る
        rows = add_targets(data, horizon) if not data.empty else data
        if rows.empty:
            raise BatchError(LABEL, f"学習に使えるデータがありません(期間: {horizon}営業日後)")
        rows[COL_TARGET_RATE_CLIPPED] = clip_targets(rows[COL_TARGET_RATE], logger, horizon)
        # 手順6、手順7: 学習用と確認用に分けて、3種類のモデルを学習する
        for model_type in const.MODEL_TYPES:
            models.append(train_model(rows, horizon, model_type, valid_start, logger))

    # 手順8: 保存する
    folder = save_models(conn, version, base_date, models)
    logger.info("モデルを保存しました(%s)", folder)
    return models


def run_train(base_date: date, batch_type: str = const.BATCH_TYPE_SINGLE) -> list[TrainedModel]:
    """学習を実行する。実行履歴とログの開始・終了も行う。

    Raises:
        BatchError: 失敗した場合
    """
    logger = get_logger(LABEL, base_date)
    version = base_date.strftime("%Y%m%d")

    def body() -> ProcessOutcome[list[TrainedModel]]:
        logger.info("処理を開始します(モデルバージョン: %s)", version)
        try:
            with db.connect() as conn:
                models = train(conn, base_date, logger)
        except BatchError as e:
            # 全銘柄をまとめて処理するので、銘柄コードは「全銘柄の一括処理」とする
            raise BatchError(LABEL, e.message, e.code or const.ALL_CODES_LABEL) from e
        except Exception as e:
            raise BatchError(LABEL, str(e), const.ALL_CODES_LABEL) from e
        logger.info("処理を終了します(モデル %d個を保存)", len(models))
        return ProcessOutcome(result=models, status=const.RUN_STATUS_SUCCESS, record_count=len(models))

    return run_with_history(batch_type, const.PROCESS_TRAIN, base_date, logger, body)
