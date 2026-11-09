"""daily_batch.py、weekly_batch.py、common/batch_runner.py、batファイルのテスト。

各処理の本体は別のテストで確かめているので、ここでは代わりの部品に差し替えて、呼ぶ順番と分かれ道を確かめる。
"""

import copy
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

import const
import daily_batch
import weekly_batch
from common import mailer, run_history
from common.batch_runner import BatchWarnings, status_text
from common.env import BASE_DIR
from common.errors import BatchError, MailSendError
from common.logger import log_file_path
from core.collect_core import CollectResult
from core.index_collect_core import INDEX_WARNING_MESSAGE

D = date(2026, 7, 7)  # 火曜


class FakeSMTP:
    sent: list[EmailMessage] = []
    # send_message の時点の To(同じ EmailMessage を差し替えて使うので、その場で控える)
    to_list: list[str] = []
    fail: bool = False
    # 送れないことにする宛先
    refuse: set[str] = set()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def starttls(self) -> None:
        pass

    def login(self, *args: str) -> None:
        if FakeSMTP.fail:
            raise OSError("ネットワークに接続できません")

    def send_message(self, message: EmailMessage) -> None:
        FakeSMTP.to_list.append(str(message["To"]))
        if str(message["To"]) in FakeSMTP.refuse:
            raise OSError("この宛先には送れません")
        # 同じものを宛先ごとに差し替えて使うので、複製を残す
        FakeSMTP.sent.append(copy.deepcopy(message))


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.sent = []
    FakeSMTP.to_list = []
    FakeSMTP.fail = False
    FakeSMTP.refuse = set()
    monkeypatch.setattr(mailer, "smtp_class", FakeSMTP)
    return FakeSMTP


class Recorder:
    """呼ばれた処理を記録する、各処理の代わり。"""

    def __init__(self, collect_result: CollectResult) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.collect_result = collect_result
        self.fail_at: tuple[str, date] | None = None

    def _maybe_fail(self, name: str, d: date) -> None:
        if self.fail_at == (name, d):
            raise BatchError(const.PROCESS_LABELS[name], "こわれた", "11110")

    def collect(self, d: date, batch_type: str) -> CollectResult:
        self.calls.append(("COLLECT", d, batch_type))
        self._maybe_fail("COLLECT", d)
        return self.collect_result

    def predict(self, d: date, batch_type: str) -> None:
        self.calls.append(("PREDICT", d))
        self._maybe_fail("PREDICT", d)

    def verify(self, d: date, batch_type: str) -> None:
        self.calls.append(("VERIFY", d))
        self._maybe_fail("VERIFY", d)

    def notify(self, d: date, is_backfill: bool, batch_type: str, warnings: BatchWarnings | None = None) -> None:
        self.calls.append(("NOTIFY", d, is_backfill))
        self._maybe_fail("NOTIFY", d)

    def install_daily(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(daily_batch, "run_collect", lambda d, batch_type: self.collect(d, batch_type))
        monkeypatch.setattr(daily_batch, "run_predict", self.predict)
        monkeypatch.setattr(daily_batch, "run_verify", self.verify)
        monkeypatch.setattr(daily_batch, "run_notify", self.notify)

    def install_weekly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name, func in (("run_train", "TRAIN"), ("run_backup", "BACKUP"), ("run_cleanup", "CLEANUP")):

            def fake(d: date, batch_type: str, _n: str = func) -> None:
                self.calls.append((_n, d, batch_type))
                self._maybe_fail(_n, d)

            monkeypatch.setattr(weekly_batch, name, fake)


def _batch_row(conn: psycopg.Connection[DictRow], process: str) -> DictRow:
    row = conn.execute("SELECT * FROM run_history WHERE process_name = %s", (process,)).fetchone()
    assert row is not None
    return row


# =====================================================================
# 日次バッチ
# =====================================================================


def test_日次バッチは取り逃した日も古い順に処理する(
    conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = Recorder(CollectResult([date(2026, 7, 6), D], False, 2))
    rec.install_daily(monkeypatch)
    assert daily_batch.main(["--date", "2026-07-07"]) == 0
    assert rec.calls == [
        ("COLLECT", D, "DAILY"),
        ("PREDICT", date(2026, 7, 6)), ("VERIFY", date(2026, 7, 6)), ("NOTIFY", date(2026, 7, 6), True),
        ("PREDICT", D), ("VERIFY", D), ("NOTIFY", D, False),
    ]  # fmt: skip
    row = _batch_row(conn, "DAILY_BATCH")
    assert (row["batch_type"], row["status"], row["record_count"]) == ("DAILY", "SUCCESS", 2)
    assert smtp.sent == []
    log = log_file_path().read_text(encoding="utf-8")
    assert "日次バッチ 2026-07-07 - 実行中の処理はありません" in log
    assert "2026-07-06 の処理を開始します(補完分)" in log


def test_営業日に空振りしたらメールを送り補完した日は処理する(
    conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = Recorder(CollectResult([date(2026, 7, 6)], True, 1))
    rec.install_daily(monkeypatch)
    assert daily_batch.run_daily(D) == 0
    assert smtp.sent[0]["Subject"] == "【株価予測】本日はデータを取得できませんでした 2026-07-07"
    text = smtp.sent[0].get_body(("plain",))
    assert text is not None and "本日(2026-07-07)は、株価データを取得できませんでした。" in str(text.get_content())
    assert ("NOTIFY", date(2026, 7, 6), True) in rec.calls


def test_休場日の空振りはメールを送らず空振りで終わる(
    conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = Recorder(CollectResult([], True, 0))
    rec.install_daily(monkeypatch)
    assert daily_batch.run_daily(date(2026, 7, 20)) == 0  # 海の日
    assert smtp.sent == []
    assert _batch_row(conn, "DAILY_BATCH")["status"] == "EMPTY"
    assert [c[0] for c in rec.calls] == ["COLLECT"]


def test_失敗したら後の処理を止めて失敗メールを送る(
    conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = Recorder(CollectResult([date(2026, 7, 6), D], False, 2))
    rec.fail_at = ("PREDICT", date(2026, 7, 6))
    rec.install_daily(monkeypatch)
    assert daily_batch.run_daily(D) == 1
    assert rec.calls[-1] == ("PREDICT", date(2026, 7, 6))
    mail = smtp.sent[0]
    assert mail["Subject"] == "【株価予測】日次バッチ失敗(予測) 2026-07-06"
    body = mail.get_body(("plain",))
    assert body is not None
    text = str(body.get_content())
    for expected in ("バッチの種類: 日次バッチ", "失敗した処理: 予測", "対象の日付: 2026-07-06",
                     "処理中の銘柄: 11110", "エラーの内容: こわれた", "ログファイル: "):  # fmt: skip
        assert expected in text
    row = _batch_row(conn, "DAILY_BATCH")
    assert (row["status"], row["error_code"], row["error_message"]) == ("FAILED", "11110", "予測: こわれた")


def test_想定外のエラーも失敗メールを送る(
    conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = Recorder(CollectResult([D], False, 1))
    rec.install_daily(monkeypatch)

    def broken(d: date, batch_type: str) -> None:
        raise ValueError("想定外")

    monkeypatch.setattr(daily_batch, "run_verify", broken)
    assert daily_batch.run_daily(D) == 1
    assert smtp.sent[0]["Subject"] == "【株価予測】日次バッチ失敗(日次バッチ) 2026-07-07"


def test_失敗メールを送れなくても終了コード1で終わる(
    conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = Recorder(CollectResult([D], False, 1))
    rec.fail_at = ("COLLECT", D)
    rec.install_daily(monkeypatch)
    smtp.fail = True
    assert daily_batch.run_daily(D) == 1
    assert "失敗メールを送れませんでした" in log_file_path().read_text(encoding="utf-8")


def test_空振りのメールを送れない場合は失敗(
    conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    Recorder(CollectResult([], True, 0)).install_daily(monkeypatch)
    smtp.fail = True
    assert daily_batch.run_daily(D) == 1
    assert _batch_row(conn, "DAILY_BATCH")["status"] == "FAILED"


def test_営業日を判定できない場合は失敗(
    conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    Recorder(CollectResult([], True, 0)).install_daily(monkeypatch)
    monkeypatch.setattr(const, "BUSINESS_DAY_SOURCE", const.BUSINESS_DAY_SOURCE_CALENDAR)
    assert daily_batch.run_daily(D) == 1
    assert "失敗(収集)" in str(smtp.sent[0]["Subject"])


def test_実行中の処理があれば何もせずに終わる(
    conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = Recorder(CollectResult([D], False, 1))
    rec.install_daily(monkeypatch)
    run_history.start_run("SINGLE", "COLLECT", D)
    assert daily_batch.run_daily(D) == 0
    assert rec.calls == []
    assert "実行中の処理があるため、起動しません(処理の名前: COLLECT、開始日時: " in log_file_path().read_text(
        encoding="utf-8"
    )


def test_途中で止まった実行履歴は片付けてから起動する(
    conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = Recorder(CollectResult([D], False, 1))
    rec.install_daily(monkeypatch)
    stale = run_history.start_run("DAILY", "DAILY_BATCH", D - timedelta(days=1))
    conn.execute("UPDATE run_history SET started_at = now() - interval '24 hours' WHERE run_id = %s", (stale,))
    assert daily_batch.run_daily(D) == 0
    assert len(rec.calls) == 4
    status = conn.execute("SELECT status FROM run_history WHERE run_id = %s", (stale,)).fetchone()
    assert status is not None and status["status"] == "FAILED"


def test_DBに接続できない場合は失敗メールを送る(smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> int:
        raise OSError("DBに接続できません")

    monkeypatch.setattr(run_history, "mark_stale_runs", broken)
    assert daily_batch.run_daily(D) == 1
    assert weekly_batch.run_weekly(D) == 1
    assert [str(m["Subject"]) for m in smtp.sent] == [
        "【株価予測】日次バッチ失敗(日次バッチ) 2026-07-07",
        "【株価予測】週次バッチ失敗(週次バッチ) 2026-07-07",
    ]


def test_実行履歴を更新できなくても終わる(
    conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = Recorder(CollectResult([D], False, 1))
    rec.fail_at = ("COLLECT", D)
    rec.install_daily(monkeypatch)
    rec.install_weekly(monkeypatch)

    def broken(*args: object) -> None:
        raise OSError("更新できません")

    monkeypatch.setattr(run_history, "finish_run", broken)
    assert daily_batch.run_daily(D) == 1
    conn.execute("UPDATE run_history SET status = 'FAILED'")
    rec.fail_at = ("TRAIN", D)
    assert weekly_batch.run_weekly(D) == 1
    assert log_file_path().read_text(encoding="utf-8").count("実行履歴を更新できませんでした") == 2


def test_日次バッチの引数エラー(conn: psycopg.Connection[DictRow]) -> None:
    assert daily_batch.main(["--date", "x"]) == 1
    assert weekly_batch.main(["--date", "x"]) == 1
    count = conn.execute("SELECT count(*) AS n FROM run_history").fetchone()
    assert count is not None and count["n"] == 0


# =====================================================================
# 週次バッチ
# =====================================================================


def test_週次バッチは学習_バックアップ_クリーニングの順(
    conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = Recorder(CollectResult([], False, 0))
    rec.install_weekly(monkeypatch)
    assert weekly_batch.main(["--date", "2026-07-04"]) == 0
    assert [c[0] for c in rec.calls] == ["TRAIN", "BACKUP", "CLEANUP"]
    assert all(c[2] == "WEEKLY" for c in rec.calls)
    assert _batch_row(conn, "WEEKLY_BATCH")["status"] == "SUCCESS"


@pytest.mark.parametrize(("fail", "called"), [("TRAIN", ["TRAIN"]), ("BACKUP", ["TRAIN", "BACKUP"])])
def test_週次バッチの失敗(
    conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch, fail: str,
    called: list[str],
) -> None:  # fmt: skip
    rec = Recorder(CollectResult([], False, 0))
    rec.fail_at = (fail, D)
    rec.install_weekly(monkeypatch)
    assert weekly_batch.run_weekly(D) == 1
    assert [c[0] for c in rec.calls] == called
    label = const.PROCESS_LABELS[fail]
    assert smtp.sent[0]["Subject"] == f"【株価予測】週次バッチ失敗({label}) 2026-07-07"
    assert _batch_row(conn, "WEEKLY_BATCH")["status"] == "FAILED"


def test_週次バッチの想定外のエラーと二重起動(
    conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(d: date, batch_type: str) -> None:
        raise ValueError("想定外")

    monkeypatch.setattr(weekly_batch, "run_train", broken)
    assert weekly_batch.run_weekly(D) == 1
    assert "週次バッチ失敗(週次バッチ)" in str(smtp.sent[0]["Subject"])
    run_history.start_run("SINGLE", "TRAIN", D)
    assert weekly_batch.run_weekly(D) == 0


# =====================================================================
# batファイル
# =====================================================================


@pytest.mark.parametrize("name", ["daily_batch.bat", "weekly_batch.bat", "sunday_shutdown.bat"])
def test_batファイルはShift_JISとCRLFでシャットダウンする(name: str) -> None:
    raw = (BASE_DIR / name).read_bytes()
    text = raw.decode("cp932")
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    assert text.startswith("@echo off\r\n")
    assert text.rstrip().endswith(r"%SystemRoot%\System32\shutdown.exe /s /t 0")
    # フルパスでない「shutdown /s /t 0」の行があると、同じフォルダの shutdown.bat(旧名)を呼んで無限ループになる
    assert "shutdown /s /t 0" not in [line.strip() for line in text.splitlines()]
    if name != "sunday_shutdown.bat":
        assert r"cd /d 【プログラムへのパス】\stock_prediction\stock_prediction_batch" in text
        assert rf".venv\Scripts\python.exe {Path(name).stem}.py" in text


# =====================================================================
# 失敗メール・警告メール
# =====================================================================


def test_失敗メールは概要を載せてADMIN_MAIL_TOだけに送りファイルに残す(
    conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAIL_TO", "to@example.com")
    monkeypatch.setenv("ADMIN_MAIL_TO", "admin1@example.com,admin2@example.com")
    rec = Recorder(CollectResult([D], False, 1))
    rec.fail_at = ("PREDICT", D)
    rec.install_daily(monkeypatch)
    assert daily_batch.run_daily(D) == 1
    assert smtp.to_list == ["admin1@example.com", "admin2@example.com"]
    text = mailer.notify_file_path(D, const.MAIL_KIND_FAILURE).read_text(encoding="utf-8")
    for expected in (
        "件名: 【株価予測】日次バッチ失敗(予測) 2026-07-07",
        "バッチの種類: 日次バッチ",
        "発生時刻: ",
        "処理中の銘柄: 11110",
        "エラーの種類: BatchError",
        "エラーの内容: こわれた",
        "今の状況: 収集は完了、予測で停止、検証・通知は未実行",
        "ログファイル: ",
        "ログを探す目印: 発生時刻 ",
    ):
        assert expected in text
    assert "@example.com" not in text


def test_今の状況の文言() -> None:
    steps = ("収集", "予測", "検証", "通知")
    assert status_text(steps, "収集") == "収集で停止、予測・検証・通知は未実行"
    assert status_text(steps, "通知") == "収集・予測・検証は完了、通知で停止"
    assert status_text(steps, "日次バッチ").startswith("日次バッチで停止(")
    assert status_text(steps, "予測", "2026-07-07 で停止").endswith("未実行。2026-07-07 で停止")


def test_エラーの内容は1行にして想定外のエラーの種類を載せる(
    conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    Recorder(CollectResult([D], False, 1)).install_daily(monkeypatch)

    def broken(d: date, batch_type: str) -> None:
        raise ValueError("1行目\n2行目")

    monkeypatch.setattr(daily_batch, "run_verify", broken)
    assert daily_batch.run_daily(D) == 1
    text = mailer.notify_file_path(D, const.MAIL_KIND_FAILURE).read_text(encoding="utf-8")
    assert "エラーの種類: ValueError" in text and "エラーの内容: 1行目 2行目" in text


def test_週次バッチの失敗メールも管理者に送る(
    conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAIL_TO", "to@example.com")
    monkeypatch.setenv("ADMIN_MAIL_TO", "admin@example.com")
    sat = date(2026, 7, 4)
    rec = Recorder(CollectResult([], False, 0))
    rec.fail_at = ("BACKUP", sat)
    rec.install_weekly(monkeypatch)
    assert weekly_batch.run_weekly(sat) == 1
    assert smtp.to_list == ["admin@example.com"]
    text = mailer.notify_file_path(sat, const.MAIL_KIND_FAILURE).read_text(encoding="utf-8")
    assert "今の状況: 学習は完了、バックアップで停止、クリーニングは未実行" in text


def test_空振りのメールはMAIL_TOに送り日ごとのファイルに残す(
    conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAIL_TO", "to@example.com")
    monkeypatch.setenv("ADMIN_MAIL_TO", "admin@example.com")
    Recorder(CollectResult([], True, 0)).install_daily(monkeypatch)
    assert daily_batch.run_daily(D) == 0
    assert smtp.to_list == ["to@example.com"]
    assert "本日はデータを取得できませんでした" in mailer.notify_file_path(D).read_text(encoding="utf-8")


def test_一部の宛先に送れなければ最後まで処理して警告メールを1通にまとめる(
    conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAIL_TO", "to@example.com,ng@example.com")
    monkeypatch.setenv("ADMIN_MAIL_TO", "admin@example.com")
    smtp.refuse = {"ng@example.com"}
    d0 = date(2026, 7, 6)
    rec = Recorder(CollectResult([d0, D], False, 2))
    rec.install_daily(monkeypatch)

    # 本物の run_notify の代わりに、宛先ごとの送信だけを本物で行う
    def notify(d: date, is_backfill: bool, batch_type: str, warnings: BatchWarnings | None = None) -> None:
        rec.calls.append(("NOTIFY", d, is_backfill))
        assert warnings is not None
        try:
            mailer.send_mail(f"通知 {d}", "<p>x</p>", "本文", target_date=d)
        except MailSendError as e:
            warnings.add_partial_mail(f"通知 {d}", e)

    monkeypatch.setattr(daily_batch, "run_notify", notify)
    assert daily_batch.run_daily(D) == 1
    # 2日分とも通知まで進む
    assert ("NOTIFY", d0, True) in rec.calls and ("NOTIFY", D, False) in rec.calls
    # 警告は管理者に1通だけ
    assert smtp.to_list.count("admin@example.com") == 1
    assert _batch_row(conn, "DAILY_BATCH")["status"] == "SUCCESS"
    text = mailer.notify_file_path(D, const.MAIL_KIND_WARNING).read_text(encoding="utf-8")
    assert "件名: 【株価予測】【警告】日次バッチ 2026-07-07" in text
    assert text.count("警告の内容: 一部の宛先にメールを送れませんでした") == 2
    assert "今の状況: 処理は最後まで完了しました" in text
    # 他の宛先を知らせない
    assert "@example.com" not in text


def test_全ての宛先に送れなければ失敗メールにする(
    conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAIL_TO", "ng@example.com")
    monkeypatch.setenv("ADMIN_MAIL_TO", "admin@example.com")
    smtp.refuse = {"ng@example.com"}
    Recorder(CollectResult([], True, 0)).install_daily(monkeypatch)
    assert daily_batch.run_daily(D) == 1
    assert mailer.notify_file_path(D, const.MAIL_KIND_FAILURE).exists()
    assert not mailer.notify_file_path(D, const.MAIL_KIND_WARNING).exists()


def test_単独起動では失敗メールも警告メールも送らない(
    conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    import cleanup as cleanup_cli

    # 同じ日付のバックアップが成功していないので、クリーニングは失敗する
    assert cleanup_cli.main(["--date", "2026-07-04"]) == 1
    assert smtp.to_list == []
    assert not (log_file_path().parent / const.NOTIFY_FILE_DIR).exists()


def test_日経平均を取得できなかったら警告メールに入れて成功のまま終わる(
    conn: psycopg.Connection[DictRow], smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMIN_MAIL_TO", "admin@example.com")
    rec = Recorder(CollectResult([D], False, 2, index_warning=INDEX_WARNING_MESSAGE))
    rec.install_daily(monkeypatch)
    # 日経平均だけの警告なら、終了コードは 0(表示だけに使うため)
    assert daily_batch.run_daily(D) == 0
    assert ("NOTIFY", D, False) in rec.calls
    assert smtp.to_list == ["admin@example.com"]
    assert _batch_row(conn, "DAILY_BATCH")["status"] == "SUCCESS"
    text = mailer.notify_file_path(D, const.MAIL_KIND_WARNING).read_text(encoding="utf-8")
    assert "件名: 【株価予測】【警告】日次バッチ 2026-07-07" in text
    assert "警告の内容: 日経平均を取得できなかった(表示だけに使うため、処理は続けました): 1件" in text


def test_日経平均の警告と他の警告があれば終了コード1() -> None:
    warnings = BatchWarnings()
    assert warnings.only_minor
    warnings.add(INDEX_WARNING_MESSAGE, 1, minor=True)
    assert warnings.only_minor
    warnings.add("一部の宛先にメールを送れませんでした", 1)
    assert not warnings.only_minor
