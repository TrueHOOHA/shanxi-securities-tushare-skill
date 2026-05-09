#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基金数据获取示例脚本（山西证券 Tushare）
"""

import sxsc_tushare as sx
import os

# 设置山西证券 Tushare token
# 注意：只需要在第一次或者 token 失效后调用
token = os.getenv('SXSC_TUSHARE_TOKEN') or 'YOUR_TOKEN_HERE'
sx.set_token(token)

# 初始化 Pro 接口
# env 参数：'prd' - 生产环境/纯Python环境, 'qa' - 仿真环境
pro = sx.get_api(env='prd')


def get_fund_list():
    """
    获取基金列表
    """
    try:
        data = pro.fund_basic(market='E', status='L', fields='ts_code,name,fund_type,found_date,issue_date,delist_date')
        print("基金列表获取成功：")
        print(data.head())
        return data
    except Exception as e:
        print(f"获取基金列表失败：{e}")
        return None


def get_fund_nav(ts_code, start_date, end_date):
    """
    获取基金净值数据
    """
    try:
        data = pro.fund_nav(ts_code=ts_code, start_date=start_date, end_date=end_date)
        print(f"{ts_code}基金净值数据获取成功：")
        print(data.head())
        return data
    except Exception as e:
        print(f"获取基金净值数据失败：{e}")
        return None


def get_fund_manager():
    """
    获取基金经理数据
    """
    try:
        data = pro.fund_manager(limit=10, fields='ts_code,name,ann_date,begin_date,end_date')
        print("基金经理数据获取成功：")
        print(data.head())
        return data
    except Exception as e:
        print(f"获取基金经理数据失败：{e}")
        return None


def main():
    """
    主函数
    """
    print("===== 山西证券 Tushare 基金数据获取示例 =====")
    
    # 获取基金列表
    fund_list = get_fund_list()
    
    if fund_list is not None:
        # 获取第一只基金的代码
        ts_code = fund_list['ts_code'].iloc[0]
        print(f"\n使用基金代码：{ts_code}")
        
        # 获取基金净值数据（最近30天）
        import datetime
        end_date = datetime.datetime.now().strftime('%Y%m%d')
        start_date = (datetime.datetime.now() - datetime.timedelta(days=30)).strftime('%Y%m%d')
        print(f"\n获取基金净值数据：{start_date} 至 {end_date}")
        get_fund_nav(ts_code, start_date, end_date)
    
    # 获取基金经理数据
    print("\n获取基金经理数据：")
    get_fund_manager()


if __name__ == "__main__":
    main()
