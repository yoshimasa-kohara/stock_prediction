"""買う時点で分かる指標で、相場の向き(下げ相場)を判断できるかの評価(読み取り専用)。

本番のバッチからは呼ばない。DB は SELECT だけで使う(セッションを読み取り専用にする)。
予測日ごとに「予測日の終値までのデータだけ」で指標を計算し、20営業日後(ほか 5・10・60)の
市場の中央値(verify_daily_summary.market_median)がプラスかどうかとの関係を調べる。

実行(stock_prediction_batch のフォルダで):
  python -m tools.evaluate_market_regime
結果は logs/検証結果_相場の向き_YYYYMMDD.md。
"""

import itertools
import sys
import time
from collections.abc import Sequence
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import psycopg
from psycopg.rows import DictRow, tuple_row

import const
from common import db
from common.env import resolve_path
from common.price_adjust import adjust_prices

HORIZONS: list[int] = [20, 5, 10, 60]
# 価格の指標のために読み始める日(予測日の最初 2021-11-15 の 75日移動平均に足りる分)
PRICE_START: date = date(2021, 4, 1)
# 学習(区切りを決める)期間の最後の年
TRAIN_LAST_YEAR: int = 2024
# 区切りの候補にする分位(学習期間の値の下位から数える)
QUANTILES: list[float] = [0.1, 0.2, 0.3, 0.4, 0.5]
# 合格の目安(20日後)
PASS_PLUS_RATE: float = 0.47
PASS_TOP_HIT: float = 0.60
PASS_WARN_MIN: float = 0.10
PASS_WARN_MAX: float = 0.35
PASS_KEEP_RATIO: float = 0.5

# 価格から作る指標(予測日数に関係なく1つ)。(列名, 説明)
PRICE_INDICATORS: list[tuple[str, str]] = [
    ("ret5", "全銘柄の直前5営業日の騰落率の中央値"),
    ("ret20", "全銘柄の直前20営業日の騰落率の中央値"),
    ("ret60", "全銘柄の直前60営業日の騰落率の中央値"),
    ("above25", "終値が25日移動平均より上の銘柄の割合"),
    ("above75", "終値が75日移動平均より上の銘柄の割合"),
    ("vol20", "全銘柄の直近20営業日の日次騰落率の標準偏差の中央値"),
]
# 予測から作る指標(予測日数ごと。補正の新旧で水準が変わる)
PRED_INDICATORS: list[tuple[str, str]] = [
    ("up_mean", "up_prob の平均"),
    ("up_share", "up_prob 50%以上の銘柄の割合"),
    ("exp_med", "見込み(予測株価÷予測時の株価−1)の中央値"),
]


# ---------------------------------------------------------------------
# 読み込み
# ---------------------------------------------------------------------
def load_prices(conn: psycopg.Connection[DictRow]) -> pd.DataFrame:
    """対象の市場の銘柄の調整済み終値を、日付 × 銘柄コードの表で返す。"""
    codes = [
        str(r["code"])
        for r in conn.execute(
            "SELECT code FROM stock_master WHERE market_code = ANY(%s)", (const.TARGET_MARKET_CODES,)
        ).fetchall()
    ]
    chunks: list[pd.DataFrame] = []
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            "SELECT code, trade_date, close::float8, adj_factor::float8 FROM daily_price"
            " WHERE trade_date >= %s AND code = ANY(%s)",
            (PRICE_START, codes),
        )
        while True:
            rows = cur.fetchmany(500_000)
            if not rows:
                break
            chunks.append(pd.DataFrame(rows, columns=["code", "trade_date", "close", "adj_factor"]))
    df = pd.concat(chunks, ignore_index=True)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    # 調整は最新日を基準にする。どの日の比も、その間の調整係数の積だけで決まり、基準日に依らない
    # (予測日より後の株価の値は使わない)
    for c in ("open", "high", "low", "volume"):
        df[c] = np.nan
    adj = adjust_prices(df, df["trade_date"].max().date())
    wide = adj.pivot(index="trade_date", columns="code", values="adj_close").sort_index()
    # 市場全体で取引のない日(銘柄の大半が NULL の日)を除く
    cnt = wide.notna().sum(axis=1)
    return wide.loc[cnt >= cnt.median() * 0.5]


def price_indicators(wide: pd.DataFrame) -> pd.DataFrame:
    """予測日(index)ごとの価格の指標。"""
    out = pd.DataFrame(index=wide.index)
    for n in (5, 20, 60):
        out[f"ret{n}"] = (wide / wide.shift(n) - 1.0).median(axis=1)
    for n, name in ((25, "above25"), (75, "above75")):
        ma = wide.rolling(n, min_periods=n).mean()
        valid = ma.notna() & wide.notna()
        out[name] = ((wide > ma) & valid).sum(axis=1) / valid.sum(axis=1).replace(0, np.nan)
    daily = wide.pct_change(fill_method=None)
    out["vol20"] = daily.rolling(20, min_periods=15).std().median(axis=1)
    # 日付の index(DatetimeIndex)を date の index にする
    out.index = pd.Index(pd.DatetimeIndex(out.index).date)
    return out


def load_pred_indicators(conn: psycopg.Connection[DictRow]) -> pd.DataFrame:
    """予測日 × 予測日数ごとの、予測から作る指標とモデルバージョン・作成日時。"""
    rows = conn.execute(
        """
        SELECT pred_date, horizon,
               avg(up_prob)::float8 AS up_mean,
               avg((up_prob >= 0.5)::int)::float8 AS up_share,
               (percentile_cont(0.5) WITHIN GROUP (ORDER BY pred_price / NULLIF(base_price, 0) - 1))::float8 AS exp_med,
               min(model_version) AS model_version, min(created_at) AS created_at, count(*) AS n
        FROM prediction GROUP BY pred_date, horizon
        """
    ).fetchall()
    return pd.DataFrame(rows)


def load_targets(conn: psycopg.Connection[DictRow]) -> pd.DataFrame:
    """予測日 × 予測日数ごとの、市場の中央値と TOP の的中率・市場の中央値を上回った割合。"""
    rows = conn.execute(
        """
        SELECT pred_date, horizon,
               max(market_median)::float8 AS market_median,
               max(hit_count) FILTER (WHERE segment = 'TOP')::float8
                 / NULLIF(max(verified_count) FILTER (WHERE segment = 'TOP'), 0) AS top_hit,
               max(beat_count) FILTER (WHERE segment = 'TOP')::float8
                 / NULLIF(max(verified_count) FILTER (WHERE segment = 'TOP'), 0) AS top_beat
        FROM verify_daily_summary GROUP BY pred_date, horizon
        """
    ).fetchall()
    df = pd.DataFrame(rows)
    return df[df["market_median"].notna()]


# ---------------------------------------------------------------------
# 表の整形
# ---------------------------------------------------------------------
def p1(x: float) -> str:
    return "-" if pd.isna(x) else f"{x * 100:.1f}%"


def d2(x: float) -> str:
    return "-" if pd.isna(x) else f"{x * 100:+.2f}%"


def md_table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * len(header)) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


# ---------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------
def group_stats(g: pd.DataFrame) -> list[str]:
    """日数、相場がプラスの日の割合、market_median の平均、TOP 的中率、TOP の市場超え割合。"""
    return [
        str(len(g)),
        p1(float((g["market_median"] >= 0).mean())),
        d2(float(g["market_median"].mean())),
        p1(float(g["top_hit"].mean())),
        p1(float(g["top_beat"].mean())),
    ]


def quintile_table(df: pd.DataFrame, col: str) -> str:
    """指標を5つの組に分けた表(組1が指標の最も低い組)。"""
    d = df.dropna(subset=[col])
    if len(d) < 10:
        return "(日数が足りない)"
    q = pd.qcut(d[col].rank(method="first"), 5, labels=False)
    rows: list[list[str]] = []
    for k in range(5):
        g = d[q == k]
        rows.append([f"組{k + 1}", f"{g[col].min():.4f}〜{g[col].max():.4f}"] + group_stats(g))
    return md_table(
        ["組", "指標の範囲", "日数", "プラスの日の割合", "market_median の平均", "TOP 的中率", "TOP 市場超え"], rows
    )


def spearman(df: pd.DataFrame, col: str) -> float:
    d = df.dropna(subset=[col, "market_median"])
    return float(d[col].corr(d["market_median"], method="spearman")) if len(d) >= 10 else float("nan")


def make_flag(df: pd.DataFrame, rules: list[tuple[str, int, float]]) -> pd.Series:
    """注意を出す日。rules は (指標, 向き, 区切り) の AND。向き=+1 は「値 ≤ 区切り」、-1 は「値 ≥ 区切り」。"""
    flag = pd.Series(True, index=df.index)
    for col, sign, thr in rules:
        flag &= (df[col] <= thr) if sign > 0 else (df[col] >= thr)
    return flag


def evaluate(df: pd.DataFrame, flag: pd.Series) -> dict[str, float]:
    """区切りの評価値。"""
    w, n = df[flag], df[~flag]
    plus = df["market_median"] >= 0
    return {
        "days": float(len(df)),
        "warn_rate": float(flag.mean()) if len(df) else float("nan"),
        "plus_warn": float(plus[flag].mean()) if flag.any() else float("nan"),
        "plus_none": float(plus[~flag].mean()) if (~flag).any() else float("nan"),
        "hit_warn": float(w["top_hit"].mean()) if len(w) else float("nan"),
        "hit_none": float(n["top_hit"].mean()) if len(n) else float("nan"),
        "miss": float(((~flag) & ~plus).sum()),
        "down_days": float((~plus).sum()),
    }


def search_rules(train: pd.DataFrame, cols: list[str]) -> list[tuple[list[tuple[str, int, float]], str]]:
    """学習期間だけで、指標の向きと区切りの候補(単独と2つの AND)を作る。"""
    sign: dict[str, int] = {c: (1 if spearman(train, c) > 0 else -1) for c in cols}
    thr: dict[tuple[str, float], float] = {}
    for c in cols:
        z = train[c] * sign[c]
        for q in QUANTILES:
            thr[(c, q)] = float(z.quantile(q)) * sign[c]  # 元の値の区切り
    cands: list[tuple[list[tuple[str, int, float]], str]] = []
    for c in cols:
        for q in QUANTILES:
            cands.append(([(c, sign[c], thr[(c, q)])], f"{c} 下位{int(q * 100)}%"))
    for c1, c2 in itertools.combinations(cols, 2):
        for q1 in QUANTILES:
            for q2 in QUANTILES:
                cands.append(
                    (
                        [(c1, sign[c1], thr[(c1, q1)]), (c2, sign[c2], thr[(c2, q2)])],
                        f"{c1} 下位{int(q1 * 100)}% かつ {c2} 下位{int(q2 * 100)}%",
                    )
                )
    return cands


def rule_text(rules: list[tuple[str, int, float]]) -> str:
    return " かつ ".join(f"{c} {'≤' if s > 0 else '≥'} {t:.4f}" for c, s, t in rules)


def threshold_section(df: pd.DataFrame, h: int) -> tuple[str, list[dict[str, Any]]]:
    """区切りの探索。学習 2021〜2024 で決め、2025〜2026 で確かめる。"""
    cols = [c for c, _ in PRICE_INDICATORS]
    years = pd.to_datetime(df["pred_date"]).dt.year
    train, test = df[years <= TRAIN_LAST_YEAR], df[years > TRAIN_LAST_YEAR]
    out: list[str] = []
    res: list[dict[str, Any]] = []
    if len(train) < 100 or len(test) < 30:
        return "(日数が足りない)", res
    for rules, label in search_rules(train, cols):
        tr = evaluate(train, make_flag(train, rules))
        te = evaluate(test, make_flag(test, rules))
        diff_tr = tr["plus_none"] - tr["plus_warn"]
        diff_te = te["plus_none"] - te["plus_warn"]
        res.append({"rules": rules, "label": label, "tr": tr, "te": te, "diff_tr": diff_tr, "diff_te": diff_te})
    # 学習で、注意を出す日の割合が目安内の候補を、差の大きい順に
    ok_rate = [r for r in res if PASS_WARN_MIN <= r["tr"]["warn_rate"] <= PASS_WARN_MAX and not pd.isna(r["diff_tr"])]
    ok_rate.sort(key=lambda r: -r["diff_tr"])
    header = ["区切り", "期間", "注意を出す日の割合", "注意あり: プラス割合", "注意なし: プラス割合", "差",
              "注意あり: TOP的中率", "注意なし: TOP的中率", "見逃し/下げ日"]  # fmt: skip

    def rows_of(r: dict[str, Any]) -> list[list[str]]:
        rr: list[list[str]] = []
        for nm, e, dd in (("学習2021-24", r["tr"], r["diff_tr"]), ("確かめ2025-26", r["te"], r["diff_te"])):
            rr.append([rule_text(r["rules"]) if nm.startswith("学習") else "", nm, p1(e["warn_rate"]),
                       p1(e["plus_warn"]), p1(e["plus_none"]), d2(dd), p1(e["hit_warn"]), p1(e["hit_none"]),
                       f"{int(e['miss'])}/{int(e['down_days'])}"])  # fmt: skip
        return rr

    warn_range = f"{int(PASS_WARN_MIN * 100)}〜{int(PASS_WARN_MAX * 100)}%"
    out.append(f"学習 {len(train)} 日、確かめ {len(test)} 日。候補 {len(res)} 個のうち、学習で注意を出す日の割合が"
               f" {warn_range} の {len(ok_rate)} 個を、学習の差が大きい順に上位10個。")  # fmt: skip
    rows: list[list[str]] = []
    for r in ok_rate[:10]:
        rows += rows_of(r)
    out.append(md_table(header, rows))
    # 合格の目安
    passed = [
        r for r in ok_rate
        if r["tr"]["plus_warn"] <= PASS_PLUS_RATE and r["tr"]["hit_warn"] <= PASS_TOP_HIT
        and r["diff_tr"] > 0 and r["diff_te"] >= r["diff_tr"] * PASS_KEEP_RATIO
    ]  # fmt: skip
    for r in passed:
        t = r["te"]
        r["te_ok"] = (
            t["plus_warn"] <= PASS_PLUS_RATE and t["hit_warn"] <= PASS_TOP_HIT
            and PASS_WARN_MIN <= t["warn_rate"] <= PASS_WARN_MAX
        )  # fmt: skip
    out.append(
        f"\n合格の目安を満たす区切り(学習で: 注意ありのプラス割合 ≤ {int(PASS_PLUS_RATE * 100)}%、TOP的中率 ≤"
        f" {int(PASS_TOP_HIT * 100)}%、注意を出す日の割合 {int(PASS_WARN_MIN * 100)}〜{int(PASS_WARN_MAX * 100)}%、"
        f"確かめで差が学習の半分以上): {len(passed)} 個"
    )
    if passed:
        prow = [[rule_text(r["rules"]), p1(r["tr"]["warn_rate"]), p1(r["tr"]["plus_warn"]), d2(r["diff_tr"]),
                 p1(r["te"]["warn_rate"]), p1(r["te"]["plus_warn"]), p1(r["te"]["hit_warn"]), d2(r["diff_te"]),
                 "○" if r["te_ok"] else "×"] for r in passed[:30]]  # fmt: skip
        out.append(md_table(["区切り", "学習: 注意割合", "学習: 注意ありプラス割合", "学習: 差", "確かめ: 注意割合",
                             "確かめ: 注意ありプラス割合", "確かめ: 注意あり TOP的中率", "確かめ: 差",
                             "確かめでも目安内"], prow))  # fmt: skip
        if len(passed) > 30:
            out.append(f"(ほか {len(passed) - 30} 個。差の大きい順に30個まで)")
    return "\n".join(out), ok_rate[:3]


def yearly_section(df: pd.DataFrame, cands: list[dict[str, Any]]) -> str:
    """上位の候補の、年ごとの効き方。"""
    years = pd.to_datetime(df["pred_date"]).dt.year
    out: list[str] = []
    base = [[str(y), *group_stats(df[years == y])] for y in sorted(years.unique())]
    out.append("年ごとの相場(全日):")
    out.append(md_table(["年", "日数", "プラスの日の割合", "market_median の平均", "TOP 的中率", "TOP 市場超え"], base))
    for r in cands:
        flag = make_flag(df, r["rules"])
        rows: list[list[str]] = []
        for y in sorted(years.unique()):
            m = years == y
            e = evaluate(df[m], flag[m])
            rows.append([str(y), str(int(e["days"])), p1(e["warn_rate"]), p1(e["plus_warn"]), p1(e["plus_none"]),
                         d2(e["plus_none"] - e["plus_warn"]) if not pd.isna(e["plus_warn"]) else "-",
                         p1(e["hit_warn"]), p1(e["hit_none"]), f"{int(e['miss'])}/{int(e['down_days'])}"])  # fmt: skip
        out.append(f"\n区切り: {rule_text(r['rules'])}")
        out.append(md_table(["年", "日数", "注意を出す日の割合", "注意あり: プラス割合", "注意なし: プラス割合", "差",
                             "注意あり: TOP的中率", "注意なし: TOP的中率", "見逃し/下げ日"], rows))  # fmt: skip
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    t0 = time.time()
    with db.connect() as conn:
        conn.execute("SET default_transaction_read_only = on")  # SELECT のみ
        print("株価を読み込み中...", flush=True)
        wide = load_prices(conn)
        print(f"  株価 {wide.shape} {time.time() - t0:.0f}秒", flush=True)
        pind = price_indicators(wide)
        del wide
        print(f"  価格の指標 {time.time() - t0:.0f}秒", flush=True)
        pred = load_pred_indicators(conn)
        tgt = load_targets(conn)
        print(f"  予測・検証 {time.time() - t0:.0f}秒", flush=True)

    # 補正の新旧の境目: 2026-11-05 以降に作られた予測のうち、最も早い予測日
    pred["created_at"] = pd.to_datetime(pred["created_at"], utc=True).dt.tz_convert("Asia/Tokyo")
    new = pred[pred["created_at"] >= pd.Timestamp("2026-11-05", tz="Asia/Tokyo")]
    boundary: date = new["pred_date"].min()
    mv_pre = pred[(pred["pred_date"] < boundary)]["model_version"].max()
    mv_post = pred[(pred["pred_date"] >= boundary)]["model_version"].min()

    md: list[str] = []
    today = date.today()
    md.append(f"# 検証結果: 相場の向きの評価({today})\n")
    md.append("買う時点(予測日の終値まで)で分かる指標で、20営業日後(ほか 5・10・60)の市場の中央値(market_median)が"
              "プラスかどうかを判断できるかの評価。DB は読み取りだけ。\n")  # fmt: skip
    md.append("## 0. 確かめたこと\n")
    pr = pred[pred["horizon"] == const.MAIN_HORIZON]
    md.append(f"- 予測日の範囲: {pr['pred_date'].min()} 〜 {pr['pred_date'].max()}({pr['pred_date'].nunique()} 日)")
    mv = pred["model_version"]
    md.append(f"- モデルバージョン: {mv.nunique()} 個({mv.min()} 〜 {mv.max()})")
    md.append(f"- 補正の新旧の境目: 予測日 **{boundary}** から新"
              f"(モデルバージョン {mv_post} から。直前の旧は {mv_pre})。"
              "予測の作成日時(prediction.created_at)が 2026-11-05 以降の行のうち最も早い予測日")  # fmt: skip
    md.append("- 検証済み(market_median あり)の予測日数: " + ", ".join(
        f"{h}日後 {tgt[tgt['horizon'] == h]['pred_date'].nunique()}" for h in HORIZONS))  # fmt: skip
    md.append("- 価格の指標の対象: stock_master の市場区分が TARGET_MARKET_CODES の銘柄(現在の銘柄マスタ。"
              "上場廃止の銘柄の行も残るので、当時の銘柄は含む)。"
              "指標は予測日の終値までの調整済み終値だけで計算\n")  # fmt: skip

    for h in HORIZONS:
        t = tgt[tgt["horizon"] == h].drop(columns="horizon")
        p = pred[pred["horizon"] == h].drop(columns=["horizon", "model_version", "created_at", "n"])
        df = t.merge(p, on="pred_date", how="left").merge(
            pind, left_on="pred_date", right_index=True, how="left"
        ).sort_values("pred_date").reset_index(drop=True)  # fmt: skip
        df["regime"] = np.where(df["pred_date"] >= boundary, "新", "旧")
        df_p = df.dropna(subset=[c for c, _ in PRICE_INDICATORS])
        md.append(f"## {h}営業日後{'(主)' if h == const.MAIN_HORIZON else ''}\n")
        md.append(f"### {h}日後 全体: 予測日 {len(df)} 日(価格の指標が揃う {len(df_p)} 日)\n")
        md.append(md_table(["", "日数", "プラスの日の割合", "market_median の平均", "TOP 的中率", "TOP 市場超え"],
                           [["全日", *group_stats(df)],
                            ["market_median ≥ 0", *group_stats(df[df["market_median"] >= 0])],
                            ["market_median < 0", *group_stats(df[df["market_median"] < 0])]]))  # fmt: skip
        md.append("\n### 指標ごとの五分位と順位相関(スピアマン。指標と market_median)\n")
        for col, desc in PRICE_INDICATORS:
            md.append(f"**{col}**({desc}) スピアマン = {spearman(df_p, col):+.3f}\n")
            md.append(quintile_table(df_p, col) + "\n")
        for col, desc in PRED_INDICATORS:
            for rg in ("旧", "新"):
                g = df[df["regime"] == rg]
                md.append(f"**{col}**({desc}) 補正{rg}(予測日 {'< ' if rg == '旧' else '≥ '}{boundary}) "
                          f"スピアマン = {spearman(g, col):+.3f}\n")  # fmt: skip
                md.append(quintile_table(g, col) + "\n")
        md.append(f"### {h}日後 区切りの探索\n")
        sec, top = threshold_section(df_p, h)
        md.append(sec + "\n")
        if top:
            md.append(f"### {h}日後 上位の候補の年ごとの効き方\n")
            md.append(yearly_section(df_p, top) + "\n")

    md.append("## 注意書き\n")
    md.append("- 20日後の騰落率は、予測日ごとの期間が重なる(隣の日とほぼ同じ期間)。独立な標本は約60(1215÷20)しかない。"
              "5日後で約240、10日後で約120、60日後で約20。日数の多さほど確かさは増えない。")  # fmt: skip
    md.append("- 2022・2023・2024・2025・2026 の相場の向きに偏りがないかは、上の「年ごとの相場」を見る。")
    md.append(
        "- 区切りの候補は数百個あり、学習で選ぶ過程で当たりやすいものが残る(多重比較)。確かめ期間の成績が本当の目安。"
    )
    md.append("- 補正の新旧で水準が変わる予測由来の指標(up_mean など)は、新が約1年分(その多くが確かめ期間)しかなく、"
              "区切りの探索には入れていない。")  # fmt: skip
    md.append(f"\n(実行時間 {time.time() - t0:.0f} 秒)")

    out = resolve_path("logs") / f"検証結果_相場の向き_{today:%Y%m%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(md), encoding="utf-8")
    print(f"出力: {out}  {time.time() - t0:.0f}秒")
    return 0


if __name__ == "__main__":
    sys.exit(main())
