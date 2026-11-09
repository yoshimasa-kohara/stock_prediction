"""特徴量の計算。

学習(7.2)と予測(7.3)の両方が、このモジュールの build_features を使う。特徴量はDBに保存しない。
全部で47個。株価は、指定日を基準にした調整済み株価(6.4)から計算する。
銘柄ごとに株価の水準が違うので、円の金額ではなく、割合や比にしてそろえる。

未来の情報を使わないため、各日(date_from〜base_date)の特徴量は、その日を「指定日」とみなして次の条件で読む。
  daily_price      : 日付がその日以前
  daily_valuation  : 日付がその日の行
  fin_summary      : 開示日がその日の前日以前
  earnings_date    : 公表日がその日以前の行のうち、銘柄ごとに最も新しい行
調整済み株価は base_date を基準にしているが、その日以前の株価どうしの比は、基準をその日にした場合と同じになる。
"""

from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
import psycopg
from psycopg.rows import DictRow, tuple_row

import const
from common import business_day
from common.price_adjust import adjust_prices, cumulative_log_factor
from core import trendline

# 特徴量の名前。モデルに渡す順番(行の後ろのコメントの番号は特徴量の No.)。
# 株価は株式分割を調整した値で計算する。計算の式は _price_features、_market_features、_fundamental_features を参照
FEATURE_NAMES: list[str] = [
    # 値動き(騰落率)
    "ret_1d",  # 1. 1日前からの値動き(終値 ÷ 1日前の終値 − 1)
    "ret_5d",  # 2. 5日前からの値動き
    "ret_10d",  # 3. 10日前からの値動き
    "ret_20d",  # 4. 20日前からの値動き
    "ret_60d",  # 5. 60日前からの値動き
    # 移動平均(単純移動平均)
    "ma5_gap",  # 6. 5日移動平均からのかい離(終値 ÷ 5日移動平均 − 1)
    "ma25_gap",  # 7. 25日移動平均からのかい離
    "ma75_gap",  # 8. 75日移動平均からのかい離
    "ma5_25_ratio",  # 9. 5日移動平均 ÷ 25日移動平均 − 1(プラスなら短期が上。ゴールデンクロス・デッドクロスの状態)
    # 変動の大きさ
    "volatility_20d",  # 10. ボラティリティ(1日の値動きの20日の標準偏差)
    "range_14d",  # 11. 1日の値幅((高値 − 安値)÷ 終値)の14日平均。ATR(平均の値幅)に近い指標
    "range_pos_20d",  # 12. 20日の最安値〜最高値の中のどこにいるか(0 = 最安値、1 = 最高値)
    # 高値・安値
    "from_high_60d",  # 13. 60日の最高値からの下がり具合(終値 ÷ 60日の最高値 − 1)
    "from_low_60d",  # 14. 60日の最安値からの上がり具合(終値 ÷ 60日の最安値 − 1)
    # ローソク足
    "candle_body",  # 15. 実体(陽線ならプラス、陰線ならマイナス。(終値 − 始値)÷ 始値)
    "upper_shadow",  # 16. 上ひげの長さ
    "lower_shadow",  # 17. 下ひげの長さ
    "gap",  # 18. 窓(始値 ÷ 前日の終値 − 1。窓開け・窓埋め)
    # テクニカル指標
    "rsi_14",  # 19. RSI(相対力指数。14日、ワイルダーの計算方法)。買われすぎ・売られすぎ(0〜100)
    "macd_hist",  # 20. MACD(移動平均収束拡散法)のヒストグラム(MACD線 − シグナル線)÷ 終値。12日・26日・9日
    # 21. ボリンジャーバンドの %b(const.BB_DAYS(20)日移動平均 ± 標準偏差の2倍の中の位置。0 = 下のバンド、1 = 上のバンド)
    "bb_pctb",
    # 出来高・売買代金
    "vol_ratio_1d",  # 22. 出来高 ÷ 20日平均の出来高(出来高の急増)
    "vol_ratio_5_20",  # 23. 5日平均の出来高 ÷ 20日平均の出来高
    "turnover_20d_log",  # 24. 20日平均の売買代金(対数)。流動性
    # ストップ高・ストップ安
    "upper_limit",  # 25. ストップ高なら 1
    "lower_limit",  # 26. ストップ安なら 1
    # 市場全体(対象の市場の全銘柄の中央値)
    "mkt_ret_1d",  # 27. 市場全体の1日の値動き
    "mkt_ret_5d",  # 28. 市場全体の5日の値動き
    "rel_ret_5d",  # 29. 市場との差(5日の値動き − 市場全体の5日の値動き)
    "rel_ret_20d",  # 30. 市場との差(20日)
    # 業種・規模
    "sector33",  # 31. 33業種コード(カテゴリ)
    "market",  # 32. 市場区分コード(カテゴリ。プライム・スタンダード・グロースなど)
    "sector_rel_ret_5d",  # 33. 同じ業種との差(5日の値動き − 同じ33業種の5日の値動きの中央値)
    # 割安・割高(バリュエーション)
    "fwd_earnings_yield",  # 34. 予想益回り(予想EPS ÷ 株価。予想PER の逆数)
    "earnings_yield",  # 35. 実績益回り(実績EPS ÷ 株価。PER の逆数)
    "book_to_price",  # 36. BPS ÷ 株価(PBR の逆数)
    "fwd_roe",  # 37. 予想ROE(自己資本利益率)
    "market_cap_log",  # 38. 時価総額(対数)。会社の規模
    # 業績
    "sales_yoy",  # 39. 売上高の前年比
    "op_yoy",  # 40. 営業利益の前年比
    "op_progress",  # 41. 営業利益の進捗率(期首からの累計 ÷ 今期の予想)
    "op_forecast_revision",  # 42. 営業利益の予想の修正率(上方修正ならプラス)
    "equity_ratio",  # 43. 自己資本比率
    "fwd_div_yield",  # 44. 予想配当利回り
    # 決算のタイミング
    "days_to_earnings",  # 45. 次の決算発表予定日までの営業日数
    "days_since_earnings",  # 46. 直近の決算発表からの営業日数
    "last_earnings_reaction",  # 47. 直近の決算発表の前後の値動き(決算への市場の反応)
]

# 評価用に計算する特徴量の候補。build_features の結果には入れるが、本番のモデルにはまだ渡さない。
# tools/evaluate_model.py で、FEATURE_NAMES だけの場合と比べてから、採用するかを決める。グループ名 → 特徴量の名前
EXTRA_FEATURE_GROUPS: dict[str, list[str]] = {
    # 移動平均のクロス(2026-10-22 の評価では精度が上がらず、保留)
    "cross": [
        "ma25_75_ratio",  # 25日移動平均 ÷ 75日移動平均 − 1(中長期の流れ)
        "days_since_cross_5_25",  # 短期(5日と25日)のゴールデンクロス(+)・デッドクロス(−)からの営業日数。上限 20
        "days_since_cross_25_75",  # 中長期(25日と75日)のゴールデンクロス(+)・デッドクロス(−)からの営業日数。上限 20
    ],
    # 一目均衡表(2026-10-22 の評価では20営業日後の精度が少し下がり、保留)
    "ichimoku": [
        "ichimoku_tenkan_kijun",  # 転換線(9日)÷ 基準線(26日)− 1
        "ichimoku_cloud_pos",  # 終値と雲の位置(雲の上ならプラス、雲の下ならマイナス、雲の中なら 0)
        "ichimoku_cloud_width",  # 雲の厚さ(雲の上端 ÷ 雲の下端 − 1)
    ],
    # ボリンジャーバンドの収束(狭まったところから上放れたら買い、の考え方。bb_pctb と組み合わせて使う)
    "bollinger": [
        "bb_width",  # バンドの幅(20日の標準偏差の4倍 ÷ 20日移動平均)。今のバンドの広さ
        "bb_squeeze",  # 今のバンドの幅 ÷ 過去60日のバンドの幅の平均。1より小さいほど、いつもより狭い(収束)
    ],
    # ボリンジャーバンドの収束(120日版)。"bollinger" とは、収束を比べる平均の日数だけが違う
    "bollinger_120": [
        "bb_width",  # "bollinger" と同じ
        "bb_squeeze_120",  # 今のバンドの幅 ÷ 過去120日のバンドの幅の平均
    ],
    # ボリンジャーバンドの %b を25日で計算したもの(改善案 U)。改善案 U では、bb_pctb と置き換えて比べる
    "bb25": [
        "bb_pctb_25",  # ボリンジャーバンドの %b(BB_CANDIDATE_DAYS(25)日移動平均 ± 標準偏差の2倍の中の位置)
    ],
    # トレンドラインからの位置(改善案 O)。線は「人の引き方」で引いた線(core/trendline.py)。
    # 計算が重いので、build_features の with_trendline が True のときだけ計算する(False のときは空)
    "trendline": [
        "trend_support_gap",  # 終値 ÷ 下値支持線の今日の値 − 1。マイナスは線を下に抜けた状態
        "trend_resist_gap",  # 終値 ÷ 上値抵抗線の今日の値 − 1。プラスは線を上に抜けた状態
    ],
    # 終値の回帰直線(改善案 T)。流れの向きと、流れのきれいさ
    "regression": [
        "trend_slope_20d",  # 直近20営業日の終値の回帰直線の傾き ÷ 今日の終値(1日あたり何%か)
        "trend_slope_60d",  # 直近60営業日の終値の回帰直線の傾き ÷ 今日の終値
        "trend_r2_20d",  # 直近20営業日の終値の、回帰直線への当てはまりの良さ(0〜1)
    ],
}
# 特徴量の候補の名前(重複を除き、出てきた順)
EXTRA_FEATURE_NAMES: list[str] = list(dict.fromkeys(name for names in EXTRA_FEATURE_GROUPS.values() for name in names))

# 一目均衡表の日数(一般的な設定): 転換線、基準線、先行スパンB、雲を先にずらす日数
ICHIMOKU_TENKAN_DAYS: int = 9
ICHIMOKU_KIJUN_DAYS: int = 26
ICHIMOKU_SPAN_B_DAYS: int = 52
ICHIMOKU_SHIFT_DAYS: int = 26

# ボリンジャーバンドの幅を平均する日数。bb_squeeze は 20日 + 60日 = 80営業日で、MIN_HISTORY_DAYS の中に収まる。
# bb_squeeze_120 は 20日 + 120日 = 140営業日が必要で、本番に組み込む場合は LOOKBACK_CALENDAR_DAYS を延ばす必要がある
BB_SQUEEZE_DAYS: int = 60
BB_SQUEEZE_LONG_DAYS: int = 120

# ボリンジャーバンドの日数の候補。特徴量の候補 bb_pctb_25 を計算する日数
BB_CANDIDATE_DAYS: int = 25

# 終値の回帰直線を引く日数
REGRESSION_SHORT_DAYS: int = 20
REGRESSION_LONG_DAYS: int = 60

# 特徴量の候補のうち、トレンドラインの線から計算するもの(with_trendline のときだけ計算する)
TRENDLINE_FEATURES: list[str] = ["trend_support_gap", "trend_resist_gap"]

# カテゴリとしてモデルに渡す特徴量
CATEGORICAL_FEATURES: list[str] = ["sector33", "market"]

# 特徴量と一緒に返す、学習・予測で使う列(特徴量ではない)
COL_CLOSE: str = "_close"  # 終値(調整前)
COL_ADJ_CLOSE: str = "_adj_close"  # 終値(base_date を基準にした調整済み)
COL_HIST_DAYS: str = "_hist_days"  # その日以前で、終値がある営業日の数

# 特徴量の計算に、date_from より前の株価をどれだけさかのぼって読むか(暦の日数)。
# MIN_HISTORY_DAYS(80営業日)を暦の日数に直し、祝日などの余裕を足した値
LOOKBACK_CALENDAR_DAYS: int = int(const.MIN_HISTORY_DAYS * 7 / 5) + 40

# 決算発表予定日まで数えるときに、base_date より先の営業日を何日分用意するか(暦の日数)
_FUTURE_CALENDAR_DAYS: int = 400


def feature_names() -> list[str]:
    """特徴量の名前を、モデルに渡す順番で返す。model テーブルの feature_names に入れる。"""
    return list(FEATURE_NAMES)


# =====================================================================
# DBの読み込み
# =====================================================================


def _dt(s: pd.Series) -> pd.Series:
    """日付の列を datetime64[ns] にそろえる(merge_asof は、キーの型がそろっている必要があるため)。"""
    return pd.to_datetime(s).astype("datetime64[ns]")


def _read_frame(
    conn: psycopg.Connection[DictRow], sql: str, params: tuple[Any, ...], columns: list[str]
) -> pd.DataFrame:
    """SQLの結果を DataFrame にする。"""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=columns)


def _load_master(conn: psycopg.Connection[DictRow]) -> pd.DataFrame:
    return _read_frame(
        conn, "SELECT code, sector33_code, market_code FROM stock_master", (), ["code", "sector33_code", "market_code"]
    )


def _load_prices(conn: psycopg.Connection[DictRow], start: date, end: date, codes: list[str]) -> pd.DataFrame:
    df = _read_frame(
        conn,
        """
        SELECT code, trade_date, open::float8, high::float8, low::float8, close::float8, volume::float8,
               turnover::float8, adj_factor::float8, upper_limit_flag, lower_limit_flag
        FROM daily_price
        WHERE trade_date BETWEEN %s AND %s AND code = ANY(%s)
        """,
        (start, end, codes),
        ["code", "trade_date", "open", "high", "low", "close", "volume", "turnover", "adj_factor",
         "upper_limit_flag", "lower_limit_flag"],
    )  # fmt: skip
    df["trade_date"] = _dt(df["trade_date"])
    return df


def _load_valuation(conn: psycopg.Connection[DictRow], start: date, end: date, codes: list[str]) -> pd.DataFrame:
    df = _read_frame(
        conn,
        """
        SELECT code, trade_date, eps::float8, fwd_eps::float8, bps::float8, fwd_roe::float8, market_cap::float8
        FROM daily_valuation WHERE trade_date BETWEEN %s AND %s AND code = ANY(%s)
        """,
        (start, end, codes),
        ["code", "trade_date", "eps", "fwd_eps", "bps", "fwd_roe", "market_cap"],
    )
    df["trade_date"] = _dt(df["trade_date"])
    return df


def _load_fin(conn: psycopg.Connection[DictRow], end: date, codes: list[str]) -> pd.DataFrame:
    """開示日が end 以前の財務情報(end は base_date の前日)。"""
    df = _read_frame(
        conn,
        """
        SELECT code, disc_date, disc_time, doc_type, cur_per_type, cur_per_en, cur_fy_en,
               sales::float8, op::float8, equity_ratio::float8, f_op::float8, f_div_ann::float8
        FROM fin_summary WHERE disc_date <= %s AND code = ANY(%s)
        """,
        (end, codes),
        ["code", "disc_date", "disc_time", "doc_type", "cur_per_type", "cur_per_en", "cur_fy_en",
         "sales", "op", "equity_ratio", "f_op", "f_div_ann"],
    )  # fmt: skip
    for col in ("disc_date", "cur_per_en", "cur_fy_en"):
        df[col] = _dt(df[col])
    # 同じ日の開示は、開示時刻の順に並べる(時刻がない開示は先にする)
    df["_time_key"] = df["disc_time"].map(lambda t: "" if t is None else t.isoformat())
    return df.sort_values(["code", "disc_date", "_time_key"]).reset_index(drop=True)


def _load_earnings(conn: psycopg.Connection[DictRow], end: date, codes: list[str]) -> pd.DataFrame:
    df = _read_frame(
        conn,
        "SELECT code, pub_date, sch_date FROM earnings_date WHERE pub_date <= %s AND code = ANY(%s)",
        (end, codes),
        ["code", "pub_date", "sch_date"],
    )
    df["pub_date"] = _dt(df["pub_date"])
    df["sch_date"] = _dt(df["sch_date"])
    return df


# =====================================================================
# 計算の部品
# =====================================================================


def _rolling(s: pd.Series, by: pd.Series, window: int, how: str) -> pd.Series:
    """銘柄ごとの移動集計(mean、std、max、min)。データが window 個そろわない間は空。"""
    roll = s.groupby(by, sort=False).rolling(window, min_periods=window)
    result: pd.Series = getattr(roll, how)()
    return result.reset_index(level=0, drop=True).sort_index()


def cross_days(short: pd.Series, long: pd.Series, by: pd.Series, cap: int) -> pd.Series:
    """短期と長期の移動平均の上下が、今の向きになってから何営業日目か(符号付き)を返す。

    プラスは短期が長期より上(ゴールデンクロスの後)、マイナスは下(デッドクロスの後)。クロスした日を 1(-1)とし、
    cap を上限にする。移動平均がそろってから一度もクロスしていない(今の向きが始まった日が分からない)場合は ±cap。
    移動平均がない日は空。2つが同じ値の日は、前の日の向きを続ける。行は by(銘柄)、日付の順に並んでいること。
    """
    state = pd.Series(np.sign(short - long), index=short.index).replace(0, np.nan)
    state = state.groupby(by, sort=False).ffill()
    prev = state.groupby(by, sort=False).shift(1)
    valid = state.notna()
    # 今の向きが始まった行(銘柄の最初の行と、向きが変わった行)
    start = valid & (prev.isna() | (state != prev))
    # 始まりが本当のクロスか(前の日の向きが分かっているか)
    crossed = (prev.notna() & start).astype(float).where(start).groupby(by, sort=False).ffill()
    run_id = start.cumsum()
    count = pd.Series(np.nan, index=state.index)
    count[valid] = state[valid].groupby(run_id[valid].to_numpy()).cumcount().to_numpy() + 1
    days = count.clip(upper=cap).where(crossed == 1, float(cap))
    return (state * days).where(valid)


def ichimoku_features(high: pd.Series, low: pd.Series, close: pd.Series, by: pd.Series) -> pd.DataFrame:
    """一目均衡表の特徴量の候補。行は by(銘柄)、日付の順に並んでいること。

    転換線 = 9日の高値と安値の真ん中、基準線 = 26日の真ん中。雲は、先行スパンA(転換線と基準線の真ん中)と
    先行スパンB(52日の真ん中)を26日先にずらしたもの(今日の雲は、26営業日前に計算した値)。

    Returns:
        列 ichimoku_tenkan_kijun(転換線 ÷ 基準線 − 1)、ichimoku_cloud_pos(雲の上なら 終値 ÷ 雲の上端 − 1、
        雲の下なら 終値 ÷ 雲の下端 − 1、雲の中なら 0)、ichimoku_cloud_width(雲の上端 ÷ 雲の下端 − 1)
    """

    def middle(days: int) -> pd.Series:
        return (_rolling(high, by, days, "max") + _rolling(low, by, days, "min")) / 2

    tenkan, kijun = middle(ICHIMOKU_TENKAN_DAYS), middle(ICHIMOKU_KIJUN_DAYS)
    span_a = ((tenkan + kijun) / 2).groupby(by, sort=False).shift(ICHIMOKU_SHIFT_DAYS)
    span_b = middle(ICHIMOKU_SPAN_B_DAYS).groupby(by, sort=False).shift(ICHIMOKU_SHIFT_DAYS)
    spans = pd.concat([span_a, span_b], axis=1)
    top: pd.Series = spans.max(axis=1, skipna=False)
    bottom: pd.Series = spans.min(axis=1, skipna=False)
    above = _safe_div(close, top) - 1
    below = _safe_div(close, bottom) - 1
    pos = above.where(close > top, below.where(close < bottom, 0.0)).where(top.notna() & close.notna())
    return pd.DataFrame(
        {
            "ichimoku_tenkan_kijun": _safe_div(tenkan, kijun) - 1,
            "ichimoku_cloud_pos": pos,
            "ichimoku_cloud_width": _safe_div(top, bottom) - 1,
        },
        index=close.index,
    )


def bollinger_pctb(close: pd.Series, by: pd.Series, days: int) -> pd.Series:
    """ボリンジャーバンドの %b。行は by(銘柄)、日付の順に並んでいること。

    バンドは days 日移動平均 ± 標準偏差の2倍。%b は、下のバンドが 0、上のバンドが 1 になる位置。

    Args:
        close: 終値(株式分割を調整した値)
        by: 銘柄コード
        days: 移動平均と標準偏差を計算する日数(本番は const.BB_DAYS、候補は BB_CANDIDATE_DAYS)

    Returns:
        %b。days 日分の株価がそろっていない行は空(NaN)
    """
    ma, sd = _rolling(close, by, days, "mean"), _rolling(close, by, days, "std")
    return _safe_div(close - (ma - 2 * sd), 4 * sd)


def bollinger_features(close: pd.Series, by: pd.Series) -> pd.DataFrame:
    """ボリンジャーバンドの幅と収束の特徴量の候補。行は by(銘柄)、日付の順に並んでいること。

    バンドは const.BB_DAYS(20)日移動平均 ± 標準偏差の2倍(bb_pctb と同じ)。
    幅は上下のバンドの差(標準偏差の4倍)を移動平均で割った値。

    Returns:
        列 bb_width(バンドの幅 ÷ 20日移動平均)、bb_squeeze(今の幅 ÷ 過去 BB_SQUEEZE_DAYS 日の幅の平均)、
        bb_squeeze_120(今の幅 ÷ 過去 BB_SQUEEZE_LONG_DAYS 日の幅の平均)
    """
    ma20, sd20 = _rolling(close, by, const.BB_DAYS, "mean"), _rolling(close, by, const.BB_DAYS, "std")
    width = _safe_div(4 * sd20, ma20)
    average = _rolling(width, by, BB_SQUEEZE_DAYS, "mean")
    average_long = _rolling(width, by, BB_SQUEEZE_LONG_DAYS, "mean")
    return pd.DataFrame(
        {
            "bb_width": width,
            "bb_squeeze": _safe_div(width, average),
            "bb_squeeze_120": _safe_div(width, average_long),
        },
        index=close.index,
    )


def regression_features(close: pd.Series, by: pd.Series) -> pd.DataFrame:
    """終値の回帰直線の特徴量の候補。行は by(銘柄)、日付の順に並んでいること。

    x を銘柄ごとの行の番号(営業日)、y を終値として、直近 n 営業日に最小二乗法で直線を引く。
    傾き = Σ(x − x の平均)(y − y の平均) ÷ Σ(x − x の平均)²。当てはまりの良さ(決定係数)= 相関係数の2乗。
    n 日の中に終値がない日があれば空。

    Returns:
        列 trend_slope_20d、trend_slope_60d(傾き ÷ 今日の終値)、trend_r2_20d(0〜1)
    """
    pos = close.groupby(by, sort=False).cumcount().astype(float)
    out: dict[str, pd.Series] = {}
    for n in (REGRESSION_SHORT_DAYS, REGRESSION_LONG_DAYS):
        sum_y = _rolling(close, by, n, "sum")
        sum_xy = _rolling(pos * close, by, n, "sum")
        mean_x = pos - (n - 1) / 2
        mean_y = sum_y / n
        cov = sum_xy - n * mean_x * mean_y
        var_x = n * (n * n - 1) / 12
        out[f"trend_slope_{n}d"] = _safe_div(cov / var_x, close)
        if n == REGRESSION_SHORT_DAYS:
            # 分散は、値の大きさによる計算の誤差を減らすため、窓の平均を引いてから求める(標準偏差の2乗 ×(n − 1))
            var_y = _rolling(close, by, n, "std") ** 2 * (n - 1)
            r2 = _safe_div(cov * cov, var_y * var_x)
            out[f"trend_r2_{n}d"] = r2.clip(0, 1)
    return pd.DataFrame(
        {"trend_slope_20d": out["trend_slope_20d"], "trend_slope_60d": out["trend_slope_60d"],
         "trend_r2_20d": out["trend_r2_20d"]},
        index=close.index,
    )  # fmt: skip


def trendline_gaps(df: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
    """trend_support_gap、trend_resist_gap を、mask が True の行だけ計算する(ほかの行は空)。

    df は code、trade_date の順に並び、adj_high、adj_low、adj_close があること。位置は銘柄ごとの行の番号。
    """
    support = np.full(len(df), np.nan)
    resist = np.full(len(df), np.nan)
    positions_all = df.groupby("code", sort=False).cumcount().to_numpy()
    mask_arr = mask.to_numpy()
    for group_index in df.groupby("code", sort=False).indices.values():
        index = np.asarray(group_index, dtype=int)
        rows = index[mask_arr[index]]
        if len(rows) == 0:
            continue
        s, r = trendline.gaps_for_series(
            df["adj_high"].to_numpy(dtype=float)[index],
            df["adj_low"].to_numpy(dtype=float)[index],
            df["adj_close"].to_numpy(dtype=float)[index],
            positions_all[rows],
        )
        support[rows] = s
        resist[rows] = r
    return pd.DataFrame({"trend_support_gap": support, "trend_resist_gap": resist}, index=df.index)


def trendline_notes_on(
    conn: psycopg.Connection[DictRow],
    target_date: date,
    codes: list[str],
    touch_tolerance: float | None = None,
    break_rate: float | None = None,
) -> dict[str, list[str]]:
    """指定日の、トレンドラインの抜けの備考を、銘柄ごとに返す。

    株式分割を調整した株価で計算する。指定日に行がない銘柄と、株価がそろっていない銘柄は含まない。
    株価が少ない銘柄(上場して間もないなど)は、線を引く期間を短くして引く。期間が TREND_MIN_WINDOW_DAYS
    (20営業日)未満になる銘柄(行の数が trendline.required_rows() 未満)は、線を引かない(core/trendline.py)。
    touch_tolerance、break_rate は、備考が付く割合を確かめる道具(tools/count_trend_notes.py)で幅を変えるときに使う。
    """
    if not codes:
        return {}
    raw = _load_prices(conn, target_date - timedelta(days=LOOKBACK_CALENDAR_DAYS), target_date, codes)
    if raw.empty:
        return {}
    df = adjust_prices(raw, target_date)
    result: dict[str, list[str]] = {}
    for code, part in df.groupby("code", sort=False):
        dates = part["trade_date"].to_numpy()
        hit = np.flatnonzero(dates == np.datetime64(pd.Timestamp(target_date)))
        if len(hit) == 0:
            continue
        today = int(hit[0])
        if today < trendline.required_rows():
            continue
        notes = trendline.notes_for_series(
            part["adj_high"].to_numpy(dtype=float),
            part["adj_low"].to_numpy(dtype=float),
            part["adj_close"].to_numpy(dtype=float),
            today,
            touch_tolerance,
            break_rate,
        )
        if notes:
            result[str(code)] = notes
    return result


def cross_days_on(conn: psycopg.Connection[DictRow], target_date: date, codes: list[str], cap: int) -> pd.DataFrame:
    """指定日の、短期(5日と25日)・中長期(25日と75日)のクロスからの日数(cross_days)を、銘柄ごとに返す。

    通知の備考(ゴールデンクロス・デッドクロス)に使う。株式分割を調整した終値で計算する。

    Returns:
        列 code、days_since_cross_5_25、days_since_cross_25_75。指定日に行がない銘柄は含まない
    """
    columns = ["code", "days_since_cross_5_25", "days_since_cross_25_75"]
    if not codes:
        return pd.DataFrame(columns=columns)
    raw = _load_prices(conn, target_date - timedelta(days=LOOKBACK_CALENDAR_DAYS), target_date, codes)
    if raw.empty:
        return pd.DataFrame(columns=columns)
    df = adjust_prices(raw, target_date)
    code, c = df["code"], df["adj_close"]
    ma5, ma25, ma75 = (_rolling(c, code, n, "mean") for n in (5, 25, 75))
    df["days_since_cross_5_25"] = cross_days(ma5, ma25, code, cap)
    df["days_since_cross_25_75"] = cross_days(ma25, ma75, code, cap)
    return df.loc[df["trade_date"] == pd.Timestamp(target_date), columns].reset_index(drop=True)


def _ewm(s: pd.Series, by: pd.Series, **kwargs: Any) -> pd.Series:
    """銘柄ごとの指数移動平均。"""
    return s.groupby(by, sort=False).transform(lambda x: x.ewm(adjust=False, **kwargs).mean())


def _safe_div(a: pd.Series, b: pd.Series) -> pd.Series:
    """割り算。分母が0の場合は空。"""
    return a / b.where(b != 0)


def _to_int_code(s: pd.Series) -> pd.Series:
    """業種コードや市場区分コード(文字列)を、カテゴリの特徴量として渡す整数に直す。数字でない値は空。"""
    return pd.to_numeric(s, errors="coerce")


def _business_day_array(trade_dates: pd.Series, base_date: date) -> np.ndarray:
    """営業日の並び。base_date 以前は株価がある日、base_date より後は RULE で判定した日。"""
    past = _dt(pd.Series(trade_dates.unique()))
    future = _dt(
        pd.Series(
            business_day.list_business_days_by_rule(
                base_date + timedelta(days=1), base_date + timedelta(days=_FUTURE_CALENDAR_DAYS)
            ),
            dtype="object",
        )
    )
    days = pd.concat([past, future]).drop_duplicates().sort_values()
    result: np.ndarray = days.to_numpy(dtype="datetime64[ns]")
    return result


def _business_days_between(bdays: np.ndarray, start: pd.Series, end: pd.Series) -> pd.Series:
    """start より後で end 以前の営業日の数。start と end のどちらかが空なら空。"""
    s = start.to_numpy(dtype="datetime64[ns]")
    e = end.to_numpy(dtype="datetime64[ns]")
    count = np.searchsorted(bdays, e, side="right") - np.searchsorted(bdays, s, side="right")
    result = pd.Series(count.astype(float), index=start.index)
    return result.where(start.notna() & end.notna())


def _asof(
    left: pd.DataFrame, right: pd.DataFrame, left_on: str, right_on: str, direction: str = "backward"
) -> pd.DataFrame:
    """銘柄ごとに、left_on 以前(direction=backward)で最も新しい right の行を結合する。left の行の順番は保つ。"""
    work = left.reset_index(names="_order").sort_values(left_on, kind="stable")
    # 銘柄コードの型をそろえる(空の表は型が違うことがあるため)
    work["code"] = work["code"].astype(object)
    right = right.assign(code=right["code"].astype(object))
    if right.empty:
        merged = work.copy()
        for col in right.columns:
            if col == right_on or pd.api.types.is_datetime64_any_dtype(right[col]):
                merged[col] = pd.Series(pd.NaT, index=merged.index, dtype="datetime64[ns]")
            elif col != "code":
                merged[col] = np.nan
    else:
        merged = pd.merge_asof(
            work,
            right.sort_values(right_on, kind="stable"),
            left_on=left_on,
            right_on=right_on,
            by="code",
            direction=direction,  # type: ignore[arg-type]
            allow_exact_matches=True,
        )
    return merged.sort_values("_order").set_index("_order").rename_axis(None)


# =====================================================================
# 株価と出来高から計算する特徴量(No.1〜33)
# =====================================================================


def _price_features(df: pd.DataFrame) -> pd.DataFrame:
    """調整済み株価の表に、株価と出来高から計算する特徴量を足す。df は code、trade_date の順に並んでいること。"""
    code = df["code"]
    c, o, h, low = df["adj_close"], df["adj_open"], df["adj_high"], df["adj_low"]
    grouped_close = c.groupby(code, sort=False)

    def lag(s: pd.Series, n: int) -> pd.Series:
        return s.groupby(code, sort=False).shift(n)

    # 値動き
    for n in (1, 5, 10, 20, 60):
        df[f"ret_{n}d"] = _safe_div(c, lag(c, n)) - 1
    # 移動平均
    ma5, ma25, ma75 = (_rolling(c, code, n, "mean") for n in (5, 25, 75))
    df["ma5_gap"] = _safe_div(c, ma5) - 1
    df["ma25_gap"] = _safe_div(c, ma25) - 1
    df["ma75_gap"] = _safe_div(c, ma75) - 1
    df["ma5_25_ratio"] = _safe_div(ma5, ma25) - 1
    # 特徴量の候補(EXTRA_FEATURE_NAMES)。中期の流れと、ゴールデンクロス・デッドクロスからの日数
    df["ma25_75_ratio"] = _safe_div(ma25, ma75) - 1
    df["days_since_cross_5_25"] = cross_days(ma5, ma25, code, const.CROSS_DAYS_CAP)
    df["days_since_cross_25_75"] = cross_days(ma25, ma75, code, const.CROSS_DAYS_CAP)
    # 特徴量の候補: 一目均衡表
    df = df.join(ichimoku_features(h, low, c, code))
    # 変動の大きさ
    df["volatility_20d"] = _rolling(df["ret_1d"], code, 20, "std")
    df["range_14d"] = _rolling(_safe_div(h - low, c), code, 14, "mean")
    high20, low20 = _rolling(h, code, 20, "max"), _rolling(low, code, 20, "min")
    df["range_pos_20d"] = _safe_div(c - low20, high20 - low20)
    # 高値・安値
    df["from_high_60d"] = _safe_div(c, _rolling(h, code, 60, "max")) - 1
    df["from_low_60d"] = _safe_div(c, _rolling(low, code, 60, "min")) - 1
    # ローソク足
    df["candle_body"] = _safe_div(c - o, o)
    df["upper_shadow"] = _safe_div(h - np.maximum(o, c), c)
    df["lower_shadow"] = _safe_div(np.minimum(o, c) - low, c)
    df["gap"] = _safe_div(o, lag(c, 1)) - 1
    # テクニカル指標: RSI(14日。ワイルダーの計算方法)
    delta = grouped_close.diff()
    avg_gain = _ewm(delta.clip(lower=0), code, alpha=1 / 14, min_periods=14)
    avg_loss = _ewm((-delta).clip(lower=0), code, alpha=1 / 14, min_periods=14)
    rs = _safe_div(avg_gain, avg_loss)
    rsi = 100 - 100 / (1 + rs)
    df["rsi_14"] = rsi.where(~((avg_loss == 0) & avg_gain.notna()), 100.0)
    # MACD のヒストグラムを終値で割った値
    macd = _ewm(c, code, span=12, min_periods=12) - _ewm(c, code, span=26, min_periods=26)
    signal = _ewm(macd, code, span=9, min_periods=9)
    df["macd_hist"] = _safe_div(macd - signal, c)
    # ボリンジャーバンドの %b(日数は const.BB_DAYS)
    df["bb_pctb"] = bollinger_pctb(c, code, const.BB_DAYS)
    # 特徴量の候補: %b を25日で計算したもの(改善案 U)
    df["bb_pctb_25"] = bollinger_pctb(c, code, BB_CANDIDATE_DAYS)
    # 特徴量の候補: ボリンジャーバンドの幅と収束
    df = df.join(bollinger_features(c, code))
    # 特徴量の候補: 終値の回帰直線
    df = df.join(regression_features(c, code))
    # 出来高
    vol = df["adj_volume"]
    vol5, vol20 = _rolling(vol, code, 5, "mean"), _rolling(vol, code, 20, "mean")
    df["vol_ratio_1d"] = _safe_div(vol, vol20)
    df["vol_ratio_5_20"] = _safe_div(vol5, vol20)
    turnover = pd.to_numeric(df["turnover"], errors="coerce").astype(float).fillna(0.0)
    df["turnover_20d_log"] = np.log(_rolling(turnover, code, 20, "mean") + 1)
    # ストップ
    df["upper_limit"] = df["upper_limit_flag"].eq(True).astype(float)
    df["lower_limit"] = df["lower_limit_flag"].eq(True).astype(float)
    # 終値がある営業日の数
    df[COL_HIST_DAYS] = df["close"].notna().astype(int).groupby(code, sort=False).cumsum()
    return df


def _market_features(df: pd.DataFrame) -> pd.DataFrame:
    """市場全体と、業種の中での強さ(No.27〜30、No.33)。中央値は対象の市場の全銘柄で計算する。"""
    in_market = df["market_code"].isin(const.TARGET_MARKET_CODES)
    medians = (
        df.loc[in_market]
        .groupby("trade_date")[["ret_1d", "ret_5d", "ret_20d"]]
        .median()
        .rename(columns={"ret_1d": "mkt_ret_1d", "ret_5d": "mkt_ret_5d", "ret_20d": "_mkt_ret_20d"})
    )
    df = df.join(medians, on="trade_date")
    df["rel_ret_5d"] = df["ret_5d"] - df["mkt_ret_5d"]
    df["rel_ret_20d"] = df["ret_20d"] - df["_mkt_ret_20d"]
    sector_median = df.groupby(["trade_date", "sector33_code"], dropna=False)["ret_5d"].transform("median")
    df["sector_rel_ret_5d"] = (df["ret_5d"] - sector_median).where(df["sector33_code"].notna())
    df["sector33"] = _to_int_code(df["sector33_code"])
    df["market"] = _to_int_code(df["market_code"])
    return df


# =====================================================================
# 業績と決算のタイミング(No.39〜47)
# =====================================================================


def _statement_group(st: pd.DataFrame) -> pd.Series:
    """同じ決算短信(銘柄・開示書類の種類・会計期間の種類・会計期間の終了日が同じ)を表すキー。会計期間がわからなければ空。"""
    key = (
        st["code"].astype(str)
        + "|"
        + st["doc_type"].astype(str)
        + "|"
        + st["cur_per_type"].astype(str)
        + "|"
        + st["cur_per_en"].dt.strftime("%Y-%m-%d")
    )
    known = st["cur_per_type"].notna() & st["cur_per_en"].notna()
    result: pd.Series = key.where(known)
    return result


def _statement_tables(fin: pd.DataFrame, prices: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """決算短信の行を、元の決算短信と、その訂正に分ける。

    J-Quants の財務情報には、訂正を表す開示書類の種類(doc_type)がない。そこで、同じ決算短信
    (銘柄・doc_type・会計期間の種類・会計期間の終了日が同じ)の2つ目以降の開示を訂正とみなす。
    元の決算短信には、前年の同じ会計期間との比較と、開示の前後の値動きを足す。
    訂正には、前年との比較(sales_yoy、op_yoy の差し替え用)だけを足す。

    Returns:
        (元の決算短信の表, 訂正の表)。訂正の表の列は stmt_group、corr_disc_date、corr_sales_yoy、corr_op_yoy
    """
    keyword = const.FIN_STATEMENT_DOC_TYPE_KEYWORD
    st = fin.loc[fin["doc_type"].astype(str).str.contains(keyword, regex=False)].copy()
    columns = ["code", "disc_date", "stmt_cur_per_type", "stmt_cur_fy_en", "stmt_op", "stmt_equity_ratio",
               "stmt_sales_yoy", "stmt_op_yoy", "stmt_next_date", "stmt_reaction", "stmt_group"]  # fmt: skip
    corr_columns = ["stmt_group", "corr_disc_date", "corr_sales_yoy", "corr_op_yoy"]
    if st.empty:
        empty = pd.DataFrame(columns=columns)
        dates = {"disc_date", "stmt_cur_fy_en", "stmt_next_date"}
        objects = {"code", "stmt_cur_per_type", "stmt_group"}
        types = {c: ("datetime64[ns]" if c in dates else ("object" if c in objects else float)) for c in columns}
        empty_corr = pd.DataFrame(columns=corr_columns).astype(
            {"stmt_group": object, "corr_disc_date": "datetime64[ns]", "corr_sales_yoy": float, "corr_op_yoy": float}
        )
        return empty.astype(types), empty_corr
    # 同じ決算短信の2つ目以降の開示は訂正(fin は開示日、開示時刻の順に並んでいる)
    st["stmt_group"] = _statement_group(st)
    st["_is_corr"] = st.groupby("stmt_group", sort=False).cumcount() > 0
    # 同じ日に複数の開示がある場合は、最も新しい(開示時刻が遅い)ものを使う(元と訂正は別々に数える)
    st = st.drop_duplicates(["code", "disc_date", "_is_corr"], keep="last")
    # 前年の同じ会計期間(会計期間の種類が同じで、終了月が12か月前)。
    # 前年の決算短信が後から訂正された場合でも、その開示の時点で出ていたもの(開示日がその開示日以前)のうち、
    # 最も新しいものを使う(訂正の開示日より前の日の特徴量に、訂正後の値を使わないため。未来の情報を使わない)
    st["_ym"] = st["cur_per_en"].dt.year * 12 + st["cur_per_en"].dt.month
    st = st.reset_index(drop=True).reset_index(names="_row")
    keys = ["code", "cur_per_type", "_ym"]
    prior = st[[*keys, "disc_date", "sales", "op"]].dropna(subset=["_ym"])
    prior = prior.assign(_ym=prior["_ym"] + 12).rename(
        columns={"disc_date": "_prior_disc", "sales": "_prior_sales", "op": "_prior_op"}
    )
    pairs = st[["_row", *keys, "disc_date"]].merge(prior, on=keys, how="inner")
    pairs = pairs.loc[pairs["_prior_disc"] <= pairs["disc_date"]]
    pairs = pairs.sort_values(["_row", "_prior_disc"], kind="stable").drop_duplicates("_row", keep="last")
    st = st.merge(pairs[["_row", "_prior_sales", "_prior_op"]], on="_row", how="left").drop(columns="_row")
    st["stmt_sales_yoy"] = _safe_div(st["sales"], st["_prior_sales"].where(st["_prior_sales"] > 0)) - 1
    st["stmt_op_yoy"] = _safe_div(st["op"], st["_prior_op"].where(st["_prior_op"] > 0)) - 1
    corr = st.loc[st["_is_corr"]].rename(
        columns={"disc_date": "corr_disc_date", "stmt_sales_yoy": "corr_sales_yoy", "stmt_op_yoy": "corr_op_yoy"}
    )
    corr = corr[corr_columns].dropna(subset=["stmt_group"]).reset_index(drop=True)
    st = st.loc[~st["_is_corr"]].reset_index(drop=True)
    # 開示日の前営業日の終値と、翌営業日の終値
    px = prices.loc[prices["adj_close"].notna(), ["code", "trade_date", "adj_close"]]
    st["_prev_key"] = st["disc_date"] - pd.Timedelta(days=1)
    st["_next_key"] = st["disc_date"] + pd.Timedelta(days=1)
    before = _asof(
        st[["code", "_prev_key"]], px.rename(columns={"adj_close": "_prev_close"}), "_prev_key", "trade_date"
    )
    after = _asof(
        st[["code", "_next_key"]],
        px.rename(columns={"adj_close": "_next_close", "trade_date": "_next_date"}),
        "_next_key",
        "_next_date",
        direction="forward",
    )
    st["stmt_next_date"] = after["_next_date"].to_numpy()
    st["stmt_reaction"] = _safe_div(after["_next_close"], before["_prev_close"]).to_numpy() - 1
    st = st.rename(
        columns={
            "cur_per_type": "stmt_cur_per_type",
            "cur_fy_en": "stmt_cur_fy_en",
            "op": "stmt_op",
            "equity_ratio": "stmt_equity_ratio",
        }
    )
    return st[columns], corr


def _statement_table(fin: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """元の決算短信の表(_statement_tables の1つ目)。"""
    return _statement_tables(fin, prices)[0]


def _apply_corrections(rows: pd.DataFrame, corr: pd.DataFrame) -> pd.DataFrame:
    """sales_yoy・op_yoy を、基準日以前に開示された一番新しい訂正の値に差し替える(訂正がなければそのまま)。

    「直近の決算」の日付(stmt_disc_date)、days_since_earnings、last_earnings_reaction は変えない。
    """
    if corr.empty:
        return rows
    work = (
        rows.loc[rows["stmt_group"].notna(), ["_fin_key", "stmt_group"]]
        .reset_index(names="_order")
        .sort_values("_fin_key", kind="stable")
    )
    if work.empty:
        return rows
    merged = pd.merge_asof(
        work,
        corr.sort_values("corr_disc_date", kind="stable"),
        left_on="_fin_key",
        right_on="corr_disc_date",
        by="stmt_group",
    )
    merged = merged.loc[merged["corr_disc_date"].notna()].set_index("_order")
    rows = rows.copy()
    rows.loc[merged.index, "stmt_sales_yoy"] = merged["corr_sales_yoy"]
    rows.loc[merged.index, "stmt_op_yoy"] = merged["corr_op_yoy"]
    return rows


def _forecast_table(fin: pd.DataFrame) -> pd.DataFrame:
    """今期の営業利益の予想がある開示に、同じ事業年度の1つ前の予想からの変化を足す。"""
    fc = fin.loc[fin["f_op"].notna(), ["code", "disc_date", "cur_fy_en", "f_op"]].copy()
    fc = fc.drop_duplicates(["code", "disc_date"], keep="last")
    previous = fc.groupby(["code", "cur_fy_en"], dropna=False)["f_op"].shift(1)
    revision = _safe_div(fc["f_op"], previous.where(previous > 0)) - 1
    # 1つ前の予想がなければ 0
    fc["fc_revision"] = revision.where(previous.notna(), 0.0)
    return fc.rename(columns={"cur_fy_en": "fc_cur_fy_en", "f_op": "fc_f_op"})


def _dividend_table(fin: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """今期の年間配当の予想がある開示に、開示日の時点の調整係数の累積を足す。"""
    dv = fin.loc[fin["f_div_ann"].notna(), ["code", "disc_date", "f_div_ann"]].copy()
    dv = dv.drop_duplicates(["code", "disc_date"], keep="last")
    cum = prices[["code", "trade_date", "_cum_log_factor"]].rename(columns={"_cum_log_factor": "div_cum_at_disc"})
    dv = _asof(dv, cum, "disc_date", "trade_date").drop(columns=["trade_date"], errors="ignore")
    return dv.rename(columns={"disc_date": "div_disc_date", "f_div_ann": "div_f_div_ann"})


def _fundamental_features(
    rows: pd.DataFrame,
    prices: pd.DataFrame,
    valuation: pd.DataFrame,
    fin: pd.DataFrame,
    earnings: pd.DataFrame,
    bdays: np.ndarray,
) -> pd.DataFrame:
    """割安・割高(No.34〜38)、業績(No.39〜44)、決算のタイミング(No.45〜47)を足す。"""
    close = rows["close"]
    # 割安・割高: その日のバリュエーション指標
    rows = rows.merge(valuation, on=["code", "trade_date"], how="left")
    rows["fwd_earnings_yield"] = _safe_div(rows["fwd_eps"], close)
    rows["earnings_yield"] = _safe_div(rows["eps"], close)
    rows["book_to_price"] = _safe_div(rows["bps"], close)
    rows["fwd_roe"] = rows["fwd_roe"]
    rows["market_cap_log"] = np.log(rows["market_cap"].where(rows["market_cap"] > 0))

    # 財務情報は、開示日がその日の前日以前のもの
    rows["_fin_key"] = rows["trade_date"] - pd.Timedelta(days=1)
    st, corr = _statement_tables(fin, prices)
    rows = _asof(rows, st.rename(columns={"disc_date": "stmt_disc_date"}), "_fin_key", "stmt_disc_date")
    rows = _apply_corrections(rows, corr)
    fc = _forecast_table(fin).rename(columns={"disc_date": "fc_disc_date"})
    rows = _asof(rows, fc, "_fin_key", "fc_disc_date")
    dv = _dividend_table(fin, prices)
    rows = _asof(rows, dv, "_fin_key", "div_disc_date")

    rows["sales_yoy"] = rows["stmt_sales_yoy"]
    rows["op_yoy"] = rows["stmt_op_yoy"]
    same_year = rows["fc_cur_fy_en"] == rows["stmt_cur_fy_en"]
    not_fy = rows["stmt_cur_per_type"].astype(str) != "FY"
    rows["op_progress"] = _safe_div(rows["stmt_op"], rows["fc_f_op"]).where(same_year & not_fy)
    rows["op_forecast_revision"] = rows["fc_revision"]
    rows["equity_ratio"] = rows["stmt_equity_ratio"]
    # 配当予想は、開示日より後の株式分割を、調整係数で補正する
    split = np.exp(rows["_cum_log_factor"] - rows["div_cum_at_disc"])
    rows["fwd_div_yield"] = _safe_div(rows["div_f_div_ann"] * split, close)

    # 決算発表予定日: 公表日がその日以前で、最も新しい行の予定日
    ed = earnings.sort_values(["pub_date", "sch_date"], na_position="first")
    ed = ed.drop_duplicates(["code", "pub_date"], keep="last").rename(columns={"sch_date": "ed_sch_date"})
    rows = _asof(rows, ed, "trade_date", "pub_date")
    upcoming = rows["ed_sch_date"].where(rows["ed_sch_date"] >= rows["trade_date"])
    rows["days_to_earnings"] = _business_days_between(bdays, rows["trade_date"], upcoming)
    rows["days_since_earnings"] = _business_days_between(bdays, rows["stmt_disc_date"], rows["trade_date"])
    rows["last_earnings_reaction"] = rows["stmt_reaction"].where(rows["stmt_next_date"] <= rows["trade_date"])
    return rows


# =====================================================================
# 公開する関数
# =====================================================================


def build_features(
    conn: psycopg.Connection[DictRow],
    base_date: date,
    date_from: date,
    codes: list[str],
    with_trendline: bool = False,
) -> pd.DataFrame:
    """銘柄 × 日付ごとに、47個の特徴量と、評価用の特徴量の候補(EXTRA_FEATURE_NAMES)を並べた表を返す。

    Args:
        conn: DBの接続
        base_date: 指定日。これより後のデータは読まない
        date_from: 特徴量を作る最初の日。学習では学習の開始日、予測では指定日
        codes: 特徴量を作る銘柄コードのリスト
        with_trendline: True なら、特徴量の候補 trend_support_gap、trend_resist_gap も計算する(計算が重いので、
            評価ツールで使うときだけ True にする。False なら空)

    Returns:
        列 code、trade_date(datetime64)、FEATURE_NAMES の47列、EXTRA_FEATURE_NAMES の列、_close(終値)、
        _adj_close(調整済みの終値)、_hist_days(その日以前で終値がある営業日の数)。
        date_from〜base_date の行で、codes の銘柄だけ
    """
    out_columns = [
        "code", "trade_date", *FEATURE_NAMES, *EXTRA_FEATURE_NAMES, COL_CLOSE, COL_ADJ_CLOSE, COL_HIST_DAYS,
    ]  # fmt: skip
    if not codes or date_from > base_date:
        return pd.DataFrame(columns=out_columns)

    master = _load_master(conn)
    # 市場全体の中央値を計算するため、対象の市場の全銘柄も読む
    market_codes = master.loc[master["market_code"].isin(const.TARGET_MARKET_CODES), "code"].tolist()
    load_codes = sorted(set(codes) | set(market_codes))
    load_start = date_from - timedelta(days=LOOKBACK_CALENDAR_DAYS)

    raw = _load_prices(conn, load_start, base_date, load_codes)
    if raw.empty:
        return pd.DataFrame(columns=out_columns)
    df = adjust_prices(raw, base_date)
    df["_cum_log_factor"] = cumulative_log_factor(df)
    df = df.merge(master, on="code", how="left")
    df = _price_features(df)
    df = _market_features(df)
    bdays = _business_day_array(df["trade_date"], base_date)

    in_rows = (df["trade_date"] >= pd.Timestamp(date_from)) & df["code"].isin(codes)
    if with_trendline:
        df = df.join(trendline_gaps(df, in_rows))
    else:
        df[TRENDLINE_FEATURES] = np.nan
    rows = df.loc[in_rows].copy()
    if rows.empty:
        return pd.DataFrame(columns=out_columns)
    valuation = _load_valuation(conn, date_from, base_date, codes)
    fin = _load_fin(conn, base_date - timedelta(days=1), codes)
    earnings = _load_earnings(conn, base_date, codes)
    prices_for_fin = df.loc[df["code"].isin(codes), ["code", "trade_date", "adj_close", "_cum_log_factor"]]
    rows = _fundamental_features(rows.reset_index(drop=True), prices_for_fin, valuation, fin, earnings, bdays)

    rows[COL_CLOSE] = rows["close"]
    rows[COL_ADJ_CLOSE] = rows["adj_close"]
    result = rows[out_columns].sort_values(["trade_date", "code"]).reset_index(drop=True)
    numeric = [*FEATURE_NAMES, *EXTRA_FEATURE_NAMES]
    result[numeric] = result[numeric].astype(float).replace([np.inf, -np.inf], np.nan)
    return result
