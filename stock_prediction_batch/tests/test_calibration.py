"""core/calibration.py(上昇確率の補正)のテスト。"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import const
from core import calibration as cal


def test_順番が逆にならないように平均してまとめる() -> None:
    # 2番目と3番目が逆になっているので、まとめて平均する(重みは件数)
    result = cal._pav(np.array([0.1, 0.6, 0.4, 0.8]), np.array([1.0, 1.0, 3.0, 1.0]))
    np.testing.assert_allclose(result, [0.1, 0.45, 0.45, 0.8])


def test_対応表を作る() -> None:
    rng = np.random.default_rng(0)
    predicted = rng.uniform(0, 1, 20000)
    # 実際に上がる確率は、モデルの確率の半分 + 0.25(高めに出ているモデル)
    actual = (rng.uniform(0, 1, 20000) < predicted * 0.5 + 0.25).astype(int)
    c = cal.fit_calibration(predicted, actual, bins=20, shrink_alpha=0, edge_min_count=1)
    assert len(c.x) == len(c.y) == 20
    assert c.x == sorted(c.x) and c.y == sorted(c.y)  # 順番は逆にならない
    assert min(c.y) >= 0 and max(c.y) <= 1
    # 75% は約62.5%、50% は約50% に直る
    assert c.apply(np.array([0.75]))[0] == pytest.approx(0.625, abs=0.04)
    assert c.apply(np.array([0.5]))[0] == pytest.approx(0.5, abs=0.04)


def test_点の間は直線でつなぎ範囲の外は端の値にする() -> None:
    c = cal.Calibration(x=[0.2, 0.6], y=[0.3, 0.5])
    np.testing.assert_allclose(c.apply(np.array([0.0, 0.2, 0.4, 0.6, 1.0])), [0.3, 0.3, 0.4, 0.5, 0.5])


def test_空の値は除き行がなければ失敗() -> None:
    c = cal.fit_calibration(np.array([0.2, np.nan, 0.8]), np.array([0, 1, 1]), bins=5, shrink_alpha=0, edge_min_count=1)
    assert c.x == [0.2, 0.8] and c.y == [0.0, 1.0]
    with pytest.raises(ValueError, match="補正の対応表を作る行がありません"):
        cal.fit_calibration(np.array([np.nan]), np.array([1]))


def test_ファイルに保存して読み込む(tmp_path: Path) -> None:
    assert cal.calibration_file_name("上昇確率_20日後.txt") == "上昇確率_20日後_補正.json"
    c = cal.Calibration(x=[0.1, 0.9], y=[0.2, 0.6])
    path = tmp_path / "models" / cal.calibration_file_name("上昇確率_20日後.txt")
    cal.save_calibration(c, path)
    assert cal.load_calibration(path) == c
    assert '"x": [0.1, 0.9]' in path.read_text(encoding="utf-8")
    assert cal.load_calibration(tmp_path / "ない.json") is None


def test_設定値の初期値() -> None:
    assert const.CALIBRATION_HISTORY_WEEKS == 52
    assert const.CALIBRATION_SHRINK_ALPHA == 1000
    assert const.CALIBRATION_EDGE_MIN_COUNT == 5000
    assert const.BUY_CANDIDATE_FALLBACK_TOP_RATE == 0.25


# =====================================================================
# B: 件数が少ない組は、全体の上昇率に近づける
# =====================================================================


def test_組の割合を基準率に近づける() -> None:
    predicted = np.array([0.1] * 100 + [0.9] * 100)
    actual = np.array([1] * 10 + [0] * 90 + [1] * 90 + [0] * 10)  # 下の組は 10%、上の組は 90%、基準率は 50%
    c = cal.fit_calibration(predicted, actual, bins=2, shrink_alpha=100, edge_min_count=1)
    # (10 + 100 × 0.5) ÷ (100 + 100) = 0.3、(90 + 100 × 0.5) ÷ (100 + 100) = 0.7
    assert c.y == [0.3, 0.7]
    # α が 0 なら近づけない
    assert cal.fit_calibration(predicted, actual, bins=2, shrink_alpha=0, edge_min_count=1).y == [0.1, 0.9]


def test_省略するとconstのαと端の件数を使う(monkeypatch: pytest.MonkeyPatch) -> None:
    predicted = np.array([0.1] * 100 + [0.9] * 100)
    actual = np.array([1] * 10 + [0] * 90 + [1] * 90 + [0] * 10)
    monkeypatch.setattr(const, "CALIBRATION_SHRINK_ALPHA", 100)
    monkeypatch.setattr(const, "CALIBRATION_EDGE_MIN_COUNT", 50)
    assert cal.fit_calibration(predicted, actual, bins=2).y == [0.3, 0.7]
    # 件数が足りなければ1つにまとまる
    monkeypatch.setattr(const, "CALIBRATION_EDGE_MIN_COUNT", 101)
    assert cal.fit_calibration(predicted, actual, bins=2).y == [0.5]


# =====================================================================
# C: 端の組は件数がたまるまでまとめる
# =====================================================================


def test_端の組は最低の件数までまとめる() -> None:
    predicted = np.arange(100) / 100
    actual = (predicted >= 0.5).astype(int)
    c = cal.fit_calibration(predicted, actual, bins=10, shrink_alpha=0, edge_min_count=30)
    # 10件ずつの10組のうち、下の3組と上の3組をそれぞれまとめて6組になる
    assert len(c.x) == 6
    assert c.x[0] == pytest.approx(np.arange(30).mean() / 100)
    assert c.x[-1] == pytest.approx(np.arange(70, 100).mean() / 100)
    assert c.x[1:-1] == pytest.approx([0.345, 0.445, 0.545, 0.645])


def test_行が少なければまとめられるだけまとめる() -> None:
    predicted = np.array([0.2, 0.4, 0.6, 0.8])
    actual = np.array([0, 0, 1, 1])
    c = cal.fit_calibration(predicted, actual, bins=4, shrink_alpha=0, edge_min_count=5000)
    assert c.x == [0.5] and c.y == [0.5]
    # 1つの組でも当てはめられる(全ての確率が同じ値になる)
    np.testing.assert_allclose(c.apply(np.array([0.1, 0.9])), [0.5, 0.5])


# =====================================================================
# A: 補正用データ
# =====================================================================


def _frame(rows: list[tuple[str, str, float, int]], horizon: int = 20) -> pd.DataFrame:
    return pd.DataFrame(
        {
            cal.COL_HORIZON: [horizon] * len(rows),
            cal.COL_CODE: [r[0] for r in rows],
            cal.COL_DATE: pd.to_datetime([r[1] for r in rows]),
            cal.COL_RAW_PROB: [r[2] for r in rows],
            cal.COL_ACTUAL: [r[3] for r in rows],
        }
    )


def _rows(data: pd.DataFrame) -> list[tuple[str, str, float]]:
    dates = data[cal.COL_DATE].dt.strftime("%Y-%m-%d")
    return list(zip(data[cal.COL_CODE], dates, data[cal.COL_RAW_PROB], strict=True))


def test_同じ銘柄と日付は新しい値で置き換える() -> None:
    stored = _frame([("11110", "2026-01-05", 0.1, 0), ("22220", "2026-01-05", 0.2, 0)])
    current = _frame([("11110", "2026-01-05", 0.9, 1)])
    kept, used = cal.merge_calibration_data(stored, current)
    assert _rows(kept) == [("11110", "2026-01-05", 0.9), ("22220", "2026-01-05", 0.2)]
    assert _rows(used) == _rows(kept)


def test_最後の日から52週より前の行は使わず残さない() -> None:
    # 最後の日 2026-06-30 の52週前は 2025-07-01。その日は「より後」に入らない
    stored = _frame([("11110", "2025-07-01", 0.1, 0), ("11110", "2025-07-02", 0.2, 0), ("11110", "2024-01-05", 0.3, 0)])
    current = _frame([("11110", "2026-06-30", 0.9, 1)])
    kept, used = cal.merge_calibration_data(stored, current)
    assert _rows(used) == [("11110", "2025-07-02", 0.2), ("11110", "2026-06-30", 0.9)]
    assert _rows(kept) == _rows(used)


def test_確認用の最後の日より後の行は使わないがファイルには残す() -> None:
    # 過去の日付で学習し直した場合: より新しいモデルの行(答えは指定日より後)が残っている
    stored = _frame([("11110", "2026-06-29", 0.1, 0), ("11110", "2026-07-01", 0.2, 1), ("11110", "2026-09-01", 0.3, 1)])
    current = _frame([("11110", "2026-06-30", 0.9, 1)])
    kept, used = cal.merge_calibration_data(stored, current)
    assert _rows(used) == [("11110", "2026-06-29", 0.1), ("11110", "2026-06-30", 0.9)]
    assert _rows(kept) == [
        ("11110", "2026-06-29", 0.1),
        ("11110", "2026-06-30", 0.9),
        ("11110", "2026-07-01", 0.2),
        ("11110", "2026-09-01", 0.3),
    ]


def test_貯めた行がなければ今回の行だけで作る(tmp_path: Path) -> None:
    stored = cal.load_calibration_data(tmp_path / "ない.csv.gz")
    assert stored.empty
    current = _frame([("11110", "2026-06-30", 0.9, 1)])
    kept, used = cal.merge_calibration_data(stored, current)
    assert _rows(kept) == _rows(used) == [("11110", "2026-06-30", 0.9)]


def test_今回の確認用の行は週数の設定によらず必ず使う(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(const, "CALIBRATION_HISTORY_WEEKS", 1)
    current = _frame([("11110", "2026-01-05", 0.5, 1), ("11110", "2026-06-30", 0.9, 1)])
    _, used = cal.merge_calibration_data(_frame([("11110", "2026-02-02", 0.2, 0)]), current)
    assert _rows(used) == [("11110", "2026-01-05", 0.5), ("11110", "2026-06-30", 0.9)]


def test_補正用データを保存して読み込む(tmp_path: Path) -> None:
    assert cal.calibration_data_path("上昇確率_20日後.txt").parts[-2:] == (
        "補正用データ",
        "上昇確率_20日後_確認用.csv.gz",
    )
    path = tmp_path / "補正用データ" / "上昇確率_20日後_確認用.csv.gz"
    # 銘柄コードは文字列のまま(先頭の 0、英字を含むコード)
    data = _frame([("01230", "2026-01-05", 0.123456789, 1), ("130A0", "2026-01-06", 0.5, 0)])
    cal.save_calibration_data(data, path)
    loaded = cal.load_calibration_data(path)
    assert list(loaded.columns) == cal.CALIBRATION_DATA_COLUMNS
    assert _rows(loaded) == _rows(data)
    assert list(loaded[cal.COL_ACTUAL]) == [1, 0] and list(loaded[cal.COL_HORIZON]) == [20, 20]
    assert [p.name for p in path.parent.iterdir()] == [path.name]  # 一時ファイルは残らない


def test_読めない補正用データは失敗にする(tmp_path: Path) -> None:
    path = tmp_path / "壊れた.csv.gz"
    path.write_bytes(b"not gzip data")
    with pytest.raises(ValueError, match="補正用データを読めません"):
        cal.load_calibration_data(path)
