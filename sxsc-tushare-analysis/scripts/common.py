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


# ============ 基础取数函数 ============
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


# ============ 报告片段示例 ============
def format_report_snippet(title, conclusion, table_df, note):
    """生成单维度 markdown 片段。"""
    lines = [f"### {title}", "", f"**结论**：{conclusion}", "", table_df.to_markdown(index=False), "", note, ""]
    return "\n".join(lines)
