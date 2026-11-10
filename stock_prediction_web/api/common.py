"""API の部品で共通に使う型と関数(予測日数ごとの値、備考、日付など)。"""

from datetime import date
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import DictRow
from pydantic import BaseModel

import const
from api.errors import ApiError
from api.logger import today_jst

# 予測がない日を指定したときのメッセージ
MSG_NO_PREDICTION: str = "この日の予測はありません(土日・祝日、またはバッチが動いていない日)"
# 予測が1件もないとき
MSG_NO_PREDICTION_AT_ALL: str = "予測がまだありません。バッチで予測を実行してください"


class Note(BaseModel):
    """備考の1つ。color は画面の色(red: 赤、blue: 青、orange: 橙、green: 緑)。"""

    text: str
    color: str


class HorizonValue(BaseModel):
    """予測日数ごとの値(市場比の確率、上昇確率、予測株価)。"""

    horizon: int
    rel_prob: float | None
    up_prob: float | None
    pred_price: float | None


def to_float(value: Decimal | float | int | None) -> float | None:
    """DB の数値を JSON の数にする。値がなければ None。"""
    return None if value is None else float(value)


def today() -> date:
    """日本時間の今日(テストで差し替えられるように関数にしている)。"""
    return today_jst()


def latest_pred_date(conn: psycopg.Connection[DictRow]) -> date | None:
    """予測がある最新の予測日。予測がなければ None。"""
    row = conn.execute("SELECT max(pred_date) AS d FROM prediction").fetchone()
    return None if row is None else row["d"]


def resolve_pred_date(conn: psycopg.Connection[DictRow], target: date | None) -> date:
    """指定された予測日を返す。省略されたら最新の予測日。予測がない日なら 404。"""
    if target is None:
        latest = latest_pred_date(conn)
        if latest is None:
            raise ApiError(404, MSG_NO_PREDICTION_AT_ALL)
        return latest
    row = conn.execute("SELECT 1 FROM prediction WHERE pred_date = %s LIMIT 1", (target,)).fetchone()
    if row is None:
        raise ApiError(404, MSG_NO_PREDICTION)
    return target


def horizon_values(by_horizon: dict[int, Any]) -> list[HorizonValue]:
    """予測日数(const.PREDICT_HORIZONS の順)ごとの値。予測がない日数は値を None にする。

    Args:
        by_horizon: 予測日数 → (市場比, 上昇確率, 予測株価) のタプル、または rel_prob・up_prob・pred_price を持つ行
    """
    result: list[HorizonValue] = []
    for h in const.PREDICT_HORIZONS:
        v = by_horizon.get(h)
        if v is None:
            result.append(HorizonValue(horizon=h, rel_prob=None, up_prob=None, pred_price=None))
        elif isinstance(v, tuple):
            result.append(
                HorizonValue(horizon=h, rel_prob=to_float(v[0]), up_prob=to_float(v[1]), pred_price=to_float(v[2]))
            )
        else:
            result.append(
                HorizonValue(
                    horizon=h,
                    rel_prob=to_float(v["rel_prob"]),
                    up_prob=to_float(v["up_prob"]),
                    pred_price=to_float(v["pred_price"]),
                )
            )
    return result


def check_range(date_from: date | None, date_to: date | None) -> None:
    """開始日が終了日より後なら 400。"""
    if date_from and date_to and date_from > date_to:
        raise ApiError(400, "開始日が終了日より後になっています")


def check_horizon(horizon: int) -> None:
    """予測日数が const.PREDICT_HORIZONS にないなら 400。"""
    if horizon not in const.PREDICT_HORIZONS:
        days = "・".join(map(str, const.PREDICT_HORIZONS))
        raise ApiError(400, f"予測日数は {days} のどれかを指定してください")
