#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
单标的纯时序因子计算模块。

来源：QuantsPlaybook 研报因子，用日线(+指数日线作市场基准)纯 python 复现。
设计约束：
- 仅单标的自身时序，不依赖全市场截面（无截面 rank，因子值为原始值）。
- 不做行业/市值中性化（无 statsmodels/全市场数据），因子为原始值非纯净因子。
- 复用 basic_metrics/adjustment 的预处理，不重复造轮子。

数据口径假设（验证阶段需核对，见 SKILL 数据假设清单）：
- daily.amount 单位千元，daily.vol 单位手(100股) → VWAP = amount*1000/(vol*100)
- daily_basic.turnover_rate 为百分比(2.5=2.5%) → 参与乘法前 /100
- vol/amount 未复权，送转日会跳变（VCF/ACF 受影响，PCF/TRCF 不受）
- 数据为 T-1，因子计算基于序列最后一个交易日
"""

import numpy as np
import pandas as pd


# ============ 均线收敛与发散因子（开源证券：形态识别，均线的收敛与发散）============
def calc_convergence_factor(series, periods=(1, 5, 10, 20, 60, 120)):
    """均线收敛因子：多周期均线在当前时点的标准差 → -log(1+std)。

    std 越小=均线越收敛=变盘前兆(价 PCF)或量能蓄势(量 VCF)。
    对 close/vol/amount/turnover 分别构建即 PCF/VCF/ACF/TRCF。
    series: 日期升序的 Series（close/vol/amount/turnover）。
    返回最新因子值（float，越大/接近0=越收敛）或 None（数据不足）。
    注意：vol/amount 未复权，送转日附近因子值可能跳变；跨标的不可比（未截面标准化）。
    """
    if series is None or len(series) < max(periods):
        return None
    mas = []
    for p in periods:
        mas.append(float(series.tail(p).mean()) if p > 1 else float(series.iloc[-1]))
    std = float(np.std(mas, ddof=0))
    if std < 0 or (1 + std) <= 0:
        return None
    return round(-np.log(1 + std), 4)


def calc_trend_momentum(close, windows=(20, 60, 120, 240)):
    """趋势动量 Mono = MA_N / close_current（再论动量：趋势动量版）。

    >1 = 价低于均线（中期偏弱/下行趋势）；<1 = 价高于均线（中期偏强/上行趋势）。
    返回 {Mono_20:.., Mono_60:.., ...}，数据不足的窗口跳过；全空则返回 {}。
    """
    out = {}
    if close is None or len(close) < 2:
        return out
    cur = float(close.iloc[-1])
    if cur <= 0:
        return out
    for w in windows:
        if len(close) >= w:
            ma = float(close.tail(w).mean())
            out[f"Mono_{w}"] = round(ma / cur, 3)
    return out


# ============ 动量质量（高质量动量选股 + 振幅切割动量）============
def calc_quantitative_momentum(close, window=60):
    """高质量动量基础版：r_window - 3000*sigma^2（风险调整动量）。

    r = close.iloc[-1]/close.iloc[-window]-1；sigma = window 日日收益 std。
    数值越大=动量越强且质量越高（波动拖累小）。
    改进版需截面 rank（全市场 ID/MAX 排序），单标的不可得，故仅基础版。
    返回 dict(momentum, raw_return, sigma) 或 None。
    """
    if close is None or len(close) < window + 1:
        return None
    r = float(close.iloc[-1] / close.iloc[-1 - window] - 1)
    ret = close.pct_change().dropna().tail(window)
    if len(ret) < 2:
        return None
    sigma = float(ret.std())
    return {"momentum": round(r - 3000 * sigma ** 2, 4), "raw_return": round(r, 4), "sigma": round(sigma, 4)}


def calc_amplitude_momentum(close, high, low, n=120, lam=0.3):
    """振幅切割动量 A/B 因子（A股市场中如何构造动量因子）。

    低振幅日涨跌幅加总呈动量效应(A因子)，高振幅日呈反转效应(B因子)。
    amp = high/low - 1；按 amp 排序取最低 lam% 日 ret 加总=A，最高 lam% 日=B。
    返回 dict(a_factor, b_factor, n, lam) 或 None。A>0=动量正向。
    """
    if close is None or len(close) < n:
        return None
    df = pd.DataFrame({"close": close, "high": high, "low": low}).tail(n).copy()
    df["amp"] = df["high"] / df["low"] - 1
    df["ret"] = df["close"].pct_change()
    df = df.dropna()
    if len(df) < 10:
        return None
    df = df.sort_values("amp")
    k = max(1, int(len(df) * lam))
    a_factor = round(float(df["ret"].head(k).sum()), 4)
    b_factor = round(float(df["ret"].tail(k).sum()), 4)
    return {"a_factor": a_factor, "b_factor": b_factor, "n": len(df), "lam": lam}


# ============ 微观结构（上下影线 + 理想振幅）============
def calc_shadow_factors(open_, high, low, close, std_window=5, lookback=20):
    """上下影线因子（上下影线因子：蜡烛图 vs 威廉版）。

    蜡烛图：upper=high-max(close,open)，lower=min(close,open)-low。
    威廉版：upper=high-close，lower=close-low（以 close 为基准，更准）。
    标准化 = 当日影线 / 过去 std_window 日均值(.shift(1))，再取 lookback 日 mean/std。
    upper_std 偏高=卖压强；lower_mean 偏高=买气支撑。
    返回 dict（upper_c/lower_c/upper_w/lower_w 各 mean/std 共8项）或 {}。
    UBL 综合需市值中性化（截面），单标的不可得，故仅输出原始影线因子。
    """
    if close is None or len(close) < std_window + lookback:
        return {}
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close})
    df["upper_c"] = df["high"] - np.maximum(df["close"], df["open"])
    df["lower_c"] = np.minimum(df["close"], df["open"]) - df["low"]
    df["upper_w"] = df["high"] - df["close"]
    df["lower_w"] = df["close"] - df["low"]
    out = {}
    for col in ["upper_c", "lower_c", "upper_w", "lower_w"]:
        base = df[col].rolling(std_window).mean().shift(1)
        normed = df[col] / base.replace(0, np.nan)
        recent = normed.tail(lookback).dropna()
        out[f"{col}_mean"] = round(float(recent.mean()), 4) if not recent.empty else None
        out[f"{col}_std"] = round(float(recent.std()), 4) if not recent.empty else None
    return out


def calc_ideal_amplitude(close, high, low, n=20, lam=0.2):
    """理想振幅 V(λ) = V_high(λ) - V_low(λ)（振幅因子的隐藏结构）。

    按窗口内 close 的 rank(pct) 切割：>= λ 为高价组，< λ 为低价组。
    V_high = 高价组振幅均值，V_low = 低价组振幅均值。
    V<0 = 低价区振幅大于高价区（负向选股能力弱）；V>0 = 高价区振幅大（状态跃迁效应强）。
    返回 dict(v_high, v_low, v, lam) 或 None。
    """
    if close is None or len(close) < n:
        return None
    df = pd.DataFrame({"close": close, "high": high, "low": low}).tail(n).copy()
    df["amp"] = df["high"] / df["low"] - 1
    df["rank"] = df["close"].rank(pct=True)
    high_grp = df[df["rank"] >= lam]
    low_grp = df[df["rank"] < lam]
    if high_grp.empty or low_grp.empty:
        return None
    v_high = float(high_grp["amp"].mean())
    v_low = float(low_grp["amp"].mean())
    return {"v_high": round(v_high, 4), "v_low": round(v_low, 4), "v": round(v_high - v_low, 4), "lam": lam}


# ============ 行为金融（凸显理论 STR + 处置效应 CGO）============
def calc_salience_score(stock_ret, bench_ret, window=20, theta=0.1):
    """凸显理论/惊恐度（方正版，用指数收益近似截面均值）。

    σ = |r_i - r_bench| / (|r_i| + |r_bench| + theta)
    weighted = σ * r_i；terrified_score = (rolling.mean + rolling.std) * 0.5。
    terrified 偏高=近期收益频繁偏离大盘，投资者注意力被极端日吸引，过度买入风险。
    低凸显股票未来收益更好（负向因子）。
    返回 dict(terrified_score, avg_score, std_score) 或 None。
    注意：用沪深300收益近似截面均值，大盘股偏差小、小盘股偏差较大。
    """
    if stock_ret is None or bench_ret is None:
        return None
    aligned = pd.DataFrame({"r": stock_ret, "b": bench_ret}).dropna()
    if len(aligned) < window:
        return None
    sigma = (aligned["r"] - aligned["b"]).abs() / (aligned["r"].abs() + aligned["b"].abs() + theta)
    weighted = sigma * aligned["r"]
    avg = weighted.rolling(window).mean()
    std = weighted.rolling(window).std()
    terrified = (avg + std) * 0.5
    last = terrified.iloc[-1]
    if pd.isna(last):
        return None
    return {
        "terrified_score": round(float(last), 6),
        "avg_score": round(float(avg.iloc[-1]), 6) if not pd.isna(avg.iloc[-1]) else None,
        "std_score": round(float(std.iloc[-1]), 6) if not pd.isna(std.iloc[-1]) else None,
    }


def calc_cgo(close, amount, vol, turnover_rate, n=100):
    """处置效应 CGO = (close - RP) / RP（资本利得突出量，Grinblatt 2005）。

    RP = 换手率衰减加权历史 VWAP 参考价。
    weight[i] = turnover[i] * Π_{j>i}(1 - turnover[j])（i 日买入且之后未卖出的留存比例），归一化。
    VWAP = amount*1000/(vol*100)（amount 千元，vol 手）。
    turnover_rate 百分比 → /100 转比例。
    CGO<0 = 持股者平均浮亏（处置效应浮亏区，投资者惜售，历史负 IC 预示反弹概率偏高）。
    返回 dict(cgo, rp) 或 None。RCGO 残差版需截面回归，单标的不可得，故仅原始 CGO。
    """
    if close is None or len(close) < n:
        return None
    df = pd.DataFrame({"close": close, "amount": amount, "vol": vol, "turnover": turnover_rate}).tail(n).copy()
    df = df.dropna(subset=["close", "amount", "vol", "turnover"])
    if len(df) < n // 2:
        return None
    df["vwap"] = df["amount"] * 1000 / (df["vol"] * 100)
    df["turn"] = df["turnover"] / 100.0
    turn = df["turn"].values
    # 衰减权重：从未来向过去累积未卖出比例
    weight = np.zeros(len(df))
    surv = 1.0
    for i in range(len(df) - 1, -1, -1):
        weight[i] = turn[i] * surv
        surv *= (1 - turn[i])
    s = weight.sum()
    if s <= 0:
        return None
    weight = weight / s
    rp = float((weight * df["vwap"].values).sum())
    cur = float(df["close"].iloc[-1])
    if rp <= 0:
        return None
    return {"cgo": round(cur / rp - 1, 4), "rp": round(rp, 4)}
