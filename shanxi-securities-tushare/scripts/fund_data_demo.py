#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基金数据调用参考模板（山西证券 Tushare）

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


# ============ 接口：fund_basic 公募基金列表 ============
# 说明：获取公募基金基础信息
# SDK 签名：pro.fund_basic(market='', status='', type='', fields='')
#   market: E场内/O场外；status: L上市/D退市/P暂停；type: 基金类型
def get_fund_basic_sdk(market="E", status="L"):
    """SDK 方式：基金列表"""
    return pro.fund_basic(
        market=market, status=status,
        fields="ts_code,name,fund_type,found_date,issue_date,delist_date",
    )


def get_fund_basic_http(market="E", status="L"):
    """HTTP 方式：基金列表"""
    return http_call(
        "fund_basic",
        {"market": market, "status": status},
        "ts_code,name,fund_type,found_date,issue_date,delist_date",
    )


# ============ 接口：fund_nav 基金净值 ============
# 说明：获取公募基金单位净值
# SDK 签名：pro.fund_nav(ts_code='', nav_date='', start_date='', end_date='', fields='')
#   日期格式 YYYYMMDD
def get_fund_nav_sdk(ts_code, start_date, end_date):
    """SDK 方式：基金净值"""
    return pro.fund_nav(
        ts_code=ts_code, start_date=start_date, end_date=end_date,
        fields="ts_code,nav_date,unit_nav,accum_nav,adj_nav",
    )


def get_fund_nav_http(ts_code, start_date, end_date):
    """HTTP 方式：基金净值"""
    return http_call(
        "fund_nav",
        {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
        "ts_code,nav_date,unit_nav,accum_nav,adj_nav",
    )


# ============ 接口：fund_manager 基金经理 ============
# 说明：获取基金经理任职信息
# SDK 签名：pro.fund_manager(ts_code='', name='', fields='')
def get_fund_manager_sdk(ts_code=None):
    """SDK 方式：基金经理"""
    return pro.fund_manager(
        ts_code=ts_code, fields="ts_code,name,ann_date,begin_date,end_date"
    )


def get_fund_manager_http(ts_code=None):
    """HTTP 方式：基金经理"""
    return http_call(
        "fund_manager",
        {"ts_code": ts_code},
        "ts_code,name,ann_date,begin_date,end_date",
    )