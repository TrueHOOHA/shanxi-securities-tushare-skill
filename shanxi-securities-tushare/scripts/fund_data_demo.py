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
import sys

import pandas as pd
import sxsc_tushare as sx

# ============ 环境初始化 ============
# token 来源：token 文件（默认 ~/sxsc_tk.csv，即 sx.set_token() 写入的位置），不使用环境变量。
# 文件名随包构建而变，统一读 upass.TOKEN_F_P；首次使用先运行：
#   python -c "import sxsc_tushare as sx; sx.set_token('你的token')"


def _token_path():
    import sxsc_tushare.upass as up
    return os.path.join(os.path.expanduser("~"), getattr(up, "TOKEN_F_P", None) or "sxsc_tk.csv")


def _load_token():
    path = _token_path()
    if not os.path.isfile(path):
        return None
    try:
        # dtype=str：纯数字 token 不丢前导零
        df = pd.read_csv(path, dtype=str)
        if not df.empty and "token" in df.columns:
            val = df.loc[0, "token"]
            if not pd.isna(val) and str(val).strip():
                return str(val).strip()
    except Exception:
        pass
    # 容忍无表头单行 token（含 UTF-16/GBK）；多行无 token 表头不猜，避免把表头当凭证
    text = None
    for enc in ("utf-8-sig", "utf-16", "gbk"):
        try:
            with open(path, "r", encoding=enc) as f:
                text = f.read()
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError:
            return None
    if text is None:
        return None
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and not ln.lstrip().startswith("#")]
    for i, ln in enumerate(lines):
        _low = ln.lower()
        if _low == "token":
            if i + 1 < len(lines):
                return lines[i + 1].split(",")[0].strip() or None
            return None
        if _low.startswith("token,"):
            return ln.split(",", 1)[1].strip() or None
    if len(lines) == 1:
        return lines[0].split(",")[0].strip() or None
    return None


token = _load_token()
if not token:
    print("未找到 token，请先创建 token 文件：" + _token_path(), file=sys.stderr)
    print("  推荐用 SDK 写入：python -c \"import sxsc_tushare as sx; sx.set_token('你的token')\"", file=sys.stderr)
    print("  或手工创建 csv：首行 token，次行 token 值（UTF-8 无 BOM）", file=sys.stderr)
    sys.exit(1)

# 直接把 token 交给 get_api：不调 set_token，不重写 token 文件
pro = sx.get_api(token, env="prd")  # 'prd' 仿真, 'qa' 生产


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
