"""買い候補と実際の株価を並べた HTML を作る道具(画面フォーマット_買い候補と実際の株価.md)。

本番のバッチからは呼ばない。DB は読み取り専用で使う。
予測日ごとの買い候補(上位20銘柄。メールと同じ)に、予測日数ごとの市場比・上昇確率・予測株価と、
答え合わせ(検証)で入った実際の株価を並べる。上昇確率は DB に登録された値(補正の仕組みを入れた後は補正後の値)。
--kind sector で、買い候補(業種分散)を並べる。

実行(stock_prediction_batch のフォルダで):
  python -m tools.make_candidates_html --from 2026-01-01 --to 2026-03-31
  python -m tools.make_candidates_html --from 2026-01-01 --to 2026-03-31 --kind sector
結果は const.py の CANDIDATES_HTML_DIR の
買い候補と実際の株価_YYYY-MM-DD_YYYY-MM-DD.html(--kind sector は末尾に _業種分散。--out で変えられる)。
"""

import argparse
import html
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.rows import DictRow

import const
from common import db
from common.args import today_jst
from common.env import resolve_path
from common.logger import get_logger
from common.stock_code import display_code
from core.features import cross_days_on, trendline_notes_on
from core.notify_core import (
    COLOR_QUIET,
    NOTE_LOW_LIQUIDITY,
    NOTE_SEPARATOR,
    cross_notes,
    is_blue_note,
    valuation_notes_on,
)

LABEL: str = "画面のHTML"
_WEEKDAYS: str = "月火水木金土日"

# 画面フォーマット 3章の CSS(元のファイルのまま)
CSS: str = """body{font-family:Meiryo,"Yu Gothic",sans-serif;margin:16px;color:#222;background:#fff;}
h1{font-size:20px;margin:0 0 6px;} h2{font-size:16px;margin:28px 0 4px;padding-top:4px;border-top:2px solid #557;}
.info{font-size:12px;color:#555;margin-bottom:8px;line-height:1.6;}
.toc{display:flex;flex-wrap:wrap;gap:4px 10px;font-size:13px;margin:8px 0 12px;} .toc a{color:#0055cc;}
.wrap{overflow-x:auto;}
table{border-collapse:separate;border-spacing:0;font-size:13px;}
th,td{border-right:1px solid #bbb;border-bottom:1px solid #bbb;padding:4px 6px;white-space:nowrap;}
thead tr:first-child th{border-top:1px solid #bbb;} th:first-child,td:first-child{border-left:1px solid #bbb;}
th{background:#dce6f1;} th.main{background:#d9ead3;} th.act{background:#fff2cc;}
td.n{text-align:right;} td.c{text-align:center;} td.b{font-weight:bold;}
tbody tr:nth-child(even) td{background:#f6f6f6;}
.up{color:#c00000;font-weight:bold;} .dn{color:#0055cc;font-weight:bold;} .eq{color:#666;font-weight:bold;}
tfoot td{background:#fff8dc;font-weight:bold;}
.fix{position:sticky;left:0;z-index:1;background:#fff;} th.fix{background:#dce6f1;} tfoot td.fix{background:#fff8dc;}
.top{font-size:12px;margin-left:8px;font-weight:normal;}
td.dash{text-align:center;color:#888;}
"""


@dataclass
class Cell:
    """1銘柄・1予測日数の値。actual は答え合わせが済んでいない場合は None。"""

    target_date: date
    rel_prob: Decimal
    up_prob: Decimal
    pred_price: Decimal
    actual: Decimal | None


@dataclass
class Row:
    """買い候補の1銘柄。"""

    rank: int
    code: str
    name: str
    base_price: Decimal
    cells: dict[int, Cell] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    market: str = ""  # 市場(銘柄マスタの市場区分名。例: プライム)


@dataclass(frozen=True)
class Kind:
    """買い候補の種類。rank_column は prediction の順位のカラム、segment は検証の集計の対象区分。"""

    rank_column: str
    segment: str
    label: str
    file_suffix: str


# 買い候補の種類(--kind の値 → 中身)。SQL のカラム名は、ここに書いた値だけを使う
KINDS: dict[str, Kind] = {
    const.CANDIDATE_KIND_TOP: Kind("buy_rank", const.SEGMENT_TOP, "買い候補", ""),
    const.CANDIDATE_KIND_SECTOR: Kind("buy_rank_sector", const.SEGMENT_TOP_SECTOR, "買い候補(業種分散)", "_業種分散"),
}


def kind_of(kind: str) -> Kind:
    """--kind の値から、買い候補の種類を返す。正しくない値は ValueError。"""
    if kind not in KINDS:
        raise ValueError(f"買い候補の種類が正しくありません({kind})。{' か '.join(KINDS)} を指定してください")
    return KINDS[kind]


def kind_description(kind: str) -> str:
    """HTML の説明に出す、買い候補の中身。"""
    if kind == const.CANDIDATE_KIND_SECTOR:
        return (
            f"買い候補(業種分散。同じ33業種は{const.SECTOR_CAP_PER_SECTOR}銘柄まで、"
            f"上位{const.BUY_CANDIDATE_SECTOR_COUNT}銘柄。メールと同じ内容)"
        )
    return f"買い候補(上位{const.BUY_CANDIDATE_COUNT}銘柄。メールと同じ内容)"


@dataclass
class Day:
    """予測日1日分。summary は予測日数 → (市場の中央値, 中央値を上回った銘柄の数)。"""

    pred_date: date
    model_version: str
    rows: list[Row]
    summary: dict[int, tuple[Decimal | None, int | None]] = field(default_factory=dict)


# =====================================================================
# DB から読む
# =====================================================================


def load_days(
    conn: psycopg.Connection[DictRow], date_from: date, date_to: date, kind: str = const.CANDIDATE_KIND_TOP
) -> list[Day]:
    """期間の予測日ごとに、買い候補(kind が sector なら買い候補(業種分散))と、その予測日数ごとの値を読む。"""
    k = kind_of(kind)
    rank = k.rank_column  # KINDS に書いたカラム名だけ
    rows = conn.execute(
        f"""
        SELECT p.pred_date, p.code, p.horizon, p.target_date, p.up_prob, p.rel_prob, p.pred_price, p.base_price,
               p.actual_price, p.verify_status, p.model_version, c.{rank} AS buy_rank, COALESCE(m.name, '') AS name,
               COALESCE(m.market_name, '') AS market_name
        FROM prediction p
        JOIN prediction c ON c.code = p.code AND c.pred_date = p.pred_date AND c.horizon = %s AND c.{rank} IS NOT NULL
        LEFT JOIN LATERAL (
            SELECT name, market_name FROM stock_master s WHERE s.code = p.code ORDER BY s.applied_date DESC LIMIT 1
        ) m ON true
        WHERE p.pred_date BETWEEN %s AND %s
        ORDER BY p.pred_date, c.{rank}, p.horizon
        """,
        (const.MAIN_HORIZON, date_from, date_to),
    ).fetchall()
    days: dict[date, Day] = {}
    by_key: dict[tuple[date, str], Row] = {}
    for r in rows:
        d: date = r["pred_date"]
        day = days.setdefault(d, Day(d, str(r["model_version"]), []))
        row = by_key.get((d, str(r["code"])))
        if row is None:
            row = Row(int(r["buy_rank"]), str(r["code"]), str(r["name"]), Decimal(r["base_price"]))
            row.market = str(r["market_name"])
            by_key[(d, row.code)] = row
            day.rows.append(row)
        verified = r["verify_status"] == const.VERIFY_STATUS_VERIFIED and r["actual_price"] is not None
        row.cells[int(r["horizon"])] = Cell(
            r["target_date"], Decimal(r["rel_prob"]), Decimal(r["up_prob"]), Decimal(r["pred_price"]),
            Decimal(r["actual_price"]) if verified else None,
        )  # fmt: skip
    for s in conn.execute(
        """
        SELECT a.pred_date, a.horizon, a.market_median, t.beat_count
        FROM verify_daily_summary a
        LEFT JOIN verify_daily_summary t ON t.pred_date = a.pred_date AND t.horizon = a.horizon AND t.segment = %s
        WHERE a.segment = %s AND a.pred_date BETWEEN %s AND %s
        """,
        (k.segment, const.SEGMENT_ALL, date_from, date_to),
    ).fetchall():
        found = days.get(s["pred_date"])
        if found is not None:
            median = None if s["market_median"] is None else Decimal(s["market_median"])
            found.summary[int(s["horizon"])] = (median, None if s["beat_count"] is None else int(s["beat_count"]))
    for day in days.values():
        add_notes(conn, day)
    return [days[d] for d in sorted(days)]


def add_notes(conn: psycopg.Connection[DictRow], day: Day) -> None:
    """備考(ストップ高・ストップ安、流動性注意、ゴールデンクロス・デッドクロス、トレンドラインの抜け、割安さ・稼ぐ力)。

    メールと同じ条件。
    """
    codes = [r.code for r in day.rows]
    flags = {
        str(f["code"]): f
        for f in conn.execute(
            "SELECT code, upper_limit_flag, lower_limit_flag FROM daily_price WHERE trade_date = %s AND code = ANY(%s)",
            (day.pred_date, codes),
        ).fetchall()
    }
    liquidity = conn.execute(
        """
        SELECT code, avg(COALESCE(turnover, 0)) AS avg_turnover
        FROM (
            SELECT code, turnover, row_number() OVER (PARTITION BY code ORDER BY trade_date DESC) AS n
            FROM daily_price WHERE code = ANY(%s) AND trade_date <= %s
        ) t
        WHERE n <= %s GROUP BY code
        """,
        (codes, day.pred_date, const.LIQUIDITY_AVG_DAYS),
    ).fetchall()
    low = {str(r["code"]) for r in liquidity if r["avg_turnover"] < const.LIQUIDITY_WARN_TURNOVER}
    frame = cross_days_on(conn, day.pred_date, codes, const.CROSS_DAYS_CAP)
    crosses = {
        str(code): cross_notes(short, mid)
        for code, short, mid in zip(
            frame["code"].tolist(),
            frame["days_since_cross_5_25"].astype(float).tolist(),
            frame["days_since_cross_25_75"].astype(float).tolist(),
            strict=True,
        )
    }
    trends = trendline_notes_on(conn, day.pred_date, codes)
    valuations = valuation_notes_on(conn, day.pred_date, codes)
    for r in day.rows:
        f = flags.get(r.code)
        if f is not None and f["upper_limit_flag"]:
            r.notes.append("ストップ高")
        if f is not None and f["lower_limit_flag"]:
            r.notes.append("ストップ安")
        if r.code in low:
            r.notes.append(NOTE_LOW_LIQUIDITY)
        r.notes.extend(crosses.get(r.code, []))
        r.notes.extend(trends.get(r.code, []))
        r.notes.extend(valuations.get(r.code, []))


# =====================================================================
# HTML
# =====================================================================


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _num(value: Decimal, sign: bool = False) -> str:
    """小数1桁、3桁区切り。"""
    return f"{value:+,.1f}" if sign else f"{value:,.1f}"


def _pct(value: Decimal, sign: bool = False) -> str:
    return f"{value * 100:+.1f}%" if sign else f"{value * 100:.1f}%"


def _cls(value: Decimal) -> str:
    return "up" if value > 0 else "dn" if value < 0 else "eq"


def _arrow(value: Decimal) -> str:
    return "↑" if value > 0 else "↓" if value < 0 else "→"


def _note_html(notes: list[str]) -> str:
    parts = []
    for n in notes:
        style = (
            f' style="color:{COLOR_QUIET};"'
            if n == NOTE_LOW_LIQUIDITY
            else ' class="dn"'
            if is_blue_note(n)
            else ' class="up"'
        )
        parts.append(f"<span{style}>{_e(n)}</span>")
    return NOTE_SEPARATOR.join(parts)


def _thead(day: Day) -> str:
    first = ['<th rowspan="2">順位</th>', '<th rowspan="2">コード</th>', '<th rowspan="2" class="fix">銘柄名</th>',
             '<th rowspan="2">市場</th>', '<th rowspan="2">現在値</th>']  # fmt: skip
    second: list[str] = []
    for h in const.PREDICT_HORIZONS:
        target = next((r.cells[h].target_date for r in day.rows if h in r.cells), None)
        when = f"(対象日 {target.month}/{target.day})" if target else ""
        main = ' class="main"' if h == const.MAIN_HORIZON else ""
        first.append(f'<th colspan="7"{main}>{h}日後{when}</th>')
        second += [f"<th{main}>{n}</th>" for n in ("市場比", "上昇確率", "予測株価")]
        second += [
            '<th class="act">実際の株価</th>', '<th class="act">差額<br>(実際の株価−現在値)</th>',
            '<th class="act">値上がり率<br>(差額÷現在値)</th>', '<th class="act">上昇<br>(↑/→/↓)</th>',
        ]  # fmt: skip
    first.append('<th rowspan="2">備考</th>')
    return f"<thead><tr>{''.join(first)}</tr><tr>{''.join(second)}</tr></thead>"


def _tbody(day: Day) -> str:
    out = []
    for r in day.rows:
        tds = [f'<td class="n">{r.rank}</td>', f"<td>{_e(display_code(r.code))}</td>",
               f'<td class="fix">{_e(r.name)}</td>', f"<td>{_e(r.market)}</td>",
               f'<td class="n">{_num(r.base_price)}</td>']  # fmt: skip
        for h in const.PREDICT_HORIZONS:
            c = r.cells.get(h)
            b = " b" if h == const.MAIN_HORIZON else ""
            if c is None:
                tds.append('<td colspan="7" class="dash">-</td>')
                continue
            tds += [f'<td class="n{b}">{_pct(c.rel_prob)}</td>', f'<td class="n{b}">{_pct(c.up_prob)}</td>',
                    f'<td class="n{b}">{_num(c.pred_price)}</td>']  # fmt: skip
            if c.actual is None:
                tds.append('<td colspan="4" class="dash">-</td>')
                continue
            diff = c.actual - r.base_price
            rate = diff / r.base_price if r.base_price else Decimal(0)
            k = _cls(diff)
            tds += [f'<td class="n {k}">{_num(c.actual)}</td>', f'<td class="n {k}">{_num(diff, True)}</td>',
                    f'<td class="n {k}">{_pct(rate, True)}</td>', f'<td class="c {k}">{_arrow(diff)}</td>']  # fmt: skip
        tds.append(f"<td>{_note_html(r.notes)}</td>")
        out.append("<tr>" + "".join(tds) + "</tr>")
    return "<tbody>" + "\n".join(out) + "</tbody>"


def _sum_cell(label: str, value: Decimal) -> str:
    """まとめの、ラベルと符号付きの%の値(プラスは赤、マイナスは青)のセル。"""
    return f'<td colspan="5" class="n">{label} <span class="{_cls(value)}">{_pct(value, True)}</span></td>'


def _tfoot(day: Day) -> str:
    """まとめ3行(平均の値上がり率と上がった銘柄 / 市場の中央値と中央値を上回った銘柄 / 中央値との差)。"""
    n = len(day.rows)
    lines: list[list[str]] = [[f'<td colspan="5" rowspan="3" class="fix">{n}銘柄のまとめ</td>'], [], []]
    for h in const.PREDICT_HORIZONS:
        rates = [
            (c.actual - r.base_price) / r.base_price
            for r in day.rows
            if (c := r.cells.get(h)) is not None and c.actual is not None and r.base_price
        ]
        median, beat = day.summary.get(h, (None, None))
        if not rates:
            for line in lines:
                line.append('<td colspan="7" class="dash">-</td>')
            continue
        avg = sum(rates, Decimal(0)) / len(rates)
        up = sum(1 for v in rates if v > 0)
        lines[0] += [_sum_cell("平均の値上がり率", avg),
                     f'<td colspan="2" class="c">上がった銘柄 ↑ {up} / {len(rates)}</td>']  # fmt: skip
        if median is None:
            lines[1].append('<td colspan="7" class="dash">-</td>')
            lines[2].append('<td colspan="7" class="dash">-</td>')
            continue
        diff = avg - median
        beat_text = "-" if beat is None else f"{beat} / {len(rates)}"
        lines[1] += [_sum_cell("市場の中央値", median),
                     f'<td colspan="2" class="c">中央値を上回った銘柄 {beat_text}</td>']  # fmt: skip
        lines[2] += [_sum_cell("中央値との差", diff),
                     '<td colspan="2"></td>']  # fmt: skip
    lines[0].append('<td rowspan="3"></td>')
    return "<tfoot>" + "\n".join("<tr>" + "".join(line) + "</tr>" for line in lines) + "</tfoot>"


def _label(d: date) -> str:
    return f"{d.isoformat()}({_WEEKDAYS[d.weekday()]})"


def build_html(days: list[Day], date_from: date, date_to: date, kind: str = const.CANDIDATE_KIND_TOP) -> str:
    """画面フォーマットの HTML を作る。"""
    k = kind_of(kind)
    title = f"{date_from.isoformat()}〜{date_to.isoformat()} {k.label}と実際の株価"
    horizons = "・".join(map(str, const.PREDICT_HORIZONS))
    info = (
        f"予測日ごとの{kind_description(kind)}に、{horizons}営業日後の実際の株価を並べたもの。"
        f"主となる日数: {const.MAIN_HORIZON}営業日後(緑の見出し)。"
        "上昇確率は補正した値(補正の仕組みを入れる前のモデルの予測は補正前の値)<br>"
        '実際の株価・差額・値上がり率・上昇: <span class="up">赤字 = 現在値より上がった</span>、'
        '<span class="dn">青字 = 下がった</span>、<span class="eq">灰色 = 同じ(→)</span>。'
        "答え合わせの日が来ていない日数は「-」。予測のない日(土日・祝日など)は表がありません"
    )
    toc = "".join(f'<a href="#d{d.pred_date.isoformat()}">{_label(d.pred_date)}</a>' for d in days)
    parts = [
        f'<!doctype html><html lang="ja"><head><meta charset="utf-8"><title>{_e(title)}</title>',
        f"<style>\n{CSS}</style></head><body>",
        f'<h1 id="top">{_e(title)}</h1>',
        f'<div class="info">{info}</div>',
        f'<div class="toc">{toc}</div>',
    ]
    if not days:
        parts.append(f"<p>この期間の{_e(k.label)}はありません。</p>")
    for d in days:
        parts.append(
            f'<h2 id="d{d.pred_date.isoformat()}">{_label(d.pred_date)} の{_e(k.label)}<span class="top">'
            f'(予測に使ったモデル: {_e(d.model_version)}) <a href="#top">▲目次へ</a></span></h2>'
            f'<div class="wrap"><table>{_thead(d)}{_tbody(d)}{_tfoot(d)}</table></div>'
        )
    parts.append("</body></html>\n")
    return "\n".join(parts)


# =====================================================================
# 実行
# =====================================================================


def _parse_date(text: str) -> date:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"日付の形式が正しくありません({text})。YYYY-MM-DD で指定してください") from e


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="買い候補と実際の株価の HTML を作る(読み取り専用)")
    parser.add_argument("--from", dest="date_from", type=_parse_date, required=True, help="最初の予測日(YYYY-MM-DD)")
    parser.add_argument("--to", dest="date_to", type=_parse_date, required=True, help="最後の予測日(YYYY-MM-DD)")
    parser.add_argument("--out", type=Path, default=None, help="出力するファイル(省略時は CANDIDATES_HTML_DIR の下)")
    parser.add_argument(
        "--kind",
        choices=list(KINDS),
        default=const.CANDIDATE_KIND_TOP,
        help="買い候補の種類(top: 買い候補、sector: 買い候補(業種分散))。省略すると top",
    )
    args = parser.parse_args(argv)
    if args.date_from > args.date_to:
        parser.error("--from は --to 以前の日付を指定してください")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    logger = get_logger(LABEL, today_jst())
    kind = KINDS[args.kind]
    name = f"買い候補と実際の株価_{args.date_from.isoformat()}_{args.date_to.isoformat()}{kind.file_suffix}.html"
    path: Path = (args.out or resolve_path(const.CANDIDATES_HTML_DIR) / name).resolve()
    logger.info("処理を開始します(予測日: %s 〜 %s、%s)", args.date_from, args.date_to, kind.label)
    try:
        with db.connect() as conn:
            # 読み取り専用にする。間違って書き込もうとしても、DB が拒否する
            conn.execute("SET default_transaction_read_only = on")
            days = load_days(conn, args.date_from, args.date_to, args.kind)
    except Exception:
        logger.exception("失敗しました")
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_html(days, args.date_from, args.date_to, args.kind), encoding="utf-8")
    logger.info("処理を終了します(予測日 %d日分。結果: %s)", len(days), path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
