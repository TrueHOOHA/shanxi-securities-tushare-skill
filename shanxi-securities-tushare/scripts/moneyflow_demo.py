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
        fields="trade_date,ggt_ss,ggt_sz,north_money,south_money",
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
