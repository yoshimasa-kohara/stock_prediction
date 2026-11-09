"""テスト用の架空のデータをDBに入れる部品。

銘柄ごとに、乱数で株価を作る(乱数の種を固定するので、毎回同じデータになる)。
1つの銘柄(11110)には、途中で1株を2株にする株式分割を入れる。
"""

from datetime import date, time, timedelta
from decimal import Decimal

import numpy as np
import psycopg
from psycopg.rows import DictRow

import const
from common import business_day

# 架空の銘柄(コード、33業種コード、市場区分コード)
SAMPLE_CODES: list[tuple[str, str, str]] = [
    (f"{i:04d}0", f"{3000 + (i % 4) * 50}", ["0111", "0112", "0113"][i % 3]) for i in range(1111, 1111 + 24)
]
# 株式分割を入れる銘柄と、権利落ち日
SPLIT_CODE: str = "11110"


def insert_sample_data(
    conn: psycopg.Connection[DictRow], start: date, end: date, seed: int = 1, split_date: date | None = None
) -> list[date]:
    """start〜end の営業日について、架空の株価・銘柄マスタ・バリュエーション指標・財務情報・決算発表予定日を入れる。

    Returns:
        株価を入れた営業日(古い順)
    """
    rng = np.random.default_rng(seed)
    days = business_day.list_business_days(start, end)
    price_rows: list[tuple[object, ...]] = []
    valuation_rows: list[tuple[object, ...]] = []
    for idx, (code, _sector, _market) in enumerate(SAMPLE_CODES):
        price = 500.0 + idx * 37.0
        drift = (idx % 5 - 2) * 0.0008
        for n, d in enumerate(days):
            factor = Decimal(1)
            if code == SPLIT_CODE and split_date is not None and d == split_date:
                # 1株を2株にする分割。株価は半分になり、調整係数は 0.5
                factor = Decimal("0.5")
                price /= 2
            ret = drift + rng.normal(0, 0.02)
            prev = price
            price = max(price * (1 + ret), 10.0)
            # 最後の銘柄は、ときどき取引がない日にする
            no_trade = idx == len(SAMPLE_CODES) - 1 and n % 17 == 5
            o = prev * (1 + rng.normal(0, 0.005))
            h = max(o, price) * (1 + abs(rng.normal(0, 0.005)))
            low = min(o, price) * (1 - abs(rng.normal(0, 0.005)))
            vol = int(rng.integers(50_000, 2_000_000))
            price_rows.append(
                (
                    d,
                    code,
                    None if no_trade else round(o, 1),
                    None if no_trade else round(h, 1),
                    None if no_trade else round(low, 1),
                    None if no_trade else round(price, 1),
                    None if no_trade else vol,
                    None if no_trade else int(vol * price),
                    factor,
                    ret > 0.035,
                    ret < -0.035,
                )
            )
            valuation_rows.append(
                (d, code, 50.0 + idx, 55.0 + idx, 900.0 + idx * 10, 0.08, 0.085, 10.0, 9.5, 1.1, 30000 + idx * 1000)
            )
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO stock_master (code, name, sector33_code, sector33_name, market_code, market_name, applied_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (code) DO NOTHING
            """,
            [(c, f"銘柄{c[:4]}", s, "業種", m, "市場", end) for c, s, m in SAMPLE_CODES],
        )
        with cur.copy(
            "COPY daily_price (trade_date, code, open, high, low, close, volume, turnover, adj_factor,"
            " upper_limit_flag, lower_limit_flag) FROM STDIN"
        ) as copy:
            for r in price_rows:
                copy.write_row(r)
        with cur.copy(
            "COPY daily_valuation (trade_date, code, eps, fwd_eps, bps, roe, fwd_roe, per, fwd_per, pbr, market_cap)"
            " FROM STDIN"
        ) as copy:
            for r in valuation_rows:
                copy.write_row(r)
        cur.executemany(
            "INSERT INTO fetched_date (target_date, data_type, status, record_count) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT DO NOTHING",
            [(d, const.DATA_TYPE_PRICE, const.FETCH_STATUS_FETCHED, len(SAMPLE_CODES)) for d in days],
        )
        # 財務情報: 四半期ごとの決算短信と、業績予想の修正
        fin_rows: list[tuple[object, ...]] = []
        for idx, (code, _, _) in enumerate(SAMPLE_CODES[:8]):
            for q, (per_type, per_en, disc) in enumerate(
                [
                    ("3Q", date(start.year - 1, 12, 31), start + timedelta(days=10)),
                    ("FY", date(start.year, 3, 31), start + timedelta(days=60)),
                    ("1Q", date(start.year, 6, 30), start + timedelta(days=130)),
                    ("3Q", date(start.year, 12, 31), start + timedelta(days=200)),
                ]
            ):
                fin_rows.append(
                    (
                        f"{idx}{q}",
                        code,
                        disc,
                        time(15, 0),
                        f"{per_type}FinancialStatements_Consolidated_JP",
                        per_type,
                        per_en,
                        date(start.year, 3, 31) if per_en <= date(start.year, 3, 31) else date(start.year + 1, 3, 31),
                        1000 * (q + 1) + idx,
                        100 * (q + 1) + idx,
                        0.4,
                        None if per_type == "FY" else 500 + idx,
                        None if per_type == "FY" else 20.0,
                    )
                )
            fin_rows.append(
                (
                    f"{idx}R",
                    code,
                    start + timedelta(days=150),
                    None,
                    "EarnForecastRevision",
                    "1Q",
                    None,
                    date(start.year + 1, 3, 31),
                    None,
                    None,
                    None,
                    550 + idx,
                    None,
                )
            )
        cur.executemany(
            """
            INSERT INTO fin_summary (disc_no, code, disc_date, disc_time, doc_type, cur_per_type, cur_per_en,
                cur_fy_en, sales, op, equity_ratio, f_op, f_div_ann)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            fin_rows,
        )
        cur.executemany(
            "INSERT INTO earnings_date (code, pub_date, fq_name, fye, sch_date) VALUES (%s, %s, %s, %s, %s)"
            " ON CONFLICT DO NOTHING",
            [
                (c, start + timedelta(days=100), "1Q", "0331", start + timedelta(days=130))
                for c, _, _ in SAMPLE_CODES[:8]
            ]
            + [(SAMPLE_CODES[0][0], start + timedelta(days=170), "3Q", "0331", None)],
        )
    return days
