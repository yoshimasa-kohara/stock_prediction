"""GET /api/screen(銘柄の検索)のテスト。"""

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow
from web_sample import LAST_DAY, PRED_CODES

import const
from api import web_const
from api.routers import screen as screen_module


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    screen_module.note_cache.clear()


def codes(body: dict[str, object]) -> list[str]:
    rows = body["rows"]
    assert isinstance(rows, list)
    return [str(r["code"]) for r in rows]


def test_screen_default(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """条件なしは、予測した全銘柄を、主となる日数の市場比の確率が高い順に返す。選択肢も返す。"""
    body = client.get("/api/screen").json()
    assert body["pred_date"] == LAST_DAY.isoformat()
    assert body["code_count"] == len(PRED_CODES)
    assert body["total"] == len(PRED_CODES)
    assert body["sort"] == f"rel_{const.MAIN_HORIZON}"
    assert codes(body) == PRED_CODES  # 市場比は銘柄の順に下がる
    first = body["rows"][0]
    assert first["rank"] == 1
    assert {"text": "ストップ高", "color": "red"} in first["notes"]
    assert body["rows"][2]["rank"] is None
    assert body["market_options"] == ["プライム", "スタンダード", "グロース"]
    assert {"value": "3050", "label": "銀行業"} in body["sector_options"]
    assert {"value": "resist_break", "label": "上値抵抗線を上抜け"} in body["note_options"]
    assert body["exclude_options"] == [
        {"value": "low_liquidity", "label": "流動性注意を除く"},
        {"value": "deficit", "label": "赤字予想(業績注意)を除く"},
    ]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("q=1114", ["11140", "11145"]),
        ("q=銀行", ["11110"]),
        ("markets=スタンダード,グロース", ["11120", "11130"]),
        ("sectors=3050", ["11110", "11140", "11145"]),
        ("min_rel=70", ["11110", "11120"]),
        ("min_up=61", ["11110"]),
        ("min_ret=2.5", ["11110", "11120"]),
        ("notes=pbr", ["11110"]),
        ("notes=low_liquidity", ["11120"]),
        ("exclude=low_liquidity,deficit", ["11110", "11140", "11145"]),
        ("sort=code&order=asc", ["11110", "11120", "11130", "11140", "11145"]),
        ("sort=rank&order=asc", ["11110", "11120", "11140", "11130", "11145"]),
        ("sort=rank&order=desc", ["11140", "11120", "11110", "11130", "11145"]),
        ("sort=base_price&order=asc", None),
        (f"sort=up_{const.PREDICT_HORIZONS[0]}&order=asc", ["11145", "11140", "11130", "11120", "11110"]),
    ],
)
def test_screen_conditions(
    client: TestClient, sample: psycopg.Connection[DictRow], query: str, expected: list[str] | None
) -> None:
    body = client.get(f"/api/screen?{query}").json()
    if expected is None:
        prices = [r["base_price"] for r in body["rows"]]
        assert prices == sorted(prices)
    else:
        assert codes(body) == expected


def test_screen_sort_by_name(client: TestClient, sample: psycopg.Connection[DictRow]) -> None:
    """銘柄名の並べ替え(文字の並び)。"""
    names = [r["name"] for r in client.get("/api/screen?sort=name&order=desc").json()["rows"]]
    assert names == sorted(names, reverse=True)


def test_screen_paging(
    client: TestClient, sample: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(web_const, "SCREEN_PAGE_SIZE", 2)
    first = client.get("/api/screen").json()
    assert codes(first) == ["11110", "11120"]
    assert first["has_more"] is True
    last = client.get("/api/screen?offset=4").json()
    assert codes(last) == ["11145"]
    assert last["has_more"] is False


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("order=up", "order は asc か desc を指定してください"),
        ("offset=-1", "offset は 0 以上を指定してください"),
        ("notes=nope", "備考の指定が正しくありません(nope)"),
        ("exclude=pbr", "除く備考の指定が正しくありません(pbr)"),
        ("sort=rel_7", "並べ替えの指定が正しくありません(rel_7)"),
        ("sort=xyz", "並べ替えの指定が正しくありません(xyz)"),
        ("min_up=2000", "上昇確率は -100〜1000 の数を入れてください"),
    ],
)
def test_screen_bad_input(client: TestClient, sample: psycopg.Connection[DictRow], query: str, message: str) -> None:
    res = client.get(f"/api/screen?{query}")
    assert res.status_code == 400
    assert res.json() == {"detail": message}


def test_screen_note_cache(
    client: TestClient, sample: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    """備考は予測日ごとにメモリに残し、2回目からは計算しない。"""
    calls: list[list[str]] = []
    original = screen_module.note_cache.get.__func__  # type: ignore[attr-defined]

    from api import notes

    real = notes.stock_notes

    def counting(conn: psycopg.Connection[DictRow], target: object, codes_: list[str]) -> dict[str, list[str]]:
        calls.append(list(codes_))
        return real(conn, target, codes_)  # type: ignore[arg-type]

    monkeypatch.setattr(notes, "stock_notes", counting)
    client.get("/api/screen?notes=pbr")
    client.get("/api/screen?notes=roe")
    assert len(calls) == 1
    assert sorted(calls[0]) == sorted(PRED_CODES)
    assert original is not None


def test_note_cache_keeps_limited_days(sample: psycopg.Connection[DictRow]) -> None:
    """残す予測日の数を超えたら、古い予測日から消す。"""
    from datetime import timedelta

    from api.notes import NoteCache

    cache = NoteCache(1)
    cache.get(sample, LAST_DAY - timedelta(days=1), ["11110"])
    cache.get(sample, LAST_DAY, ["11110"])
    assert list(cache._data) == [LAST_DAY]


def test_note_filter_matches_cross_with_days() -> None:
    """クロスの備考に「(本日)」「(N営業日前)」が付いても、絞り込みで見つかる。"""
    from api.notes import note_filters, to_note

    filters = {f.key: f for f in note_filters()}
    assert filters["golden_short"].matches(["短期ゴールデンクロス発生(本日)"])
    assert filters["dead_mid"].matches(["中長期デッドクロス発生(3営業日前)"])
    assert not filters["golden_short"].matches(["中長期ゴールデンクロス発生(本日)"])
    assert not filters["dead_short"].matches(["短期ゴールデンクロス発生(本日)"])
    assert filters["support_break"].matches(["下値支持線(接触3回)を下抜け(下降が加速・本日)"])
    assert filters["resist_break"].matches(["上値抵抗線(接触3回)を上抜け(2営業日前)"])
    # 色は今までどおり(デッドクロス・下抜けは青、ゴールデンクロス・上抜けは赤)
    assert to_note("短期デッドクロス発生(本日)").color == "blue"
    assert to_note("短期ゴールデンクロス発生(3営業日前)").color == "red"
    assert to_note("下値支持線(接触4回)を下抜け(上昇から転換・本日)").color == "blue"
    assert to_note("上値抵抗線(接触3回)を上抜け(下降から転換・2営業日前)").color == "red"
