"""J-Quants API(V2)の呼び出し。

APIキーは、リクエストのヘッダー x-api-key に入れる。
件数が多くて1回で返りきらない場合は、pagination_key を付けて続きのページを取得し、全件をまとめて返す。
"""

import re
from datetime import date
from typing import Any

import requests

import const
from common.env import get_env
from common.errors import ApiError, PlanRangeError, RateLimitError, TransientApiError
from common.rate_limit import fins_rate_limited, rate_limited, retry_on_error

# 使うAPIのパス
PATH_DAILY_BARS: str = "/equities/bars/daily"
PATH_MASTER: str = "/equities/master"
PATH_CALENDAR: str = "/markets/calendar"
PATH_VALUATION: str = "/equities/valuation"
PATH_FIN_SUMMARY: str = "/fins/summary"
PATH_EARNINGS_DATE: str = "/fins/earnings-date"

# 契約プランの期間外の可能性を、ログのエラーメッセージに加える文
PLAN_RANGE_HINT: str = "契約プランの期間外の可能性があります"

# 契約プランの期間外の 400 のメッセージに入っている文(例: 「... following dates: 2016-11-02 ~ .」)
SUBSCRIPTION_RANGE_MARKER: str = "Your subscription covers the following dates:"
_SUBSCRIPTION_FIRST_DATE = re.compile(re.escape(SUBSCRIPTION_RANGE_MARKER) + r"\s*(\d{4})-?(\d{2})-?(\d{2})")

# 接続を使い回すためのセッション(テストで差し替える)
session: requests.Session = requests.Session()

# 最後に呼び出したときのページ数(初回の一括取得の時間の見積もりに使う)
last_page_count: int = 0


def parse_subscription_first_date(text: str) -> date | None:
    """契約プランの期間外の 400 のメッセージから、契約で取れる最初の日を取り出す。読み取れなければ None。"""
    m = _SUBSCRIPTION_FIRST_DATE.search(text)
    if m is None:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _describe(path: str, params: dict[str, str]) -> str:
    """エラーメッセージに入れる、どの呼び出しかの説明。pagination_key は長いので入れない。"""
    shown = " ".join(f"{k}={v}" for k, v in params.items() if k != "pagination_key")
    return f"{path} {shown}".strip()


def _request_once(path: str, params: dict[str, str]) -> dict[str, Any]:
    """1回のリクエストを送り、レスポンスのJSONを返す。

    Raises:
        RateLimitError: 429(やり直す)
        TransientApiError: 通信エラー、タイムアウト、5xx(やり直す)
        PlanRangeError: 403、決算発表予定日の 400、契約期間が入った 400(契約プランの期間外の可能性。やり直さない)
        ApiError: それ以外の4xx など(やり直さない)
    """
    url = const.JQUANTS_BASE_URL + path
    headers = {"x-api-key": get_env("JQUANTS_API_KEY")}
    try:
        response = session.get(url, headers=headers, params=params, timeout=const.API_TIMEOUT_SEC)
    except (requests.ConnectionError, requests.Timeout) as e:
        raise TransientApiError(f"通信エラー({_describe(path, params)}): {e}") from e

    status = response.status_code
    if status == 200:
        body: Any = response.json()
        if not isinstance(body, dict):
            raise ApiError(f"J-Quants API のレスポンスの形が想定と違います({_describe(path, params)})", status)
        return body
    if status == 210:
        # データを取得できなかった。0件として扱い、やり直さない
        return {"data": []}
    if status == 429:
        raise RateLimitError(f"リクエスト数の上限を超えました({_describe(path, params)})", status)
    if status >= 500:
        raise TransientApiError(f"J-Quants API がエラーを返しました({status} {_describe(path, params)})", status)

    message = f"J-Quants API がエラーを返しました({status} {_describe(path, params)})"
    detail = response.text.strip()[:200]
    if detail:
        message += f": {detail}"
    if status == 400 and SUBSCRIPTION_RANGE_MARKER in response.text:
        # 全てのエンドポイント共通。取れる最初の日が読み取れれば持たせる
        raise PlanRangeError(
            f"{message}。{PLAN_RANGE_HINT}",
            status,
            subscription_range=True,
            first_date=parse_subscription_first_date(response.text),
        )
    if status == 403 or (status == 400 and path == PATH_EARNINGS_DATE):
        raise PlanRangeError(f"{message}。{PLAN_RANGE_HINT}", status)
    raise ApiError(message, status)


@retry_on_error
@rate_limited
def _send_request(path: str, params: dict[str, str]) -> dict[str, Any]:
    """全体の上限を守って送る。"""
    return _request_once(path, params)


@retry_on_error
@fins_rate_limited
@rate_limited
def _send_fins_request(path: str, params: dict[str, str]) -> dict[str, Any]:
    """財務情報の上限と全体の上限の両方を守って送る。"""
    return _request_once(path, params)


def request_all_pages(path: str, params: dict[str, str]) -> list[dict[str, Any]]:
    """全ページを取得して、レスポンスの data をつないで返す。0件の場合は空のリスト。"""
    global last_page_count
    rows: list[dict[str, Any]] = []
    query = dict(params)
    pages = 0
    send = _send_fins_request if path == PATH_FIN_SUMMARY else _send_request
    while True:
        body = send(path, query)
        pages += 1
        data = body.get("data") or []
        if not isinstance(data, list):
            raise ApiError(f"J-Quants API のレスポンスの data がリストではありません({_describe(path, params)})")
        rows.extend(data)
        key = body.get("pagination_key")
        if not key:
            break
        query = dict(params)
        query["pagination_key"] = str(key)
    last_page_count = pages
    return rows


def _ymd(d: date) -> str:
    return d.isoformat()


def fetch_daily_bars(target_date: date) -> list[dict[str, Any]]:
    """株価四本値。全銘柄の1日分。"""
    return request_all_pages(PATH_DAILY_BARS, {"date": _ymd(target_date)})


def fetch_master(target_date: date) -> list[dict[str, Any]]:
    """上場銘柄一覧。その日時点の全銘柄。"""
    return request_all_pages(PATH_MASTER, {"date": _ymd(target_date)})


def fetch_calendar(date_from: date, date_to: date) -> list[dict[str, Any]]:
    """取引カレンダー。"""
    return request_all_pages(PATH_CALENDAR, {"from": _ymd(date_from), "to": _ymd(date_to)})


def fetch_valuation(target_date: date) -> list[dict[str, Any]]:
    """バリュエーション指標。全銘柄の1日分。"""
    return request_all_pages(PATH_VALUATION, {"date": _ymd(target_date)})


def fetch_fin_summary(disc_date: date) -> list[dict[str, Any]]:
    """財務情報。その日に開示された全銘柄の分。"""
    return request_all_pages(PATH_FIN_SUMMARY, {"date": _ymd(disc_date)})


def fetch_earnings_date(pub_date: date) -> list[dict[str, Any]]:
    """決算発表予定日。その日に公表・変更された全銘柄の分。"""
    return request_all_pages(PATH_EARNINGS_DATE, {"date": _ymd(pub_date)})
