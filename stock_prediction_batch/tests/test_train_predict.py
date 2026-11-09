"""core/train_core.py、core/predict_core.py と、train.py、predict.py のテスト。"""

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
import pytest
from psycopg.rows import DictRow

import const
import predict as predict_cli
import train as train_cli
from common.env import resolve_path
from common.errors import BatchError
from common.logger import get_logger, log_file_path
from core import calibration, features, predict_core, train_core
from tests.sample_data import SAMPLE_CODES, insert_sample_data

BASE = date(2025, 10, 4)  # 学習の基準日(土曜)
PRED = date(2025, 10, 6)  # 予測日(月曜)
ALL_CODES = [c for c, _, _ in SAMPLE_CODES]


@pytest.fixture
def sample(conn: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    insert_sample_data(conn, date(2024, 7, 1), date(2025, 12, 26), split_date=date(2025, 6, 2))
    return conn


@pytest.fixture
def trained(sample: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    train_core.run_train(BASE)
    return sample


# =====================================================================
# 学習
# =====================================================================


def test_学習すると12個のモデルを保存して登録する(trained: psycopg.Connection[DictRow]) -> None:
    rows = trained.execute("SELECT * FROM model ORDER BY horizon, model_type").fetchall()
    assert [(r["horizon"], r["model_type"]) for r in rows] == [
        (h, t) for h in (5, 10, 20, 60) for t in ("PRICE", "REL_PROB", "UP_PROB")
    ]
    assert rows[1]["file_path"] == "20251004/市場比_5日後.txt"
    r = rows[2]
    assert (r["model_version"], r["base_date"]) == ("20251004", BASE)
    assert r["file_path"] == "20251004/上昇確率_5日後.txt"
    assert r["feature_names"] == features.feature_names()
    assert r["train_end_date"] <= BASE and r["train_rows"] > 0
    assert 0 <= float(r["valid_score"]) <= 1
    for row in rows:
        assert (resolve_path(const.MODEL_DIR) / row["file_path"]).is_file()
    history = trained.execute("SELECT status, record_count FROM run_history").fetchone()
    assert history is not None and (history["status"], history["record_count"]) == ("SUCCESS", 12)
    # 上昇確率用のモデルだけ、補正の対応表を同じフォルダに保存する
    folder = resolve_path(const.MODEL_DIR) / "20251004"
    assert sorted(p.name for p in folder.glob("*_補正.json")) == sorted(
        f"上昇確率_{h}日後_補正.json" for h in const.PREDICT_HORIZONS
    )
    text = log_file_path().read_text(encoding="utf-8")
    assert "補正の対応表を作りました" in text
    assert "20営業日後 上昇確率用 補正に使った行: " in text and "基準率 " in text and "補正後の最大 " in text
    # 補正用データは、確認用の期間の行を予測日数ごとに、モデルのフォルダの外に貯める
    data_dir = resolve_path(const.MODEL_DIR) / const.CALIBRATION_DATA_DIR
    assert sorted(p.name for p in data_dir.iterdir()) == sorted(
        f"上昇確率_{h}日後_確認用.csv.gz" for h in const.PREDICT_HORIZONS
    )
    stored = calibration.load_calibration_data(data_dir / "上昇確率_20日後_確認用.csv.gz")
    assert not stored.empty and set(stored[calibration.COL_HORIZON]) == {20}
    assert set(stored[calibration.COL_ACTUAL]) <= {0, 1}
    assert stored[calibration.COL_DATE].max().date() <= BASE


def test_日本語のファイル名で保存し読み込んでも同じ予測になる(trained: psycopg.Connection[DictRow]) -> None:
    """LightGBM のモデルを日本語のファイル名で保存・読み込みできることの確認。"""
    path = resolve_path(const.MODEL_DIR) / "20251004" / "上昇確率_5日後.txt"
    booster = train_core.load_booster(path)
    feats = features.build_features(trained, PRED, PRED, ALL_CODES)
    x = feats[features.feature_names()]
    first = booster.predict(x)
    again = path.parent / "読み込み直し.txt"
    train_core.save_booster(booster, again)
    np.testing.assert_allclose(first, train_core.load_booster(again).predict(x))


def test_予測では上昇確率を補正する(trained: psycopg.Connection[DictRow]) -> None:
    """補正の対応表を、全ての確率を 0.3 にするものに置き換えて予測すると、上昇確率は全て 0.3 になる。"""
    folder = resolve_path(const.MODEL_DIR) / "20251004"
    for h in const.PREDICT_HORIZONS:
        (folder / f"上昇確率_{h}日後_補正.json").write_text('{"x": [0.0, 1.0], "y": [0.3, 0.3]}', encoding="utf-8")
    predict_core.run_predict(PRED)
    rows = _predictions(trained, PRED)
    probs = {float(r["up_prob"]) for r in rows}
    assert probs == {0.3}
    text = log_file_path().read_text(encoding="utf-8")
    assert "(補正前 " in text
    # 全て 50% 未満なので、上昇確率の上位25%(同じ値は全て入る)に置き換えて選ぶ
    assert any(r["buy_rank"] is not None for r in rows)
    assert "買い候補: 条件を満たす銘柄が足りないため、上昇確率の上位25%で選びました" in text
    assert "買い候補(業種分散): 条件を満たす銘柄が足りないため、上昇確率の上位25%で選びました" in text


def test_補正の対応表がない古いモデルは補正せずに警告する(trained: psycopg.Connection[DictRow]) -> None:
    folder = resolve_path(const.MODEL_DIR) / "20251004"
    for p in folder.glob("*_補正.json"):
        p.unlink()
    models = predict_core.load_models(trained, PRED)
    assert all(models.calibrations[h] is None for h in const.PREDICT_HORIZONS)
    predict_core.run_predict(PRED)
    text = log_file_path().read_text(encoding="utf-8")
    assert "上昇確率の補正の対応表がないため、補正しません(5・10・20・60営業日後)" in text
    assert len({float(r["up_prob"]) for r in _predictions(trained, PRED)}) > 1


def _stored_rows(file_name: str = "上昇確率_20日後.txt") -> pd.DataFrame:
    return calibration.load_calibration_data(calibration.calibration_data_path(file_name))


def _used_rows_line(horizon: int = 20) -> str:
    """ログの、最後の「補正に使った行」の行。"""
    lines = log_file_path().read_text(encoding="utf-8").splitlines()
    return [line for line in lines if f"{horizon}営業日後 上昇確率用 補正に使った行" in line][-1]


def test_学習のたびに補正用データを貯めて対応表に使う(sample: psycopg.Connection[DictRow]) -> None:
    train_core.run_train(BASE)
    first = _stored_rows()
    later = date(2025, 11, 8)
    train_core.run_train(later)
    second = _stored_rows()
    # 前の学習の行が残り、新しい日付の行が足される。同じ(銘柄コード, 日付)は1行だけ
    assert second[calibration.COL_DATE].min() == first[calibration.COL_DATE].min()
    assert second[calibration.COL_DATE].max() > first[calibration.COL_DATE].max()
    assert not second.duplicated([calibration.COL_CODE, calibration.COL_DATE]).any()
    assert f"補正に使った行: {len(second):,}行" in _used_rows_line()
    # 同じ基準日で学習し直しても、貯めたデータは消えない(モデルのフォルダの外にあるため)
    train_core.run_train(later)
    assert len(_stored_rows()) == len(second)


def test_過去の日付で学習し直すと指定日より後の補正用データは使わない(sample: psycopg.Connection[DictRow]) -> None:
    later = date(2025, 11, 8)
    train_core.run_train(later)
    newest = _stored_rows()[calibration.COL_DATE].max()
    assert newest.date() > BASE
    train_core.run_train(BASE)
    stored = _stored_rows()
    # ファイルには残すが、対応表に使う期間の最後は基準日以前
    assert stored[calibration.COL_DATE].max() == newest
    used_end = _used_rows_line().split("期間 ")[1].split(" 〜 ")[1].split("、")[0]
    assert date.fromisoformat(used_end) <= BASE
    used_count = int(_used_rows_line().split("補正に使った行: ")[1].split("行")[0].replace(",", ""))
    assert used_count < len(stored)


def test_52週より古い補正用データは学習のときに消す(sample: psycopg.Connection[DictRow]) -> None:
    path = calibration.calibration_data_path("上昇確率_20日後.txt")
    old = pd.DataFrame(
        {
            calibration.COL_HORIZON: [20, 20],
            calibration.COL_CODE: ["99990", "99990"],
            calibration.COL_DATE: pd.to_datetime(["2024-01-05", "2025-06-02"]),
            calibration.COL_RAW_PROB: [0.1, 0.2],
            calibration.COL_ACTUAL: [0, 1],
        }
    )
    calibration.save_calibration_data(old, path)
    train_core.run_train(BASE)
    kept = _stored_rows()
    assert kept.loc[kept[calibration.COL_CODE] == "99990", calibration.COL_DATE].tolist() == [
        pd.Timestamp("2025-06-02")
    ]


def test_読めない補正用データがあれば学習を失敗にする(sample: psycopg.Connection[DictRow]) -> None:
    path = calibration.calibration_data_path("上昇確率_5日後.txt")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not gzip data")
    with pytest.raises(BatchError, match="補正用データを読めません"):
        train_core.run_train(BASE)
    assert path.read_bytes() == b"not gzip data"


def test_同じ基準日で学習し直すと作り直す(trained: psycopg.Connection[DictRow]) -> None:
    extra = resolve_path(const.MODEL_DIR) / "20251004" / "古いファイル.txt"
    extra.write_text("x", encoding="utf-8")
    train_core.run_train(BASE)
    assert not extra.exists()
    count = trained.execute("SELECT count(*) AS n FROM model").fetchone()
    assert count is not None and count["n"] == 12


def test_正解の作り方() -> None:
    df = pd.DataFrame(
        {
            "code": ["A"] * 4 + ["B"] * 4,
            "trade_date": pd.to_datetime(["2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09"] * 2),
            "_adj_close": [100.0, 110.0, 100.0, None, 50.0, 50.0, 40.0, 60.0],
            "_hist_days": [80, 81, 82, 82, 79, 80, 81, 82],
        }
    )
    out = train_core.add_targets(df, 1)
    # A: 01-06→01-07 上がった、01-07→01-08 下がった、01-08 は翌日の終値がない。B: 01-06 は上場直後で除く
    assert list(zip(out["code"], out["trade_date"].dt.day, out["_target_up"], strict=True)) == [
        ("A", 6, 1), ("A", 7, 0), ("B", 7, 0), ("B", 8, 1),
    ]  # fmt: skip
    assert out.loc[0, "_target_rate"] == pytest.approx(0.1)
    # 同じ値は「上がっていない」(0)
    assert out.loc[2, "_target_up"] == 0
    # 市場比: 日ごとの騰落率の中央値より高ければ 1。01-07 は A(−9.1%)と B(−20%)の中央値より A が高い。
    # 1銘柄だけの日は中央値と同じなので 0
    assert list(out["_target_rel"]) == [0, 1, 0, 0]


def test_学習用と確認用の間に隙間をあける() -> None:
    data = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2025-06-25", "2025-06-27", "2025-06-30", "2025-07-01"]),
            "_target_date": pd.to_datetime(["2025-06-30", "2025-07-02", "2025-07-03", "2025-07-04"]),
        }
    )
    train, valid = train_core.split_train_valid(data, date(2025, 7, 1))
    # 学習用は正解の日が 07-01 より前の行だけ。06-27、06-30 はどちらにも入らない
    assert list(train["trade_date"].dt.day) == [25]
    assert list(valid["trade_date"].dt.day) == [1]


def test_極端な値は境目に置き換える() -> None:
    rates = pd.Series(np.linspace(-1, 1, 201))
    clipped = train_core.clip_targets(rates, get_logger("学習", BASE), 5)
    assert clipped.min() == pytest.approx(rates.quantile(0.01))
    assert clipped.max() == pytest.approx(rates.quantile(0.99))
    assert len(clipped) == len(rates)


def test_確認用の成績() -> None:
    valid = pd.DataFrame({"_target_up": [1, 0, 1, 0], "_target_rate": [0.1, -0.1, 0.0, 0.0]})
    assert train_core.valid_score("UP_PROB", np.array([0.6, 0.4, 0.4, 0.5]), valid) == 0.5
    assert train_core.valid_score("PRICE", np.array([0.1, -0.1, 0.0, 0.1]), valid) == pytest.approx(0.025)
    rel = pd.DataFrame({"_target_rel": [1, 1, 0, 0]})
    assert train_core.valid_score("REL_PROB", np.array([0.5, 0.4, 0.4, 0.9]), rel) == 0.5
    # 上昇確率用と市場比用は AUC、予測株価用は l2 で学習を止める
    assert [train_core._params(t)["metric"] for t in const.MODEL_TYPES] == ["auc", "l2", "auc"]


def test_学習に使えるデータがない場合は失敗(conn: psycopg.Connection[DictRow]) -> None:
    with pytest.raises(BatchError, match="学習に使えるデータがありません") as e:
        train_core.run_train(BASE)
    assert e.value.code == "全銘柄の一括処理"
    row = conn.execute("SELECT status, error_code FROM run_history").fetchone()
    assert row is not None and (row["status"], row["error_code"]) == ("FAILED", "全銘柄の一括処理")


def test_確認用の期間のデータがない場合は失敗(
    sample: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(const, "VALID_PERIOD_MONTHS", 0)
    with pytest.raises(BatchError, match="学習用または確認用のデータがありません"):
        train_core.run_train(BASE)


def test_ファイルを保存できない場合はフォルダを消して失敗(
    sample: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(booster: object, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        raise OSError("書き込めません")

    monkeypatch.setattr(train_core, "save_booster", broken)
    with pytest.raises(BatchError, match="モデルのファイルを保存できませんでした"):
        train_core.run_train(BASE)
    assert not (resolve_path(const.MODEL_DIR) / "20251004").exists()
    assert not (resolve_path(const.MODEL_DIR) / const.CALIBRATION_DATA_DIR).exists()  # 補正用データも変えない
    count = sample.execute("SELECT count(*) AS n FROM model").fetchone()
    assert count is not None and count["n"] == 0


def test_DBのエラーの場合はフォルダを消して失敗(
    sample: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(json, "dumps", lambda *a, **k: "壊れたJSON")
    with pytest.raises(BatchError):
        train_core.run_train(BASE)
    assert not (resolve_path(const.MODEL_DIR) / "20251004").exists()


def test_train_pyの終了コード(sample: psycopg.Connection[DictRow]) -> None:
    assert train_cli.main(["--date", "2025-10-04"]) == 0
    assert train_cli.main(["--date", "2000-01-01"]) == 1
    assert train_cli.main(["--date", "x"]) == 1
    assert "日付の形式が正しくありません(--date)" in log_file_path().read_text(encoding="utf-8")


# =====================================================================
# 予測
# =====================================================================


def _predictions(conn: psycopg.Connection[DictRow], d: date) -> list[DictRow]:
    return conn.execute("SELECT * FROM prediction WHERE pred_date = %s ORDER BY horizon, code", (d,)).fetchall()


def test_予測を登録し買い候補の順位を付ける(trained: psycopg.Connection[DictRow]) -> None:
    result = predict_core.run_predict(PRED)
    rows = _predictions(trained, PRED)
    assert result.model_version == "20251004"
    assert len(rows) == result.code_count * 4
    five = [r for r in rows if r["horizon"] == 5]
    twenty = [r for r in rows if r["horizon"] == 20]
    assert {r["target_date"] for r in five} == {date(2025, 10, 14)}  # 10-13 はスポーツの日
    assert {r["target_date"] for r in twenty} == {date(2025, 11, 5)}  # 11-03 は文化の日
    assert all(r["verify_status"] == "PENDING" and r["actual_price"] is None for r in rows)
    assert all(0 <= r["rel_prob"] <= 1 for r in rows)
    # 方式R: 主となる日数(20日後)の上昇確率が50%以上で見込みがプラスの銘柄を、市場比の確率が高い順に並べる
    ranked = sorted((r for r in twenty if r["buy_rank"] is not None), key=lambda r: r["buy_rank"])
    assert len(ranked) == result.candidate_count <= 20
    assert all(r["up_prob"] >= 0.5 and r["pred_price"] > r["base_price"] for r in ranked)
    rels = [(-float(r["rel_prob"]), r["code"]) for r in ranked]
    assert rels == sorted(rels)
    pool = [r for r in twenty if r["up_prob"] >= 0.5 and r["pred_price"] > r["base_price"]]
    assert result.candidate_count == min(len(pool), 20)
    # 順位は全ての予測日数の行に同じ値
    by_code = {r["code"]: r["buy_rank"] for r in twenty}
    assert all(r["buy_rank"] == by_code[r["code"]] for r in rows)
    # 業種は予測した時点の銘柄マスタの値
    sectors = {str(r["code"]): r["sector33_code"] for r in trained.execute("SELECT * FROM stock_master").fetchall()}
    assert all(r["sector33_code"] == sectors[r["code"]] for r in rows)
    # 買い候補(業種分散): 同じ並べ方で、同じ33業種は3銘柄まで、上位30銘柄。全ての予測日数の行に同じ値
    by_sector = sorted((r for r in twenty if r["buy_rank_sector"] is not None), key=lambda r: r["buy_rank_sector"])
    assert [r["buy_rank_sector"] for r in by_sector] == list(range(1, result.candidate_sector_count + 1))
    assert all(r["up_prob"] >= 0.5 and r["pred_price"] > r["base_price"] for r in by_sector)
    counts: dict[str, int] = {}
    for r in by_sector:
        counts[r["sector33_code"]] = counts.get(r["sector33_code"], 0) + 1
    assert max(counts.values()) <= const.SECTOR_CAP_PER_SECTOR
    capped = sum(min(sum(1 for p in pool if p["sector33_code"] == s), 3) for s in {p["sector33_code"] for p in pool})
    assert result.candidate_sector_count == min(capped, 30)
    by_code_sector = {r["code"]: r["buy_rank_sector"] for r in twenty}
    assert all(r["buy_rank_sector"] == by_code_sector[r["code"]] for r in rows)
    # 予測株価 = 予測時の株価 ×(1 + 騰落率)、予測時の株価は指定日の終値
    close = trained.execute(
        "SELECT close FROM daily_price WHERE code = %s AND trade_date = %s", (five[0]["code"], PRED)
    ).fetchone()
    assert close is not None and five[0]["base_price"] == close["close"]


def test_再実行するとその日の予測と集計を削除して作り直す(trained: psycopg.Connection[DictRow]) -> None:
    predict_core.run_predict(PRED)
    trained.execute("UPDATE prediction SET verify_status = 'VERIFIED' WHERE pred_date = %s", (PRED,))
    trained.execute(
        "INSERT INTO verify_daily_summary (pred_date, horizon, segment, target_date, model_version, verified_count,"
        " hit_count, up_count, beat_count, sum_change_rate, sum_excess_rate, sum_price_error_rate, unverifiable_count,"
        " sector_verified_count, sector_beat_count, sum_sector_excess_rate)"
        " VALUES (%s, 5, 'ALL', %s, 'x', 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0)",
        (PRED, PRED),
    )
    predict_core.run_predict(PRED)
    assert {r["verify_status"] for r in _predictions(trained, PRED)} == {"PENDING"}
    count = trained.execute("SELECT count(*) AS n FROM verify_daily_summary").fetchone()
    assert count is not None and count["n"] == 0


def test_学習の基準日が指定日より後のモデルは使わない(trained: psycopg.Connection[DictRow]) -> None:
    with pytest.raises(BatchError, match=r"使えるモデルがありません\(指定日: 2025-10-03\)"):
        predict_core.run_predict(date(2025, 10, 3))


def test_モデルのファイルがない場合は失敗(trained: psycopg.Connection[DictRow]) -> None:
    (resolve_path(const.MODEL_DIR) / "20251004" / "予測株価_20日後.txt").unlink()
    with pytest.raises(BatchError, match="使えるモデルがありません"):
        predict_core.run_predict(PRED)


def test_モデルの行が足りない場合は失敗(trained: psycopg.Connection[DictRow]) -> None:
    trained.execute("DELETE FROM model WHERE horizon = 10 AND model_type = 'PRICE'")
    with pytest.raises(BatchError, match="使えるモデルがありません"):
        predict_core.run_predict(PRED)


def test_市場比用のモデルがない古いモデルは使えない(trained: psycopg.Connection[DictRow]) -> None:
    trained.execute("DELETE FROM model WHERE model_type = 'REL_PROB'")
    with pytest.raises(BatchError, match="使えるモデルがありません"):
        predict_core.run_predict(PRED)


def test_予測する銘柄がない場合は失敗(trained: psycopg.Connection[DictRow]) -> None:
    with pytest.raises(BatchError, match=r"予測する銘柄がありません\(指定日: 2025-10-11\)"):
        predict_core.run_predict(date(2025, 10, 11))  # 土曜(株価がない)


def test_順位付けの方式R(monkeypatch: pytest.MonkeyPatch) -> None:
    main = pd.DataFrame(
        {
            "code": ["A", "B", "C", "D", "E"],
            "up_prob": [0.9, 0.5, 0.49, 0.7, 0.6],
            "rel_prob": [0.55, 0.7, 0.9, 0.8, 0.7],
            "pred_price": [101.0, 101.0, 120.0, 100.0, 105.0],
            "base_price": [100.0, 100.0, 100.0, 100.0, 100.0],
        }
    )
    assert const.RANKING_METHOD == "R"
    # C は上昇確率が50%未満、D は見込みがプラスでない(予測株価が同じ)ので対象外。B と E は同じ市場比なので銘柄コード順
    assert predict_core.rank_candidates(main).to_dict() == {"B": 1, "E": 2, "A": 3}
    # 条件に合う銘柄がなければ0銘柄
    assert predict_core.rank_candidates(main.loc[main["code"].isin(["C", "D"])]).empty


def test_順位付けの方式C(monkeypatch: pytest.MonkeyPatch) -> None:
    main = pd.DataFrame(
        {
            "code": ["A", "B", "C", "D"],
            "up_prob": [0.9, 0.65, 0.61, 0.55],
            "rel_prob": [0.5, 0.5, 0.5, 0.5],
            "pred_price": [101.0, 110.0, 110.0, 200.0],
            "base_price": [100.0, 100.0, 100.0, 100.0],
        }
    )
    monkeypatch.setattr(const, "RANKING_METHOD", "C")
    ranks = predict_core.rank_candidates(main)
    # D は上昇確率が 60% 未満なので対象外。B と C は同じ予測リターンなので、銘柄コードの小さい順
    assert ranks.to_dict() == {"B": 1, "C": 2, "A": 3}
    monkeypatch.setattr(const, "RANKING_METHOD", "A")
    assert predict_core.rank_candidates(main).to_dict() == {"A": 1, "B": 2, "C": 3, "D": 4}
    monkeypatch.setattr(const, "BUY_CANDIDATE_COUNT", 2)
    assert list(predict_core.rank_candidates(main).index) == ["A", "B"]
    monkeypatch.setattr(const, "RANKING_METHOD", "X")
    with pytest.raises(BatchError, match="RANKING_METHOD"):
        predict_core.rank_candidates(main)
    with pytest.raises(BatchError, match="RANKING_METHOD"):
        predict_core.rank_candidates_sector(main.assign(sector33_code=None))


def _sector_frame() -> pd.DataFrame:
    # 市場比の確率が高い順に A〜H。業種 S1 が5銘柄、S2 が1銘柄、業種なしが2銘柄。I は上昇確率が50%未満で対象外
    return pd.DataFrame(
        {
            "code": ["A", "B", "C", "D", "E", "F", "G", "H", "I"],
            "up_prob": [0.6] * 8 + [0.4],
            "rel_prob": [0.95, 0.9, 0.85, 0.8, 0.75, 0.7, 0.7, 0.6, 0.99],
            "pred_price": [101.0] * 9,
            "base_price": [100.0] * 9,
            "sector33_code": ["S1", "S1", None, "S1", "S1", "S2", None, "S1", "S1"],
        }
    )


def test_買い候補の業種分散は同じ業種を上限までにする(monkeypatch: pytest.MonkeyPatch) -> None:
    main = _sector_frame()
    assert const.SECTOR_CAP_PER_SECTOR == 3 and const.BUY_CANDIDATE_SECTOR_COUNT == 30
    # S1 は上から3銘柄(A・B・D)まで。業種なしの C・G は銘柄ごとに別の業種なので上限がかからない。
    # F と G は同じ市場比なので銘柄コード順。30件に届かないので、ある分だけ
    assert predict_core.rank_candidates_sector(main).to_dict() == {"A": 1, "B": 2, "C": 3, "D": 4, "F": 5, "G": 6}
    # 今の買い候補(上限なし)は変わらない
    assert list(predict_core.rank_candidates(main).index) == ["A", "B", "C", "D", "E", "F", "G", "H"]
    monkeypatch.setattr(const, "SECTOR_CAP_PER_SECTOR", 1)
    assert predict_core.rank_candidates_sector(main).to_dict() == {"A": 1, "C": 2, "F": 3, "G": 4}
    monkeypatch.setattr(const, "BUY_CANDIDATE_SECTOR_COUNT", 2)
    assert predict_core.rank_candidates_sector(main).to_dict() == {"A": 1, "C": 2}
    # 見込みがプラスの銘柄がなければ0銘柄
    assert predict_core.rank_candidates_sector(main.loc[main["code"] == "I"].assign(pred_price=100.0)).empty


def _fallback_frame() -> pd.DataFrame:
    # 上昇確率が高い順に A〜H(全8銘柄なので上位25%は2銘柄)。H は上位でも市場比でもないが見込みがプラス
    return pd.DataFrame(
        {
            "code": ["A", "B", "C", "D", "E", "F", "G", "H"],
            "up_prob": [0.48, 0.47, 0.46, 0.45, 0.44, 0.43, 0.42, 0.41],
            "rel_prob": [0.1, 0.2, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4],
            "pred_price": [101.0] * 8,
            "base_price": [100.0] * 8,
            "sector33_code": ["S1"] * 8,
        }
    )


def test_足りない日は上昇確率の上位25パーセントに置き換える(monkeypatch: pytest.MonkeyPatch) -> None:
    main = _fallback_frame()
    # 50%以上がないので上位2銘柄(A・B)に置き換え、市場比の確率の順(B が上)に並べる。足りない分はそのまま
    assert predict_core.rank_candidates(main).to_dict() == {"B": 1, "A": 2}
    assert predict_core.rank_candidates_sector(main).to_dict() == {"B": 1, "A": 2}
    # 割合は設定値に従う。8銘柄の 30% は切り上げて3銘柄
    monkeypatch.setattr(const, "BUY_CANDIDATE_FALLBACK_TOP_RATE", 0.3)
    assert predict_core.rank_candidates(main).to_dict() == {"C": 1, "B": 2, "A": 3}


def test_置き換えても見込みプラスの条件は変えない() -> None:
    main = _fallback_frame()
    main.loc[main["code"] == "A", "pred_price"] = 100.0  # 予測株価が同じ = 見込みがプラスでない
    assert predict_core.rank_candidates(main).to_dict() == {"B": 1}
    assert predict_core.rank_candidates_sector(main).to_dict() == {"B": 1}


def test_同じ上昇確率の銘柄は全て上位に入る() -> None:
    main = _fallback_frame()
    main.loc[main["code"].isin(["C", "D"]), "up_prob"] = 0.47  # 上位2番目と同じ値
    assert predict_core.rank_candidates(main).to_dict() == {"C": 1, "D": 2, "B": 3, "A": 4}


def test_件数が足りている日は置き換えない(monkeypatch: pytest.MonkeyPatch) -> None:
    main = _fallback_frame()
    main.loc[main["code"].isin(["G", "H"]), "up_prob"] = 0.6
    monkeypatch.setattr(const, "BUY_CANDIDATE_COUNT", 2)
    monkeypatch.setattr(const, "BUY_CANDIDATE_SECTOR_COUNT", 2)
    assert predict_core.rank_candidates(main).to_dict() == {"G": 1, "H": 2}
    assert predict_core.rank_candidates_sector(main).to_dict() == {"G": 1, "H": 2}
    # 足りなくなれば置き換える。上位2銘柄(G・H)は 50% 以上なので、50% のままで選び直す(対象を狭くしない)
    monkeypatch.setattr(const, "BUY_CANDIDATE_COUNT", 3)
    assert predict_core.rank_candidates(main).to_dict() == {"G": 1, "H": 2}


def test_業種分散は上限をかけて足りなければ置き換える(monkeypatch: pytest.MonkeyPatch) -> None:
    # 上位20の条件は足りているが、業種分散だけ上限のために足りない日
    main = _fallback_frame()
    main["up_prob"] = [0.6, 0.6, 0.6, 0.6, 0.4, 0.3, 0.2, 0.1]  # S1 が50%以上で4銘柄
    main["rel_prob"] = [0.9, 0.8, 0.7, 0.6, 0.95, 0.5, 0.4, 0.3]
    main["sector33_code"] = ["S1", "S1", "S1", "S1", "S2", "S3", "S4", "S5"]
    monkeypatch.setattr(const, "BUY_CANDIDATE_COUNT", 4)
    monkeypatch.setattr(const, "BUY_CANDIDATE_SECTOR_COUNT", 4)
    assert predict_core.rank_candidates(main).to_dict() == {"A": 1, "B": 2, "C": 3, "D": 4}
    # 全8銘柄の上位25%は2銘柄で 60% なので、50% のまま: S1 の上限3銘柄で足りないが、選べる銘柄は増えない
    assert predict_core.rank_candidates_sector(main).to_dict() == {"A": 1, "B": 2, "C": 3}
    # 上位の割合を 75% にすると、6銘柄目の 30% まで広がる
    monkeypatch.setattr(const, "BUY_CANDIDATE_FALLBACK_TOP_RATE", 0.75)
    assert predict_core.rank_candidates(main).to_dict() == {"A": 1, "B": 2, "C": 3, "D": 4}
    assert predict_core.rank_candidates_sector(main).to_dict() == {"E": 1, "A": 2, "B": 3, "C": 4}


def test_業種は銘柄マスタにない銘柄はNone(conn: psycopg.Connection[DictRow]) -> None:
    conn.execute(
        "INSERT INTO stock_master (code, name, sector33_code, applied_date) VALUES"
        " ('11110', 'あ', '3050', '2026-01-05'), ('11120', 'い', NULL, '2026-01-05')"
    )
    assert predict_core.sectors_of(conn, ["11110", "11120", "99990"]) == {"11110": "3050", "11120": None, "99990": None}


def test_特徴量や予測のエラーは全銘柄の一括処理として失敗(
    trained: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object) -> pd.DataFrame:
        raise ValueError("こわれた")

    monkeypatch.setattr(features, "build_features", broken)
    with pytest.raises(BatchError) as e:
        predict_core.run_predict(PRED)
    assert e.value.code == "全銘柄の一括処理"


def test_予測のエラー(trained: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> None:
    real = predict_core.load_models

    def broken_models(conn: psycopg.Connection[DictRow], d: date) -> predict_core.LoadedModels:
        m = real(conn, d)
        return predict_core.LoadedModels(m.version, m.boosters, ["存在しない列"], m.calibrations)

    monkeypatch.setattr(predict_core, "load_models", broken_models)
    with pytest.raises(BatchError):
        predict_core.run_predict(PRED)


def test_指定日に取引がない銘柄だけなら失敗(
    trained: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = features.build_features

    def no_close(*args: object) -> pd.DataFrame:
        df: pd.DataFrame = real(*args)  # type: ignore[arg-type]
        df["_close"] = np.nan
        return df

    monkeypatch.setattr(features, "build_features", no_close)
    with pytest.raises(BatchError, match="予測する銘柄がありません"):
        predict_core.run_predict(PRED)


def test_predict_pyの終了コード(trained: psycopg.Connection[DictRow]) -> None:
    assert predict_cli.main(["--date", "2025-10-06"]) == 0
    assert predict_cli.main(["--date", "2025-10-03"]) == 1
    assert predict_cli.main(["--date", "2025/10/06"]) == 1
    row = trained.execute("SELECT feature_names FROM model LIMIT 1").fetchone()
    assert row is not None and json.dumps(row["feature_names"])
