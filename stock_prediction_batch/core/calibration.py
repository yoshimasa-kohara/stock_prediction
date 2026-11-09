"""上昇確率の補正。

モデルが出した上昇確率を、実際に上がった割合に読み替える「対応表」を、アイソトニック回帰で作る。
アイソトニック回帰は「モデルの確率が高いほど、補正後の確率も高い(順番は逆にならない)」という決まりを守りながら、
実際に上がった割合に一番近くなるように値を決める方法。計算は PAV(隣り合う組を平均してまとめる方法)で行う。

対応表は、モデルの確率の点(x)と補正後の確率(y)の組で、点の間は直線でつなぐ。範囲の外は端の値にする。

対応表は、学習のたびに貯めた確認用の期間の答え合わせ(補正用データ)の、直近 CALIBRATION_HISTORY_WEEKS 週分で作る。
補正用データは予測日数ごとに1つの gzip の CSV で、MODEL_DIR の下の CALIBRATION_DATA_DIR に置く。
"""

import json
import os
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

import const
from common.env import resolve_path

# 補正用データの列: 予測日数、銘柄コード、日付、補正前の確率、実際に上がったか(1 か 0)
COL_HORIZON: str = "horizon"
COL_CODE: str = "code"
COL_DATE: str = "trade_date"
COL_RAW_PROB: str = "raw_prob"
COL_ACTUAL: str = "actual_up"
CALIBRATION_DATA_COLUMNS: list[str] = [COL_HORIZON, COL_CODE, COL_DATE, COL_RAW_PROB, COL_ACTUAL]


@dataclass(frozen=True)
class Calibration:
    """対応表。x はモデルの確率(小さい順)、y は補正後の確率。"""

    x: list[float]
    y: list[float]

    def apply(self, values: object) -> np.ndarray:
        """モデルの確率を補正する。点の間は直線でつなぎ、範囲の外は端の値にする。"""
        result: np.ndarray = np.interp(np.asarray(values, dtype=float), self.x, self.y)
        return result

    def to_json(self) -> str:
        return json.dumps({"x": self.x, "y": self.y}, ensure_ascii=False)

    @staticmethod
    def from_json(text: str) -> "Calibration":
        data = json.loads(text)
        return Calibration(x=[float(v) for v in data["x"]], y=[float(v) for v in data["y"]])


def _pav(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """重み付きの PAV。values を、小さい順に並んだ(減らない)値に直す。"""
    blocks: list[list[float]] = []  # [合計(値 × 重み), 重み, まとめた数]
    for v, w in zip(values.tolist(), weights.tolist(), strict=True):
        blocks.append([v * w, w, 1.0])
        # 前の組の平均の方が大きければ、まとめて平均する
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            s, wt, n = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += wt
            blocks[-1][2] += n
    return np.concatenate([np.full(int(n), s / wt) for s, wt, n in blocks])


def _merge_edge_groups(groups: list[np.ndarray], min_count: int) -> list[np.ndarray]:
    """確率の順に並んだ組の、一番下と一番上を、件数が min_count 以上になるまで隣の組とまとめる。

    行が少なくて足りない場合は、例外にせず、1つの組になるまでまとめる。
    """
    merged = list(groups)
    while len(merged) > 1 and len(merged[0]) < min_count:
        merged[:2] = [np.concatenate(merged[:2])]
    while len(merged) > 1 and len(merged[-1]) < min_count:
        merged[-2:] = [np.concatenate(merged[-2:])]
    return merged


def fit_calibration(
    predicted: np.ndarray,
    actual: np.ndarray,
    bins: int = const.CALIBRATION_BIN_COUNT,
    shrink_alpha: float | None = None,
    edge_min_count: int | None = None,
) -> Calibration:
    """モデルの確率(predicted)と、実際に上がったか(actual。1 か 0)から、対応表を作る。

    行が多いので、まずモデルの確率の順に bins 個の組(同じくらいの行数)に分ける。一番上と一番下の組は、
    件数が edge_min_count 以上になるまで隣の組とまとめる。組ごとの上がった割合は、件数が少ないほど全ての行の
    上がった割合(基準率)に近づけ((上がった件数 + α × 基準率) ÷ (件数 + α))、PAV で順番が逆にならないように直す。

    Args:
        shrink_alpha: α。省略すると const.CALIBRATION_SHRINK_ALPHA
        edge_min_count: 端の組の最低の件数。省略すると const.CALIBRATION_EDGE_MIN_COUNT

    Raises:
        ValueError: 行がない場合
    """
    p = np.asarray(predicted, dtype=float)
    a = np.asarray(actual, dtype=float)
    mask = ~(np.isnan(p) | np.isnan(a))
    p, a = p[mask], a[mask]
    if len(p) == 0:
        raise ValueError("補正の対応表を作る行がありません")
    alpha = const.CALIBRATION_SHRINK_ALPHA if shrink_alpha is None else shrink_alpha
    min_count = const.CALIBRATION_EDGE_MIN_COUNT if edge_min_count is None else edge_min_count
    order = np.argsort(p, kind="stable")
    groups = _merge_edge_groups(list(np.array_split(order, min(bins, len(p)))), min_count)
    base_rate = float(a.mean())
    xs = np.array([p[g].mean() for g in groups])
    counts = np.array([len(g) for g in groups], dtype=float)
    rates = np.array([(a[g].sum() + alpha * base_rate) / (len(g) + alpha) for g in groups])
    ys = np.clip(_pav(rates, counts), 0.0, 1.0)
    return Calibration(x=[round(float(v), 6) for v in xs], y=[round(float(v), 6) for v in ys])


def calibration_file_name(model_file: str) -> str:
    """モデルのファイル名から、対応表のファイル名を作る(例: 上昇確率_20日後.txt → 上昇確率_20日後_補正.json)。"""
    return f"{Path(model_file).stem}_補正.json"


def save_calibration(calibration: Calibration, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(calibration.to_json(), encoding="utf-8")


def load_calibration(path: Path) -> Calibration | None:
    """対応表を読む。ファイルがなければ None(補正の仕組みを入れる前の古いモデル)。"""
    if not path.is_file():
        return None
    return Calibration.from_json(path.read_text(encoding="utf-8"))


# =====================================================================
# 補正用データ(確認用の期間の答え合わせを貯めたもの)
# =====================================================================


def calibration_data_path(model_file: str) -> Path:
    """補正用データのファイル(例: 上昇確率_20日後.txt → <MODEL_DIR>/補正用データ/上昇確率_20日後_確認用.csv.gz)。"""
    name = f"{Path(model_file).stem}_確認用.csv.gz"
    return resolve_path(const.MODEL_DIR) / const.CALIBRATION_DATA_DIR / name


def load_calibration_data(path: Path) -> pd.DataFrame:
    """補正用データを読む。ファイルがなければ行のない表を返す(最初の学習)。

    Raises:
        ValueError: ファイルが壊れていて読めない場合(黙って作り直すと貯めた分が消えるので、止める)
    """
    if not path.is_file():
        return pd.DataFrame(
            {
                COL_HORIZON: pd.Series(dtype=int),
                COL_CODE: pd.Series(dtype=str),
                COL_DATE: pd.Series(dtype="datetime64[ns]"),
                COL_RAW_PROB: pd.Series(dtype=float),
                COL_ACTUAL: pd.Series(dtype=int),
            }
        )
    try:
        data = pd.read_csv(path, dtype={COL_CODE: str}, usecols=CALIBRATION_DATA_COLUMNS, parse_dates=[COL_DATE])
    except (OSError, EOFError, ValueError, zlib.error) as e:
        raise ValueError(f"補正用データを読めません({path}): {e}") from e
    return data[CALIBRATION_DATA_COLUMNS]


def save_calibration_data(data: pd.DataFrame, path: Path) -> None:
    """補正用データを書く。途中で失敗しても元のファイルが壊れないように、一時ファイルに書いてから置き換える。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    data[CALIBRATION_DATA_COLUMNS].to_csv(temporary, index=False, compression="gzip", date_format="%Y-%m-%d")
    os.replace(temporary, path)


def merge_calibration_data(stored: pd.DataFrame, current: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """貯めた行(stored)に、今回の確認用の期間の行(current)を足し、残す行と対応表に使う行を返す。

    同じ(銘柄コード, 日付)の行は、current の値で置き換える。
    「確認用の期間の最後の日」から CALIBRATION_HISTORY_WEEKS 週より前の行は残さない。
    対応表には、その最後の日より後の行を使わない(過去の日付で学習し直すと、より新しいモデルの行がファイルに
    残っている。その行は、その日の時点では答えが出ていない。基準日より後の行もここで除かれる)。
    今回の確認用の期間の行は、週数の設定によらず必ず使う。

    Returns:
        (ファイルに残す行, 対応表に使う行)。どちらも日付、銘柄コードの順
    """
    keys = [COL_CODE, COL_DATE]
    valid_end = current[COL_DATE].max()
    limit = valid_end - pd.Timedelta(weeks=const.CALIBRATION_HISTORY_WEEKS)
    joined = stored.merge(current[keys], on=keys, how="left", indicator=True)
    others = joined.loc[(joined["_merge"] == "left_only") & (joined[COL_DATE] > limit), CALIBRATION_DATA_COLUMNS]
    new_rows = current[CALIBRATION_DATA_COLUMNS]
    kept = pd.concat([others, new_rows], ignore_index=True) if not others.empty else new_rows
    earlier = others.loc[others[COL_DATE] <= valid_end]
    used = pd.concat([earlier, new_rows], ignore_index=True) if not earlier.empty else new_rows
    return kept.sort_values(keys[::-1]), used.sort_values(keys[::-1])
