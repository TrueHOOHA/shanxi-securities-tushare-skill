#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
公共模块：环境初始化与取数函数（SDK/HTTP 双模式）。

本文件是 Agent 执行分析时的公共基础设施参考模板：
- 环境初始化（SDK 与 HTTP 两种方式）
- 通用 HTTP 调用函数
- 基础取数函数（以日线为例，其余接口见数据 skill 的 demo 模板）

用法：先运行 check_env.py 确认 mode（sdk/http），再选择对应取数函数。
"""

import os

import pandas as pd
import requests

# ============ 环境初始化 ============
token = os.getenv("SXSC_TUSHARE_TOKEN")

# --- 方式二：HTTP 初始化（无需 SDK） ---
# SECURITY: HTTP_URL 为明文 HTTP，token 放在请求 body 中传输，存在被中间人
# 窃取/篡改的风险。仅限在受控内网 / VPN 环境下使用 HTTP 模式；禁止在公网
# 暴露该地址。若需公网访问，应自建 HTTPS 反向代理或要求服务端启用 TLS。
HTTP_URL = "http://221.204.19.233:7172"

# --- 方式一：SDK 初始化（需要 sxsc_tushare 库） ---
# 条件导入：SDK 不存在时降级为 HTTP 模式，避免模块无法导入导致 HTTP 通道也失效。
try:
    import sxsc_tushare as sx

    sx.set_token(token)
    pro = sx.get_api(env="prd")  # 'prd' 仿真, 'qa' 生产
    mode = "sdk"
except ImportError:
    pro = None
    mode = "http"


def http_call(api_name, params, fields):
    """HTTP 通用调用，返回 DataFrame。api_name 必须先在接口对应表中确认。
    注意：token 经明文 HTTP 传输，仅限受控内网/VPN 使用。
    """
    if not token:
        raise RuntimeError("未设置环境变量 SXSC_TUSHARE_TOKEN，HTTP 模式不可用")
    resp = requests.post(
        HTTP_URL, json={"api_name": api_name, "token": token, "params": params, "fields": fields}
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"{api_name} 调用失败: {data.get('msg')}")
    return pd.DataFrame(data["data"]["items"], columns=data["data"]["fields"])


# ============ 基础取数函数 ============
def get_daily_sdk(ts_code, start_date, end_date):
    """SDK 方式：日线行情（仅 mode=sdk 时可用）"""
    api = pro
    if api is None:
        raise RuntimeError("SDK 未初始化（未安装 sxsc_tushare），请改用 HTTP 模式")
    return api.daily(
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


def calc_start_date(end_date, n_trading_days, buffer_days=40):
    """按需要的交易日数推算起始日期（YYYYMMDD）。
    n 个交易日约需 n × 1.45 个自然日（含周末/节假日），
    再加 buffer_days 余量确保数据充分。
    用法：要算近 250 日涨跌幅时，start_date = calc_start_date(end_date, 250)。
    """
    from datetime import datetime, timedelta
    e = datetime.strptime(end_date, "%Y%m%d")
    approx = int(n_trading_days * 1.45) + buffer_days
    return (e - timedelta(days=approx)).strftime("%Y%m%d")


def get_daily_for_period(ts_code, end_date, periods=(5, 20, 60, 120, 250)):
    """取足最大周期的日线数据，自动推算起始日期。
    periods: 需要计算的交易日周期列表（如近 5/20/60/120/250 日）。
    返回按 trade_date 升序、重置索引的 DataFrame。
    调用方无需关心 start_date——脚本按最大周期自动预留余量，
    避免「要 250 日却只取 249 日导致 N/A」的坑。
    按 mode 自动选择 SDK / HTTP 通道。
    """
    start = calc_start_date(end_date, max(periods))
    df = get_daily_sdk(ts_code, start, end_date) if mode == "sdk" else get_daily_http(ts_code, start, end_date)
    return df.sort_values("trade_date").reset_index(drop=True)


# ============ 场内基金（ETF）取数 ============
def get_fund_daily_sdk(ts_code, start_date, end_date):
    """SDK 方式：场内基金日线行情（ETF 必须用 fund_daily，不能用 daily——daily 对 ETF 返回空）"""
    api = pro
    if api is None:
        raise RuntimeError("SDK 未初始化（未安装 sxsc_tushare），请改用 HTTP 模式")
    return api.fund_daily(
        ts_code=ts_code, start_date=start_date, end_date=end_date,
        fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
    )


def get_fund_daily_http(ts_code, start_date, end_date):
    """HTTP 方式：场内基金日线行情（ETF 用 fund_daily 接口）"""
    return http_call(
        "fund_daily",
        {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
        "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
    )


def get_fund_daily_for_period(ts_code, end_date, periods=(5, 20, 60, 120, 250)):
    """场内基金（ETF）取足最大周期的日线数据，自动推算起始日期。
    与 get_daily_for_period 同理，但用 fund_daily 接口。按 mode 自动选择通道。
    """
    start = calc_start_date(end_date, max(periods))
    df = get_fund_daily_sdk(ts_code, start, end_date) if mode == "sdk" else get_fund_daily_http(ts_code, start, end_date)
    return df.sort_values("trade_date").reset_index(drop=True)


# ============ 报告片段示例 ============
def format_report_snippet(title, conclusion, table_df, note):
    """生成单维度 markdown 片段。"""
    lines = [f"### {title}", "", f"**结论**：{conclusion}", "", table_df.to_markdown(index=False), "", note, ""]
    return "\n".join(lines)
