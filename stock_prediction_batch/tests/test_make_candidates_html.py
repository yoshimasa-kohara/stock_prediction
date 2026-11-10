"""tools/make_candidates_html.py(買い候補と実際の株価の HTML)のテスト。"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import DictRow

import const
from tools import make_candidates_html as mk

D = date(2026, 2, 2)  # 予測日(月曜)


def _pred(conn: psycopg.Connection[DictRow], code: str, horizon: int, rank: int | None, actual: str | None,
          rank_sector: int | None = None) -> None:  # fmt: skip
    conn.execute(
        "INSERT INTO prediction (code, pred_date, horizon, target_date, up_prob, rel_prob, pred_price, base_price,"
        " model_version, buy_rank, buy_rank_sector, actual_price, change_rate, verify_status)"
        " VALUES (%s, %s, %s, %s, 0.6123, 0.7012, 1050, 1000, '20260131', %s, %s, %s, %s, %s)",
        (
            code, D, horizon, date(2026, 3, 4), rank, rank_sector,
            None if actual is None else Decimal(actual),
            None if actual is None else (Decimal(actual) - 1000) / 1000,
            "PENDING" if actual is None else "VERIFIED",
        ),
    )  # fmt: skip


def _summary(conn: psycopg.Connection[DictRow], horizon: int, segment: str, median: str | None, beat: int) -> None:
    conn.execute(
        "INSERT INTO verify_daily_summary (pred_date, horizon, segment, target_date, model_version, verified_count,"
        " hit_count, up_count, beat_count, sum_change_rate, sum_excess_rate, market_median, sum_price_error_rate,"
        " unverifiable_count, sector_verified_count, sector_beat_count, sum_sector_excess_rate)"
        " VALUES (%s, %s, %s, %s, '20260131', 2, 1, 1, %s, 0, 0, %s, 0, 0, 0, 0, 0)",
        (D, horizon, segment, D, beat, None if median is None else Decimal(median)),
    )


@pytest.fixture
def data(conn: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    conn.execute(
        "INSERT INTO stock_master (code, name, market_name, applied_date)"
        " VALUES ('11110', '□□銀行', 'プライム', %s), ('22220', '△△化学', 'グロース', %s)",
        (D, D),
    )
    conn.execute(
        "INSERT INTO daily_price (trade_date, code, close, turnover, adj_factor, upper_limit_flag, lower_limit_flag)"
        " VALUES (%s, '11110', 1000, 500000000, 1, true, false), (%s, '22220', 1000, 1000, 1, false, false)",
        (D, D),
    )
    # 11110 は5・20日後が答え合わせ済み(上がった・下がった)、60日後はまだ。22220 は5日後が同じ値
    # 買い候補(業種分散)は 22220(1位)と 33330(2位。20日後の行だけ)
    for code, rank, rank_sector, actuals in (
        ("11110", 1, None, ("1100", None, "950", None)),
        ("22220", 2, 1, ("1000", None, None, None)),
    ):
        for h, actual in zip(const.PREDICT_HORIZONS, actuals, strict=True):
            _pred(conn, code, h, rank, actual, rank_sector)
    _pred(conn, "33330", 20, None, "1200", 2)  # 買い候補ではない銘柄は出さない
    _summary(conn, 5, "ALL", "0.02", 0)
    _summary(conn, 5, "TOP", None, 1)
    _summary(conn, 5, "TOP_SECTOR", None, 0)
    _summary(conn, 20, "ALL", "-0.01", 0)
    _summary(conn, 20, "TOP", None, 0)
    _summary(conn, 20, "TOP_SECTOR", None, 2)
    return conn


def test_読み込みとHTML(data: psycopg.Connection[DictRow]) -> None:
    days = mk.load_days(data, date(2026, 1, 1), date(2026, 3, 31))
    assert [d.pred_date for d in days] == [D]
    day = days[0]
    assert [(r.rank, r.code, r.name) for r in day.rows] == [(1, "11110", "□□銀行"), (2, "22220", "△△化学")]
    assert day.rows[0].notes == ["ストップ高"] and day.rows[1].notes == ["流動性注意"]
    assert day.summary[5] == (Decimal("0.02"), 1) and day.summary[20] == (Decimal("-0.01"), 0)
    page = mk.build_html(days, date(2026, 1, 1), date(2026, 3, 31))
    assert "<title>2026-01-01〜2026-03-31 買い候補と実際の株価</title>" in page
    assert '<a href="#d2026-02-02">2026-02-02(月)</a>' in page and "(予測に使ったモデル: 20260131)" in page
    assert '<th colspan="7" class="main">20日後(対象日 3/4)</th>' in page
    # コードは5桁目が 0 なら4桁。銘柄名の次に市場の列
    assert [r.market for r in day.rows] == ["プライム", "グロース"]
    assert '<th rowspan="2" class="fix">銘柄名</th><th rowspan="2">市場</th>' in page
    assert '<td>1111</td><td class="fix">□□銀行</td><td>プライム</td>' in page
    assert '<td colspan="5" rowspan="3" class="fix">' in page
    # 主となる日数の値は太字。上がった = 赤、下がった = 青、同じ = 灰色
    assert '<td class="n b">70.1%</td><td class="n b">61.2%</td><td class="n b">1,050.0</td>' in page
    assert (
        '<td class="n up">1,100.0</td><td class="n up">+100.0</td><td class="n up">+10.0%</td><td class="c up">↑</td>'
        in page
    )
    assert '<td class="n dn">-5.0%</td><td class="c dn">↓</td>' in page
    assert '<td class="c eq">→</td>' in page
    # 答え合わせの日が来ていない日数は「-」
    assert '<td colspan="4" class="dash">-</td>' in page
    # まとめ: 5日後は平均 +5.0%、上がった銘柄 1 / 2、市場の中央値 +2.0%、中央値との差 +3.0%
    assert '平均の値上がり率 <span class="up">+5.0%</span>' in page and "上がった銘柄 ↑ 1 / 2" in page
    assert '市場の中央値 <span class="up">+2.0%</span>' in page and "中央値を上回った銘柄 1 / 2" in page
    assert '中央値との差 <span class="up">+3.0%</span>' in page
    assert '市場の中央値 <span class="dn">-1.0%</span>' in page
    # 10日後と60日後はまとめも「-」
    assert page.count('<td colspan="7" class="dash">-</td>') == 6
    assert '<span class="up">ストップ高</span>' in page and '<span style="color:#2e7d32;">流動性注意</span>' in page
    assert "33330" not in page


def test_買い候補の業種分散(data: psycopg.Connection[DictRow]) -> None:
    days = mk.load_days(data, date(2026, 1, 1), date(2026, 3, 31), const.CANDIDATE_KIND_SECTOR)
    day = days[0]
    assert [(r.rank, r.code) for r in day.rows] == [(1, "22220"), (2, "33330")]
    assert sorted(day.rows[1].cells) == [20]
    # まとめの「中央値を上回った銘柄」は、買い候補(業種分散)の集計(TOP_SECTOR)の値
    assert day.summary[5] == (Decimal("0.02"), 0) and day.summary[20] == (Decimal("-0.01"), 2)
    page = mk.build_html(days, date(2026, 1, 1), date(2026, 3, 31), const.CANDIDATE_KIND_SECTOR)
    assert "<title>2026-01-01〜2026-03-31 買い候補(業種分散)と実際の株価</title>" in page
    assert "予測日ごとの買い候補(業種分散。同じ33業種は3銘柄まで、上位30銘柄。メールと同じ内容)に" in page
    assert "2026-02-02(月) の買い候補(業種分散)" in page and "□□銀行" not in page
    assert "この期間の買い候補(業種分散)はありません。" in mk.build_html(
        [], date(2026, 1, 1), date(2026, 1, 31), const.CANDIDATE_KIND_SECTOR
    )
    with pytest.raises(ValueError, match=r"買い候補の種類が正しくありません\(x\)"):
        mk.load_days(data, date(2026, 1, 1), date(2026, 3, 31), "x")


def test_備考の色() -> None:
    html = mk._note_html(
        ["短期ゴールデンクロス発生(本日)", "中長期デッドクロス発生(3営業日前)", "流動性注意", "赤字予想(業績注意)"]
    )
    # 複数ある場合は「／」でつなぐ
    assert html == ('<span class="up">短期ゴールデンクロス発生(本日)</span>／'
                    '<span class="dn">中長期デッドクロス発生(3営業日前)</span>／'
                    '<span style="color:#2e7d32;">流動性注意</span>／'
                    '<span class="dn">赤字予想(業績注意)</span>')  # fmt: skip


def test_買い候補がない期間() -> None:
    assert "この期間の買い候補はありません。" in mk.build_html([], date(2026, 1, 1), date(2026, 1, 31))


def test_mainはファイルを書き読み取り専用で動く(
    data: psycopg.Connection[DictRow], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 出力先を省略した呼び出しが、資料のフォルダにファイルを作らないようにする
    monkeypatch.setattr(const, "CANDIDATES_HTML_DIR", str(tmp_path / "image"))
    out = tmp_path / "結果.html"
    assert mk.main(["--from", "2026-02-01", "--to", "2026-02-28", "--out", str(out)]) == 0
    assert "□□銀行" in out.read_text(encoding="utf-8")
    assert mk.main(["--from", "2026-02-01", "--to", "2026-02-28"]) == 0


def test_出力先を省略すると資料のimageフォルダに作る(
    data: psycopg.Connection[DictRow], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 初期値は const.py の CANDIDATES_HTML_DIR
    assert const.CANDIDATES_HTML_DIR == "【調査用の資料の出力先のパスを記載する】"
    monkeypatch.setattr(const, "CANDIDATES_HTML_DIR", str(tmp_path / "image"))
    assert mk.main(["--from", "2026-02-01", "--to", "2026-02-28"]) == 0
    assert (tmp_path / "image" / "買い候補と実際の株価_2026-02-01_2026-02-28.html").is_file()
    # 買い候補(業種分散)は、ファイル名の末尾に _業種分散
    assert mk.main(["--from", "2026-02-01", "--to", "2026-02-28", "--kind", "sector"]) == 0
    page = (tmp_path / "image" / "買い候補と実際の株価_2026-02-01_2026-02-28_業種分散.html").read_text(encoding="utf-8")
    assert "買い候補(業種分散)と実際の株価" in page and "△△化学" in page


def test_mainは失敗すると1(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def broken(*args: object) -> list[mk.Day]:
        raise RuntimeError("こわれた")

    monkeypatch.setattr(mk, "load_days", broken)
    assert mk.main(["--from", "2026-02-01", "--to", "2026-02-28", "--out", str(tmp_path / "x.html")]) == 1


def test_引数のチェック() -> None:
    with pytest.raises(SystemExit):
        mk.parse_args(["--from", "2026-02-28", "--to", "2026-02-01"])
    with pytest.raises(SystemExit):
        mk.parse_args(["--from", "2026/02/01", "--to", "2026-02-28"])
    with pytest.raises(SystemExit):
        mk.parse_args(["--from", "2026-02-01", "--to", "2026-02-28", "--kind", "x"])
    assert mk.parse_args(["--from", "2026-02-01", "--to", "2026-02-28"]).kind == "top"


def test_トレンドラインの抜けの備考(data: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> None:
    up = "上値抵抗線(接触3回)を上抜け(2営業日前)"
    down = "下値支持線(接触5回)を下抜け(下降が加速・本日)"

    def fake(conn: psycopg.Connection[DictRow], target: date, codes: list[str]) -> dict[str, list[str]]:
        assert target == D and codes == ["11110", "22220"]
        return {"11110": [up], "22220": [down]}

    monkeypatch.setattr(mk, "trendline_notes_on", fake)
    days = mk.load_days(data, date(2026, 1, 1), date(2026, 3, 31))
    # クロスの後、割安さ・稼ぐ力の前に並ぶ(メールと同じ順番)
    assert days[0].rows[0].notes == ["ストップ高", up] and days[0].rows[1].notes == ["流動性注意", down]
    page = mk.build_html(days, date(2026, 1, 1), date(2026, 3, 31))
    assert f'<span class="up">{up}</span>' in page and f'<span class="dn">{down}</span>' in page
