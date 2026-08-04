#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票数据调用参考模板（山西证券 Tushare）

本文件是 Agent 调取数据时的【参考模板】，不作为可执行脚本：
- 按接口给出函数签名、参数说明、返回字段
- 每个接口同时提供 SDK 与 HTTP 两种调用方式，按环境校验结果选择

用法：Agent 按需复制单个接口的调用函数，填入实际参数即可。
"""

import os

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
    """HTTP 通用调用，返回 DataFrame。
    api_name: 接口名；params: 接口参数 dict；fields: 逗号分隔字段串。
    """
    resp = requests.post(
        HTTP_URL, json={"api_name": api_name, "token": token, "params": params, "fields": fields}
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"{api_name} 调用失败: {data.get('msg')}")
    return pd.DataFrame(data["data"]["items"], columns=data["data"]["fields"])


# ============ 接口：stock_basic 股票列表 ============
# 说明：获取上市公司基础信息（名称、行业、上市日期等）
# SDK 签名：pro.stock_basic(exchange='', list_status='', fields='', ts_code='')
#   exchange: 交易所（SSE/SZSE/BSE）；list_status: L上市/P退市/D暂停；fields: 逗号分隔字段
def get_stock_basic_sdk(exchange="", list_status="L"):
    """SDK 方式：股票列表"""
    return pro.stock_basic(
        exchange=exchange,
        list_status=list_status,
        fields="ts_code,symbol,name,area,industry,list_date",
    )


def get_stock_basic_http(list_status="L"):
    """HTTP 方式：股票列表"""
    return http_call(
        "stock_basic",
        {"exchange": "", "list_status": list_status},
        "ts_code,symbol,name,area,industry,list_date",
    )


# ============ 接口：daily 日线行情 ============
# 说明：获取 A 股日线行情（前复权；ah_vol/ah_amount 为盘后量额）
# SDK 签名：pro.daily(ts_code='', trade_date='', start_date='', end_date='', fields='')
#   ts_code/trade_date 至少给一个；日期格式 YYYYMMDD
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


# ============ 接口：fina_indicator 财务指标 ============
# 说明：获取上市公司财务指标（ROE、毛利率、EPS 等）
# SDK 签名：pro.fina_indicator(ts_code='', start_date='', end_date='', fields='')
#   注意：start_date/end_date 为公告日期而非报告期
def get_fina_indicator_sdk(ts_code, start_date, end_date):
    """SDK 方式：财务指标"""
    return pro.fina_indicator(
        ts_code=ts_code, start_date=start_date, end_date=end_date,
        fields="ts_code,end_date,roe,roe_waa,grossprofit_margin,netprofit_margin,basic_eps",
    )


def get_fina_indicator_http(ts_code, start_date, end_date):
    """HTTP 方式：财务指标"""
    return http_call(
        "fina_indicator",
        {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
        "ts_code,end_date,roe,roe_waa,grossprofit_margin,netprofit_margin,basic_eps",
    )