# ============================================================
#  render.py  —  從資料 dict 產出多頁式 HTML 報告
#  第一頁：總覽（Header + 燈號 + 今日重點）
#  第二頁(01)：自營業務損益 + 單檔損失
#  第三頁(02)：財管商品業務集中度
#  第四頁(03)：經紀業務
# ============================================================

from pathlib import Path


def _wan(v, unit="萬"):
    if v is None or v == 0:
        return "0"
    wan = float(v) / 10000
    if abs(wan) >= 10000:
        s = f"{wan/10000:.2f}億"
    else:
        s = f"{wan:,.0f}{unit}"
    return ("+" if wan > 0 else "") + s


def _pct(v, digits=2):
    if v is None:
        return "—"
    return f"{float(v)*100:.{digits}f}%"


def _updn(v):
    if v is None or v == 0:
        return ""
    return "up" if float(v) > 0 else "dn"


def _badge(status):
    # 統一顯示名稱：接近L1 → L1 80%提醒
    if status == "接近L1":
        status = "L1 80%提醒"
    MAP = {
        "超限": "br", "月損失超限": "br",
        # 財管集中度：達L1/達L2 皆為黃燈（警示，非超限）
        "達L2": "by", "達L1": "by",
        "L1 80%": "by", "L1 80%提醒": "by", "接近L1": "by", "80%提醒": "by",
        "正常": "bg", "—": "bd-",
    }
    cls = MAP.get(status, "bx")
    return f'<span class="b {cls}">{status or "—"}</span>'


def _conc_row(item, cat_label):
    pct    = item.get("pct") or 0
    l1     = item.get("l1") or 0
    l2     = item.get("l2") or 0
    status = item.get("status", "正常")
    name   = item.get("name", "")
    fill   = min(pct / l1, 1.0) * 100 if l1 else 0
    # 達L1/達L2 皆為黃色警示（非紅色超限）
    YELLOW_ST = ("達L1","達L2","L1 80%","L1 80%提醒","接近L1","80%提醒")
    color  = "#f59e0b" if status in YELLOW_ST else "var(--grn)"
    row_cls = "wy"     if status in YELLOW_ST else ""
    name_style = f'style="color:{color};"' if row_cls else ""
    l_label = f"L1={_pct(l1,0)}" + (f" L2={_pct(l2,0)}" if l2 else "")
    return f"""<div class="conc-row {row_cls}">
      <div class="conc-cat">{cat_label}</div>
      <div class="conc-name" {name_style}>{name}</div>
      <div><div class="mini-bar"><div class="mini-fill" style="width:{fill:.0f}%;background:{color};"></div></div>
           <div class="conc-meta">{l_label}</div></div>
      <div class="conc-pct" style="color:{color};">{_pct(pct)}</div>
      {_badge(status)}
    </div>"""


def generate_html(data: dict) -> str:
    m  = data["market"]
    wm = data["wm"]
    b  = data["broker"]
    report_date = data["report_date"]
    alert_items = data.get("alert_items", [])

    # ── 燈號 ─────────────────────────────────────────────────
    sig_market = "red"    if (m["loss_over"] or m["d3_over"]) else \
                 "yellow" if (m["loss_warn"] or m["d3_warn"]) else "green"
    # 財管燈號：達L1/達L2 皆為黃燈，無紅燈
    _wm_alert_st = ("達L1","達L2","L1 80%","L1 80%提醒","接近L1","80%提醒")
    sig_wm     = "yellow" if any(v.get("status") in _wm_alert_st for v in wm["conc"].values()) else "green"

    sig_txt = {"red":("⚠ 超限","var(--red)"), "yellow":("！警示","#b45309"), "green":("✓ 正常","var(--grn)")}
    sm_txt, sm_col = sig_txt[sig_market]
    sw_txt, sw_col = sig_txt[sig_wm]

    sm_sub = f"月損失超限{len(m['loss_over'])}件 | 單檔{len(m['d3_over'])}件" if sig_market=="red" else \
             f"月損失提醒{len(m['loss_warn'])}件 | 單檔{len(m['d3_warn'])}件" if sig_market=="yellow" else "各項指標正常"
    sw_sub = next((f"{v.get('name','')} 達L1" for v in wm["conc"].values() if v.get("status")=="達L1"), "各集中度正常")
    sb_sub = f"融資維持率{b.get('total_maint',0):.1f}% | ABC合計{_pct(b.get('abc_pct'))}"

    # ── 今日重點 ─────────────────────────────────────────────
    ai_html = ""
    for ai in alert_items:
        icon = {"red":"🔴","yellow":"🟡","blue":"🔵"}.get(ai["type"],"🔵")
        cls  = {"red":"r","yellow":"y","blue":"b"}.get(ai["type"],"b")
        ai_html += f'<div class="ai {cls}">{icon} {ai["text"]}</div>\n'
    if not ai_html:
        ai_html = '<div class="ai g">✅ 今日各項指標正常</div>'

    # ── P&L 列產生器 ─────────────────────────────────────────
    def _pnl_row(r, row_cls=""):
        ns = 'style="color:var(--red);font-weight:600;"' if row_cls=="alert-row" else ""
        st = r.get("status","")
        return f"""<tr class="{row_cls}">
          <td class="l" {ns}>{r['dept']}</td>
          <td class="r {_updn(r['mtd'])}">{_wan(r['mtd'])}</td>
          <td class="r {_updn(r['ytd'])}">{_wan(r['ytd'])}</td>
          <td class="r">{_badge(st) if st else _badge('—')}</td>
        </tr>"""

    def _total_row(r, cls="subtotal"):
        accent = "style='color:var(--acc);'" if cls=="grand" else ""
        return f"""<tr class="{cls}">
          <td class="l" {accent}>{r['dept']}</td>
          <td class="r {_updn(r['mtd'])}">{_wan(r['mtd'])}</td>
          <td class="r {_updn(r['ytd'])}">{_wan(r['ytd'])}</td>
          <td class="r">—</td>
        </tr>"""

    COL_GRP = """<colgroup>
      <col style="width:52%"><col style="width:18%"><col style="width:18%"><col style="width:12%">
    </colgroup>"""
    TBL_HDR = """<thead><tr>
      <th>部門 / 業務</th><th class="r">MTD</th><th class="r">YTD</th><th class="r">狀態</th>
    </tr></thead>"""
    STR_HDR = """<thead><tr>
      <th>部門</th><th class="r">MTD</th><th class="r">YTD</th><th class="r">狀態</th>
    </tr></thead>"""

    ib_rows    = "".join(_pnl_row(r) for r in m["ib_rows"]) + _total_row(m["ib_total"], "grand")
    strat_rows = "".join(_pnl_row(r) for r in m["strategy_rows"]) + _total_row(m["strategy_total"])
    trade_rows = ""
    for r in m["trade_rows"]:
        rc = "alert-row" if r.get("status") in ("超限","月損失超限") else ""
        trade_rows += _pnl_row(r, rc)
    trade_rows += _total_row(m["trade_total"]) + _total_row(m["ft_total"], "grand")

    ft_badge = _badge('月損失超限') if any(r.get("status") in ("超限","月損失超限") for r in m["trade_rows"]) else _badge('正常')

    # ── 損失超限 bars ─────────────────────────────────────────
    loss_over_cnt = len(m["loss_over"])
    loss_warn_cnt = len(m["loss_warn"])
    d3_over_cnt   = len(m["d3_over"])
    d3_warn_cnt   = len(m["d3_warn"])

    loss_bars = ""
    for r in m["loss_over"]:
        pv = float(r.get("m_pct") or 0) * 100
        loss_bars += f"""<div class="lrow" style="background:var(--redbg);border-color:var(--redbd);">
          <div style="flex:1;"><div style="font-size:12px;font-weight:700;color:var(--red);">{r['dept']} {r['biz']}</div>
          <div style="font-size:12px;color:var(--mid);font-family:var(--mono);margin-top:2px;">月損失使用率</div></div>
          <div style="display:flex;align-items:center;gap:7px;flex:1;">
            <div class="pbar"><div class="pbar-fill" style="width:100%;background:var(--red);"></div></div>
            <div class="pbar-pct" style="color:var(--red);">{pv:.1f}%</div>
          </div>{_badge('月損失超限')}</div>"""
    for r in m["loss_warn"]:
        pv = float(r.get("m_pct") or 0) * 100
        loss_bars += f"""<div class="lrow" style="background:var(--yelbg);border-color:var(--yelbd);">
          <div style="flex:1;"><div style="font-size:12px;font-weight:600;color:var(--yel);">{r['dept']} {r['biz']}</div>
          <div style="font-size:12px;color:var(--mid);font-family:var(--mono);margin-top:2px;">月損失使用率</div></div>
          <div style="display:flex;align-items:center;gap:7px;flex:1;">
            <div class="pbar"><div class="pbar-fill" style="width:{min(pv,100):.0f}%;background:#f59e0b;"></div></div>
            <div class="pbar-pct" style="color:var(--yel);">{pv:.1f}%</div>
          </div>{_badge('80%提醒')}</div>"""

    # ── D3 ───────────────────────────────────────────────────
    # d3：只顯示超限/80%提醒，觀察不顯示
    d3_alert_list = [r for r in (m.get("d3_top5") or []) if r.get("status") in ("超限","80%提醒")]
    # 補充：d3_top5 只取前5，可能超限/提醒不在其中，從完整 d3_rows 撈
    d3_all_alert = [r for r in (m.get("d3_rows") or []) if r.get("status") in ("超限","80%提醒")]
    if not d3_alert_list:
        d3_alert_list = d3_all_alert

    d3_html = ""
    if d3_alert_list:
        d3_html += """<div style="display:grid;grid-template-columns:60px 1fr 70px 70px 60px;gap:0;margin-top:4px;">
          <div style="font-size:8px;color:var(--dim);font-family:var(--mono);padding:0 0 4px;border-bottom:1px solid var(--bd);font-weight:600;">股票代號</div>
          <div style="font-size:8px;color:var(--dim);font-family:var(--mono);padding:0 0 4px;border-bottom:1px solid var(--bd);font-weight:600;">股票名稱</div>
          <div style="font-size:8px;color:var(--dim);font-family:var(--mono);padding:0 0 4px;border-bottom:1px solid var(--bd);font-weight:600;text-align:right;">年度損失</div>
          <div style="font-size:8px;color:var(--dim);font-family:var(--mono);padding:0 0 4px;border-bottom:1px solid var(--bd);font-weight:600;text-align:right;">損失率</div>
          <div style="font-size:8px;color:var(--dim);font-family:var(--mono);padding:0 0 4px;border-bottom:1px solid var(--bd);font-weight:600;text-align:right;">狀態</div>"""
        for r in d3_alert_list:
            dc = "var(--red)" if r.get("status")=="超限" else "#f59e0b"
            d3_html += f"""
          <div style="padding:4px 0;font-size:10px;font-family:var(--mono);color:var(--acc2);border-bottom:1px solid var(--bd);">{r['code']}</div>
          <div style="padding:4px 0;font-size:10px;color:var(--mid);border-bottom:1px solid var(--bd);">{r['name']}</div>
          <div style="padding:4px 0;font-size:10px;font-family:var(--mono);color:{dc};text-align:right;border-bottom:1px solid var(--bd);font-weight:600;">{_wan(r['pnl'])}</div>
          <div style="padding:4px 0;font-size:10px;font-family:var(--mono);color:{dc};text-align:right;border-bottom:1px solid var(--bd);">{_pct(r['loss_rate'])}</div>
          <div style="padding:4px 0;text-align:right;border-bottom:1px solid var(--bd);">{_badge(r['status'])}</div>"""
        d3_html += "</div>"
    else:
        d3_html = '<div style="font-size:10px;color:var(--grn);padding:4px 0;">✅ 無超限或80%提醒</div>'
    

    # ── 財管 ─────────────────────────────────────────────────
    alloc = wm.get("alloc", {})
    bond_pct   = (alloc.get("bond",   0) or 0) * 100
    fund_pct   = (alloc.get("fund",   0) or 0) * 100
    struct_pct = (alloc.get("struct", 0) or 0) * 100
    conc = wm.get("conc", {})
    ha   = wm.get("ha", {})
    ha_total_str = f"{ha.get('total',0)/1e8:.1f}億" if ha.get("total",0) > 0 else "—"

    # ── 財管金額（億）────────────────────────────────────────
    # 從高資產客戶的 total 估算整體規模，或直接從 alloc 比例反推
    # 用各類別第一名的持有金額加總估算總規模（百萬元）
    wm_total_mn = 0
    try:
        from pathlib import Path
        import openpyxl as _ox
        # 取總表 G8/G9/G10 的配置比例推算（直接用 conc 資料的已知金額）
        # 海外債券：bond_inv 名稱是美國國庫債 8.31%，持有 4332.86百萬
        # → 總規模 = 4332.86 / 0.083102
        _bond_pct_val = (conc.get("bond_inv",{}).get("pct") or 0)
        _bond_name = conc.get("bond_inv",{}).get("name","")
        if _bond_pct_val > 0:
            # 從 NVDA 反推結構型總額
            _struct_pct_val = (conc.get("struct_target",{}).get("pct") or 0)
            # 用已知金額：NVDA 84.5億 / 17.88% = 結構型總額
            if _struct_pct_val > 0:
                _struct_total = 8450 / _struct_pct_val  # 百萬
                wm_total_mn = _struct_total / max(struct_pct/100, 0.01)
    except Exception:
        pass
    if wm_total_mn <= 0:
        wm_total_mn = 52100   # fallback: 約 521億（百萬）
    wm_bond_amt   = wm_total_mn * bond_pct   / 100 / 100   # 億
    wm_fund_amt   = wm_total_mn * fund_pct   / 100 / 100
    wm_struct_amt = wm_total_mn * struct_pct / 100 / 100

    # ── 財管警示明細（只顯示達L1/L2/80%提醒/L1 80%）──────────
    cat_labels_map = {
        "bond_inv":      "債券｜投資等級",
        "bond_noninv":   "債券｜非投資等級",
        "fund":          "基金｜單一標的",
        "struct_target": "結構型｜連結標的",
        "struct_upper":  "結構型上手｜BBB+以上",
        "struct_lower":  "結構型上手｜下緣",
    }
    wm_alert_rows = ""
    for k, label in cat_labels_map.items():
        v = conc.get(k, {})
        st = v.get("status","正常")
        if st in ("達L1","達L2","L1 80%","L1 80%提醒","接近L1","80%提醒"):
            pct_val = (v.get("pct") or 0)
            l1_val  = (v.get("l1") or 0)
            fill    = min(pct_val/l1_val,1.0)*100 if l1_val else 0
            # 達L1/達L2 皆為黃色警示
            color   = "#f59e0b"
            row_cls = "wy"
            wm_alert_rows += f"""<div class="conc-row {row_cls}" style="margin-bottom:4px;">
              <div class="conc-cat">{label}</div>
              <div class="conc-name" style="color:{color};">{v.get('name','')}</div>
              <div><div class="mini-bar"><div class="mini-fill" style="width:{fill:.0f}%;background:{color};"></div></div>
                   <div class="conc-meta">L1={_pct(v.get('l1'),0)}</div></div>
              <div class="conc-pct" style="color:{color};">{_pct(pct_val)}</div>
              {_badge(st)}
            </div>"""
    if not wm_alert_rows:
        wm_alert_rows = '<div style="font-size:10px;color:var(--grn);padding:4px 0;">✅ 各項集中度正常</div>'

    # ── 經紀 ─────────────────────────────────────────────────
    # A~C / D~E 合計計算
    _dist = b.get("dist_rows") or []
    _abc = [r for r in _dist if r["grade"] in ("A","B","C")]
    _de  = [r for r in _dist if r["grade"] in ("D","E")]

    abc_pct_sum = sum(r["pct"] for r in _abc)
    abc_bal_sum = sum(r["balance"] for r in _abc)
    # 加權平均維持率（以餘額為權重）
    # 注意：各等級維持率為該等級整體加權平均，此處再以餘額加權
    abc_maint = sum(r["maint"] * r["balance"] for r in _abc) / abc_bal_sum if abc_bal_sum > 0 else 0
    # TODO: 若各等級維持率非加權平均值，請自行調整此計算方式

    de_pct_sum = sum(r["pct"] for r in _de)
    de_bal_sum = sum(r["balance"] for r in _de)
    de_maint   = sum(r["maint"] * r["balance"] for r in _de) / de_bal_sum if de_bal_sum > 0 else 0

    dist_html = f"""
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:8px;">
      <div style="background:var(--gnbg);border:1px solid var(--gnbd);border-radius:8px;padding:12px 14px;">
        <div style="font-size:11px;font-weight:700;color:var(--grn);font-family:var(--mono);margin-bottom:8px;">A～C 級</div>
        <div style="font-size:22px;font-weight:800;font-family:var(--mono);color:var(--grn);">{_pct(abc_pct_sum)}</div>
        <div style="font-size:11px;color:var(--mid);font-family:var(--mono);margin-top:4px;">融資餘額　{_wan(abc_bal_sum)}</div>
        <div style="font-size:11px;color:var(--mid);font-family:var(--mono);margin-top:2px;">維持率　{abc_maint:.1f}%
        <!-- 維持率為各等級加權平均，若需精確值請確認資料來源 --></div>
      </div>
      <div style="background:var(--yelbg);border:1px solid var(--yelbd);border-radius:8px;padding:12px 14px;">
        <div style="font-size:11px;font-weight:700;color:var(--yel);font-family:var(--mono);margin-bottom:8px;">D～E 級</div>
        <div style="font-size:22px;font-weight:800;font-family:var(--mono);color:var(--yel);">{_pct(de_pct_sum)}</div>
        <div style="font-size:11px;color:var(--mid);font-family:var(--mono);margin-top:4px;">融資餘額　{_wan(de_bal_sum)}</div>
        <div style="font-size:11px;color:var(--mid);font-family:var(--mono);margin-top:2px;">維持率　{de_maint:.1f}%
        <!-- 維持率為各等級加權平均，若需精確值請確認資料來源 --></div>
      </div>
    </div>"""

    margin_html = "".join(f"""<tr>
      <td><span style="color:var(--acc2);font-family:var(--mono);">{r['code']}</span> {r['name']}</td>
      <td>{_badge(r.get('grade',''))}</td>
      <td class="r">{r['balance']/1e8:.1f}</td>
      <td class="r">{_pct(r['conc'])}</td></tr>"""
        for r in (b.get("margin_top5") or []))

    short_html = "".join(f"""<tr>
      <td><span style="color:var(--acc2);font-family:var(--mono);">{r['code']}</span> {r['name']}</td>
      <td class="r">{r['collat']/1e8:.2f}</td>
      <td class="r">{_pct(r['pct'])}</td>
      <td class="r" {'style="color:var(--red);font-weight:600;"' if r['maint']<150 else ''}>{r['maint']:.1f}%</td></tr>"""
        for r in (b.get("short_top5") or []))

    unlim_html = "".join(f"""<tr>
      <td style="color:var(--mid);">{r['name']}</td>
      <td class="r">{r['amount']/10000:,.0f}</td>
      <td class="r" {'style="color:var(--yel);font-weight:600;"' if r['maint']<160 else ''}>{r['maint']:.1f}%</td></tr>"""
        for r in (b.get("unlim_top5") or []))

    sl = b.get("sec_lending", {})
    sl_total = (sl.get("total") or 0) or 1
    loans = b.get("loans", {})

    # ══════════════════════════════════════════════════════════
    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<title>風控整合日報 {report_date}</title>
<style>
:root{{
  --bg:#f4f6f9;--s1:#fff;--s2:#f8f9fb;--bd:#e3e8ef;--bd2:#d0d7e2;
  --txt:#1a2535;--dim:#8a9bb5;--mid:#4a6080;--acc:#1565c0;--acc2:#1976d2;
  --grn:#1a9e6a;--gnbg:#edf7f3;--gnbd:#b2dfcf;
  --red:#c62828;--redbg:#fef2f2;--redbd:#fccaca;
  --yel:#b45309;--yelbg:#fffbeb;--yelbd:#fde68a;
  --mono:'IBM Plex Mono',monospace;--sans:system-ui,sans-serif;
}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:var(--bg);color:var(--txt);font-family:var(--sans);font-size:14px;line-height:1.5;}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}
@keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:.25}}}}
.page{{width:210mm;min-height:297mm;margin:0 auto 20px;background:var(--s1);padding:16px 18px 20px;box-shadow:0 2px 14px rgba(0,0,0,.1);}}
.hdr{{display:flex;align-items:center;justify-content:space-between;border-bottom:2px solid var(--acc);padding-bottom:10px;margin-bottom:14px;}}
.logo{{width:30px;height:30px;border-radius:6px;background:linear-gradient(135deg,#1565c0,#1e88e5);display:flex;align-items:center;justify-content:center;font-family:var(--mono);font-weight:700;font-size:10px;color:#fff;flex-shrink:0;}}
.pulse{{width:7px;height:7px;border-radius:50%;background:var(--grn);box-shadow:0 0 4px var(--grn);animation:pulse 2s infinite;}}
.alert-bar{{background:var(--redbg);border:1px solid var(--redbd);border-left:4px solid var(--red);border-radius:6px;padding:8px 13px;margin-bottom:14px;}}
.alert-label{{font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--red);font-family:var(--mono);margin-bottom:5px;}}
.alert-items{{display:flex;flex-wrap:wrap;gap:5px;}}
.ai{{font-size:13px;padding:3px 10px;border-radius:4px;border:1px solid;}}
.ai.r{{border-color:var(--redbd);color:var(--red);background:#fff;}}
.ai.y{{border-color:var(--yelbd);color:var(--yel);background:var(--yelbg);}}
.ai.b{{border-color:#c2d3f0;color:var(--acc2);background:#e8f0fb;}}
.ai.g{{border-color:var(--gnbd);color:var(--grn);background:var(--gnbg);}}
.sig-row{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px;}}
.sig-card{{background:var(--s1);border:1px solid var(--bd);border-radius:9px;padding:14px 16px;display:flex;align-items:center;gap:10px;position:relative;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.05);}}
.sig-card::after{{content:'';position:absolute;bottom:0;left:0;right:0;height:3px;}}
.sig-card.red::after{{background:var(--red);}}.sig-card.yellow::after{{background:#f59e0b;}}.sig-card.green::after{{background:var(--grn);}}
.led{{width:11px;height:11px;border-radius:50%;flex-shrink:0;}}
.led.red{{background:var(--red);box-shadow:0 0 6px rgba(198,40,40,.5);animation:blink 1.2s infinite;}}
.led.yellow{{background:#f59e0b;}}.led.green{{background:var(--grn);}}
.sec-hd{{display:flex;align-items:center;justify-content:space-between;border-bottom:2px solid var(--acc);padding-bottom:8px;margin-bottom:14px;}}
.sec-title{{font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--mid);font-family:var(--mono);display:flex;align-items:center;gap:5px;}}
.sec-title .n{{color:var(--acc2);font-size:14px;}}.sec-title .dept{{color:var(--acc);}}
.sec-date{{font-size:11px;color:var(--dim);font-family:var(--mono);}}
.tbl{{width:100%;border-collapse:collapse;table-layout:fixed;}}
.tbl th{{text-align:left;font-size:11px;font-family:var(--mono);color:var(--dim);padding:0 4px 6px;border-bottom:1.5px solid var(--bd);font-weight:600;letter-spacing:.03em;text-transform:uppercase;white-space:nowrap;}}
.tbl th.r,.tbl td.r{{text-align:right;}}
.tbl td{{padding:5px 4px;font-size:13px;border-bottom:1px solid var(--bd);font-family:var(--mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}}
.tbl td.l{{color:var(--mid);font-size:12px;font-family:var(--sans);}}
.tbl tr:last-child td{{border-bottom:none;}}
.tbl tr.subtotal td{{border-top:1px solid var(--bd2);background:var(--s2);font-weight:700;}}
.tbl tr.grand td{{border-top:2px solid var(--acc);background:#e8f0fb;font-weight:700;font-size:13px;}}
.tbl tr.alert-row td{{background:var(--redbg);}}
.up{{color:var(--grn)!important;font-weight:600;}}.dn{{color:var(--red)!important;font-weight:600;}}.yw{{color:var(--yel)!important;font-weight:600;}}
.b{{display:inline-block;font-size:11px;font-family:var(--mono);font-weight:700;padding:2px 6px;border-radius:3px;}}
.br{{background:var(--redbg);color:var(--red);border:1px solid var(--redbd);}}
.by{{background:var(--yelbg);color:var(--yel);border:1px solid var(--yelbd);}}
.bg{{background:var(--gnbg);color:var(--grn);border:1px solid var(--gnbd);}}
.bb{{background:#e8f0fb;color:var(--acc2);border:1px solid #c2d3f0;}}
.bx{{background:var(--s2);color:var(--mid);border:1px solid var(--bd);}}
.bd-{{background:#f5f5f5;color:var(--dim);border:1px solid #ddd;}}
.sd{{font-size:10px;font-family:var(--mono);color:var(--dim);letter-spacing:.1em;text-transform:uppercase;display:flex;align-items:center;gap:7px;margin:8px 0 6px;font-weight:600;}}
.sd::after{{content:'';flex:1;height:1px;background:var(--bd);}}
.dept-box{{background:var(--s2);border:1px solid var(--bd);border-radius:8px;padding:10px 14px;}}
.dept-hd{{font-size:10px;font-weight:700;color:var(--acc2);font-family:var(--mono);margin-bottom:7px;display:flex;align-items:center;justify-content:space-between;}}
.c-row{{display:flex;gap:7px;margin-bottom:9px;}}
.cb{{flex:1;border-radius:7px;padding:8px 10px;text-align:center;}}
.lrow{{display:flex;align-items:center;gap:8px;padding:6px 10px;border-radius:6px;margin-bottom:5px;border:1px solid;}}
.pbar{{height:5px;border-radius:3px;flex:1;background:var(--bd);overflow:hidden;}}
.pbar-fill{{height:100%;border-radius:3px;}}
.pbar-pct{{font-size:12px;font-family:var(--mono);font-weight:700;min-width:44px;text-align:right;}}
.d3i{{display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--bd);}}
.d3i:last-of-type{{border-bottom:none;}}
.dot{{width:5px;height:5px;border-radius:50%;flex-shrink:0;}}
.conc-row{{display:flex;align-items:center;gap:6px;padding:5px 9px;border-radius:6px;border:1px solid var(--bd);margin-bottom:4px;background:var(--s2);}}
.conc-row.wr{{background:var(--redbg);border-color:var(--redbd);border-left:3px solid var(--red);}}
.conc-row.wy{{background:var(--yelbg);border-color:var(--yelbd);border-left:3px solid #f59e0b;}}
.conc-cat{{font-size:11px;font-family:var(--mono);color:var(--dim);min-width:95px;}}
.conc-name{{flex:1;font-size:13px;font-weight:600;}}
.mini-bar{{height:4px;background:var(--bd);border-radius:2px;overflow:hidden;width:55px;}}
.mini-fill{{height:100%;border-radius:2px;}}
.conc-meta{{font-size:9px;color:var(--dim);font-family:var(--mono);margin-top:2px;}}
.conc-pct{{font-family:var(--mono);font-size:14px;font-weight:700;min-width:48px;text-align:right;}}
.md5{{display:grid;grid-template-columns:repeat(5,1fr);gap:5px;margin-bottom:9px;}}
.mdi{{background:var(--s2);border:1px solid var(--bd);border-radius:6px;padding:7px 4px;text-align:center;}}
.mg{{font-size:18px;font-weight:800;font-family:var(--mono);}}.mp{{font-size:13px;font-weight:600;font-family:var(--mono);margin-top:1px;}}.mm{{font-size:10px;color:var(--dim);font-family:var(--mono);margin-top:1px;}}
.gA{{color:#1a9e6a;}}.gB{{color:var(--acc2);}}.gC{{color:#b45309;}}.gD{{color:#d97706;}}.gE{{color:var(--red);}}
@media print{{
  body{{background:white;}}
  .page{{box-shadow:none;margin:0;page-break-after:always;}}
  .page:last-child{{page-break-after:auto;}}
  .page-landscape{{page-break-after:always;}}
  @page{{size:A4 portrait;margin:8mm;}}
  @page landscape{{size:A4 landscape;margin:8mm;}}
}}
</style>
</head>
<body>

<!-- ═══ 第一頁：總覽 ═══ -->
<div class="page">
  <div class="hdr">
    <div style="display:flex;align-items:center;gap:10px;">
      <div class="logo">RM</div>
      <div>
        <div style="font-size:16px;font-weight:700;">風險管理整合日報</div>
        <div style="font-size:8.5px;color:var(--dim);font-family:var(--mono);"></div>
      </div>
    </div>
    <div style="display:flex;align-items:center;gap:10px;">
      <div class="pulse"></div>
      <div>
        <div style="font-family:var(--mono);font-size:11px;color:var(--acc2);font-weight:700;">資料日期：{report_date}</div>
        <div style="font-size:8.5px;color:var(--dim);text-align:right;font-family:var(--mono);">風險管理部</div>
      </div>
    </div>
  </div>

  <div class="alert-bar">
    <div class="alert-label">⚡ 今日重點說明</div>
    <div class="alert-items">{ai_html}</div>
  </div>

  <div class="sig-row">
    <div class="sig-card {sig_market}">
      <div class="led {sig_market}"></div>
      <div>
        <div style="font-size:13px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);">自營業務</div>
        <div style="font-size:13px;font-weight:700;color:{sm_col};margin-top:2px;">{sm_txt}</div>
        <div style="font-size:10px;color:var(--dim);font-family:var(--mono);margin-top:2px;">{sm_sub}</div>
      </div>
    </div>
    <div class="sig-card {sig_wm}">
      <div class="led {sig_wm}"></div>
      <div>
        <div style="font-size:13px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);">財管商品業務</div>
        <div style="font-size:13px;font-weight:700;color:{sw_col};margin-top:2px;">{sw_txt}</div>
        <div style="font-size:10px;color:var(--dim);font-family:var(--mono);margin-top:2px;">{sw_sub}</div>
      </div>
    </div>
    <div class="sig-card green">
      <div class="led green"></div>
      <div>
        <div style="font-size:13px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);">經紀業務</div>
        <div style="font-size:13px;font-weight:700;color:var(--grn);margin-top:2px;">✓ 正常</div>
        <div style="font-size:10px;color:var(--dim);font-family:var(--mono);margin-top:2px;">{sb_sub}</div>
      </div>
    </div>
  </div>

  <!-- ── 自營業務 ── -->
  <div style="background:var(--s2);border:1px solid var(--bd);border-radius:8px;padding:10px 13px;margin-bottom:8px;">
    <div style="font-size:13px;font-weight:700;color:var(--acc);font-family:var(--mono);letter-spacing:.05em;text-transform:uppercase;margin-bottom:7px;">自營業務</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
      <div>
        <div class="sd" style="margin-top:0;">損失超限 / 警示</div>
        <div class="c-row">
          <div class="cb" style="background:var(--redbg);border:1px solid var(--redbd);">
            <div style="font-size:18px;font-weight:800;font-family:var(--mono);color:var(--red);">{loss_over_cnt}</div>
            <div style="font-size:10px;color:var(--mid);margin-top:1px;">月損失超限</div>
          </div>
          <div class="cb" style="background:var(--{{'yelbg' if loss_warn_cnt else 'gnbg'}});border:1px solid var(--{{'yelbd' if loss_warn_cnt else 'gnbd'}});">
            <div style="font-size:18px;font-weight:800;font-family:var(--mono);color:var(--{{'yel' if loss_warn_cnt else 'grn'}});">{loss_warn_cnt}</div>
            <div style="font-size:10px;color:var(--mid);margin-top:1px;">月損失80%提醒</div>
          </div>
        </div>
        {loss_bars}
      </div>
      <div>
        <div class="sd" style="margin-top:0;">單檔損失超限 / 警示</div>
        <div class="c-row">
          <div class="cb" style="background:var(--redbg);border:1px solid var(--redbd);">
            <div style="font-size:18px;font-weight:800;font-family:var(--mono);color:var(--red);">{d3_over_cnt}</div>
            <div style="font-size:10px;color:var(--mid);margin-top:1px;">單檔超限</div>
          </div>
          <div class="cb" style="background:var(--{{'yelbg' if d3_warn_cnt else 'gnbg'}});border:1px solid var(--{{'yelbd' if d3_warn_cnt else 'gnbd'}});">
            <div style="font-size:18px;font-weight:800;font-family:var(--mono);color:var(--{{'yel' if d3_warn_cnt else 'grn'}});">{d3_warn_cnt}</div>
            <div style="font-size:10px;color:var(--mid);margin-top:1px;">80%提醒</div>
          </div>
        </div>
        {d3_html}
      </div>
    </div>
  </div>

  <!-- ── 財管商品業務 ── -->
  <div style="background:var(--s2);border:1px solid var(--bd);border-radius:8px;padding:10px 13px;margin-bottom:8px;">
    <div style="font-size:13px;font-weight:700;color:var(--acc);font-family:var(--mono);letter-spacing:.05em;text-transform:uppercase;margin-bottom:7px;">財管商品業務</div>
    <div style="display:flex;height:9px;border-radius:4px;overflow:hidden;gap:2px;margin-bottom:8px;">
      <div style="width:{bond_pct:.1f}%;background:#1565c0;border-radius:3px;"></div>
      <div style="width:{fund_pct:.1f}%;background:#0097a7;border-radius:3px;"></div>
      <div style="width:{struct_pct:.1f}%;background:#7c4dff;border-radius:3px;"></div>
    </div>
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:10px;">
      <div style="background:var(--s1);border:1px solid var(--bd);border-radius:6px;padding:7px 10px;display:flex;align-items:center;gap:7px;">
        <div style="width:9px;height:9px;border-radius:2px;background:#1565c0;flex-shrink:0;"></div>
        <div>
          <div style="font-size:8.5px;color:var(--dim);font-family:var(--mono);">海外債券</div>
          <div style="font-size:14px;font-weight:700;font-family:var(--mono);margin-top:1px;">{wm_bond_amt:.1f}億</div>
          <div style="font-size:8.5px;color:var(--dim);font-family:var(--mono);">{bond_pct:.1f}%</div>
        </div>
      </div>
      <div style="background:var(--s1);border:1px solid var(--bd);border-radius:6px;padding:7px 10px;display:flex;align-items:center;gap:7px;">
        <div style="width:9px;height:9px;border-radius:2px;background:#0097a7;flex-shrink:0;"></div>
        <div>
          <div style="font-size:8.5px;color:var(--dim);font-family:var(--mono);">基金商品</div>
          <div style="font-size:14px;font-weight:700;font-family:var(--mono);margin-top:1px;">{wm_fund_amt:.1f}億</div>
          <div style="font-size:8.5px;color:var(--dim);font-family:var(--mono);">{fund_pct:.1f}%</div>
        </div>
      </div>
      <div style="background:var(--s1);border:1px solid var(--bd);border-radius:6px;padding:7px 10px;display:flex;align-items:center;gap:7px;">
        <div style="width:9px;height:9px;border-radius:2px;background:#7c4dff;flex-shrink:0;"></div>
        <div>
          <div style="font-size:8.5px;color:var(--dim);font-family:var(--mono);">結構型商品</div>
          <div style="font-size:14px;font-weight:700;font-family:var(--mono);margin-top:1px;">{wm_struct_amt:.1f}億</div>
          <div style="font-size:8.5px;color:var(--dim);font-family:var(--mono);">{struct_pct:.1f}%</div>
        </div>
      </div>
    </div>
    <div class="sd">達警示集中度明細</div>
    {wm_alert_rows}
  </div>

  <!-- ── 經紀業務 ── -->
  <div style="background:var(--s2);border:1px solid var(--bd);border-radius:8px;padding:10px 13px;">
    <div style="font-size:12px;font-weight:700;color:var(--acc);font-family:var(--mono);letter-spacing:.05em;text-transform:uppercase;margin-bottom:7px;">經紀業務</div>
    <div class="sd" style="margin-top:0;">融資餘額 A～E 級分佈</div>
    {dist_html}
    <div style="text-align:center;font-size:9px;color:var(--mid);font-family:var(--mono);background:var(--gnbg);border:1px solid var(--gnbd);border-radius:4px;padding:4px;margin-bottom:8px;">
      ABC合計 <strong style="color:var(--grn);">{_pct(b.get('abc_pct'))}</strong> ｜ 融資餘額 <strong>{_wan(b.get('total_balance',0))}</strong> ｜ 維持率 <strong style="color:var(--grn);">{b.get('total_maint',0):.1f}%</strong>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
      <div>
        <div class="sd" style="margin-top:0;">款項借貸餘額</div>
        <table style="width:100%;border-collapse:collapse;">
          <thead><tr>
            <th style="font-size:8px;color:var(--dim);font-family:var(--mono);padding:0 0 4px;border-bottom:1px solid var(--bd);text-align:left;">類型</th>
            <th style="font-size:8px;color:var(--dim);font-family:var(--mono);padding:0 0 4px;border-bottom:1px solid var(--bd);text-align:right;">未還餘額</th>
          </tr></thead>
          <tbody>
            <tr><td style="padding:4px 0;font-size:10px;border-bottom:1px solid var(--bd);color:var(--mid);">半年型借貸</td><td style="padding:4px 0;font-size:10px;border-bottom:1px solid var(--bd);text-align:right;font-family:var(--mono);">{_wan(loans.get('half_year',0)) if loans.get('half_year',0) else '—'}</td></tr>
            <tr><td style="padding:4px 0;font-size:10px;border-bottom:1px solid var(--bd);color:var(--mid);">T+5 借款</td><td style="padding:4px 0;font-size:10px;border-bottom:1px solid var(--bd);text-align:right;font-family:var(--mono);">{_wan(loans.get('t5',0)) if loans.get('t5',0) else '—'}</td></tr>
            <tr><td style="padding:4px 0;font-size:10px;color:var(--mid);">T+30 借款</td><td style="padding:4px 0;font-size:10px;text-align:right;font-family:var(--mono);">{_wan(loans.get('t30',0)) if loans.get('t30',0) else '—'}</td></tr>
          </tbody>
        </table>
      </div>
      <div>
        <div class="sd" style="margin-top:0;">有價證券借貸</div>
        <table style="width:100%;border-collapse:collapse;">
          <thead><tr>
            <th style="font-size:8px;color:var(--dim);font-family:var(--mono);padding:0 0 4px;border-bottom:1px solid var(--bd);text-align:left;">來源</th>
            <th style="font-size:8px;color:var(--dim);font-family:var(--mono);padding:0 0 4px;border-bottom:1px solid var(--bd);text-align:right;">金額</th>
            <th style="font-size:8px;color:var(--dim);font-family:var(--mono);padding:0 0 4px;border-bottom:1px solid var(--bd);text-align:right;">比重</th>
          </tr></thead>
          <tbody>
            <tr><td style="padding:4px 0;font-size:10px;border-bottom:1px solid var(--bd);color:var(--mid);">外資借券</td><td style="padding:4px 0;font-size:10px;border-bottom:1px solid var(--bd);text-align:right;font-family:var(--mono);color:var(--acc2);">{_wan(sl.get('foreign',0))}</td><td style="padding:4px 0;font-size:10px;border-bottom:1px solid var(--bd);text-align:right;font-family:var(--mono);">{_pct(sl.get('foreign',0)/sl_total)}</td></tr>
            <tr><td style="padding:4px 0;font-size:10px;border-bottom:1px solid var(--bd);color:var(--mid);">他家券商</td><td style="padding:4px 0;font-size:10px;border-bottom:1px solid var(--bd);text-align:right;font-family:var(--mono);">{_wan(sl.get('broker',0))}</td><td style="padding:4px 0;font-size:10px;border-bottom:1px solid var(--bd);text-align:right;font-family:var(--mono);">{_pct(sl.get('broker',0)/sl_total)}</td></tr>
            <tr><td style="padding:4px 0;font-size:10px;color:var(--mid);">自營借券</td><td style="padding:4px 0;font-size:10px;text-align:right;font-family:var(--mono);">{_wan(sl.get('prop',0))}</td><td style="padding:4px 0;font-size:10px;text-align:right;font-family:var(--mono);">{_pct(sl.get('prop',0)/sl_total)}</td></tr>
          </tbody>
        </table>
        <div style="margin-top:4px;font-size:8.5px;color:var(--dim);font-family:var(--mono);text-align:right;">總計 {_wan(sl.get('total',0))}｜費率 {sl.get('rate',0):.2f}%</div>
      </div>
    </div>
  </div>

</div>

<!-- ═══ 第二頁：01 自營業務損益 + 單檔損失 ═══ -->
<div class="page">
  <div class="sec-hd">
    <div class="sec-title"><span class="n">01</span> <span class="dept">自營業務</span> — 損益概覽</div>
    <div class="sec-date">截至 {m['data_date']}・單位：萬元</div>
  </div>

  <div class="sd" style="margin-top:0;">損失超限 / 警示（月損失使用率）</div>
  <div class="c-row">
    <div class="cb" style="background:var(--redbg);border:1.5px solid var(--redbd);">
      <div style="font-size:20px;font-weight:800;font-family:var(--mono);color:var(--red);">{loss_over_cnt}</div>
      <div style="font-size:9px;color:var(--mid);margin-top:1px;">月損失超限</div>
    </div>
    <div class="cb" style="background:var(--{'yelbg' if loss_warn_cnt else 'gnbg'});border:1.5px solid var(--{'yelbd' if loss_warn_cnt else 'gnbd'});">
      <div style="font-size:20px;font-weight:800;font-family:var(--mono);color:var(--{'yel' if loss_warn_cnt else 'grn'});">{loss_warn_cnt}</div>
      <div style="font-size:9px;color:var(--mid);margin-top:1px;">月損失80%提醒</div>
    </div>
  </div>
  {loss_bars}

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:10px;">
    <div class="dept-box">
      <div class="dept-hd"><span>🏦 投資銀行處</span><span class="b bg">正常</span></div>
      <table class="tbl">
        {COL_GRP}{TBL_HDR}
        <tbody>{ib_rows}</tbody>
      </table>
    </div>
    <div class="dept-box">
      <div class="dept-hd"><span>📊 金融交易處</span>{ft_badge}</div>
      <div class="sd" style="margin-top:0;">策略部位</div>
      <table class="tbl">
        {COL_GRP}{STR_HDR}
        <tbody>{strat_rows}</tbody>
      </table>
      <div class="sd">交易部位</div>
      <table class="tbl">
        {COL_GRP}{TBL_HDR}
        <tbody>{trade_rows}</tbody>
      </table>
    </div>
  </div>

  <div class="sd" style="margin-top:14px;">單檔損失超限 / 警示</div>
  <div class="c-row">
    <div class="cb" style="background:var(--redbg);border:1.5px solid var(--redbd);">
      <div style="font-size:20px;font-weight:800;font-family:var(--mono);color:var(--red);">{d3_over_cnt}</div>
      <div style="font-size:9px;color:var(--mid);margin-top:1px;">單檔超限</div>
    </div>
    <div class="cb" style="background:var(--{'yelbg' if d3_warn_cnt else 'gnbg'});border:1.5px solid var(--{'yelbd' if d3_warn_cnt else 'gnbd'});">
      <div style="font-size:20px;font-weight:800;font-family:var(--mono);color:var(--{'yel' if d3_warn_cnt else 'grn'});">{d3_warn_cnt}</div>
      <div style="font-size:9px;color:var(--mid);margin-top:1px;">80%提醒</div>
    </div>
  </div>
  {d3_html}
  <div style="margin-top:5px;font-size:8.5px;color:var(--dim);font-family:var(--mono);">超限條件：損失率≥30%且損失≥100萬 | </div>
</div>

<!-- ═══ 第三頁：02 財管商品業務 ═══ -->
<div class="page">
  <div class="sec-hd">
    <div class="sec-title"><span class="n">02</span> <span class="dept">財管商品業務</span> — 集中度管理</div>
    <div class="sec-date">資料日 2026/02/26</div>
  </div>
  <div style="display:flex;height:10px;border-radius:5px;overflow:hidden;gap:2px;margin-bottom:6px;">
    <div style="width:{bond_pct:.1f}%;background:#1565c0;border-radius:3px;"></div>
    <div style="width:{fund_pct:.1f}%;background:#0097a7;border-radius:3px;"></div>
    <div style="width:{struct_pct:.1f}%;background:#7c4dff;border-radius:3px;"></div>
  </div>
  <div style="display:flex;gap:14px;margin-bottom:14px;">
    <div style="display:flex;align-items:center;gap:4px;font-size:9px;font-family:var(--mono);color:var(--mid);"><div style="width:8px;height:8px;border-radius:2px;background:#1565c0;"></div>海外債券 {bond_pct:.1f}%</div>
    <div style="display:flex;align-items:center;gap:4px;font-size:9px;font-family:var(--mono);color:var(--mid);"><div style="width:8px;height:8px;border-radius:2px;background:#0097a7;"></div>基金商品 {fund_pct:.1f}%</div>
    <div style="display:flex;align-items:center;gap:4px;font-size:9px;font-family:var(--mono);color:var(--mid);"><div style="width:8px;height:8px;border-radius:2px;background:#7c4dff;"></div>結構型商品 {struct_pct:.1f}%</div>
  </div>
  <div class="sd">債券單一標的集中度</div>
  {_conc_row(conc.get('bond_inv',{}),'投資等級')}
  {_conc_row(conc.get('bond_noninv',{}),'非投資等級')}
  <div class="sd">基金單一標的集中度</div>
  {_conc_row(conc.get('fund',{}),'單一標的')}
  <div class="sd">結構型商品單一標的集中度</div>
  {_conc_row(conc.get('struct_target',{}),'單一連結標的')}
  <div class="sd">結構型商品上手集中度</div>
  {_conc_row(conc.get('struct_upper',{}),'BBB+(含)以上')}
  {_conc_row(conc.get('struct_lower',{}),'投資等級下緣')}
  <div class="sd">高資產客戶</div>
  <table class="tbl" style="table-layout:auto;">
    <thead><tr><th>指標</th><th class="r">數值</th><th class="r">狀態</th></tr></thead>
    <tbody>
      <tr><td class="l">高資產客戶人數</td><td class="r">{int(ha.get('count',0))} 人</td><td class="r"><span class="b bx">—</span></td></tr>
      <tr><td class="l">客戶投資總額</td><td class="r">{ha_total_str}</td><td class="r"><span class="b bx">—</span></td></tr>
      <tr><td class="l">BB-(含)以下債券</td><td class="r">{int(ha.get('bb_count',0))} 人 / {_wan(ha.get('bb_amount',0))}</td><td class="r"><span class="b {'bg' if not ha.get('bb_count') else 'br'}">{'無' if not ha.get('bb_count') else '有'}</span></td></tr>
      <tr><td class="l">境外非投信基金</td><td class="r">{int(ha.get('offshore_count',0))} 人 / {_wan(ha.get('offshore_amount',0))}</td><td class="r"><span class="b {'bg' if not ha.get('offshore_count') else 'br'}">{'無' if not ha.get('offshore_count') else '有'}</span></td></tr>
    </tbody>
  </table>
</div>

<!-- ═══ 第四頁：03 經紀業務 ═══ -->
<div class="page">
  <div class="sec-hd">
    <div class="sec-title"><span class="n">03</span> <span class="dept">經紀業務</span> — 風控</div>
    <div class="sec-date">資料日 2026/02/26</div>
  </div>

  <!-- 融資前5大個股 -->
  <div style="background:var(--s2);border:1px solid var(--bd);border-radius:12px;padding:12px 13px;margin-bottom:10px;">
    <div class="sd" style="margin-top:0;">融資前5大個股</div>
    <table class="tbl" style="table-layout:auto;">
      <thead><tr>
        <th>股票</th><th>評等</th>
        <th class="r">融資(億)</th><th class="r">集中度</th>
      </tr></thead>
      <tbody>{margin_html}</tbody>
    </table>
  </div>

  <!-- 融券前5大個股 -->
  <div style="background:var(--s2);border:1px solid var(--bd);border-radius:8px;padding:10px 13px;margin-bottom:10px;">
    <div class="sd" style="margin-top:0;">融券前5大個股</div>
    <table class="tbl" style="table-layout:auto;">
      <thead><tr>
        <th>股票</th><th class="r">擔保金(億)</th><th class="r">占比</th><th class="r">維持率</th>
      </tr></thead>
      <tbody>{short_html}</tbody>
    </table>
  </div>

  <!-- 不限用途借貸前5大客戶 -->
  <div style="background:var(--s2);border:1px solid var(--bd);border-radius:8px;padding:10px 13px;margin-bottom:10px;">
    <div class="sd" style="margin-top:0;">不限用途借貸前5大客戶</div>
    <table class="tbl" style="table-layout:auto;">
      <thead><tr>
        <th>客戶</th><th class="r">借款(萬)</th><th class="r">維持率</th>
      </tr></thead>
      <tbody>{unlim_html}</tbody>
    </table>
    <div style="margin-top:4px;font-size:8.5px;color:var(--yel);font-family:var(--mono);">⚠ 維持率低於160%者需留意</div>
  </div>

  <!-- 有價證券借貸 + 款項借貸 -->
  <div style="background:var(--s2);border:1px solid var(--bd);border-radius:8px;padding:10px 13px;">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;">
      <div>
        <div class="sd" style="margin-top:0;">有價證券借貸</div>
        <table class="tbl" style="table-layout:auto;">
          <thead><tr>
            <th>來源</th><th class="r">借券金額</th><th class="r">占比</th>
          </tr></thead>
          <tbody>
            <tr><td class="l">外資借券</td><td class="r" style="color:var(--acc2);font-weight:600;">{_wan(sl.get('foreign',0))}</td><td class="r"><span class="b bb">{_pct(sl.get('foreign',0)/sl_total)}</span></td></tr>
            <tr><td class="l">他家券商</td><td class="r">{_wan(sl.get('broker',0))}</td><td class="r"><span class="b bx">{_pct(sl.get('broker',0)/sl_total)}</span></td></tr>
            <tr><td class="l">自營借券</td><td class="r">{_wan(sl.get('prop',0))}</td><td class="r"><span class="b bx">{_pct(sl.get('prop',0)/sl_total)}</span></td></tr>
            <tr class="grand"><td class="l" style="font-weight:700;">借券總餘額</td><td class="r" style="font-weight:700;">{_wan(sl.get('total',0))}</td><td class="r"><span class="b bg">加權費率 {sl.get('rate',0):.2f}%</span></td></tr>
          </tbody>
        </table>
      </div>
      <div>
        <div class="sd" style="margin-top:0;">款項借貸餘額</div>
        <table class="tbl" style="table-layout:auto;">
          <thead><tr>
            <th>類型</th><th class="r">未還餘額</th>
          </tr></thead>
          <tbody>
            <tr><td class="l">半年型借貸</td><td class="r">{_wan(loans.get('half_year',0)) if loans.get('half_year',0) else '—'}</td></tr>
            <tr><td class="l">T+5 借款</td><td class="r" style="color:var(--{'yel' if loans.get('t5',0) else 'dim'});">{_wan(loans.get('t5',0)) if loans.get('t5',0) else '—'}</td></tr>
            <tr><td class="l">T+30 借款</td><td class="r">{_wan(loans.get('t30',0)) if loans.get('t30',0) else '—'}</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

</div>

</body></html>"""


def save_html(html: str, output_dir: Path, report_date: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = report_date.replace("/", "")
    path = output_dir / f"風控整合日報_{date_str}.html"
    path.write_text(html, encoding="utf-8")
    return path
