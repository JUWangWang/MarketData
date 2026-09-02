#!/usr/bin/env python3
"""
金融市場每日監控指標 — 資料抓取腳本
GitHub Actions 每日自動執行，輸出 data/YYYY-MM-DD.json
手動補抓：python fetch_market.py 2026-03-07
"""

import json
import os
import sys
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal

import yfinance as yf
import pandas as pd
import requests

# ── CONFIG ──────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# ── TARGET DATE ──────────────────────────────────────────────
def get_target_date():
    """
    手動執行：
        python fetch_market.py 2026-09-02
        → 指定抓 2026-09-02

    自動執行：
        以台灣時間為基準，
        找「今天以前最近一個 NYSE 正式交易日」。

    例如：
        台灣 2026-09-03 07:30
        → TARGET = 2026-09-02

        台灣週一早上
        → TARGET = 上週五

        美國休市後隔天
        → 自動跳過美國假日
    """
    if len(sys.argv) > 1:
        target = sys.argv[1]
        datetime.strptime(target, "%Y-%m-%d")  # 驗證格式
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

print(f"🕒 Taiwan time: {datetime.now(ZoneInfo('Asia/Taipei')).isoformat()}")
print(f"🗓  Target market date: {TARGET}")


# ── HELPERS ─────────────────────────────────────────────────
def make_entry(curr_val, prev_val, curr_date):
    if curr_val is None:
        return None
    chg_abs = round(curr_val - prev_val, 6) if prev_val is not None else None
    chg_pct = round((curr_val - prev_val) / prev_val * 100, 4) if prev_val is not None else None
    return {
        "value":   round(curr_val, 4),
        "prev":    round(prev_val, 4) if prev_val is not None else None,
        "chg_abs": chg_abs,
        "chg_pct": chg_pct,
        "date":    curr_date,
    }


def treasury_get(target_date_str):
    """從 Treasury.gov 官方 API 抓 2Y/10Y/30Y 殖利率
    XML 結構：feed > entry > content > m:properties > d:BC_2YEAR ...
    """
    import xml.etree.ElementTree as ET
    d = datetime.strptime(target_date_str, "%Y-%m-%d")
    months = set()
    for delta in [0, 1, 2]:
        m = d - timedelta(days=30*delta)
        months.add(m.strftime("%Y%m"))

    D    = 'http://schemas.microsoft.com/ado/2007/08/dataservices'
    M    = 'http://schemas.microsoft.com/ado/2007/08/dataservices/metadata'
    rows = []

    for ym in sorted(months, reverse=True):
        url = (f"https://home.treasury.gov/resource-center/data-chart-center"
               f"/interest-rates/pages/xml?data=daily_treasury_yield_curve"
               f"&field_tdr_date_value_month={ym}")
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            root = ET.fromstring(r.content)
            count = 0
            # properties 在 entry > content > m:properties，用 iter 直接找
            for props in root.iter(f'{{{M}}}properties'):
                date_el = props.find(f'{{{D}}}NEW_DATE')
                y2_el   = props.find(f'{{{D}}}BC_2YEAR')
                y10_el  = props.find(f'{{{D}}}BC_10YEAR')
                y30_el  = props.find(f'{{{D}}}BC_30YEAR')
                if date_el is None or not date_el.text:
                    continue
                date_only = date_el.text[:10]  # "2026-03-09T00:00:00" -> "2026-03-09"
                if date_only > target_date_str:
                    continue
                y2v  = float(y2_el.text)  if y2_el  is not None and y2_el.text  else None
                y10v = float(y10_el.text) if y10_el is not None and y10_el.text else None
                y30v = float(y30_el.text) if y30_el is not None and y30_el.text else None
                if y2v is not None:
                    rows.append((date_only, y2v, y10v, y30v))
                    count += 1
            print(f"  📥 Treasury.gov {ym}: {count} 筆")
        except Exception as e:
            print(f"  ❌ Treasury.gov {ym}: {type(e).__name__}: {e}")

    if not rows:
        print("  ⚠️  Treasury.gov 無有效資料")
        return None, None, None

    rows.sort(key=lambda x: x[0], reverse=True)
    curr = rows[0]
    prev = rows[1] if len(rows) > 1 else None
    print(f"  ✅ curr={curr[0]} 2Y={curr[1]} 10Y={curr[2]} 30Y={curr[3]}")

    def mk(idx):
        cv = curr[idx]
        pv = prev[idx] if prev else None
        return make_entry(cv, pv, curr[0]) if cv is not None else None

    return mk(1), mk(2), mk(3)


def yf_get(symbol, target_date_str):
    """用 yfinance 抓股票/指數，找最接近 target_date 的收盤價"""
    try:
        d = datetime.strptime(target_date_str, "%Y-%m-%d")
        start = (d - timedelta(days=14)).strftime("%Y-%m-%d")
        end   = (d + timedelta(days=2)).strftime("%Y-%m-%d")
        t = yf.Ticker(symbol)
        hist = t.history(start=start, end=end, interval="1d", auto_adjust=True)
        if hist.empty:
            raise ValueError("empty")
        # 只取 <= target_date 的列
        hist.index = hist.index.strftime("%Y-%m-%d")
        valid = hist[hist.index <= target_date_str].sort_index(ascending=False)
        if valid.empty:
            raise ValueError("no valid rows")
        curr_date = valid.index[0]
        curr_val  = float(valid.iloc[0]["Close"])
        prev_val  = float(valid.iloc[1]["Close"]) if len(valid) > 1 else None
        return make_entry(curr_val, prev_val, curr_date)
    except Exception as e:
        # fallback: yf.download（對 ^MOVE 等特殊 ticker 較穩定）
        print(f"  ⚠ t.history failed ({e}), trying yf.download...")
        try:
            hist2 = yf.download(symbol, start=start, end=end, interval="1d",
                                auto_adjust=True, progress=False)
            if hist2.empty:
                print(f"  ❌ yfinance {symbol}: no data")
                return None
            # MultiIndex 欄位攤平
            if isinstance(hist2.columns, pd.MultiIndex):
                hist2.columns = hist2.columns.get_level_values(0)
            hist2.index = hist2.index.strftime("%Y-%m-%d")
            valid2 = hist2[hist2.index <= target_date_str].sort_index(ascending=False)
            if valid2.empty:
                print(f"  ❌ yfinance {symbol}: no valid rows after filter")
                return None
            curr_date = valid2.index[0]
            curr_val  = float(valid2.iloc[0]["Close"])
            prev_val  = float(valid2.iloc[1]["Close"]) if len(valid2) > 1 else None
            return make_entry(curr_val, prev_val, curr_date)
        except Exception as e2:
            print(f"  ❌ yfinance {symbol}: {e2}")
            return None


def move_get(target_date_str):
    """抓 MOVE 指數：
    - 若 target 是今日 → 用 quoteSummary API 抓當日最新報價（不 lag）
    - 否則 → 用 chart API 抓歷史資料
    - 以上失敗 → fallback yf_get
    """
    today_str = date.today().strftime("%Y-%m-%d")

    # 方法1：target 是今日，用 quoteSummary 抓最新報價
    if target_date_str == today_str:
        for host in ["query2.finance.yahoo.com", "query1.finance.yahoo.com"]:
            try:
                url = f"https://{host}/v10/finance/quoteSummary/%5EMOVE"
                params = {"modules": "price"}
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/json",
                    "Referer": "https://finance.yahoo.com/",
                }
                r = requests.get(url, params=params, headers=headers, timeout=15)
                r.raise_for_status()
                j = r.json()
                price = j["quoteSummary"]["result"][0]["price"]
                curr_val  = price["regularMarketPrice"]["raw"]
                prev_val  = price["regularMarketPreviousClose"]["raw"]
                market_ts = price["regularMarketTime"]["raw"]
                curr_date = datetime.utcfromtimestamp(market_ts).strftime("%Y-%m-%d")
                print(f"  ✅ MOVE (quote {host}): {curr_val:.2f} ({curr_date})")
                return make_entry(curr_val, prev_val, curr_date)
            except Exception as e:
                print(f"  ⚠ MOVE quote {host}: {e}")

    # 方法2：chart API 抓歷史資料
    for host in ["query2.finance.yahoo.com", "query1.finance.yahoo.com"]:
        try:
            url = f"https://{host}/v8/finance/chart/%5EMOVE"
            params = {"interval": "1d", "range": "10d"}
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
                "Referer": "https://finance.yahoo.com/",
            }
            r = requests.get(url, params=params, headers=headers, timeout=15)
            r.raise_for_status()
            j = r.json()
            ts    = j["chart"]["result"][0]["timestamp"]
            close = j["chart"]["result"][0]["indicators"]["quote"][0]["close"]
            rows = []
            for t, c in zip(ts, close):
                if c is None: continue
                dt_str = datetime.utcfromtimestamp(t).strftime("%Y-%m-%d")
                rows.append((dt_str, c))
            rows.sort(reverse=True)
            valid = [(dt, v) for dt, v in rows if dt <= target_date_str]
            if valid:
                curr_date, curr_val = valid[0]
                prev_val = valid[1][1] if len(valid) > 1 else None
                print(f"  ✅ MOVE (chart {host}): {curr_val:.2f} ({curr_date})")
                return make_entry(curr_val, prev_val, curr_date)
        except Exception as e:
            print(f"  ⚠ MOVE chart {host}: {e}")

    # 方法3：fallback yf_get
    print(f"  ⚠ MOVE fallback yf_get...")
    result = yf_get("^MOVE", target_date_str)
    if result:
        print(f"  ✅ MOVE (yf_get): {result['value']} ({result['date']})")
    else:
        print(f"  ❌ MOVE: 所有來源都失敗")
    return result


# ── FETCH ALL ────────────────────────────────────────────────
def fetch_all(target):
    result = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "target_date":  target,
    }
    errors = []

    # VIX — yfinance ^VIX（與 MOVE 同步，無 FRED 延遲問題）
    print("📡 VIX...")
    d = yf_get("^VIX", target)
    result["vix"] = d
    print(f"  {'✅' if d else '❌'} VIX: {d['value'] if d else 'N/A'}")

    # MOVE — 同時查 Yahoo Finance API 和 FRED，選日期較新的那筆
    print("📡 MOVE...")
    d = move_get(target)
    result["move"] = d
    print(f"  {'✅' if d else '❌'} MOVE: {d['value'] if d else 'N/A'}")

    # 公債殖利率 — Treasury.gov 官方 API（無延遲、無需 API key）
    print("📡 2Y/10Y/30Y 公債（Treasury.gov）...")
    y2, y10, y30 = treasury_get(target)
    result["y2"]  = y2
    result["y10"] = y10
    result["y30"] = y30
    print(f"  {'✅' if y2  else '❌'} 2Y:  {y2['value']  if y2  else 'N/A'}%")
    print(f"  {'✅' if y10 else '❌'} 10Y: {y10['value'] if y10 else 'N/A'}%")
    print(f"  {'✅' if y30 else '❌'} 30Y: {y30['value'] if y30 else 'N/A'}%")

    # 10Y-2Y 利差（直接計算）
    if result.get("y10") and result.get("y2"):
        spread = round((result["y10"]["value"] - result["y2"]["value"]) * 100, 2)
        result["spread"] = spread
        print(f"  ✅ 10Y-2Y 利差: {spread} bps")
    else:
        result["spread"] = None

    # SOX — yfinance ^SOX
    print("📡 SOX...")
    d = yf_get("^SOX", target)
    result["sox"] = d
    print(f"  {'✅' if d else '❌'} SOX: {d['value'] if d else 'N/A'}")

    # 個股
    stocks_meta = {
        "NVDA": {"name": "輝達",     "emoji": "🟢", "grade": "IG1"},
        "TSM":  {"name": "台積電",   "emoji": "🔵", "grade": "IG1"},
        "SMCI": {"name": "超微電腦", "emoji": "⚡",  "grade": "HY1"},
        "ARM":  {"name": "安謀控股", "emoji": "💻", "grade": "IG1"},
        "TSLA": {"name": "特斯拉",   "emoji": "🚗", "grade": "IG3"},
    }
    result["stocks"] = {}
    for sym, meta in stocks_meta.items():
        print(f"📡 {sym}...")
        d = yf_get(sym, target)
        if d:
            d.update(meta)
        result["stocks"][sym] = d
        print(f"  {'✅' if d else '❌'} {sym}: ${d['value'] if d else 'N/A'}")

    if errors:
        result["errors"] = errors

    return result

# ── DATA DATE VALIDATION ─────────────────────────────────────
def validate_data_dates(data, target):
    """
    確認抓到的資料確實屬於 TARGET。

    若任一主要市場資料仍停留在前一日：
    → 視為資料來源尚未更新完成
    → 不寫 market_YYYY-MM-DD.json
    → 不更新 latest.json
    → 回傳失敗，讓 GitHub Actions 稍後重試
    """

    checks = {}

    # 市場指標
    checks["VIX"] = data.get("vix")
    checks["MOVE"] = data.get("move")
    checks["SOX"] = data.get("sox")

    # 美國公債
    checks["2Y"] = data.get("y2")
    checks["10Y"] = data.get("y10")
    checks["30Y"] = data.get("y30")

    # 個股
    for symbol, stock in data.get("stocks", {}).items():
        checks[symbol] = stock

    failed = []

    print("\n🔎 資料日期驗證")

    for name, item in checks.items():

        if item is None:
            failed.append(f"{name}: 無資料")
            print(f"  ❌ {name}: 無資料")
            continue

        actual_date = item.get("date")

        if actual_date != target:
            failed.append(
                f"{name}: expected={target}, actual={actual_date}"
            )
            print(
                f"  ❌ {name}: {actual_date} "
                f"(應為 {target})"
            )
        else:
            print(f"  ✅ {name}: {actual_date}")

    if failed:
        print("\n⚠️ 市場資料尚未全部更新完成")
        print("本次不寫入任何 JSON，等待 GitHub Actions 重試。")

        for msg in failed:
            print(f"  - {msg}")

        return False

    print("\n✅ 所有市場資料日期均正確")

    return True

# ── MAIN ─────────────────────────────────────────────────────
if __name__ == "__main__":
    data = fetch_all(TARGET)

    # ── 寫檔前先驗證日期 ─────────────────────────────
    if not validate_data_dates(data, TARGET):
        print("\n❌ DATA_NOT_READY")
        print("資料日期尚未全部到達 TARGET，本次執行失敗。")
        sys.exit(2)

    # 存成日期檔
    out_path = DATA_DIR / f"market_{TARGET}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 已寫入 {out_path}")

    # 同時更新 latest.json（給 HTML 今日頁面用）
    latest_path = DATA_DIR / "latest.json"
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ 已更新 {latest_path}")

    # 輸出摘要
    print("\n📊 數據摘要:")
    if data.get("vix"):
        v = data["vix"]["value"]
        print(f"  VIX: {v:.2f} {'🚨 恐慌' if v>=30 else '⚠️ 警戒' if v>=20 else '✅ 正常'}")
    if data.get("y10") and data.get("y2"):
        print(f"  10Y-2Y 利差: {data['spread']} bps {'🚨 倒掛！' if data['spread']<0 else ''}")
    for sym, s in data.get("stocks", {}).items():
        if s:
            print(f"  {sym}: ${s['value']:.2f} ({s['chg_pct']:+.2f}%)")

