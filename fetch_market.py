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

MACROVAR_KEY = os.environ.get("MACROVAR_KEY", "91af5bb6-72e8-487b-97e1-dbd35984f123")
MACROVAR_API = "https://api.macrovar.com/api"

# 手動指定日期（補抓歷史）
if len(sys.argv) > 1:
    TARGET = sys.argv[1]
else:
    d = date.today()
    d -= timedelta(days=1)
    while d.weekday() >= 5:
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
    """從 Treasury.gov 官方 API 抓 2Y/10Y/30Y 殖利率"""
    import xml.etree.ElementTree as ET
    d = datetime.strptime(target_date_str, "%Y-%m-%d")
    months = set()
    for delta in [0, 1, 2]:
        m = d - timedelta(days=30*delta)
        months.add(m.strftime("%Y%m"))

    D = 'http://schemas.microsoft.com/ado/2007/08/dataservices'
    M = 'http://schemas.microsoft.com/ado/2007/08/dataservices/metadata'
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
            for props in root.iter(f'{{{M}}}properties'):
                date_el = props.find(f'{{{D}}}NEW_DATE')
                y2_el   = props.find(f'{{{D}}}BC_2YEAR')
                y10_el  = props.find(f'{{{D}}}BC_10YEAR')
                y30_el  = props.find(f'{{{D}}}BC_30YEAR')
                if date_el is None or not date_el.text:
                    continue
                date_only = date_el.text[:10]
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
    """用 yfinance 抓股票/指數"""
    try:
        d = datetime.strptime(target_date_str, "%Y-%m-%d")
        start = (d - timedelta(days=14)).strftime("%Y-%m-%d")
        end   = (d + timedelta(days=2)).strftime("%Y-%m-%d")
        t = yf.Ticker(symbol)
        hist = t.history(start=start, end=end, interval="1d", auto_adjust=True)
        if hist.empty:
            return None
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


# ── MACROVAR CDS ─────────────────────────────────────────────
CDS_COUNTRIES = [
    {"key": "US", "name": "美國", "flag": "🇺🇸",
     "candidates": ["USCDS5Y", "US.CDS.5Y", "USA.CDS.5Y", "united-states-cds-5-years", "us cds 5 year"]},
    {"key": "KR", "name": "南韓", "flag": "🇰🇷",
     "candidates": ["KRCDS5Y", "KR.CDS.5Y", "KOR.CDS.5Y", "south-korea-cds-5-years", "korea cds 5 year"]},
    {"key": "JP", "name": "日本", "flag": "🇯🇵",
     "candidates": ["JPCDS5Y", "JP.CDS.5Y", "JPN.CDS.5Y", "japan-cds-5-years", "japan cds 5 year"]},
    {"key": "CN", "name": "中國", "flag": "🇨🇳",
     "candidates": ["CNCDS5Y", "CN.CDS.5Y", "CHN.CDS.5Y", "china-cds-5-years", "china cds 5 year"]},
    {"key": "HK", "name": "香港", "flag": "🇭🇰",
     "candidates": ["HKCDS5Y", "HK.CDS.5Y", "HKG.CDS.5Y", "hong-kong-cds-5-years", "hong kong cds 5 year"]},
]

_cds_symbol_cache = {}


def macrovar_search_cds():
    """搜尋可用的 CDS symbols（除錯用）"""
    print("  🔍 搜尋 MacroVar CDS symbols...")
    try:
        r = requests.post(f"{MACROVAR_API}/search",
                          json={"api_key": MACROVAR_KEY, "query": "sovereign cds 5 year"},
                          timeout=15)
        print(f"  搜尋 HTTP {r.status_code}: {r.text[:600]}")
    except Exception as e:
        print(f"  ⚠️  搜尋失敗: {e}")


def macrovar_get_cds(country_key, candidates):
    """嘗試多種 symbol 格式，回傳第一個成功的結果"""
    if country_key in _cds_symbol_cache:
        sym = _cds_symbol_cache[country_key]
        candidates = [sym] + [c for c in candidates if c != sym]

    for sym in candidates:
        try:
            r = requests.post(f"{MACROVAR_API}/markets",
                              json={"api_key": MACROVAR_KEY, "symbol": sym},
                              timeout=10)
            if r.status_code != 200:
                print(f"    '{sym}' → HTTP {r.status_code}")
                continue
            text = r.text.strip()
            if not text or text in ["null", "[]", "{}", ""]:
                print(f"    '{sym}' → 空回應")
                continue

            print(f"    '{sym}' → {text[:150]}")
            data = r.json()

            curr_val, prev_val, val_date = None, None, None

            if isinstance(data, list) and len(data) >= 1:
                valid = [row for row in data
                         if isinstance(row, dict) and str(row.get("date", "")) <= TARGET]
                if valid:
                    valid.sort(key=lambda x: str(x.get("date", "")), reverse=True)
                    curr_val = float(valid[0].get("value") or valid[0].get("close") or 0)
                    val_date = str(valid[0].get("date", TARGET))[:10]
                    prev_val = float(valid[1].get("value") or valid[1].get("close") or 0) if len(valid) > 1 else None
            elif isinstance(data, dict):
                curr_val = float(data.get("value") or data.get("close") or data.get("last") or 0)
                val_date = str(data.get("date", TARGET))[:10]
                prev_val = float(data.get("prev") or data.get("previous") or 0) or None

            if curr_val and curr_val > 0:
                _cds_symbol_cache[country_key] = sym
                print(f"  ✅ {country_key} '{sym}' = {curr_val} bps ({val_date})")
                return make_entry(curr_val, prev_val, val_date or TARGET)

        except Exception as e:
            print(f"    '{sym}' → 例外: {e}")
            continue

    print(f"  ❌ {country_key}: 所有 symbol 均無效")
    return None


def fetch_cds_all():
    """抓取所有國家主權 CDS（5年期，bps）"""
    print("📡 主權 CDS（MacroVar）...")
    macrovar_search_cds()

    result = {}
    for c in CDS_COUNTRIES:
        print(f"  → {c['flag']} {c['name']}...")
        entry = macrovar_get_cds(c["key"], c["candidates"])
        if entry:
            entry["name"] = c["name"]
            entry["flag"] = c["flag"]
        result[c["key"]] = entry

    return result


# ── FETCH ALL ────────────────────────────────────────────────
def fetch_all(target):
    result = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "target_date":  target,
    }
    errors = []

    print("📡 VIX...")
    d = yf_get("^VIX", target)
    result["vix"] = d
    print(f"  {'✅' if d else '❌'} VIX: {d['value'] if d else 'N/A'}")

    print("📡 MOVE...")
    d = yf_get("^MOVE", target)
    result["move"] = d
    print(f"  {'✅' if d else '❌'} MOVE: {d['value'] if d else 'N/A'}")

    print("📡 2Y/10Y/30Y 公債（Treasury.gov）...")
    y2, y10, y30 = treasury_get(target)
    result["y2"]  = y2
    result["y10"] = y10
    result["y30"] = y30
    print(f"  {'✅' if y2  else '❌'} 2Y:  {y2['value']  if y2  else 'N/A'}%")
    print(f"  {'✅' if y10 else '❌'} 10Y: {y10['value'] if y10 else 'N/A'}%")
    print(f"  {'✅' if y30 else '❌'} 30Y: {y30['value'] if y30 else 'N/A'}%")

    if result.get("y10") and result.get("y2"):
        spread = round((result["y10"]["value"] - result["y2"]["value"]) * 100, 2)
        result["spread"] = spread
        print(f"  ✅ 10Y-2Y 利差: {spread} bps")
    else:
        result["spread"] = None

    print("📡 SOX...")
    d = yf_get("^SOX", target)
    result["sox"] = d
    print(f"  {'✅' if d else '❌'} SOX: {d['value'] if d else 'N/A'}")

    # 主權 CDS（MacroVar）—— 失敗不影響其他資料
    try:
        result["cds"] = fetch_cds_all()
        cds_ok = sum(1 for v in result["cds"].values() if v)
        print(f"  CDS 成功 {cds_ok}/{len(CDS_COUNTRIES)} 個國家")
    except Exception as e:
        print(f"  ❌ CDS 整體失敗: {e}")
        result["cds"] = {}

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

    out_path = DATA_DIR / f"market_{TARGET}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 已寫入 {out_path}")

    latest_path = DATA_DIR / "latest.json"
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ 已更新 {latest_path}")

    print("\n📊 數據摘要:")
    if data.get("vix"):
        v = data["vix"]["value"]
        print(f"  VIX: {v:.2f} {'🚨 恐慌' if v>=30 else '⚠️ 警戒' if v>=20 else '✅ 正常'}")
    if data.get("y10") and data.get("y2"):
        print(f"  10Y-2Y 利差: {data['spread']} bps {'🚨 倒掛！' if data['spread']<0 else ''}")
    if data.get("cds"):
        print("  主權 CDS (bps):")
        for k, v in data["cds"].items():
            if v:
                print(f"    {v.get('flag','')} {v.get('name',k)}: {v['value']:.1f} bps")
            else:
                print(f"    {k}: ❌ 無資料")
    for sym, s in data.get("stocks", {}).items():
        if s:
            print(f"  {sym}: ${s['value']:.2f} ({s['chg_pct']:+.2f}%)")
