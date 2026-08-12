#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基础指标模块：收益率、均线、波动率、回撤、夏普、Sortino、信息比率、财务趋势、风险信号。

Agent 按需复制函数，填入实际参数即可。
"""

import numpy as np


def calc_returns(df_close, periods=(5, 20, 60, 120, 250)):
    """区间收益率。df_close: 日期升序的 close 序列。"""
    latest = df_close.iloc[-1]
    out = {"最新收盘": round(latest, 2)}
    for p in periods:
        if len(df_close) > p:
            out[f"近{p}日涨幅%"] = round((latest / df_close.iloc[-1 - p] - 1) * 100, 2)
    return out


def calc_cagr(start_value, end_value, days):
    """复合年化增长率（CAGR）。
    days: 持有天数（交易日）。年化按 250 交易日计。
    """
    if start_value <= 0 or days <= 0:
        return None
    years = days / 250
    cagr = (end_value / start_value) ** (1 / years) - 1
    return round(cagr * 100, 2)


def calc_ma(df_close, windows=(5, 10, 20, 60)):
    """均线值（用于判断多头/空头排列）。"""
    return {f"MA{w}": round(df_close.tail(w).mean(), 2) for w in windows}


def calc_volatility(df_close, window=20):
    """年化波动率。"""
    ret = df_close.pct_change().dropna()
    return round(ret.tail(window).std() * np.sqrt(250) * 100, 2)


def calc_max_drawdown(df_nav):
    """最大回撤（%）。df_nav: 日期升序的净值序列。"""
    cummax = df_nav.cummax()
    dd = (df_nav / cummax - 1).min()
    return round(dd * 100, 2)


def calc_sharpe(df_nav, risk_free=0.02, periods=250):
    """夏普比率（年化）。"""
    ret = df_nav.pct_change().dropna()
    if len(ret) < 2 or ret.std() == 0:
        return None
    annual_ret = ret.mean() * periods
    annual_std = ret.std() * np.sqrt(periods)
    return round((annual_ret - risk_free) / annual_std, 2)


def calc_sortino(df_nav, risk_free=0.02, periods=250):
    """Sortino 比率（仅用下行波动率，比 Sharpe 更合理）。"""
    ret = df_nav.pct_change().dropna()
    if len(ret) < 2:
        return None
    annual_ret = ret.mean() * periods
    downside = ret[ret < 0]
    if len(downside) == 0 or downside.std() == 0:
        return None
    annual_downside_std = downside.std() * np.sqrt(periods)
    return round((annual_ret - risk_free) / annual_downside_std, 2)


def calc_information_ratio(stock_returns, benchmark_returns, periods=250):
    """信息比率 = 超额收益年化 / 跟踪误差年化。"""
    excess = stock_returns - benchmark_returns
    if len(excess) < 2 or excess.std() == 0:
        return None
    annual_excess = excess.mean() * periods
    tracking_error = excess.std() * np.sqrt(periods)
    return round(annual_excess / tracking_error, 2)


def calc_roe_trend(df):
    """ROE 趋势。df: fina_indicator 结果，按 end_date 排序。"""
    df = df.sort_values("end_date")
    return df[["end_date", "roe", "grossprofit_margin", "netprofit_margin"]].head(8)


def calc_revenue_growth(df):
    """营收/利润同比增速。df: income 结果（含 total_revenue, n_income_attr_p）。"""
    df = df.sort_values("end_date")
    df["rev_yoy"] = df["total_revenue"].pct_change(4) * 100
    df["profit_yoy"] = df["n_income_attr_p"].pct_change(4) * 100
    return df[["end_date", "rev_yoy", "profit_yoy"]].tail(8)


def flag_risks(stock_code, df_price, df_margin=None, df_holding=None):
    """风险信号汇总（示例规则，Agent 可按需扩展）。"""
    risks = []
    ret = df_price["pct_chg"]
    if ret.tail(20).abs().mean() > 5:
        risks.append("近20日日波动偏大，价格波动风险高")
    if df_margin is not None and len(df_margin) > 1:
        rzye = df_margin["rzye"].iloc[-1]
        rzye_prev = df_margin["rzye"].iloc[-2]
        if rzye > rzye_prev * 1.1:
            risks.append("融资余额快速上升，杠杆情绪偏热")
    return risks
