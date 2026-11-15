"""画面イメージを撮るための架空の値を、テスト用の DB(stock_prediction_test)に入れる。

使い方(stock_prediction_web のフォルダで):
    .venv\\Scripts\\python -m screenshot.make_sample_db

- テスト用の DB の全てのテーブルを作り直してから入れる(tests/conftest.py と同じ方法)。本番の DB には接続しない。
- 値は全て架空のもの。銘柄名は「サンプル○○」、銘柄コードは 11110〜(実在の銘柄と重ならない範囲)。
  画面イメージは GitHub に公開するポートフォリオにも使うので、実在の会社名・人名・メールアドレス・PC のパスを入れない。
- 基準の日(最新の予測日)は BASE_DAY。乱数の種を固定するので、毎回同じ値になる。
- 予測の答え合わせ(prediction の検証用のカラムと、検証の集計テーブル)は、バッチの検証(core.verify_core.verify)で作る。
  買い候補の順位はバッチの予測の関数(core.predict_core.rank_candidates など)で付ける。
"""

import logging
import math
import sys
import time as time_mod
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd
import psycopg
from psycopg.rows import DictRow

import const
from common import db
from common.business_day import list_business_days_by_rule
from common.logger import BatchLogger
from core.predict_core import rank_candidates, rank_candidates_sector
from core.verify_core import verify
from screenshot.db_switch import check_test_db, use_test_db

# テストの補助の関数(tests/web_sample.py)を使う。tests は __init__.py を置かないフォルダなので、パスに足して読む
_TESTS_DIR = Path(__file__).resolve().parent.parent / "tests"
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
from web_sample import add_fin, add_holding  # noqa: E402

# バッチのテーブルの作成の SQL(tests/conftest.py と同じもの)
_BATCH_DIR: Path = Path(__file__).resolve().parent.parent.parent / "stock_prediction_batch"
_CREATE_SQL: Path = _BATCH_DIR / "etc" / "db" / "sql" / "create_tables.sql"

# =====================================================================
# 期間と乱数
# =====================================================================

# 基準の日(最新の予測日。金曜日)
BASE_DAY: date = date(2026, 11, 13)
# 株価を入れる最初の日(チャートの 1Y と ALL、75日移動平均のため、予測の期間より前から入れる)
PRICE_START: date = date(2024, 10, 1)
# 取引カレンダーの期間(60営業日後の予測の対象日が入るように、翌年の終わりまで)
CALENDAR_START: date = date(2024, 1, 1)
CALENDAR_END: date = date(2027, 12, 31)
# 予測を入れる営業日の数(基準の日を含む。約1年)
PRED_DAY_COUNT: int = 245
# 検証を1日ずつ行う日数(基準の日を含む直近の日)。それより前は1回でまとめて答え合わせをする
VERIFY_DAILY_DAYS: int = 25
# 乱数の種
SEED: int = 20261113
# 日本時間
JST: timezone = timezone(timedelta(hours=9))

# 学習の基準日(モデルバージョン)の間隔(週)。モデルは土曜日に学習する
MODEL_INTERVAL_WEEKS: int = 4
# 予測のモデルの最初の学習の基準日
FIRST_MODEL_DAY: date = date(2025, 10, 18)

# 相場全体の、期間ごとの1日の平均の傾き(古い順。上げた時期と下げた時期を交互に入れ、直近は小幅に上げる)
MARKET_DRIFTS: list[float] = [
    0.0005, -0.0004, 0.0008, -0.0007, 0.0004, 0.0009, 0.0006, -0.0005, 0.0007, -0.0003, 0.0001, 0.0001,
]  # fmt: skip

# 銘柄ごとの値動きの、前の日の位置を残す割合(1 に近いほど、一方向の動きが続く)
IDIO_KEEP: float = 0.97

# 予測の作り方の係数。上昇確率 = シグモイド(PROB_SCALE × (FUTURE_WEIGHT × 実際の値動き + LATENT_WEIGHT × 銘柄ごとの
# ゆっくり変わる値 + NOISE_WEIGHT × ばらつき))。実際の値動きの重みで、的中率が 50〜65% あたりになるようにした
PROB_SCALE: float = 0.38
FUTURE_WEIGHT: float = 0.17
LATENT_WEIGHT: float = 0.85
NOISE_WEIGHT: float = 0.45
LATENT_KEEP: float = 0.92

# =====================================================================
# 銘柄
# =====================================================================

# 33業種: コード → (業種名, 株価の下限, 株価の上限, PER, PBR, 営業利益率, 1日の値動きの大きさ)
SECTORS: dict[str, tuple[str, int, int, float, float, float, float]] = {
    "0050": ("水産・農林業", 2000, 4000, 11, 1.0, 0.04, 0.014),
    "1050": ("鉱業", 1500, 3000, 8, 0.6, 0.25, 0.020),
    "2050": ("建設業", 1000, 3500, 12, 1.0, 0.06, 0.013),
    "3050": ("食料品", 2000, 6000, 20, 1.6, 0.07, 0.011),
    "3100": ("繊維製品", 600, 1500, 14, 0.8, 0.06, 0.015),
    "3150": ("パルプ・紙", 500, 1200, 13, 0.6, 0.04, 0.015),
    "3200": ("化学", 1500, 5000, 15, 1.2, 0.10, 0.015),
    "3250": ("医薬品", 2500, 7000, 22, 2.0, 0.15, 0.016),
    "3300": ("石油・石炭製品", 700, 1500, 9, 0.7, 0.04, 0.018),
    "3350": ("ゴム製品", 2000, 4500, 11, 1.0, 0.09, 0.014),
    "3400": ("ガラス・土石製品", 1500, 4000, 12, 0.9, 0.08, 0.015),
    "3450": ("鉄鋼", 1200, 3000, 8, 0.45, 0.07, 0.019),
    "3500": ("非鉄金属", 2000, 5000, 12, 0.9, 0.07, 0.019),
    "3550": ("金属製品", 1000, 2500, 13, 0.9, 0.07, 0.014),
    "3600": ("機械", 2000, 8000, 17, 1.6, 0.11, 0.018),
    "3650": ("電気機器", 2000, 12000, 22, 2.2, 0.11, 0.021),
    "3700": ("輸送用機器", 1500, 4000, 9, 0.8, 0.07, 0.017),
    "3750": ("精密機器", 2500, 9000, 25, 2.8, 0.15, 0.019),
    "3800": ("その他製品", 1500, 6000, 18, 1.5, 0.09, 0.016),
    "4050": ("電気・ガス業", 1000, 3000, 9, 0.6, 0.06, 0.013),
    "5050": ("陸運業", 1500, 4000, 16, 1.3, 0.10, 0.011),
    "5100": ("海運業", 3000, 6000, 6, 0.8, 0.12, 0.022),
    "5150": ("空運業", 2000, 3500, 14, 1.2, 0.08, 0.013),
    "5200": ("倉庫・運輸関連業", 1500, 4000, 13, 1.0, 0.08, 0.012),
    "5250": ("情報・通信業", 800, 6000, 24, 2.8, 0.13, 0.019),
    "6050": ("卸売業", 1500, 5000, 10, 1.1, 0.04, 0.014),
    "6100": ("小売業", 1500, 8000, 22, 2.4, 0.06, 0.014),
    "7050": ("銀行業", 500, 2500, 10, 0.45, 0.25, 0.016),
    "7100": ("証券、商品先物取引業", 600, 1500, 11, 0.9, 0.20, 0.018),
    "7150": ("保険業", 2000, 5000, 12, 1.2, 0.08, 0.015),
    "7200": ("その他金融業", 500, 2000, 10, 1.0, 0.15, 0.016),
    "8050": ("不動産業", 1000, 3000, 11, 1.1, 0.15, 0.015),
    "9050": ("サービス業", 1000, 5000, 25, 3.0, 0.10, 0.018),
}

# 市場区分: コード → 名前
MARKETS: dict[str, str] = {"0111": "プライム", "0112": "スタンダード", "0113": "グロース"}
PRIME, STANDARD, GROWTH = "0111", "0112", "0113"

# 業績の型(財務情報の作り方)
PROFILE_NORMAL: str = "normal"  # 黒字で、ゆるやかに伸びる
PROFILE_TURNAROUND: str = "turnaround"  # 前の期は赤字で、直近の期に黒字化
PROFILE_LOSS_SHRINK: str = "loss_shrink"  # 赤字が続くが縮小している(予想も赤字)
PROFILE_TO_LOSS: str = "to_loss"  # 直近の期に赤字化(予想は黒字に戻る)

# 見た目のための銘柄の性質
LOW_LIQUIDITY: str = "low_liquidity"  # 売買代金が少ない(流動性注意)
UPPER_LIMIT: str = "upper_limit"  # 基準の日にストップ高


@dataclass(frozen=True)
class StockDef:
    """架空の銘柄の定義。"""

    name: str
    sector: str
    market: str
    profile: str = PROFILE_NORMAL
    flags: tuple[str, ...] = ()


# 銘柄の一覧(この順に銘柄コード 11110、11120、… を付ける)
STOCK_DEFS: list[StockDef] = [
    StockDef("サンプル水産", "0050", PRIME),
    StockDef("サンプル資源開発", "1050", PRIME),
    StockDef("サンプル建設", "2050", PRIME),
    StockDef("サンプル住宅", "2050", PRIME),
    StockDef("サンプル工務店", "2050", STANDARD, flags=(LOW_LIQUIDITY,)),
    StockDef("サンプル食品", "3050", PRIME),
    StockDef("サンプル製菓", "3050", PRIME),
    StockDef("サンプル乳業", "3050", STANDARD),
    StockDef("サンプル繊維", "3100", PRIME),
    StockDef("サンプル製紙", "3150", PRIME),
    StockDef("サンプル化学", "3200", PRIME),
    StockDef("サンプル化成", "3200", PRIME),
    StockDef("サンプル塗料", "3200", STANDARD),
    StockDef("サンプル製薬", "3250", PRIME),
    StockDef("サンプル創薬", "3250", GROWTH, PROFILE_LOSS_SHRINK),
    StockDef("サンプル石油", "3300", PRIME),
    StockDef("サンプルゴム", "3350", PRIME),
    StockDef("サンプル窯業", "3400", PRIME),
    StockDef("サンプル製鋼", "3450", PRIME),
    StockDef("サンプル特殊鋼", "3450", STANDARD),
    StockDef("サンプル電線", "3500", PRIME),
    StockDef("サンプル金属", "3550", STANDARD),
    StockDef("サンプル機械", "3600", PRIME),
    StockDef("サンプル重工", "3600", PRIME),
    StockDef("サンプル精機", "3600", PRIME),
    StockDef("サンプル工作機", "3600", STANDARD, PROFILE_TURNAROUND),
    StockDef("サンプル電機", "3650", PRIME),
    StockDef("サンプル電子", "3650", PRIME),
    StockDef("サンプル半導体", "3650", PRIME),
    StockDef("サンプル電子部品", "3650", STANDARD),
    StockDef("サンプルセンサー", "3650", GROWTH, flags=(UPPER_LIMIT,)),
    StockDef("サンプル自動車", "3700", PRIME),
    StockDef("サンプル車体", "3700", PRIME, PROFILE_TO_LOSS),
    StockDef("サンプル自動車部品", "3700", STANDARD),
    StockDef("サンプル光学", "3750", PRIME),
    StockDef("サンプル計器", "3750", STANDARD),
    StockDef("サンプル玩具", "3800", PRIME),
    StockDef("サンプル楽器", "3800", STANDARD),
    StockDef("サンプル電力", "4050", PRIME),
    StockDef("サンプルガス", "4050", PRIME),
    StockDef("サンプル鉄道", "5050", PRIME),
    StockDef("サンプル運輸", "5050", PRIME),
    StockDef("サンプル海運", "5100", PRIME),
    StockDef("サンプル航空", "5150", PRIME),
    StockDef("サンプル倉庫", "5200", STANDARD, flags=(LOW_LIQUIDITY,)),
    StockDef("サンプル通信", "5250", PRIME),
    StockDef("サンプルソフト", "5250", PRIME),
    StockDef("サンプルシステムズ", "5250", PRIME),
    StockDef("サンプルクラウド", "5250", GROWTH, PROFILE_TURNAROUND),
    StockDef("サンプルネット", "5250", GROWTH, PROFILE_LOSS_SHRINK),
    StockDef("サンプルゲームス", "5250", GROWTH),
    StockDef("サンプル商事", "6050", PRIME),
    StockDef("サンプル物産", "6050", PRIME),
    StockDef("サンプル産業", "6050", STANDARD),
    StockDef("サンプルストア", "6100", PRIME),
    StockDef("サンプルドラッグ", "6100", PRIME),
    StockDef("サンプル百貨店", "6100", STANDARD, PROFILE_TO_LOSS),
    StockDef("サンプル銀行", "7050", PRIME),
    StockDef("サンプル信託銀行", "7050", PRIME),
    StockDef("サンプル地方銀行", "7050", STANDARD),
    StockDef("サンプル証券", "7100", PRIME),
    StockDef("サンプル保険", "7150", PRIME),
    StockDef("サンプルリース", "7200", PRIME),
    StockDef("サンプル不動産", "8050", PRIME),
    StockDef("サンプル地所", "8050", STANDARD),
    StockDef("サンプル人材", "9050", PRIME),
    StockDef("サンプルサービス", "9050", PRIME),
    StockDef("サンプル介護", "9050", GROWTH, PROFILE_LOSS_SHRINK),
]

# 実行履歴の失敗の行に入れる、エラーの内容(架空のもの)
FAILED_MESSAGE: str = "J-Quants の API がタイムアウトしました(架空のエラー)。3回やり直しても応答がありません"

# 最初の銘柄コードと、銘柄コードの間隔(5桁目は 0。画面には4桁で出る)
FIRST_CODE: int = 11110
CODE_STEP: int = 10

# 時価総額の範囲(百万円)
MARKET_CAP_RANGE: dict[str, tuple[float, float]] = {
    PRIME: (80_000, 2_500_000),
    STANDARD: (4_000, 40_000),
    GROWTH: (3_000, 50_000),
}
# 1日の売買代金の平均の目安(億円)の範囲
TURNOVER_RANGE: dict[str, tuple[float, float]] = {PRIME: (5, 60), STANDARD: (1.5, 6), GROWTH: (2, 15)}
# 規模の区分(TOPIX の区分)
SCALE_BY_MARKET: dict[str, str] = {PRIME: "TOPIX Mid400", STANDARD: "TOPIX Small 2", GROWTH: "-"}

# 予測の見た目の調整: 銘柄名 → 基準の日を含む直近の何営業日に、銘柄ごとのゆっくり変わる値に足す値
# (トップ・買い候補に、ストップ高・流動性注意の銘柄が出るように。ランキングの「確率が急に上がった銘柄」も出す)
LATENT_BOOSTS: dict[str, tuple[int, float]] = {
    "サンプルセンサー": (1, 2.4),
    "サンプル工務店": (8, 1.8),
    "サンプル半導体": (3, 1.2),
    "サンプル光学": (1, 1.6),
}


@dataclass
class Stock:
    """作った銘柄の値(株価は営業日の順の配列)。"""

    code: str
    d: StockDef
    vol: float
    shares: float
    per: float
    pbr: float
    margin: float
    close: np.ndarray = field(default_factory=lambda: np.zeros(0))
    open_: np.ndarray = field(default_factory=lambda: np.zeros(0))
    high: np.ndarray = field(default_factory=lambda: np.zeros(0))
    low: np.ndarray = field(default_factory=lambda: np.zeros(0))
    volume: np.ndarray = field(default_factory=lambda: np.zeros(0))


@dataclass
class SampleData:
    """作った値のまとめ(capture.py と、結果の確かめに使う)。"""

    days: list[date]  # 株価がある営業日(古い順)
    pred_days: list[date]  # 予測日(古い順)
    stocks: list[Stock]
    counts: dict[str, int] = field(default_factory=dict)

    def stock(self, name: str) -> Stock:
        return next(s for s in self.stocks if s.d.name == name)

    def close_on(self, code: str, day: date) -> float:
        s = next(s for s in self.stocks if s.code == code)
        return float(s.close[self.days.index(day)])


# =====================================================================
# 作る処理
# =====================================================================


def recreate_tables(conn: psycopg.Connection[DictRow]) -> None:
    """テスト用の DB の全てのテーブルを作り直す(tests/conftest.py の _create_tables と同じ)。"""
    check_test_db(conn)
    conn.execute("DROP SCHEMA public CASCADE")
    conn.execute("CREATE SCHEMA public")
    conn.execute(_CREATE_SQL.read_text(encoding="utf-8").encode("utf-8"))


def tick_round(price: float) -> float:
    """株価を、呼値(よびね)に近い単位で丸める(3000円未満は1円、それ以上は5円)。"""
    unit = 1.0 if price < 3000 else 5.0
    return max(unit, round(price / unit) * unit)


def make_stocks(rng: np.random.Generator, days: list[date]) -> list[Stock]:
    """銘柄と株価日足を作る。市場全体・業種・銘柄ごとの値動きを足し合わせ、最後の終値が業種の範囲に入るようにする。"""
    n = len(days)
    # 相場全体の値動き。期間を MARKET_DRIFTS の数に分け、期間ごとの平均の傾きを決める(ばらつきは乱数)
    market = rng.normal(0, 0.009, n)
    for idx, drift in zip(np.array_split(np.arange(n), len(MARKET_DRIFTS)), MARKET_DRIFTS, strict=True):
        market[idx] += drift - market[idx].mean()
    sector_moves = {code: rng.normal(0, 0.006, n) for code in SECTORS}
    stocks: list[Stock] = []
    for i, d in enumerate(STOCK_DEFS):
        _name, lo, hi, per, pbr, margin, vol = SECTORS[d.sector]
        vol *= 1.3 if d.market == GROWTH else 1.0
        beta = rng.uniform(0.7, 1.3)
        # 銘柄ごとの値動きは、平均に戻る動き(IDIO_KEEP)にする。一方向に上がり(下がり)続ける銘柄ばかりになると、
        # 銘柄別の的中率が 100% に近い値ばかりになるため
        idio = np.zeros(n)
        shocks = rng.normal(0, vol, n)
        for k in range(1, n):
            idio[k] = IDIO_KEEP * idio[k - 1] + shocks[k]
        rets = beta * market + sector_moves[d.sector] + np.diff(idio, prepend=0.0)
        path = np.exp(np.cumsum(rets))
        final = math.exp(rng.uniform(math.log(lo), math.log(hi)))
        if d.market == GROWTH:
            final = min(final, 1800.0)
        if UPPER_LIMIT in d.flags:
            final = 650.0
        close = path / path[-1] * final
        if UPPER_LIMIT in d.flags:
            # 基準の日にストップ高(500円以上700円未満の値幅の制限は100円)。前の日は550円
            close[:-1] *= 550.0 / close[-2]
            close[-1] = 650.0
        cap_lo, cap_hi = MARKET_CAP_RANGE[d.market]
        cap = math.exp(rng.uniform(math.log(cap_lo), math.log(cap_hi)))
        stock = Stock(
            code=str(FIRST_CODE + i * CODE_STEP),
            d=d,
            vol=vol,
            shares=cap * 1_000_000 / final,
            per=per * math.exp(rng.normal(0, 0.25)),
            pbr=pbr * math.exp(rng.normal(0, 0.25)),
            margin=margin * math.exp(rng.normal(0, 0.2)),
        )
        stock.close = np.array([tick_round(c) for c in close])
        prev = np.concatenate([[stock.close[0]], stock.close[:-1]])
        gap = rng.normal(0, vol * 0.3, n)
        stock.open_ = np.array([tick_round(p * (1 + g)) for p, g in zip(prev, gap, strict=True)])
        top = np.maximum(stock.open_, stock.close)
        bottom = np.minimum(stock.open_, stock.close)
        wick_up, wick_down = rng.normal(0, vol * 0.4, n), rng.normal(0, vol * 0.4, n)
        stock.high = np.array([tick_round(t * (1 + abs(e))) for t, e in zip(top, wick_up, strict=True)])
        stock.low = np.array([tick_round(b * (1 - abs(e))) for b, e in zip(bottom, wick_down, strict=True)])
        if UPPER_LIMIT in d.flags:
            stock.high[-1] = stock.close[-1]
        t_lo, t_hi = (0.2, 0.45) if LOW_LIQUIDITY in d.flags else TURNOVER_RANGE[d.market]
        turnover = math.exp(rng.uniform(math.log(t_lo), math.log(t_hi))) * 100_000_000
        move = np.abs(np.diff(np.log(stock.close), prepend=math.log(stock.close[0])))
        daily = turnover * np.exp(rng.normal(0, 0.35, n)) * (1 + move / vol * 0.3)
        unit = 100
        stock.volume = np.maximum(np.round(daily / stock.close / unit) * unit, unit)
        stocks.append(stock)
    return stocks


def make_index(rng: np.random.Generator, stocks: list[Stock], n: int) -> np.ndarray:
    """日経平均(架空の値)。銘柄の値動きの平均に、少しのばらつきを足す。最後の値を 46,000 円台にする。"""
    rets = np.mean([np.diff(np.log(s.close), prepend=math.log(s.close[0])) for s in stocks], axis=0)
    rets = rets + rng.normal(0, 0.002, n)
    path = np.exp(np.cumsum(rets))
    result: np.ndarray = path / path[-1] * 46_382.15
    return result


def insert_master_and_prices(
    conn: psycopg.Connection[DictRow], days: list[date], stocks: list[Stock], index: np.ndarray
) -> None:
    """銘柄マスタ、取引カレンダー、取得済みの日、株価日足、日経平均を入れる。"""
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO stock_master (code, name, sector33_code, sector33_name, scale_category, market_code,
                                      market_name, applied_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (s.code, s.d.name, s.d.sector, SECTORS[s.d.sector][0], SCALE_BY_MARKET[s.d.market], s.d.market,
                 MARKETS[s.d.market], BASE_DAY)
                for s in stocks
            ],
        )  # fmt: skip
        business = set(list_business_days_by_rule(CALENDAR_START, CALENDAR_END))
        cal = [
            (CALENDAR_START + timedelta(days=i), "1" if CALENDAR_START + timedelta(days=i) in business else "0")
            for i in range((CALENDAR_END - CALENDAR_START).days + 1)
        ]
        cur.executemany(
            "INSERT INTO trading_calendar (cal_date, holiday_division, is_business_day) VALUES (%s, %s, %s)",
            [(d, div, div == "1") for d, div in cal],
        )
        fetched = [
            (d, data_type, const.FETCH_STATUS_FETCHED, len(stocks))
            for d in days
            for data_type in (const.DATA_TYPE_PRICE, const.DATA_TYPE_VALUATION)
        ]
        cur.executemany(
            "INSERT INTO fetched_date (target_date, data_type, status, record_count) VALUES (%s, %s, %s, %s)", fetched
        )
        with cur.copy(
            "COPY daily_price (trade_date, code, open, high, low, close, volume, turnover, adj_factor,"
            " upper_limit_flag, lower_limit_flag) FROM STDIN"
        ) as copy:
            for s in stocks:
                for k, d in enumerate(days):
                    close = float(s.close[k])
                    volume = int(s.volume[k])
                    upper = UPPER_LIMIT in s.d.flags and k == len(days) - 1
                    copy.write_row(
                        (d, s.code, s.open_[k], s.high[k], s.low[k], close, volume,
                         int(volume * (s.high[k] + s.low[k] + close * 2) / 4), 1, upper, False)
                    )  # fmt: skip
        with cur.copy("COPY index_daily_price (index_code, trade_date, open, high, low, close) FROM STDIN") as copy:
            prev = float(index[0])
            for k, d in enumerate(days):
                c = float(index[k])
                copy.write_row(
                    (const.INDEX_CODE_N225, d, round(prev, 2), round(max(prev, c) * 1.003, 2),
                     round(min(prev, c) * 0.997, 2), round(c, 2))
                )  # fmt: skip
                prev = c


def model_versions(pred_days: list[date]) -> list[date]:
    """学習の基準日(4週ごとの土曜日)。最初の予測日より前の1つから、基準の日までのもの。"""
    result: list[date] = []
    d = FIRST_MODEL_DAY
    while d <= BASE_DAY:
        result.append(d)
        d += timedelta(weeks=MODEL_INTERVAL_WEEKS)
    assert result[0] < pred_days[0]
    return result


def insert_models(conn: psycopg.Connection[DictRow], versions: list[date]) -> None:
    """モデル(学習の結果)の行を入れる。ファイルは作らない(画面はファイルを読まない)。"""
    rows = []
    for v in versions:
        ver = v.strftime("%Y%m%d")
        for h in const.PREDICT_HORIZONS:
            for model_type in const.MODEL_TYPES:
                rows.append(
                    (ver, h, model_type, v, v.replace(year=v.year - const.TRAIN_PERIOD_YEARS), v - timedelta(days=1),
                     1_850_000 + (v.toordinal() % 97) * 1000, '["ret_5", "ret_20", "ma_gap_25", "volume_ratio_20"]',
                     f"models/{ver}/{model_type}_{h}.txt")
                )  # fmt: skip
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO model (model_version, horizon, model_type, base_date, train_start_date, train_end_date,
                               train_rows, feature_names, file_path)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )


def sigmoid(x: np.ndarray) -> np.ndarray:
    result: np.ndarray = 1.0 / (1.0 + np.exp(-x))
    return result


def make_predictions(
    rng: np.random.Generator,
    days: list[date],
    pred_days: list[date],
    future_days: list[date],
    stocks: list[Stock],
    versions: list[date],
) -> list[tuple[object, ...]]:
    """予測(上昇確率・市場比の確率・予測株価・買い候補の順位)を作る。

    上昇確率・市場比の確率は、実際のその後の値動き(株価がまだない日は乱数)と、銘柄ごとにゆっくり変わる値と、
    ばらつきから作る。買い候補の順位は、バッチの予測と同じ関数(rank_candidates、rank_candidates_sector)で付ける。
    """
    all_days = days + future_days
    close = np.array([s.close for s in stocks])  # 銘柄 × 営業日
    vols = np.array([s.vol for s in stocks])
    first = days.index(pred_days[0])
    latent = rng.normal(0, 1, len(stocks))
    # 業種ごとにゆっくり変わる値(同じ業種の銘柄が買い候補に並ぶことがあるように)
    sector_codes = sorted({s.d.sector for s in stocks})
    sector_of = np.array([sector_codes.index(s.d.sector) for s in stocks])
    sector_latent = rng.normal(0, 1, len(sector_codes))
    names = [s.d.name for s in stocks]
    rows: list[tuple[object, ...]] = []
    for k, d in enumerate(pred_days):
        t = first + k
        latent = LATENT_KEEP * latent + math.sqrt(1 - LATENT_KEEP**2) * rng.normal(0, 1, len(stocks))
        sector_latent = LATENT_KEEP * sector_latent + math.sqrt(1 - LATENT_KEEP**2) * rng.normal(
            0, 1, len(sector_codes)
        )
        boosted = 0.8 * latent + 0.6 * sector_latent[sector_of]
        for name, (last_n, add) in LATENT_BOOSTS.items():
            if k >= len(pred_days) - last_n:
                boosted[names.index(name)] += add
        day_bias = rng.normal(0, 0.08)
        version = max(v for v in versions if v < d).strftime("%Y%m%d")
        per_h: dict[int, pd.DataFrame] = {}
        for h in const.PREDICT_HORIZONS:
            if t + h < len(days):
                ret = np.log(close[:, t + h] / close[:, t])
                z = ret / (vols * math.sqrt(h))
                rel = (ret - np.median(ret)) / (vols * math.sqrt(h))
            else:
                z = rng.normal(0, 1, len(stocks))
                rel = z - np.median(z)
            noise = rng.normal(0, 1, len(stocks))
            up = sigmoid(PROB_SCALE * (FUTURE_WEIGHT * z + LATENT_WEIGHT * boosted + NOISE_WEIGHT * noise) + day_bias)
            noise_rel = rng.normal(0, 1, len(stocks))
            relp = sigmoid(PROB_SCALE * (FUTURE_WEIGHT * rel + LATENT_WEIGHT * boosted + NOISE_WEIGHT * noise_rel))
            base = close[:, t]
            expected = (up - 0.5) * 2 * vols * math.sqrt(h) * 0.9 + rng.normal(0, 0.002, len(stocks))
            per_h[h] = pd.DataFrame(
                {
                    "code": [s.code for s in stocks],
                    "up_prob": np.round(np.clip(up, 0.05, 0.95), 4),
                    "rel_prob": np.round(np.clip(relp, 0.05, 0.95), 4),
                    "pred_price": np.round(base * (1 + expected), 4),
                    "base_price": base,
                    "sector33_code": [s.d.sector for s in stocks],
                }
            )
        main = per_h[const.MAIN_HORIZON]
        ranks = rank_candidates(main)
        ranks_sector = rank_candidates_sector(main)
        for h, df in per_h.items():
            target = all_days[t + h]
            for r in df.itertuples(index=False):
                rows.append(
                    (r.code, d, h, target, r.up_prob, r.rel_prob, r.pred_price, r.base_price, version,
                     r.sector33_code, ranks.get(r.code), ranks_sector.get(r.code))
                )  # fmt: skip
    return rows


def insert_predictions(conn: psycopg.Connection[DictRow], rows: list[tuple[object, ...]]) -> None:
    with (
        conn.transaction(),
        conn.cursor() as cur,
        cur.copy(
            "COPY prediction (code, pred_date, horizon, target_date, up_prob, rel_prob, pred_price, base_price,"
            " model_version, sector33_code, buy_rank, buy_rank_sector) FROM STDIN"
        ) as copy,
    ):
        for row in rows:
            copy.write_row(row)


def quiet_logger(target: date) -> BatchLogger:
    """バッチの検証に渡すロガー(バッチの logs フォルダには書かない)。"""
    base = logging.getLogger("screenshot.verify")
    if not base.handlers:
        base.addHandler(logging.NullHandler())
        base.propagate = False
    return BatchLogger(base, const.PROCESS_LABELS[const.PROCESS_VERIFY], target)


def run_verify(conn: psycopg.Connection[DictRow], days: list[date]) -> None:
    """バッチの検証で答え合わせをする。直近 VERIFY_DAILY_DAYS 日は1日ずつ行い、期間の集計を日ごとに残す。"""
    daily = days[-VERIFY_DAILY_DAYS:]
    verify(conn, days[-VERIFY_DAILY_DAYS - 1], quiet_logger(days[-VERIFY_DAILY_DAYS - 1]))
    for d in daily:
        verify(conn, d, quiet_logger(d))


@dataclass(frozen=True)
class Fin:
    """1つの事業年度の業績(円。eps と配当は円/株)。"""

    sales: float
    op: float
    np: float
    eps: float
    div: float


def fy_end(year: int) -> date:
    """3月決算の事業年度の終了日(year 年 3月31日)。"""
    return date(year, 3, 31)


def make_fins(rng: np.random.Generator, s: Stock) -> dict[int, Fin]:
    """事業年度(終了日の年)→ 業績。2021〜2026年3月期の実績と、2027年3月期の予想(会社の期初の予想)。"""
    last_price = float(s.close[-1])
    fwd_eps = last_price / s.per
    growth = rng.normal(0.06, 0.05)
    nps: dict[int, float] = {}
    np_2027 = fwd_eps * s.shares
    nps[2027] = np_2027
    for y in range(2026, 2020, -1):
        nps[y] = nps[y + 1] / (1 + growth + rng.normal(0, 0.08))
    if s.d.profile == PROFILE_TURNAROUND:
        nps[2025], nps[2024] = -0.35 * abs(nps[2026]), -0.15 * abs(nps[2026])
    elif s.d.profile == PROFILE_LOSS_SHRINK:
        scale = abs(np_2027)
        for y, mult in zip(range(2021, 2028), (-2.6, -2.9, -2.4, -2.0, -1.6, -1.1, -0.6), strict=True):
            nps[y] = mult * scale
    elif s.d.profile == PROFILE_TO_LOSS:
        nps[2026] = -0.3 * abs(nps[2025])
    result: dict[int, Fin] = {}
    for y, net in nps.items():
        loss = s.d.profile == PROFILE_LOSS_SHRINK
        sales_base = (abs(np_2027) / 0.68 / s.margin) * (1 + growth) ** (y - 2027)
        if loss:
            sales_base = abs(np_2027) * 3.0 * (1.25 ** (y - 2027))
        # 純利益 ÷ 営業利益の割合は、年ごとにばらつかせる(税金・特別損益の違い)
        op = net / rng.uniform(0.65, 0.71) if net > 0 else net * rng.uniform(1.0, 1.3)
        eps = net / s.shares
        div = 0.0 if eps <= 0 else max(1.0, round(eps * 0.3))
        result[y] = Fin(sales_base * math.exp(rng.normal(0, 0.03)), op, net, eps, div)
    return result


# 銘柄コード → (事業年度ごとの業績, 今期の会社予想(修正の後))。バリュエーション指標で使う
FinMap = dict[str, tuple[dict[int, Fin], Fin]]


def insert_financials(rng: np.random.Generator, conn: psycopg.Connection[DictRow], stocks: list[Stock]) -> FinMap:
    """財務情報(通期・四半期の決算短信、業績予想・配当予想の修正)を入れ、銘柄ごとの業績を返す。

    四半期の値は、事業年度の初めからの累計(J-Quants と同じ)。今期(2027年3月期)は、1Q と 2Q まで発表済み
    (一部の銘柄は 2Q がまだで、決算発表予定日が基準の日より後)。
    """
    result: FinMap = {}
    quarter_rate = {"1Q": 0.24, "2Q": 0.49, "3Q": 0.75}
    late = {s.code for s in stocks[3::9]}  # 2Q の発表が基準の日より後の銘柄
    revise_up = {s.code for s in stocks[5::11]}
    revise_down = {s.code for s in stocks[8::17]}
    for s in stocks:
        fins = make_fins(rng, s)
        seq = 0

        def disc_no(day: date, code: str = s.code) -> str:
            nonlocal seq
            seq += 1
            return f"{day:%Y%m%d}{code}{seq:02d}"

        offset = int(rng.integers(0, 8))
        for y in range(2021, 2027):
            f, nxt = fins[y], fins[y + 1]
            day = date(y, 5, 8) + timedelta(days=offset)
            add_fin(conn, s.code, disc_no(day), day, "FY", fy_end(y), sales=round(f.sales), op=round(f.op),
                    np=round(f.np), eps=round(f.eps, 2), nx_f_sales=round(nxt.sales, -6), nx_f_op=round(nxt.op, -6),
                    nx_f_np=round(nxt.np, -6), nx_f_div_ann=nxt.div)  # fmt: skip
        # 今期の会社予想(修正があれば、修正の後の値)
        forecast = fins[2027]
        actual_rate = math.exp(rng.normal(0, 0.06))
        for y in (2025, 2026, 2027):
            r = actual_rate
            f = fins[y] if y < 2027 else Fin(forecast.sales * r, forecast.op * r, forecast.np * r, forecast.eps * r,
                                             forecast.div)  # fmt: skip
            for per_type, rate in quarter_rate.items():
                q_month = {"1Q": (y - 1, 8), "2Q": (y - 1, 11), "3Q": (y, 2)}[per_type]
                day = date(q_month[0], q_month[1], 1) + timedelta(days=offset + 2)
                if per_type == "2Q" and y == 2027:
                    if s.code in late:
                        continue
                    day = date(2026, 11, 2) + timedelta(days=offset)
                if day > BASE_DAY:
                    continue
                if y == 2027 and per_type == "2Q" and s.code in revise_up | revise_down:
                    # 2Q の発表の前に、業績予想の修正を出す
                    mult = 1.12 if s.code in revise_up else 0.85
                    rev_day = date(2026, 10, 20) + timedelta(days=offset)
                    forecast = Fin(forecast.sales * (1 + (mult - 1) / 3), forecast.op * mult, forecast.np * mult,
                                   forecast.eps * mult, forecast.div)  # fmt: skip
                    add_fin(conn, s.code, disc_no(rev_day), rev_day, "FY", fy_end(2027),
                            doc_type="EarnForecastRevision", f_sales=round(forecast.sales, -6),
                            f_op=round(forecast.op, -6), f_np=round(forecast.np, -6),
                            f_eps=round(forecast.eps, 2))  # fmt: skip
                cur_forecast = forecast if y == 2027 else fins[y]
                n_sales, n_op, n_np = (math.exp(rng.normal(0, 0.03)) for _ in range(3))
                add_fin(conn, s.code, disc_no(day), day, per_type, fy_end(y), sales=round(f.sales * rate * n_sales),
                        op=round(f.op * rate * n_op), np=round(f.np * rate * n_np),
                        eps=round(f.eps * rate * n_np, 2), f_sales=round(cur_forecast.sales, -6),
                        f_op=round(cur_forecast.op, -6), f_np=round(cur_forecast.np, -6),
                        f_eps=round(cur_forecast.eps, 2), f_div_ann=cur_forecast.div)  # fmt: skip
        if s.code in revise_up and forecast.div > 0:
            day = date(2026, 11, 10)
            add_fin(conn, s.code, disc_no(day), day, "FY", fy_end(2027), doc_type="DividendForecastRevision",
                    f_div_ann=forecast.div + 5)  # fmt: skip
        result[s.code] = (fins, forecast)
    return result


def insert_valuation_and_earnings(
    conn: psycopg.Connection[DictRow], days: list[date], stocks: list[Stock], fin_map: FinMap
) -> None:
    """バリュエーション指標(予測日の分)と決算発表予定日を入れる。"""
    with conn.transaction(), conn.cursor() as cur:
        with cur.copy(
            "COPY daily_valuation (trade_date, code, eps, fwd_eps, bps, roe, fwd_roe, per, fwd_per, pbr, market_cap)"
            " FROM STDIN"
        ) as copy:
            for s in stocks:
                fins, forecast = fin_map[s.code]
                bps = float(s.close[-1]) / s.pbr
                for k, d in enumerate(days):
                    year = d.year if d >= date(d.year, 5, 15) else d.year - 1
                    eps = fins[year].eps
                    fwd = (forecast if year == 2026 else fins[year + 1]).eps
                    close = float(s.close[k])
                    copy.write_row(
                        (d, s.code, round(eps, 4), round(fwd, 4), round(bps, 4), round(eps / bps, 6),
                         round(fwd / bps, 6), round(close / eps, 4) if eps > 0 else None,
                         round(close / fwd, 4) if fwd > 0 else None, round(close / bps, 4),
                         round(close * s.shares / 1_000_000))
                    )  # fmt: skip
        late = [s.code for s in stocks[3::9]]
        # 2Q の発表がまだの銘柄は、基準の日の後の週に発表予定
        rows = [
            (c, date(2026, 10, 15), "2Q", "0331", date(2026, 11, 16) + timedelta(days=i % 4))
            for i, c in enumerate(late)
        ]
        cur.executemany(
            "INSERT INTO earnings_date (code, pub_date, fq_name, fye, sch_date) VALUES (%s, %s, %s, %s, %s)", rows
        )


def nearest_day(sample: SampleData, code: str, ratio: float, back_from: int, back_to: int) -> date:
    """基準の日の終値に対して、終値が ratio 倍に一番近い過去の日(back_from〜back_to 営業日前の間)。"""
    s = next(s for s in sample.stocks if s.code == code)
    last = float(s.close[-1])
    best = min(range(back_from, back_to), key=lambda b: abs(float(s.close[-1 - b]) / last - ratio))
    return sample.days[-1 - best]


def latest_predictions(conn: psycopg.Connection[DictRow]) -> dict[str, tuple[float, float]]:
    """基準の日の、主となる日数の(市場比の確率, 上昇確率)。"""
    rows = conn.execute(
        "SELECT code, rel_prob, up_prob FROM prediction WHERE pred_date = %s AND horizon = %s",
        (BASE_DAY, const.MAIN_HORIZON),
    ).fetchall()
    return {str(r["code"]): (float(r["rel_prob"]), float(r["up_prob"])) for r in rows}


def insert_holdings(conn: psycopg.Connection[DictRow], sample: SampleData) -> int:
    """保有銘柄(保有中・買い増し・売却済み・一部の売却)を入れる。判定が全種類出るように、最新の予測で銘柄を選ぶ。"""
    preds = latest_predictions(conn)
    th = const.UP_PROB_THRESHOLD

    def pick(cond: str, exclude: set[str]) -> str:
        for code, (rel, up) in sorted(preds.items(), key=lambda x: -abs(x[1][0] - 0.5)):
            if code in exclude:
                continue
            beat, rise = rel >= const.REL_PROB_THRESHOLD, up >= th
            if {"hold": beat and rise, "market_down": beat and not rise, "switch": not beat and rise,
                    "sell": not beat and not rise}[cond]:  # fmt: skip
                return code
        raise RuntimeError(f"判定が {cond} になる銘柄がありません")

    used: set[str] = set()
    plan: list[tuple[str, float, int, str]] = []  # (判定の種類, 基準の日の終値に対する買値の倍率, 株数, メモ)
    plan.append(("hold", 0.86, 100, "配当と株主優待が目的。長く持つ予定"))
    plan.append(("hold", 1.04, 200, "下がったところで買い増し。決算の内容を見て判断する"))
    plan.append(("market_down", 0.93, 200, ""))
    plan.append(("switch", 0.97, 100, "業種の中で出遅れている。乗り換え先を検討中"))
    plan.append(("sell", 1.14, 100, "損切りラインに近い。次の決算までに判断する"))
    count = 0
    codes: dict[int, str] = {}
    for i, (cond, ratio, qty, note) in enumerate(plan):
        if i == 1:
            code = codes[0]  # 買い増し(同じ銘柄の2回目の購入)
        else:
            code = pick(cond, used)
            used.add(code)
        codes[i] = code
        if i == 1:
            # 買い増しの行は、上昇予測だが下降の注意が出るように、平均買値を最新の終値より上にする
            ratio = 1.10
        buy_day = nearest_day(sample, code, ratio, 15 if i == 1 else 40, 120 if i == 1 else 200)
        hid = add_holding(conn, code, str(sample.close_on(code, buy_day)), qty, buy_day)
        if note:
            conn.execute("UPDATE holding SET note = %s WHERE holding_id = %s", (note, hid))
        count += 1
    # 300株の保有(売却のダイアログの見本で、300株のうち100株を売る)
    lot_code = pick("hold", used)
    used.add(lot_code)
    day = nearest_day(sample, lot_code, 0.95, 30, 90)
    add_holding(conn, lot_code, str(sample.close_on(lot_code, day)), 300, day)
    count += 1
    # 売却済み(2025年の秋〜2026年。一部の売却を含む)
    sold_names = ["サンプル鉄道", "サンプル化成", "サンプル商事", "サンプル電機", "サンプル保険", "サンプル窯業"]
    for j, name in enumerate(sold_names):
        s = sample.stock(name)
        buy_k = 230 + j * 38
        sell_k = buy_k + 22 + j * 6
        buy_day, sell_day = sample.days[buy_k], sample.days[sell_k]
        qty = 200 if j % 2 == 0 else 100
        hid = add_holding(conn, s.code, str(float(s.close[buy_k])), qty, buy_day, sell_day, str(float(s.close[sell_k])))
        if name == "サンプル窯業":
            conn.execute("UPDATE holding SET note = %s WHERE holding_id = %s", ("目標の株価に届いたので売却", hid))
        count += 1
    # 一部の売却: 同じ購入の300株のうち100株を売った行と、残りの200株(保有中)
    s = sample.stock("サンプル物産")
    buy_k, sell_k = len(sample.days) - 150, len(sample.days) - 40
    buy_price = str(float(s.close[buy_k]))
    add_holding(conn, s.code, buy_price, 100, sample.days[buy_k], sample.days[sell_k], str(float(s.close[sell_k])))
    add_holding(conn, s.code, buy_price, 200, sample.days[buy_k])
    return count + 2


def insert_watch_stocks(conn: psycopg.Connection[DictRow], sample: SampleData) -> int:
    """検討中銘柄(追加した日の予測日と、その日の株価)を入れる。"""
    items = [
        ("サンプル精機", 12, "受注が伸びている。押し目を待つ"),
        ("サンプルソフト", 5, "決算発表の後の値動きを見る"),
        ("サンプル製薬", 20, ""),
        ("サンプル海運", 3, "配当利回りが高い。業績の修正に注意"),
        ("サンプルドラッグ", 30, "出店のペースを確認する"),
    ]
    for name, back, memo in items:
        s = sample.stock(name)
        d = sample.days[-1 - back]
        added = datetime.combine(d, time(21, 15), JST)
        conn.execute(
            """
            INSERT INTO watch_stock (code, memo, pred_date, base_price, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (s.code, memo, d, Decimal(str(float(s.close[-1 - back]))), added, added),
        )
    return len(items)


def insert_run_history(conn: psycopg.Connection[DictRow], days: list[date]) -> int:
    """実行履歴(直近14日。成功・空振り・失敗。失敗の後は単独で起動して取り直した例)。"""
    rows: list[tuple[object, ...]] = []
    business = set(days)
    daily_steps = [
        (const.PROCESS_COLLECT, 9, 4_120),
        (const.PROCESS_PREDICT, 6, 3_860),
        (const.PROCESS_VERIFY, 3, 15_240),
        (const.PROCESS_NOTIFY, 1, 1),
        (const.PROCESS_BACKUP, 4, 1),
    ]
    failed_day = date(2026, 11, 10)
    for i in range(14):
        d = BASE_DAY - timedelta(days=13 - i)
        start = datetime.combine(d, time(19, 0, 12), JST)
        t = start
        status_all = const.RUN_STATUS_SUCCESS
        if d.weekday() == 5:
            # 土曜日は週次バッチ(学習・クリーニング)
            wt = start
            for proc, minutes, n in ((const.PROCESS_TRAIN, 95, 12), (const.PROCESS_CLEANUP, 2, 3_200)):
                rows.append((const.BATCH_TYPE_WEEKLY, proc, d, const.RUN_STATUS_SUCCESS, wt,
                             wt + timedelta(minutes=minutes, seconds=17), n, None, None))  # fmt: skip
                wt += timedelta(minutes=minutes, seconds=20)
            rows.append((const.BATCH_TYPE_WEEKLY, const.PROCESS_WEEKLY_BATCH, d, const.RUN_STATUS_SUCCESS, start, wt,
                         None, None, None))  # fmt: skip
            continue
        if d not in business:
            # 休みの日(日曜・祝日)は、収集が空振り
            rows.append((const.BATCH_TYPE_DAILY, const.PROCESS_COLLECT, d, const.RUN_STATUS_EMPTY, t,
                         t + timedelta(seconds=41), 0, None, None))  # fmt: skip
            rows.append((const.BATCH_TYPE_DAILY, const.PROCESS_DAILY_BATCH, d, const.RUN_STATUS_EMPTY, start,
                         t + timedelta(seconds=45), None, None, None))  # fmt: skip
            continue
        for proc, minutes, n in daily_steps:
            end = t + timedelta(minutes=minutes, seconds=(i * 7) % 50)
            if d == failed_day and proc == const.PROCESS_COLLECT:
                rows.append((const.BATCH_TYPE_DAILY, proc, d, const.RUN_STATUS_FAILED, t, end, None, None,
                             FAILED_MESSAGE))  # fmt: skip
                status_all = const.RUN_STATUS_FAILED
                t = end
                break
            rows.append((const.BATCH_TYPE_DAILY, proc, d, const.RUN_STATUS_SUCCESS, t, end, n, None, None))
            t = end + timedelta(seconds=3)
        rows.append((const.BATCH_TYPE_DAILY, const.PROCESS_DAILY_BATCH, d, status_all, start, t, None, None, None))
        if d == failed_day:
            # 同じ日の夜に、単独で起動して取り直した
            t = datetime.combine(d, time(21, 32, 5), JST)
            for proc, minutes, n in daily_steps[:4]:
                end = t + timedelta(minutes=minutes, seconds=11)
                rows.append((const.BATCH_TYPE_SINGLE, proc, d, const.RUN_STATUS_SUCCESS, t, end, n, None, None))
                t = end + timedelta(seconds=40)
    rows.sort(key=lambda r: cast(datetime, r[4]))
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO run_history (batch_type, process_name, target_date, status, started_at, finished_at,
                                     record_count, error_code, error_message)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )
    return len(rows)


def table_counts(conn: psycopg.Connection[DictRow]) -> dict[str, int]:
    """テーブルごとの行数。"""
    names = [
        str(r["tablename"])
        for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
    ]
    result: dict[str, int] = {}
    for name in names:
        row = conn.execute(f"SELECT count(*) AS n FROM {name}").fetchone()  # noqa: S608 - テーブル名は DB から読んだもの
        result[name] = 0 if row is None else int(row["n"])
    return result


def build(conn: psycopg.Connection[DictRow]) -> SampleData:
    """テスト用の DB のテーブルを作り直し、架空の値を全て入れる。"""
    rng = np.random.default_rng(SEED)
    recreate_tables(conn)
    calendar = list_business_days_by_rule(PRICE_START, CALENDAR_END)
    days = [d for d in calendar if d <= BASE_DAY]
    future_days = [d for d in calendar if d > BASE_DAY]
    pred_days = days[-PRED_DAY_COUNT:]
    stocks = make_stocks(rng, days)
    index = make_index(rng, stocks, len(days))
    insert_master_and_prices(conn, days, stocks, index)
    versions = model_versions(pred_days)
    insert_models(conn, versions)
    insert_predictions(conn, make_predictions(rng, days, pred_days, future_days, stocks, versions))
    run_verify(conn, days)
    fin_map = insert_financials(rng, conn, stocks)
    insert_valuation_and_earnings(conn, pred_days, stocks, fin_map)
    sample = SampleData(days=days, pred_days=pred_days, stocks=stocks)
    insert_holdings(conn, sample)
    insert_watch_stocks(conn, sample)
    insert_run_history(conn, days)
    sample.counts = table_counts(conn)
    return sample


def main() -> None:
    """テスト用の DB に切り替えて、架空の値を入れる。"""
    name = use_test_db()
    started = time_mod.perf_counter()
    with db.connect() as conn:
        check_test_db(conn)
        sample = build(conn)
    print(f"テスト用の DB({name})に架空の値を入れました({time_mod.perf_counter() - started:.1f}秒)")
    for table, n in sample.counts.items():
        print(f"  {table}: {n:,}行")


if __name__ == "__main__":  # pragma: no cover - コマンドで実行するだけ
    main()
