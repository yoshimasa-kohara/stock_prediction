"""画面イメージ(PNG)を、本物の画面で撮る。

使い方(stock_prediction_web のフォルダで。先に frontend で npm run build をしておく):
    .venv\\Scripts\\python -m screenshot.capture              架空の値を入れ直してから、全ての画面を撮る
    .venv\\Scripts\\python -m screenshot.capture --skip-db    架空の値を入れ直さずに撮る(make_sample_db の実行の後)
    .venv\\Scripts\\python -m screenshot.capture 04 05        画面の番号を指定して、その画面だけ撮る

手順:
  (1) make_sample_db で、テスト用の DB(stock_prediction_test)に架空の値を入れる
  (2) テスト用の DB につないだ API を、このプロセスの中でポート CAPTURE_PORT(8081)で起動する
      (本番の Windows のサービス(8080)は止めない・触らない。ログは一時フォルダに出す)
  (3) Playwright(プレイライト)の Chromium で各画面を開き、操作して、ページの中だけを撮る
  (4) API を止める
出力先: OUTPUT_DIR の 画面イメージ_NN_画面名_PC.png(幅1366・倍率1)・_スマホ.png(幅390・倍率2)

「今日」は make_sample_db.BASE_DAY にそろえる(API の today と、ブラウザーの時計)。毎回同じ画面になるようにするため。
"""

import argparse
import logging
import tempfile
import threading
import time as time_mod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path

from playwright.sync_api import Browser, Page, sync_playwright

from screenshot import make_sample_db
from screenshot.db_switch import check_test_db, use_test_db
from screenshot.make_sample_db import BASE_DAY, JST

# API を起動するアドレスとポート(本番のサービスの 8080 とは別にする。外からはつながらないように 127.0.0.1)
CAPTURE_HOST: str = "127.0.0.1"
CAPTURE_PORT: int = 8081
# 画面イメージの出力先
OUTPUT_DIR: Path = Path("【画面イメージの出力先のパスを記載する】")
# 撮る大きさ: (ファイル名の末尾, 幅, 倍率)。高さはページ全体
SIZES: list[tuple[str, int, int]] = [("PC", 1366, 1), ("スマホ", 390, 2)]
# 最初に開くときの画面の高さ(ページ全体を撮るので、撮る画像の高さには関係しない)
VIEWPORT_HEIGHT: int = 800
# 08 銘柄検索で、銘柄コード・銘柄名の欄に入れる文字(架空の銘柄名に共通する文字)
SEARCH_TEXT: str = "サンプル"
# ブラウザーの時計(基準の日の、日次バッチが終わった後の時刻)
BROWSER_NOW: datetime = datetime.combine(BASE_DAY, time(21, 30), JST)
# 02 買い候補の「バッチ実行中の表示」に出す、実行中の処理の開始の時刻(単独で起動した検証)
RUNNING_STARTED_AT: datetime = datetime.combine(BASE_DAY, time(21, 5), JST)
# 撮っている間だけ使う、実行中とみなす時間(時間)。開始の時刻を固定しても実行中と表示されるように大きくする
CAPTURE_RUNNING_STALE_HOURS: int = 24 * 365 * 100
# 画面の読み込みを待つ最大の時間(ミリ秒)
LOAD_TIMEOUT_MS: int = 60_000
# 読み込みが終わった後、チャートなどの描画を待つ時間(ミリ秒)
SETTLE_MS: int = 800

# 銘柄詳細で撮る銘柄(備考が3つ出て、業績予想の上方修正の履歴も出る銘柄)
DETAIL_STOCK_NAME: str = "サンプル電力"
# 銘柄詳細のチャートで、マウスを置く位置(チャートの幅に対する割合)
CHART_HOVER_RATE: float = 0.78


@dataclass(frozen=True)
class Shot:
    """1つの画面イメージ。"""

    no: str  # 画面の番号(01〜11)
    name: str  # ファイル名の画面名の部分(例: 01_トップ)
    path: Callable[[], str]  # 開く URL のパス
    action: Callable[[Page], None] | None = None  # 開いた後の操作
    clip_selector: str | None = None  # 指定すると、ページの先頭からこの要素の下の端までを撮る


@dataclass
class CaptureContext:
    """撮るときに使う値(DB から読む)。"""

    pred_days: list[date]
    detail_code: str
    lot_code: str
    lot_close: float  # lot_code の基準の日の終値


def wait_loaded(page: Page) -> None:
    """API の読み込みが終わり、「読み込み中…」が消えるまで待つ。"""
    page.wait_for_load_state("networkidle", timeout=LOAD_TIMEOUT_MS)
    page.wait_for_function("() => document.querySelectorAll('.loading').length === 0", timeout=LOAD_TIMEOUT_MS)
    page.wait_for_timeout(SETTLE_MS)


def open_calendar(page: Page) -> None:
    """予測日(終了)のカレンダーを開く(02 買い候補)。"""
    page.get_by_role("button", name="予測日(終了)をカレンダーで選ぶ").first.click()
    page.wait_for_timeout(300)


def hover_chart(page: Page) -> None:
    """銘柄詳細のチャートに、マウスを置く(マウスの位置の日の情報を出す)。"""
    chart = page.get_by_test_id("price-chart").first
    chart.scroll_into_view_if_needed()
    box = chart.bounding_box()
    assert box is not None
    page.mouse.move(box["x"] + box["width"] * CHART_HOVER_RATE, box["y"] + box["height"] * 0.35)
    page.wait_for_timeout(300)
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(200)


def show_bollinger(page: Page) -> None:
    """ボリンジャーバンドのチェックボックスを ON にする。"""
    page.get_by_label("ボリンジャーバンド").first.check()
    page.wait_for_timeout(SETTLE_MS)


def open_sell_dialog(ctx: CaptureContext) -> Callable[[Page], None]:
    """保有中の300株の行の「売却」を押し、100株を売るダイアログにする(05 保有銘柄)。"""

    def run(page: Page) -> None:
        row = page.locator("tr", has=page.locator(f"a:text-is('{display_code(ctx.lot_code)}')")).first
        if row.count() == 0:
            row = page.locator("tr", has_text=display_code(ctx.lot_code)).first
        row.get_by_role("button", name="売却").click()
        page.locator("#sell-qty").fill("100")
        # 売値は、基準の日の終値
        page.locator("#sell-price").fill(f"{ctx.lot_close:.0f}")
        # ボタンを押したときに横にスクロールした表を、左の端に戻す
        page.evaluate("() => document.querySelectorAll('.wrap').forEach(w => { w.scrollLeft = 0; })")
        page.wait_for_timeout(300)

    return run


def search_by_name(page: Page) -> None:
    """銘柄検索の「銘柄コード・銘柄名」に SEARCH_TEXT を入れて検索する(08 銘柄検索)。

    入力欄の見本の文字(placeholder)に実在の会社名があるため、空のままでは撮らない(ポートフォリオに使うため)。
    架空の銘柄名は全て SEARCH_TEXT を含むので、検索の結果は全ての銘柄のまま。
    """
    page.get_by_label("銘柄コード・銘柄名", exact=True).fill(SEARCH_TEXT)
    page.get_by_role("button", name="検索", exact=True).click()
    wait_loaded(page)


def show_sold(page: Page) -> None:
    """保有銘柄の「売却済み」のタブを開く。"""
    page.get_by_role("tab", name="売却済み").first.click()
    wait_loaded(page)


def display_code(code: str) -> str:
    """画面に出す銘柄コード(5桁目が 0 なら4桁)。"""
    return code[:4] if len(code) == 5 and code.endswith("0") else code


def load_context() -> CaptureContext:
    """撮るときに使う値を、テスト用の DB から読む。"""
    from common import db

    with db.connect() as conn:
        check_test_db(conn)
        days = [r["d"] for r in conn.execute("SELECT DISTINCT pred_date AS d FROM prediction ORDER BY 1")]
        detail = conn.execute("SELECT code FROM stock_master WHERE name = %s", (DETAIL_STOCK_NAME,)).fetchone()
        lot = conn.execute("SELECT code FROM holding WHERE quantity = 300 AND sell_date IS NULL").fetchone()
        assert detail is not None and lot is not None
        close = conn.execute(
            "SELECT close FROM daily_price WHERE code = %s AND trade_date = %s", (lot["code"], BASE_DAY)
        ).fetchone()
    assert close is not None
    return CaptureContext(
        pred_days=days, detail_code=str(detail["code"]), lot_code=str(lot["code"]), lot_close=float(close["close"])
    )


def build_shots(ctx: CaptureContext) -> list[Shot]:
    """撮る画面の一覧(画面イメージの説明のとおりの状態)。"""
    days = ctx.pred_days
    # 03: 上の日は60日後が未到来、下の日は全ての日数の答え合わせ済み(新しい日付が上)
    results_from, results_to = days[-62], days[-60]
    code = ctx.detail_code
    return [
        Shot("01", "01_トップ", lambda: "/"),
        Shot("02", "02_買い候補", lambda: "/candidates", open_calendar),
        Shot(
            "03",
            "03_買い候補と実際の株価",
            lambda: f"/candidates/results?from={results_from.isoformat()}&to={results_to.isoformat()}",
        ),
        Shot("04", "04_銘柄詳細", lambda: f"/stocks/{code}", hover_chart, ".panel:has(> h2:text-matches('^業績'))"),
        Shot(
            "04",
            "04_銘柄詳細_ボリンジャーバンド表示",
            lambda: f"/stocks/{code}",
            show_bollinger,
            ".panel:has(> h2:text-matches('^業績'))",
        ),
        Shot("05", "05_保有銘柄", lambda: "/holdings", open_sell_dialog(ctx)),
        Shot("05", "05_保有銘柄_売却済み", lambda: "/holdings", show_sold),
        Shot("06", "06_的中率", lambda: "/accuracy"),
        Shot("07", "07_実行履歴", lambda: "/runs"),
        Shot("08", "08_銘柄検索", lambda: "/search", search_by_name),
        Shot("09", "09_精度の比較", lambda: "/accuracy/compare"),
        Shot("10", "10_検討中銘柄", lambda: "/watch-stocks"),
        Shot("11", "11_ランキング", lambda: "/rankings"),
    ]


def set_running(on: bool) -> None:
    """実行中(RUNNING)の実行履歴を入れる・消す(02 買い候補の「バッチ実行中の表示」のため)。

    開始の時刻は RUNNING_STARTED_AT に固定する(撮るたびに表示の時刻が変わらないように)。実行中の表示は、開始から
    const.RUNNING_STALE_HOURS 時間以内の行だけが対象なので、API を起動するときにこの値を大きくしておく(start_api)。
    """
    import const
    from common import db

    with db.connect() as conn:
        check_test_db(conn)
        conn.execute("DELETE FROM run_history WHERE status = %s", (const.RUN_STATUS_RUNNING,))
        if on:
            conn.execute(
                "INSERT INTO run_history (batch_type, process_name, target_date, status, started_at)"
                " VALUES (%s, %s, %s, %s, %s)",
                (const.BATCH_TYPE_SINGLE, const.PROCESS_VERIFY, BASE_DAY, const.RUN_STATUS_RUNNING, RUNNING_STARTED_AT),
            )


def take(browser: Browser, base_url: str, shot: Shot) -> list[Path]:
    """1つの画面を、PC とスマホの大きさで撮る。撮ったファイルを返す。"""
    files: list[Path] = []
    for label, width, scale in SIZES:
        context = browser.new_context(
            viewport={"width": width, "height": VIEWPORT_HEIGHT},
            device_scale_factor=scale,
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
        )
        page = context.new_page()
        page.clock.set_fixed_time(BROWSER_NOW)
        page.goto(base_url + shot.path(), timeout=LOAD_TIMEOUT_MS)
        wait_loaded(page)
        if shot.action is not None:
            shot.action(page)
        out = OUTPUT_DIR / f"画面イメージ_{shot.name}_{label}.png"
        if shot.clip_selector is None:
            page.screenshot(path=str(out), full_page=True)
        else:
            bottom = page.locator(shot.clip_selector).first.evaluate(
                "el => el.getBoundingClientRect().bottom + window.scrollY"
            )
            page.screenshot(
                path=str(out), full_page=True, clip={"x": 0, "y": 0, "width": width, "height": float(bottom) + 16}
            )
        files.append(out)
        context.close()
    return files


def start_api() -> tuple[object, threading.Thread]:
    """テスト用の DB につないだ API を、別のスレッドで起動する。(uvicorn のサーバー, スレッド)を返す。"""
    import uvicorn

    from api import web_const

    # ログは stock_prediction_web\logs ではなく、一時フォルダに出す
    log_dir = Path(tempfile.mkdtemp(prefix="screenshot_logs_"))
    web_const.LOG_DIR = str(log_dir)  # type: ignore[misc]
    import api.common
    import api.routers.candidates
    import api.routers.holdings
    import api.routers.runs

    # API の「今日」を基準の日にする(実行履歴の期間の初期値などが、撮る日によって変わらないように)
    # 実行中とみなす時間(バッチの const の値)を、このプロセスの中だけで大きくする(set_running を参照)
    import const
    from api import logger as api_logger
    from api.main import create_app

    setattr(const, "RUNNING_STALE_HOURS", CAPTURE_RUNNING_STALE_HOURS)  # noqa: B010 - Final の値を差し替えるため
    for module in (api.common, api.routers.candidates, api.routers.holdings, api.routers.runs):
        setattr(module, "today", lambda: BASE_DAY)  # noqa: B010 - 関数を差し替えるため
    app = create_app()
    # API の開始・終了のログは、コンソールには出さない(一時フォルダのファイルにだけ出す)
    for handler in list(api_logger.logger.handlers):
        if type(handler) is logging.StreamHandler:
            api_logger.logger.removeHandler(handler)
    server = uvicorn.Server(uvicorn.Config(app, host=CAPTURE_HOST, port=CAPTURE_PORT, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time_mod.monotonic() + 30
    while not server.started:
        if time_mod.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError(f"API を起動できませんでした(ポート {CAPTURE_PORT} が使われていないか確かめてください)")
        time_mod.sleep(0.1)
    return server, thread


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="画面イメージ(PNG)を本物の画面で撮る")
    parser.add_argument("numbers", nargs="*", help="撮る画面の番号(01〜11)。省略すると全て")
    parser.add_argument("--skip-db", action="store_true", help="架空の値を入れ直さない")
    args = parser.parse_args(argv)
    use_test_db()
    started = time_mod.perf_counter()
    if not args.skip_db:
        make_sample_db.main()
    ctx = load_context()
    shots = [s for s in build_shots(ctx) if not args.numbers or s.no in args.numbers]
    server, thread = start_api()
    base_url = f"http://{CAPTURE_HOST}:{CAPTURE_PORT}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for shot in shots:
                if shot.no == "02":
                    set_running(True)
                try:
                    for f in take(browser, base_url, shot):
                        print(f"撮りました: {f.name}")
                finally:
                    if shot.no == "02":
                        set_running(False)
            browser.close()
    finally:
        server.should_exit = True  # type: ignore[attr-defined]
        thread.join(timeout=10)
    print(f"終わりました({time_mod.perf_counter() - started:.1f}秒)")


if __name__ == "__main__":  # pragma: no cover - コマンドで実行するだけ
    main()
