#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
指数与行业数据调用参考模板（山西证券 Tushare）

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


# ============ 接口：index_daily 指数日线行情 ============
# 说明：获取指数每日行情；单次最多 8000 行，可分段补全
# SDK 签名：pro.index_daily(ts_code='', trade_date='', start_date='', end_date='', fields='')
#   ts_code 必填，如 000300.SH（沪深300）、399001.SZ（深证成指）
def get_index_daily_sdk(ts_code, start_date, end_date):
    """SDK 方式：指数日线"""
    return pro.index_daily(
        ts_code=ts_code, start_date=start_date, end_date=end_date,
        fields="ts_code,trade_date,close,open,high,low,pct_chg,vol,amount",
    )


def get_index_daily_http(ts_code, start_date, end_date):
    """HTTP 方式：指数日线"""
    return http_call(
        "index_daily",
        {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
        "ts_code,trade_date,close,open,high,low,pct_chg,vol,amount",
    )


# ============ 接口：index_classify 申万行业分类 ============
# 说明：获取申万行业分类（2014 / 2021 两版）
# SDK 签名：pro.index_classify(index_code='', level='', parent_code='', src='', fields='')
#   level: L1一级/L2二级/L3三级；src: SW2014/SW2021
def get_index_classify_sdk(level="L1", src="SW2021"):
    """SDK 方式：申万行业分类"""
    return pro.index_classify(
        level=level, src=src, fields="index_code,industry_name,level,parent_code,src"
    )


def get_index_classify_http(level="L1", src="SW2021"):
    """HTTP 方式：申万行业分类"""
    return http_call(
        "index_classify",
        {"level": level, "src": src},
        "index_code,industry_name,level,parent_code,src",
    )


# ============ 接口：index_member 申万行业成分 ============
# 说明：获取申万行业成分股
# SDK 签名：pro.index_member(index_code='', con_code='', fields='')
#   index_code 为申万行业指数代码，如 801010.SI（农林牧渔一级）
def get_index_member_sdk(index_code):
    """SDK 方式：行业成分股"""
    return pro.index_member(
        index_code=index_code, fields="index_code,con_code,con_name,in_date,out_date"
    )


def get_index_member_http(index_code):
    """HTTP 方式：行业成分股"""
    return http_call(
        "index_member",
        {"index_code": index_code},
        "index_code,con_code,con_name,in_date,out_date",
    )