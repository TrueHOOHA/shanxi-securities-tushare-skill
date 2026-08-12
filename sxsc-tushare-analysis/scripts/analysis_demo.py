#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多维分析计算参考模板（山西证券 Tushare 分析 skill）

本文件是 Agent 在执行多维分析时的【参考模板】，不作为可执行脚本：
- 提供取数（SDK/HTTP 双模式）与分析计算（指标、趋势、对比）的常用函数
- Agent 按需复制函数，填入实际参数即可

用法：先运行 check_env.py 确认 mode（sdk/http），再选择对应取数函数。
"""

import os

import numpy as np
import pandas as pd
import requests

# ============ 环境初始化（两种方式共用） ============
token = os.getenv("SXSC_TUSHARE_TOKEN")

# --- 方式一：SDK 初始化（需要 sxsc_tushare 库） ---
import sxsc_tushare as sx

sx.set_token(token)
pro = sx.get_api(env="prd")  # 'prd' 仿真, 'qa' 生产

# --- 方式二：HTTP 初始化（无需 SDK） ---
HTTP_URL = "http://221.204.19.233:7172"


def http_call(api_name, params, fields):
    """HTTP 通用调用，返回 DataFrame。api_name 必须先在接口对应表中确认。"""
    resp = requests.post(
        HTTP_URL, json={"api_name": api_name, "token": token, "params": params, "fields": fields}
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"{api_name} 调用失败: {data.get('msg')}")
    return pd.DataFrame(data["data"]["items"], columns=data["data"]["fields"])


# ============ 取数函数（以股票日线为例，其余接口见数据 skill demo） ============
def get_daily_sdk(ts_code, start_date, end_date):
    """SDK 方式：日线行情"""
    return pro.daily(
        ts_code=ts_code, start_date=start_date, end_date=end_date,
        fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
    )


def get_daily_http(ts_code, start_date, end_date):
    """HTTP 方式：日线行情"""
    return http_call(
        "daily",
        {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
        "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
    )


# ============ 分析计算函数 ============
def calc_returns(df_close, periods=(5, 20, 60, 120, 250)):
    """区间收益率。df_close: 日期升序的 close 序列。"""
    latest = df_close.iloc[-1]
    out = {"最新收盘": round(latest, 2)}
    for p in periods:
        if len(df_close) > p:
            out[f"近{p}日涨幅%"] = round((latest / df_close.iloc[-1 - p] - 1) * 100, 2)
    return out


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


# ============ 横向对比：归一化 ============
def rebase_series(series_dict, base_date=None):
    """多标的序列归一化（rebase 到基准日=100）。
    series_dict: {label: Series}，每个 Series 索引为日期、值为复权价或净值。
    base_date: 基准日字符串 YYYYMMDD，默认取各序列最早公共日期。
    返回归一化后的 DataFrame，每列=一个标的，基准日=100。
    """
    import pandas as pd
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
    import pandas as pd
    rows = {}
    for label, s in series_dict.items():
        latest = s.iloc[-1]
        row = {"标的": label, "最新值": round(latest, 2)}
        for p in periods:
            if len(s) > p:
                row[f"近{p}日涨幅%"] = round((latest / s.iloc[-1 - p] - 1) * 100, 2)
        rows[label] = row
    return pd.DataFrame(rows.values())


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


# ============ 技术指标 ============
def calc_macd(df_close, fast=12, slow=26, signal=9):
    """MACD 指标。返回 (DIF, DEA, MACD柱) 最新值。"""
    ema_fast = df_close.ewm(span=fast, adjust=False).mean()
    ema_slow = df_close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd_bar = (dif - dea) * 2
    return {
        "DIF": round(dif.iloc[-1], 2),
        "DEA": round(dea.iloc[-1], 2),
        "MACD": round(macd_bar.iloc[-1], 2),
        "signal": "金叉" if dif.iloc[-1] > dea.iloc[-1] else "死叉",
    }


def calc_rsi(df_close, period=14):
    """RSI 相对强弱指标。返回最新 RSI 值与超买/超卖判断。"""
    delta = df_close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    rsi = 100 - 100 / (1 + rs)
    val = round(rsi.iloc[-1], 1)
    flag = "超买" if val > 70 else ("超卖" if val < 30 else "中性")
    return {"RSI": val, "signal": flag}


def calc_kdj(df_high, df_low, df_close, period=9):
    """KDJ 随机指标。返回 K/D/J 最新值。"""
    low_min = df_low.rolling(period).min()
    high_max = df_high.rolling(period).max()
    rsv = (df_close - low_min) / (high_max - low_min) * 100
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    j = 3 * k - 2 * d
    return {
        "K": round(k.iloc[-1], 2),
        "D": round(d.iloc[-1], 2),
        "J": round(j.iloc[-1], 2),
        "signal": "金叉" if k.iloc[-1] > d.iloc[-1] and k.iloc[-2] <= d.iloc[-2] else
                  ("死叉" if k.iloc[-1] < d.iloc[-1] and k.iloc[-2] >= d.iloc[-2] else "无叉"),
    }


def calc_boll(df_close, window=20, num_std=2):
    """布林带。返回上轨/中轨/下轨及当前位置判断。"""
    mid = df_close.rolling(window).mean()
    std = df_close.rolling(window).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    price = df_close.iloc[-1]
    up = round(upper.iloc[-1], 2)
    lo = round(lower.iloc[-1], 2)
    md = round(mid.iloc[-1], 2)
    pos = "触及上轨（超买）" if price >= up * 0.98 else (
          "触及下轨（超卖）" if price <= lo * 1.02 else "中轨附近")
    return {"上轨": up, "中轨": md, "下轨": lo, "position": pos}


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


# ============ 风险调整收益（进阶） ============
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


# ============ 量价分析 ============
def calc_obv(df_close, df_vol):
    """OBV（能量潮）。返回 OBV 趋势方向。"""
    direction = np.where(df_close.diff() > 0, 1, np.where(df_close.diff() < 0, -1, 0))
    obv = (direction * df_vol).cumsum()
    trend = "上升" if obv.iloc[-1] > obv.iloc[-5] else ("下降" if obv.iloc[-1] < obv.iloc[-5] else "持平")
    return {"OBV": round(obv.iloc[-1], 0), "trend": trend}


def calc_volume_ratio(df_vol, window=5):
    """量比 = 当日成交量 / 近 N 日平均成交量。"""
    ma_vol = df_vol.tail(window + 1).head(window).mean()
    if ma_vol == 0:
        return None
    ratio = df_vol.iloc[-1] / ma_vol
    flag = "放量" if ratio > 2 else ("缩量" if ratio < 0.5 else "正常")
    return {"量比": round(ratio, 2), "signal": flag}


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
    偏度 < 0 = 左偏（亏损侧尾部更长，风险更大）；峰度 > 3 = 厚尾（极端事件概率高于正态分布）。
    """
    ret = np.asarray(returns.dropna(), dtype=float)
    if len(ret) < 10:
        return None
    mean = ret.mean()
    std = ret.std()
    if std == 0:
        return None
    n = len(ret)
    # 偏度 = E[(X-μ)³] / σ³
    skew = round(float(np.mean(((ret - mean) / std) ** 3)) * np.sqrt(n * (n - 1)) / (n - 2), 2) if n > 2 else 0
    # 峰度 = E[(X-μ)⁴] / σ⁴ - 3（超额峰度，正态分布=0）
    kurt = round(float(np.mean(((ret - mean) / std) ** 4)) - 3, 2)
    return {
        "偏度": skew,
        "峰度(超额)": kurt,
        "interpretation": f"{'左偏' if skew < 0 else '右偏'}，{'厚尾' if kurt > 0 else '薄尾'}（正态=0）",
        "risk": "尾部风险高" if (skew < -0.5 and kurt > 0) else "尾部风险适中",
    }


def calc_drawdown_detail(df_nav):
    """回撤深度分析：最大回撤、回撤持续期、恢复时间、痛苦指数。
    痛苦指数 = 回撤深度的平均值（衡量持续处于水下状态的程度）。
    """
    cummax = df_nav.cummax()
    dd = (df_nav / cummax - 1) * 100
    max_dd = round(dd.min(), 2)

    # 回撤持续期：从开始回撤到恢复的最长天数
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


# ============ 复合增长率 ============
def calc_cagr(start_value, end_value, days):
    """复合年化增长率（CAGR）。
    days: 持有天数（交易日）。年化按 250 交易日计。
    """
    if start_value <= 0 or days <= 0:
        return None
    years = days / 250
    cagr = (end_value / start_value) ** (1 / years) - 1
    return round(cagr * 100, 2)


# ============ 标准化与横向对比 ============
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


# ============ 流动性风险 ============
def calc_amihud_illiquidity(price_returns, dollar_volume):
    """Amihud 非流动性指标 = |日收益率| / 日成交额（百万元）。
    衡量单位资金引起的价格变动，值越大 = 流动性越差。
    返回均值及判断。
    """
    ret = np.asarray(np.abs(price_returns).dropna(), dtype=float)
    vol = np.asarray(dollar_volume.reindex(price_returns.index).dropna(), dtype=float)
    min_len = min(len(ret), len(vol))
    if min_len == 0:
        return None
    ret = ret[:min_len]
    vol = vol[:min_len]
    vol[vol == 0] = 1e-10
    illiq = ret / (vol / 1e6)
    mean_illiq = round(float(illiq.mean()), 6)
    return {
        "Amihud非流动性": mean_illiq,
        "interpretation": "流动性差" if mean_illiq > 0.1 else ("流动性一般" if mean_illiq > 0.01 else "流动性好"),
    }


# ============ 报告片段示例 ============
def format_report_snippet(title, conclusion, table_df, note):
    """生成单维度 markdown 片段。"""
    lines = [f"### {title}", "", f"**结论**：{conclusion}", "", table_df.to_markdown(index=False), "", note, ""]
    return "\n".join(lines)


if __name__ == "__main__":
    print("本文件是参考模板，不作为可执行脚本。请复制所需函数到你的分析脚本中。")