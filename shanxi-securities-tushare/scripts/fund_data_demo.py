#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基金数据获取示例脚本（山西证券 Tushare · HTTP 直连）

零外部依赖：仅使用 Python 标准库（urllib + json）通过 HTTP 调取数据，
无需安装 sxsc_tushare 专有包。若环境已安装 pandas，结果以 DataFrame 展示。
"""

import os
import json
import urllib.request

# 山西证券 Tushare HTTP 端点（Python / 仿真环境）
API_URL = "http://221.204.19.233:7172"

token = os.getenv("SXSC_TUSHARE_TOKEN", "")


def call_api(api_name, fields="", **params):
    """通用 HTTP 调取：POST JSON Body，返回 list[dict]。code 非 0 视为失败并抛错。"""
    payload = {
        "api_name": api_name,
        "token": token,
        "params": params,
        "fields": fields,
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if result.get("code") != 0:
        raise RuntimeError(
            f"接口 {api_name} 调用失败：code={result.get('code')} msg={result.get('msg')}"
        )
    data = result.get("data") or {}
    fields_list = data.get("fields") or []
    items = data.get("items") or []
    return [dict(zip(fields_list, row)) for row in items]


def show(rows, n=5):
    """有 pandas 则以 DataFrame 展示前 n 行，否则打印 list[dict]。"""
    if not rows:
        print("(空结果)")
        return
    try:
        import pandas as pd

        print(pd.DataFrame(rows).head(n))
    except ImportError:
        print(rows[:n])


def get_fund_list():
    """获取基金列表"""
    try:
        data = call_api(
            "fund_basic",
            fields="ts_code,name,fund_type,found_date,issue_date,delist_date",
            market="E",
            status="L",
        )
        print("基金列表获取成功：")
        show(data)
        return data
    except Exception as e:
        print(f"获取基金列表失败：{e}")
        return None


def get_fund_nav(ts_code, start_date, end_date):
    """获取基金净值数据"""
    try:
        data = call_api(
            "fund_nav",
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
        )
        print(f"{ts_code}基金净值数据获取成功：")
        show(data)
        return data
    except Exception as e:
        print(f"获取基金净值数据失败：{e}")
        return None


def get_fund_manager():
    """获取基金经理数据"""
    try:
        data = call_api(
            "fund_manager",
            fields="ts_code,name,ann_date,begin_date,end_date",
            limit=10,
        )
        print("基金经理数据获取成功：")
        show(data)
        return data
    except Exception as e:
        print(f"获取基金经理数据失败：{e}")
        return None


def main():
    """主函数"""
    print("===== 山西证券 Tushare 基金数据获取示例（HTTP 直连）=====")
    if not token:
        print("警告：未设置环境变量 SXSC_TUSHARE_TOKEN，调用将失败。")

    # 获取基金列表
    fund_list = get_fund_list()

    if fund_list:
        # 获取第一只基金的代码
        ts_code = fund_list[0]["ts_code"]
        print(f"\n使用基金代码：{ts_code}")

        # 获取基金净值数据（最近30天）
        import datetime

        end_date = datetime.datetime.now().strftime("%Y%m%d")
        start_date = (datetime.datetime.now() - datetime.timedelta(days=30)).strftime("%Y%m%d")
        print(f"\n获取基金净值数据：{start_date} 至 {end_date}")
        get_fund_nav(ts_code, start_date, end_date)

    # 获取基金经理数据
    print("\n获取基金经理数据：")
    get_fund_manager()


if __name__ == "__main__":
    main()
