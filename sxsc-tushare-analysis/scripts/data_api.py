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
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

import pandas as pd


# ---------- 数据通道提示 ----------
# SDK 初始化失败会自动降级为 HTTP 协议。降级本身不影响取数结果，但必须显式暴露，
# 否则日志与报告都看不出实际走的是哪条通道（曾出现 check_env 报 sdk、实际走 http 的情况）。
_CHANNEL_NOTES: List[str] = []

# ---------- 取数错误记录 ----------
# 服务端对不存在的字段/非法参数会返回 {"code": 40101, "msg": "接口xxx不包含字段yyy"}。
# 早期实现直接 `return None` 把 msg 丢掉了，使"字段名写错"与"确实没有数据"完全同形，
# 下游只能静默降级——曾导致涨跌停/龙虎榜机构/游资/股东增减持四个子项永久取不到数据，
# 而报告把它们呈现为"无记录"。这里统一留痕，供日志与报告页脚展示。
_ERRORS: List[Dict[str, Any]] = []


def _record_error(api_name: str, reason: str, detail: str = "") -> bool:
    """记录一次取数失败。同一 (接口, 原因) 去重计数。返回是否为首见。"""
    for e in _ERRORS:
        if e["api"] == api_name and e["reason"] == reason:
            e["count"] += 1
            return False
    _ERRORS.append({"api": api_name, "reason": reason, "detail": detail, "count": 1})
    return True


def data_channel_note() -> Optional[str]:
    """返回本进程内数据通道的降级说明；无降级时返回 None。"""
    return "；".join(dict.fromkeys(_CHANNEL_NOTES)) or None


def data_errors() -> List[Dict[str, Any]]:
    """返回本进程内取数失败记录（字段名错/参数错/权限不足等）；无失败时为空列表。"""
    return [dict(e) for e in _ERRORS]


def data_error_note(limit: int = 3) -> Optional[str]:
    """取数失败的简要说明（供报告页脚展示），避免"取数失败"被当成"无数据"。"""
    if not _ERRORS:
        return None
    items = [f"{e['api']}：{e['reason']}" for e in _ERRORS[:limit]]
    more = f"，另有 {len(_ERRORS) - limit} 个接口失败" if len(_ERRORS) > limit else ""
    return "；".join(items) + more


# ---------- 工具函数 ----------

def shift_date(date_str: str, days: int) -> str:
    """按**自然日**回溯日期（YYYYMMDD）。

    ⚠️ 这是自然日，不是交易日！"近 250 交易日"不能写成 -250（约等于 166 交易日），
    "近 5 年"不能写成 -250*5（约 3.4 年）。需要按交易日回溯时用 `shift_trade_days`，
    或至少按 交易日≈自然日*365/250 折算（250 交易日 ≈ 365 自然日）。
    """
    d = datetime.strptime(date_str, "%Y%m%d")
    return (d + timedelta(days=days)).strftime("%Y%m%d")


def shift_trade_days(date_str: str, days: int, api: Optional["DataAPI"] = None) -> str:
    """按交易日回溯（经 trade_cal 校准），用于"近 N 交易日"口径的窗口计算。

    与 shift_date（自然日）区分：涉及"交易日"语义的窗口必须用本函数，
    否则窗口系统性偏短约 1/3（如 250 自然日 ≈ 166 交易日）。
    失败时回退为自然日估算（250 交易日 ≈ 365 自然日），并如实返回。
    """
    try:
        end = date_str
        api = api or DataAPI()
        cal = api.get_trade_cal("SSE", shift_date(end, -max(days * 2, 366)), end)
        if cal is not None and not cal.empty and "cal_date" in cal.columns:
            is_open = cal["is_open"].astype(str).str.upper() if "is_open" in cal.columns else None
            dates = cal["cal_date"].astype(str).tolist()
            if is_open is not None:
                dates = [d for d, o in zip(dates, is_open) if o in ("1", "Y", "TRUE", "True")]
            dates = sorted(set(dates))
            if dates and dates[-1] <= end:
                idx = len(dates) - 1
                if days <= idx:
                    return dates[idx - days]
        return shift_date(end, -int(round(days * 365 / 250)))
    except Exception:
        return shift_date(date_str, -int(round(days * 365 / 250)))


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
    """统一 API 调用封装：异常和空结果都返回 None。

    注意：返回值无法区分"接口报错"与"确实无数据"（历史设计，调用方依赖 None 判空）。
    因此这里额外把失败原因记入 _ERRORS，异常不再彻底消失。
    """
    name = getattr(func, "__name__", "unknown")
    try:
        df = func(*args, **kwargs)
    except Exception as exc:
        if _record_error(name, "调用异常", f"{type(exc).__name__}: {exc}"):
            print(f"[data_api] 取数异常 {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None
    if df is None or df.empty:
        # 空结果不是错误，但要与"报错"区分开：这里不记 _ERRORS
        return None
    return df


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
        # tushare 数据为 T-1：最新可用日期 = 昨天（自然日-1），接口层据此裁剪避免取到 T-0 占位行
        self._t_minus_1 = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

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
            except Exception as exc:
                # SDK 初始化失败时降级为 HTTP（记录并提示，避免静默降级）
                self.mode = "http"
                note = f"SDK 初始化失败，已降级为 HTTP 协议（{type(exc).__name__}: {exc}）"
                if note not in _CHANNEL_NOTES:
                    _CHANNEL_NOTES.append(note)
                print(f"[data_api] {note}", file=sys.stderr)

    def _detect_mode(self) -> str:
        try:
            import sxsc_tushare as sx  # noqa: F401
            return "sdk"
        except ImportError:
            return "http"

    def _cap_dates(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """裁剪 end_date/trade_date 不超过 T-1（tushare 只有 T-1 数据，避免取到 T-0 占位行）。

        仅裁 8 位 YYYYMMDD 日期，不影响 start_m/end_m（6 位月份）或 start_q/end_q（季度）。
        """
        capped = dict(params)
        for k in ("end_date", "trade_date"):
            v = capped.get(k)
            if isinstance(v, str) and len(v) == 8 and v > self._t_minus_1:
                capped[k] = self._t_minus_1
        return capped

    def _api(self):
        if self.mode == "sdk":
            return self._pro
        return None

    def _http_call(self, api_name: str, params: Dict[str, Any], fields: str) -> Optional[pd.DataFrame]:
        """HTTP 通用调用。

        服务端对不存在的字段/非法参数返回 code != 0 并附带 msg；早期实现把它丢掉了，
        使"字段名写错"与"确实无数据"同形。现在记入 _ERRORS 并打 stderr（同一错误只打一次）。
        """
        if not self.token:
            if _record_error(api_name, "缺少 token", "环境变量 SXSC_TUSHARE_TOKEN 未设置"):
                print("[data_api] 未设置 SXSC_TUSHARE_TOKEN，无法取数", file=sys.stderr)
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
                msg = data.get("msg") or data.get("message") or f"code={data.get('code')}"
                if _record_error(api_name, str(msg), f"params={params} fields={fields}"):
                    print(f"[data_api] 取数失败 {api_name}: {msg}", file=sys.stderr)
                return None
            return pd.DataFrame(data["data"]["items"], columns=data["data"]["fields"])
        except Exception as exc:
            if _record_error(api_name, f"HTTP 调用异常（{type(exc).__name__}）", str(exc)):
                print(f"[data_api] HTTP 调用异常 {api_name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return None

    def _call(self, api_name: str, params: Dict[str, Any], fields: str) -> Optional[pd.DataFrame]:
        """统一调用：SDK 优先，否则 HTTP。"""
        _acquire_rate_slot()
        # share_float 查未来解禁计划，end_date 是未来窗口上界，不裁剪
        if api_name != "share_float":
            params = self._cap_dates(params)
        if self.mode == "sdk" and self._pro is not None:
            api = getattr(self._pro, api_name, None)
            if api is not None:
                return safe_call(api, **params, fields=fields)
        return self._http_call(api_name, params, fields)

    # ---------- A 股基础 ----------

    def get_stock_basic(self, ts_code: Optional[str] = None, fields: Optional[str] = None,
                        list_status: Optional[str] = None) -> Optional[pd.DataFrame]:
        """个股或全市场基础信息；ts_code/list_status 均可选（指数成分行业匹配用全市场查询）。"""
        f = fields or "ts_code,symbol,name,area,industry,list_date,exchange,list_status"
        params: Dict[str, Any] = {}
        if ts_code:
            params["ts_code"] = ts_code
        if list_status:
            params["list_status"] = list_status
        return self._call("stock_basic", params, f)

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
        """大股东增减持（变动数量字段为 `change_vol`，单位：股）。

        ⚠️ 原实现请求 `change_amount`，服务端 schema 无此列 → 40101 → 恒为空，
        迫使增减持只能走 top10_holders 降级路径，且报告结论仍写"公告口径/近半年"。
        增减方向用 `in_de`（IN 增持 / DE 减持）。
        """
        start_date = shift_date(end_date, -lookback_days)
        return self._call("stk_holdertrade", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,ann_date,holder_name,holder_type,in_de,change_vol,change_ratio,"
                          "after_share,after_ratio,avg_price,total_share")

    # ---------- 两融 ----------

    def get_margin_detail(self, ts_code: str, end_date: str, lookback_days: int = 60) -> Optional[pd.DataFrame]:
        start_date = shift_date(end_date, -lookback_days)
        return self._call("margin_detail", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,trade_date,rzye,rzmre,rqyl,rzrqye")

    def get_margin_secs(self, ts_code: str, end_date: str, lookback_days: int = 30) -> Optional[pd.DataFrame]:
        """融资融券标的名单（盘前更新）：用于判断标的是否为两融标的。"""
        start_date = shift_date(end_date, -lookback_days)
        return self._call("margin_secs", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "trade_date,ts_code,name,exchange")

    # ---------- 市场异动 ----------

    def get_top_list(self, trade_date: str) -> Optional[pd.DataFrame]:
        return self._call("top_list", {"trade_date": trade_date},
                          "trade_date,ts_code,name,close,pct_change,amount,l_buy,l_sell,net_amount")

    def get_limit_list_d(self, trade_date: str) -> Optional[pd.DataFrame]:
        """涨跌停列表（新）。

        ⚠️ 字段名以服务端实际 schema 为准：服务端对不存在的字段直接返回 40101，
        原实现请求的 `fc_ratio`/`fl_ratio` 并不存在，导致涨跌停记录**恒为空**，
        而报告把它呈现为"该股无涨跌停记录"（实测：万科A 20260929 收于涨停价仍未被记录）。
        """
        return self._call("limit_list_d", {"trade_date": trade_date},
                          "trade_date,ts_code,industry,name,close,pct_chg,amount,limit_amount,"
                          "float_mv,total_mv,turnover_ratio,fd_amount,first_time,last_time,"
                          "open_times,up_stat,limit_times,limit")

    # ---------- 解禁 ----------

    def get_share_float(self, ts_code: str, end_date: str, lookforward_days: int = 90) -> Optional[pd.DataFrame]:
        float_end = shift_date(end_date, lookforward_days)
        return self._call("share_float", {"ts_code": ts_code, "start_date": end_date, "end_date": float_end},
                          "ts_code,float_date,float_share,float_ratio,holder_name")

    # ---------- 市场异动补充 ----------

    def get_top_inst(self, trade_date: str) -> Optional[pd.DataFrame]:
        """龙虎榜机构明细。

        ⚠️ 服务端实际 schema 为 exalter/side/buy/buy_rate/sell/sell_rate/net_buy/reason，
        没有 name/b_amount/s_amount/net_amount（原实现四个字段全取错 → 机构明细恒为空）。
        机构名称在 `exalter`，买卖净额在 `net_buy`。
        """
        return self._call("top_inst", {"trade_date": trade_date},
                          "trade_date,ts_code,exalter,side,buy,buy_rate,sell,sell_rate,net_buy,reason")


    def get_stk_shock(self, ts_code: str, end_date: str, lookback_days: int = 250) -> Optional[pd.DataFrame]:
        """个股异常波动记录。"""
        start_date = shift_date(end_date, -lookback_days)
        return self._call("stk_shock", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,trade_date,name,trade_market,reason,period")

    def get_stk_high_shock(self, ts_code: str, end_date: str, lookback_days: int = 250) -> Optional[pd.DataFrame]:
        """个股严重异常波动记录。"""
        start_date = shift_date(end_date, -lookback_days)
        return self._call("stk_high_shock", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,trade_date,name,trade_market,reason,period")

    def get_stk_alert(self, ts_code: str, end_date: str, lookback_days: int = 250) -> Optional[pd.DataFrame]:
        """交易所重点提示证券（风险警示类）。"""
        start_date = shift_date(end_date, -lookback_days)
        return self._call("stk_alert", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,name,start_date,end_date,type")

    def get_stk_limit(self, ts_code: str, end_date: str, lookback_days: int = 10) -> Optional[pd.DataFrame]:
        """每日涨跌停价格。"""
        start_date = shift_date(end_date, -lookback_days)
        return self._call("stk_limit", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "trade_date,ts_code,pre_close,up_limit,down_limit")

    def get_hm_detail(self, ts_code: str, end_date: str, lookback_days: int = 120) -> Optional[pd.DataFrame]:
        """游资每日明细（净额为 `buy_sell_count`，单位：元）。

        ⚠️ 实测服务端 schema 与官方文档**完全不同**：
          服务端真实列 = rank_date/ts_code/name/buy_num/sell_num/buy_sell_count/category/ins_name
          文档所列字段 = trade_date/ts_name/buy_amount/sell_amount/net_amount/hm_name/hm_orgs/tag
        原实现按文档请求 9 个字段（其中 8 个不存在）→ 服务端 40101 → 游资子项永久失效。
        """
        start_date = shift_date(end_date, -lookback_days)
        return self._call("hm_detail", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "rank_date,ts_code,name,buy_num,sell_num,buy_sell_count,category,ins_name")

    def get_hm_list(self) -> Optional[pd.DataFrame]:
        """游资名录。

        ⚠️ 服务端实际 schema 为 category/description/relate_part（文档所写 name/desc 均不存在）。
        """
        return self._call("hm_list", {}, "category,description,relate_part")

    def get_suspend_d(self, ts_code: str, end_date: str, lookback_days: int = 60) -> Optional[pd.DataFrame]:
        """每日停复牌信息（suspend_type: S-停牌 R-复牌）。"""
        start_date = shift_date(end_date, -lookback_days)
        return self._call("suspend_d", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,trade_date,suspend_timing,suspend_type")
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

    def get_cn_m(self, start_m: str, end_m: str) -> Optional[pd.DataFrame]:
        """货币供应量（M0/M1/M2 月度同比）。"""
        return self._call("cn_m", {"start_m": start_m, "end_m": end_m},
                          "month,m0,m0_yoy,m1,m1_yoy,m2,m2_yoy")

    def get_shibor(self, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """Shibor 利率（on/1w/1m/3m/6m/1y，单位 %）。"""
        return self._call("shibor", {"start_date": start_date, "end_date": end_date},
                          "date,on,1w,1m,3m,6m,1y")

    # ---------- 基金 ----------

    def get_fund_basic(self, ts_code: str, fields: Optional[str] = None) -> Optional[pd.DataFrame]:
        f = fields or "ts_code,name,management,custodian,fund_type,found_date,list_date,delist_date,m_fee,c_fee,benchmark,invest_type"
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
        """基金分红（含 div_proc 方案进度，供"只统计已实施"过滤）。

        ⚠️ 原 fields 未请求 div_proc，调用方只能按 ex_date 去重，预案行（ex_date 为空）
        会整组塌成 1 行仍被计数，分红次数/金额虚增。
        """
        return self._call("fund_div", {"ts_code": ts_code},
                          "ts_code,ann_date,div_proc,ex_date,record_date,pay_date,div_cash")


    def get_fund_company(self) -> Optional[pd.DataFrame]:
        """公募基金管理人（接口无入参，返回全量，调用方按管理人名称过滤）。"""
        return self._call("fund_company", {},
                          "name,shortname,province,city,chairman,manager,reg_capital,setup_date,employees,main_business,website")
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

    def get_index_dailybasic(self, ts_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """大盘指数每日指标（指数级 PE/PB/市值）。不覆盖部分指数（如科创50），空结果由调用方降级。"""
        return self._call("index_dailybasic", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,trade_date,pe,pb,total_mv")

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

    def get_pledge_stat(self, ts_code: str) -> Optional[pd.DataFrame]:
        """股权质押统计数据（历史序列，取最新期作为当前质押比例）。"""
        return self._call("pledge_stat", {"ts_code": ts_code},
                          "ts_code,end_date,pledge_count,unrest_pledge,rest_pledge,total_share,pledge_ratio")

    def get_pledge_detail(self, ts_code: str) -> Optional[pd.DataFrame]:
        """股权质押明细。"""
        return self._call("pledge_detail", {"ts_code": ts_code},
                          "ts_code,ann_date,holder_name,pledge_amount,start_date,end_date,is_release,pledgor,p_total_ratio,h_total_ratio")

    def get_stk_managers(self, ts_code: str) -> Optional[pd.DataFrame]:
        """上市公司管理层（含 `begin_date` 上任 / `end_date` 离任，可用于区分在任与离任）。

        ⚠️ 原 docstring 断言"接口实际不返回 end_date"，与实测 schema 相反：
        服务端确实返回 begin_date/end_date（默认显示=Y），漏取会使在任判定失效、
        管理层人数把离任与历次披露一并计入而系统性高估。
        """
        return self._call("stk_managers", {"ts_code": ts_code},
                          "ts_code,ann_date,name,gender,lev,title,edu,national,birthday,begin_date,end_date")

    def get_stk_rewards(self, ts_code: str) -> Optional[pd.DataFrame]:
        """管理层薪酬和持股（按报告期）。"""
        return self._call("stk_rewards", {"ts_code": ts_code},
                          "ts_code,ann_date,end_date,name,title,reward,hold_vol")

    def get_namechange(self, ts_code: str) -> Optional[pd.DataFrame]:
        """股票曾用名。"""
        return self._call("namechange", {"ts_code": ts_code},
                          "ts_code,name,start_date,end_date,ann_date,change_reason")

    def get_new_share(self, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """IPO新股列表（按上网发行日期区间取全市场，调用方本地过滤标的）。"""
        return self._call("new_share", {"start_date": start_date, "end_date": end_date},
                          "ts_code,name,ipo_date,issue_date,amount,market_amount,price,pe,limit_amount")

    # ---------- 大宗交易 ----------

    def get_block_trade(self, ts_code: str, end_date: str, lookback_days: int = 60) -> Optional[pd.DataFrame]:
        start_date = shift_date(end_date, -lookback_days)
        return self._call("block_trade", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,trade_date,price,vol,amount,buyer,seller")

    def get_repurchase(self, ts_code: str, end_date: str, lookback_days: int = 365) -> Optional[pd.DataFrame]:
        """股票回购。接口无 ts_code 入参，按公告日期区间取全市场后本地过滤。

        ⚠️ 单位与口径（实测校准）：
          · `amount` 单位是**元**（不是万元）、`vol` 单位是**股**（不是万股）。
          · 同一回购计划的多条公告是**累计快照**（vol/amount 随 ann_date 单调递增，
            增量÷增量股数落在当日 high_limit/low_limit 区间内），**禁止对多条记录求和**。
          · `proc` 为「预案」「股东大会通过」的记录属未实施计划，不计入已回购金额。
        """
        start_date = shift_date(end_date, -lookback_days)
        df = self._call("repurchase", {"start_date": start_date, "end_date": end_date},
                        "ts_code,ann_date,end_date,proc,exp_date,vol,amount,high_limit,low_limit")
        if df is None or df.empty or "ts_code" not in df.columns:
            return None
        return df[df["ts_code"] == ts_code].reset_index(drop=True)

    def get_hsgt_top10(self, ts_code: str, end_date: str, lookback_days: int = 60) -> Optional[pd.DataFrame]:
        """沪深股通十大成交股上榜记录（amount/net_amount 单位：元）。"""
        start_date = shift_date(end_date, -lookback_days)
        return self._call("hsgt_top10", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "trade_date,ts_code,name,close,change,rank,market_type,amount,net_amount,buy,sell")

    def get_broker_recommend(self, month: str) -> Optional[pd.DataFrame]:
        """券商月度金股（month=YYYYMM 为必选入参）；调用方按月循环后本地过滤标的。"""
        return self._call("broker_recommend", {"month": month}, "month,broker,ts_code,name")

    def get_daily_info(self, start_date: str, end_date: str, ts_code: str = "SH_A") -> Optional[pd.DataFrame]:
        """市场交易统计（板块口径，SH_A=上海A股）；amount 亿元、vol 亿股、pe 平均市盈率、tr 换手率%。"""
        return self._call("daily_info", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "trade_date,ts_code,ts_name,com_count,amount,vol,trans_count,pe,tr")

    def get_sz_daily_info(self, start_date: str, end_date: str, ts_code: str = "股票") -> Optional[pd.DataFrame]:
        """深圳市场每日交易情况（板块代码为中文，"股票"=深市股票总和）；amount 单位为元（换算亿元需 /1e8，与 daily_info 的亿元口径不同）。"""
        return self._call("sz_daily_info", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "trade_date,ts_code,count,amount,vol,total_mv")

    # ---------- 全市场两融汇总 ----------

    def get_margin(self, end_date: str, exchange_id: str = "SSE", lookback_days: int = 250) -> Optional[pd.DataFrame]:
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

    def get_express(self, ts_code: str, end_date: str, lookback_days: int = 400) -> Optional[pd.DataFrame]:
        """业绩快报（比定期报告更早披露）。"""
        start_date = shift_date(end_date, -lookback_days)
        return self._call("express", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,ann_date,end_date,revenue,n_income,yoy_sales,yoy_dedu_np,diluted_eps,diluted_roe")

    def get_fina_audit(self, ts_code: str, end_date: str, lookback_days: int = 800) -> Optional[pd.DataFrame]:
        """财务审计意见（年报口径）。"""
        start_date = shift_date(end_date, -lookback_days)
        return self._call("fina_audit", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                          "ts_code,ann_date,end_date,audit_result,audit_agency")

    def get_fina_mainbz(self, ts_code: str, end_date: str, lookback_days: int = 800) -> Optional[pd.DataFrame]:
        """主营业务构成（type=P 按产品）。"""
        start_date = shift_date(end_date, -lookback_days)
        return self._call("fina_mainbz", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date, "type": "P"},
                          "ts_code,end_date,bz_item,bz_sales,bz_profit,curr_type")

    def get_disclosure_date(self, ts_code: str) -> Optional[pd.DataFrame]:
        """财报披露计划（含未来预定披露日 pre_date）。"""
        return self._call("disclosure_date", {"ts_code": ts_code},
                          "ts_code,ann_date,end_date,pre_date,actual_date")

    def get_dividend(self, ts_code: str) -> Optional[pd.DataFrame]:
        """分红送股（cash_div_tax 每股税前分红、stk_div 每股送转；div_proc 含 实施/预案 等阶段）。"""
        return self._call("dividend", {"ts_code": ts_code},
                          "ts_code,end_date,ann_date,div_proc,stk_div,cash_div,cash_div_tax,record_date,ex_date,pay_date,base_share")

    # ---------- 行业分类 ----------

    def get_index_classify(self, level: str = "L1", src: str = "SW2021") -> Optional[pd.DataFrame]:
        return self._call("index_classify", {"level": level, "src": src},
                          "index_code,industry_name,parent_code,level,industry_code,is_pub,src")

    # ---------- 期货 ----------

    def _fut_sdk_call(self, api_name: str, params: Dict[str, Any], fields: str = "") -> Optional[pd.DataFrame]:
        """期货接口SDK调用：不传fields，规避定制SDK字段名校验，返回全字段本地选列。"""
        _acquire_rate_slot()
        params = self._cap_dates(params)
        if self.mode == "sdk" and self._pro is not None:
            api = getattr(self._pro, api_name, None)
            if api is not None:
                return safe_call(api, **params)
        return self._http_call(api_name, params, fields)

    def get_fut_basic(self, exchange: Optional[str] = None, fut_type: Optional[str] = None,
                      ts_code: Optional[str] = None) -> Optional[pd.DataFrame]:
        """期货合约信息。

        ⚠️ `exchange` 文档标注为**必选**：实测不传 exchange 时服务端返回 None（不是空表），
        因此"只传 fut_type=2"取连续合约列表会失败；原 docstring 声称可全量取，与实测不符。
        调用方请带上 exchange，或对 None 结果做兜底。
        """
        params = {k: v for k, v in [("exchange", exchange), ("fut_type", fut_type), ("ts_code", ts_code)]
                  if v is not None}
        return self._fut_sdk_call("fut_basic", params)

    def get_fut_daily(self, ts_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """期货日线行情。

        ⚠️ 显式请求含 `delv_settle`（交割结算价，SKILL.md 期货结算维度要求输出）。
        字段清单 = 实测默认返回列 + delv_settle，逐项已验证存在——服务端对不存在的
        字段会整体拒绝（code=40101），不能凭空加列。
        """
        df = self._fut_sdk_call(
            "fut_daily",
            {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
            "ts_code,trade_date,pre_close,pre_settle,open,high,low,close,settle,"
            "change1,change2,vol,amount,oi,oi_chg,delv_settle",
        )
        return drop_t0_placeholder(df, ["close"])

    def get_fut_mapping(self, ts_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        return self._fut_sdk_call("fut_mapping", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date})

    def get_fut_holding(self, symbol: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """每日成交持仓排名（前 20 会员多空持仓）。

        ⚠️ 入参是 `symbol`（产品代码如 JM/RB），**不是 `ts_code`**：文档入参表里没有 ts_code，
        实测传 ts_code（如 JM.DCE）服务端返回 None，导致持仓排名恒为空，
        而报告却把代码错误解释成"该交易所无持仓数据"。
        为兼容原有调用，这里接受带交易所后缀的代码并自动取 symbol 部分。
        """
        sym = symbol.split(".")[0] if symbol else symbol
        return self._fut_sdk_call("fut_holding", {"symbol": sym, "start_date": start_date, "end_date": end_date})

    def get_fut_wsr(self, trade_date: str) -> Optional[pd.DataFrame]:
        return self._fut_sdk_call("fut_wsr", {"trade_date": trade_date})

    def get_fut_settle(self, ts_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        return self._fut_sdk_call("fut_settle", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date})
