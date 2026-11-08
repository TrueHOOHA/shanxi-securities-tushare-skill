#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
财务三表数据调用参考模板（山西证券 Tushare）

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


# ============ 接口：income 利润表 ============
# 说明：获取上市公司利润表；当前只能按单只股票取历史数据
# SDK 签名：pro.income(ts_code='', start_date='', end_date='', period='', report_type='', fields='')
#   start_date/end_date 为公告日期；period 为报告期（如 20231231）
def get_income_sdk(ts_code, start_date, end_date):
    """利润表"""
    return pro.income(
        ts_code=ts_code, start_date=start_date, end_date=end_date,
        fields="ts_code,ann_date,end_date,report_type,basic_eps,total_revenue,revenue,operate_profit,total_profit,n_income,n_income_attr_p,ebitda,rd_exp",
    )


# ============ 接口：balancesheet 资产负债表 ============
# 说明：获取上市公司资产负债表；当前只能按单只股票取历史数据
# SDK 签名：pro.balancesheet(ts_code='', start_date='', end_date='', period='', fields='')
#   start_date/end_date 为公告日期
def get_balancesheet_sdk(ts_code, start_date, end_date):
    """资产负债表"""
    return pro.balancesheet(
        ts_code=ts_code, start_date=start_date, end_date=end_date,
        fields="ts_code,ann_date,end_date,report_type,total_assets,total_liab,total_hldr_eqy_exc_min_int,cap_rese,undistr_profit",
    )


# ============ 接口：cashflow 现金流量表 ============
# 说明：获取上市公司现金流量表；当前只能按单只股票取历史数据
# SDK 签名：pro.cashflow(ts_code='', start_date='', end_date='', period='', fields='')
#   start_date/end_date 为公告日期
def get_cashflow_sdk(ts_code, start_date, end_date):
    """现金流量表"""
    return pro.cashflow(
        ts_code=ts_code, start_date=start_date, end_date=end_date,
        fields="ts_code,ann_date,end_date,report_type,n_cashflow_act,n_cashflow_inv_act,n_cash_flows_fnc_act,c_pay_acq_const_fiolta",
    )
