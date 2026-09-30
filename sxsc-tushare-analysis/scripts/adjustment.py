#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调整与归一化模块：复权处理、序列归一化、收益率对比、历史分位数、截面去极值。

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
    merged = merged.sort_values("trade_date").set_index("trade_date")
    # adj_factor 接口可能缺失最新交易日（数据滞后），左连接后该日为 NaN，
    # 会导致 close_post 最新日 NaN、区间收益率与技术指标全失效。
    # adj_factor 单调递增，前向填充用前一日因子是安全近似。
    merged["adj_factor"] = merged["adj_factor"].ffill()
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
    ).sort_values("nav_date").drop_duplicates("nav_date", keep="last").set_index("nav_date")
    # fund_adj 因子可能缺失最新 nav_date（数据滞后），左连接后该日 adj_factor=NaN，
    # adj_nav 末行即 NaN、区间收益/夏普/回撤整表失效。因子单调递增，前向填充
    # 用前一日因子是安全近似；口径与 apply_adj_factor / apply_etf_adj 保持一致。
    merged["adj_factor"] = merged["adj_factor"].ffill()
    latest_factor = merged["adj_factor"].iloc[-1] if not merged.empty else 1.0
    if pd.isna(latest_factor):
        latest_factor = 1.0
    merged["adj_nav"] = merged["unit_nav"] * merged["adj_factor"] / latest_factor
    return merged


def apply_etf_adj(df_daily, df_adj):
    """基于 fund_adj 校正场内基金（ETF）价格序列，消除份额拆分/分红除权扭曲。
    df_daily: fund_daily 结果（含 trade_date, close）
    df_adj: fund_adj 结果（含 trade_date, adj_factor）
    返回 df（trade_date 索引）增加 close_post 列（后复权收盘价 = close × adj_factor）。
    ETF 拆分（如 1拆2，adj_factor=2.0）会导致不复权价腰斩，
    直接算收益会严重失真，必须复权。fund_daily 返回的是不复权价。
    ⚠️ ETF 的 fund_adj 因子口径与股票 adj_factor 不同（实测方向/量级不统一：
    510500 ~0.34、512100 ~0.373、510300 ~1.27），close_post 绝对值非后复权价，
    仅供计算收益/夏普/回撤等 scale-invariant 指标（全程同列，pct_change 与因子绝对值无关）；
    展示价格请用未复权 close。
    ⚠️ 本函数**只复权 close**（产出 close_post），不复权 OHLC——
    KDJ/布林带等需 high/low/close 同 scale 的指标，请改用 `apply_adj_factor`（它复权全部 OHLC）。
    """
    merged = df_daily.merge(df_adj[["trade_date", "adj_factor"]], on="trade_date", how="left")
    merged = merged.sort_values("trade_date").set_index("trade_date")
    # adj_factor 可能缺失最新交易日（数据滞后），前向填充避免 close_post 最新日 NaN
    merged["adj_factor"] = merged["adj_factor"].ffill()
    merged["close_post"] = merged["close"] * merged["adj_factor"]
    return merged


# ============ 横向对比：归一化 ============
def _check_date_index(index, fn_name):
    """校验索引为日期类型；整数/RangeIndex 等非日期索引会令 rebase/compare 产出无意义结果。"""
    if isinstance(index, pd.RangeIndex) or (hasattr(index, "is_integer") and index.is_integer()):
        raise TypeError(f"{fn_name}: 需日期索引（DatetimeIndex/日期字符串），收到整数索引。"
                        f"请先 .set_index('trade_date') 后再调用。")


def rebase_series(series_dict, base_date=None):
    """多标的序列归一化（rebase 到基准日=100）。
    series_dict: {label: Series}，每个 Series 索引为日期、值为复权价或净值。
    base_date: 基准日字符串 YYYYMMDD，默认取各序列最早公共日期（即所有序列都有数据的最早一天）。
    返回归一化后的 DataFrame，每列=一个标的，基准日=100。
    ⚠️ 每个 Series 必须是日期索引（DatetimeIndex 或日期字符串 index），
    传整数索引会产出无意义结果——调用前先 .set_index('trade_date')。
    """
    df = pd.DataFrame(series_dict).sort_index()
    if df.empty:
        return df
    _check_date_index(df.index, "rebase_series")
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
    ⚠️ Series 必须是日期索引（传整数索引会产出无意义"最新值"）。
    "最新值"列显示后复权绝对值，仅供量级参考，展示价格请用未复权价。
    """
    rows = {}
    for label, s in series_dict.items():
        if len(s) == 0:
            rows[label] = {"标的": label, "最新值": "N/A"}
            continue
        _check_date_index(s.index, f"compare_returns[{label}]")
        latest = s.iloc[-1]
        row = {"标的": label, "最新值": round(float(latest), 2)}
        for p in periods:
            if len(s) > p:
                row[f"近{p}日涨幅%"] = round(float((latest / s.iloc[-1 - p] - 1) * 100), 2)
        rows[label] = row
    return pd.DataFrame(rows.values())


# ============ 历史分位数 ============
def calc_percentile_rank(value, historical_series):
    """计算当前值在历史序列中的百分位。
    value: 当前值（如当前 PE）；为 None/NaN/inf 时返回 None（数据缺失，不给分位）
    historical_series: 历史值序列（如近 5 年每日 PE）
    返回 0-100 的百分位数，如 85 表示当前值高于历史 85% 的时间。
    """
    # 缺失值必须返回 None 而不是 0.0：NaN 参与比较恒为 False，
    # 会算出"0% 分位（极低）"这类与事实相反的假信号（估值/盈利分位最危险）。
    # 调用方虽有 _safe_float 前置过滤，但不能依赖它——本函数自身必须安全。
    if value is None or not np.isfinite(value):
        return None
    arr = np.array(historical_series.dropna())
    if len(arr) == 0:
        return None
    rank = (arr < value).sum() / len(arr) * 100
    return round(rank, 1)


# ============ 截面去极值（分析借用） ============
# 仅用于截面均值/标准化前，避免单只异常股拉偏行业均值或 Z-Score；
# 【禁止】用于 VaR/CVaR、偏度/峰度、最大回撤等刻画尾部的指标——会抹掉真实尾部信息。

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
