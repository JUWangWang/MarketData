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
FRED_KEY = os.environ.get("FRED_KEY", "")
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


def fred_get(series_id, target_date_str, n=5):
    """從 FRED 抓資料，找最接近 target_date 的最新值與前一值"""
    if not FRED_KEY:
        print(f"  ⚠️  FRED_KEY not set, skipping {series_id}")
        return None
    d = datetime.strptime(target_date_str, "%Y-%m-%d")
    obs_start = (d - timedelta(days=14)).strftime("%Y-%m-%d")
    obs_end   = (d + timedelta(days=2)).strftime("%Y-%m-%d")
    url = (
        f"https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={FRED_KEY}&file_type=json"
        f"&sort_order=desc&limit={n}"
        f"&observation_start={obs_start}&observation_end={obs_end}"
    )
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        obs = [o for o in r.json().get("observations", [])
               if o["value"] not in (".", "") and o["date"] <= target_date_str]
        if not obs:
            return None
        curr_date, curr_val = obs[0]["date"], float(obs[0]["value"])
        prev_val = float(obs[1]["value"]) if len(obs) > 1 else None
        return make_entry(curr_val, prev_val, curr_date)
    except Exception as e:
        print(f"  ❌ FRED {series_id}: {e}")
        return None


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

    # 公債殖利率 — FRED（最精準）
    for series, key, label in [
        ("DGS2",  "y2",  "2Y"),
        ("DGS10", "y10", "10Y"),
        ("DGS30", "y30", "30Y"),
    ]:
        print(f"📡 {label} 公債...")
        d = fred_get(series, target)
        result[key] = d
        print(f"  {'✅' if d else '❌'} {label}: {d['value'] if d else 'N/A'}%")

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

