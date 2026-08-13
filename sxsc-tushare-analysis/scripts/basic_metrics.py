#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基础指标模块：收益率、均线、波动率、回撤、夏普、Sortino、信息比率、财务趋势、风险信号。

Agent 按需复制函数，填入实际参数即可。
"""

import numpy as np


def calc_returns(df_close, periods=(5, 20, 60, 120, 250)):
    """区间收益率。df_close: 日期升序的 close 序列。
    返回 dict：键为「最新收盘」和 f"近{p}日涨幅%"，调用时用字符串键，
    不要用整数 p 直接访问（如 out["近20日涨幅%"]，而非 out[20]）。
    """
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


def calc_holder_concentration(df_holders):
    """股东户数时间序列分析——筹码集中度指标。
    df_holders: stk_holdernumber 结果（含 end_date, holder_num），按 end_date 升序。
    返回 dict：最新户数、近4季变化率、趋势判断、筹码集中度信号。
    股东户数减少 = 筹码集中（主力吸筹），户数增加 = 筹码分散（主力派发）。
    """
    df = df_holders.sort_values("end_date").dropna(subset=["holder_num"])
    if len(df) < 2:
        return {"holder_num": "N/A", "trend": "数据不足", "signal": "N/A"}

    latest = int(df["holder_num"].iloc[-1])
    changes = df["holder_num"].pct_change() * 100
    # 近4季变化率（取最近3-4期）
    last_4 = df.tail(4)
    qoq = []
    for i in range(1, len(last_4)):
        c = round((last_4["holder_num"].iloc[i] / last_4["holder_num"].iloc[i-1] - 1) * 100, 1)
        qoq.append(f"{last_4['end_date'].iloc[i]}:{c:+.1f}%")

    # 趋势判断：结合最近一期环比 与 整体变化率（中期趋势）
    latest_chg = round(changes.iloc[-1], 1) if len(changes) > 0 else 0
    # 中期（自监测以来）变化率
    total_chg = round((df["holder_num"].iloc[-1] / df["holder_num"].iloc[0] - 1) * 100, 1)

    if latest_chg < -3:
        signal = "筹码集中（主力吸筹）"
    elif latest_chg > 3:
        signal = "筹码分散（主力派发）"
    elif total_chg < -15:
        signal = "中期筹码集中（户数显著下降）"
    elif total_chg > 15:
        signal = "中期筹码分散（户数显著上升）"
    elif latest_chg < -1:
        signal = "筹码缓慢集中"
    elif latest_chg > 1:
        signal = "筹码缓慢分散"
    else:
        signal = "筹码稳定"

    # 结合股价：如果有 price_series 可做更深入判断，这里只输出户数变化
    return {
        "holder_num": latest,
        "total_chg_pct": total_chg,
        "latest_qoq": latest_chg,
        "qoq_detail": " → ".join(qoq[-4:]),
        "signal": signal,
    }
