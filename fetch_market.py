#!/usr/bin/env python3
"""
金融市場每日監控指標 — 資料抓取腳本
GitHub Actions 每日自動執行

自動執行：
    抓台灣時間今天以前，最近一個 NYSE 正式交易日

手動補抓：
    python fetch_market.py 2026-09-03
"""

import json
import math
import sys
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal
import yfinance as yf
import pandas as pd
import requests


# ── CONFIG ───────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)


# ── TARGET DATE ──────────────────────────────────────────────
def get_target_date():
    """
    手動執行：
        python fetch_market.py 2026-09-02

    自動執行：
        以台灣時間為基準，
        找今天以前最近一個 NYSE 正式交易日。
    """

    if len(sys.argv) > 1:
        target = sys.argv[1]
        datetime.strptime(target, "%Y-%m-%d")
        return target

    now_tw = datetime.now(ZoneInfo("Asia/Taipei"))

    nyse = mcal.get_calendar("NYSE")

    start_date = now_tw.date() - timedelta(days=14)
    end_date = now_tw.date() - timedelta(days=1)

    schedule = nyse.schedule(
        start_date=start_date,
        end_date=end_date
    )

    if schedule.empty:
        raise RuntimeError("找不到最近的 NYSE 交易日")

    last_trading_day = schedule.index[-1].date()

    return last_trading_day.strftime("%Y-%m-%d")


TARGET = get_target_date()

print(
    f"🕒 Taiwan time: "
    f"{datetime.now(ZoneInfo('Asia/Taipei')).isoformat()}"
)
print(f"🗓  Target market date: {TARGET}")


# ── HELPERS ──────────────────────────────────────────────────
def is_valid_number(value):
    """
    檢查是否為有效有限數值。
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
    except (TypeError, ValueError):
        return False

    return math.isfinite(value)


def make_entry(curr_val, prev_val, curr_date):
    """
    建立統一資料格式。
    curr_val 若不是有效數值，直接視為無資料。
    """

    if not is_valid_number(curr_val):
        return None

    curr_val = float(curr_val)

    if not is_valid_number(prev_val):
        prev_val = None
    else:
        prev_val = float(prev_val)

    chg_abs = (
        round(curr_val - prev_val, 6)
        if prev_val is not None
        else None
    )

    chg_pct = (
        round((curr_val - prev_val) / prev_val * 100, 4)
        if prev_val not in (None, 0)
        else None
    )

    return {
        "value": round(curr_val, 4),
        "prev": round(prev_val, 4) if prev_val is not None else None,
        "chg_abs": chg_abs,
        "chg_pct": chg_pct,
        "date": curr_date,
    }


# ── TREASURY ─────────────────────────────────────────────────
def treasury_get(target_date_str):
    """
    從 Treasury.gov 官方 API 抓 2Y / 10Y / 30Y 殖利率
    """

    import xml.etree.ElementTree as ET

    d = datetime.strptime(target_date_str, "%Y-%m-%d")

    months = set()

    for delta in [0, 1, 2]:
        m = d - timedelta(days=30 * delta)
        months.add(m.strftime("%Y%m"))

    D = "http://schemas.microsoft.com/ado/2007/08/dataservices"
    M = "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"

    rows = []

    for ym in sorted(months, reverse=True):

        url = (
            "https://home.treasury.gov/resource-center/data-chart-center"
            "/interest-rates/pages/xml"
            "?data=daily_treasury_yield_curve"
            f"&field_tdr_date_value_month={ym}"
        )

        try:
            r = requests.get(
                url,
                timeout=20,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            r.raise_for_status()

            root = ET.fromstring(r.content)

            count = 0

            for props in root.iter(f"{{{M}}}properties"):

                date_el = props.find(f"{{{D}}}NEW_DATE")
                y2_el = props.find(f"{{{D}}}BC_2YEAR")
                y10_el = props.find(f"{{{D}}}BC_10YEAR")
                y30_el = props.find(f"{{{D}}}BC_30YEAR")

                if date_el is None or not date_el.text:
                    continue

                date_only = date_el.text[:10]

                if date_only > target_date_str:
                    continue

                y2v = (
                    float(y2_el.text)
                    if y2_el is not None and y2_el.text
                    else None
                )

                y10v = (
                    float(y10_el.text)
                    if y10_el is not None and y10_el.text
                    else None
                )

                y30v = (
                    float(y30_el.text)
                    if y30_el is not None and y30_el.text
                    else None
                )

                if is_valid_number(y2v):

                    rows.append(
                        (
                            date_only,
                            y2v,
                            y10v,
                            y30v
                        )
                    )

                    count += 1

            print(f"  📥 Treasury.gov {ym}: {count} 筆")

        except Exception as e:
            print(
                f"  ❌ Treasury.gov {ym}: "
                f"{type(e).__name__}: {e}"
            )

    if not rows:
        print("  ⚠️ Treasury.gov 無有效資料")
        return None, None, None

    rows.sort(
        key=lambda x: x[0],
        reverse=True
    )

    curr = rows[0]
    prev = rows[1] if len(rows) > 1 else None

    print(
        f"  ✅ curr={curr[0]} "
        f"2Y={curr[1]} "
        f"10Y={curr[2]} "
        f"30Y={curr[3]}"
    )

    def mk(idx):

        cv = curr[idx]
        pv = prev[idx] if prev else None

        return make_entry(
            cv,
            pv,
            curr[0]
        )

    return mk(1), mk(2), mk(3)


# ── YFINANCE ─────────────────────────────────────────────────
def yf_get(symbol, target_date_str):
    """
    使用 yfinance 抓股票或指數，
    只接受 <= target_date 的資料。
    """

    d = datetime.strptime(
        target_date_str,
        "%Y-%m-%d"
    )

    start = (
        d - timedelta(days=14)
    ).strftime("%Y-%m-%d")

    end = (
        d + timedelta(days=2)
    ).strftime("%Y-%m-%d")

    try:

        t = yf.Ticker(symbol)

        hist = t.history(
            start=start,
            end=end,
            interval="1d",
            auto_adjust=True
        )

        if hist.empty:
            raise ValueError("empty")

        hist.index = hist.index.strftime(
            "%Y-%m-%d"
        )

        valid = hist[
            hist.index <= target_date_str
        ].sort_index(
            ascending=False
        )

        if valid.empty:
            raise ValueError("no valid rows")

        curr_date = valid.index[0]

        curr_val = float(
            valid.iloc[0]["Close"]
        )

        prev_val = (
            float(valid.iloc[1]["Close"])
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
                f"invalid current value: {curr_val}"
            )

        return entry

    except Exception as e:

        print(
            f"  ⚠ t.history failed ({e}), "
            f"trying yf.download..."
        )

        try:

            hist2 = yf.download(
                symbol,
                start=start,
                end=end,
                interval="1d",
                auto_adjust=True,
                progress=False
            )

            if hist2.empty:
                print(
                    f"  ❌ yfinance {symbol}: no data"
                )
                return None

            if isinstance(
                hist2.columns,
                pd.MultiIndex
            ):
                hist2.columns = (
                    hist2.columns
                    .get_level_values(0)
                )

            hist2.index = (
                hist2.index
                .strftime("%Y-%m-%d")
            )

            valid2 = hist2[
                hist2.index <= target_date_str
            ].sort_index(
                ascending=False
            )

            if valid2.empty:
                print(
                    f"  ❌ yfinance {symbol}: "
                    "no valid rows after filter"
                )
                return None

            curr_date = valid2.index[0]

            curr_val = float(
                valid2.iloc[0]["Close"]
            )

            prev_val = (
                float(valid2.iloc[1]["Close"])
                if len(valid2) > 1
                else None
            )

            entry = make_entry(
                curr_val,
                prev_val,
                curr_date
            )

            if entry is None:
                print(
                    f"  ❌ yfinance {symbol}: "
                    f"invalid value {curr_val}"
                )
                return None

            return entry

        except Exception as e2:

            print(
                f"  ❌ yfinance {symbol}: {e2}"
            )

            return None


# ── MOVE ─────────────────────────────────────────────────────
def move_get(target_date_str):
    """
    抓 MOVE 指數

    1. 若 target 是今天，先試 quoteSummary
    2. 再試 chart API
    3. 最後 fallback yf_get
    """

    today_str = date.today().strftime(
        "%Y-%m-%d"
    )

    # ── 方法 1：quoteSummary ──────────────────────────
    if target_date_str == today_str:

        for host in [
            "query2.finance.yahoo.com",
            "query1.finance.yahoo.com"
        ]:

            try:

                url = (
                    f"https://{host}"
                    "/v10/finance/quoteSummary/%5EMOVE"
                )

                params = {
                    "modules": "price"
                }

                headers = {
                    "User-Agent":
                        "Mozilla/5.0 "
                        "(Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36",
                    "Accept": "application/json",
                    "Referer":
                        "https://finance.yahoo.com/",
                }

                r = requests.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=15
                )

                r.raise_for_status()

                j = r.json()

                price = (
                    j["quoteSummary"]
                    ["result"][0]
                    ["price"]
                )

                curr_val = (
                    price["regularMarketPrice"]
                    ["raw"]
                )

                prev_val = (
                    price["regularMarketPreviousClose"]
                    ["raw"]
                )

                market_ts = (
                    price["regularMarketTime"]
                    ["raw"]
                )

                curr_date = (
                    datetime
                    .utcfromtimestamp(market_ts)
                    .strftime("%Y-%m-%d")
                )

                entry = make_entry(
                    curr_val,
                    prev_val,
                    curr_date
                )

                if entry is None:
                    raise ValueError(
                        "MOVE quote invalid value"
                    )

                print(
                    f"  ✅ MOVE (quote {host}): "
                    f"{entry['value']:.2f} "
                    f"({curr_date})"
                )

                return entry

            except Exception as e:

                print(
                    f"  ⚠ MOVE quote {host}: {e}"
                )

    # ── 方法 2：chart API ─────────────────────────────
    for host in [
        "query2.finance.yahoo.com",
        "query1.finance.yahoo.com"
    ]:

        try:

            url = (
                f"https://{host}"
                "/v8/finance/chart/%5EMOVE"
            )

            params = {
                "interval": "1d",
                "range": "10d"
            }

            headers = {
                "User-Agent":
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36",
                "Accept": "application/json",
                "Referer":
                    "https://finance.yahoo.com/",
            }

            r = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=15
            )

            r.raise_for_status()

            j = r.json()

            result = j["chart"]["result"][0]

            ts = result["timestamp"]

            close = (
                result["indicators"]
                ["quote"][0]
                ["close"]
            )

            rows = []

            for t, c in zip(ts, close):

                if not is_valid_number(c):
                    continue

                dt_str = (
                    datetime
                    .utcfromtimestamp(t)
                    .strftime("%Y-%m-%d")
                )

                rows.append(
                    (
                        dt_str,
                        float(c)
                    )
                )

            rows.sort(
                reverse=True
            )

            valid = [
                (dt, v)
                for dt, v in rows
                if dt <= target_date_str
            ]

            if valid:

                curr_date, curr_val = valid[0]

                prev_val = (
                    valid[1][1]
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
                        "MOVE chart invalid value"
                    )

                print(
                    f"  ✅ MOVE (chart {host}): "
                    f"{entry['value']:.2f} "
                    f"({curr_date})"
                )

                return entry

        except Exception as e:

            print(
                f"  ⚠ MOVE chart {host}: {e}"
            )

    # ── 方法 3：fallback yfinance ─────────────────────
    print("  ⚠ MOVE fallback yf_get...")

    result = yf_get(
        "^MOVE",
        target_date_str
    )

    if result:

        print(
            f"  ✅ MOVE (yf_get): "
            f"{result['value']} "
            f"({result['date']})"
        )

    else:

        print(
            "  ❌ MOVE: 所有來源都失敗"
        )

    return result


# ── FETCH ALL ────────────────────────────────────────────────
def fetch_all(target):

    result = {
        "generated_at":
            datetime.utcnow().isoformat()
            + "Z",
        "target_date":
            target,
    }

    # ── VIX ────────────────────────────────────────────
    print("📡 VIX...")

    d = yf_get(
        "^VIX",
        target
    )

    result["vix"] = d

    print(
        f"  {'✅' if d else '❌'} "
        f"VIX: "
        f"{d['value'] if d else 'N/A'}"
    )

    # ── MOVE ───────────────────────────────────────────
    print("📡 MOVE...")

    d = move_get(target)

    result["move"] = d

    print(
        f"  {'✅' if d else '❌'} "
        f"MOVE: "
        f"{d['value'] if d else 'N/A'}"
    )

    # ── Treasury ───────────────────────────────────────
    print(
        "📡 2Y/10Y/30Y 公債（Treasury.gov）..."
    )

    y2, y10, y30 = treasury_get(target)

    result["y2"] = y2
    result["y10"] = y10
    result["y30"] = y30

    print(
        f"  {'✅' if y2 else '❌'} "
        f"2Y: "
        f"{y2['value'] if y2 else 'N/A'}%"
    )

    print(
        f"  {'✅' if y10 else '❌'} "
        f"10Y: "
        f"{y10['value'] if y10 else 'N/A'}%"
    )

    print(
        f"  {'✅' if y30 else '❌'} "
        f"30Y: "
        f"{y30['value'] if y30 else 'N/A'}%"
    )

    # ── 10Y-2Y spread ─────────────────────────────────
    if (
        result.get("y10")
        and result.get("y2")
    ):

        spread = round(
            (
                result["y10"]["value"]
                - result["y2"]["value"]
            )
            * 100,
            2
        )

        result["spread"] = spread

        print(
            f"  ✅ 10Y-2Y 利差: "
            f"{spread} bps"
        )

    else:

        result["spread"] = None

    # ── SOX ────────────────────────────────────────────
    print("📡 SOX...")

    d = yf_get(
        "^SOX",
        target
    )

    result["sox"] = d

    print(
        f"  {'✅' if d else '❌'} "
        f"SOX: "
        f"{d['value'] if d else 'N/A'}"
    )

    # ── 個股 ───────────────────────────────────────────
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

    result["stocks"] = {}

    for sym, meta in stocks_meta.items():

        print(f"📡 {sym}...")

        d = yf_get(
            sym,
            target
        )

        if d:
            d.update(meta)

        result["stocks"][sym] = d

        print(
            f"  {'✅' if d else '❌'} "
            f"{sym}: "
            f"${d['value'] if d else 'N/A'}"
        )

    return result


# ── DATA VALIDATION ──────────────────────────────────────────
def validate_data(data, target):
    """
    寫檔前完整驗證。

    必須同時符合：
    1. 有資料
    2. 日期 = TARGET
    3. value 是有效有限數字

    任一失敗：
    → 不寫 market_YYYY-MM-DD.json
    → 不更新 latest.json
    → workflow 回傳失敗
    """

    checks = {}

    checks["VIX"] = data.get("vix")
    checks["MOVE"] = data.get("move")
    checks["SOX"] = data.get("sox")

    checks["2Y"] = data.get("y2")
    checks["10Y"] = data.get("y10")
    checks["30Y"] = data.get("y30")

    for symbol, stock in (
        data.get("stocks", {}).items()
    ):
        checks[symbol] = stock

    failed = []

    print("\n🔎 資料完整性驗證")

    for name, item in checks.items():

        # ── 無資料 ─────────────────────────────────────
        if item is None:

            failed.append(
                f"{name}: 無資料"
            )

            print(
                f"  ❌ {name}: 無資料"
            )

            continue

        actual_date = item.get("date")
        value = item.get("value")

        # ── 日期檢查 ───────────────────────────────────
        if actual_date != target:

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

        # ── 數值檢查 ───────────────────────────────────
        if not is_valid_number(value):

            failed.append(
                f"{name}: "
                f"value 無效 ({value})"
            )

            print(
                f"  ❌ {name}: "
                f"日期正確，但 "
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
            "本次不寫入任何 JSON，"
            "等待 GitHub Actions 重試。"
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


# ── MAIN ─────────────────────────────────────────────────────
if __name__ == "__main__":

    data = fetch_all(TARGET)

    # ── 寫檔前完整驗證 ────────────────────────────────
    if not validate_data(
        data,
        TARGET
    ):

        print(
            "\n❌ DATA_NOT_READY"
        )

        print(
            "資料尚未完整到達 TARGET，"
            "本次執行失敗。"
        )

        sys.exit(2)

    # ── 日期檔 ────────────────────────────────────────
    out_path = (
        DATA_DIR
        / f"market_{TARGET}.json"
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
        f"\n✅ 已寫入 {out_path}"
    )

# ── latest.json ───────────────────────────────────
latest_path = DATA_DIR / "latest.json"

should_update_latest = True
existing_latest_date = None

# 如果 latest.json 已存在，
# 先確認這次 TARGET 是否比目前 latest 新或相同
if latest_path.exists():

    try:
        with open(
            latest_path,
            "r",
            encoding="utf-8"
        ) as f:
            existing_latest = json.load(f)

        existing_latest_date = existing_latest.get(
            "target_date"
        )

        if existing_latest_date:

            current_target_date = datetime.strptime(
                TARGET,
                "%Y-%m-%d"
            ).date()

            old_latest_date = datetime.strptime(
                existing_latest_date,
                "%Y-%m-%d"
            ).date()

            # 歷史補抓不可讓 latest.json 往回退
            if current_target_date < old_latest_date:
                should_update_latest = False

    except Exception as e:
        print(
            f"⚠️ 讀取 existing latest.json 失敗：{e}"
        )

        # 若舊 latest 本身讀不到，
        # 允許用本次完整有效資料修復
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
        f"✅ 已更新 {latest_path}"
    )

else:

    print(
        f"ℹ️ 本次為歷史補抓 TARGET={TARGET}，"
        f"目前 latest={existing_latest_date}，"
        "因此不更新 latest.json"
    )

    # ── 摘要 ──────────────────────────────────────────
    print(
        "\n📊 數據摘要:"
    )

    if data.get("vix"):

        v = data["vix"]["value"]

        print(
            f"  VIX: {v:.2f} "
            f"{'🚨 恐慌' if v >= 30 else '⚠️ 警戒' if v >= 20 else '✅ 正常'}"
        )

    if (
        data.get("y10")
        and data.get("y2")
    ):

        print(
            f"  10Y-2Y 利差: "
            f"{data['spread']} bps "
            f"{'🚨 倒掛！' if data['spread'] < 0 else ''}"
        )

    for sym, s in (
        data.get("stocks", {}).items()
    ):

        if s:

            chg_pct = s.get(
                "chg_pct"
            )

            if is_valid_number(chg_pct):

                print(
                    f"  {sym}: "
                    f"${s['value']:.2f} "
                    f"({chg_pct:+.2f}%)"
                )

            else:

                print(
                    f"  {sym}: "
                    f"${s['value']:.2f}"
                )
