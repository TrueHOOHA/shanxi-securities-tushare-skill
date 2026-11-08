#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
资金流向数据调用参考模板（山西证券 Tushare）

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


# ============ 接口：moneyflow 个股资金流向 ============
# 说明：获取沪深 A 股资金流向（大单/小单），单次最多 5000 行
# SDK 签名：pro.moneyflow(ts_code='', trade_date='', start_date='', end_date='', fields='')
#   ts_code/trade_date 至少给一个；net_mf_amount 为净流入额（万元）
def get_moneyflow_sdk(ts_code, start_date, end_date):
    """个股资金流向"""
    return pro.moneyflow(
        ts_code=ts_code, start_date=start_date, end_date=end_date,
        fields="ts_code,trade_date,buy_sm_amount,sell_sm_amount,buy_md_amount,sell_md_amount,buy_lg_amount,sell_lg_amount,buy_elg_amount,sell_elg_amount,net_mf_vol,net_mf_amount",
    )


# ============ 接口：moneyflow_hsgt 沪深港通资金流向 ============
# 说明：获取沪深港通（北向/南向）资金流向
# SDK 签名：pro.moneyflow_hsgt(start_date='', end_date='', fields='')
#   north_money 北向净流入、south_money 南向净流入（百万元）
def get_moneyflow_hsgt_sdk(start_date, end_date):
    """沪深港通资金流向"""
    return pro.moneyflow_hsgt(
        start_date=start_date, end_date=end_date,
        fields="trade_date,ggt_ss,ggt_sz,sgt_north_money,north_money,south_money",
    )


# ============ 接口：top_list 龙虎榜每日明细 ============
# 说明：获取每日龙虎榜上榜个股明细
# SDK 签名：pro.top_list(trade_date='', ts_code='', fields='')
#   trade_date 必填；net_amount 净买入额（元）
def get_top_list_sdk(trade_date):
    """龙虎榜每日明细"""
    return pro.top_list(
        trade_date=trade_date,
        fields="trade_date,ts_code,name,close,pct_change,amount,l_buy,l_sell,net_amount",
    )
