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
    if len(df_close) == 0:
        return {"最新收盘": "N/A"}
    latest = df_close.iloc[-1]
    if isinstance(latest, float) and np.isnan(latest):
        return {"最新收盘": "N/A"}
    out = {"最新收盘": round(float(latest), 2)}
    for p in periods:
        if len(df_close) > p:
            out[f"近{p}日涨幅%"] = round(float((latest / df_close.iloc[-1 - p] - 1) * 100), 2)
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
    """均线值（用于判断多头/空头排列）。

    窗口数据不足时该窗口返回 None：不能用短窗口均值冒充 MA60
    （30 个点算出的"MA60"与真实 60 日均线完全不同）。
    """
    out = {}
    for w in windows:
        if len(df_close) < w:
            out[f"MA{w}"] = None
        else:
            out[f"MA{w}"] = round(float(df_close.tail(w).mean()), 2)
    return out


def calc_volatility(df_close, window: Optional[int] = None):
    """年化波动率。

    ⚠️ 默认用**全样本**收益率，与 calc_max_drawdown / calc_sharpe 口径一致——
    旧实现默认只取近 20 日收益率，导致同一句结论里"年化波动"（近20日窗口）
    与"最大回撤/夏普"（全样本）混用窗口（实测同序列 20 日波动 37.35% vs
    全样本 31.54%）。需要短期波动率时显式传 window。
    """
    ret = df_close.pct_change().dropna()
    if len(ret) < 2:
        return None
    sample = ret if window is None else ret.tail(window)
    if len(sample) == 0:
        return None
    return round(float(sample.std()) * np.sqrt(250) * 100, 2)


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
    """Sortino 比率（分母用全样本下行偏差，MAR 与分子无风险利率同口径）。
    下行偏差 = sqrt(mean(min(r - MAR, 0)^2))，MAR = risk_free / periods（与分子的
    无风险利率一致）；旧实现用"负收益子集的样本标准差（围绕负收益均值）"，
    既非标准下行偏差，又与分子 rf、MAR=0 的口径不一致，会系统性高估 Sortino。
    """
    ret = df_nav.pct_change().dropna()
    if len(ret) < 2:
        return None
    annual_ret = ret.mean() * periods
    mar = risk_free / periods  # 与分子 rf 同口径；rf=0 时即 MAR=0
    downside_dev = np.sqrt(np.mean(np.minimum(ret - mar, 0) ** 2))  # 全样本，未达标收益计 0
    if downside_dev == 0:
        return None
    return round((annual_ret - risk_free) / (downside_dev * np.sqrt(periods)), 2)


def calc_information_ratio(stock_returns, benchmark_returns, periods=250):
    """信息比率 = 超额收益年化 / 跟踪误差年化。"""
    excess = stock_returns - benchmark_returns
    if len(excess) < 2 or excess.std() == 0:
        return None
    annual_excess = excess.mean() * periods
    tracking_error = excess.std() * np.sqrt(periods)
    return round(annual_excess / tracking_error, 2)

