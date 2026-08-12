#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
风险建模模块：VaR/CVaR、尾部风险（偏度/峰度）、回撤深度分析、Amihud 非流动性、
滚动 Beta、滚动夏普、相对强度（RS）。

用于风险维度的立体度量：尾部风险 + 流动性风险 + 回撤体感 + 动态监控。
"""

import numpy as np
import pandas as pd


# ============ 风险建模 ============
def calc_var_cvar(returns, confidence=(0.95, 0.99)):
    """历史法 VaR/CVaR（在险价值/条件在险价值）。
    returns: 日收益率序列。
    VaR = 损失分布在置信水平的分位数（如 95% VaR 表示有 5% 概率日亏损超过此值）。
    CVaR = 超过 VaR 的平均损失（尾部期望损失），比 VaR 更保守。
    """
    ret = np.asarray(returns.dropna(), dtype=float)
    results = {}
    for c in confidence:
        var = round(np.percentile(ret, (1 - c) * 100), 4)
        tail = ret[ret <= var]
        cvar = round(tail.mean(), 4) if len(tail) > 0 else var
        results[f"VaR({int(c*100)}%)"] = f"{var*100:.2f}%"
        results[f"CVaR({int(c*100)}%)"] = f"{cvar*100:.2f}%"
    return results


def calc_tail_risk(returns):
    """尾部风险分析：偏度、峰度、极端值。
    偏度 < 0 = 左偏（亏损侧尾部更长，风险更大）；峰度 > 0（超额）= 厚尾。
    """
    ret = np.asarray(returns.dropna(), dtype=float)
    if len(ret) < 10:
        return None
    mean = ret.mean()
    std = ret.std()
    if std == 0:
        return None
    n = len(ret)
    skew = round(float(np.mean(((ret - mean) / std) ** 3)) * np.sqrt(n * (n - 1)) / (n - 2), 2) if n > 2 else 0
    kurt = round(float(np.mean(((ret - mean) / std) ** 4)) - 3, 2)
    return {
        "偏度": skew,
        "峰度(超额)": kurt,
        "interpretation": f"{'左偏' if skew < 0 else '右偏'}，{'厚尾' if kurt > 0 else '薄尾'}（正态=0）",
        "risk": "尾部风险高" if (skew < -0.5 and kurt > 0) else "尾部风险适中",
    }


def calc_drawdown_detail(df_nav):
    """回撤深度分析：最大回撤、回撤持续期、痛苦指数。
    痛苦指数 = 回撤深度的平均值（衡量持续处于水下状态的程度）。
    """
    cummax = df_nav.cummax()
    dd = (df_nav / cummax - 1) * 100
    max_dd = round(dd.min(), 2)

    underwater = dd < 0
    durations = []
    start = None
    for i, u in enumerate(underwater):
        if u and start is None:
            start = i
        elif not u and start is not None:
            durations.append(i - start)
            start = None
    if start is not None:
        durations.append(len(underwater) - start)
    max_duration = max(durations) if durations else 0
    pain_index = round(float(np.abs(dd[underwater]).mean()), 2) if underwater.any() else 0

    return {
        "最大回撤%": max_dd,
        "最长回撤持续期(交易日)": max_duration,
        "痛苦指数": pain_index,
    }


def calc_amihud_illiquidity(price_returns, dollar_volume):
    """Amihud 非流动性指标 = |日收益率| / 日成交额（百万元）。
    衡量单位资金引起的价格变动，值越大 = 流动性越差。
    """
    ret = np.asarray(np.abs(price_returns).dropna(), dtype=float)
    vol = np.asarray(dollar_volume.reindex(price_returns.index).dropna(), dtype=float)
    min_len = min(len(ret), len(vol))
    if min_len == 0:
        return None
    ret = ret[:min_len]
    vol = vol[:min_len].copy()
    vol[vol == 0] = 1e-10
    illiq = ret / (vol / 1e6)
    mean_illiq = round(float(illiq.mean()), 6)
    return {
        "Amihud非流动性": mean_illiq,
        "interpretation": "流动性差" if mean_illiq > 0.1 else ("流动性一般" if mean_illiq > 0.01 else "流动性好"),
    }


# ============ 滚动分析 ============
def calc_rolling_beta(stock_returns, market_returns, window=60):
    """滚动 Beta：展示市场敏感度随时间的变化。
    返回最近一期 Beta 及趋势（上升=波动加大/防御减弱）。
    """
    aligned = pd.DataFrame({"stock": stock_returns, "market": market_returns}).dropna()
    if len(aligned) < window:
        return None
    stock = np.asarray(aligned["stock"], dtype=float)
    market = np.asarray(aligned["market"], dtype=float)
    betas = []
    for i in range(window, len(aligned)):
        s = stock[i - window:i]
        m = market[i - window:i]
        v = np.var(m)
        if v > 0:
            betas.append(np.cov(s, m)[0, 1] / v)
    if len(betas) < 2:
        return None
    latest = round(betas[-1], 2)
    trend = "上升" if betas[-1] > betas[0] else "下降"
    return {"当前Beta": latest, "趋势": trend, f"窗口{window}日": True}


def calc_rolling_sharpe(df_nav, window=60, periods=250, risk_free=0.02):
    """滚动夏普比率：展示风险调整收益随时间的变化。"""
    ret = df_nav.pct_change().dropna()
    if len(ret) < window:
        return None
    sharpes = []
    for i in range(window, len(ret)):
        s = ret.iloc[i - window:i]
        if s.std() > 0:
            sharpes.append((s.mean() * periods - risk_free) / (s.std() * np.sqrt(periods)))
    if len(sharpes) < 2:
        return None
    return {
        "当前滚动夏普": round(sharpes[-1], 2),
        "趋势": "上升" if sharpes[-1] > sharpes[0] else "下降",
    }


# ============ 相对强度分析 ============
def calc_relative_strength(ts_code_returns, benchmark_returns):
    """相对强度（RS）：标的收益率 vs 基准收益率的比值。
    RS > 1 = 跑赢基准；RS < 1 = 跑输基准。RS 上升趋势 = 相对走强。
    """
    aligned = pd.DataFrame({"stock": ts_code_returns, "bench": benchmark_returns}).dropna()
    if len(aligned) == 0:
        return None
    cum_stock = (1 + aligned["stock"]).cumprod()
    cum_bench = (1 + aligned["bench"]).cumprod()
    rs = cum_stock / cum_bench
    latest_rs = round(rs.iloc[-1], 3)
    ref_val = rs.iloc[-20] if len(rs) > 20 else rs.iloc[0]
    trend = "走强" if rs.iloc[-1] > ref_val else "走弱"
    return {"RS": latest_rs, "trend": trend}
