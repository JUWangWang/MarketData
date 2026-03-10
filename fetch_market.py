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

import yfinance as yf
import requests

# ── CONFIG ──────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# 手動指定日期（補抓歷史）
if len(sys.argv) > 1:
    TARGET = sys.argv[1]  # "2026-03-07"
else:
    # Actions 在 UTC 22:30 執行（台灣時間隔天早上）
    # 目標是「前一個交易日」的收盤資料
    d = date.today()
    d -= timedelta(days=1)
    while d.weekday() >= 5:   # 5=Saturday, 6=Sunday
        d -= timedelta(days=1)
    TARGET = d.strftime("%Y-%m-%d")

print(f"🗓  Target date: {TARGET}")


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
    """從 Treasury.gov 官方 API 抓 2Y/10Y/30Y 殖利率，無延遲、無需 API key"""
    import xml.etree.ElementTree as ET
    d = datetime.strptime(target_date_str, "%Y-%m-%d")
    # 抓當月與前一個月（確保有前日數據）
    months = set()
    for delta in [0, 1]:
        m = d - timedelta(days=30*delta)
        months.add(m.strftime("%Y%m"))

    rows = []  # list of (date_str, bc_2y, bc_10y, bc_30y)
    D = 'http://schemas.microsoft.com/ado/2007/08/dataservices'
    M = 'http://schemas.microsoft.com/ado/2007/08/dataservices/metadata'
    ATOM = 'http://www.w3.org/2005/Atom'

    for ym in sorted(months, reverse=True):
        url = (f"https://home.treasury.gov/resource-center/data-chart-center"
               f"/interest-rates/pages/xml?data=daily_treasury_yield_curve"
               f"&field_tdr_date_value_month={ym}")
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            for entry in root.findall(f'{{{ATOM}}}entry'):
                props = entry.find(f'{{{M}}}properties')
                if props is None:
                    continue
                date_el = props.find(f'{{{D}}}NEW_DATE')
                y2_el   = props.find(f'{{{D}}}BC_2YEAR')
                y10_el  = props.find(f'{{{D}}}BC_10YEAR')
                y30_el  = props.find(f'{{{D}}}BC_30YEAR')
                if date_el is None or date_el.text is None:
                    continue
                # Treasury 日期格式：2026-03-10T00:00:00
                date_only = date_el.text[:10]
                if date_only > target_date_str:
                    continue
                rows.append((
                    date_only,
                    float(y2_el.text)  if y2_el  is not None and y2_el.text  else None,
                    float(y10_el.text) if y10_el is not None and y10_el.text else None,
                    float(y30_el.text) if y30_el is not None and y30_el.text else None,
                ))
        except Exception as e:
            print(f"  ❌ Treasury.gov {ym}: {e}")

    if not rows:
        return None, None, None

    rows.sort(key=lambda x: x[0], reverse=True)
    curr = rows[0]
    prev = rows[1] if len(rows) > 1 else None

    def mk(ci, pi):
        cv = curr[ci]
        pv = prev[pi] if prev else None
        return make_entry(cv, pv, curr[0]) if cv is not None else None

    return mk(1,1), mk(2,2), mk(3,3)  # y2, y10, y30


def yf_get(symbol, target_date_str):
    """用 yfinance 抓股票/指數，找最接近 target_date 的收盤價"""
    try:
        d = datetime.strptime(target_date_str, "%Y-%m-%d")
        start = (d - timedelta(days=14)).strftime("%Y-%m-%d")
        end   = (d + timedelta(days=2)).strftime("%Y-%m-%d")
        t = yf.Ticker(symbol)
        hist = t.history(start=start, end=end, interval="1d", auto_adjust=True)
        if hist.empty:
            return None
        # 只取 <= target_date 的列
        hist.index = hist.index.strftime("%Y-%m-%d")
        valid = hist[hist.index <= target_date_str].sort_index(ascending=False)
        if valid.empty:
            return None
        curr_date = valid.index[0]
        curr_val  = float(valid.iloc[0]["Close"])
        prev_val  = float(valid.iloc[1]["Close"]) if len(valid) > 1 else None
        return make_entry(curr_val, prev_val, curr_date)
    except Exception as e:
        print(f"  ❌ yfinance {symbol}: {e}")
        return None


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

    # MOVE — yfinance ^MOVE
    print("📡 MOVE...")
    d = yf_get("^MOVE", target)
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


# ── MAIN ─────────────────────────────────────────────────────
if __name__ == "__main__":
    data = fetch_all(TARGET)

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
