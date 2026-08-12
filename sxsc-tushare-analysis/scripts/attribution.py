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
    beta = round(cov / var, 2)
    alpha_daily = stock.mean() - (risk_free / periods) - beta * (market.mean() - risk_free / periods)
    alpha_annual = round(alpha_daily * periods * 100, 2)
    return {
        "Beta": beta,
        "Alpha(年化%)": alpha_annual,
        "interpretation": f"市场敏感度{'高' if beta > 1.2 else ('低' if beta < 0.8 else '适中')}，{'跑赢' if alpha_annual > 0 else '跑输'}市场"
    }


# ============ 财务健康评分：Piotroski F-Score ============
def calc_piotroski_fscore(df_fina, df_income, df_cashflow):
    """Piotroski F-Score（0-9 分）。
    需要 2 期年报数据（当期 + 去年同期）。
    df_fina: fina_indicator 结果（含 roe, grossprofit_margin）
    df_income: income 结果（含 total_revenue, n_income_attr_p）
    df_cashflow: cashflow 结果（含 n_cashflow_act）
    各项均按 end_date 排序后取最近 2 期对比。
    """
    df_fina = df_fina.sort_values("end_date").tail(2)
    df_income = df_income.sort_values("end_date").tail(2)
    df_cashflow = df_cashflow.sort_values("end_date").tail(2)

    score = 0
    details = []

    # 1. 净利润为正
    ni = df_income["n_income_attr_p"].iloc[-1]
    s = ni > 0
    score += s; details.append(f"净利润为正: {'是' if s else '否'}")

    # 2. 经营现金流为正
    cfo = df_cashflow["n_cashflow_act"].iloc[-1]
    s = cfo > 0
    score += s; details.append(f"经营现金流为正: {'是' if s else '否'}")

    # 3. ROA 上升（用 ROE 近似）
    if len(df_fina) >= 2:
        s = df_fina["roe"].iloc[-1] > df_fina["roe"].iloc[-2]
    else:
        s = False
    score += s; details.append(f"ROE 上升: {'是' if s else '否'}")

    # 4. CFO > 净利润（盈利质量）
    s = cfo > ni
    score += s; details.append(f"现金流>净利润: {'是' if s else '否'}")

    # 5. 毛利率上升
    if len(df_fina) >= 2:
        s = df_fina["grossprofit_margin"].iloc[-1] > df_fina["grossprofit_margin"].iloc[-2]
    else:
        s = False
    score += s; details.append(f"毛利率上升: {'是' if s else '否'}")

    # 6. 资产周转率上升（营收/总资产）
    if len(df_income) >= 2 and "total_revenue" in df_income.columns:
        rev_growth = df_income["total_revenue"].iloc[-1] / df_income["total_revenue"].iloc[-2]
        s = rev_growth > 1
    else:
        s = False
    score += s; details.append(f"营收增长: {'是' if s else '否'}")

    rating = "强" if score >= 7 else ("弱" if score <= 2 else "中等")
    return {"F-Score": score, "rating": rating, "details": details}


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
        "interpretation": f"事件{'正向显著' if car > 2 else ('负向显著' if car < -2 else '无显著影响')}",
    }
