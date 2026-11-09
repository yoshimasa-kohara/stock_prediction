"""common/price_adjust.py と core/features.py のテスト。"""

from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd
import psycopg
import pytest
from psycopg.rows import DictRow

import const
from common import business_day as bd
from common.price_adjust import adjust_prices, cumulative_log_factor, to_base_price
from core import features
from tests.sample_data import SAMPLE_CODES, SPLIT_CODE, insert_sample_data

SPLIT_DATE = date(2025, 6, 2)
ALL_CODES = [c for c, _, _ in SAMPLE_CODES]

# =====================================================================
# 調整済み株価
# =====================================================================


def _prices() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "code": ["A", "A", "A", "A", "B"],
            "trade_date": pd.to_datetime(["2025-06-02", "2025-06-03", "2025-06-04", "2025-06-05", "2025-06-03"]),
            "open": [1000.0, 1000.0, 500.0, 510.0, 300.0],
            "high": [1010.0, 1010.0, 505.0, 520.0, 310.0],
            "low": [990.0, 990.0, 495.0, 500.0, 290.0],
            "close": [1000.0, 1000.0, 500.0, 515.0, 300.0],
            "volume": [100.0, 100.0, 200.0, 210.0, 50.0],
            "adj_factor": [1.0, 1.0, 0.5, 1.0, 1.0],
        }
    )


def test_分割より前の株価は調整係数を掛けてそろえる() -> None:
    df = adjust_prices(_prices(), date(2025, 6, 5))
    a = df.loc[df["code"] == "A"]
    assert a["adj_close"].tolist() == [500.0, 500.0, 500.0, 515.0]
    assert a["adj_volume"].tolist() == [200.0, 200.0, 200.0, 210.0]
    assert a["adj_open"].tolist()[0] == 500.0
    assert df.loc[df["code"] == "B", "adj_close"].tolist() == [300.0]


def test_基準日より後の調整係数は使わず行も除く() -> None:
    df = adjust_prices(_prices(), date(2025, 6, 3))
    a = df.loc[df["code"] == "A"]
    assert a["adj_close"].tolist() == [1000.0, 1000.0]
    assert len(df) == 3


def test_調整係数が空や0の場合は1として扱う() -> None:
    p = _prices()
    p.loc[2, "adj_factor"] = None
    p.loc[3, "adj_factor"] = 0
    df = adjust_prices(p, date(2025, 6, 5))
    assert df.loc[df["code"] == "A", "adj_multiplier"].tolist() == [1.0, 1.0, 1.0, 1.0]


def test_検証の実株価は予測日の基準にそろえる() -> None:
    assert to_base_price(Decimal("500"), [Decimal("1"), Decimal("0.5")]) == Decimal("1000")
    assert to_base_price(Decimal("850"), []) == Decimal("850")
    with pytest.raises(ValueError):
        to_base_price(Decimal("1"), [Decimal("0")])


def test_調整係数の累積() -> None:
    p = _prices()
    cum = cumulative_log_factor(p)
    assert np.exp(cum.iloc[3] - cum.iloc[1]) == pytest.approx(0.5)
    assert cum.iloc[4] == 0


# =====================================================================
# 日付の計算(features で使う)
# =====================================================================


def test_か月前と年前() -> None:
    assert bd.months_before(date(2026, 7, 8), 1) == date(2026, 6, 8)
    assert bd.months_before(date(2026, 3, 31), 1) == date(2026, 2, 28)
    assert bd.months_before(date(2026, 1, 15), 3) == date(2025, 10, 15)
    assert bd.years_before(date(2028, 2, 29), 1) == date(2027, 2, 28)
    assert bd.years_before(date(2026, 7, 4), 20) == date(2006, 7, 4)


def test_RULEの営業日の一覧() -> None:
    assert bd.list_business_days_by_rule(date(2026, 7, 17), date(2026, 7, 21)) == [date(2026, 7, 17), date(2026, 7, 21)]
    assert bd.list_business_days_by_rule(date(2026, 7, 2), date(2026, 7, 1)) == []


# =====================================================================
# 特徴量
# =====================================================================


@pytest.fixture
def sample(conn: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    insert_sample_data(conn, date(2025, 1, 6), date(2025, 12, 26), split_date=SPLIT_DATE)
    return conn


def test_47個の特徴量を順番どおりに返す(sample: psycopg.Connection[DictRow]) -> None:
    df = features.build_features(sample, date(2025, 12, 26), date(2025, 12, 26), ALL_CODES)
    assert len(features.feature_names()) == 47
    assert list(df.columns) == [
        "code", "trade_date", *features.feature_names(), *features.EXTRA_FEATURE_NAMES, "_close", "_adj_close",
        "_hist_days",
    ]  # fmt: skip
    # 特徴量の候補は、本番のモデルに渡す47個には入れない
    assert not set(features.EXTRA_FEATURE_NAMES) & set(features.feature_names())
    assert len(df) == len(ALL_CODES)
    assert df["trade_date"].unique().tolist() == [pd.Timestamp("2025-12-26")]
    # カテゴリの特徴量は整数
    assert set(df["market"].dropna().astype(int)) == {111, 112, 113}
    assert df["sector33"].notna().all()


def test_未来のデータを使わない(sample: psycopg.Connection[DictRow]) -> None:
    """指定日より後のデータがあってもなくても、指定日の特徴量は同じになる。"""
    d = date(2025, 9, 1)
    with_future = features.build_features(sample, d, d, ALL_CODES)
    # 指定日より後のデータを消して、もう一度計算する
    for table, column in (("daily_price", "trade_date"), ("daily_valuation", "trade_date")):
        sample.execute(f"DELETE FROM {table} WHERE {column} > %s", (d,))
    sample.execute("DELETE FROM fin_summary WHERE disc_date >= %s", (d,))
    sample.execute("DELETE FROM earnings_date WHERE pub_date > %s", (d,))
    without_future = features.build_features(sample, d, d, ALL_CODES)
    pd.testing.assert_frame_equal(
        with_future.drop(columns=["_adj_close"]), without_future.drop(columns=["_adj_close"]), check_exact=False
    )


def test_分割の前後で値動きの特徴量がずれない(sample: psycopg.Connection[DictRow]) -> None:
    df = features.build_features(sample, date(2025, 6, 10), date(2025, 5, 20), [SPLIT_CODE])
    assert df["ret_1d"].abs().max() < 0.2
    assert df["ma25_gap"].abs().max() < 0.3


def test_業績と決算のタイミングの特徴量(sample: psycopg.Connection[DictRow]) -> None:
    df = features.build_features(sample, date(2025, 12, 26), date(2025, 4, 1), ALL_CODES[:2])
    first = df.loc[df["code"] == ALL_CODES[0]].set_index("trade_date")
    later = first.loc[pd.Timestamp("2025-09-01")]
    assert later["equity_ratio"] == pytest.approx(0.4)
    assert later["days_since_earnings"] > 0
    # 最新の予想(3Q の 500)と、同じ事業年度の1つ前の予想(修正の 550)の比
    assert later["op_forecast_revision"] == pytest.approx((500 / 550) - 1)
    # 修正の直後は、修正(550)と1Q の予想(500)の比
    assert first.loc[pd.Timestamp("2025-06-10"), "op_forecast_revision"] == pytest.approx((550 / 500) - 1)
    # 1Q の決算短信の営業利益(300)÷ 今期の予想
    assert first.loc[pd.Timestamp("2025-06-10"), "op_progress"] == pytest.approx(300 / 550)
    # 決算発表予定日(2025-05-16)の前は、予定日までの営業日数
    assert first.loc[pd.Timestamp("2025-05-15"), "days_to_earnings"] == 1
    assert first.loc[pd.Timestamp("2025-05-16"), "days_to_earnings"] == 0
    # 前年の同じ会計期間(2024-12-31 → 2025-12-31 は開示前なので、3Q どうしの比較はない)
    assert df["sales_yoy"].isna().all() or df["sales_yoy"].dropna().gt(-1).all()


def test_前年の決算短信の訂正は訂正の開示日より前の比較に使わない() -> None:
    def row(disc: str, per_en: str, sales: float) -> dict[str, object]:
        return {
            "code": "A", "disc_date": pd.Timestamp(disc), "disc_time": None,
            "doc_type": "FYFinancialStatements_Consolidated_JP",
            "cur_per_type": "FY", "cur_per_en": pd.Timestamp(per_en), "cur_fy_en": pd.Timestamp(per_en),
            "sales": sales, "op": sales / 10, "equity_ratio": 0.5, "f_op": np.nan, "f_div_ann": np.nan, "_time_key": "",
        }  # fmt: skip

    # 前年(2024-03 期)の決算短信 → 今年(2025-03 期)の決算短信 → 前年の決算短信の訂正(今年の開示より後)
    fin = pd.DataFrame(
        [row("2024-05-10", "2024-03-31", 1000.0), row("2025-05-10", "2025-03-31", 1200.0),
         row("2025-06-20", "2024-03-31", 600.0)]
    )  # fmt: skip
    prices = pd.DataFrame(columns=["code", "trade_date", "adj_close"]).astype(
        {"code": object, "trade_date": "datetime64[ns]", "adj_close": float}
    )
    st = features._statement_table(fin, prices)
    current = st.loc[st["disc_date"] == pd.Timestamp("2025-05-10")]
    # 2025-05-10 の時点で出ていた前年の値(1000)と比べる。後から出た訂正(600)は使わない
    assert current["stmt_sales_yoy"].iloc[0] == pytest.approx(0.2)


def _fin_row(disc: str, per_en: str, sales: float, per_type: str = "FY") -> dict[str, object]:
    return {
        "code": "A", "disc_date": pd.Timestamp(disc), "disc_time": None,
        "doc_type": f"{per_type}FinancialStatements_Consolidated_JP",
        "cur_per_type": per_type, "cur_per_en": pd.Timestamp(per_en), "cur_fy_en": pd.Timestamp(per_en),
        "sales": sales, "op": sales / 10, "equity_ratio": 0.5, "f_op": np.nan, "f_div_ann": np.nan, "_time_key": "",
    }  # fmt: skip


def test_決算短信の訂正は元の決算短信と分けて数える() -> None:
    # 2025-05-10 の決算短信(前年 1000 → 1200)と、2025-05-20 の訂正(1200 → 1500)
    fin = pd.DataFrame(
        [_fin_row("2024-05-10", "2024-03-31", 1000.0), _fin_row("2025-05-10", "2025-03-31", 1200.0),
         _fin_row("2025-05-20", "2025-03-31", 1500.0)]
    )  # fmt: skip
    prices = pd.DataFrame(columns=["code", "trade_date", "adj_close"]).astype(
        {"code": object, "trade_date": "datetime64[ns]", "adj_close": float}
    )
    orig, corr = features._statement_tables(fin, prices)
    assert list(orig["disc_date"]) == [pd.Timestamp("2024-05-10"), pd.Timestamp("2025-05-10")]
    assert list(corr["corr_disc_date"]) == [pd.Timestamp("2025-05-20")]
    assert corr["corr_sales_yoy"].iloc[0] == pytest.approx(0.5)


def test_訂正は売上と営業利益の前年比だけを差し替える() -> None:
    fin = pd.DataFrame(
        [_fin_row("2024-05-10", "2024-03-31", 1000.0), _fin_row("2025-05-10", "2025-03-31", 1200.0),
         _fin_row("2025-05-20", "2025-03-31", 1500.0)]
    )  # fmt: skip
    prices = pd.DataFrame(columns=["code", "trade_date", "adj_close"]).astype(
        {"code": object, "trade_date": "datetime64[ns]", "adj_close": float}
    )
    st, corr = features._statement_tables(fin, prices)
    days = pd.to_datetime(["2025-05-12", "2025-05-20", "2025-05-21"])
    rows = pd.DataFrame({"code": "A", "trade_date": days, "_fin_key": days - pd.Timedelta(days=1)})
    rows = features._asof(rows, st.rename(columns={"disc_date": "stmt_disc_date"}), "_fin_key", "stmt_disc_date")
    out = features._apply_corrections(rows, corr)
    # 訂正の開示日の前日まで(05-12、05-20)は元の値。訂正の翌日(05-21)から訂正後の値
    assert list(out["stmt_sales_yoy"]) == pytest.approx([0.2, 0.2, 0.5])
    assert list(out["stmt_op_yoy"]) == pytest.approx([0.2, 0.2, 0.5])
    # 「直近の決算」の日付は、訂正の後も元の開示日のまま
    assert set(out["stmt_disc_date"]) == {pd.Timestamp("2025-05-10")}


def test_訂正がない決算短信と会計期間が違う訂正は影響しない() -> None:
    # 2Q の決算短信は、同じ日付でも会計期間の種類が違えば訂正ではない
    fin = pd.DataFrame(
        [_fin_row("2025-05-10", "2025-03-31", 1200.0), _fin_row("2025-11-05", "2025-09-30", 700.0, "2Q")]
    )
    prices = pd.DataFrame(columns=["code", "trade_date", "adj_close"]).astype(
        {"code": object, "trade_date": "datetime64[ns]", "adj_close": float}
    )
    st, corr = features._statement_tables(fin, prices)
    assert len(st) == 2
    assert corr.empty


def test_財務情報がない銘柄は業績の特徴量が空(sample: psycopg.Connection[DictRow]) -> None:
    df = features.build_features(sample, date(2025, 12, 26), date(2025, 12, 26), [ALL_CODES[-1]])
    assert df[["sales_yoy", "op_progress", "equity_ratio", "days_since_earnings"]].isna().all().all()


def test_決算短信がなくても計算できる(sample: psycopg.Connection[DictRow]) -> None:
    sample.execute("DELETE FROM fin_summary")
    sample.execute("DELETE FROM earnings_date")
    df = features.build_features(sample, date(2025, 12, 26), date(2025, 12, 26), ALL_CODES)
    assert df["equity_ratio"].isna().all()
    assert df["days_to_earnings"].isna().all()


def test_銘柄がない場合や期間が逆の場合は空(sample: psycopg.Connection[DictRow]) -> None:
    assert features.build_features(sample, date(2025, 12, 26), date(2025, 12, 26), []).empty
    assert features.build_features(sample, date(2025, 12, 1), date(2025, 12, 26), ALL_CODES).empty
    # 株価がない期間
    assert features.build_features(sample, date(2020, 1, 10), date(2020, 1, 10), ALL_CODES).empty
    # 期間に、指定した銘柄の株価がない
    assert features.build_features(sample, date(2025, 12, 26), date(2025, 12, 26), ["99990"]).empty


# =====================================================================
# ゴールデンクロス・デッドクロス
# =====================================================================


def test_一目均衡表の特徴量() -> None:
    # 高値・安値・終値が毎日1ずつ上がる銘柄。t日目の値は t
    n = 100
    values = pd.Series(np.arange(1, n + 1, dtype=float))
    by = pd.Series(["a"] * n)
    result = features.ichimoku_features(values, values, values, by)
    t = float(n)
    tenkan, kijun = t - 4, t - 12.5  # 9日・26日の真ん中
    top, bottom = (t - 26 - 4 + t - 26 - 12.5) / 2, (t - 26 + t - 26 - 51) / 2  # 26日前の先行スパンA・B
    last = result.iloc[-1]
    assert last["ichimoku_tenkan_kijun"] == pytest.approx(tenkan / kijun - 1)
    assert last["ichimoku_cloud_pos"] == pytest.approx(t / top - 1)
    assert last["ichimoku_cloud_width"] == pytest.approx(top / bottom - 1)
    # 雲は 52 + 26 = 78日目から計算できる(52日分がそろった日から、26日先にずらすため)
    assert result["ichimoku_cloud_pos"].iloc[:77].isna().all() and result["ichimoku_cloud_pos"].iloc[77:].notna().all()
    # 下がり続ける銘柄は雲の下(マイナス)。雲の中なら 0
    down = features.ichimoku_features(values[::-1].reset_index(drop=True), values[::-1].reset_index(drop=True),
                                      values[::-1].reset_index(drop=True), by)  # fmt: skip
    assert down["ichimoku_cloud_pos"].iloc[-1] < 0
    flat = pd.Series([10.0] * n)
    inside = features.ichimoku_features(flat + 1, flat - 1, flat, by)
    assert inside["ichimoku_cloud_pos"].iloc[-1] == 0


def test_ボリンジャーバンドの収束の特徴量() -> None:
    # 前半の 80日は大きく上下し、後半の 20日はほとんど動かない銘柄(バンドが狭まる)
    rng = np.random.default_rng(0)
    wide = 100 + rng.normal(0, 5, 80)
    narrow = 100 + rng.normal(0, 0.5, 20)
    close = pd.Series(np.concatenate([wide, narrow]))
    by = pd.Series(["a"] * len(close))
    result = features.bollinger_features(close, by)
    # 幅 = 20日の標準偏差の4倍 ÷ 20日移動平均
    last20 = close.iloc[-20:]
    width = 4 * last20.std() / last20.mean()
    assert result["bb_width"].iloc[-1] == pytest.approx(width)
    # 収束 = 今の幅 ÷ 過去60日の幅の平均
    assert result["bb_squeeze"].iloc[-1] == pytest.approx(width / result["bb_width"].iloc[-60:].mean())
    assert result["bb_squeeze"].iloc[-1] < 0.5
    # 幅は20日目から、収束は 20 + 60 − 1 = 79日目から計算できる(MIN_HISTORY_DAYS の80日の中に収まる)
    assert result["bb_width"].iloc[:19].isna().all() and result["bb_width"].iloc[19:].notna().all()
    assert result["bb_squeeze"].iloc[:78].isna().all() and result["bb_squeeze"].iloc[78:].notna().all()
    assert features.BB_SQUEEZE_DAYS + 20 <= const.MIN_HISTORY_DAYS
    # 120日版は 20 + 120 − 1 = 139日目から計算できる。値は今の幅 ÷ 過去120日の幅の平均
    long_close = pd.Series(np.concatenate([100 + rng.normal(0, 5, 150), 100 + rng.normal(0, 0.5, 20)]))
    long = features.bollinger_features(long_close, pd.Series(["a"] * len(long_close)))
    assert long["bb_squeeze_120"].iloc[:138].isna().all() and long["bb_squeeze_120"].iloc[138:].notna().all()
    expected = long["bb_width"].iloc[-1] / long["bb_width"].iloc[-120:].mean()
    assert long["bb_squeeze_120"].iloc[-1] == pytest.approx(expected)
    # 特徴量の候補の名前は重複しない(bb_width は2つのグループにある)
    assert len(features.EXTRA_FEATURE_NAMES) == len(set(features.EXTRA_FEATURE_NAMES))


def test_ボリンジャーバンドの日数(monkeypatch: pytest.MonkeyPatch) -> None:
    # %b は days 日の移動平均 ± 標準偏差の2倍の中の位置
    rng = np.random.default_rng(1)
    close = pd.Series(100 + rng.normal(0, 3, 60))
    by = pd.Series(["a"] * len(close))
    for days in (20, 25):
        pctb = features.bollinger_pctb(close, by, days)
        last = close.iloc[-days:]
        lower = last.mean() - 2 * last.std()
        assert pctb.iloc[-1] == pytest.approx((close.iloc[-1] - lower) / (4 * last.std()))
        # days 日分の株価がそろうまでは空
        assert pctb.iloc[: days - 1].isna().all() and pctb.iloc[days - 1 :].notna().all()
    # 本番の日数は const.BB_DAYS(初期値 20)、候補は 25日
    assert const.BB_DAYS == 20 and features.BB_CANDIDATE_DAYS == 25
    assert "bb_pctb_25" in features.EXTRA_FEATURE_NAMES and "bb_pctb_25" not in features.FEATURE_NAMES
    # const.BB_DAYS を変えると、収束の特徴量の幅も同じ日数で計算する
    monkeypatch.setattr(const, "BB_DAYS", 25)
    width = features.bollinger_features(close, by)["bb_width"]
    last25 = close.iloc[-25:]
    assert width.iloc[:24].isna().all() and width.iloc[-1] == pytest.approx(4 * last25.std() / last25.mean())


def test_クロスからの日数() -> None:
    nan = float("nan")
    short = pd.Series([nan, 1, 2, 3, 1, 1, 1, 5, 5, nan, nan, 2, 0])
    long = pd.Series([nan, 2, 1, 1, 2, 2, 1, 1, 1, nan, nan, 1, 1])
    by = pd.Series(["a"] * 9 + ["b"] * 4)
    result = features.cross_days(short, long, by, 3).tolist()
    # 移動平均がそろった最初の日は、クロスした日が分からないので ±上限。同じ値の日は前の日の向きを続ける
    expected = [nan, -3, 1, 2, -1, -2, -3, 1, 2, nan, nan, 3, -1]
    assert [None if v != v else v for v in result] == [None if v != v else v for v in expected]


def test_指定日のクロスからの日数を株式分割を調整した終値で計算する(conn: psycopg.Connection[DictRow]) -> None:
    days = pd.bdate_range("2026-01-05", periods=100)
    # 96営業日は下がり続け、最後の4営業日で大きく上がる。途中で 1:2 の分割があっても、調整するのでクロスは起きない
    closes = [1000 - i * 2 for i in range(96)] + [810 + i * 60 for i in range(1, 5)]
    for i, (d, c) in enumerate(zip(days, closes, strict=True)):
        close = c / 2 if i >= 40 else c
        conn.execute(
            "INSERT INTO daily_price (trade_date, code, close, adj_factor) VALUES (%s, '12340', %s, %s)",
            (d.date(), close, Decimal("0.5") if i == 40 else Decimal(1)),
        )
    target = days[-1].date()
    frame = features.cross_days_on(conn, target, ["12340", "99990"], const.CROSS_DAYS_CAP)
    assert frame["code"].tolist() == ["12340"]
    short = float(frame["days_since_cross_5_25"].astype(float).iloc[0])
    mid = float(frame["days_since_cross_25_75"].astype(float).iloc[0])
    # 短期は最近ゴールデンクロスした(1〜5営業日前)。中長期はまだ下向きで、クロスしたのは上限より前
    assert 1 <= short <= const.CROSS_NOTE_DAYS
    assert mid == -const.CROSS_DAYS_CAP
    assert features.cross_days_on(conn, target, [], const.CROSS_DAYS_CAP).empty
    assert features.cross_days_on(conn, target, ["99990"], const.CROSS_DAYS_CAP).empty
