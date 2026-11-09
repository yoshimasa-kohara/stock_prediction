"""API のテスト用の架空のデータ(画面のテスト専用。値は全て架空のもの)。

- 銘柄は6つ。11110〜11140 は予測の対象の市場、11145 は 11140 の優先株式(銘柄コードの前方一致の確かめ用)、
  13050 は対象外の市場(ETF。検索の scope の確かめ用)。
- 株価は LAST_DAY までの PRICE_DAYS 営業日分。乱数の種を固定するので、毎回同じデータになる。
- 予測は LAST_DAY を含む直近 PRED_DAYS 営業日分。一番古い予測日の行は、答え合わせ済み(VERIFIED)にする。
"""

from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import psycopg
from psycopg.rows import DictRow

import const
from common import business_day

# 最後の営業日(最新の予測日)
LAST_DAY: date = date(2026, 7, 22)
# 株価を入れる営業日の数(トレンドラインと75日移動平均が計算できる長さ)
PRICE_DAYS: int = 160
# 予測を入れる営業日の数
PRED_DAYS: int = 3
# モデルバージョン
MODEL_VERSION: str = "20260718"

# (コード, 銘柄名, 市場区分コード, 市場の名前, 33業種コード, 33業種名)
STOCKS: list[tuple[str, str, str, str, str, str]] = [
    ("11110", "サンプル銀行A", "0111", "プライム", "3050", "銀行業"),
    ("11120", "サンプル商事A", "0112", "スタンダード", "6050", "卸売業"),
    ("11130", "サンプル電機A", "0113", "グロース", "3650", "電気機器"),
    ("11140", "サンプル建設A", "0111", "プライム", "3050", "銀行業"),
    ("11145", "サンプル建設A(優先株式)", "0111", "プライム", "3050", "銀行業"),
    ("13050", "サンプルETF", "0109", "その他", "9999", "その他"),
]
# 予測する銘柄(対象の市場の銘柄)
PRED_CODES: list[str] = ["11110", "11120", "11130", "11140", "11145"]
# 買い候補の順位(主となる日数)。11130 は買い候補に入れない
BUY_RANKS: dict[str, int] = {"11110": 1, "11120": 2, "11140": 3}
# 買い候補(業種分散)の順位。11140 は入れず、11130 を入れる
BUY_RANKS_SECTOR: dict[str, int] = {"11110": 1, "11120": 2, "11130": 3}
# 銘柄コード → 33業種コード(予測した時点の業種として prediction に入れる)
SECTORS: dict[str, str] = {s[0]: s[4] for s in STOCKS}


def price_days() -> list[date]:
    """株価を入れる営業日(古い順)。"""
    start = LAST_DAY - timedelta(days=PRICE_DAYS * 2)
    days = business_day.list_business_days(start, LAST_DAY)
    return days[-PRICE_DAYS:]


def pred_days() -> list[date]:
    """予測を入れる営業日(古い順)。"""
    return price_days()[-PRED_DAYS:]


def insert_web_sample(conn: psycopg.Connection[DictRow]) -> None:
    """架空のデータを入れる。"""
    rng = np.random.default_rng(7)
    days = price_days()
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO stock_master (code, name, market_code, market_name, sector33_code, sector33_name,
                                      scale_category, applied_date)
            VALUES (%s, %s, %s, %s, %s, %s, 'TOPIX Small 1', %s)
            """,
            [(*s, LAST_DAY) for s in STOCKS],
        )
        prices: list[tuple[object, ...]] = []
        for idx, (code, *_rest) in enumerate(STOCKS):
            price = 1000.0 + idx * 300
            for n, d in enumerate(days):
                ret = (0.002 if idx % 2 == 0 else -0.001) + rng.normal(0, 0.015)
                prev = price
                price = max(price * (1 + ret), 10.0)
                # 11120 は売買代金が少ない(流動性注意)。11110 は最後の日にストップ高
                volume = 1_000 if code == "11120" else 500_000
                last = n == len(days) - 1
                prices.append(
                    (
                        d,
                        code,
                        round(prev, 1),
                        round(max(prev, price) * 1.004, 1),
                        round(min(prev, price) * 0.996, 1),
                        round(price, 1),
                        volume,
                        int(volume * price),
                        Decimal(1),
                        last and code == "11110",
                        False,
                    )
                )
        cur.executemany(
            """
            INSERT INTO daily_price (trade_date, code, open, high, low, close, volume, turnover, adj_factor,
                                     upper_limit_flag, lower_limit_flag)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            prices,
        )
        # バリュエーション指標(最後の日だけ)。11110 は PBR 0.4(割安)、11130 は赤字予想
        cur.executemany(
            """
            INSERT INTO daily_valuation (trade_date, code, eps, fwd_eps, bps, roe, fwd_roe, per, fwd_per, pbr,
                                         market_cap)
            VALUES (%s, %s, 100, %s, 2000, %s, 0.05, 10, %s, %s, 182000)
            """,
            [
                (LAST_DAY, "11110", 120, 0.086, 7.9, 0.4),
                (LAST_DAY, "11120", 80, 0.05, 15, 1.2),
                (LAST_DAY, "11130", -30, 0.02, None, 2.0),
                (LAST_DAY, "11140", 90, 0.07, 11.8, 0.9),
            ],
        )
        # 決算発表予定日
        cur.execute(
            "INSERT INTO earnings_date (code, pub_date, fq_name, fye, sch_date) VALUES (%s, %s, %s, %s, %s)",
            ("11110", LAST_DAY - timedelta(days=30), "1Q", "0331", LAST_DAY + timedelta(days=16)),
        )
        # モデル
        cur.execute(
            """
            INSERT INTO model (model_version, horizon, model_type, base_date, train_start_date, train_end_date,
                               train_rows, feature_names, file_path)
            VALUES (%s, %s, %s, '2026-07-18', '2024-07-18', '2026-07-18', 1000, '[]', 'models/x.txt')
            """,
            (MODEL_VERSION, const.MAIN_HORIZON, const.MODEL_TYPE_UP_PROB),
        )
        # 予測
        closes = {(r[0], r[1]): r[5] for r in prices}
        preds: list[tuple[object, ...]] = []
        for k, d in enumerate(pred_days()):
            for i, code in enumerate(PRED_CODES):
                base = float(closes[(d, code)])  # type: ignore[arg-type]
                for j, h in enumerate(const.PREDICT_HORIZONS):
                    target = business_day.business_days_after(d, h)
                    verified = k == 0 and h == const.PREDICT_HORIZONS[0]
                    actual = round(base * (1.05 if i % 2 == 0 else 0.97), 4) if verified else None
                    up = 0.62 - i * 0.04 + j * 0.01
                    preds.append(
                        (
                            code,
                            d,
                            h,
                            target,
                            round(up, 4),
                            round(0.80 - i * 0.07, 4),
                            round(base * (1.04 - i * 0.01), 4),
                            base,
                            MODEL_VERSION,
                            SECTORS[code],
                            BUY_RANKS.get(code),
                            BUY_RANKS_SECTOR.get(code),
                            actual,
                            None if actual is None else round((actual - base) / base, 6),
                            None if actual is None else (actual > base) == (up >= const.UP_PROB_THRESHOLD),
                            const.VERIFY_STATUS_VERIFIED if verified else const.VERIFY_STATUS_PENDING,
                        )
                    )
        cur.executemany(
            """
            INSERT INTO prediction (code, pred_date, horizon, target_date, up_prob, rel_prob, pred_price, base_price,
                                    model_version, sector33_code, buy_rank, buy_rank_sector, actual_price,
                                    change_rate, is_hit, verify_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            preds,
        )
        # 予測日ごとの集計(一番古い予測日の、最初の予測日数)と、期間の集計・上昇確率の確かさ(最新の日)
        first, h0 = pred_days()[0], const.PREDICT_HORIZONS[0]
        # (対象区分, 検証した件数, 的中, 市場の中央値を上回った件数, 差の合計, 業種比の件数, 業種の中央値を上回った件数,
        #  業種の中央値との差の合計)
        for segment, n_verified, hit, beat, excess, n_sector, sector_beat, sector_excess in (
            (const.SEGMENT_ALL, 5, 3, 2, Decimal("0"), 5, 2, Decimal("0")),
            (const.SEGMENT_TOP, 3, 2, 2, Decimal("0.09"), 3, 2, Decimal("0.03")),
            (const.SEGMENT_TOP_SECTOR, 3, 2, 1, Decimal("0.03"), 3, 2, Decimal("0.06")),
            (const.SEGMENT_HIGH_PROB, 0, 0, 0, Decimal("0"), 0, 0, Decimal("0")),
        ):
            cur.execute(
                """
                INSERT INTO verify_daily_summary (pred_date, horizon, segment, target_date, model_version,
                    verified_count, hit_count, up_count, beat_count, sum_change_rate, sum_excess_rate, market_median,
                    sector_verified_count, sector_beat_count, sum_sector_excess_rate, sum_price_error_rate,
                    unverifiable_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0.1, %s, 0.01, %s, %s, %s, 0.2, 0)
                """,
                (first, h0, segment, business_day.business_days_after(first, h0), MODEL_VERSION, n_verified, hit, 3,
                 beat, excess, n_sector, sector_beat, sector_excess),
            )  # fmt: skip
        for period in const.VERIFY_SUMMARY_PERIODS:
            for segment, count, rate in (
                (const.SEGMENT_ALL, 1000, "0.538"),
                (const.SEGMENT_TOP, 60, "0.62"),
                (const.SEGMENT_TOP_SECTOR, 90, "0.6"),
                (const.SEGMENT_HIGH_PROB, 30, "0.7"),
            ):
                cur.execute(
                    """
                    INSERT INTO verify_period_summary (summary_date, period_type, horizon, segment, verified_count,
                        hit_count, hit_rate, up_rate, beat_rate, avg_change_rate, avg_excess_rate, sector_beat_rate,
                        avg_sector_excess_rate, avg_price_error_rate)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 0.5, 0.785, 0.01, 0.059, 0.579, 0.0185, 0.04)
                    """,
                    (LAST_DAY, period, const.MAIN_HORIZON, segment, count, int(count * float(rate)), rate),
                )
            for band, n_band, avg, rate_up in ((50, 800, "0.55", "0.53"), (60, 900, "0.634", "0.454")):
                cur.execute(
                    """
                    INSERT INTO verify_calibration_summary (summary_date, period_type, horizon, prob_band,
                        verified_count, up_count, avg_up_prob, up_rate, gap)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (LAST_DAY, period, const.MAIN_HORIZON, band, n_band, int(n_band * float(rate_up)), avg, rate_up,
                     Decimal(rate_up) - Decimal(avg)),
                )  # fmt: skip


def add_holding(
    conn: psycopg.Connection[DictRow],
    code: str,
    buy_price: str,
    quantity: int,
    buy_date: date,
    sell_date: date | None = None,
    sell_price: str | None = None,
) -> int:
    """保有銘柄を1行入れ、holding_id を返す。"""
    row = conn.execute(
        """
        INSERT INTO holding (code, buy_price, quantity, buy_date, sell_date, sell_price)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING holding_id
        """,
        (code, Decimal(buy_price), quantity, buy_date, sell_date, None if sell_price is None else Decimal(sell_price)),
    ).fetchone()
    assert row is not None
    return int(row["holding_id"])
