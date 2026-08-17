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
    """基于 adj_factor 构建后复权价格序列（daily 为未复权，趋势/收益/技术指标必须复权后再算）。
    df_daily: daily 接口结果（未复权，含 trade_date 及 open/high/low/close/pre_close 等列）
    df_adj: adj_factor 接口结果（含 trade_date, adj_factor）
    返回 df 增加 *_post 列（open_post/high_post/low_post/close_post/pre_close_post，
    按 daily 实际存在的列逐列 ×adj_factor）。
    所有后复权列同 scale，可安全用于 OHLC 类指标（KDJ/布林带等）；
    ⚠️ 切勿用 close_post 配合未复权的 high/low——scale 不一致会导致 KDJ 等指标失真
    （如需后复权，请统一用 *_post 列，或对 high/low 也取 *_post）。
    """
    merged = df_daily.merge(df_adj[["trade_date", "adj_factor"]], on="trade_date", how="left")
    for col in ("open", "high", "low", "close", "pre_close"):
        if col in merged.columns:
            merged[f"{col}_post"] = merged[col] * merged["adj_factor"]
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
    ).sort_values("nav_date").reset_index(drop=True)
    latest_factor = merged["adj_factor"].iloc[-1] if not merged.empty else 1.0
    if pd.isna(latest_factor):
        latest_factor = 1.0
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
    base_date: 基准日字符串 YYYYMMDD，默认取各序列最早公共日期（即所有序列都有数据的最早一天）。
    返回归一化后的 DataFrame，每列=一个标的，基准日=100。
    """
    df = pd.DataFrame(series_dict).sort_index()
    common = df.dropna()
    if common.empty:
        return df
    if base_date is None:
        base_date = common.index[0]
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
    if len(arr) == 0:
        return None
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
    if len(arr) == 0:
        return None
    mean = arr.mean()
    std = arr.std()
    if std == 0:
        return None
    z = (np.mean(values) - mean) / std if hasattr(values, '__len__') else (values - mean) / std
    z = round(float(z), 2)
    flag = "显著偏高" if z > 1.96 else ("显著偏低" if z < -1.96 else "正常范围")
    return {"Z-Score": z, "interpretation": flag}


# ============ 因子加工借鉴：清洗 / 去极值 / 截面分位 ============
# 说明：分析 skill 非"全市场因子流水线"，仅借鉴其中对分析有用的三步：
#   ① clean_panel        —— 统一清洗带日期的面板（财务三表/估值面板）
#   ② winsorize_cross_section —— 截面去极值，仅用于截面均值/标准化前
#   ③ valuation_percentiles    —— 估值双口径分位（历史分位 + 同业截面分位）
# 注意：② 严禁用于 VaR/CVaR、偏度/峰度、最大回撤等刻画尾部的指标。


def clean_panel(df, date_col="end_date", value_cols=None, dedup=True, sort_asc=True):
    """统一清洗带日期的面板数据（财务三表/估值面板等）。
    - 去重：同 date_col 保留最新公告（ann_date 最大）的行
    - 排序：按 date_col 升序
    - 数值列：value_cols 强制转 numeric（非数→NaN），便于后续计算
    返回清洗后的 DataFrame（原始 df 不被修改）。
    """
    if df is None or len(df) == 0:
        return df
    out = df.copy()
    if dedup and date_col in out.columns:
        keys = [date_col] + (["ann_date"] if "ann_date" in out.columns else [])
        out = out.sort_values(keys).drop_duplicates(date_col, keep="last")
    if sort_asc and date_col in out.columns:
        out = out.sort_values(date_col).reset_index(drop=True)
    cols = value_cols if value_cols is not None else [c for c in out.columns if c not in (date_col, "ann_date", "ts_code")]
    for c in cols:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def winsorize_cross_section(series, method="mad", k=3.0):
    """截面去极值：对一组截面值（如某日行业成分股 PE）做 3σ-MAD 或 1/99 百分位截断。
    仅用于截面均值/标准化前，避免单只异常股拉偏行业均值或 Z-Score。
    【禁止】用于 VaR/CVaR、偏度/峰度、最大回撤等刻画尾部的指标——会抹掉真实尾部信息。
    """
    s = pd.Series(series).dropna()
    if len(s) < 5:
        return s
    if method == "mad":
        med = s.median()
        mad = (s - med).abs().median()
        if mad == 0:
            return s
        spread = k * 1.4826 * mad
        lower, upper = med - spread, med + spread
    else:
        lower, upper = s.quantile(0.01), s.quantile(0.99)
    return s.clip(lower, upper)


def valuation_percentiles(value, historical_series, cross_section_series, min_sample=10):
    """估值双口径分位：历史分位 + 同业截面分位（值高于参考集多少比例，0-100）。
    - historical_series：该标的自身近 N 年该指标序列（时序分位，calc_percentile_rank）
    - cross_section_series：当日同行业/同类标的该指标截面（截面分位）
    双口径交叉判断：双双偏高=贵、双双偏低=便宜、背离=相对自身历史与相对同业不一致（需找原因）。
    方向由调用方按指标语义解读（PE 高=偏贵；ROE 高=偏优），函数只返回中性分位数。
    截面样本 < min_sample 时标注"样本不足"，分位仅供参考（避免 5-6 只同业的 20% 步长噪声）。
    """
    cs = pd.Series(cross_section_series).dropna()
    hist = pd.Series(historical_series).dropna() if historical_series is not None else pd.Series([], dtype=float)
    cs_rank = calc_percentile_rank(value, cs) if len(cs) > 0 else None
    return {
        "历史分位": calc_percentile_rank(value, hist) if len(hist) > 0 else None,
        "截面分位": cs_rank,
        "截面样本数": int(len(cs)),
        "截面可靠性": "样本不足(仅供参考)" if len(cs) < min_sample else "可用",
    }
