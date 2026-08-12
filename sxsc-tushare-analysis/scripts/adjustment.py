#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调整与归一化模块：复权处理、序列归一化、收益率对比、历史分位数、Z-Score 标准化。

用于纵向对比（复权消除除权跳变）和横向对比（多标的归一化）。
"""

import numpy as np
import pandas as pd


# ============ 纵向对比：复权处理 ============
def apply_adj_factor(df_daily, df_adj):
    """基于 adj_factor 构建后复权价格序列。
    df_daily: daily 接口结果（含 close, trade_date）
    df_adj: adj_factor 接口结果（含 trade_date, adj_factor）
    返回 df 增加 close_post 列（后复权收盘价）。
    """
    merged = df_daily.merge(df_adj[["trade_date", "adj_factor"]], on="trade_date", how="left")
    merged["close_post"] = merged["close"] * merged["adj_factor"]
    return merged


def apply_fund_adj(df_nav, df_adj):
    """基于 fund_adj 构建基金复权净值序列。
    df_nav: fund_nav 结果（含 nav_date, unit_nav）
    df_adj: fund_adj 结果（含 trade_date, adj_factor）
    返回 df 增加 adj_nav 列（复权净值，前复权）。
    """
    merged = df_nav.merge(
        df_adj[["trade_date", "adj_factor"]].rename(columns={"trade_date": "nav_date"}),
        on="nav_date", how="left"
    )
    latest_factor = merged["adj_factor"].iloc[0] if not merged.empty else 1.0
    merged["adj_nav"] = merged["unit_nav"] * merged["adj_factor"] / latest_factor
    return merged


def apply_etf_adj(df_daily, df_adj):
    """基于 fund_adj 校正场内基金（ETF）价格序列，消除份额拆分/分红除权扭曲。
    df_daily: fund_daily 结果（含 trade_date, close）
    df_adj: fund_adj 结果（含 trade_date, adj_factor）
    返回 df 增加 close_post 列（后复权收盘价 = close × adj_factor）。
    ETF 拆分（如 1拆2，adj_factor=2.0）会导致不复权价腰斩，
    直接算收益会严重失真，必须复权。fund_daily 返回的是不复权价。
    """
    merged = df_daily.merge(df_adj[["trade_date", "adj_factor"]], on="trade_date", how="left")
    merged["close_post"] = merged["close"] * merged["adj_factor"]
    return merged


# ============ 横向对比：归一化 ============
def rebase_series(series_dict, base_date=None):
    """多标的序列归一化（rebase 到基准日=100）。
    series_dict: {label: Series}，每个 Series 索引为日期、值为复权价或净值。
    base_date: 基准日字符串 YYYYMMDD，默认取各序列最早公共日期。
    返回归一化后的 DataFrame，每列=一个标的，基准日=100。
    """
    df = pd.DataFrame(series_dict)
    if base_date is None:
        base_date = df.index[0]
    base_val = df.loc[base_date]
    return (df / base_val * 100).round(2)


def compare_returns(series_dict, periods=(20, 60, 120, 250)):
    """多标的收益率横向对比表。
    series_dict: {label: Series}，每个 Series 为复权价/复权净值（日期升序）。
    返回 DataFrame，行=各标的，列=各区间收益率(%)。
    """
    rows = {}
    for label, s in series_dict.items():
        latest = s.iloc[-1]
        row = {"标的": label, "最新值": round(latest, 2)}
        for p in periods:
            if len(s) > p:
                row[f"近{p}日涨幅%"] = round((latest / s.iloc[-1 - p] - 1) * 100, 2)
        rows[label] = row
    return pd.DataFrame(rows.values())


# ============ 历史分位数 ============
def calc_percentile_rank(value, historical_series):
    """计算当前值在历史序列中的百分位。
    value: 当前值（如当前 PE）
    historical_series: 历史值序列（如近 5 年每日 PE）
    返回 0-100 的百分位数，如 85 表示当前值高于历史 85% 的时间。
    """
    arr = np.array(historical_series.dropna())
    rank = (arr < value).sum() / len(arr) * 100
    return round(rank, 1)


# ============ Z-Score 标准化 ============
def calc_zscore(values, benchmark_series=None):
    """Z-Score 标准化：将指标转换为与均值的标准差倍数。
    values: 当前值或值序列；benchmark_series: 参照群体（如同行业所有股票的 PE）。
    Z > 1.96 = 显著偏高（5%显著性水平）；Z < -1.96 = 显著偏低。
    """
    if benchmark_series is None:
        return None
    arr = np.asarray(benchmark_series, dtype=float)
    mean = arr.mean()
    std = arr.std()
    if std == 0:
        return None
    z = (np.mean(values) - mean) / std if hasattr(values, '__len__') else (values - mean) / std
    z = round(float(z), 2)
    flag = "显著偏高" if z > 1.96 else ("显著偏低" if z < -1.96 else "正常范围")
    return {"Z-Score": z, "interpretation": flag}
