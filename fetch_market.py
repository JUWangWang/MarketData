#!/usr/bin/env python3
"""
Market data fetcher for GitHub Actions.

Rules:
- Every item must match TARGET exactly.
- Reject None / NaN / inf.
- Do not write JSON unless all required items pass validation.
- Historical backfill must not rewind latest.json.

Fetch order for Yahoo symbols:
1) Yahoo daily chart API
2) Yahoo intraday 5m chart API
3) yfinance Ticker.history
4) yf.download

Treasury yields:
- Treasury.gov official XML
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

YAHOO_HOSTS = (
    "query2.finance.yahoo.com",
    "query1.finance.yahoo.com",
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://finance.yahoo.com/",
}

STOCKS_META = {
    "NVDA": {"name": "輝達", "emoji": "🟢", "grade": "IG1"},
    "TSM": {"name": "台積電", "emoji": "🔵", "grade": "IG1"},
    "SMCI": {"name": "超微電腦", "emoji": "⚡", "grade": "HY1"},
    "ARM": {"name": "安謀控股", "emoji": "💻", "grade": "IG1"},
    "TSLA": {"name": "特斯拉", "emoji": "🚗", "grade": "IG3"},
}


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

    return schedule.index[-1].date().strftime("%Y-%m-%d")


TARGET = get_target_date()


def is_valid_number(value):
    if value is None:
        return False

    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def make_entry(curr_val, prev_val, curr_date, source):
    if not is_valid_number(curr_val):
        return None

    curr_val = float(curr_val)

    prev_val = (
        float(prev_val)
        if is_valid_number(prev_val)
        else None
    )

    chg_abs = None
    chg_pct = None

    if prev_val is not None:
        chg_abs = round(curr_val - prev_val, 6)

        if prev_val != 0:
            chg_pct = round(
                (curr_val - prev_val)
                / prev_val
                * 100,
                4,
            )

    return {
        "value": round(curr_val, 4),
        "prev": (
            round(prev_val, 4)
            if prev_val is not None
            else None
        ),
        "chg_abs": chg_abs,
        "chg_pct": chg_pct,
        "date": curr_date,
        "source": source,
    }


def market_date_from_timestamp(timestamp, timezone_name):
    tz = ZoneInfo(timezone_name or "UTC")

    return datetime.fromtimestamp(
        timestamp,
        tz,
    ).strftime("%Y-%m-%d")


def yahoo_chart_request(symbol, params):
    encoded = quote(symbol, safe="")

    for host in YAHOO_HOSTS:
        try:
            url = (
                f"https://{host}"
                f"/v8/finance/chart/{encoded}"
            )

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
                {},
            )

            if chart.get("error"):
                raise RuntimeError(
                    str(chart["error"])
                )

            results = (
                chart.get("result")
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
            print(
                f"  ⚠ {symbol} {host}: "
                f"{type(exc).__name__}: {exc}"
            )

    return (
        None,
        None,
    )


def yahoo_daily_get(symbol, target_date):
    result, host = yahoo_chart_request(
        symbol,
        {
            "interval": "1d",
            "range": "1mo",
            "includePrePost": "false",
            "events": "div,splits",
        },
    )

    if result is None:
        return None

    timestamps = (
        result.get("timestamp")
        or []
    )

    quotes = (
        result
        .get("indicators", {})
        .get("quote")
        or []
    )

    if not timestamps or not quotes:
        return None

    closes = (
        quotes[0]
        .get("close")
        or []
    )

    tz_name = (
        result
        .get("meta", {})
        .get("exchangeTimezoneName")
    )

    rows = []

    for ts, close in zip(
        timestamps,
        closes,
    ):
        if not is_valid_number(close):
            continue

        trade_date = (
            market_date_from_timestamp(
                ts,
                tz_name,
            )
        )

        if trade_date <= target_date:
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

    curr_date, curr_val = rows[0]

    prev_val = (
        rows[1][1]
        if len(rows) > 1
        else None
    )

    if curr_date != target_date:
        print(
            f"  ⚠ {symbol} daily stale: "
            f"latest={curr_date}, "
            f"target={target_date}"
        )

        return None

    entry = make_entry(
        curr_val,
        prev_val,
        curr_date,
        f"yahoo_daily:{host}",
    )

    print(
        f"  ✅ {symbol} Yahoo daily: "
        f"{entry['value']} "
        f"({entry['date']})"
    )

    return entry


def yahoo_previous_close(
    symbol,
    target_date,
):
    result, _ = yahoo_chart_request(
        symbol,
        {
            "interval": "1d",
            "range": "1mo",
            "includePrePost": "false",
        },
    )

    if result is None:
        return None

    timestamps = (
        result.get("timestamp")
        or []
    )

    quotes = (
        result
        .get("indicators", {})
        .get("quote")
        or []
    )

    if not quotes:
        return None

    closes = (
        quotes[0]
        .get("close")
        or []
    )

    tz_name = (
        result
        .get("meta", {})
        .get("exchangeTimezoneName")
    )

    rows = []

    for ts, close in zip(
        timestamps,
        closes,
    ):
        if not is_valid_number(close):
            continue

        trade_date = (
            market_date_from_timestamp(
                ts,
                tz_name,
            )
        )

        if trade_date < target_date:
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


def yahoo_intraday_get(
    symbol,
    target_date,
):
    target = datetime.strptime(
        target_date,
        "%Y-%m-%d",
    ).date()

    ny_tz = ZoneInfo(
        "America/New_York"
    )

    start_dt = datetime.combine(
        target,
        dt_time(0, 0),
        tzinfo=ny_tz,
    )

    end_dt = (
        start_dt
        + timedelta(days=1)
    )

    result, host = yahoo_chart_request(
        symbol,
        {
            "period1": int(
                start_dt.timestamp()
            ),
            "period2": int(
                end_dt.timestamp()
            ),
            "interval": "5m",
            "includePrePost": "false",
            "events": "div,splits",
        },
    )

    if result is None:
        return None

    timestamps = (
        result.get("timestamp")
        or []
    )

    quotes = (
        result
        .get("indicators", {})
        .get("quote")
        or []
    )

    if not timestamps or not quotes:
        return None

    closes = (
        quotes[0]
        .get("close")
        or []
    )

    tz_name = (
        result
        .get("meta", {})
        .get("exchangeTimezoneName")
        or "America/New_York"
    )

    market_tz = ZoneInfo(
        tz_name
    )

    rows = []

    for ts, close in zip(
        timestamps,
        closes,
    ):
        if not is_valid_number(close):
            continue

        local_dt = (
            datetime.fromtimestamp(
                ts,
                market_tz,
            )
        )

        if (
            local_dt.strftime("%Y-%m-%d")
            != target_date
        ):
            continue

        local_time = (
            local_dt.time()
        )

        if (
            local_time < dt_time(9, 30)
            or
            local_time > dt_time(16, 5)
        ):
            continue

        rows.append(
            (
                local_dt,
                float(close),
            )
        )

    if not rows:
        print(
            f"  ⚠ {symbol} "
            "intraday TARGET 無資料"
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
            target_date,
        )
    )

    entry = make_entry(
        curr_val,
        prev_val,
        target_date,
        f"yahoo_intraday_5m:{host}",
    )

    print(
        f"  ✅ {symbol} Yahoo intraday: "
        f"{entry['value']} "
        f"({entry['date']}) "
        f"last_bar="
        f"{curr_dt.strftime('%H:%M')}"
    )

    return entry


def yfinance_history_get(
    symbol,
    target_date,
):
    target_dt = (
        datetime.strptime(
            target_date,
            "%Y-%m-%d",
        )
    )

    start = (
        target_dt
        - timedelta(days=14)
    ).strftime("%Y-%m-%d")

    end = (
        target_dt
        + timedelta(days=2)
    ).strftime("%Y-%m-%d")

    try:
        hist = (
            yf.Ticker(symbol)
            .history(
                start=start,
                end=end,
                interval="1d",
                auto_adjust=True,
            )
        )

        if hist.empty:
            raise ValueError("empty")

        hist.index = (
            hist.index
            .strftime("%Y-%m-%d")
        )

        valid = (
            hist[
                hist.index
                <= target_date
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

        if curr_date != target_date:
            raise ValueError(
                f"stale date {curr_date}"
            )

        curr_val = (
            valid.iloc[0]["Close"]
        )

        prev_val = (
            valid.iloc[1]["Close"]
            if len(valid) > 1
            else None
        )

        entry = make_entry(
            curr_val,
            prev_val,
            curr_date,
            "yfinance_history",
        )

        if entry is None:
            raise ValueError(
                "invalid current value"
            )

        print(
            f"  ✅ {symbol} "
            "yfinance history: "
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


def yf_download_get(
    symbol,
    target_date,
):
    target_dt = (
        datetime.strptime(
            target_date,
            "%Y-%m-%d",
        )
    )

    start = (
        target_dt
        - timedelta(days=14)
    ).strftime("%Y-%m-%d")

    end = (
        target_dt
        + timedelta(days=2)
    ).strftime("%Y-%m-%d")

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
            raise ValueError("empty")

        if isinstance(
            hist.columns,
            pd.MultiIndex,
        ):
            hist.columns = (
                hist.columns
                .get_level_values(0)
            )

        hist.index = (
            hist.index
            .strftime("%Y-%m-%d")
        )

        valid = (
            hist[
                hist.index
                <= target_date
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

        if curr_date != target_date:
            raise ValueError(
                f"stale date {curr_date}"
            )

        curr_val = (
            valid.iloc[0]["Close"]
        )

        prev_val = (
            valid.iloc[1]["Close"]
            if len(valid) > 1
            else None
        )

        entry = make_entry(
            curr_val,
            prev_val,
            curr_date,
            "yf_download",
        )

        if entry is None:
            raise ValueError(
                "invalid current value"
            )

        print(
            f"  ✅ {symbol} "
            "yf.download: "
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


def market_get(
    symbol,
    target_date,
):
    result = (
        yahoo_daily_get(
            symbol,
            target_date,
        )
    )

    if result:
        return result

    print(
        f"  ↪ {symbol}: "
        "fallback → Yahoo intraday 5m"
    )

    result = (
        yahoo_intraday_get(
            symbol,
            target_date,
        )
    )

    if result:
        return result

    print(
        f"  ↪ {symbol}: "
        "fallback → yfinance history"
    )

    result = (
        yfinance_history_get(
            symbol,
            target_date,
        )
    )

    if result:
        return result

    print(
        f"  ↪ {symbol}: "
        "fallback → yf.download"
    )

    return yf_download_get(
        symbol,
        target_date,
    )


def treasury_get(
    target_date,
):
    target_dt = (
        datetime.strptime(
            target_date,
            "%Y-%m-%d",
        )
    )

    months = {
        target_dt.strftime("%Y%m"),
        (
            target_dt
            - timedelta(days=31)
        ).strftime("%Y%m"),
    }

    d_ns = (
        "http://schemas.microsoft.com/"
        "ado/2007/08/dataservices"
    )

    m_ns = (
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
                headers=HEADERS,
                timeout=20,
            )

            response.raise_for_status()

            root = ET.fromstring(
                response.content
            )

            count = 0

            for props in root.iter(
                f"{{{m_ns}}}properties"
            ):
                date_el = props.find(
                    f"{{{d_ns}}}NEW_DATE"
                )

                y2_el = props.find(
                    f"{{{d_ns}}}BC_2YEAR"
                )

                y10_el = props.find(
                    f"{{{d_ns}}}BC_10YEAR"
                )

                y30_el = props.find(
                    f"{{{d_ns}}}BC_30YEAR"
                )

                if (
                    date_el is None
                    or
                    not date_el.text
                ):
                    continue

                trade_date = (
                    date_el.text[:10]
                )

                if (
                    trade_date
                    > target_date
                ):
                    continue

                y2 = (
                    float(y2_el.text)
                    if (
                        y2_el is not None
                        and y2_el.text
                    )
                    else None
                )

                y10 = (
                    float(y10_el.text)
                    if (
                        y10_el is not None
                        and y10_el.text
                    )
                    else None
                )

                y30 = (
                    float(y30_el.text)
                    if (
                        y30_el is not None
                        and y30_el.text
                    )
                    else None
                )

                if all(
                    is_valid_number(v)
                    for v in (
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
                f"{ym}: {count} 筆"
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

    def build(idx):
        return make_entry(
            curr[idx],
            (
                prev[idx]
                if prev
                else None
            ),
            curr[0],
            "treasury_gov",
        )

    return (
        build(1),
        build(2),
        build(3),
    )


def fetch_all(
    target_date,
):
    data = {
        "generated_at": (
            datetime.now(
                ZoneInfo("UTC")
            ).isoformat()
        ),
        "target_date": target_date,
    }

    print("\n📡 VIX")
    data["vix"] = market_get(
        "^VIX",
        target_date,
    )

    print("\n📡 MOVE")
    data["move"] = market_get(
        "^MOVE",
        target_date,
    )

    print(
        "\n📡 Treasury "
        "2Y / 10Y / 30Y"
    )

    y2, y10, y30 = (
        treasury_get(
            target_date
        )
    )

    data["y2"] = y2
    data["y10"] = y10
    data["y30"] = y30

    data["spread"] = (
        round(
            (
                y10["value"]
                - y2["value"]
            )
            * 100,
            2,
        )
        if y2 and y10
        else None
    )

    print("\n📡 SOX")
    data["sox"] = market_get(
        "^SOX",
        target_date,
    )

    data["stocks"] = {}

    for symbol, meta in (
        STOCKS_META.items()
    ):
        print(
            f"\n📡 {symbol}"
        )

        item = market_get(
            symbol,
            target_date,
        )

        if item:
            item.update(meta)

        data["stocks"][
            symbol
        ] = item

        time.sleep(0.5)

    return data


def validate_data(
    data,
    target_date,
):
    checks = {
        "VIX": data.get("vix"),
        "MOVE": data.get("move"),
        "SOX": data.get("sox"),
        "2Y": data.get("y2"),
        "10Y": data.get("y10"),
        "30Y": data.get("y30"),
    }

    checks.update(
        data.get(
            "stocks",
            {},
        )
    )

    failed = []

    print(
        "\n======================================"
    )

    print(
        "🔎 資料完整性驗證"
    )

    print(
        "======================================"
    )

    for name, item in (
        checks.items()
    ):
        if not isinstance(
            item,
            dict,
        ):
            print(
                f"❌ {name}: 無資料"
            )

            failed.append(
                f"{name}: 無資料"
            )

            continue

        actual_date = (
            item.get("date")
        )

        value = (
            item.get("value")
        )

        source = (
            item.get(
                "source",
                "-",
            )
        )

        if (
            actual_date
            != target_date
        ):
            print(
                f"❌ {name}: "
                f"日期 {actual_date} "
                f"(應為 {target_date}) "
                f"[{source}]"
            )

            failed.append(
                f"{name}: "
                f"expected={target_date}, "
                f"actual={actual_date}"
            )

            continue

        if not is_valid_number(
            value
        ):
            print(
                f"❌ {name}: "
                f"invalid value={value} "
                f"[{source}]"
            )

            failed.append(
                f"{name}: "
                f"invalid value={value}"
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
            "\n⚠️ 市場資料未完整到達 TARGET"
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
        "\n✅ "
        "所有市場資料日期與數值均正確"
    )

    return True


def write_data(
    data,
    target_date,
):
    market_path = (
        DATA_DIR
        / f"market_{target_date}.json"
    )

    with market_path.open(
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
        f"{market_path}"
    )

    latest_path = (
        DATA_DIR
        / "latest.json"
    )

    existing_latest_date = None
    should_update_latest = True

    if latest_path.exists():
        try:
            with latest_path.open(
                "r",
                encoding="utf-8",
            ) as f:
                existing = (
                    json.load(f)
                )

            existing_latest_date = (
                existing.get(
                    "target_date"
                )
            )

            if existing_latest_date:
                old_date = (
                    datetime.strptime(
                        existing_latest_date,
                        "%Y-%m-%d",
                    )
                    .date()
                )

                new_date = (
                    datetime.strptime(
                        target_date,
                        "%Y-%m-%d",
                    )
                    .date()
                )

                if new_date < old_date:
                    should_update_latest = False

        except Exception as exc:
            print(
                "⚠️ latest.json "
                "讀取失敗："
                f"{type(exc).__name__}: "
                f"{exc}"
            )

    if should_update_latest:
        with latest_path.open(
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
            f"ℹ️ TARGET={target_date} "
            f"< latest="
            f"{existing_latest_date}，"
            "不更新 latest.json"
        )


def print_summary(data):
    print(
        "\n📊 數據摘要"
    )

    if data.get("vix"):
        print(
            f"  VIX: "
            f"{data['vix']['value']:.2f}"
        )

    if (
        data.get("spread")
        is not None
    ):
        print(
            f"  10Y-2Y: "
            f"{data['spread']} bps"
        )

    for symbol, item in (
        data.get(
            "stocks",
            {},
        )
        .items()
    ):
        if not item:
            continue

        chg_pct = (
            item.get("chg_pct")
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


if __name__ == "__main__":
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

        sys.exit(2)

    write_data(
        data,
        TARGET,
    )

    print_summary(
        data
    )
