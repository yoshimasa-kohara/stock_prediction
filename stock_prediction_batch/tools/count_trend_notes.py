"""トレンドラインの抜けの備考が、買い候補にどれくらい付くかを数える道具。

本番のバッチからは呼ばない。DB は読み取り専用で使う。
期間の予測日ごとに、買い候補(主となる日数の上位20銘柄。prediction.buy_rank がある行)について、
メールと同じ計算(core/features.py の trendline_notes_on)で備考を作り、付いた数と割合をログと CSV に出す。
接触の幅(--touch)と抜けの幅(--break)は、カンマで区切って複数指定でき、全ての組み合わせを数える。

実行(stock_prediction_batch のフォルダで):
  python -m tools.count_trend_notes --from 2026-01-01 --to 2026-07-22
  python -m tools.count_trend_notes --from 2026-01-01 --to 2026-07-22 --touch 0.01,0.02 --break 0.02,0.03,0.05
結果は logs/trend_notes_YYYYMMDD_HHMM.csv(1行 = 接触の幅 × 抜けの幅)。
"""

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
from common import db
from common.args import today_jst
from common.env import resolve_path
from common.logger import get_logger
from core import trendline
from core.features import trendline_notes_on

LABEL: str = "トレンドラインの備考の割合"

# CSV の列
CSV_COLUMNS: list[str] = [
    "touch_tolerance", "break_rate", "days", "candidates",
    "resist_count", "resist_rate", "support_count", "support_rate", "any_count", "any_rate",
    "turn_count", "accel_count", "flat_count",
]  # fmt: skip


def load_candidates(conn: psycopg.Connection[DictRow], date_from: date, date_to: date) -> dict[date, list[str]]:
    """期間の予測日ごとの、買い候補の銘柄コード(主となる日数の買い候補の順位がある行)。"""
    days: dict[date, list[str]] = {}
    for r in conn.execute(
        """
        SELECT pred_date, code FROM prediction
        WHERE horizon = %s AND buy_rank IS NOT NULL AND pred_date BETWEEN %s AND %s
        ORDER BY pred_date, buy_rank
        """,
        (const.MAIN_HORIZON, date_from, date_to),
    ).fetchall():
        days.setdefault(r["pred_date"], []).append(str(r["code"]))
    return days


def count_notes(
    conn: psycopg.Connection[DictRow], days: dict[date, list[str]], touch: float, brk: float
) -> dict[str, Any]:
    """1つの幅の組み合わせで、備考が付いた買い候補の数を数える。"""
    total = resist = support = either = turn = accel = flat = 0
    for d, codes in days.items():
        notes = trendline_notes_on(conn, d, codes, touch_tolerance=touch, break_rate=brk)
        total += len(codes)
        for code in codes:
            found = notes.get(code, [])
            has_resist = any(n.startswith("上値抵抗線") for n in found)
            has_support = any(n.startswith(trendline.NOTE_SUPPORT_PREFIX) for n in found)
            resist += has_resist
            support += has_support
            either += has_resist or has_support
            for n in found:
                if "から転換" in n:
                    turn += 1
                elif "が加速" in n:
                    accel += 1
                else:
                    flat += 1

    def rate(n: int) -> float:
        return n / total if total else 0.0

    return {
        "touch_tolerance": touch, "break_rate": brk, "days": len(days), "candidates": total,
        "resist_count": resist, "resist_rate": rate(resist), "support_count": support, "support_rate": rate(support),
        "any_count": either, "any_rate": rate(either), "turn_count": turn, "accel_count": accel, "flat_count": flat,
    }  # fmt: skip


def _parse_date(text: str) -> date:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"日付の形式が正しくありません({text})。YYYY-MM-DD で指定してください") from e


def _parse_rates(text: str) -> list[float]:
    try:
        values = [float(v) for v in text.split(",") if v.strip()]
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"幅の形式が正しくありません({text})。例: 0.02,0.03") from e
    if not values or any(not 0 < v < 1 for v in values):
        raise argparse.ArgumentTypeError(f"幅は 0 より大きく 1 より小さい値で指定してください({text})")
    return values


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="トレンドラインの抜けの備考が買い候補に付く割合を数える(読み取り専用)")
    parser.add_argument("--from", dest="date_from", type=_parse_date, required=True, help="最初の予測日(YYYY-MM-DD)")
    parser.add_argument("--to", dest="date_to", type=_parse_date, required=True, help="最後の予測日(YYYY-MM-DD)")
    parser.add_argument(
        "--touch", type=_parse_rates, default=[const.TREND_TOUCH_TOLERANCE], help="接触の幅(例: 0.01,0.02)"
    )
    parser.add_argument("--break", dest="break_rates", type=_parse_rates, default=[const.TREND_BREAK_RATE],
                        help="抜けの幅(例: 0.02,0.03)")  # fmt: skip
    args = parser.parse_args(argv)
    if args.date_from > args.date_to:
        parser.error("--from は --to 以前の日付を指定してください")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    logger = get_logger(LABEL, today_jst())
    path: Path = resolve_path(const.LOG_DIR) / f"trend_notes_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    logger.info(
        "処理を開始します(予測日: %s 〜 %s、接触の幅: %s、抜けの幅: %s)",
        args.date_from, args.date_to, ",".join(f"{v:g}" for v in args.touch),
        ",".join(f"{v:g}" for v in args.break_rates),
    )  # fmt: skip
    started = time.monotonic()
    results: list[dict[str, Any]] = []
    try:
        with db.connect() as conn:
            # 読み取り専用にする。間違って書き込もうとしても、DB が拒否する
            conn.execute("SET default_transaction_read_only = on")
            days = load_candidates(conn, args.date_from, args.date_to)
            if not days:
                logger.warning("期間に買い候補がありません")
            for touch in args.touch:
                for brk in args.break_rates:
                    r = count_notes(conn, days, touch, brk)
                    results.append(r)
                    logger.info(
                        "接触の幅 %g 抜けの幅 %g: 買い候補 %d件(%d日)のうち、上値抵抗線の上抜け %d件(%.1f%%)、"
                        "下値支持線の下抜け %d件(%.1f%%)、どちらか %d件(%.1f%%)。転換 %d件、加速 %d件、水平 %d件",
                        touch, brk, r["candidates"], r["days"], r["resist_count"], r["resist_rate"] * 100,
                        r["support_count"], r["support_rate"] * 100, r["any_count"], r["any_rate"] * 100,
                        r["turn_count"], r["accel_count"], r["flat_count"],
                    )  # fmt: skip
    except Exception:
        logger.exception("失敗しました")
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    # Excel で開けるように、BOM 付きの UTF-8 で書く
    pd.DataFrame(results, columns=CSV_COLUMNS).to_csv(path, index=False, encoding="utf-8-sig", float_format="%.6f")
    logger.info("処理を終了します(結果: %s、%.0f秒)", path, time.monotonic() - started)
    return 0


if __name__ == "__main__":
    sys.exit(main())
