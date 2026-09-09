#!/usr/bin/env python3
"""
金融市場每日監控指標 — 資料抓取腳本

資料來源順序
1. Yahoo Finance Daily Chart API
2. Yahoo Finance Intraday 5m Chart API
3. yfinance Ticker.history
4. yf.download
5. Treasury.gov：2Y / 10Y / 30Y

資料安全原則
- 每筆資料 date 必須 == TARGET
- value 必須為有限數字
- 驗證失敗不寫 JSON
- JSON 禁止 NaN / inf
- 歷史補抓不得讓 latest.json 倒退
"""

import json
import math
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd
import pandas_market_calendars as mcal
import requests
import yfinance as yf


DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

YAHOO_HOSTS = [
    "query2.finance.yahoo.com",
    "query1.finance.yahoo.com",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://finance.yahoo.com/",
}


# ============================================================
# TARGET DATE
# ============================================================

def get_target_date():
    if len(sys.argv) > 1:
        target = sys.argv[1].strip()
        datetime.strptime(target, "%Y-%m-%d")
        return target

    now_tw = datetime.now(ZoneInfo("Asia/Taipei"))

    nyse = mcal.get_calendar("NYSE")

    schedule = nyse.schedule(
        start_date=now_tw.date() - timedelta(days=14),
        end_date=now_tw.date() - timedelta(days=1),
    )

    if schedule.empty:
        raise RuntimeError("找不到最近的 NYSE 交易日")

    return (
        schedule.index[-1]
        .date()
        .strftime("%Y-%m-%d")
    )


TARGET = get_target_date()

print(
    "🕒 Taiwan time:",
    datetime.now(
        ZoneInfo("Asia/Taipei")
    ).isoformat(),
)

print(
    "🗓 Target market date:",
    TARGET,
)


# ============================================================
# BASIC HELPERS
# ============================================================

def is_valid_number(value):
    if value is None:
        return False

    try:
        value = float(value)

    except (
        TypeError,
        ValueError,
    ):
        return False

    return math.isfinite(value)


def make_entry(
    curr_val,
    prev_val,
    curr_date,
    source=None,
):
    if not is_valid_number(
        curr_val
    ):
        return None

    curr_val = float(
        curr_val
    )

    prev_val = (
        float(prev_val)
        if is_valid_number(
            prev_val
        )
        else None
    )

    chg_abs = None
    chg_pct = None

    if prev_val is not None:

        chg_abs = round(
            curr_val - prev_val,
            6,
        )

        if prev_val != 0:

            chg_pct = round(
                (
                    curr_val
                    - prev_val
                )
                / prev_val
                * 100,
                4,
            )

    result = {
        "value": round(
            curr_val,
            4,
        ),
        "prev": (
            round(
                prev_val,
                4,
            )
            if prev_val is not None
            else None
        ),
        "chg_abs": chg_abs,
        "chg_pct": chg_pct,
        "date": curr_date,
    }

    if source:
        result["source"] = source

    return result


def unix_to_market_date(
    timestamp,
    timezone_name=None,
):
    if timezone_name:

        try:

            return (
                datetime.fromtimestamp(
                    timestamp,
                    ZoneInfo(
                        timezone_name
                    ),
                )
                .strftime(
                    "%Y-%m-%d"
                )
            )

        except Exception:
            pass

    return (
        datetime.fromtimestamp(
            timestamp,
            ZoneInfo("UTC"),
        )
        .strftime(
            "%Y-%m-%d"
        )
    )


# ============================================================
# YAHOO COMMON REQUEST
# ============================================================

def yahoo_chart_request(
    symbol,
    params,
):
    encoded_symbol = quote(
        symbol,
        safe="",
    )

    last_error = None

    for host in YAHOO_HOSTS:

        try:

            url = (
                f"https://{host}"
                f"/v8/finance/chart/"
                f"{encoded_symbol}"
            )

            response = requests.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=20,
            )

            response.raise_for_status()

            payload = (
                response.json()
            )

            chart = payload.get(
                "chart",
                {},
            )

            if chart.get(
                "error"
            ):
                raise RuntimeError(
                    chart["error"]
                )

            results = (
                chart.get(
                    "result"
                )
                or []
            )

            if not results:
                raise ValueError(
                    "Yahoo chart result empty"
                )

            return (
                results[0],
                host,
            )

        except Exception as exc:

            last_error = exc

            print(
                f"  ⚠ {symbol} "
                f"{host}: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

    if last_error:

        print(
            f"  ❌ {symbol}: "
            "Yahoo chart 全部失敗"
        )

    return (
        None,
        None,
    )


# ============================================================
# YAHOO DAILY
# ============================================================

def yahoo_daily_get(
    symbol,
    target_date_str,
):
    params = {
        "interval": "1d",
        "range": "1mo",
        "includePrePost": "false",
        "events": "div,splits",
    }

    result, host = (
        yahoo_chart_request(
            symbol,
            params,
        )
    )

    if result is None:
        return None

    timestamps = (
        result.get(
            "timestamp"
        )
        or []
    )

    quote_list = (
        result.get(
            "indicators",
            {},
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

        print(
            f"  ⚠ {symbol}: "
            "Yahoo daily "
            "timestamp/quote empty"
        )

        return None

    closes = (
        quote_list[0]
        .get(
            "close"
        )
        or []
    )

    timezone_name = (
        result.get(
            "meta",
            {},
        )
        .get(
            "exchangeTimezoneName"
        )
    )

    rows = []

    for (
        timestamp,
        close,
    ) in zip(
        timestamps,
        closes,
    ):

        if not is_valid_number(
            close
        ):
            continue

        trade_date = (
            unix_to_market_date(
                timestamp,
                timezone_name,
            )
        )

        if (
            trade_date
            <= target_date_str
        ):

            rows.append(
                (
                    trade_date,
                    float(close),
                )
            )

    if not rows:

        print(
            f"  ⚠ {symbol}: "
            "Yahoo daily 無可用資料"
        )

        return None

    rows.sort(
        key=lambda x: x[0],
        reverse=True,
    )

    curr_date, curr_val = (
        rows[0]
    )

    prev_val = (
        rows[1][1]
        if len(rows) > 1
        else None
    )

    # 日線若仍是舊日期，
    # 不接受，改走 intraday
    if (
        curr_date
        != target_date_str
    ):

        print(
            f"  ⚠ {symbol} "
            "daily 尚未同步："
            f"latest={curr_date}, "
            f"target={target_date_str}"
        )

        return None

    entry = make_entry(
        curr_val,
        prev_val,
        curr_date,
        source=(
            f"yahoo_daily:"
            f"{host}"
        ),
    )

    print(
        f"  ✅ {symbol} "
        "(Yahoo daily): "
        f"{entry['value']} "
        f"({entry['date']})"
    )

    return entry


# ============================================================
# PREVIOUS CLOSE
# ============================================================

def yahoo_previous_close(
    symbol,
    target_date_str,
):
    params = {
        "interval": "1d",
        "range": "1mo",
        "includePrePost": "false",
    }

    result, _ = (
        yahoo_chart_request(
            symbol,
            params,
        )
    )

    if result is None:
        return None

    timestamps = (
        result.get(
            "timestamp"
        )
        or []
    )

    quote_list = (
        result.get(
            "indicators",
            {},
        )
        .get(
            "quote"
        )
        or []
    )

    if not quote_list:
        return None

    closes = (
        quote_list[0]
        .get(
            "close"
        )
        or []
    )

    timezone_name = (
        result.get(
            "meta",
            {},
        )
        .get(
            "exchangeTimezoneName"
        )
    )

    rows = []

    for (
        timestamp,
        close,
    ) in zip(
        timestamps,
        closes,
    ):

        if not is_valid_number(
            close
        ):
            continue

        trade_date = (
            unix_to_market_date(
                timestamp,
                timezone_name,
            )
        )

        if (
            trade_date
            < target_date_str
        ):

            rows.append(
                (
                    trade_date,
                    float(close),
                )
            )

    if not rows:
        return None

    rows.sort(
        key=lambda x: x[0],
        reverse=True,
    )

    return rows[0][1]


# ============================================================
# YAHOO INTRADAY 5M
# ============================================================

def yahoo_intraday_get(
    symbol,
    target_date_str,
):
    target_date = (
        datetime.strptime(
            target_date_str,
            "%Y-%m-%d",
        )
        .date()
    )

    ny_tz = ZoneInfo(
        "America/New_York"
    )

    start_dt = (
        datetime.combine(
            target_date,
            dt_time(
                0,
                0,
            ),
            tzinfo=ny_tz,
        )
    )

    end_dt = (
        start_dt
        + timedelta(
            days=1
        )
    )

    params = {
        "period1": int(
            start_dt.timestamp()
        ),
        "period2": int(
            end_dt.timestamp()
        ),
        "interval": "5m",
        "includePrePost": "false",
        "events": "div,splits",
    }

    result, host = (
        yahoo_chart_request(
            symbol,
            params,
        )
    )

    if result is None:
        return None

    timestamps = (
        result.get(
            "timestamp"
        )
        or []
    )

    quote_list = (
        result.get(
            "indicators",
            {},
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

        print(
            f"  ⚠ {symbol}: "
            "Yahoo intraday "
            "timestamp/quote empty"
        )

        return None

    closes = (
        quote_list[0]
        .get(
            "close"
        )
        or []
    )

    timezone_name = (
        result.get(
            "meta",
            {},
        )
        .get(
            "exchangeTimezoneName"
        )
        or "America/New_York"
    )

    try:

        market_tz = ZoneInfo(
            timezone_name
        )

    except Exception:

        market_tz = (
            ny_tz
        )

    rows = []

    for (
        timestamp,
        close,
    ) in zip(
        timestamps,
        closes,
    ):

        if not is_valid_number(
            close
        ):
            continue

        dt_local = (
            datetime.fromtimestamp(
                timestamp,
                market_tz,
            )
        )

        trade_date = (
            dt_local.strftime(
                "%Y-%m-%d"
            )
        )

        if (
            trade_date
            != target_date_str
        ):
            continue

        local_time = (
            dt_local.time()
        )

        # 美國 regular session
        # 約 09:30 - 16:00 ET
        if (
            local_time
            < dt_time(
                9,
                30,
            )
            or local_time
            > dt_time(
                16,
                5,
            )
        ):
            continue

        rows.append(
            (
                dt_local,
                float(close),
            )
        )

    if not rows:

        print(
            f"  ⚠ {symbol}: "
            "TARGET intraday 無資料"
        )

        return None

    rows.sort(
        key=lambda x: x[0]
    )

    curr_dt, curr_val = (
        rows[-1]
    )

    prev_val = (
        yahoo_previous_close(
            symbol,
            target_date_str,
        )
    )

    entry = make_entry(
        curr_val,
        prev_val,
        target_date_str,
        source=(
            f"yahoo_intraday_5m:"
            f"{host}"
        ),
    )

    if entry is None:
        return None

    print(
        f"  ✅ {symbol} "
        "(Yahoo intraday 5m): "
        f"{entry['value']} "
        f"({entry['date']}) "
        f"last_bar="
        f"{curr_dt.strftime('%H:%M')}"
    )

    return entry


# ============================================================
# YFINANCE HISTORY
# ============================================================

def yfinance_history_get(
    symbol,
    target_date_str,
):
    target_dt = (
        datetime.strptime(
            target_date_str,
            "%Y-%m-%d",
        )
    )

    start = (
        target_dt
        - timedelta(
            days=14
        )
    ).strftime(
        "%Y-%m-%d"
    )

    end = (
        target_dt
        + timedelta(
            days=2
        )
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
            .strftime(
                "%Y-%m-%d"
            )
        )

        valid = (
            hist[
                hist.index
                <= target_date_str
            ]
            .sort_index(
                ascending=False
            )
        )

        if valid.empty:

            raise ValueError(
                "no valid rows"
            )

        curr_date = (
            valid.index[0]
        )

        if (
            curr_date
            != target_date_str
        ):

            raise ValueError(
                f"stale date "
                f"{curr_date}"
            )

        curr_val = (
            valid.iloc[0][
                "Close"
            ]
        )

        prev_val = (
            valid.iloc[1][
                "Close"
            ]
            if len(valid) > 1
            else None
        )

        entry = make_entry(
            curr_val,
            prev_val,
            curr_date,
            source=(
                "yfinance_history"
            ),
        )

        if entry is None:

            raise ValueError(
                "invalid current value"
            )

        print(
            f"  ✅ {symbol} "
            "(yfinance history): "
            f"{entry['value']} "
            f"({entry['date']})"
        )

        return entry

    except Exception as exc:

        print(
            f"  ⚠ {symbol} "
            "yfinance history: "
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        return None


# ============================================================
# YF.DOWNLOAD
# ============================================================

def yf_download_get(
    symbol,
    target_date_str,
):
    target_dt = (
        datetime.strptime(
            target_date_str,
            "%Y-%m-%d",
        )
    )

    start = (
        target_dt
        - timedelta(
            days=14
        )
    ).strftime(
        "%Y-%m-%d"
    )

    end = (
        target_dt
        + timedelta(
            days=2
        )
    ).strftime(
        "%Y-%m-%d"
    )

    try:

        hist = yf.download(
            symbol,
            start=start,
            end=end,
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=False,
        )

        if hist.empty:

            raise ValueError(
                "empty"
            )

        if isinstance(
            hist.columns,
            pd.MultiIndex,
        ):

            hist.columns = (
                hist.columns
                .get_level_values(
                    0
                )
            )

        hist.index = (
            hist.index
            .strftime(
                "%Y-%m-%d"
            )
        )

        valid = (
            hist[
                hist.index
                <= target_date_str
            ]
            .sort_index(
                ascending=False
            )
        )

        if valid.empty:

            raise ValueError(
                "no valid rows"
            )

        curr_date = (
            valid.index[0]
        )

        if (
            curr_date
            != target_date_str
        ):

            raise ValueError(
                f"stale date "
                f"{curr_date}"
            )

        curr_val = (
            valid.iloc[0][
                "Close"
            ]
        )

        prev_val = (
            valid.iloc[1][
                "Close"
            ]
            if len(valid) > 1
            else None
        )

        entry = make_entry(
            curr_val,
            prev_val,
            curr_date,
            source=(
                "yf_download"
            ),
        )

        if entry is None:

            raise ValueError(
                "invalid current value"
            )

        print(
            f"  ✅ {symbol} "
            "(yf.download): "
            f"{entry['value']} "
            f"({entry['date']})"
        )

        return entry

    except Exception as exc:

        print(
            f"  ❌ {symbol} "
            "yf.download: "
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        return None


# ============================================================
# MARKET GET
# ============================================================

def market_get(
    symbol,
    target_date_str,
):
    # 1. Yahoo daily
    result = (
        yahoo_daily_get(
            symbol,
            target_date_str,
        )
    )

    if result:
        return result

    # 2. Yahoo intraday 5m
    print(
        f"  ↪ {symbol}: "
        "改抓 Yahoo intraday 5m..."
    )

    result = (
        yahoo_intraday_get(
            symbol,
            target_date_str,
        )
    )

    if result:
        return result

    # 3. yfinance history
    print(
        f"  ↪ {symbol}: "
        "改抓 yfinance history..."
    )

    result = (
        yfinance_history_get(
            symbol,
            target_date_str,
        )
    )

    if result:
        return result

    # 4. yf.download
    print(
        f"  ↪ {symbol}: "
        "改抓 yf.download..."
    )

    return (
        yf_download_get(
            symbol,
            target_date_str,
        )
    )


# ============================================================
# TREASURY
# ============================================================

def treasury_get(
    target_date_str,
):
    target_dt = (
        datetime.strptime(
            target_date_str,
            "%Y-%m-%d",
        )
    )

    months = {
        target_dt.strftime(
            "%Y%m"
        ),
        (
            target_dt
            - timedelta(
                days=31
            )
        ).strftime(
            "%Y%m"
        ),
    }

    D = (
        "http://schemas.microsoft.com/"
        "ado/2007/08/dataservices"
    )

    M = (
        "http://schemas.microsoft.com/"
        "ado/2007/08/dataservices/metadata"
    )

    rows = []

    for ym in sorted(
        months,
        reverse=True,
    ):

        url = (
            "https://home.treasury.gov/"
            "resource-center/"
            "data-chart-center/"
            "interest-rates/pages/xml"
            "?data=daily_treasury_yield_curve"
            f"&field_tdr_date_value_month={ym}"
        )

        try:

            response = requests.get(
                url,
                timeout=20,
                headers=HEADERS,
            )

            response.raise_for_status()

            root = (
                ET.fromstring(
                    response.content
                )
            )

            count = 0

            for props in root.iter(
                f"{{{M}}}properties"
            ):

                date_el = props.find(
                    f"{{{D}}}NEW_DATE"
                )

                y2_el = props.find(
                    f"{{{D}}}BC_2YEAR"
                )

                y10_el = props.find(
                    f"{{{D}}}BC_10YEAR"
                )

                y30_el = props.find(
                    f"{{{D}}}BC_30YEAR"
                )

                if (
                    date_el is None
                    or not date_el.text
                ):
                    continue

                trade_date = (
                    date_el.text[
                        :10
                    ]
                )

                if (
                    trade_date
                    > target_date_str
                ):
                    continue

                y2 = (
                    float(
                        y2_el.text
                    )
                    if (
                        y2_el
                        is not None
                        and y2_el.text
                    )
                    else None
                )

                y10 = (
                    float(
                        y10_el.text
                    )
                    if (
                        y10_el
                        is not None
                        and y10_el.text
                    )
                    else None
                )

                y30 = (
                    float(
                        y30_el.text
                    )
                    if (
                        y30_el
                        is not None
                        and y30_el.text
                    )
                    else None
                )

                if all(
                    is_valid_number(
                        x
                    )
                    for x in (
                        y2,
                        y10,
                        y30,
                    )
                ):

                    rows.append(
                        (
                            trade_date,
                            y2,
                            y10,
                            y30,
                        )
                    )

                    count += 1

            print(
                f"  📥 Treasury.gov "
                f"{ym}: "
                f"{count} 筆"
            )

        except Exception as exc:

            print(
                f"  ❌ Treasury.gov "
                f"{ym}: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

    if not rows:

        return (
            None,
            None,
            None,
        )

    rows.sort(
        key=lambda x: x[0],
        reverse=True,
    )

    curr = rows[0]

    prev = (
        rows[1]
        if len(rows) > 1
        else None
    )

    print(
        f"  ✅ Treasury "
        f"curr={curr[0]} "
        f"2Y={curr[1]} "
        f"10Y={curr[2]} "
        f"30Y={curr[3]}"
    )

    def build(
        idx,
    ):

        return make_entry(
            curr[idx],
            (
                prev[idx]
                if prev
                else None
            ),
            curr[0],
            source="treasury_gov",
        )

    return (
        build(1),
        build(2),
        build(3),
    )


# ============================================================
# FETCH ALL
# ============================================================

def fetch_all(
    target,
):
    data = {
        "generated_at": (
            datetime.now(
                ZoneInfo("UTC")
            )
            .isoformat()
        ),
        "target_date": target,
    }

    print(
        "\n📡 VIX..."
    )

    data["vix"] = (
        market_get(
            "^VIX",
            target,
        )
    )

    print(
        "\n📡 MOVE..."
    )

    data["move"] = (
        market_get(
            "^MOVE",
            target,
        )
    )

    print(
        "\n📡 "
        "2Y/10Y/30Y "
        "公債（Treasury.gov）..."
    )

    y2, y10, y30 = (
        treasury_get(
            target
        )
    )

    data["y2"] = y2
    data["y10"] = y10
    data["y30"] = y30

    if (
        y10
        and y2
    ):

        data["spread"] = round(
            (
                y10["value"]
                - y2["value"]
            )
            * 100,
            2,
        )

    else:

        data["spread"] = None

    print(
        "\n📡 SOX..."
    )

    data["sox"] = (
        market_get(
            "^SOX",
            target,
        )
    )

    stocks_meta = {
        "NVDA": {
            "name": "輝達",
            "emoji": "🟢",
            "grade": "IG1",
        },
        "TSM": {
            "name": "台積電",
            "emoji": "🔵",
            "grade": "IG1",
        },
        "SMCI": {
            "name": "超微電腦",
            "emoji": "⚡",
            "grade": "HY1",
        },
        "ARM": {
            "name": "安謀控股",
            "emoji": "💻",
            "grade": "IG1",
        },
        "TSLA": {
            "name": "特斯拉",
            "emoji": "🚗",
            "grade": "IG3",
        },
    }

    data["stocks"] = {}

    for (
        symbol,
        meta,
    ) in stocks_meta.items():

        print(
            f"\n📡 {symbol}..."
        )

        item = (
            market_get(
                symbol,
                target,
            )
        )

        if item:
            item.update(
                meta
            )

        data[
            "stocks"
        ][
            symbol
        ] = item

        time.sleep(
            0.5
        )

    return data


# ============================================================
# VALIDATION
# ============================================================

def validate_data(
    data,
    target,
):
    checks = {
        "VIX": data.get(
            "vix"
        ),
        "MOVE": data.get(
            "move"
        ),
        "SOX": data.get(
            "sox"
        ),
        "2Y": data.get(
            "y2"
        ),
        "10Y": data.get(
            "y10"
        ),
        "30Y": data.get(
            "y30"
        ),
    }

    for (
        symbol,
        item,
    ) in (
        data.get(
            "stocks",
            {},
        )
        .items()
    ):

        checks[
            symbol
        ] = item

    failed = []

    print(
        "\n"
        "======================================"
    )

    print(
        "🔎 資料完整性驗證"
    )

    print(
        "======================================"
    )

    for (
        name,
        item,
    ) in checks.items():

        if item is None:

            failed.append(
                f"{name}: 無資料"
            )

            print(
                f"❌ {name}: 無資料"
            )

            continue

        actual_date = (
            item.get(
                "date"
            )
        )

        value = (
            item.get(
                "value"
            )
        )

        source = (
            item.get(
                "source",
                "-",
            )
        )

        if (
            actual_date
            != target
        ):

            failed.append(
                f"{name}: "
                f"expected={target}, "
                f"actual={actual_date}"
            )

            print(
                f"❌ {name}: "
                f"{actual_date} "
                f"(應為 {target}) "
                f"[{source}]"
            )

            continue

        if not is_valid_number(
            value
        ):

            failed.append(
                f"{name}: "
                f"invalid value={value}"
            )

            print(
                f"❌ {name}: "
                f"value={value} "
                f"[{source}]"
            )

            continue

        print(
            f"✅ {name}: "
            f"{actual_date}, "
            f"value={value} "
            f"[{source}]"
        )

    if failed:

        print(
            "\n⚠️ "
            "市場資料未完整到達 TARGET"
        )

        print(
            "本次不寫入任何 JSON。"
        )

        for msg in failed:

            print(
                "  -",
                msg,
            )

        return False

    print(
        "\n✅ "
        "所有市場資料日期與數值均正確"
    )

    return True


# ============================================================
# WRITE JSON
# ============================================================

def write_data(
    data,
    target,
):
    output_path = (
        DATA_DIR
        / f"market_{target}.json"
    )

    with open(
        output_path,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )

    print(
        f"\n✅ 已寫入 "
        f"{output_path}"
    )

    latest_path = (
        DATA_DIR
        / "latest.json"
    )

    should_update = True

    existing_latest_date = None

    if latest_path.exists():

        try:

            with open(
                latest_path,
                "r",
                encoding="utf-8",
            ) as f:

                existing = (
                    json.load(
                        f
                    )
                )

            existing_latest_date = (
                existing.get(
                    "target_date"
                )
            )

            if existing_latest_date:

                current_date = (
                    datetime.strptime(
                        target,
                        "%Y-%m-%d",
                    )
                    .date()
                )

                old_date = (
                    datetime.strptime(
                        existing_latest_date,
                        "%Y-%m-%d",
                    )
                    .date()
                )

                if (
                    current_date
                    < old_date
                ):

                    should_update = (
                        False
                    )

        except Exception as exc:

            print(
                "⚠️ latest.json "
                "讀取失敗："
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            should_update = True

    if should_update:

        with open(
            latest_path,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )

        print(
            f"✅ 已更新 "
            f"{latest_path}"
        )

    else:

        print(
            f"ℹ️ 本次 TARGET="
            f"{target} "
            f"< latest="
            f"{existing_latest_date}，"
            "不更新 latest.json"
        )


# ============================================================
# SUMMARY
# ============================================================

def print_summary(
    data,
):
    print(
        "\n📊 數據摘要"
    )

    if data.get(
        "vix"
    ):

        print(
            f"  VIX: "
            f"{data['vix']['value']:.2f}"
        )

    if (
        data.get(
            "spread"
        )
        is not None
    ):

        print(
            f"  10Y-2Y: "
            f"{data['spread']} bps"
        )

    for (
        symbol,
        item,
    ) in (
        data.get(
            "stocks",
            {},
        )
        .items()
    ):

        if not item:
            continue

        chg_pct = (
            item.get(
                "chg_pct"
            )
        )

        source = (
            item.get(
                "source",
                "-",
            )
        )

        if is_valid_number(
            chg_pct
        ):

            print(
                f"  {symbol}: "
                f"${item['value']:.2f} "
                f"({chg_pct:+.2f}%) "
                f"[{source}]"
            )

        else:

            print(
                f"  {symbol}: "
                f"${item['value']:.2f} "
                f"[{source}]"
            )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    data = fetch_all(
        TARGET
    )

    if not validate_data(
        data,
        TARGET,
    ):

        print(
            "\n❌ DATA_NOT_READY"
        )

        print(
            "TARGET 資料尚未完整取得，"
            "本次
