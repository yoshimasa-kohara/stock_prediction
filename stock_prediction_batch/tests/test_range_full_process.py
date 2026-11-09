"""range_date_full_process.py と common/args.py の parse_range_args のテスト。

各処理の本体は別のテストで確かめているので、ここでは代わりの部品に差し替えて、曜日ごとの呼び分けと止まり方を確かめる。
"""

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

import const
import range_date_full_process as rfp
from common import db, mailer, run_history
from common.args import RangeArgs, parse_range_args
from common.env import resolve_path
from common.errors import ArgError, BatchError
from common.logger import log_file_path
from core import predict_core
from core.collect_core import RangeCollectState

MON = date(2026, 7, 6)  # 月曜
SAT = date(2026, 7, 11)  # 土曜
SUN = date(2026, 7, 12)  # 日曜


class Recorder:
    """呼ばれた処理を記録する、各処理の代わり。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, date, str]] = []
        self.fail_at: tuple[str, date] | None = None
        self.unexpected_at: date | None = None
        # 収集に渡された状態と、通知に渡されたファイル
        self.collect_states: list[RangeCollectState] = []
        self.notify_paths: list[Path | None] = []
        # 収集で、ネットワークのエラーで株価を取れなかったことにする日
        self.network_fail_at: set[date] = set()
        # 収集で、日経平均を取得できなかったことにする日
        self.index_fail_at: set[date] = set()

    def _record(self, name: str, d: date, batch_type: str) -> None:
        self.calls.append((name, d, batch_type))
        if self.fail_at == (name, d):
            raise BatchError(const.PROCESS_LABELS[name], "こわれた")
        if self.unexpected_at == d:
            raise RuntimeError("想定外")

    def collect(self, d: date, batch_type: str, state: RangeCollectState) -> None:
        self.collect_states.append(state)
        self._record("COLLECT", d, batch_type)
        if d in self.network_fail_at:
            # 収集失敗の一覧に書いたことにする
            state.failure_count += 1
            state.failed_price_dates.add(d)
        if d in self.index_fail_at:
            state.index_warning_dates.append(d)

    def train(self, d: date, batch_type: str) -> None:
        self._record("TRAIN", d, batch_type)

    def predict(self, d: date, batch_type: str) -> None:
        self._record("PREDICT", d, batch_type)

    def verify(self, d: date, batch_type: str) -> None:
        self._record("VERIFY", d, batch_type)

    def notify(
        self, d: date, is_backfill: bool, batch_type: str, send_mail: bool = True, text_path: Path | None = None
    ) -> None:
        # メールは送らない指定で呼ばれること
        assert send_mail is False
        assert is_backfill is False
        self.notify_paths.append(text_path)
        self._record("NOTIFY", d, batch_type)

    def backup(self, d: date, batch_type: str) -> None:
        self._record("BACKUP", d, batch_type)

    def cleanup(self, d: date, batch_type: str) -> None:
        self._record("CLEANUP", d, batch_type)

    def names(self) -> list[tuple[str, date]]:
        return [(n, d) for n, d, _ in self.calls]


@pytest.fixture
def rec(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    r = Recorder()
    monkeypatch.setattr(rfp, "run_collect", r.collect)
    monkeypatch.setattr(rfp, "run_train", r.train)
    monkeypatch.setattr(rfp, "run_predict", r.predict)
    monkeypatch.setattr(rfp, "run_verify", r.verify)
    monkeypatch.setattr(rfp, "run_notify", r.notify)
    monkeypatch.setattr(rfp, "run_backup", r.backup)
    monkeypatch.setattr(rfp, "run_cleanup", r.cleanup)
    return r


def _fetched(conn: psycopg.Connection[DictRow], *days: date) -> None:
    for d in days:
        conn.execute(
            "INSERT INTO fetched_date (target_date, data_type, status, record_count)"
            " VALUES (%s, 'PRICE', 'FETCHED', 3)",
            (d,),
        )


def _model(conn: psycopg.Connection[DictRow], base: date) -> None:
    conn.execute(
        "INSERT INTO model (model_version, horizon, model_type, base_date, train_start_date, train_end_date,"
        " train_rows, feature_names, file_path) VALUES ('v1', 5, 'UP_PROB', %s, %s, %s, 1, '[]', 'v1/a.txt')",
        (base, base, base),
    )


# =====================================================================
# 曜日ごとの呼び分け
# =====================================================================


def test_平日は収集から通知まで土曜は学習からクリーニングまで日曜は何もしない(
    conn: psycopg.Connection[DictRow], rec: Recorder
) -> None:
    _model(conn, MON - timedelta(days=3))
    _fetched(conn, *(MON + timedelta(days=i) for i in range(5)))
    assert rfp.run_range(MON, SUN, MON) == 0

    expected: list[tuple[str, date]] = []
    for i in range(5):
        d = MON + timedelta(days=i)
        expected += [("COLLECT", d), ("PREDICT", d), ("VERIFY", d), ("NOTIFY", d)]
    expected += [("TRAIN", SAT), ("BACKUP", SAT), ("CLEANUP", SAT)]
    assert rec.names() == expected
    # 起動のしかたは、平日は DAILY、土曜は WEEKLY
    assert {bt for n, _, bt in rec.calls if n in ("COLLECT", "PREDICT", "VERIFY", "NOTIFY")} == {const.BATCH_TYPE_DAILY}
    assert {bt for n, _, bt in rec.calls if n in ("TRAIN", "BACKUP", "CLEANUP")} == {const.BATCH_TYPE_WEEKLY}
    assert "処理を終了します(処理した日数: 7日)" in log_file_path().read_text(encoding="utf-8")


def test_株価がない日は収集だけ(conn: psycopg.Connection[DictRow], rec: Recorder) -> None:
    _model(conn, MON - timedelta(days=3))
    assert rfp.run_range(MON, MON, MON) == 0
    assert rec.names() == [("COLLECT", MON)]
    assert "株価がない日(休場日など)のため" in log_file_path().read_text(encoding="utf-8")


def test_predict_fromより前は平日は収集だけで土曜は何もしない(conn: psycopg.Connection[DictRow], rec: Recorder) -> None:
    _fetched(conn, *(MON + timedelta(days=i) for i in range(5)))
    next_mon = SUN + timedelta(days=1)
    _fetched(conn, next_mon)
    _model(conn, SAT)
    assert rfp.run_range(MON, next_mon, next_mon) == 0
    expected = [("COLLECT", MON + timedelta(days=i)) for i in range(5)]
    expected += [("COLLECT", next_mon), ("PREDICT", next_mon), ("VERIFY", next_mon), ("NOTIFY", next_mon)]
    assert rec.names() == expected


def test_最初のモデルはpredict_from以降で最初の土曜に作りそれまでの平日は収集だけ(
    conn: psycopg.Connection[DictRow], rec: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    next_mon = SUN + timedelta(days=1)
    _fetched(conn, *(MON + timedelta(days=i) for i in range(5)), next_mon)

    # 土曜の学習で、モデルが登録されたことにする
    def train(d: date, batch_type: str) -> None:
        rec.calls.append(("TRAIN", d, batch_type))
        _model(conn, d)

    monkeypatch.setattr(rfp, "run_train", train)
    assert rfp.run_range(MON, next_mon, MON) == 0
    expected = [("COLLECT", MON + timedelta(days=i)) for i in range(5)]
    expected += [("TRAIN", SAT), ("BACKUP", SAT), ("CLEANUP", SAT)]
    expected += [("COLLECT", next_mon), ("PREDICT", next_mon), ("VERIFY", next_mon), ("NOTIFY", next_mon)]
    assert rec.names() == expected
    assert "使えるモデルがまだないため、予測・検証・通知は行いません" in log_file_path().read_text(encoding="utf-8")


# =====================================================================
# 収集で取れなかった日と、通知の内容のファイル
# =====================================================================


def test_収集の状態は全ての日で使い回し通知の内容はファイルに出す(
    conn: psycopg.Connection[DictRow], rec: Recorder
) -> None:
    _model(conn, MON - timedelta(days=3))
    _fetched(conn, *(MON + timedelta(days=i) for i in range(5)))
    assert rfp.run_range(MON, SUN, MON) == 0
    assert len(rec.collect_states) == 5
    assert all(s is rec.collect_states[0] for s in rec.collect_states)
    assert rec.collect_states[0].failure_path.name.startswith("収集失敗_")
    expected = rfp.notify_file_path(MON, SUN)
    assert expected.name == "全処理_通知_2026-07-06_2026-07-12.txt"
    assert rec.notify_paths == [expected] * 5


def test_収集で株価を取れなかった日は予測をとばし最後まで処理して終了コード1(
    conn: psycopg.Connection[DictRow], rec: Recorder
) -> None:
    _model(conn, MON - timedelta(days=3))
    tue = MON + timedelta(days=1)
    _fetched(conn, MON, tue + timedelta(days=1))
    rec.network_fail_at = {tue}
    assert rfp.run_range(MON, tue + timedelta(days=1), MON) == 1
    assert rec.names() == [
        ("COLLECT", MON), ("PREDICT", MON), ("VERIFY", MON), ("NOTIFY", MON),
        ("COLLECT", tue),
        ("COLLECT", tue + timedelta(days=1)), ("PREDICT", tue + timedelta(days=1)),
        ("VERIFY", tue + timedelta(days=1)), ("NOTIFY", tue + timedelta(days=1)),
    ]  # fmt: skip
    text = log_file_path().read_text(encoding="utf-8")
    assert "WARNING 全処理 2026-07-07 - 株価を取得できなかったため、予測・検証・通知は行いません" in text
    assert "収集で取れなかったデータが 1件あります" in text


def test_収集の行はコメントアウトされていない() -> None:
    # 本番の初回の収集のため、run_collect の行は有効にしておく。収集をとばすときだけコメントアウトする
    source = Path(rfp.__file__).read_text(encoding="utf-8")
    assert "    run_collect(d, batch_type=const.BATCH_TYPE_DAILY, state=collect_state)\n" in source


# =====================================================================
# 学習の間隔(RANGE_TRAIN_INTERVAL_WEEKS)
# =====================================================================


def test_最初の学習の日はpredict_from以降で最初の土曜() -> None:
    assert rfp.first_train_date(MON) == SAT
    assert rfp.first_train_date(SAT) == SAT
    assert rfp.first_train_date(SUN) == SAT + timedelta(days=7)


@pytest.mark.parametrize(
    ("interval", "weeks", "expected"),
    [
        (1, 0, True),
        (1, 1, True),
        (4, 0, True),
        (4, 1, False),
        (4, 3, False),
        (4, 4, True),
        (4, 8, True),
    ],
)
def test_学習するのは最初の土曜からN週ごと(
    monkeypatch: pytest.MonkeyPatch, interval: int, weeks: int, expected: bool
) -> None:
    monkeypatch.setattr(const, "RANGE_TRAIN_INTERVAL_WEEKS", interval)
    assert rfp.is_train_day(SAT + timedelta(days=7 * weeks), MON) is expected


def test_間隔を4週にしたときは4週ごとに学習し学習しない土曜もバックアップとクリーニングは行う(
    conn: psycopg.Connection[DictRow], rec: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 間隔を 4 にした場合(何年分もやり直すとき)の動き。本番の値は 1
    monkeypatch.setattr(const, "RANGE_TRAIN_INTERVAL_WEEKS", 4)
    # 5週分の土曜(最初の学習の土曜から 0〜4週後)
    sats = [SAT + timedelta(days=7 * i) for i in range(5)]
    assert rfp.run_range(SAT, sats[-1], MON) == 0
    weekly = [(n, d) for n, d in rec.names() if n in ("TRAIN", "BACKUP", "CLEANUP")]
    expected: list[tuple[str, date]] = []
    for i, s in enumerate(sats):
        if i in (0, 4):
            expected.append(("TRAIN", s))
        expected += [("BACKUP", s), ("CLEANUP", s)]
    assert weekly == expected
    text = log_file_path().read_text(encoding="utf-8")
    assert "学習の間隔: 4週ごと" in text
    assert "学習は4週ごとのため、この土曜は学習しません(バックアップ・クリーニングだけ行います)" in text


def test_間隔を4週にしたときはやり直しても学習する土曜は変わらない(
    conn: psycopg.Connection[DictRow], rec: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 間隔を 4 にした場合(何年分もやり直すとき)の動き。本番の値は 1
    monkeypatch.setattr(const, "RANGE_TRAIN_INTERVAL_WEEKS", 4)
    # --predict-from は変えずに、--from を3週後の月曜にしてやり直す
    restart = MON + timedelta(days=7 * 3)
    assert rfp.run_range(restart, SAT + timedelta(days=7 * 4), MON) == 0
    trains = [d for n, d in rec.names() if n == "TRAIN"]
    assert trains == [SAT + timedelta(days=7 * 4)]


def test_学習の間隔が1未満なら始めない(rec: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(const, "RANGE_TRAIN_INTERVAL_WEEKS", 0)
    assert rfp.run_range(MON, SUN, MON) == 1
    assert rec.calls == []
    assert "RANGE_TRAIN_INTERVAL_WEEKS の値が正しくありません(0)" in log_file_path().read_text(encoding="utf-8")


# =====================================================================
# 本番の値の学習の間隔(const.RANGE_TRAIN_INTERVAL_WEEKS = 1。値は monkeypatch で固定しない)
# =====================================================================


def test_本番の学習の間隔は1週() -> None:
    # 本番の値が変わったら、ここで気付けるようにする
    assert const.RANGE_TRAIN_INTERVAL_WEEKS == 1


def test_本番の値では全ての土曜で学習する(conn: psycopg.Connection[DictRow], rec: Recorder) -> None:
    # 5週分の土曜(最初の学習の土曜から 0〜4週後)
    sats = [SAT + timedelta(days=7 * i) for i in range(5)]
    assert all(rfp.is_train_day(s, MON) for s in sats)
    assert rfp.run_range(SAT, sats[-1], MON) == 0
    assert [d for n, d in rec.names() if n == "TRAIN"] == sats
    assert "この土曜は学習しません" not in log_file_path().read_text(encoding="utf-8")


def test_本番の値では平日は直前の土曜のモデルを使う(
    conn: psycopg.Connection[DictRow], rec: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    used: dict[date, str] = {}

    def train(d: date, batch_type: str) -> None:
        # 学習の代わりに、その土曜を基準日にしたモデルの行とファイルを作る
        rec.train(d, batch_type)
        version = d.strftime("%Y%m%d")
        folder = resolve_path(const.MODEL_DIR) / version
        folder.mkdir(parents=True)
        for horizon in const.PREDICT_HORIZONS:
            for model_type in const.MODEL_TYPES:
                file_name = f"{horizon}_{model_type}.txt"
                (folder / file_name).touch()
                conn.execute(
                    "INSERT INTO model (model_version, horizon, model_type, base_date, train_start_date,"
                    " train_end_date, train_rows, feature_names, file_path)"
                    " VALUES (%s, %s, %s, %s, %s, %s, 1, '[]', %s)",
                    (version, horizon, model_type, d, d, d, f"{version}/{file_name}"),
                )

    def predict(d: date, batch_type: str) -> None:
        # モデルの選び方は本物(predict_core.load_models)で確かめる
        rec.predict(d, batch_type)
        with db.connect() as c:
            used[d] = predict_core.load_models(c, d).version

    monkeypatch.setattr(rfp, "run_train", train)
    monkeypatch.setattr(rfp, "run_predict", predict)
    monkeypatch.setattr(predict_core, "load_booster", lambda path: object())

    first_week = [MON + timedelta(days=i) for i in range(5)]  # 最初の土曜より前なので、モデルがない
    next_sat = SAT + timedelta(days=7)
    second_week = [SAT + timedelta(days=2 + i) for i in range(5)]
    third_week = [next_sat + timedelta(days=2 + i) for i in range(5)]
    _fetched(conn, *first_week, *second_week, *third_week)

    assert rfp.run_range(MON, third_week[-1], MON) == 0
    assert used == {
        **dict.fromkeys(second_week, SAT.strftime("%Y%m%d")),
        **dict.fromkeys(third_week, next_sat.strftime("%Y%m%d")),
    }


# =====================================================================
# 失敗したときの止まり方
# =====================================================================


def test_失敗したらその日で止める(conn: psycopg.Connection[DictRow], rec: Recorder) -> None:
    _model(conn, MON - timedelta(days=3))
    tue = MON + timedelta(days=1)
    _fetched(conn, MON, tue, tue + timedelta(days=1))
    rec.fail_at = ("VERIFY", tue)
    assert rfp.run_range(MON, SUN, MON) == 1
    # 失敗した日の、失敗した処理より後と、次の日以降は呼ばない
    assert rec.names()[-1] == ("VERIFY", tue)
    assert ("NOTIFY", tue) not in rec.names()
    assert all(d <= tue for _, d in rec.names())
    text = log_file_path().read_text(encoding="utf-8")
    assert "検証 で失敗したため、ここで止めます(こわれた)" in text
    assert "--from 2026-07-07 を指定して、もう一度実行してください" in text


def test_土曜の処理で失敗したら止める(conn: psycopg.Connection[DictRow], rec: Recorder) -> None:
    rec.fail_at = ("BACKUP", SAT)
    assert rfp.run_range(SAT, SUN, SAT) == 1
    assert rec.names() == [("TRAIN", SAT), ("BACKUP", SAT)]


def test_想定外のエラーでも止める(conn: psycopg.Connection[DictRow], rec: Recorder) -> None:
    rec.unexpected_at = MON
    assert rfp.run_range(MON, SUN, MON) == 1
    assert rec.names() == [("COLLECT", MON)]
    text = log_file_path().read_text(encoding="utf-8")
    assert "想定外のエラーが発生したため、ここで止めます: 想定外" in text
    assert "Traceback" in text


def test_実行中の処理があれば始めない(conn: psycopg.Connection[DictRow], rec: Recorder) -> None:
    run_history.start_run(const.BATCH_TYPE_DAILY, const.PROCESS_DAILY_BATCH, MON)
    assert rfp.run_range(MON, SUN, MON) == 1
    assert rec.calls == []


def test_二重起動の確認ができなければ始めない(
    conn: psycopg.Connection[DictRow], rec: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: Any) -> bool:
        raise RuntimeError("DBに接続できません")

    monkeypatch.setattr(rfp, "prepare", broken)
    assert rfp.run_range(MON, SUN, MON) == 1
    assert rec.calls == []
    assert "処理を始められませんでした: DBに接続できません" in log_file_path().read_text(encoding="utf-8")


# =====================================================================
# 引数
# =====================================================================


def test_引数を解釈する() -> None:
    assert parse_range_args(["--from", "2024-07-29", "--to", "2026-07-22"]) == RangeArgs(
        date(2024, 7, 29), date(2026, 7, 22), date(2024, 7, 29)
    )
    assert parse_range_args(
        ["--from", "2024-07-29", "--to", "2026-07-22", "--predict-from", "2025-07-28"]
    ) == RangeArgs(date(2024, 7, 29), date(2026, 7, 22), date(2025, 7, 28))


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--from", "2024-07-29"], "--from と --to は両方を指定してください"),
        ([], "--from と --to は両方を指定してください"),
        (["--from", "2024-7-29", "--to", "2026-07-22"], r"日付の形式が正しくありません\(--from / --to\)"),
        (["--from", "2026-07-23", "--to", "2026-07-22"], "--from は --to 以前の日付を指定してください"),
        (
            ["--from", "2024-07-29", "--to", "2026-07-22", "--predict-from", "2025-02-30"],
            r"日付の形式が正しくありません\(--predict-from\)",
        ),
        (
            ["--from", "2024-07-29", "--to", "2026-07-22", "--predict-from", "2026-07-23"],
            "--predict-from は --from 〜 --to の間の日付を指定してください",
        ),
        (["--from", "2024-07-29", "--to", "2026-07-22", "--date", "2026-07-22"], "引数が正しくありません"),
    ],
)
def test_引数のエラー(argv: list[str], message: str) -> None:
    with pytest.raises(ArgError, match=message):
        parse_range_args(argv)


def test_mainは引数エラーなら1で何もしない(rec: Recorder) -> None:
    assert rfp.main(["--from", "2024-07-29"]) == 1
    assert rec.calls == []
    assert "--from と --to は両方を指定してください" in log_file_path().read_text(encoding="utf-8")


def test_mainは引数を渡して実行する(conn: psycopg.Connection[DictRow], rec: Recorder) -> None:
    assert rfp.main(["--from", SUN.isoformat(), "--to", SUN.isoformat()]) == 0
    assert rec.calls == []


# =====================================================================
# 元に戻した処理と、失敗・警告のメール
# =====================================================================


class AdminSMTP:
    """送った宛先を記録する、SMTP の代わり。"""

    to_list: list[str] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __enter__(self) -> "AdminSMTP":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def starttls(self) -> None:
        pass

    def login(self, *args: str) -> None:
        pass

    def send_message(self, message: Any) -> None:
        AdminSMTP.to_list.append(str(message["To"]))


@pytest.fixture
def admin_smtp(monkeypatch: pytest.MonkeyPatch) -> type[AdminSMTP]:
    AdminSMTP.to_list = []
    monkeypatch.setattr(mailer, "smtp_class", AdminSMTP)
    monkeypatch.setenv("MAIL_TO", "to@example.com")
    monkeypatch.setenv("ADMIN_MAIL_TO", "admin@example.com")
    return AdminSMTP


def test_平日に収集が呼ばれ土曜にバックアップとクリーニングが呼ばれる(
    conn: psycopg.Connection[DictRow], rec: Recorder
) -> None:
    assert rfp.run_range(MON, MON, MON) == 0
    assert rec.names()[0] == ("COLLECT", MON)
    rec.calls.clear()
    assert rfp.run_range(SAT, SAT, SAT) == 0
    assert rec.names() == [("TRAIN", SAT), ("BACKUP", SAT), ("CLEANUP", SAT)]


def test_全処理の失敗メールは管理者に送る(
    conn: psycopg.Connection[DictRow], rec: Recorder, admin_smtp: type[AdminSMTP]
) -> None:
    _model(conn, MON - timedelta(days=3))
    _fetched(conn, MON)
    rec.fail_at = ("PREDICT", MON)
    assert rfp.run_range(MON, SUN, MON) == 1
    assert admin_smtp.to_list == ["admin@example.com"]
    text = mailer.notify_file_path(MON, const.MAIL_KIND_FAILURE).read_text(encoding="utf-8")
    assert "件名: 【株価予測】全処理失敗(予測) 2026-07-06" in text
    assert "今の状況: 収集は完了、予測で停止、検証・通知は未実行。2026-07-06 で停止" in text


def test_全処理の想定外のエラーも失敗メールを送る(
    conn: psycopg.Connection[DictRow], rec: Recorder, admin_smtp: type[AdminSMTP]
) -> None:
    rec.unexpected_at = MON
    assert rfp.run_range(MON, MON, MON) == 1
    text = mailer.notify_file_path(MON, const.MAIL_KIND_FAILURE).read_text(encoding="utf-8")
    assert "エラーの種類: RuntimeError" in text


def test_間隔を4週にしたときの学習しない土曜の失敗メールは学習を完了と書かない(
    conn: psycopg.Connection[DictRow], rec: Recorder, admin_smtp: type[AdminSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 間隔を 4 にした場合(何年分もやり直すとき)の動き。本番の値は 1
    monkeypatch.setattr(const, "RANGE_TRAIN_INTERVAL_WEEKS", 4)
    next_sat = SAT + timedelta(days=7)
    rec.fail_at = ("CLEANUP", next_sat)
    assert rfp.run_range(next_sat, next_sat, SAT) == 1
    text = mailer.notify_file_path(next_sat, const.MAIL_KIND_FAILURE).read_text(encoding="utf-8")
    assert "今の状況: バックアップは完了、クリーニングで停止" in text


def test_収集失敗の一覧があれば警告メールを1通送る(
    conn: psycopg.Connection[DictRow], rec: Recorder, admin_smtp: type[AdminSMTP]
) -> None:
    rec.network_fail_at = {MON, MON + timedelta(days=1)}
    tue = MON + timedelta(days=1)
    assert rfp.run_range(MON, tue, MON) == 1
    # 警告メールは管理者に1通だけ
    assert admin_smtp.to_list == ["admin@example.com"]
    text = mailer.notify_file_path(tue, const.MAIL_KIND_WARNING).read_text(encoding="utf-8")
    assert "件名: 【株価予測】【警告】全処理 2026-07-07" in text
    assert "取れなかったデータがあります: 2件" in text
    assert "一覧のファイル: " in text and "収集失敗_" in text
    assert "今の状況: 処理は最後まで完了しました" in text


def test_途中で止まったら失敗メールと警告メールを送る(
    conn: psycopg.Connection[DictRow], rec: Recorder, admin_smtp: type[AdminSMTP]
) -> None:
    tue = MON + timedelta(days=1)
    rec.network_fail_at = {MON}
    rec.fail_at = ("COLLECT", tue)
    assert rfp.run_range(MON, tue, MON) == 1
    assert mailer.notify_file_path(tue, const.MAIL_KIND_FAILURE).exists()
    assert mailer.notify_file_path(tue, const.MAIL_KIND_WARNING).exists()
    assert admin_smtp.to_list == ["admin@example.com", "admin@example.com"]


def test_全処理の毎日の通知は日ごとのファイルを作らない(
    conn: psycopg.Connection[DictRow], rec: Recorder, admin_smtp: type[AdminSMTP]
) -> None:
    _model(conn, MON - timedelta(days=3))
    _fetched(conn, MON)
    assert rfp.run_range(MON, MON, MON) == 0
    assert admin_smtp.to_list == []
    assert not mailer.notify_file_path(MON).exists()


def test_日経平均を取得できなかった日があれば警告メールを送り終了コードは0(
    conn: psycopg.Connection[DictRow], rec: Recorder, admin_smtp: type[AdminSMTP]
) -> None:
    tue = MON + timedelta(days=1)
    rec.index_fail_at = {MON, tue}
    assert rfp.run_range(MON, tue, MON) == 0
    assert admin_smtp.to_list == ["admin@example.com"]
    text = mailer.notify_file_path(tue, const.MAIL_KIND_WARNING).read_text(encoding="utf-8")
    assert "警告の内容: 日経平均を取得できなかった(表示だけに使うため、処理は続けました): 2件" in text


def test_日経平均と収集失敗の両方があれば警告メールは1通で終了コード1(
    conn: psycopg.Connection[DictRow], rec: Recorder, admin_smtp: type[AdminSMTP]
) -> None:
    rec.index_fail_at = {MON}
    rec.network_fail_at = {MON}
    assert rfp.run_range(MON, MON, MON) == 1
    assert admin_smtp.to_list == ["admin@example.com"]
    text = mailer.notify_file_path(MON, const.MAIL_KIND_WARNING).read_text(encoding="utf-8")
    assert "取れなかったデータがあります: 1件" in text and "日経平均を取得できなかった" in text
