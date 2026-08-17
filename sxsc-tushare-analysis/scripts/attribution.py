#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
归因分析模块：CAPM Beta/Alpha、Piotroski F-Score、事件研究法（CAR）。

用于收益归因分解、财务健康量化评分、事件冲击因果分析。
"""

import numpy as np
import pandas as pd


# ============ 收益归因：Beta/Alpha ============
def calc_beta_alpha(stock_returns, market_returns, risk_free=0.02, periods=250):
    """CAPM Beta/Alpha 分解。
    stock_returns / market_returns: 日收益率序列（对齐索引）。
    返回 Beta（市场敏感度）、Alpha（超额收益年化）。
    """
    aligned = pd.DataFrame({"stock": stock_returns, "market": market_returns}).dropna()
    if len(aligned) < 30:
        return None
    stock = np.asarray(aligned["stock"], dtype=float)
    market = np.asarray(aligned["market"], dtype=float)
    cov = np.cov(stock, market)[0, 1]
    var = np.var(market)
    if var == 0:
        return None
    beta = round(float(cov / var), 2)
    alpha_daily = stock.mean() - (risk_free / periods) - beta * (market.mean() - risk_free / periods)
    alpha_annual = round(float(alpha_daily * periods * 100), 2)
    return {
        "Beta": beta,
        "Alpha(年化%)": alpha_annual,
        "interpretation": f"市场敏感度{'高' if beta > 1.2 else ('低' if beta < 0.8 else '适中')}，{'跑赢' if alpha_annual > 0 else '跑输'}市场"
    }


# ============ 财务健康评分：Piotroski F-Score ============
def _filter_annual(df, date_col="end_date"):
    """筛年报并去重：保留 end_date 以 '1231' 结尾的年报，同报告期保留最新公告。"""
    if df is None or len(df) == 0:
        return df
    df = df[df[date_col].astype(str).str.endswith("1231")].copy()
    if df.empty:
        return df
    if "ann_date" in df.columns:
        df = df.sort_values(["end_date", "ann_date"]).drop_duplicates(date_col, keep="last")
    else:
        df = df.drop_duplicates(date_col, keep="last")
    return df.sort_values(date_col)


def calc_piotroski_fscore(df_fina, df_income, df_cashflow, df_balances=None):
    """Piotroski F-Score（0-9 分）。
    需要 2 期年报数据（当期 + 去年同期）。各表先按 end_date 筛年报(1231)、
    去重后取最近 2 期对比，避免拿 Q3 与 Q2 比导致的非同比错误。

    df_fina: fina_indicator 结果（含 npta/roa, grossprofit_margin, debt_to_assets,
             current_ratio, assets_turn；均为接口预计算字段）
    df_income: income 结果（含 n_income_attr_p）
    df_cashflow: cashflow 结果（含 n_cashflow_act）
    df_balances: balancesheet 结果（含 total_share, total_assets, comp_type 可选），
                 用于判断是否新增股本；comp_type 用于金融业识别(2银行/3保险/4证券)；
                 可选，缺失则第 7 项计 0 分并标注数据缺失。

    ROA 取值口径：优先 npta(总资产净利润，Piotroski 净利口径)，次选 roa(EBIT 口径，
    部分金融业为空)，最后用 n_income_attr_p/total_assets 自算——兼容证券/银行等 roa 字段为空的标的。

    9 项标准：
      盈利能力   1) ROA>0  2) 经营现金流>0  3) ΔROA>0  4) 经营现金流>净利润(应计质量)
      杠杆/流动/融资 5) Δ资产负债率≤0  6) Δ流动比率>0  7) 未新增股本(Δtotal_share≤0)
      运营效率   8) Δ毛利率>0  9) Δ总资产周转率>0
    返回 F-Score/rating/details，以及 有效项/缺失项 计数；金融业额外给 comp_note 弱参考提示。
    """
    df_fina = _filter_annual(df_fina)
    df_income = _filter_annual(df_income)
    df_cashflow = _filter_annual(df_cashflow)
    df_balances = _filter_annual(df_balances) if df_balances is not None else None

    score = 0
    details = []

    def _val(df, col, pos):
        if df is None or col not in df.columns or len(df) < abs(pos):
            return None
        v = df[col].iloc[pos]
        return v if pd.notna(v) else None

    def _get_roa(pos):
        # 优先 npta(净利口径)，次选 roa(EBIT 口径，金融业常空)，最后用 净利润/总资产 自算
        v = _val(df_fina, "npta", pos)
        if v is None:
            v = _val(df_fina, "roa", pos)
        if v is None:
            ni_ = _val(df_income, "n_income_attr_p", pos)
            ta_ = _val(df_balances, "total_assets", pos) if df_balances is not None else None
            if ni_ is not None and ta_ not in (None, 0, 0.0):
                v = ni_ / ta_ * 100
        return v

    CUR, PREV = -1, -2

    # 1. ROA > 0
    roa = _get_roa(CUR)
    s = roa is not None and roa > 0
    score += s; details.append(f"ROA>0: {'是' if s else ('否' if roa is not None else '数据缺失')}")

    # 2. 经营现金流 > 0
    cfo = _val(df_cashflow, "n_cashflow_act", CUR)
    s = cfo is not None and cfo > 0
    score += s; details.append(f"经营现金流>0: {'是' if s else ('否' if cfo is not None else '数据缺失')}")

    # 3. ΔROA > 0
    roa_prev = _get_roa(PREV)
    if roa is None or roa_prev is None:
        details.append("ΔROA>0: 数据不足")
    else:
        s = roa > roa_prev; score += s; details.append(f"ΔROA>0: {'是' if s else '否'}")

    # 4. 经营现金流 > 净利润（盈利质量）
    ni = _val(df_income, "n_income_attr_p", CUR)
    if cfo is None or ni is None:
        details.append("现金流>净利润: 数据缺失")
    else:
        s = cfo > ni; score += s; details.append(f"现金流>净利润: {'是' if s else '否'}")

    # 5. Δ资产负债率 ≤ 0（杠杆下降）
    da = _val(df_fina, "debt_to_assets", CUR); da_prev = _val(df_fina, "debt_to_assets", PREV)
    if da is None or da_prev is None:
        details.append("Δ资产负债率≤0: 数据不足")
    else:
        s = da <= da_prev; score += s; details.append(f"Δ资产负债率≤0: {'是' if s else '否'}")

    # 6. Δ流动比率 > 0
    cr = _val(df_fina, "current_ratio", CUR); cr_prev = _val(df_fina, "current_ratio", PREV)
    if cr is None or cr_prev is None:
        details.append("Δ流动比率>0: 数据不足")
    else:
        s = cr > cr_prev; score += s; details.append(f"Δ流动比率>0: {'是' if s else '否'}")

    # 7. 未新增股本
    sh = _val(df_balances, "total_share", CUR) if df_balances is not None else None
    sh_prev = _val(df_balances, "total_share", PREV) if df_balances is not None else None
    if sh is None or sh_prev is None:
        details.append("未新增股本: 数据缺失(未提供资产负债表)")
    else:
        s = sh <= sh_prev; score += s; details.append(f"未新增股本: {'是' if s else '否'}")

    # 8. Δ毛利率 > 0
    gm = _val(df_fina, "grossprofit_margin", CUR); gm_prev = _val(df_fina, "grossprofit_margin", PREV)
    if gm is None or gm_prev is None:
        details.append("Δ毛利率>0: 数据不足")
    else:
        s = gm > gm_prev; score += s; details.append(f"Δ毛利率>0: {'是' if s else '否'}")

    # 9. Δ总资产周转率 > 0
    at = _val(df_fina, "assets_turn", CUR); at_prev = _val(df_fina, "assets_turn", PREV)
    if at is None or at_prev is None:
        details.append("Δ总资产周转率>0: 数据不足")
    else:
        s = at > at_prev; score += s; details.append(f"Δ总资产周转率>0: {'是' if s else '否'}")

    rating = "强" if score >= 7 else ("弱" if score <= 2 else "中等")
    # 金融业识别（balancesheet comp_type: 1工商业 2银行 3保险 4证券）
    comp_note = None
    if df_balances is not None and "comp_type" in df_balances.columns and len(df_balances):
        ct = df_balances["comp_type"].iloc[-1]
        if str(ct) in ("2", "3", "4"):
            comp_note = (f"标的为金融机构(comp_type={ct})，F-Score 为工商业设计，"
                         f"毛利率/资产周转率等项对金融业语义失真，结果仅弱参考")
    missing = sum(1 for d in details if "数据缺失" in d or "数据不足" in d)
    out = {"F-Score": score, "rating": rating, "details": details,
           "有效项": 9 - missing, "缺失项": missing}
    if comp_note:
        out["comp_note"] = comp_note
    return out


# ============ 事件研究 ============
def calc_event_study(stock_returns, market_returns, event_date, window_before=30, window_after=10):
    """事件研究法：计算事件前后的异常收益（AR）与累计异常收益（CAR）。
    event_date: 事件日在 returns 序列中的位置索引。
    估计窗口 = 事件前 window_before 天，用于估算正常收益（市场模型回归）。
    事件窗口 = 事件后 window_after 天，计算 AR = 实际收益 - 正常收益。
    """
    aligned = pd.DataFrame({"stock": stock_returns, "market": market_returns}).dropna()
    est = aligned.iloc[event_date - window_before:event_date]
    evt = aligned.iloc[event_date:event_date + window_after]
    if len(est) < 10 or len(evt) == 0:
        return None

    s = np.asarray(est["stock"], dtype=float)
    m = np.asarray(est["market"], dtype=float)
    v = np.var(m)
    if v == 0:
        return None
    beta = np.cov(s, m)[0, 1] / v
    alpha = s.mean() - beta * m.mean()

    evt_m = np.asarray(evt["market"], dtype=float)
    evt_s = np.asarray(evt["stock"], dtype=float)
    normal_returns = alpha + beta * evt_m
    ar = evt_s - normal_returns
    car = round(float(ar.sum()) * 100, 2)

    return {
        "CAR(累计异常收益%)": car,
        "事件窗口AR均值%": round(float(ar.mean()) * 100, 2),
        "Beta(估计窗口)": round(beta, 2),
        "interpretation": f"事件 CAR {'偏正(>2%)' if car > 2 else ('偏负(<-2%)' if car < -2 else '影响不明显')}（阈值判断，未做 t 检验/置信区间）",
    }
