"""本番の初回の全処理(2021-11-08〜2026-10-30)の検証用スクリプト。

本番のバッチからは呼ばない。DB は読み取り専用(SELECT だけ。接続後に read only にする)で使い、
モデルのファイル(補正の対応表 *_補正.json)は読むだけで、書き換えない。

確かめること:
  1. 実行の完全さ(model、prediction、検証状態、run_history の FAILED)
  2. 的中率(期間の集計、年ごと、相場が下げた時期とそうでない時期)
  3. 買い候補の中身(1日あたりの件数の分布、33業種の内訳)
  4. 上昇確率の確かさ(verify_calibration_summary)
  5. 買い候補が0件の日の原因

読むテーブル: prediction、verify_daily_summary、verify_period_summary、verify_calibration_summary、model、run_history。
営業日の抜けの確認のため、common.business_day 経由で trading_calendar も読む(読み取りだけ)。

実行(stock_prediction_batch のフォルダで):
  python -m tools.verify_full_run
  python -m tools.verify_full_run --from 2021-11-08 --to 2026-10-30
結果は logs/検証結果_本番初回_YYYYMMDD.md。
"""

# ruff: noqa: E501
import argparse
import sys
import time
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg
from psycopg.rows import DictRow

import const
from common import business_day, db
from common.args import today_jst
from common.env import resolve_path
from common.logger import get_logger
from core.calibration import Calibration, calibration_file_name, load_calibration

LABEL: str = "本番初回の検証"

# 全処理の期間の初期値(本番の初回)
DEFAULT_FROM: date = date(2021, 11, 8)
DEFAULT_TO: date = date(2026, 10, 30)
# 予測の最初の日の期待値(ログで数えた予測日の数)
EXPECTED_PRED_DAYS: int = 1212
# モデルバージョンの数の見込み
EXPECTED_MODEL_VERSIONS: int = 65
# 1つのモデルバージョンにある行の数(予測日数 4 × 種類 3)
MODEL_TYPES: list[str] = ["UP_PROB", "PRICE", "REL_PROB"]
# 的中率の表に出す対象区分
SEGMENTS_ALL: list[str] = ["ALL", "TOP", "TOP_SECTOR", "HIGH_PROB"]
SEGMENTS_SHORT: list[str] = ["ALL", "TOP", "TOP_SECTOR"]
# 買い候補の件数の上限
TOP_LIMIT: int = const.BUY_CANDIDATE_COUNT
SECTOR_LIMIT: int = const.BUY_CANDIDATE_SECTOR_COUNT

Row = dict[str, Any]


# ---------------------------------------------------------------------
# 書式の部品
# ---------------------------------------------------------------------
def pct1(v: float | None) -> str:
    """割合を%で小数1桁にする。"""
    return "-" if v is None else f"{v * 100:.1f}%"


def pct2_diff(v: float | None) -> str:
    """差を%で小数2桁にする(符号付き)。"""
    return "-" if v is None else f"{v * 100:+.2f}%"


def num(v: float | None, digits: int = 1) -> str:
    return "-" if v is None else f"{v:,.{digits}f}"


def ratio(n: float, d: float) -> float | None:
    return None if d == 0 else n / d


def mean_opt(values: Sequence[float | None]) -> float | None:
    """None を除いた平均。全て None なら None。"""
    xs = [v for v in values if v is not None and v == v]
    return sum(xs) / len(xs) if xs else None


def md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    """Markdown の表を作る。"""
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    lines.append("")
    return lines


def query(conn: psycopg.Connection[DictRow], sql: str, params: Any = None) -> list[Row]:
    return list(conn.execute(sql, params).fetchall())


def fl(v: Any) -> float | None:
    """Decimal などを float にする。None はそのまま。"""
    return None if v is None else float(v)


def find_runs(all_dates: list[date], flagged: set[date]) -> list[list[date]]:
    """予測日の並びの中で、flagged が続いた期間ごとにまとめる(予測日が抜けた所では途切れない)。"""
    runs: list[list[date]] = []
    cur: list[date] = []
    for d in all_dates:
        if d in flagged:
            cur.append(d)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return runs


def version_text(versions: list[str]) -> str:
    uniq = sorted(set(versions))
    return uniq[0] if len(uniq) == 1 else f"{uniq[0]}〜{uniq[-1]}({len(uniq)}種)"


# ---------------------------------------------------------------------
# 共有する状態
# ---------------------------------------------------------------------
class Context:
    """複数の章で使う値と、気付いた点。"""

    def __init__(self, date_from: date, date_to: date) -> None:
        self.date_from = date_from
        self.date_to = date_to
        self.notes: list[str] = []
        self.daily: pd.DataFrame = pd.DataFrame()  # 予測日ごとの集計(主となる日数)
        self.models: pd.DataFrame = pd.DataFrame()  # model テーブル
        # 予測日 → (市場の中央値, 全銘柄の平均騰落率)  主となる日数
        self.market: dict[date, tuple[float | None, float | None]] = {}
        # 対象日 → 市場の中央値(主となる日数。直前の相場の確認に使う)
        self.market_by_target: dict[date, float | None] = {}


# ---------------------------------------------------------------------
# 1. 実行の完全さ
# ---------------------------------------------------------------------
DAILY_SQL: str = """
SELECT pred_date,
  count(*) AS rows_all,
  count(*) FILTER (WHERE horizon = 5) AS n5,
  count(*) FILTER (WHERE horizon = 10) AS n10,
  count(*) FILTER (WHERE horizon = 20) AS n20,
  count(*) FILTER (WHERE horizon = 60) AS n60,
  count(DISTINCT model_version) AS versions,
  min(model_version) AS model_version,
  count(*) FILTER (WHERE horizon = %(h)s AND buy_rank IS NOT NULL) AS n_top,
  count(*) FILTER (WHERE horizon = %(h)s AND buy_rank_sector IS NOT NULL) AS n_sector,
  count(*) FILTER (WHERE horizon = %(h)s AND up_prob >= %(thr)s) AS n_a,
  count(*) FILTER (WHERE horizon = %(h)s AND pred_price > base_price) AS n_b,
  count(*) FILTER (WHERE horizon = %(h)s AND up_prob >= %(thr)s AND pred_price > base_price) AS n_ok,
  count(*) FILTER (WHERE horizon = %(h)s AND up_prob < %(thr)s AND pred_price > base_price) AS fail_a,
  count(*) FILTER (WHERE horizon = %(h)s AND up_prob >= %(thr)s AND pred_price <= base_price) AS fail_b,
  count(*) FILTER (WHERE horizon = %(h)s AND up_prob < %(thr)s AND pred_price <= base_price) AS fail_both,
  (max(up_prob) FILTER (WHERE horizon = %(h)s))::float8 AS up_max,
  (percentile_cont(0.99) WITHIN GROUP (ORDER BY up_prob::float8) FILTER (WHERE horizon = %(h)s)) AS up_p99,
  (percentile_cont(0.5) WITHIN GROUP (ORDER BY up_prob::float8) FILTER (WHERE horizon = %(h)s)) AS up_med,
  (percentile_cont(0.5) WITHIN GROUP (ORDER BY (pred_price / NULLIF(base_price, 0) - 1)::float8)
     FILTER (WHERE horizon = %(h)s)) AS ret_med
FROM prediction
GROUP BY pred_date
ORDER BY pred_date
"""


def section_completeness(conn: psycopg.Connection[DictRow], ctx: Context) -> list[str]:
    out: list[str] = ["## 1. 実行の完全さ", ""]

    # --- model ---
    out += ["### 1.1 model(モデル)", ""]
    models = pd.DataFrame(
        query(
            conn,
            "SELECT model_version, horizon, model_type, base_date, train_start_date, train_end_date, train_rows,"
            " valid_score, file_path FROM model ORDER BY model_version, horizon, model_type",
        )
    )
    ctx.models = models
    versions = sorted(models["model_version"].unique().tolist())
    expected_rows = len(const.PREDICT_HORIZONS) * len(MODEL_TYPES)
    out.append(
        f"- モデルバージョンの数: {len(versions)}(見込み 約{EXPECTED_MODEL_VERSIONS})"
        f"、最初: {versions[0]}、最後: {versions[-1]}"
    )
    incomplete: list[str] = []
    for v, g in models.groupby("model_version"):
        pairs = set(zip(g["horizon"].astype(int), g["model_type"], strict=True))
        want = {(h, t) for h in const.PREDICT_HORIZONS for t in MODEL_TYPES}
        if len(g) != expected_rows or pairs != want:
            incomplete.append(f"{v}(行数 {len(g)}、足りない組 {sorted(want - pairs)})")
    out.append(f"- 予測日数×種類({expected_rows}個)がそろっていないバージョン: {len(incomplete)}件")
    out += [f"  - {t}" for t in incomplete]
    out.append("")
    if incomplete:
        ctx.notes.append(f"model で {expected_rows} 行そろっていないバージョンが {len(incomplete)} 件ある")

    # --- prediction(予測日ごと) ---
    out += ["### 1.2 prediction(予測日ごとの行数)", ""]
    daily = pd.DataFrame(query(conn, DAILY_SQL, {"h": const.MAIN_HORIZON, "thr": const.UP_PROB_THRESHOLD}))
    for c in ["up_max", "up_p99", "up_med", "ret_med"]:
        daily[c] = daily[c].astype(float)
    ctx.daily = daily
    dates: list[date] = daily["pred_date"].tolist()
    rows_all = daily["rows_all"]
    out.append(f"- 予測日の数: {len(daily)}(見込み {EXPECTED_PRED_DAYS:,})。最初: {dates[0]}、最後: {dates[-1]}")
    out.append(
        f"- 予測日ごとの行数(全予測日数の合計): 最小 {int(rows_all.min()):,}、最大 {int(rows_all.max()):,}、"
        f"平均 {rows_all.mean():,.0f}、中央値 {rows_all.median():,.0f}"
    )
    per_h = daily["n20"]
    out.append(
        f"- 主となる日数({const.MAIN_HORIZON}日後)の銘柄数: 最小 {int(per_h.min()):,}、最大 {int(per_h.max()):,}、"
        f"平均 {per_h.mean():,.0f}"
    )
    odd_h = daily[(daily["n5"] != daily["n20"]) | (daily["n10"] != daily["n20"]) | (daily["n60"] != daily["n20"])]
    out.append(f"- 予測日数ごとの行数が食い違う予測日: {len(odd_h)}日")
    multi = daily[daily["versions"] != 1]
    out.append(f"- 1日に複数のモデルバージョンが混ざった予測日: {len(multi)}日")
    out.append("")
    if len(daily) != EXPECTED_PRED_DAYS:
        ctx.notes.append(f"予測日の数が {len(daily)} 日で、ログの 1,212 日と違う")
    if len(odd_h):
        ctx.notes.append(f"予測日数ごとの行数が食い違う予測日が {len(odd_h)} 日ある")
    if len(multi):
        ctx.notes.append(f"1日に複数のモデルバージョンが混ざった予測日が {len(multi)} 日ある")

    low_limit = rows_all.median() * 0.5
    low = daily[rows_all < low_limit].sort_values("rows_all")
    out.append(f"行数が極端に少ない日(中央値の半分未満 = {low_limit:,.0f} 行未満): {len(low)}日")
    out.append("")
    if len(low):
        out += md_table(
            ["予測日", "行数(全予測日数)", "20日後の銘柄数", "モデルバージョン"],
            [
                [str(r.pred_date), f"{r.rows_all:,}", f"{r.n20:,}", str(r.model_version)]
                for r in low.head(20).itertuples()
            ],
        )
        ctx.notes.append(f"行数が中央値の半分未満の予測日が {len(low)} 日ある")
    lowest = daily.sort_values("rows_all").head(5)
    out.append("行数が少ない順の上位5日(参考):")
    out.append("")
    out += md_table(
        ["予測日", "行数(全予測日数)", "20日後の銘柄数"],
        [[str(r.pred_date), f"{r.rows_all:,}", f"{r.n20:,}"] for r in lowest.itertuples()],
    )

    # --- 予測日が抜けている営業日 ---
    try:
        bdays = business_day.list_business_days(dates[0], dates[-1], conn)
        src = "取引カレンダー"
    except Exception:  # カレンダーが足りないときは平日と祝日のルールで数える
        bdays = business_day.list_business_days_by_rule(dates[0], dates[-1])
        src = "ルール(平日と祝日)"
    missing = [d for d in bdays if d not in set(dates)]
    extra = [d for d in dates if d not in set(bdays)]
    out.append(
        f"予測日が抜けている営業日(営業日の判定: {src}。{dates[0]}〜{dates[-1]}の営業日 {len(bdays)}日): {len(missing)}日"
    )
    out.append("")
    if missing:
        out.append("  " + "、".join(str(d) for d in missing[:60]) + (" …" if len(missing) > 60 else ""))
        out.append("")
        ctx.notes.append(f"予測日が抜けている営業日が {len(missing)} 日ある")
    if extra:
        out.append(f"営業日でないのに予測がある日: {len(extra)}日(" + "、".join(str(d) for d in extra[:20]) + ")")
        out.append("")
        ctx.notes.append(f"営業日でない日に予測がある日が {len(extra)} 日ある")

    # --- モデルバージョンごとの予測日数 ---
    ver_days = daily.groupby("model_version").agg(days=("pred_date", "count"), first=("pred_date", "min"),
                                                  last=("pred_date", "max"))  # fmt: skip
    no_pred = [v for v in versions if v not in ver_days.index]
    unknown = [v for v in ver_days.index if v not in versions]
    out.append(
        f"- 予測で使われなかったモデルバージョン: {len(no_pred)}件 {no_pred}。"
        f"modelにないのに予測が使ったバージョン: {len(unknown)}件 {unknown}"
    )
    out.append("")
    if unknown:
        ctx.notes.append(f"model にないモデルバージョンを使った予測がある: {unknown}")
    mrows = []
    for v in versions:
        g = models[models["model_version"] == v]
        base = g["base_date"].iloc[0]
        if v in ver_days.index:
            vd = ver_days.loc[v]
            mrows.append([v, str(base), str(len(g)), str(int(vd["days"])), str(vd["first"]), str(vd["last"])])
        else:
            mrows.append([v, str(base), str(len(g)), "0", "-", "-"])
    out += md_table(
        ["モデルバージョン", "学習の基準日", "modelの行数", "使った予測日数", "最初の予測日", "最後の予測日"], mrows
    )

    # --- verify_status ---
    out += ["### 1.3 verify_status(検証状態)", ""]
    st = query(
        conn,
        "SELECT horizon, verify_status, count(*) AS n, min(pred_date) AS d0, max(pred_date) AS d1"
        " FROM prediction GROUP BY horizon, verify_status ORDER BY horizon, verify_status",
    )
    out += md_table(
        ["予測日数", "検証状態", "件数", "最初の予測日", "最後の予測日"],
        [[str(r["horizon"]), r["verify_status"], f"{r['n']:,}", str(r["d0"]), str(r["d1"])] for r in st],
    )
    pend60 = [r for r in st if r["verify_status"] == "PENDING" and r["horizon"] == 60]
    if pend60:
        pdays = int((daily["pred_date"] >= pend60[0]["d0"]).sum())
        out.append(f"- PENDING(60日後)の最初の予測日 {pend60[0]['d0']} 以降の予測日: {pdays}日(見込み 約60営業日)")
        out.append("")
    early_pending = [r for r in st if r["verify_status"] == "PENDING" and r["d0"] < date(2026, 7, 1)]
    if early_pending:
        ctx.notes.append("2026-07-01 より前の予測日に PENDING がある(" + "、".join(
            f"{r['horizon']}日後 {r['d0']}〜" for r in early_pending) + ")")  # fmt: skip

    unv_year = query(
        conn,
        "SELECT extract(year FROM pred_date)::int AS y, horizon, count(*) AS n FROM prediction"
        " WHERE verify_status = 'UNVERIFIABLE' GROUP BY y, horizon ORDER BY y, horizon",
    )
    unv_total = sum(r["n"] for r in unv_year)
    out.append(f"UNVERIFIABLE(検証不能)の件数: {unv_total:,}")
    out.append("")
    if unv_year:
        table: dict[int, dict[int, int]] = {}
        for r in unv_year:
            table.setdefault(r["y"], {})[r["horizon"]] = r["n"]
        out += md_table(
            ["予測日の年"] + [f"{h}日後" for h in const.PREDICT_HORIZONS],
            [[str(y)] + [f"{table[y].get(h, 0):,}" for h in const.PREDICT_HORIZONS] for y in sorted(table)],
        )
        top_codes = query(
            conn,
            "SELECT code, count(*) AS n, count(DISTINCT pred_date) AS d, min(pred_date) AS d0, max(pred_date) AS d1"
            " FROM prediction WHERE verify_status = 'UNVERIFIABLE' GROUP BY code ORDER BY n DESC LIMIT 10",
        )
        distinct = query(conn, "SELECT count(DISTINCT code) AS c FROM prediction WHERE verify_status='UNVERIFIABLE'")
        out.append(f"UNVERIFIABLE がある銘柄の数: {distinct[0]['c']:,}。件数の多い銘柄の上位10:")
        out.append("")
        out += md_table(
            ["銘柄コード", "件数", "予測日の数", "最初の予測日", "最後の予測日"],
            [[r["code"], f"{r['n']:,}", f"{r['d']:,}", str(r["d0"]), str(r["d1"])] for r in top_codes],
        )
        top_days = query(
            conn,
            "SELECT pred_date, count(*) AS n FROM prediction WHERE verify_status = 'UNVERIFIABLE'"
            " GROUP BY pred_date ORDER BY n DESC LIMIT 5",
        )
        out.append("件数の多い予測日の上位5: " + "、".join(f"{r['pred_date']}({r['n']:,}件)" for r in top_days))
        out.append("")
        out.append(
            "- 検証不能の理由は prediction に保存していない。上の分布(特定の銘柄に集中、対象日に株価がない)から、"
            "上場廃止・売買停止・対象日の株価欠損が主な理由と推測(確度 70%)"
        )
        out.append("")
        total_rows = query(conn, "SELECT count(*) AS n FROM prediction")[0]["n"]
        ctx.notes.append(f"UNVERIFIABLE は {unv_total:,} 件(全 {total_rows:,} 行の {pct1(unv_total / total_rows)})")

    # --- run_history ---
    out += ["### 1.4 run_history(全処理の期間)", ""]
    hist = query(
        conn,
        "SELECT process_name, status, count(*) AS n FROM run_history WHERE target_date BETWEEN %s AND %s"
        " GROUP BY process_name, status ORDER BY process_name, status",
        (ctx.date_from, ctx.date_to),
    )
    out += md_table(["処理", "状態", "件数"], [[r["process_name"], r["status"], f"{r['n']:,}"] for r in hist])
    failed = query(
        conn,
        "SELECT run_id, batch_type, process_name, target_date, status, started_at, finished_at, record_count,"
        " left(error_message, 150) AS msg FROM run_history WHERE status IN ('FAILED', 'RUNNING')"
        " AND target_date BETWEEN %s AND %s ORDER BY started_at",
        (ctx.date_from, ctx.date_to),
    )
    out.append(
        f"FAILED と RUNNING の行: {len(failed)}件(前提: FAILED は 2022-07-09 のクリーニングと 2026-08-15 の学習の2件)"
    )
    out.append("")
    out += md_table(
        ["run_id", "起動", "処理", "対象日", "状態", "開始", "終了", "件数", "エラーの内容(先頭150字)"],
        [
            [
                str(r["run_id"]), r["batch_type"], r["process_name"], str(r["target_date"]), r["status"],
                r["started_at"].strftime("%Y-%m-%d %H:%M"),
                "-" if r["finished_at"] is None else r["finished_at"].strftime("%Y-%m-%d %H:%M"),
                "-" if r["record_count"] is None else str(r["record_count"]),
                (r["msg"] or "-").replace("|", "/").replace("\n", " "),
            ]
            for r in failed
        ],
    )  # fmt: skip
    expected = {(date(2022, 7, 9), "CLEANUP"), (date(2026, 8, 15), "TRAIN")}
    others = [r for r in failed if (r["target_date"], r["process_name"]) not in expected]
    if others:
        ctx.notes.append(
            "前提の2件以外の FAILED/RUNNING が run_history にある: "
            + "、".join(f"{r['target_date']} {r['process_name']} {r['status']}" for r in others)
        )
    return out


# ---------------------------------------------------------------------
# 2. 的中率
# ---------------------------------------------------------------------
SUMMARY_SQL: str = """
SELECT {key} AS k, horizon, segment,
  count(*) AS days, sum(verified_count) AS n, sum(hit_count) AS hit, sum(up_count) AS up,
  sum(beat_count) AS beat, sum(sum_excess_rate) AS ex, sum(sum_change_rate) AS chg,
  sum(sector_verified_count) AS sn, sum(sector_beat_count) AS sb, sum(sum_sector_excess_rate) AS sex,
  avg(market_median) AS mm
FROM verify_daily_summary
WHERE segment = ANY(%s) {where}
GROUP BY k, horizon, segment
ORDER BY horizon, k, segment
"""

HEAD_RATE: list[str] = [
    "件数",
    "的中率",
    "市場の中央値を上回った割合",
    "中央値との差の平均",
    "業種の中央値を上回った割合",
    "業種の中央値との差の平均",
]


def rate_cells(r: Row) -> list[str]:
    """集計の1行を、表のセルにする。"""
    n = float(r["n"] or 0)
    sn = float(r["sn"] or 0)
    return [
        f"{int(n):,}",
        pct1(ratio(float(r["hit"] or 0), n)),
        pct1(ratio(float(r["beat"] or 0), n)),
        pct2_diff(ratio(float(r["ex"] or 0), n)),
        pct1(ratio(float(r["sb"] or 0), sn)),
        pct2_diff(ratio(float(r["sex"] or 0), sn)),
    ]


def summary_rows(conn: psycopg.Connection[DictRow], key_sql: str, where: str, segments: list[str]) -> list[Row]:
    return query(conn, SUMMARY_SQL.format(key=key_sql, where=where), (segments,))


def section_accuracy(conn: psycopg.Connection[DictRow], ctx: Context) -> list[str]:
    out: list[str] = ["## 2. 的中率", ""]
    out.append(
        "verify_daily_summary を予測日で足し合わせた値。件数は検証済みの行数。"
        "市場の中央値・業種の中央値を上回った割合は、同じ予測日・予測日数の全銘柄(業種)の中央値との比較。"
    )
    out.append("")

    # --- 2.1 期間の集計 ---
    out += ["### 2.1 全期間", ""]
    rows = summary_rows(conn, "'全期間'", "", SEGMENTS_ALL)
    by_h: dict[int, list[Row]] = {}
    for r in rows:
        by_h.setdefault(r["horizon"], []).append(r)
    for h in const.PREDICT_HORIZONS:
        out.append(f"#### {h}営業日後")
        out.append("")
        out += md_table(
            ["対象区分", "予測日の数"] + HEAD_RATE,
            [[r["segment"], f"{r['days']:,}"] + rate_cells(r) for r in by_h.get(h, [])],
        )
    all_rows = {(r["horizon"], r["segment"]): r for r in rows}

    # verify_period_summary の全期間との照合
    latest = query(conn, "SELECT max(summary_date) AS d FROM verify_period_summary")[0]["d"]
    if latest is not None:
        ps = query(
            conn,
            "SELECT horizon, segment, verified_count, hit_rate, beat_rate, avg_excess_rate FROM verify_period_summary"
            " WHERE summary_date = %s AND period_type = 'ALL'",
            (latest,),
        )
        diffs: list[str] = []
        for p in ps:
            mine = all_rows.get((p["horizon"], p["segment"]))
            if mine is None or p["hit_rate"] is None:
                continue
            hit = ratio(float(mine["hit"]), float(mine["n"]))
            if int(p["verified_count"]) != int(mine["n"]) or (
                hit is not None and abs(hit - float(p["hit_rate"])) > 0.00006
            ):
                diffs.append(f"{p['horizon']}日後 {p['segment']}: 期間集計 {p['verified_count']:,}件/{pct1(float(p['hit_rate']))}"
                             f" と 日ごと集計 {int(mine['n']):,}件/{pct1(hit)}")  # fmt: skip
        out.append(
            f"- verify_period_summary(summary_date={latest}、ALL)との照合: 件数または的中率が合わない組 {len(diffs)}件"
        )
        out += [f"  - {t}" for t in diffs]
        out.append("")
        if diffs:
            ctx.notes.append(
                f"verify_period_summary(ALL)と verify_daily_summary の合計が合わない組が {len(diffs)} 件ある"
            )

    # 気付いた点: 買い候補が ALL より悪いなど
    for h in const.PREDICT_HORIZONS:
        a = all_rows.get((h, "ALL"))
        t = all_rows.get((h, "TOP"))
        if a and t and a["n"] and t["n"]:
            ah, th = float(a["hit"]) / float(a["n"]), float(t["hit"]) / float(t["n"])
            ab = float(a["beat"]) / float(a["n"])
            tb = float(t["beat"]) / float(t["n"])
            if tb < 0.5:
                ctx.notes.append(f"{h}日後: 買い候補(TOP)の市場の中央値を上回った割合が {pct1(tb)} で 50% を下回る")
            if abs(ab - 0.5) > 0.02:
                ctx.notes.append(
                    f"{h}日後: 全銘柄(ALL)の市場の中央値を上回った割合が {pct1(ab)} で、50% から 2 ポイント以上ずれる"
                )
            ctx.notes.append(f"{h}日後: 的中率 TOP {pct1(th)} / ALL {pct1(ah)}、市場の中央値を上回った割合 TOP {pct1(tb)} / ALL {pct1(ab)}")  # fmt: skip

    # --- 2.2 年ごと ---
    out += ["### 2.2 年ごと(予測日の年。TOP・TOP_SECTOR・ALL)", ""]
    yrows = summary_rows(conn, "(extract(year FROM pred_date)::int)::text", "", SEGMENTS_SHORT)
    for h in const.PREDICT_HORIZONS:
        out.append(f"#### {h}営業日後")
        out.append("")
        out += md_table(
            ["年", "対象区分", "予測日の数"] + HEAD_RATE,
            [[r["k"], r["segment"], f"{r['days']:,}"] + rate_cells(r) for r in yrows if r["horizon"] == h],
        )

    # --- 2.3 相場が下げた時期 ---
    out += ["### 2.3 予測日の市場の中央値(market_median)がマイナスの日とプラスの日", ""]
    out.append(
        "market_median は、その予測日から N 営業日後までの全銘柄の騰落率の中央値(予測日数ごとに別の値)。"
        "マイナス = 予測日数ごとに、その予測日から後の相場が下げた日。"
    )
    out.append("")
    key = "CASE WHEN market_median IS NULL THEN '不明' WHEN market_median < 0 THEN '1 市場マイナス' ELSE '2 市場プラス(0以上)' END"
    mrows = summary_rows(conn, key, "", SEGMENTS_SHORT)
    for h in const.PREDICT_HORIZONS:
        out.append(f"#### {h}営業日後")
        out.append("")
        out += md_table(
            ["市場の中央値", "対象区分", "予測日の数", "市場の中央値の平均"] + HEAD_RATE,
            [
                [r["k"][2:] if r["k"][:1].isdigit() else r["k"], r["segment"], f"{r['days']:,}", pct2_diff(fl(r["mm"]))]
                + rate_cells(r)
                for r in mrows
                if r["horizon"] == h
            ],
        )
    return out


# ---------------------------------------------------------------------
# 3. 買い候補の中身
# ---------------------------------------------------------------------
def count_distribution(s: pd.Series, limit: int) -> tuple[list[str], list[list[str]]]:
    """1日あたりの件数の分布(要約と区間別の日数)。"""
    summary = [
        f"最小 {int(s.min())}、5%点 {s.quantile(0.05):.0f}、中央値 {s.median():.0f}、平均 {s.mean():.1f}、最大 {int(s.max())}",
        f"{limit}件未満の日: {int((s < limit).sum())}日、0件の日: {int((s == 0).sum())}日",
    ]
    edges = [(0, 0), (1, 9), (10, limit - 1), (limit, 10**6)]
    rows: list[list[str]] = []
    for lo, hi in edges:
        label = f"{lo}件" if lo == hi else (f"{lo}件以上" if hi == 10**6 else f"{lo}〜{hi}件")
        n = int(((s >= lo) & (s <= hi)).sum())
        rows.append([label, f"{n:,}", pct1(n / len(s))])
    return summary, rows


def section_candidates(conn: psycopg.Connection[DictRow], ctx: Context) -> list[str]:
    out: list[str] = ["## 3. 買い候補の中身", ""]
    d = ctx.daily
    out.append(
        f"件数は主となる日数({const.MAIN_HORIZON}日後)の行で数えた(買い候補の順位は全ての予測日数の行に同じ値が入る)。"
    )
    out.append("")
    for title, col, limit in [
        ("3.1 買い候補(TOP。上位20)", "n_top", TOP_LIMIT),
        ("3.2 買い候補(業種分散。TOP_SECTOR。上位30)", "n_sector", SECTOR_LIMIT),
    ]:
        out += [f"### {title}", ""]
        summary, rows = count_distribution(d[col], limit)
        out += [f"- {t}" for t in summary]
        out.append("")
        out += md_table(["1日の件数", "日数", "割合"], rows)
        zero = int((d[col] == 0).sum())
        low = int((d[col] < limit).sum())
        ctx.notes.append(f"{title.split(' ', 1)[1]}: {limit}件未満の日 {low}日、0件の日 {zero}日(全 {len(d)}日)")

    # --- 業種の内訳 ---
    out += ["### 3.3 33業種の内訳(上位10業種)", ""]
    sec = query(
        conn,
        "SELECT sector33_code, count(*) AS allc,"
        " count(*) FILTER (WHERE buy_rank IS NOT NULL) AS top,"
        " count(*) FILTER (WHERE buy_rank_sector IS NOT NULL) AS sec"
        " FROM prediction WHERE horizon = %s GROUP BY sector33_code",
        (const.MAIN_HORIZON,),
    )
    tot_all = sum(r["allc"] for r in sec)
    for label, col in [("買い候補(TOP)", "top"), ("買い候補(業種分散。TOP_SECTOR)", "sec")]:
        total = sum(r[col] for r in sec)
        top10 = sorted(sec, key=lambda r: -r[col])[:10]
        out.append(f"#### {label}(延べ {total:,}件。業種コードは33業種コード。名前は銘柄マスタを読まないため省略)")
        out.append("")
        out += md_table(
            ["順位", "33業種コード", "件数", "割合", "全銘柄の割合(参考)", "偏り(割合 ÷ 全銘柄の割合)"],
            [
                [
                    str(i), str(r["sector33_code"]) if r["sector33_code"] is not None else "(NULL)", f"{r[col]:,}",
                    pct1(ratio(r[col], total)), pct1(ratio(r["allc"], tot_all)),
                    "-" if r["allc"] == 0 or total == 0 else f"{(r[col] / total) / (r['allc'] / tot_all):.2f}倍",
                ]
                for i, r in enumerate(top10, 1)
            ],
        )  # fmt: skip
        if total and top10:
            share = top10[0][col] / total
            if share >= 0.15:
                ctx.notes.append(f"{label}は業種コード {top10[0]['sector33_code']} が {pct1(share)} を占める")
        used = sum(1 for r in sec if r[col] > 0)
        out.append(f"- 1件でも入った業種の数: {used}(NULL を含む)")
        out.append("")
    return out


# ---------------------------------------------------------------------
# 4. 上昇確率の確かさ
# ---------------------------------------------------------------------
def section_calibration(conn: psycopg.Connection[DictRow], ctx: Context) -> list[str]:
    out: list[str] = ["## 4. 上昇確率の確かさ(verify_calibration_summary)", ""]
    latest = query(conn, "SELECT max(summary_date) AS d FROM verify_calibration_summary")[0]["d"]
    if latest is None:
        out.append("verify_calibration_summary に行がありません。")
        out.append("")
        ctx.notes.append("verify_calibration_summary に行がない")
        return out
    out.append(f"集計の基準日(summary_date): {latest}。ずれ = 実際に上がった割合 − 確率の平均(マイナスは自信過剰)。")
    out.append("")
    rows = query(
        conn,
        "SELECT period_type, horizon, prob_band, verified_count, up_count, avg_up_prob, up_rate, gap"
        " FROM verify_calibration_summary WHERE summary_date = %s ORDER BY period_type, horizon, prob_band",
        (latest,),
    )
    for period, label in [("ALL", "全期間"), ("3M", "直近3か月")]:
        out += [f"### 4.{1 if period == 'ALL' else 2} {label}", ""]
        for h in const.PREDICT_HORIZONS:
            sub = [r for r in rows if r["period_type"] == period and r["horizon"] == h]
            out.append(f"#### {h}営業日後")
            out.append("")
            out += md_table(
                ["確率の帯", "件数", "実際に上がった件数", "確率の平均", "実際に上がった割合", "ずれ"],
                [
                    [
                        f"{r['prob_band']}〜{r['prob_band'] + 10}%", f"{r['verified_count']:,}", f"{r['up_count']:,}",
                        pct1(fl(r["avg_up_prob"])), pct1(fl(r["up_rate"])), pct2_diff(fl(r["gap"])),
                    ]
                    for r in sub
                ],
            )  # fmt: skip
            if period == "ALL":
                big = [
                    r
                    for r in sub
                    if r["gap"] is not None and r["verified_count"] >= 1000 and abs(float(r["gap"])) >= 0.05
                ]
                for r in big:
                    ctx.notes.append(
                        f"{h}日後の確率 {r['prob_band']}%台(件数 {r['verified_count']:,})は、ずれが {pct2_diff(float(r['gap']))}"
                    )
    return out


# ---------------------------------------------------------------------
# 5. 買い候補が0件の日の原因
# ---------------------------------------------------------------------
def load_market(conn: psycopg.Connection[DictRow], ctx: Context) -> None:
    """主となる日数の、予測日ごとの市場の中央値と全銘柄の平均騰落率(verify_daily_summary の ALL)。"""
    for r in query(
        conn,
        "SELECT pred_date, target_date, market_median, sum_change_rate, verified_count FROM verify_daily_summary"
        " WHERE horizon = %s AND segment = 'ALL'",
        (const.MAIN_HORIZON,),
    ):
        mm = fl(r["market_median"])
        mean = ratio(float(r["sum_change_rate"]), float(r["verified_count"])) if r["verified_count"] else None
        ctx.market[r["pred_date"]] = (mm, mean)
        ctx.market_by_target[r["target_date"]] = mm


def calibration_info(ctx: Context) -> dict[str, dict[str, float | None]]:
    """モデルバージョンごとの、主となる日数の上昇確率用の補正の対応表(50% → ○% など)。"""
    info: dict[str, dict[str, float | None]] = {}
    m = ctx.models
    sub = m[(m["horizon"] == const.MAIN_HORIZON) & (m["model_type"] == "UP_PROB")]
    base_dir = resolve_path(const.MODEL_DIR)
    for r in sub.itertuples():
        path = base_dir / Path(str(r.file_path))
        cal: Calibration | None = load_calibration(path.with_name(calibration_file_name(str(r.file_path))))
        d: dict[str, float | None] = {"c50": None, "c60": None, "c75": None, "cmax": None}
        if cal is not None:
            d["c50"] = float(cal.apply([0.5])[0])
            d["c60"] = float(cal.apply([0.6])[0])
            d["c75"] = float(cal.apply([0.75])[0])
            d["cmax"] = float(max(cal.y))
        info[str(r.model_version)] = d
    return info


def cal_text(c: dict[str, float | None], key: str) -> str:
    """補正の対応表の値の表示。"""
    return "なし" if c.get(key) is None else pct1(c[key])


def run_row(g: pd.DataFrame, ctx: Context) -> list[str]:
    """続いた期間の1行。"""
    dates: list[date] = g["pred_date"].tolist()
    mm = [ctx.market.get(d, (None, None))[0] for d in dates]
    mean = [ctx.market.get(d, (None, None))[1] for d in dates]
    prev = [ctx.market_by_target.get(d) for d in dates]
    return [
        f"{dates[0]}〜{dates[-1]}", str(len(dates)), version_text(g["model_version"].tolist()),
        f"{int(g['n_top'].min())}〜{int(g['n_top'].max())}", num(float(g["n_ok"].mean()), 0),
        pct1(float(g["up_max"].mean())), pct2_diff(mean_opt(mm)), pct2_diff(mean_opt(mean)), pct2_diff(mean_opt(prev)),
    ]  # fmt: skip


RUN_HEAD: list[str] = [
    "期間", "予測日の数", "モデルバージョン", "買い候補の件数", "条件を満たす銘柄(平均)", "up_probの最大(平均)",
    "市場の中央値(20日後。平均)", "全銘柄の平均騰落率(20日後)", "直前20営業日の市場の中央値(平均)",
]  # fmt: skip


def section_zero_days(ctx: Context) -> list[str]:
    out: list[str] = ["## 5. 買い候補が0件の日の原因", ""]
    d = ctx.daily.copy()
    dates: list[date] = d["pred_date"].tolist()
    thr = const.UP_PROB_THRESHOLD
    out.append(
        f"主となる日数 = {const.MAIN_HORIZON}日後。買い候補の条件 = 上昇確率 {thr * 100:.0f}% 以上、"
        "かつ見込み(予測株価 ÷ 予測時の株価 − 1)がプラス。"
        "「直前20営業日の市場の中央値」は、20営業日前の予測日の market_median(= その予測日から今日までの全銘柄の騰落率の中央値)。"
    )
    out.append("")
    zero = d[d["n_top"] == 0]
    low = d[d["n_top"] < TOP_LIMIT]
    other = d[d["n_top"] > 0]
    zero_set = set(zero["pred_date"])
    low_set = set(low["pred_date"])

    # --- 5.1 一覧 ---
    out += ["### 5.1 0件の日と20件未満の日", ""]
    out.append(f"- 0件の日: {len(zero)}日、1〜{TOP_LIMIT - 1}件の日: {len(low) - len(zero)}日")
    yr = d.assign(y=[x.year for x in d["pred_date"]])
    ytab = yr.groupby("y").agg(days=("pred_date", "count"), zero=("n_top", lambda s: int((s == 0).sum())),
                               low=("n_top", lambda s: int(((s > 0) & (s < TOP_LIMIT)).sum())))  # fmt: skip
    out.append("")
    out += md_table(
        ["年", "予測日の数", "0件の日", "1〜19件の日"],
        [[str(y), str(int(r.days)), str(int(r.zero)), str(int(r.low))] for y, r in ytab.iterrows()],
    )
    out += ["#### 20件未満の日が続いた期間(0件を含む)", ""]
    runs_low = find_runs(dates, low_set)
    idx = d.set_index("pred_date", drop=False)
    out += md_table(
        ["期間", "予測日の数", "うち0件の日", "モデルバージョン", "件数の最小〜最大"],
        [
            [
                f"{r[0]}〜{r[-1]}", str(len(r)), str(sum(1 for x in r if x in zero_set)),
                version_text(idx.loc[r]["model_version"].tolist()),
                f"{int(idx.loc[r]['n_top'].min())}〜{int(idx.loc[r]['n_top'].max())}",
            ]
            for r in runs_low
        ],
    )  # fmt: skip
    out += ["#### 0件の日が続いた期間", ""]
    runs_zero = find_runs(dates, zero_set)
    out += md_table(RUN_HEAD, [run_row(idx.loc[r], ctx) for r in runs_zero])
    out += ["#### 0件の日の一覧", ""]
    out += md_table(
        ["予測日", "モデルバージョン", "件数", "条件を満たす銘柄", "up_probの最大"],
        [
            [str(r["pred_date"]), str(r["model_version"]), str(r["n_top"]), str(r["n_ok"]), pct1(float(r["up_max"]))]
            for _, r in zero.iterrows()
        ],
    )
    if len(zero):
        ctx.notes.append(
            f"買い候補が0件の日は {len(zero)} 日、{len(runs_zero)} 回の連続に分かれる(最長 {max(len(r) for r in runs_zero)}日)"
        )

    # --- 5.2 条件ごとの落ちた件数 ---
    out += ["### 5.2 0件の日に、どの条件で落ちたか(銘柄の延べ数。主となる日数)", ""]

    def cond_row(label: str, g: pd.DataFrame) -> list[str]:
        n = float(g["n20"].sum())
        return [
            label, str(len(g)), f"{int(n):,}", f"{int(g['n_ok'].sum()):,}", f"{int(g['fail_a'].sum()):,}",
            f"{int(g['fail_b'].sum()):,}", f"{int(g['fail_both'].sum()):,}",
            pct1(ratio(float(g["n_a"].sum()), n)), pct1(ratio(float(g["n_b"].sum()), n)),
        ]  # fmt: skip

    out += md_table(
        ["区分", "日数", "銘柄の延べ数", "両方を満たす", "上昇確率だけで落ちた", "見込みだけで落ちた", "両方で落ちた",
         "上昇確率が条件以上の割合", "見込みがプラスの割合"],
        [cond_row("0件の日", zero), cond_row("1〜19件の日", d[(d["n_top"] > 0) & (d["n_top"] < TOP_LIMIT)]),
         cond_row("20件の日", d[d["n_top"] >= TOP_LIMIT])],
    )  # fmt: skip
    z_ok0 = int((zero["n_ok"] == 0).sum())
    z_a0 = int((zero["n_a"] == 0).sum())
    z_b0 = int((zero["n_b"] == 0).sum())
    z_mis = int((zero["n_ok"] > 0).sum())
    out.append(
        f"- 0件の日のうち、両方を満たす銘柄が0の日: {z_ok0}日、上昇確率が条件以上の銘柄が0の日: {z_a0}日、見込みがプラスの銘柄が0の日: {z_b0}日"
    )
    out.append(
        f"- 0件の日のうち、両方を満たす銘柄があるのに買い候補が0件の日: {z_mis}日(この条件以外の理由。順位付けの他の条件)"
    )
    out.append("")

    # --- 5.3 上昇確率の分布 ---
    out += ["### 5.3 上昇確率(up_prob。主となる日数)の分布: 予測日ごとの値の平均", ""]

    def dist_row(label: str, g: pd.DataFrame) -> list[str]:
        if g.empty:
            return [label, "0", "-", "-", "-", "-", "-"]
        return [
            label, str(len(g)), pct1(float(g["up_max"].mean())), pct1(float(g["up_max"].max())), pct1(float(g["up_p99"].mean())),
            pct1(float(g["up_med"].mean())), pct2_diff(float(g["ret_med"].mean())),
        ]  # fmt: skip

    out += md_table(
        ["区分", "日数", "最大(平均)", "最大(最大)", "上位1%点(平均)", "中央値(平均)", "見込みの中央値(平均)"],
        [dist_row("0件の日", zero), dist_row("買い候補がある日", other)],
    )

    # --- 5.4 補正の対応表 ---
    out += ["### 5.4 モデルバージョンごとの上昇確率の補正(対応表)と0件の日", ""]
    cal = calibration_info(ctx)
    m = ctx.models
    up20 = m[(m["horizon"] == const.MAIN_HORIZON) & (m["model_type"] == "UP_PROB")].set_index("model_version")
    g = d.groupby("model_version").agg(days=("pred_date", "count"), zero=("n_top", lambda s: int((s == 0).sum())),
                                       top_avg=("n_top", "mean"), up_max=("up_max", "mean"))  # fmt: skip
    vrows: list[list[str]] = []
    xs: list[float] = []
    ys: list[float] = []
    for v, r in g.iterrows():
        c = cal.get(str(v), {})
        mi = up20.loc[str(v)] if str(v) in up20.index else None
        z = float(r["zero"]) / float(r["days"])

        vrows.append(
            [
                str(v), "-" if mi is None else f"{mi['train_start_date']}〜{mi['train_end_date']}",
                "-" if mi is None else f"{int(float(str(mi['train_rows']))):,}",
                "-" if mi is None or pd.isna(mi["valid_score"]) else f"{float(mi['valid_score']):.3f}",
                f"50%→{cal_text(c, 'c50')}", f"60%→{cal_text(c, 'c60')}", f"75%→{cal_text(c, 'c75')}", cal_text(c, "cmax"),
                str(int(r["days"])), str(int(r["zero"])), pct1(z), num(float(r["top_avg"]), 1), pct1(float(r["up_max"])),
            ]
        )  # fmt: skip
        if c.get("c50") is not None:
            xs.append(float(c["c50"]))  # type: ignore[arg-type]
            ys.append(z)
    out += md_table(
        [
            "モデルバージョン",
            "学習データの期間",
            "学習の行数",
            "確認用の的中率(UP_PROB)",
            "補正(50%)",
            "補正(60%)",
            "補正(75%)",
            "補正後の最大",
            "使った予測日数",
            "0件の日",
            "0件の割合",
            "買い候補の平均件数",
            "up_probの最大(平均)",
        ],
        vrows,
    )
    if len(xs) >= 3 and len(set(xs)) > 1 and len(set(ys)) > 1:
        corr = float(pd.Series(xs).corr(pd.Series(ys)))
        out.append(
            f"- 補正(50% → ○%)の値と、0件の割合の相関係数: {corr:.2f}(モデルバージョン {len(xs)}個。1に近いほど同じ向き)"
        )
        out.append("")
        ctx.notes.append(f"補正(50% → ○%)と 0件の割合の相関係数は {corr:.2f}")
    no_cal = [str(v) for v in g.index if cal.get(str(v), {}).get("c50") is None]
    if no_cal:
        ctx.notes.append(f"補正の対応表がないモデルバージョンがある: {no_cal}")

    # --- 5.5 相場 ---
    out += ["### 5.5 年ごとの相場と買い候補", ""]
    yr = yr.assign(
        mm=pd.Series([ctx.market.get(x, (None, None))[0] for x in yr["pred_date"]], index=yr.index, dtype="float64"),
        mean=pd.Series([ctx.market.get(x, (None, None))[1] for x in yr["pred_date"]], index=yr.index, dtype="float64"),
        prev=pd.Series([ctx.market_by_target.get(x) for x in yr["pred_date"]], index=yr.index, dtype="float64"),
    )
    ymk: list[list[str]] = []
    for y, grp in yr.groupby("y"):
        mm_list = [v for v in grp["mm"].tolist() if v is not None and v == v]
        ymk.append(
            [
                str(y), str(len(grp)), str(int((grp["n_top"] == 0).sum())), num(float(grp["n_top"].mean()), 1),
                pct1(float(grp["up_max"].mean())), pct1(float(grp["n_a"].sum() / grp["n20"].sum())),
                pct2_diff(mean_opt(grp["mm"].tolist())), pct1(ratio(sum(1 for v in mm_list if v < 0), len(mm_list))),
                pct2_diff(mean_opt(grp["mean"].tolist())), pct2_diff(mean_opt(grp["prev"].tolist())),
            ]
        )  # fmt: skip
    out += md_table(
        [
            "年",
            "予測日の数",
            "0件の日",
            "買い候補の平均件数",
            "up_probの最大(平均)",
            "上昇確率が条件以上の割合",
            "市場の中央値(20日後。平均)",
            "市場の中央値がマイナスの日の割合",
            "全銘柄の平均騰落率(20日後)",
            "直前20営業日の市場の中央値(平均)",
        ],
        ymk,
    )
    out.append("0件の日と、そうでない日の相場の比較:")
    out.append("")

    def mk_row(label: str, g2: pd.DataFrame) -> list[str]:
        mm = [ctx.market.get(x, (None, None))[0] for x in g2["pred_date"]]
        mn = [ctx.market.get(x, (None, None))[1] for x in g2["pred_date"]]
        pv = [ctx.market_by_target.get(x) for x in g2["pred_date"]]
        mm_valid = [v for v in mm if v is not None]
        pv_valid = [v for v in pv if v is not None]
        return [
            label, str(len(g2)), pct2_diff(mean_opt(mm)), pct1(ratio(sum(1 for v in mm_valid if v < 0), len(mm_valid))),
            pct2_diff(mean_opt(mn)), pct2_diff(mean_opt(pv)), pct1(ratio(sum(1 for v in pv_valid if v < 0), len(pv_valid))),
        ]  # fmt: skip

    out += md_table(
        [
            "区分",
            "日数",
            "市場の中央値(20日後。平均)",
            "市場の中央値がマイナスの日の割合",
            "全銘柄の平均騰落率(20日後)",
            "直前20営業日の市場の中央値(平均)",
            "直前20営業日がマイナスの日の割合",
        ],
        [mk_row("0件の日", zero), mk_row("買い候補がある日", other)],
    )

    # --- 5.6 2024年以降 ---
    out += ["### 5.6 2024年以降に0件の日がなくなった理由の候補", ""]
    late = d[[x.year >= 2024 for x in d["pred_date"]]]
    early = d[[x.year < 2024 for x in d["pred_date"]]]
    out.append(
        f"- 2023年以前の予測日 {len(early)}日のうち0件 {int((early['n_top'] == 0).sum())}日、"
        f"2024年以降 {len(late)}日のうち0件 {int((late['n_top'] == 0).sum())}日"
    )
    for label, gg in [("2023年以前", early), ("2024年以降", late)]:
        vs = sorted(set(gg["model_version"]))
        trs = [up20.loc[v] for v in vs if v in up20.index]
        if trs:
            rows_avg = sum(float(t["train_rows"]) for t in trs) / len(trs)
            starts = min(t["train_start_date"] for t in trs)
            c50 = mean_opt([cal.get(v, {}).get("c50") for v in vs])
            cmax = mean_opt([cal.get(v, {}).get("cmax") for v in vs])
            out.append(
                f"- {label}: モデル {len(vs)}個、学習の行数の平均 {rows_avg:,.0f}、学習データの最も早い開始日 {starts}、"
                f"補正(50% → ○%)の平均 {pct1(c50)}、補正後の最大の平均 {pct1(cmax)}、"
                f"上昇確率が条件以上の銘柄の割合 {pct1(ratio(float(gg['n_a'].sum()), float(gg['n20'].sum())))}"
            )
    out.append("")

    # --- 5.7 原因の候補 ---
    out += ["### 5.7 原因の候補(数字の根拠。直し方の提案はしない)", ""]
    if len(zero) == 0:
        out.append("0件の日がないため、省略。")
        out.append("")
        return out
    cands: list[tuple[float, str]] = []
    sum_a = float(zero["fail_a"].sum() + zero["fail_both"].sum())
    sum_b = float(zero["fail_b"].sum() + zero["fail_both"].sum())
    tot = float(zero["n20"].sum())
    cands.append((
        sum_a / tot,
        f"上昇確率が {thr * 100:.0f}% 未満で落ちた銘柄: 0件の日の延べ {int(sum_a):,}/{int(tot):,}({pct1(sum_a / tot)})。"
        f"上昇確率が条件以上の銘柄が0の日は {z_a0}/{len(zero)}日。up_prob の最大の平均 0件の日 {pct1(float(zero['up_max'].mean()))}"
        f" / 買い候補がある日 {pct1(float(other['up_max'].mean()))}",
    ))  # fmt: skip
    cands.append((
        sum_b / tot,
        f"見込み(予測株価 ÷ 予測時の株価 − 1)がプラスでなく落ちた銘柄: 0件の日の延べ {int(sum_b):,}/{int(tot):,}({pct1(sum_b / tot)})。"
        f"見込みがプラスの銘柄が0の日は {z_b0}/{len(zero)}日。見込みの中央値の平均 0件の日 {pct2_diff(float(zero['ret_med'].mean()))}"
        f" / 買い候補がある日 {pct2_diff(float(other['ret_med'].mean()))}",
    ))  # fmt: skip
    cands.append((
        float(z_ok0) / len(zero),
        f"両方の条件を満たす銘柄が0の日: {z_ok0}/{len(zero)}日({pct1(z_ok0 / len(zero))})。条件を満たしても0件の日: {z_mis}日",
    ))  # fmt: skip
    if len(xs) >= 3:
        low_c = [(v, cal[str(v)]["c50"]) for v in g.index if cal.get(str(v), {}).get("c50") is not None]
        zero_vers = g[g["zero"] > 0].index
        zc = mean_opt([cal[str(v)]["c50"] for v in zero_vers if cal.get(str(v), {}).get("c50") is not None])
        oc = mean_opt(
            [cal[str(v)]["c50"] for v in g.index if v not in zero_vers and cal.get(str(v), {}).get("c50") is not None]
        )
        cands.append((
            0.5,
            f"補正で確率が下がった可能性: 0件の日があるモデルの補正(50% → ○%)の平均 {pct1(zc)}、0件の日がないモデル {pct1(oc)}"
            f"(モデル {len(low_c)}個。相関係数 {float(pd.Series(xs).corr(pd.Series(ys))) if len(set(xs)) > 1 and len(set(ys)) > 1 else float('nan'):.2f})",
        ))  # fmt: skip
    zmm = mean_opt([ctx.market.get(x, (None, None))[0] for x in zero["pred_date"]])
    omm = mean_opt([ctx.market.get(x, (None, None))[0] for x in other["pred_date"]])
    zpv = mean_opt([ctx.market_by_target.get(x) for x in zero["pred_date"]])
    opv = mean_opt([ctx.market_by_target.get(x) for x in other["pred_date"]])
    cands.append((
        0.4,
        f"相場が下げた(または下げていた)可能性: 20日後の市場の中央値の平均 0件の日 {pct2_diff(zmm)} / 買い候補がある日 {pct2_diff(omm)}、"
        f"直前20営業日の市場の中央値の平均 0件の日 {pct2_diff(zpv)} / 買い候補がある日 {pct2_diff(opv)}",
    ))  # fmt: skip
    for i, (_, text) in enumerate(sorted(cands, key=lambda t: -t[0]), 1):
        out.append(f"{i}. {text}")
    out.append("")
    out.append("(並びは、0件の日の銘柄の延べ数・日数の割合が大きい順の目安。因果の判断はしていない)")
    out.append("")
    return out


# ---------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------
def _parse_date(text: str) -> date:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"日付の形式が正しくありません({text})。YYYY-MM-DD で指定してください") from e


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="本番の初回の全処理の検証(読み取り専用)")
    parser.add_argument("--from", dest="date_from", type=_parse_date, default=DEFAULT_FROM, help="全処理の最初の日")
    parser.add_argument("--to", dest="date_to", type=_parse_date, default=DEFAULT_TO, help="全処理の最後の日")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    today = today_jst()
    logger = get_logger(LABEL, today)
    path: Path = resolve_path(const.LOG_DIR) / f"検証結果_本番初回_{today.strftime('%Y%m%d')}.md"
    logger.info("処理を開始します(全処理の期間: %s 〜 %s)", args.date_from, args.date_to)
    started = time.monotonic()
    ctx = Context(args.date_from, args.date_to)
    body: list[str] = []
    try:
        with db.connect() as conn:
            # 読み取り専用にする。間違って書き込もうとしても、DB が拒否する
            conn.execute("SET default_transaction_read_only = on")
            for name, func in [
                ("1 実行の完全さ", section_completeness),
                ("2 的中率", section_accuracy),
                ("3 買い候補の中身", section_candidates),
                ("4 上昇確率の確かさ", section_calibration),
            ]:
                t0 = time.monotonic()
                body += func(conn, ctx) + [""]
                logger.info("%s を集計しました(%.0f秒)", name, time.monotonic() - t0)
            load_market(conn, ctx)
        body += section_zero_days(ctx)
    except Exception:
        logger.exception("失敗しました")
        return 1

    head = [
        f"# 検証結果: 本番の初回の全処理({args.date_from}〜{args.date_to})",
        "",
        f"- 作成日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}(tools/verify_full_run.py。DB は読み取り専用)",
        "- 数値の書式: 割合は%で小数1桁、差は%で小数2桁",
        "",
    ]
    tail = ["## 気付いた点", ""] + [f"- {n}" for n in ctx.notes] + [""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(head + body + tail), encoding="utf-8")
    logger.info("処理を終了します(結果: %s、%.0f秒)", path, time.monotonic() - started)
    return 0


if __name__ == "__main__":
    sys.exit(main())
