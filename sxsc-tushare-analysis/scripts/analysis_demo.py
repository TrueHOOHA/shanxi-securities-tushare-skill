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


# ============ 报告片段示例 ============
def format_report_snippet(title, conclusion, table_df, note):
    """生成单维度 markdown 片段。"""
    lines = [f"### {title}", "", f"**结论**：{conclusion}", "", table_df.to_markdown(index=False), "", note, ""]
    return "\n".join(lines)


if __name__ == "__main__":
    print("本文件是参考模板，不作为可执行脚本。请复制所需函数到你的分析脚本中。")