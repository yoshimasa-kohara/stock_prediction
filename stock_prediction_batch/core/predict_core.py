"""予測の本体。

学習済みのモデルを使い、対象の全銘柄について、予測日数ごとに上昇確率、予測株価、市場比の確率を計算して
prediction に登録する。
主となる日数(MAIN_HORIZON)の予測から、買い候補(上位 BUY_CANDIDATE_COUNT 銘柄)の順位を付ける。
あわせて、同じ33業種を SECTOR_CAP_PER_SECTOR 銘柄までにした買い候補(業種分散。上位 BUY_CANDIDATE_SECTOR_COUNT 銘柄)の
順位と、予測した時点の33業種コードも登録する。
それぞれ、条件を満たす銘柄が件数に足りない日は、上昇確率の条件をその日の上位の割合に置き換えて
選び直す。
同じ日付で再実行すると、その日の予測を削除して作り直す。
"""

import math
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import lightgbm as lgb
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
from core.calibration import Calibration, calibration_file_name, load_calibration
from core.features import COL_CLOSE
from core.train_core import load_booster

LABEL: str = const.PROCESS_LABELS[const.PROCESS_PREDICT]


@dataclass(frozen=True)
class LoadedModels:
    """予測に使うモデル。"""

    version: str
    boosters: dict[tuple[int, str], lgb.Booster]
    feature_names: list[str]
    # 予測日数 → 上昇確率の補正の対応表。ない場合(補正の仕組みを入れる前の古いモデル)は None
    calibrations: dict[int, Calibration | None]


@dataclass(frozen=True)
class PredictResult:
    """予測の結果。"""

    model_version: str
    code_count: int
    candidate_count: int
    candidate_sector_count: int  # 買い候補(業種分散)の銘柄数


def load_models(conn: psycopg.Connection[DictRow], target_date: date) -> LoadedModels:
    """学習の基準日が指定日以前で最も新しいモデルバージョンを選び、モデルを読み込む。"""
    error = f"使えるモデルがありません(指定日: {target_date.isoformat()})"
    row = conn.execute(
        "SELECT model_version FROM model WHERE base_date <= %s ORDER BY base_date DESC, model_version DESC LIMIT 1",
        (target_date,),
    ).fetchone()
    if row is None:
        raise BatchError(LABEL, error)
    version = str(row["model_version"])
    rows = conn.execute(
        "SELECT horizon, model_type, feature_names, file_path FROM model WHERE model_version = %s", (version,)
    ).fetchall()
    by_key = {(int(r["horizon"]), str(r["model_type"])): r for r in rows}
    boosters: dict[tuple[int, str], lgb.Booster] = {}
    calibrations: dict[int, Calibration | None] = {}
    names: list[str] = []
    for horizon in const.PREDICT_HORIZONS:
        for model_type in const.MODEL_TYPES:
            r = by_key.get((horizon, model_type))
            if r is None:
                raise BatchError(LABEL, error)
            path = resolve_path(const.MODEL_DIR) / str(r["file_path"])
            if not path.is_file():
                raise BatchError(LABEL, error)
            boosters[(horizon, model_type)] = load_booster(path)
            if model_type == const.MODEL_TYPE_UP_PROB:
                calibrations[horizon] = load_calibration(path.parent / calibration_file_name(path.name))
            names = list(r["feature_names"])
    return LoadedModels(version=version, boosters=boosters, feature_names=names, calibrations=calibrations)


def target_codes(conn: psycopg.Connection[DictRow], target_date: date) -> list[str]:
    """予測する銘柄。指定日に終値があり、対象の市場で、終値がある営業日が MIN_HISTORY_DAYS 以上の銘柄。"""
    rows = conn.execute(
        """
        SELECT p.code
        FROM daily_price p
        JOIN stock_master m ON m.code = p.code
        WHERE p.trade_date = %s AND p.close IS NOT NULL AND m.market_code = ANY(%s)
          AND (SELECT count(*) FROM daily_price h
               WHERE h.code = p.code AND h.trade_date <= %s AND h.close IS NOT NULL) >= %s
        ORDER BY p.code
        """,
        (target_date, const.TARGET_MARKET_CODES, target_date, const.MIN_HISTORY_DAYS),
    ).fetchall()
    return [str(r["code"]) for r in rows]


def _sorted_pool(main: pd.DataFrame, up_threshold: float | None = None) -> pd.DataFrame:
    """RANKING_METHOD の条件に当てはまる銘柄を、買い候補の順(上から)に並べて返す。

    up_threshold は上昇確率の足切り。省略すると UP_PROB_THRESHOLD。方式Cは RANKING_MIN_UP_PROB を使うので関係しない。
    """
    threshold = const.UP_PROB_THRESHOLD if up_threshold is None else up_threshold
    if const.RANKING_METHOD == "R":
        # 上昇確率が50%以上、かつ見込み(予測株価 ÷ 予測時の株価 − 1)がプラスの中で、
        # 市場比の確率が高い順
        pool = main.loc[(main["up_prob"] >= threshold) & (main["pred_price"] > main["base_price"])].copy()
        pool["_key"] = pool["rel_prob"]
    elif const.RANKING_METHOD == "A":
        pool = main.loc[main["up_prob"] >= threshold].copy()
        pool["_key"] = pool["up_prob"]
    elif const.RANKING_METHOD == "C":
        pool = main.loc[main["up_prob"] >= const.RANKING_MIN_UP_PROB].copy()
        pool["_key"] = pool["pred_price"] / pool["base_price"]
    else:
        raise BatchError(LABEL, f"RANKING_METHOD の値が正しくありません({const.RANKING_METHOD})")
    # 同じ値で並んだ場合は、銘柄コードの小さい順
    return pool.sort_values(["_key", "code"], ascending=[False, True])


def _ranks(pool: pd.DataFrame) -> pd.Series:
    return pd.Series(range(1, len(pool) + 1), index=pool["code"].to_numpy())


def _fallback_threshold(main: pd.DataFrame) -> float:
    """買い候補が足りない日の足切り。

    その日の全銘柄の上昇確率の上位 BUY_CANDIDATE_FALLBACK_TOP_RATE に入る一番低い値(同じ値の銘柄は全て入る)。
    その値が UP_PROB_THRESHOLD より高い日は、置き換えると対象が狭くなるだけなので UP_PROB_THRESHOLD のままにする。
    """
    top_count = max(1, math.ceil(len(main) * const.BUY_CANDIDATE_FALLBACK_TOP_RATE))
    return min(const.UP_PROB_THRESHOLD, float(main["up_prob"].nlargest(top_count).min()))


def _cap_sector(pool: pd.DataFrame) -> pd.DataFrame:
    """同じ33業種を上から SECTOR_CAP_PER_SECTOR 銘柄までにする。33業種がない銘柄は、銘柄ごとに別の業種として扱う。"""
    sector = pool["sector33_code"].where(pool["sector33_code"].notna(), "NULL:" + pool["code"].astype(str))
    return pool.loc[pool.groupby(sector, sort=False).cumcount() < const.SECTOR_CAP_PER_SECTOR]


def rank_candidates(main: pd.DataFrame) -> pd.Series:
    """主となる日数の予測から、買い候補の順位を付ける。

    条件を満たす銘柄が BUY_CANDIDATE_COUNT に足りない日は、上昇確率の足切りを _fallback_threshold にして選び直す。

    Args:
        main: 列 code、up_prob、rel_prob、pred_price、base_price

    Returns:
        銘柄コード → 順位(1〜BUY_CANDIDATE_COUNT)。上位に入らなかった銘柄は含まない
    """
    picked = _sorted_pool(main).head(const.BUY_CANDIDATE_COUNT)
    if len(picked) < const.BUY_CANDIDATE_COUNT:
        picked = _sorted_pool(main, _fallback_threshold(main)).head(const.BUY_CANDIDATE_COUNT)
    return _ranks(picked)


def rank_candidates_sector(main: pd.DataFrame) -> pd.Series:
    """主となる日数の予測から、買い候補(業種分散)の順位を付ける。

    買い候補と同じ条件・同じ並べ方で並べ、同じ33業種は上から SECTOR_CAP_PER_SECTOR 銘柄までを残し、
    上位 BUY_CANDIDATE_SECTOR_COUNT 銘柄を選ぶ。33業種がない銘柄は、銘柄ごとに別の業種として扱う(上限がかからない)。
    件数に足りない日は、上昇確率の足切りを _fallback_threshold にして選び直す。

    Args:
        main: 列 code、up_prob、rel_prob、pred_price、base_price、sector33_code(ない銘柄は None)

    Returns:
        銘柄コード → 順位(1〜BUY_CANDIDATE_SECTOR_COUNT)。選ばれなかった銘柄は含まない
    """
    picked = _cap_sector(_sorted_pool(main)).head(const.BUY_CANDIDATE_SECTOR_COUNT)
    if len(picked) < const.BUY_CANDIDATE_SECTOR_COUNT:
        picked = _cap_sector(_sorted_pool(main, _fallback_threshold(main))).head(const.BUY_CANDIDATE_SECTOR_COUNT)
    return _ranks(picked)


def sectors_of(conn: psycopg.Connection[DictRow], codes: list[str]) -> dict[str, str | None]:
    """銘柄コード → 銘柄マスタの33業種コード(予測した時点の値。ない銘柄は None)。"""
    rows = conn.execute("SELECT code, sector33_code FROM stock_master WHERE code = ANY(%s)", (codes,)).fetchall()
    found = {str(r["code"]): (None if r["sector33_code"] is None else str(r["sector33_code"])) for r in rows}
    return {c: found.get(c) for c in codes}


def _decimal(value: object, places: str) -> Decimal:
    return Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def predict(conn: psycopg.Connection[DictRow], target_date: date, logger: BatchLogger) -> PredictResult:
    """予測の手順2〜9を行う。"""
    models = load_models(conn, target_date)
    logger.info("使うモデル: %s", models.version)
    missing = [h for h in const.PREDICT_HORIZONS if models.calibrations.get(h) is None]
    if missing:
        logger.warning(
            "上昇確率の補正の対応表がないため、補正しません(%s営業日後)。補正の仕組みを入れる前のモデルです",
            "・".join(map(str, missing)),
        )
    codes = target_codes(conn, target_date)
    if not codes:
        raise BatchError(LABEL, f"予測する銘柄がありません(指定日: {target_date.isoformat()})")
    logger.info("予測する銘柄: %s銘柄", f"{len(codes):,}")

    # 手順4: 特徴量(指定日の1日分)
    try:
        feats = features.build_features(conn, target_date, target_date, codes)
    except Exception as e:
        raise BatchError(LABEL, f"特徴量を計算できませんでした: {e}", const.ALL_CODES_LABEL) from e
    feats = feats.loc[feats[COL_CLOSE].notna()].reset_index(drop=True)
    if feats.empty:
        raise BatchError(LABEL, f"予測する銘柄がありません(指定日: {target_date.isoformat()})")
    x = feats[models.feature_names]

    # 手順5、手順6: 予測日数ごとに予測し、対象日を求める
    results: dict[int, pd.DataFrame] = {}
    for horizon in const.PREDICT_HORIZONS:
        try:
            up_raw = models.boosters[(horizon, const.MODEL_TYPE_UP_PROB)].predict(x)
            rate = models.boosters[(horizon, const.MODEL_TYPE_PRICE)].predict(x)
            rel = models.boosters[(horizon, const.MODEL_TYPE_REL_PROB)].predict(x)
        except Exception as e:
            raise BatchError(LABEL, f"予測できませんでした: {e}", const.ALL_CODES_LABEL) from e
        # 上昇確率を補正する。補正前の値は DEBUG のログに出す
        calibration = models.calibrations.get(horizon)
        up = up_raw if calibration is None else calibration.apply(up_raw)
        frame = pd.DataFrame(
            {
                "code": feats["code"],
                "base_price": feats[COL_CLOSE].astype(float),
                "up_prob": pd.Series(up).clip(0, 1).round(4),
                "up_raw": pd.Series(up_raw).clip(0, 1).round(4),
                "rel_prob": pd.Series(rel).clip(0, 1).round(4),
                "pred_price": feats[COL_CLOSE].astype(float) * (1 + pd.Series(rate)),
            }
        )
        results[horizon] = frame
        for r in frame.itertuples():
            logger.debug(
                "%d営業日後 上昇確率 %.4f(補正前 %.4f) 市場比 %.4f 予測株価 %.1f",
                horizon, r.up_prob, r.up_raw, r.rel_prob, r.pred_price, extra={"code": r.code},
            )  # fmt: skip
    target_dates = {h: business_day.business_days_after(target_date, h, conn) for h in const.PREDICT_HORIZONS}

    # 手順7: 買い候補と買い候補(業種分散)の順位。業種は予測した時点の銘柄マスタの値
    sectors = sectors_of(conn, [str(c) for c in feats["code"]])
    main = results[const.MAIN_HORIZON].assign(sector33_code=lambda f: f["code"].map(sectors))
    ranks = rank_candidates(main)
    ranks_sector = rank_candidates_sector(main)
    up_by_code = main.set_index("code")["up_prob"]
    for title, picked in (("買い候補", ranks), ("買い候補(業種分散)", ranks_sector)):
        if (up_by_code.loc[picked.index] < const.UP_PROB_THRESHOLD).any():
            logger.info(
                "%s: 条件を満たす銘柄が足りないため、上昇確率の上位%g%%で選びました",
                title, const.BUY_CANDIDATE_FALLBACK_TOP_RATE * 100,
            )  # fmt: skip

    # 手順8: 登録する
    rows = []
    for horizon, frame in results.items():
        for r in frame.itertuples():
            rank = ranks.get(r.code)
            rank_sector = ranks_sector.get(r.code)
            rows.append(
                (
                    r.code, target_date, horizon, target_dates[horizon],
                    _decimal(r.up_prob, "0.0001"), _decimal(r.rel_prob, "0.0001"),
                    _decimal(r.pred_price, "0.0001"), _decimal(r.base_price, "0.0001"),
                    models.version, sectors.get(str(r.code)), None if rank is None else int(rank),
                    None if rank_sector is None else int(rank_sector), const.VERIFY_STATUS_PENDING,
                )
            )  # fmt: skip
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM prediction WHERE pred_date = %s", (target_date,))
        cur.execute("DELETE FROM verify_daily_summary WHERE pred_date = %s", (target_date,))
        with cur.copy(
            "COPY prediction (code, pred_date, horizon, target_date, up_prob, rel_prob, pred_price, base_price,"
            " model_version, sector33_code, buy_rank, buy_rank_sector, verify_status) FROM STDIN"
        ) as copy:
            for row in rows:
                copy.write_row(row)
    logger.info(
        "予測を登録しました(%s件 = %s銘柄 × %d期間)", f"{len(rows):,}", f"{len(feats):,}", len(const.PREDICT_HORIZONS)
    )
    return PredictResult(
        model_version=models.version,
        code_count=len(feats),
        candidate_count=len(ranks),
        candidate_sector_count=len(ranks_sector),
    )


def run_predict(target_date: date, batch_type: str = const.BATCH_TYPE_SINGLE) -> PredictResult:
    """予測を実行する。実行履歴とログの開始・終了も行う。

    Raises:
        BatchError: 失敗した場合
    """
    logger = get_logger(LABEL, target_date)

    def body() -> ProcessOutcome[PredictResult]:
        logger.info("処理を開始します")
        with db.connect() as conn:
            result = predict(conn, target_date, logger)
        logger.info(
            "処理を終了します(買い候補: %d銘柄、買い候補(業種分散): %d銘柄)",
            result.candidate_count, result.candidate_sector_count,
        )  # fmt: skip
        return ProcessOutcome(result=result, status=const.RUN_STATUS_SUCCESS, record_count=result.code_count)

    return run_with_history(batch_type, const.PROCESS_PREDICT, target_date, logger, body)
