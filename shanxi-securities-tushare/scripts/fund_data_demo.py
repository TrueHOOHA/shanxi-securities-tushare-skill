#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基金数据调用参考模板（山西证券 Tushare）

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


# ============ 接口：fund_basic 公募基金列表 ============
# 说明：获取公募基金基础信息
# SDK 签名：pro.fund_basic(market='', status='', type='', fields='')
#   market: E场内/O场外；status: L上市/D退市/P暂停；type: 基金类型
def get_fund_basic_sdk(market="E", status="L"):
    """基金列表"""
    return pro.fund_basic(
        market=market, status=status,
        fields="ts_code,name,fund_type,found_date,issue_date,delist_date",
    )


# ============ 接口：fund_nav 基金净值 ============
# 说明：获取公募基金单位净值
# SDK 签名：pro.fund_nav(ts_code='', nav_date='', start_date='', end_date='', fields='')
#   日期格式 YYYYMMDD
def get_fund_nav_sdk(ts_code, start_date, end_date):
    """基金净值"""
    return pro.fund_nav(
        ts_code=ts_code, start_date=start_date, end_date=end_date,
        fields="ts_code,nav_date,unit_nav,accum_nav,adj_nav",
    )


# ============ 接口：fund_manager 基金经理 ============
# 说明：获取基金经理任职信息
# SDK 签名：pro.fund_manager(ts_code='', name='', fields='')
def get_fund_manager_sdk(ts_code=None):
    """基金经理"""
    return pro.fund_manager(
        ts_code=ts_code, fields="ts_code,name,ann_date,begin_date,end_date"
    )
