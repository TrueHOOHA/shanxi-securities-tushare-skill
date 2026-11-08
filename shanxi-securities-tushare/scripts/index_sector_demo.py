#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
指数与行业数据调用参考模板（山西证券 Tushare）

本文件是 Agent 调取数据时的【参考模板】，不作为可执行脚本：
- 按接口给出函数签名、参数说明、返回字段
- 每个接口提供 SDK 调用方式

用法：Agent 按需复制单个接口的调用函数，填入实际参数即可。
"""

import os

import sxsc_tushare as sx

# ============ 环境初始化 ============
token = os.getenv("SXSC_TUSHARE_TOKEN")

sx.set_token(token)
pro = sx.get_api(env="prd")  # 'prd' 仿真, 'qa' 生产


# ============ 接口：index_daily 指数日线行情 ============
# 说明：获取指数每日行情；单次最多 8000 行，可分段补全
# SDK 签名：pro.index_daily(ts_code='', trade_date='', start_date='', end_date='', fields='')
#   ts_code 必填，如 000300.SH（沪深300）、399001.SZ（深证成指）
def get_index_daily_sdk(ts_code, start_date, end_date):
    """指数日线"""
    return pro.index_daily(
        ts_code=ts_code, start_date=start_date, end_date=end_date,
        fields="ts_code,trade_date,close,open,high,low,pct_chg,vol,amount",
    )


# ============ 接口：index_classify 申万行业分类 ============
# 说明：获取申万行业分类（2014 / 2021 两版）
# SDK 签名：pro.index_classify(index_code='', level='', parent_code='', src='', fields='')
#   level: L1一级/L2二级/L3三级；src: SW2014/SW2021
def get_index_classify_sdk(level="L1", src="SW2021"):
    """申万行业分类"""
    return pro.index_classify(
        level=level, src=src, fields="index_code,industry_name,level,parent_code,src"
    )


# ============ 接口：index_member 申万行业成分 ============
# 说明：获取申万行业成分股
# SDK 签名：pro.index_member(index_code='', con_code='', fields='')
#   index_code 为申万行业指数代码，如 801010.SI（农林牧渔一级）
def get_index_member_sdk(index_code):
    """行业成分股"""
    return pro.index_member(
        index_code=index_code, fields="index_code,con_code,con_name,in_date,out_date"
    )


# ============ 接口：index_global 国际指数行情 ============
# 说明：获取国际主要指数日线行情
# SDK 签名：pro.index_global(ts_code='', trade_date='', start_date='', end_date='', fields='')
# 注意：ts_code 格式为无点前缀的代码，如 DJI（道琼斯）、SPX（标普500）、IXIC（纳斯达克）、
#       N225（日经225）、HSI（恒生指数）、XIN9（富时A50）等，详见 references/国际指数.md
# 不要写成 .DJI 或 ^DJI —— 该接口不使用点前缀
def get_index_global_sdk(ts_codes, start_date, end_date):
    """国际指数行情（多指数逗号分隔）
    注意：返回数据为降序（最新在前），使用前须 .sort_values('trade_date')。
    """
    df = pro.index_global(
        ts_code=ts_codes, start_date=start_date, end_date=end_date,
        fields="ts_code,trade_date,close,open,high,low,pct_chg",
    )
    return df.sort_values("trade_date").reset_index(drop=True)
