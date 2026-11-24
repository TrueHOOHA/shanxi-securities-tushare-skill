#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票数据调用参考模板（山西证券 Tushare）

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


# ============ 接口：stock_basic 股票列表 ============
# 说明：获取上市公司基础信息（名称、行业、上市日期等）
# SDK 签名：pro.stock_basic(exchange='', list_status='', fields='', ts_code='')
#   exchange: 交易所（SSE/SZSE/BSE）；list_status: L上市/P退市/D暂停；fields: 逗号分隔字段
def get_stock_basic_sdk(exchange="", list_status="L"):
    """股票列表"""
    return pro.stock_basic(
        exchange=exchange,
        list_status=list_status,
        fields="ts_code,symbol,name,area,industry,list_date",
    )


# ============ 接口：daily 日线行情 ============
# 说明：获取 A 股未复权日线行情（ah_vol/ah_amount 为盘后量额）
# SDK 签名：pro.daily(ts_code='', trade_date='', start_date='', end_date='', fields='')
#   ts_code/trade_date 至少给一个；日期格式 YYYYMMDD
def get_daily_sdk(ts_code, start_date, end_date):
    """日线行情"""
    return pro.daily(
        ts_code=ts_code, start_date=start_date, end_date=end_date,
        fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
    )


# ============ 接口：fina_indicator 财务指标 ============
# 说明：获取上市公司财务指标（ROE、毛利率、EPS 等）
# SDK 签名：pro.fina_indicator(ts_code='', start_date='', end_date='', fields='')
#   注意：start_date/end_date 为公告日期而非报告期
def get_fina_indicator_sdk(ts_code, start_date, end_date):
    """财务指标"""
    return pro.fina_indicator(
        ts_code=ts_code, start_date=start_date, end_date=end_date,
        fields="ts_code,end_date,roe,roe_waa,grossprofit_margin,netprofit_margin,eps",
    )
