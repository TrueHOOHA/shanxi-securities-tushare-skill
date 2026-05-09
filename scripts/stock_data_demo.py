#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票数据获取示例脚本（山西证券 Tushare）
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


def get_stock_list():
    """
    获取股票列表
    """
    try:
        data = pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name,area,industry,list_date')
        print("股票列表获取成功：")
        print(data.head())
        return data
    except Exception as e:
        print(f"获取股票列表失败：{e}")
        return None


def get_daily_data(ts_code, start_date, end_date):
    """
    获取股票日线数据
    """
    try:
        data = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        print(f"{ts_code}日线数据获取成功：")
        print(data.head())
        return data
    except Exception as e:
        print(f"获取日线数据失败：{e}")
        return None


def get_financial_data(ts_code, start_date, end_date):
    """
    获取财务指标数据
    """
    try:
        data = pro.fina_indicator(ts_code=ts_code, start_date=start_date, end_date=end_date)
        print(f"{ts_code}财务指标数据获取成功：")
        print(data.head())
        return data
    except Exception as e:
        print(f"获取财务指标数据失败：{e}")
        return None


def main():
    """
    主函数
    """
    print("===== 山西证券 Tushare 股票数据获取示例 =====")
    
    # 获取股票列表
    stock_list = get_stock_list()
    
    if stock_list is not None:
        # 获取第一只股票的代码
        ts_code = stock_list['ts_code'].iloc[0]
        print(f"\n使用股票代码：{ts_code}")
        
        # 获取日线数据（最近30天）
        import datetime
        end_date = datetime.datetime.now().strftime('%Y%m%d')
        start_date = (datetime.datetime.now() - datetime.timedelta(days=30)).strftime('%Y%m%d')
        print(f"\n获取日线数据：{start_date} 至 {end_date}")
        get_daily_data(ts_code, start_date, end_date)
        
        # 获取财务数据（最近一年）
        fin_end_date = end_date
        fin_start_date = (datetime.datetime.now() - datetime.timedelta(days=365)).strftime('%Y%m%d')
        print(f"\n获取财务数据：{fin_start_date} 至 {fin_end_date}")
        get_financial_data(ts_code, fin_start_date, fin_end_date)


if __name__ == "__main__":
    main()
