#!/usr/bin/env python3
"""
金融市場每日監控指標 — 資料抓取腳本
GitHub Actions 每日自動執行

資料取得順序
------------------------------------------------------------
股票 / 指數：
1. Yahoo Finance Daily Chart API
2. Yahoo Finance Intraday Chart API
   - 若 daily 尚未同步 TARGET
   - 使用 TARGET 最後一筆 regular-session 5m close
3. yfinance Ticker.history
4. yf.download

Treasury：
- Treasury.gov 官方資料

資料安全原則
------------------------------------------------------------
1. TARGET 必須為指定交易日
2. 每項資料 date 必須 == TARGET
3. value 不可為 None / NaN / inf
4. 驗證失敗完全不寫 JSON
5. allow_nan=False
6. 歷史補抓不允許 latest.json 倒退
"""

import json
import math
import sys
import time
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd
import pandas_market_calendars as mcal
import requests
import yfinance as yf


# ============================================================
# CONFIG
# ============================================================

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

YAHOO_HOSTS = [
    "query2.finance.yahoo.com",
    "query1.finance.yahoo.com",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://finance.yahoo.com/",
}


# ============================================================
# TARGET DATE
# ============================================================

def get_target_date():
    """
    手動：
        python fetch_market.py 2026-09-08

    自動：
        台灣時間今天以前最近一個 NYSE 正式交易日
    """

    if len(sys.argv) > 1:

        target = sys.argv[1]

        # 驗證日期格式
        datetime.strptime(
            target,
            "%Y-%m-%d"
        )

        return target

    now_tw = datetime.now(
        ZoneInfo("Asia/Taipei")
    )

    nyse = mcal.get_calendar(
        "NYSE"
    )

    schedule = nyse.schedule(
        start_date=(
            now_tw.date()
            - timedelta(days=14)
        ),
        end_date=(
            now_tw.date()
            - timedelta(days=1)
        ),
    )

    if schedule.empty:

        raise RuntimeError(
            "找不到最近的 NYSE 交易日"
        )

    return (
        schedule.index[-1]
        .date()
        .strftime("%Y-%m-%d")
    )


TARGET = get_target_date()


print(
    "🕒 Taiwan time: "
    + datetime.now(
        ZoneInfo("Asia/Taipei")
    ).isoformat()
)

print(
    f"🗓 Target market date: {TARGET}"
)


# ============================================================
# BASIC HELPERS
# ============================================================

def is_valid_number(value):
    """
    有效有限數字
    """

    if value is None:
        return False

    try:

        value = float(value)

    except (
        TypeError,
        ValueError
    ):

        return False

    return math.isfinite(
        value
    )


def make_entry(
    curr_val,
    prev_val,
    curr_date,
    source=None,
):
    """
    統一輸出格式
    """

    if not is_valid_number(
        curr_val
    ):

        return None

    curr_val = float(
        curr_val
    )

    if is_valid_number(
        prev_val
    ):

        prev_val = float(
            prev_val
        )

    else:

        prev_val = None

    chg_abs = None
    chg_pct = None

    if prev_val is not None:

        chg_abs = round(
            curr_val
            - prev_val,
            6
        )

        if prev_val != 0:

            chg_pct = round(
                (
                    curr_val
                    - prev_val
                )
                / prev_val
                * 100,
                4
            )

    result = {
        "value": round(
            curr_val,
            4
        ),
        "prev": (
            round(
                prev_val,
                4
            )
            if prev_val
            is not None
            else None
        ),
        "chg_abs": chg_abs,
        "chg_pct": chg_pct,
        "date": curr_date,
    }

    if source:

        result[
            "source"
        ] = source

    return result


def unix_to_market_date(
    timestamp,
    timezone_name=None
):
    """
    Yahoo timestamp 轉交易所日期
    """

    if timezone_name:

        try:

            return (
                datetime.fromtimestamp(
                    timestamp,
                    ZoneInfo(
                        timezone_name
                    )
                )
                .strftime(
                    "%Y-%m-%d"
                )
            )

        except Exception:

            pass

    return (
        datetime.utcfromtimestamp(
            timestamp
        )
        .strftime(
            "%Y-%m-%d"
        )
    )


# ============================================================
# YAHOO DAILY CHART
# ============================================================

def yahoo_daily_get(
    symbol,
    target_date_str
):
    """
    Yahoo daily chart API

    注意：
    只有 curr_date == TARGET 才成功。

    如果 Yahoo 日線尚未同步，
    例如 TARGET=9/8 但只回 9/4，
    則返回 None，讓程式進入 intraday fallback。
    """

    encoded_symbol = quote(
        symbol,
        safe=""
    )

    for host in YAHOO_HOSTS:

        try:

            url = (
                f"https://{host}"
                f"/v8/finance/chart/"
                f"{encoded_symbol}"
            )

            params = {
                "interval": "1d",
                "range": "1mo",
                "includePrePost": "false",
                "events": "div,splits",
            }

            response = requests.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=20,
            )

            response.raise_for_status()

            payload = response.json()

            chart = payload.get(
                "chart",
                {}
            )

            if chart.get(
                "error"
            ):

                raise RuntimeError(
                    chart[
                        "error"
                    ]
                )

            results = (
                chart.get(
                    "result"
                )
                or []
            )

            if not results:

                raise ValueError(
                    "Yahoo daily result empty"
                )

            result = results[0]

            timestamps = (
                result.get(
                    "timestamp"
                )
                or []
            )

            quote_list = (
                result
                .get(
                    "indicators",
                    {}
                )
                .get(
                    "quote"
                )
                or []
            )

            if (
                not timestamps
                or not quote_list
            ):

                raise ValueError(
                    "Yahoo daily timestamp/quote empty"
                )

            closes = (
                quote_list[0]
                .get(
                    "close"
                )
                or []
            )

            timezone_name = (
                result
                .get(
                    "meta",
                    {}
                )
                .get(
                    "exchangeTimezoneName"
                )
            )

            rows = []

            for (
                timestamp,
                close
            ) in zip(
                timestamps,
                closes
            ):

                if not is_valid_number(
                    close
                ):

                    continue

                trade_date = (
                    unix_to_market_date(
                        timestamp,
                        timezone_name
                    )
                )

                if (
                    trade_date
                    <= target_date_str
                ):

                    rows.append(
                        (
                            trade_date,
                            float(close)
                        )
                    )

            if not rows:

                raise ValueError(
                    "no daily rows"
                )

            rows.sort(
                key=lambda x: x[0],
                reverse=True
            )

            curr_date = (
                rows[0][0]
            )

            curr_val = (
                rows[0][1]
            )

            prev_val = (
                rows[1][1]
                if len(rows) > 1
                else None
            )

            # ------------------------------------------------
            # 重要：
            # daily 若還停在舊日期，不接受
            # ------------------------------------------------

            if (
                curr_date
                != target_date_str
            ):

                print(
                    f"  ⚠ {symbol} daily 尚未同步："
                    f"latest={curr_date}, "
                    f"target={target_date_str}"
                )

                return None

            entry = make_entry(
                curr_val,
                prev_val,
                curr_date,
                source="yahoo_daily",
            )

            print(
                f"  ✅ {symbol} "
                f"(Yahoo daily): "
                f"{entry['value']} "
                f"({entry['date']})"
            )

            return entry

        except Exception as e:

            print(
                f"  ⚠ {symbol} "
                f"daily {host}: "
                f"{type(e).__name__}: "
                f"{e}"
            )

    return None


# ============================================================
# GET PREVIOUS CLOSE
# ============================================================

def yahoo_previous_close(
    symbol,
    target_date_str
):
    """
    取得 TARGET 以前最近一個日線 close。

    主要給 intraday fallback 計算漲跌幅。
    """

    encoded_symbol = quote(
        symbol,
        safe=""
    )

    for host in YAHOO_HOSTS:

        try:

            url = (
                f"https://{host}"
                f"/v8/finance/chart/"
                f"{encoded_symbol}"
            )

            params = {
                "interval": "1d",
                "range": "1mo",
                "includePrePost": "false",
            }

            response = requests.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=20,
            )

            response.raise_for_status()

            result = (
                response.json()
                ["chart"]
                ["result"][0]
            )

            timestamps = (
                result.get(
                    "timestamp"
                )
                or []
            )

            quote_list = (
                result
                .get(
                    "indicators",
                    {}
                )
                .get(
                    "quote"
                )
                or []
            )

            if not quote_list:

                continue

            closes = (
                quote_list[0]
                .get(
                    "close"
                )
                or []
            )

            timezone_name = (
                result
                .get(
                    "meta",
                    {}
                )
                .get(
                    "exchangeTimezoneName"
                )
            )

            rows = []

            for (
                timestamp,
                close
            ) in zip(
                timestamps,
                closes
            ):

                if not is_valid_number(
                    close
                ):

                    continue

                trade_date = (
                    unix_to_market_date(
                        timestamp,
                        timezone_name
                    )
                )

                if (
                    trade_date
                    < target_date_str
                ):

                    rows.append(
                        (
                            trade_date,
                            float(close)
                        )
                    )

            if rows:

                rows.sort(
                    key=lambda x: x[0],
                    reverse=True
                )

                return (
                    rows[0][1]
                )

        except Exception:

            pass

    return None


# ============================================================
# YAHOO INTRADAY FALLBACK
# ============================================================

def yahoo_intraday_get(
    symbol,
    target_date_str
):
    """
    當 Yahoo daily 尚未同步 TARGET，
    使用 Yahoo intraday 5m 資料取得 TARGET 收盤價。

    僅接受：
    - TARGET 當天
    - regular trading session
    - 最後一筆有效 close

    目的：
    例如 TARGET = 2026-09-08，
    daily 還只有 9/4，
    但 intraday 已經存在完整 9/8，
    就能補出 9/8 收盤資料。
    """

    target_date = datetime.strptime(
        target_date_str,
        "%Y-%m-%d"
    ).date()

    # 使用美東時間建立 TARGET 的時間範圍
    ny_timezone = ZoneInfo(
        "America/New_York"
    )

    start_dt = datetime.combine(
        target_date,
        dt_time(
            0,
            0
        ),
        tzinfo=ny_timezone,
    )

    end_dt = (
        start_dt
        + timedelta(
            days=1
        )
    )

    period1 = int(
        start_dt.timestamp()
    )

    period2 = int(
        end_dt.timestamp()
    )

    encoded_symbol = quote(
        symbol,
        safe=""
    )

    for host in YAHOO_HOSTS:

        try:

            url = (
                f"https://{host}"
                f"/v8/finance/chart/"
                f"{encoded_symbol}"
            )

            params = {
                "period1": period1,
                "period2": period2,
                "interval": "5m",
                "includePrePost": "false",
                "events": "div,splits",
            }

            response = requests.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=20,
            )

            response.raise_for_status()

            payload = response.json()

            chart = payload.get(
                "chart",
                {}
            )

            if chart.get(
                "error"
            ):

                raise RuntimeError(
                    chart[
                        "error"
                    ]
                )

            results = (
                chart.get(
                    "result"
                )
                or []
            )

            if not results:

                raise ValueError(
                    "intraday result empty"
                )

            result = results[0]

            timestamps = (
                result.get(
                    "timestamp"
                )
                or []
            )

            quote_list = (
                result
                .get(
                    "indicators",
                    {}
                )
                .get(
                    "quote"
                )
                or []
            )

            if (
                not timestamps
                or not quote_list
            ):

                raise ValueError(
                    "intraday timestamp/quote empty"
                )

            closes = (
                quote_list[0]
                .get(
                    "close"
                )
                or []
            )

            timezone_name = (
                result
                .get(
                    "meta",
                    {}
                )
                .get(
                    "exchangeTimezoneName"
                )
                or
                "America/New_York"
            )

            tz = ZoneInfo(
                timezone_name
            )

            rows = []

            for (
                timestamp,
                close
            ) in zip(
                timestamps,
                closes
            ):

                if not is_valid_number(
                    close
                ):

                    continue

                dt_local = (
                    datetime.fromtimestamp(
                        timestamp,
                        tz
                    )
                )

                trade_date = (
                    dt_local
                    .strftime(
                        "%Y-%m-%d"
                    )
                )

                # 只接受 TARGET
                if (
                    trade_date
                    != target_date_str
                ):

                    continue

                # regular session
                # 美國現股通常 09:30 - 16:00
                local_time = (
                    dt_local.time()
                )

                if (
                    local_time
                    < dt_time(
                        9,
                        30
                    )
                    or
                    local_time
                    > dt_time(
                        16,
                        5
                    )
                ):

                    continue

                rows.append(
                    (
                        dt_local,
                        float(close)
                    )
                )

            if not rows:

                raise ValueError(
                    "TARGET intraday rows empty"
                )

            rows.sort(
                key=lambda x: x[0]
            )

            # 最後一筆 regular-session close
            curr_dt = (
                rows[-1][0]
            )

            curr_val = (
                rows[-1][1]
            )

            prev_val = (
                yahoo_previous_close(
                    symbol,
                    target_date_str
                )
            )

            entry = make_entry(
                curr_val,
                prev_val,
                target_date_str,
                source="yahoo_intraday_5m",
            )

            if entry is None:

                raise ValueError(
                    "invalid intraday close"
                )

            print(
                f"  ✅ {symbol} "
                f"(Yahoo intraday 5m): "
                f"{entry['value']} "
                f"({entry['date']}) "
                f"last bar="
                f"{curr_dt.strftime('%H:%M')}"
            )

            return entry

        except Exception as e:

            print(
                f"  ⚠ {symbol} "
                f"intraday {host}: "
                f"{type(e).__name__}: "
                f"{e}"
            )

    return None


# ============================================================
# YFINANCE FALLBACK
# ============================================================

def yfinance_history_get(
    symbol,
    target_date_str
):
    """
    Ticker.history fallback
    """

    target_dt = datetime.strptime(
        target_date_str,
        "%Y-%m-%d"
    )

    start = (
        target_dt
        - timedelta(days=14)
    ).strftime(
        "%Y-%m-%d"
    )

    end = (
        target_dt
        + timedelta(days=2)
    ).strftime(
        "%Y-%m-%d"
    )

    try:

        hist = (
            yf.Ticker(
                symbol
            )
            .history(
                start=start,
                end=end,
                interval="1d",
                auto_adjust=True,
            )
        )

        if hist.empty:

            raise ValueError(
                "empty"
            )

        hist.index = (
            hist.index
            .st
