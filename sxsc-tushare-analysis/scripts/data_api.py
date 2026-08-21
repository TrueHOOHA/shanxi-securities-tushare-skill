#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一数据访问层（Data API）。

职责：
- 封装 SDK / HTTP 双模式切换
- 提供股票、指数、基金、财务、资金流等统一取数接口
- 统一处理：T-0 占位行过滤、排序、字段校验、异常降级

用法：
    api = DataAPI()
    df = api.get_daily(ts_code="600519.SH", start_date="20250101", end_date="20251231")
"""

import os
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

import pandas as pd


# ---------- 工具函数 ----------

def shift_date(date_str: str, days: int) -> str:
    d = datetime.strptime(date_str, "%Y%m%d")
    return (d + timedelta(days=days)).strftime("%Y%m%d")


# ---------- 进程级速率限制器 ----------
# 多维度并行取数时，所有 SDK/HTTP 调用共享同一个限流器，
# 确保每秒请求不超过 MAX_RPS 次，避免触发服务端限流/封禁。
_MAX_RPS = 30
_rate_lock = threading.Lock()
_rate_timestamps: deque = deque()


def _acquire_rate_slot() -> None:
    """阻塞直至获得一个请求配额（滑动窗口限流，每秒 ≤ _MAX_RPS 次）。"""
    while True:
        with _rate_lock:
            now = time.monotonic()
            # 清理 1 秒窗口外的时间戳
            while _rate_timestamps and _rate_timestamps[0] <= now - 1.0:
                _rate_timestamps.popleft()
            if len(_rate_timestamps) < _MAX_RPS:
                _rate_timestamps.append(now)
                return
            # 窗口已满，算出到最早时间戳过期还需多久
            wait = 1.0 - (now - _rate_timestamps[0])
        if wait > 0:
            time.sleep(wait)


def ensure_sorted(df: pd.DataFrame, date_col: str = "trade_date") -> pd.DataFrame:
    if df is None or df.empty:
        return df
    if date_col in df.columns:
        return df.sort_values(date_col).reset_index(drop=True)
    return df


def drop_t0_placeholder(df: Optional[pd.DataFrame], price_cols: List[str] = ("close",)) -> Optional[pd.DataFrame]:
    """Tushare 传当天 end_date 时会返回 T-0 占位行（价格全 NaN），需要剔除。"""
    if df is None or df.empty:
        return df
    subset = [c for c in price_cols if c in df.columns]
    if not subset:
        return df
    return df.dropna(subset=subset).reset_index(drop=True)


def safe_call(func: Callable[..., pd.DataFrame], *args, **kwargs) -> Optional[pd.DataFrame]:
    """统一 API 调用封装：异常和空结果都返回 None。"""
    try:
        df = func(*args, **kwargs)
        if df is None or df.empty:
            return None
        return df
    except Exception:
        return None


# ---------- DataAPI 类 ----------

class DataAPI:
    """山西证券 Tushare 统一数据访问接口。"""

    DEFAULT_HTTP_URL = "http://221.204.19.233:7172"

    def __init__(self, mode: Optional[str] = None, env: str = "prd"):
        """
        Args:
            mode: "sdk" 或 "http"，None 时自动检测
            env: "prd"（仿真/纯 Python）或 "qa"（生产）
        """
        self.env = env
        self.token = os.getenv("SXSC_TUSHARE_TOKEN")
        self.http_url = os.getenv("SXSC_TUSHARE_HTTP_URL") or self.DEFAULT_HTTP_URL

        if mode is None:
            self.mode = self._detect_mode()
        else:
            self.mode = mode

        self._pro = None
        if self.mode == "sdk":
            try:
                import sxsc_tushare as sx
                sx.set_token(self.token)
                self._pro = sx.get_api(env=self.env)
            except Exception:
                # SDK 初始化失败时降级为 HTTP
                self.mode = "http"

    def _detect_mode(self) -> str:
        try:
            import sxsc_tushare as sx  # noqa: F401
            return "sdk"
        except ImportError:
            return "http"

    def _api(self):
        if self.mode == "sdk":
            return self._pro
        return None

    def _http_call(self, api_name: str, params: Dict[str, Any], fields: str) -> Optional[pd.DataFrame]:
        """HTTP 通用调用。"""
        if not self.token:
            return None
        try:
            import requests
            resp = requests.post(
                self.http_url,
                json={"api_name": api_name, "token": self.token, "params": params, "fields": fields},
                timeout=60,
            )
            data = resp.json()
            if data.get("code") != 0:
                return None
            return pd.DataFrame(data["data"]["items"], columns=data["data"]["fields"])
        except Exception:
            return None

    def _call(self, api_name: str, params: Dict[str, Any], fields: str) -> Optional[pd.DataFrame]:
        """统一调用：SDK 优先，否则 HTTP。"""
        _acquire_rate_slot()
        if self.mode == "sdk" and self._pro is not None:
            api = getattr(self._pro, api_name, None)
            if api is not None:
                return safe_call(api, **params, fields=fields)
        return self._http_call(api_name, params, fields)

    # ---------- A 股基础 ----------

    def get_stock_basic(self, ts_code: str, fields: Optional[str] = None) -> Optional[pd.DataFrame]:
        f = fields or "ts_code,symbol,name,area,industry,list_date,exchange,list_status"
        return self._call("stock_basic", {"ts_code": ts_code}, f)

    def get_stock_company(self, ts_code: str, fields: Optional[str] = None) -> Optional[pd.DataFrame]:
        f = fields or "ts_code,employees,main_business,reg_capital,province,city"
        return self._call("stock_company", {"ts_code": ts_code}, f)

    # ---------- 行情 ----------

    def get_daily(self, ts_code: str, start_date: str, end_date: str, fields: Optional[str] = None) -> Optional[pd.DataFrame]:
        f = fields or "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
        df = self._call("daily", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date}, f)
        return drop_t0_placeholder(df, ["close"])

    def get_adj_factor(self, ts_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        return self._call("adj_factor", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,trade_date,adj_factor")

    def get_daily_basic(self, ts_code: str, start_date: str, end_date: str, fields: Optional[str] = None) -> Optional[pd.DataFrame]:
        f = fields or ("ts_code,trade_date,close,turnover_rate,volume_ratio,"
                       "pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,total_mv,circ_mv")
        df = self._call("daily_basic", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date}, f)
        return drop_t0_placeholder(df, ["close"])

    def get_index_daily(self, ts_code: str, start_date: str, end_date: str, fields: Optional[str] = None) -> Optional[pd.DataFrame]:
        f = fields or "ts_code,trade_date,close,open,high,low,pre_close,change,pct_chg,vol,amount"
        df = self._call("index_daily", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date}, f)
        return drop_t0_placeholder(df, ["close"])

    # ---------- 财务 ----------

    def get_fina_indicator(self, ts_code: str, end_date: str, lookback_days: int = 800,
                           fields: Optional[str] = None) -> Optional[pd.DataFrame]:
        f = fields or ("ts_code,end_date,ann_date,roe,roe_waa,grossprofit_margin,"
                       "netprofit_margin,eps,debt_to_assets,current_ratio,assets_turn")
        start_date = shift_date(end_date, -lookback_days)
        return self._call("fina_indicator", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date}, f)

    def get_forecast(self, ts_code: str, end_date: str, lookback_days: int = 800) -> Optional[pd.DataFrame]:
        start_date = shift_date(end_date, -lookback_days)
        return self._call("forecast", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,end_date,ann_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max")

    # ---------- 资金流向 ----------

    def get_moneyflow(self, ts_code: str, end_date: str, lookback_days: int = 60) -> Optional[pd.DataFrame]:
        start_date = shift_date(end_date, -lookback_days)
        return self._call("moneyflow", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,trade_date,buy_sm_amount,sell_sm_amount,buy_md_amount,sell_md_amount,"
                          "buy_lg_amount,sell_lg_amount,buy_elg_amount,sell_elg_amount,net_mf_amount")

    def get_hsgt_money(self, end_date: str, lookback_days: int = 60) -> Optional[pd.DataFrame]:
        start_date = shift_date(end_date, -lookback_days)
        return self._call("moneyflow_hsgt", {"start_date": start_date, "end_date": end_date},
                          "trade_date,north_money,south_money")

    # ---------- 股东/筹码 ----------

    def get_stk_holdernumber(self, ts_code: str, end_date: str, lookback_days: int = 800) -> Optional[pd.DataFrame]:
        start_date = shift_date(end_date, -lookback_days)
        return self._call("stk_holdernumber", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,end_date,holder_num")

    def get_stk_holdertrade(self, ts_code: str, end_date: str, lookback_days: int = 180) -> Optional[pd.DataFrame]:
        start_date = shift_date(end_date, -lookback_days)
        return self._call("stk_holdertrade", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,ann_date,holder_name,change_amount,change_ratio")

    # ---------- 两融 ----------

    def get_margin_detail(self, ts_code: str, end_date: str, lookback_days: int = 60) -> Optional[pd.DataFrame]:
        start_date = shift_date(end_date, -lookback_days)
        return self._call("margin_detail", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,trade_date,rzye,rzmre,rqyl,rzrqye")

    # ---------- 市场异动 ----------

    def get_top_list(self, trade_date: str) -> Optional[pd.DataFrame]:
        return self._call("top_list", {"trade_date": trade_date},
                          "trade_date,ts_code,name,close,pct_change,amount,l_buy,l_sell,net_amount")

    def get_limit_list_d(self, trade_date: str) -> Optional[pd.DataFrame]:
        return self._call("limit_list_d", {"trade_date": trade_date},
                          "trade_date,ts_code,name,close,pct_chg,fc_ratio,fl_ratio,fd_amount,first_time,last_time")

    # ---------- 解禁 ----------

    def get_share_float(self, ts_code: str, end_date: str, lookforward_days: int = 90) -> Optional[pd.DataFrame]:
        float_end = shift_date(end_date, lookforward_days)
        return self._call("share_float", {"ts_code": ts_code, "start_date": end_date, "end_date": float_end},
                          "ts_code,float_date,float_share,float_ratio,holder_name")

    # ---------- 市场异动补充 ----------

    def get_top_inst(self, trade_date: str) -> Optional[pd.DataFrame]:
        return self._call("top_inst", {"trade_date": trade_date},
                          "trade_date,ts_code,name,b_amount,s_amount,net_amount,reason")

    # ---------- 宏观数据 ----------

    def get_cn_cpi(self, start_m: str, end_m: str) -> Optional[pd.DataFrame]:
        return self._call("cn_cpi", {"start_m": start_m, "end_m": end_m},
                          "month,nt_val,nt_yoy,nt_mom,nt_accu")

    def get_cn_ppi(self, start_m: str, end_m: str) -> Optional[pd.DataFrame]:
        return self._call("cn_ppi", {"start_m": start_m, "end_m": end_m},
                          "month,ppi_yoy,ppi_mom,ppi_accu")

    def get_shibor_lpr(self, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        return self._call("shibor_lpr", {"start_date": start_date, "end_date": end_date},
                          "date,1y,5y")

    def get_cn_gdp(self, start_quarter: str, end_quarter: str) -> Optional[pd.DataFrame]:
        return self._call("cn_gdp", {"start_q": start_quarter, "end_q": end_quarter},
                          "quarter,gdp,gdp_yoy,pi,si,ti")

    # ---------- 基金 ----------

    def get_fund_basic(self, ts_code: str, fields: Optional[str] = None) -> Optional[pd.DataFrame]:
        f = fields or "ts_code,name,fund_type,found_date,issue_date,delist_date"
        return self._call("fund_basic", {"ts_code": ts_code}, f)

    def get_fund_nav(self, ts_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        return self._call("fund_nav", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,nav_date,unit_nav,accum_nav,adj_nav")

    def get_fund_adj(self, ts_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        return self._call("fund_adj", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,trade_date,adj_factor")

    def get_fund_daily(self, ts_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        df = self._call("fund_daily", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount")
        return drop_t0_placeholder(df, ["close"])

    def get_fund_manager(self, ts_code: str) -> Optional[pd.DataFrame]:
        return self._call("fund_manager", {"ts_code": ts_code},
                          "ts_code,name,ann_date,begin_date,end_date")

    def get_fund_portfolio(self, ts_code: str, end_date: str, lookback_days: int = 800) -> Optional[pd.DataFrame]:
        start_date = shift_date(end_date, -lookback_days)
        return self._call("fund_portfolio", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,ann_date,end_date,symbol,mkv,amount,stk_mkv_ratio")

    def get_fund_share(self, ts_code: str, end_date: str, lookback_days: int = 400) -> Optional[pd.DataFrame]:
        start_date = shift_date(end_date, -lookback_days)
        return self._call("fund_share", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,trade_date,fd_share")

    def get_fund_div(self, ts_code: str) -> Optional[pd.DataFrame]:
        return self._call("fund_div", {"ts_code": ts_code},
                          "ts_code,ann_date,ex_date,record_date,pay_date,div_cash")

    # ---------- 指数 ----------

    def get_index_basic(self, ts_code: str, fields: Optional[str] = None) -> Optional[pd.DataFrame]:
        f = fields or "ts_code,name,market,publisher,category,base_date,base_point,list_date"
        return self._call("index_basic", {"ts_code": ts_code}, f)

    def get_index_weight(self, index_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        return self._call("index_weight", {"index_code": index_code, "start_date": start_date, "end_date": end_date},
                          "trade_date,con_code,weight")

    def get_index_member(self, index_code: str) -> Optional[pd.DataFrame]:
        return self._call("index_member", {"index_code": index_code},
                          "index_code,con_code,con_name,in_date,out_date")

    def get_index_global(self, ts_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        df = self._call("index_global", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,trade_date,close,open,high,low,pre_close,change,pct_chg")
        return drop_t0_placeholder(df, ["close"])

    def get_trade_cal(self, exchange: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        return self._call("trade_cal", {"exchange": exchange, "start_date": start_date, "end_date": end_date},
                          "exchange,cal_date,is_open,pretrade_date")

    # ---------- 股东筹码补充 ----------

    def get_top10_holders(self, ts_code: str, end_date: str, lookback_days: int = 400) -> Optional[pd.DataFrame]:
        start_date = shift_date(end_date, -lookback_days)
        return self._call("top10_holders", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,ann_date,end_date,holder_name,hold_amount,hold_ratio,hold_float_ratio,hold_change,holder_type")

    def get_top10_floatholders(self, ts_code: str, end_date: str, lookback_days: int = 400) -> Optional[pd.DataFrame]:
        start_date = shift_date(end_date, -lookback_days)
        return self._call("top10_floatholders", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,ann_date,end_date,holder_name,hold_amount,hold_ratio,hold_float_ratio,hold_change,holder_type")

    # ---------- 大宗交易 ----------

    def get_block_trade(self, ts_code: str, end_date: str, lookback_days: int = 60) -> Optional[pd.DataFrame]:
        start_date = shift_date(end_date, -lookback_days)
        return self._call("block_trade", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,trade_date,price,vol,amount,buyer,seller")

    # ---------- 全市场两融汇总 ----------

    def get_margin(self, end_date: str, exchange_id: str = "SSE", lookback_days: int = 60) -> Optional[pd.DataFrame]:
        start_date = shift_date(end_date, -lookback_days)
        return self._call("margin", {"exchange_id": exchange_id, "start_date": start_date, "end_date": end_date},
                          "trade_date,exchange_id,rzye,rzmre,rzche,rqye,rqmcl,rzrqye,rqyl")

    # ---------- 财务报表 ----------

    def get_income(self, ts_code: str, end_date: str, lookback_days: int = 800) -> Optional[pd.DataFrame]:
        start_date = shift_date(end_date, -lookback_days)
        return self._call("income", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,total_revenue,revenue,n_income,n_income_attr_p")

    def get_cashflow(self, ts_code: str, end_date: str, lookback_days: int = 800) -> Optional[pd.DataFrame]:
        start_date = shift_date(end_date, -lookback_days)
        return self._call("cashflow", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,n_cashflow_act")

    def get_balancesheet(self, ts_code: str, end_date: str, lookback_days: int = 800) -> Optional[pd.DataFrame]:
        start_date = shift_date(end_date, -lookback_days)
        return self._call("balancesheet", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,total_share")

    # ---------- 行业分类 ----------

    def get_index_classify(self, level: str = "L1", src: str = "SW2021") -> Optional[pd.DataFrame]:
        return self._call("index_classify", {"level": level, "src": src},
                          "index_code,industry_name,parent_code,level,industry_code,is_pub,src")

    # ---------- 期货 ----------

    def _fut_sdk_call(self, api_name: str, params: Dict[str, Any]) -> Optional[pd.DataFrame]:
        """期货接口SDK调用：不传fields，规避定制SDK字段名校验，返回全字段本地选列。"""
        _acquire_rate_slot()
        if self.mode == "sdk" and self._pro is not None:
            api = getattr(self._pro, api_name, None)
            if api is not None:
                return safe_call(api, **params)
        return self._http_call(api_name, params, "")

    def get_fut_basic(self, exchange: Optional[str] = None, fut_type: Optional[str] = None,
                      ts_code: Optional[str] = None) -> Optional[pd.DataFrame]:
        params = {k: v for k, v in [("exchange", exchange), ("fut_type", fut_type), ("ts_code", ts_code)]
                  if v is not None}
        return self._fut_sdk_call("fut_basic", params)

    def get_fut_daily(self, ts_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        df = self._fut_sdk_call("fut_daily", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date})
        return drop_t0_placeholder(df, ["close"])

    def get_fut_mapping(self, ts_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        return self._fut_sdk_call("fut_mapping", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date})

    def get_fut_holding(self, ts_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        return self._fut_sdk_call("fut_holding", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date})

    def get_fut_wsr(self, trade_date: str) -> Optional[pd.DataFrame]:
        return self._fut_sdk_call("fut_wsr", {"trade_date": trade_date})

    def get_fut_settle(self, ts_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        return self._fut_sdk_call("fut_settle", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date})
