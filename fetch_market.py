#!/usr/bin/env python3
"""
金融市場每日監控指標 — 資料抓取腳本
GitHub Actions 每日自動執行

設計原則：
1. 自動執行抓台灣時間今天以前最近一個 NYSE 正式交易日。
2. 手動補抓可指定 YYYY-MM-DD。
3. Yahoo chart API 為股票 / 指數主要來源。
4. yfinance Ticker.history / yf.download 作為 fallback。
5. 寫檔前嚴格驗證日期與數值。
6. 禁止 NaN / inf 寫入 JSON。
7. 歷史補抓不可讓 latest.json 往回退。
"""

import json
import math
import sys
from datetime import datetime, timedelta
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
        台灣時間今天以前，
        最近一個 NYSE 正式交易日。
    """

    if len(sys.argv) > 1:
        target = sys.argv[1]

        # 驗證格式
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
        )
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
    f"🗓  Target market date: {TARGET}"
)


# ============================================================
# HELPERS
# ============================================================

def is_valid_number(value):
    """
    排除：
    - None
    - NaN
    - inf
    - -inf
    - 非數字
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
    curr_date
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

    if prev_val is not None:

        chg_abs = round(
            curr_val - prev_val,
            6
        )

    else:
        chg_abs = None

    if prev_val not in (
        None,
        0
    ):

        chg_pct = round(
            (
                curr_val
                - prev_val
            )
            / prev_val
            * 100,
            4
        )

    else:
        chg_pct = None

    return {
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


def unix_to_market_date(
    timestamp,
    timezone_name=None
):
    """
    Yahoo timestamp
    轉成交易所當地日期。

    避免單純 UTC 轉換造成
    日期跨日誤判。
    """

    if timezone_name:

        try:
            dt = datetime.fromtimestamp(
                timestamp,
                ZoneInfo(
                    timezone_name
                )
            )

            return dt.strftime(
                "%Y-%m-%d"
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
# YAHOO CHART API
# ============================================================

def yahoo_chart_get(
    symbol,
    target_date_str
):
    """
    Yahoo Finance chart API

    第一順位資料來源：
    - VIX
    - MOVE
    - SOX
    - NVDA
    - TSM
    - SMCI
    - ARM
    - TSLA
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
                timeout=20
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
                    "Yahoo chart result empty"
                )

            result = results[0]

            timestamps = (
                result.get(
                    "timestamp"
                )
                or []
            )

            indicators = (
                result.get(
                    "indicators",
                    {}
                )
            )

            quote_data = (
                indicators.get(
                    "quote"
                )
                or []
            )

            if (
                not timestamps
                or not quote_data
            ):

                raise ValueError(
                    "Yahoo chart timestamp/quote empty"
                )

            closes = (
                quote_data[0]
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
                    "no valid rows "
                    f"<= {target_date_str}"
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

            entry = make_entry(
                curr_val,
                prev_val,
                curr_date
            )

            if entry is None:

                raise ValueError(
                    "invalid current value"
                )

            print(
                f"  ✅ {symbol} "
                f"(Yahoo chart {host}): "
                f"{entry['value']} "
                f"({entry['date']})"
            )

            return entry

        except Exception as e:

            print(
                f"  ⚠ {symbol} "
                f"Yahoo chart {host}: "
                f"{type(e).__name__}: "
                f"{e}"
            )

    return None


# ============================================================
# YFINANCE FALLBACK
# ============================================================

def yfinance_fallback_get(
    symbol,
    target_date_str
):
    """
    fallback 1:
        Ticker.history

    fallback 2:
        yf.download
    """

    d = datetime.strptime(
        target_date_str,
        "%Y-%m-%d"
    )

    start = (
        d
        - timedelta(days=14)
    ).strftime(
        "%Y-%m-%d"
    )

    end = (
        d
        + timedelta(days=2)
    ).strftime(
        "%Y-%m-%d"
    )

    # --------------------------------------------------------
    # fallback 1:
    # Ticker.history
    # --------------------------------------------------------

    try:

        ticker = yf.Ticker(
            symbol
        )

        hist = ticker.history(
            start=start,
            end=end,
            interval="1d",
            auto_adjust=True
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
            curr_date
        )

        if entry is None:

            raise ValueError(
                "invalid current value: "
                f"{curr_val}"
            )

        print(
            f"  ✅ {symbol} "
            "(yfinance history): "
            f"{entry['value']} "
            f"({entry['date']})"
        )

        return entry

    except Exception as e:

        print(
            f"  ⚠ {symbol} "
            "yfinance history: "
            f"{type(e).__name__}: "
            f"{e}"
        )

    # --------------------------------------------------------
    # fallback 2:
    # yf.download
    # --------------------------------------------------------

    try:

        hist = yf.download(
            symbol,
            start=start,
            end=end,
            interval="1d",
            auto_adjust=True,
            progress=False
        )

        if hist.empty:

            raise ValueError(
                "empty"
            )

        if isinstance(
            hist.columns,
            pd.MultiIndex
        ):

            hist.columns = (
                hist.columns
                .get_level_values(0)
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
            curr_date
        )

        if entry is None:

            raise ValueError(
                "invalid current value: "
                f"{curr_val}"
            )

        print(
            f"  ✅ {symbol} "
            "(yf.download): "
            f"{entry['value']} "
            f"({entry['date']})"
        )

        return entry

    except Exception as e:

        print(
            f"  ❌ {symbol} "
            "yfinance fallback: "
            f"{type(e).__name__}: "
            f"{e}"
        )

        return None


# ============================================================
# MARKET GET
# ============================================================

def market_get(
    symbol,
    target_date_str
):
    """
    第一順位：
        Yahoo chart API

    fallback：
        yfinance history
        yf.download
    """

    entry = yahoo_chart_get(
        symbol,
        target_date_str
    )

    if entry:
        return entry

    print(
        f"  ⚠ {symbol}: "
        "Yahoo chart 失敗，"
        "改用 yfinance fallback"
    )

    return yfinance_fallback_get(
        symbol,
        target_date_str
    )


# ============================================================
# TREASURY
# ============================================================

def treasury_get(
    target_date_str
):
    """
    Treasury.gov 官方資料：
    - 2Y
    - 10Y
    - 30Y
    """

    import xml.etree.ElementTree as ET

    d = datetime.strptime(
        target_date_str,
        "%Y-%m-%d"
    )

    months = set()

    for delta in [
        0,
        1,
        2
    ]:

        month_date = (
            d
            - timedelta(
                days=30 * delta
            )
        )

        months.add(
            month_date.strftime(
                "%Y%m"
            )
        )

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
        reverse=True
    ):

        url = (
            "https://home.treasury.gov/"
            "resource-center/data-chart-center/"
            "interest-rates/pages/xml"
            "?data=daily_treasury_yield_curve"
            f"&field_tdr_date_value_month={ym}"
        )

        try:

            response = requests.get(
                url,
                timeout=20,
                headers=HEADERS
            )

            response.raise_for_status()

            root = ET.fromstring(
                response.content
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

                date_only = (
                    date_el.text[:10]
                )

                if (
                    date_only
                    > target_date_str
                ):
                    continue

                y2v = (
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

                y10v = (
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

                y30v = (
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
                    is_valid_number(v)
                    for v in [
                        y2v,
                        y10v,
                        y30v
                    ]
                ):

                    rows.append(
                        (
                            date_only,
                            y2v,
                            y10v,
                            y30v
                        )
                    )

                    count += 1

            print(
                f"  📥 Treasury.gov "
                f"{ym}: {count} 筆"
            )

        except Exception as e:

            print(
                f"  ❌ Treasury.gov "
                f"{ym}: "
                f"{type(e).__name__}: "
                f"{e}"
            )

    if not rows:

        print(
            "  ⚠️ Treasury.gov "
            "無有效資料"
        )

        return (
            None,
            None,
            None
        )

    rows.sort(
        key=lambda x: x[0],
        reverse=True
    )

    curr = rows[0]

    prev = (
        rows[1]
        if len(rows) > 1
        else None
    )

    print(
        f"  ✅ curr={curr[0]} "
        f"2Y={curr[1]} "
        f"10Y={curr[2]} "
        f"30Y={curr[3]}"
    )

    def mk(index):

        return make_entry(
            curr[index],
            (
                prev[index]
                if prev
                else None
            ),
            curr[0]
        )

    return (
        mk(1),
        mk(2),
        mk(3)
    )


# ============================================================
# FETCH ALL
# ============================================================

def fetch_all(
    target
):

    result = {
        "generated_at": (
            datetime.utcnow()
            .isoformat()
            + "Z"
        ),
        "target_date": target,
    }

    # --------------------------------------------------------
    # VIX
    # --------------------------------------------------------

    print(
        "📡 VIX..."
    )

    result[
        "vix"
    ] = market_get(
        "^VIX",
        target
    )

    # --------------------------------------------------------
    # MOVE
    # --------------------------------------------------------

    print(
        "📡 MOVE..."
    )

    result[
        "move"
    ] = market_get(
        "^MOVE",
        target
    )

    # --------------------------------------------------------
    # Treasury
    # --------------------------------------------------------

    print(
        "📡 2Y/10Y/30Y "
        "公債（Treasury.gov）..."
    )

    y2, y10, y30 = treasury_get(
        target
    )

    result[
        "y2"
    ] = y2

    result[
        "y10"
    ] = y10

    result[
        "y30"
    ] = y30

    # --------------------------------------------------------
    # 10Y-2Y spread
    # --------------------------------------------------------

    if (
        y10
        and y2
    ):

        result[
            "spread"
        ] = round(
            (
                y10[
                    "value"
                ]
                - y2[
                    "value"
                ]
            )
            * 100,
            2
        )

    else:

        result[
            "spread"
        ] = None

    # --------------------------------------------------------
    # SOX
    # --------------------------------------------------------

    print(
        "📡 SOX..."
    )

    result[
        "sox"
    ] = market_get(
        "^SOX",
        target
    )

    # --------------------------------------------------------
    # STOCKS
    # --------------------------------------------------------

    stocks_meta = {

        "NVDA": {
            "name": "輝達",
            "emoji": "🟢",
            "grade": "IG1"
        },

        "TSM": {
            "name": "台積電",
            "emoji": "🔵",
            "grade": "IG1"
        },

        "SMCI": {
            "name": "超微電腦",
            "emoji": "⚡",
            "grade": "HY1"
        },

        "ARM": {
            "name": "安謀控股",
            "emoji": "💻",
            "grade": "IG1"
        },

        "TSLA": {
            "name": "特斯拉",
            "emoji": "🚗",
            "grade": "IG3"
        },
    }

    result[
        "stocks"
    ] = {}

    for (
        symbol,
        meta
    ) in stocks_meta.items():

        print(
            f"📡 {symbol}..."
        )

        entry = market_get(
            symbol,
            target
        )

        if entry:

            entry.update(
                meta
            )

        result[
            "stocks"
        ][
            symbol
        ] = entry

    return result


# ============================================================
# VALIDATION
# ============================================================

def validate_data(
    data,
    target
):
    """
    必須：
    1. 有資料
    2. date == TARGET
    3. value 為 finite number
    """

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
        stock
    ) in data.get(
        "stocks",
        {}
    ).items():

        checks[
            symbol
        ] = stock

    failed = []

    print(
        "\n🔎 資料完整性驗證"
    )

    for (
        name,
        item
    ) in checks.items():

        if item is None:

            failed.append(
                f"{name}: 無資料"
            )

            print(
                f"  ❌ {name}: "
                "無資料"
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
                f"  ❌ {name}: "
                f"日期 {actual_date} "
                f"(應為 {target})"
            )

            continue

        if not is_valid_number(
            value
        ):

            failed.append(
                f"{name}: "
                f"value 無效 ({value})"
            )

            print(
                f"  ❌ {name}: "
                f"value={value}"
            )

            continue

        print(
            f"  ✅ {name}: "
            f"{actual_date}, "
            f"value={value}"
        )

    if failed:

        print(
            "\n⚠️ 市場資料尚未全部更新完成"
        )

        print(
            "本次不寫入任何 JSON。"
        )

        for msg in failed:

            print(
                f"  - {msg}"
            )

        return False

    print(
        "\n✅ 所有市場資料日期與數值均正確"
    )

    return True


# ============================================================
# WRITE DATA
# ============================================================

def write_data(
    data,
    target
):

    # --------------------------------------------------------
    # 日期檔
    # --------------------------------------------------------

    out_path = (
        DATA_DIR
        / f"market_{target}.json"
    )

    with open(
        out_path,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
            allow_nan=False
        )

    print(
        f"\n✅ 已寫入 "
        f"{out_path}"
    )

    # --------------------------------------------------------
    # latest.json
    # --------------------------------------------------------

    latest_path = (
        DATA_DIR
        / "latest.json"
    )

    should_update_latest = True

    existing_latest_date = None

    if latest_path.exists():

        try:

            with open(
                latest_path,
                "r",
                encoding="utf-8"
            ) as f:

                existing_latest = (
                    json.load(
                        f
                    )
                )

            existing_latest_date = (
                existing_latest.get(
                    "target_date"
                )
            )

            if existing_latest_date:

                new_date = (
                    datetime.strptime(
                        target,
                        "%Y-%m-%d"
                    )
                    .date()
                )

                old_date = (
                    datetime.strptime(
                        existing_latest_date,
                        "%Y-%m-%d"
                    )
                    .date()
                )

                if (
                    new_date
                    < old_date
                ):

                    should_update_latest = (
                        False
                    )

        except Exception as e:

            print(
                "⚠️ 讀取 "
                "existing latest.json "
                f"失敗：{e}"
            )

            should_update_latest = True

    if should_update_latest:

        with open(
            latest_path,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2,
                allow_nan=False
            )

        print(
            f"✅ 已更新 "
            f"{latest_path}"
        )

    else:

        print(
            f"ℹ️ 歷史補抓 "
            f"TARGET={target}；"
            f"目前 latest="
            f"{existing_latest_date}，"
            "不更新 latest.json"
        )


# ============================================================
# SUMMARY
# ============================================================

def print_summary(
    data
):

    print(
        "\n📊 數據摘要:"
    )

    if data.get(
        "vix"
    ):

        v = (
            data[
                "vix"
            ][
                "value"
            ]
        )

        if v >= 30:

            level = (
                "🚨 恐慌"
            )

        elif v >= 20:

            level = (
                "⚠️ 警戒"
            )

        else:

            level = (
                "✅ 正常"
            )

        print(
            f"  VIX: "
            f"{v:.2f} "
            f"{level}"
        )

    if (
        data.get(
            "y10"
        )
        and data.get(
            "y2"
        )
    ):

        spread = (
            data.get(
                "spread"
            )
        )

        suffix = ""

        if (
            spread
            is not None
            and spread < 0
        ):

            suffix = (
                " 🚨 倒掛！"
            )

        print(
            "  10Y-2Y 利差: "
            f"{spread} bps"
            f"{suffix}"
        )

    for (
        symbol,
        stock
    ) in data.get(
        "stocks",
        {}
    ).items():

        if not stock:
            continue

        chg_pct = (
            stock.get(
                "chg_pct"
            )
        )

        if is_valid_number(
            chg_pct
        ):

            print(
                f"  {symbol}: "
                f"${stock['value']:.2f} "
                f"({chg_pct:+.2f}%)"
            )

        else:

            print(
                f"  {symbol}: "
                f"${stock['value']:.2f}"
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
        TARGET
    ):

        print(
            "\n❌ DATA_NOT_READY"
        )

        print(
            "資料尚未完整到達 "
            "TARGET，"
            "本次執行失敗。"
        )

        sys.exit(
            2
        )

    write_data(
        data,
        TARGET
    )

    print_summary(
        data
    )
