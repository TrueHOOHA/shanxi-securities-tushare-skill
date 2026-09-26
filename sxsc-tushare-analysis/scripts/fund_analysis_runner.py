#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基金综合分析 Runner。

支持：场外基金（.OF）用 fund_nav，场内 ETF（.SH/.SZ）用 fund_daily。

维度：
1. 概况
2. 净值走势
3. 业绩指标
4. 基金经理
5. 持仓分析
6. 规模变化
7. 分红
8. 风险提示
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from adjustment import apply_adj_factor, apply_fund_adj, apply_etf_adj
from factor_signals import calc_amplitude_momentum, calc_quantitative_momentum
from basic_metrics import calc_max_drawdown, calc_returns, calc_sharpe, calc_volatility
from data_api import DataAPI, shift_date
from result_model import DimensionResult, ResultStatus, safe_result
from report_html import df_to_md_table, render_html_report
from composite import calc_composite_score, calc_factor_positioning, calc_risk_budget


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return None
        return float(value)
    except Exception:
        return None


def _is_etf(ts_code: str) -> bool:
    return ts_code.endswith((".SH", ".SZ"))


class FundAnalysisRunner:
    """基金综合分析 Runner。"""

    PERIODS = (5, 20, 60, 120, 250)
    DEFAULT_DIMENSIONS = ["overview", "nav", "performance", "peer", "manager", "portfolio", "share", "div", "momentum_quality", "risk"]

    def __init__(
        self,
        ts_code: str,
        end_date: Optional[str] = None,
        dimensions: Optional[List[str]] = None,
        api: Optional[DataAPI] = None,
    ):
        self.ts_code = ts_code
        self.end_date = end_date or _today()
        self.dimensions = dimensions or self.DEFAULT_DIMENSIONS.copy()
        self.api = api or DataAPI()

        self.fund_name: Optional[str] = None
        self.fund_type: Optional[str] = None
        self.found_date: Optional[str] = None
        self.results: Dict[str, DimensionResult] = {}

    # ---------- 维度：概况 ----------

    @safe_result("概况")
    def analyze_overview(self) -> DimensionResult:
        df = self.api.get_fund_basic(self.ts_code)
        if df is None or df.empty:
            return DimensionResult.empty("概况", note="无法获取基金基础信息")

        row = df.iloc[0]
        self.fund_name = row.get("name")
        self.fund_type = row.get("fund_type")
        self.found_date = row.get("found_date")
        self.benchmark = row.get("benchmark")

        data = {
            "ts_code": self.ts_code,
            "name": self.fund_name,
            "fund_type": self.fund_type,
            "found_date": row.get("found_date"),
            "list_date": row.get("list_date"),
            "delist_date": row.get("delist_date"),
        }
        # 管理人信息（fund_company 接口无入参返回全量，按管理人名称本地过滤）
        mgmt_name = row.get("management")
        if mgmt_name:
            data["management"] = mgmt_name
            try:
                fc_df = self.api.get_fund_company()
                if fc_df is not None and not fc_df.empty and "name" in fc_df.columns:
                    fc_hit = fc_df[fc_df["name"] == mgmt_name]
                    if not fc_hit.empty:
                        fc = fc_hit.iloc[0]
                        data["manager_company"] = {
                            "shortname": fc.get("shortname"),
                            "setup_date": fc.get("setup_date"),
                            "employees": fc.get("employees"),
                            "reg_capital": fc.get("reg_capital"),
                            "chairman": fc.get("chairman"),
                        }
            except Exception:
                pass

        return DimensionResult.success("概况", data=data)

    # ---------- 维度：净值走势 ----------

    @safe_result("净值走势")
    def analyze_nav(self) -> DimensionResult:
        start = shift_date(self.end_date, -max(self.PERIODS) * 2)

        if _is_etf(self.ts_code):
            # 场内 ETF
            df_daily = self.api.get_fund_daily(self.ts_code, start, self.end_date)
            df_adj = self.api.get_fund_adj(self.ts_code, start, self.end_date)
            if df_daily is None or df_daily.empty:
                return DimensionResult.empty("净值走势", note="无法获取 ETF 行情")

            df = apply_etf_adj(df_daily, df_adj) if df_adj is not None else df_daily.sort_values("trade_date").set_index("trade_date")
            nav_series = df["close_post"] if "close_post" in df.columns else df["close"]
            nav_series = nav_series.sort_index()
            latest_nav = _safe_float(df_daily.sort_values("trade_date").iloc[-1]["close"])
        else:
            # 场外基金
            df_nav = self.api.get_fund_nav(self.ts_code, start, self.end_date)
            df_adj = self.api.get_fund_adj(self.ts_code, start, self.end_date)
            if df_nav is None or df_nav.empty:
                return DimensionResult.empty("净值走势", note="无法获取基金净值")

            df = apply_fund_adj(df_nav, df_adj) if df_adj is not None else df_nav.sort_values("nav_date").set_index("nav_date")
            nav_series = df["adj_nav"] if "adj_nav" in df.columns else df["unit_nav"]
            nav_series = nav_series.sort_index()
            latest_nav = _safe_float(df_nav.sort_values("nav_date").iloc[-1]["unit_nav"])

        if len(nav_series) < 2:
            return DimensionResult.insufficient_history("净值走势", note="净值历史数据不足")

        # ETF 专项：折溢价率（收盘价 vs 单位净值）与跟踪误差（对基准指数）
        etf_check = None
        if _is_etf(self.ts_code):
            try:
                _nav_df = self.api.get_fund_nav(self.ts_code, start, self.end_date)
                if _nav_df is not None and not _nav_df.empty:
                    _nd = _nav_df.sort_values("nav_date").drop_duplicates("nav_date").set_index("nav_date")["unit_nav"]
                    _px = df_daily.sort_values("trade_date").drop_duplicates("trade_date").set_index("trade_date")["close"]
                    _j = pd.DataFrame({"px": _px, "nav": _nd}).dropna()  # 两序列按日期索引自动对齐
                    if len(_j) >= 20:
                        etf_check = {"premium_discount_pct": round(float((_j["px"] / _j["nav"] - 1).iloc[-1] * 100), 2)}
                # 跟踪误差：ETF 日收益(复权) vs 基准指数日收益
                _bmap = {"沪深300": "000300.SH", "中证500": "000905.SH", "上证50": "000016.SH",
                         "创业板": "399006.SZ", "科创50": "000688.SH", "中证1000": "000852.SH", "中证800": "000906.SH"}
                _bench_txt = str(getattr(self, "benchmark", "") or "")
                _bcode = next((_v for _k, _v in _bmap.items() if _k in _bench_txt), None)
                if _bcode:
                    _bdf = self.api.get_index_daily(_bcode, start, self.end_date)
                    if _bdf is not None and not _bdf.empty:
                        _bser = _bdf.set_index("trade_date")["close"].sort_index().pct_change().dropna()
                        _eser = nav_series.pct_change().dropna()
                        _diff = pd.DataFrame({"etf": _eser, "idx": _bser}).dropna()
                        _diff.index = _diff.index.astype(str)
                        _dup = _diff.index.duplicated()
                        _diff = _diff[~_dup]
                        if len(_diff) >= 60:
                            etf_check = etf_check or {}
                            etf_check["benchmark_code"] = _bcode
                            etf_check["tracking_error_ann_pct"] = round(float(_diff["etf"].sub(_diff["idx"]).std() * (252 ** 0.5) * 100), 2)
            except Exception:
                etf_check = etf_check if isinstance(etf_check, dict) else None

        returns = calc_returns(nav_series, periods=self.PERIODS)
        conclusion = f"最新净值 {latest_nav}，近20日 {returns.get('近20日涨幅%', 'N/A')}%"
        if etf_check and etf_check.get("premium_discount_pct") is not None:
            conclusion += f"，最新折溢价 {etf_check['premium_discount_pct']:+.2f}%（收盘价 vs 单位净值）"
        if etf_check and etf_check.get("tracking_error_ann_pct") is not None:
            conclusion += f"，年化跟踪误差 {etf_check['tracking_error_ann_pct']:.2f}%（基准 {etf_check.get('benchmark_code')}）"
        return DimensionResult.success("净值走势", conclusion=conclusion, data={
            "latest_nav": latest_nav,
            "returns": returns,
            "etf_check": etf_check,
            "chart": {
                "title": "净值走势（复权）",
                "type": "line",
                "dates": [str(d) for d in nav_series.index.tolist()],
                "series": [{"name": "复权净值", "data": [round(float(v), 4) for v in nav_series.tolist()]}],
            },
        })

    # ---------- 维度：业绩指标 ----------

    @safe_result("业绩指标")
    def analyze_performance(self) -> DimensionResult:
        start = shift_date(self.end_date, -max(self.PERIODS) * 2)

        if _is_etf(self.ts_code):
            df = self.api.get_fund_daily(self.ts_code, start, self.end_date)
            adj_df = self.api.get_fund_adj(self.ts_code, start, self.end_date)
            series_key = "close_post" if adj_df is not None else "close"
            nav_series = (apply_etf_adj(df, adj_df)[series_key] if adj_df is not None else df.set_index("trade_date")["close"]).sort_index()
        else:
            df = self.api.get_fund_nav(self.ts_code, start, self.end_date)
            adj_df = self.api.get_fund_adj(self.ts_code, start, self.end_date)
            series_key = "adj_nav" if adj_df is not None else "unit_nav"
            nav_series = (apply_fund_adj(df, adj_df)[series_key] if adj_df is not None else df.set_index("nav_date")["unit_nav"]).sort_index()

        if nav_series is None or len(nav_series) < 2:
            return DimensionResult.empty("业绩指标", note="净值数据不足")

        data = {
            "volatility": calc_volatility(nav_series),
            "max_drawdown": calc_max_drawdown(nav_series),
            "sharpe": calc_sharpe(nav_series),
        }
        conclusion = (
            f"年化波动 {data['volatility']}%，最大回撤 {data['max_drawdown']}%，夏普 {data['sharpe']}"
        )
        return DimensionResult.success("业绩指标", conclusion=conclusion, data=data)

    # ---------- 维度：基金经理 ----------

    @safe_result("基金经理")
    def analyze_manager(self) -> DimensionResult:
        df = self.api.get_fund_manager(self.ts_code)
        if df is None or df.empty:
            return DimensionResult.empty("基金经理", note="无法获取基金经理信息")

        df = df.sort_values("begin_date", ascending=False).reset_index(drop=True)
        latest = df.iloc[0]
        data = {
            "name": latest.get("name"),
            "begin_date": latest.get("begin_date"),
            "tenure_days": None,
        }
        if latest.get("begin_date"):
            try:
                begin = datetime.strptime(str(latest["begin_date"]), "%Y%m%d")
                data["tenure_days"] = (datetime.now() - begin).days
            except Exception:
                pass

        conclusion = f"现任基金经理：{data['name']}，任职起始 {data['begin_date']}"
        if data["tenure_days"]:
            conclusion += f"（约 {data['tenure_days'] // 365} 年）"
        return DimensionResult.success("基金经理", conclusion=conclusion, data=data)

    # ---------- 维度：持仓分析 ----------

    @safe_result("持仓分析")
    def analyze_portfolio(self) -> DimensionResult:
        df = self.api.get_fund_portfolio(self.ts_code, self.end_date)
        if df is None or df.empty:
            return DimensionResult.empty("持仓分析", note="无法获取基金持仓")

        df = df.sort_values("ann_date").reset_index(drop=True)
        latest = df.iloc[-1]
        latest_period = latest.get("end_date")

        # 最新一期持仓，按占股票市值比排序
        latest_holdings = df[df["end_date"] == latest_period].sort_values("stk_mkv_ratio", ascending=False).head(10)

        # 补充股票名称
        symbols = latest_holdings["symbol"].tolist()
        name_map = {}
        try:
            all_codes = ", ".join(symbols)
            sb = self.api._call("stock_basic", {"ts_code": all_codes}, "ts_code,name")
            if sb is not None and not sb.empty:
                name_map = dict(zip(sb["ts_code"], sb["name"]))
        except Exception:
            pass

        holdings = []
        for _, r in latest_holdings.iterrows():
            holdings.append({
                "symbol": r.get("symbol"),
                "name": name_map.get(r.get("symbol"), ""),
                "ratio": _safe_float(r.get("stk_mkv_ratio")),
                "market_val": _safe_float(r.get("mkv")),
            })

        data = {
            "period": latest_period,
            "holdings": holdings,
            "top10_total_ratio": round(sum(h["ratio"] for h in holdings if h["ratio"]), 2) if holdings else None,
        }
        total = data.get("top10_total_ratio", "N/A")
        conclusion = f"最新报告期 {latest_period}，前十大重仓占比合计约 {total}%"
        return DimensionResult.success("持仓分析", conclusion=conclusion, data=data)

    # ---------- 维度：规模变化 ----------

    @safe_result("规模变化")
    def analyze_share(self) -> DimensionResult:
        df = self.api.get_fund_share(self.ts_code, self.end_date)
        if df is None or df.empty:
            return DimensionResult.empty("规模变化", note="无法获取份额数据")

        df = df.sort_values("trade_date").reset_index(drop=True)
        latest = df.iloc[-1]
        latest_share = _safe_float(latest.get("fd_share"))


        # 最新规模 = 份额(万份) × 单位净值 / 1e4（亿元）
        latest_scale_yi = None
        try:
            _nav_df = self.api.get_fund_nav(self.ts_code, shift_date(self.end_date, -30), self.end_date)
            if _nav_df is not None and not _nav_df.empty:
                _unit_nav = _safe_float(_nav_df.sort_values("nav_date").iloc[-1].get("unit_nav"))
                if _unit_nav and latest_share:
                    latest_scale_yi = round(latest_share * _unit_nav / 1e4, 2)
        except Exception:
            pass

        # 份额拆分归一化：拆分日 fd_share 与 fund_adj 因子同步跳变。
        # 检测后把历史份额统一到最新拆分口径，避免"最新 vs 季度变化"不可比。
        split_note = ""
        adj = self.api.get_fund_adj(self.ts_code, df["trade_date"].iloc[0], df["trade_date"].iloc[-1])
        if adj is not None and not adj.empty:
            adj = adj.sort_values("trade_date").drop_duplicates("trade_date").set_index("trade_date")["adj_factor"]
            adj = adj.reindex(df["trade_date"]).ffill().fillna(adj.iloc[0])
            df["adj_factor"] = adj.values
            split_dates = []
            for i in range(1, len(df)):
                af_chg = df["adj_factor"].iloc[i] / df["adj_factor"].iloc[i - 1]
                sh_chg = df["fd_share"].iloc[i] / df["fd_share"].iloc[i - 1]
                if af_chg > 1.3 and sh_chg > 1.3:
                    split_dates.append(df["trade_date"].iloc[i])
            if split_dates:
                latest_adj = df["adj_factor"].iloc[-1]
                df["fd_share"] = df["fd_share"] * latest_adj / df["adj_factor"]
                ratios = []
                for d in split_dates:
                    idx = df[df["trade_date"] == d].index[0]
                    ratio = df["adj_factor"].iloc[idx] / df["adj_factor"].iloc[idx - 1]
                    ratios.append(f"{d}({ratio:.1f}x)")
                split_note = f"（期间发生份额拆分{'、'.join(ratios)}，历史份额已统一为最新拆分口径）"

        # 按季度采样：仅取季度末月(3/6/9/12)，每季末月取最后交易日，取最近4季
        df["ym"] = df["trade_date"].str[:6]
        df["mm"] = df["trade_date"].str[4:6]
        qe = df[df["mm"].isin(["03", "06", "09", "12"])]
        quarterly = qe.groupby("ym").tail(1).sort_values("trade_date").tail(4)
        share_changes = []
        for _, row in quarterly.iterrows():
            share_changes.append({
                "quarter": row.get("ym"),
                "fd_share": _safe_float(row.get("fd_share")),
            })

        data = {
            "latest_share": latest_share,
            "quarterly_changes": share_changes,
            "split_note": split_note,
            "latest_scale_yi": latest_scale_yi,
        }
        conclusion = f"最新份额 {latest_share:,.2f} 万份"
        if len(share_changes) >= 2:
            first = share_changes[0]["fd_share"]
            last = share_changes[-1]["fd_share"]
            if first and last:
                chg = round((last / first - 1) * 100, 2)
                conclusion += f"，近4季变化 {chg:+.2f}%{split_note}"
        # 份额走势图（亿份，按最新拆分口径）
        data["chart"] = {
            "title": "基金份额走势（亿份）",
            "type": "line",
            "dates": df["trade_date"].tolist(),
            "series": [{"name": "份额", "data": [round(v / 1e4, 2) if v == v else None for v in df["fd_share"].tolist()]}],
        }
        return DimensionResult.success("规模变化", conclusion=conclusion, data=data)

    # ---------- 维度：分红 ----------

    @safe_result("分红")
    def analyze_dividend(self) -> DimensionResult:
        df = self.api.get_fund_div(self.ts_code)
        if df is None or df.empty:
            return DimensionResult.empty("分红", note="该基金无分红记录（fund_div接口未覆盖或基金确实未进行现金分红，部分ETF通过净值增长体现收益）")

        df = df.sort_values("ann_date").reset_index(drop=True)
        # fund_div 可能返回重复行（同一除息日多行），按 ex_date 去重避免虚增次数与累计金额
        if "ex_date" in df.columns:
            df = df.drop_duplicates("ex_date", keep="last").reset_index(drop=True)
        total_div = _safe_float(df["div_cash"].sum())
        data = {
            "count": len(df),
            "total_div_cash": total_div,
            "latest": df.iloc[-1].to_dict(),
        }
        latest_div = df.iloc[-1]
        conclusion = f"历史分红 {len(df)} 次，累计 {total_div:.4f} 元/份，最近一次除息日 {latest_div.get('ex_date', 'N/A')}"
        # 标注成立前分红（转型基金：fund_basic 的 found_date 可能为转型日，fund_div 保留原基金历史分红）
        if self.found_date and "ex_date" in df.columns:
            pre_count = int((df["ex_date"].astype(str) < self.found_date).sum())
            if pre_count > 0:
                conclusion += f"（其中 {pre_count} 次在成立日 {self.found_date} 前，可能为转型前原基金记录）"
        return DimensionResult.success("分红", conclusion=conclusion, data=data)


    # ---------- 维度：同类对比（默认） ----------

    def _extract_peer_keywords(self, name: Optional[str]) -> list:
        """从基金名称提取行业/主题关键词（先剔除管理人名，再取剩余中文片段与指数代码），缩窄同类对比口径。"""
        import re
        if not name:
            return []
        manager_stop = {"国泰","华宝","易方达","华夏","南方","嘉实","富国","广发","招商","博时","汇添富","景顺","东财","浦银","华泰柏瑞","银华","华安","大成","鹏华","工银","交银","建信","兴全","中欧","万家","国联安","长信","银河","中银","上投","摩根","泰康","平安","前海","开源","国寿","人保","西藏","诺安","信达","华商","中邮","安信","长城","中海","中加"}
        generic_stop = {"基金","指数","策略","增强","红利","低波","联接","股票型","债券型","混合型","货币","商品","REITs","LOF","ETF","型"}
        rest = str(name)
        for m in manager_stop:  # 剔除管理人名子串，避免"华泰柏瑞沪深"整体当关键词
            rest = rest.replace(m, "")
        cn = re.findall(r"[\u4e00-\u9fa5]+", rest)
        nums = re.findall(r"\d+", rest)
        kws = [s for s in cn if s not in generic_stop and len(s) >= 2]
        kws += [n for n in nums if len(n) >= 3]  # 指数代码（如 300/500/50）
        return kws
    @safe_result("同类对比")
    def analyze_peer(self) -> DimensionResult:
        if not self.fund_type:
            return DimensionResult.empty("同类对比", note="无法获取基金类型")

        # ETF 优先选同后缀场内基金，再用本基金名称关键词缩窄到同主题，避免混入其他行业ETF
        fetch_fields = "ts_code,name,fund_type,found_date"
        peer_scope = self.fund_type
        if _is_etf(self.ts_code):
            suffix = self.ts_code[-3:]  # .SH or .SZ
            peers_df = self.api._call("fund_basic", {"fund_type": self.fund_type}, fetch_fields)
            if peers_df is not None and not peers_df.empty:
                peers_df = peers_df[peers_df["ts_code"].str.endswith(suffix)].copy()
            keywords = self._extract_peer_keywords(self.fund_name)
            if keywords and peers_df is not None and not peers_df.empty and "name" in peers_df.columns:
                # 中文词与数字词分开判断：必须同时命中（如"沪深"+"300"），避免"沪深港黄金"这类含"沪深"但不含"300"的误入
                _zh = [k for k in keywords if not k[0].isdigit()]
                _num = [k for k in keywords if k[0].isdigit()]

                def _match(n):
                    if _zh and not any(z in n for z in _zh):
                        return False
                    if _num and not any(k in n for k in _num):
                        return False
                    return True

                narrow = peers_df[peers_df["name"].apply(lambda n: _match(str(n)))]
                if len(narrow) >= 3:
                    peers_df = narrow
                    peer_scope = f"同主题({'/'.join(keywords)})"
        else:
            peers_df = self.api._call("fund_basic", {"fund_type": self.fund_type}, fetch_fields)
        if peers_df is None or peers_df.empty:
            return DimensionResult.empty("同类对比", note="无法获取同类基金列表")

        # 按成立日期排序，优先选上市早、历史长的同类基金
        if "found_date" in peers_df.columns:
            peers_df = peers_df.sort_values("found_date")
        peer_codes = peers_df[peers_df["ts_code"] != self.ts_code].head(50)["ts_code"].tolist()
        if not peer_codes:
            return DimensionResult.empty("同类对比", note="未找到同类基金")

        start = shift_date(self.end_date, -max(self.PERIODS) * 2)

        def _calc_return(code: str) -> Optional[Dict[str, Any]]:
            import time
            for _attempt in range(2):
                try:
                    if _is_etf(code):
                        df = self.api.get_fund_daily(code, start, self.end_date)
                        adj = self.api.get_fund_adj(code, start, self.end_date)
                        if df is None or df.empty:
                            return None
                        df = df.sort_values("trade_date")
                        if adj is not None and not adj.empty:
                            df = apply_etf_adj(df, adj)
                            series = df["close_post"].sort_index() if "close_post" in df.columns else df.set_index("trade_date")["close"].sort_index()
                        else:
                            series = df.set_index("trade_date")["close"].sort_index()
                    else:
                        df = self.api.get_fund_nav(code, start, self.end_date)
                        adj = self.api.get_fund_adj(code, start, self.end_date)
                        if df is None or df.empty:
                            return None
                        df = df.sort_values("nav_date")
                        if adj is not None and not adj.empty:
                            df = apply_fund_adj(df, adj)
                            series = df["adj_nav"].sort_index() if "adj_nav" in df.columns else df.set_index("nav_date")["unit_nav"].sort_index()
                        else:
                            series = df.set_index("nav_date")["unit_nav"].sort_index()
                    if series is None or len(series) < 2:
                        return None
                    returns = calc_returns(series, periods=self.PERIODS)
                    # 归一化净值序列（基准日=100，供同类对比图）
                    _base = float(series.iloc[0])
                    _norm = [round(float(v) / _base * 100, 2) if v == v else None for v in series.tolist()]
                    return {
                        "ts_code": code,
                        "近20日%": returns.get("近20日涨幅%", "N/A"),
                        "近60日%": returns.get("近60日涨幅%", "N/A"),
                        "近120日%": returns.get("近120日涨幅%", "N/A"),
                        "近250日%": returns.get("近250日涨幅%", "N/A"),
                        "_series": {"dates": [str(d) for d in series.index.tolist()], "norm": _norm},
                    }
                except Exception:
                    pass
                time.sleep(0.3)
            return None

        rows = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            rows = [r for r in executor.map(_calc_return, peer_codes) if r is not None]

        if not rows:
            return DimensionResult.empty("同类对比", note="无法计算同类基金收益")

        # 优先用 nav 维度已算好的 returns，避免重复调 API 被限流
        own_row = {"ts_code": self.ts_code}
        nav_res = self.results.get("nav")
        if nav_res and nav_res.is_ok() and nav_res.data:
            nav_returns = nav_res.data.get("returns", {})
            own_row = {
                "ts_code": self.ts_code,
                "近20日%": nav_returns.get("近20日涨幅%", "N/A"),
                "近60日%": nav_returns.get("近60日涨幅%", "N/A"),
                "近120日%": nav_returns.get("近120日涨幅%", "N/A"),
                "近250日%": nav_returns.get("近250日涨幅%", "N/A"),
            }
        else:
            own_row = _calc_return(self.ts_code) or {"ts_code": self.ts_code}
        peer_df = pd.DataFrame(rows)

        for col in ["近20日%", "近60日%", "近120日%", "近250日%"]:
            own_val = own_row.get(col)
            if own_val == "N/A" or own_val is None:
                continue
            try:
                own_val_f = float(own_val)
            except Exception:
                continue
            valid = peer_df[peer_df[col] != "N/A"][col].astype(float)
            if len(valid) > 0:
                own_row[f"{col}排名%"] = round((valid < own_val_f).sum() / len(valid) * 100, 1)

        data = {"own": own_row, "peers": peer_df.head(10).to_dict("records"), "peer_total": len(peer_df), "scope": peer_scope}

        # 同类对比图：本基金 vs 同类中位数（归一化净值，基准日=100，按本基金日期对齐）
        _own_dates, _own_norm = [], []
        nav_res = self.results.get("nav")
        if nav_res and nav_res.is_ok() and nav_res.data:
            _c = nav_res.data.get("chart")
            if _c and _c.get("series") and _c.get("dates"):
                _vals = _c["series"][0].get("data") or []
                _bd = _vals[0] if _vals else None
                if _bd:
                    _own_dates = _c["dates"]
                    _own_norm = [round(v / _bd * 100, 2) if v is not None and v == v else None for v in _vals]
        _med = []
        if _own_dates and rows:
            _pn = []
            for _r in rows:
                _s = _r.get("_series") or {}
                if _s.get("dates") and _s.get("norm"):
                    _m = dict(zip(_s["dates"], _s["norm"]))
                    _pn.append([_m.get(d) for d in _own_dates])
            if _pn:
                for i in range(len(_own_dates)):
                    _vs = [p[i] for p in _pn if p[i] is not None]
                    _med.append(round(sorted(_vs)[len(_vs) // 2], 2) if _vs else None)
        if _own_dates and _own_norm and any(v is not None for v in _med):
            data["chart"] = {
                "title": "净值归一化对比（基准日=100）",
                "type": "line",
                "dates": _own_dates,
                "series": [
                    {"name": self.fund_name or self.ts_code, "data": _own_norm},
                    {"name": "同类中位数", "data": _med},
                ],
            }
        rank20 = own_row.get("近20日%排名%", "N/A")
        conclusion = f"同类基金({peer_scope})共 {len(peer_df)} 只，本基金近20日收益排名约 {rank20}% 分位"
        return DimensionResult.success("同类对比", conclusion=conclusion, data=data)

    # ---------- 维度：动量质量（因子化，自包含取数） ----------

    @safe_result("动量质量")
    def analyze_momentum_quality(self) -> DimensionResult:
        start = shift_date(self.end_date, -max(self.PERIODS) * 2)
        qm = am = None
        if _is_etf(self.ts_code):
            # ETF：fund_daily OHLC 全列复权（apply_adj_factor 适配 fund_daily，拆分日不跳变）
            df_daily = self.api.get_fund_daily(self.ts_code, start, self.end_date)
            if df_daily is None or df_daily.empty:
                return DimensionResult.empty("动量质量", note="无法获取 ETF 行情")
            df_adj = self.api.get_fund_adj(self.ts_code, start, self.end_date)
            if df_adj is not None and not df_adj.empty:
                df = apply_adj_factor(df_daily, df_adj)
                price = df["close_post"].sort_index()
                high = df["high_post"].sort_index()
                low = df["low_post"].sort_index()
                _src = "ETF 复权行情"
            else:
                df = df_daily.sort_values("trade_date").set_index("trade_date")
                price = df["close"].sort_index()
                high = df["high"].sort_index()
                low = df["low"].sort_index()
                _src = "ETF 未复权行情（缺复权因子，拆分日可能失真）"
            if len(price) < 121:
                return DimensionResult.insufficient_history("动量质量", note="需至少121日数据")
            qm = calc_quantitative_momentum(price, window=60)
            am = calc_amplitude_momentum(price, high, low, n=120, lam=0.3)
        else:
            # 场外：复权净值序列（adj_nav 优先，缺失退回 unit_nav）；无 OHLC，振幅切割不适用
            df_nav = self.api.get_fund_nav(self.ts_code, start, self.end_date)
            if df_nav is None or df_nav.empty:
                return DimensionResult.empty("动量质量", note="无法获取基金净值")
            _nd = df_nav.sort_values("nav_date").drop_duplicates("nav_date")
            _use_adj = "adj_nav" in _nd.columns and _nd["adj_nav"].notna().any()
            series = (_nd.set_index("nav_date")["adj_nav"] if _use_adj else _nd.set_index("nav_date")["unit_nav"]).sort_index()
            _src = "复权净值(adj_nav)" if _use_adj else "单位净值(unit_nav)"
            if len(series) < 121:
                return DimensionResult.insufficient_history("动量质量", note="需至少121日数据")
            qm = calc_quantitative_momentum(series, window=60)

        data = {"quantitative_momentum": qm, "amplitude_momentum": am, "source": _src}
        conclusion = f"动量质量因子分析（数据源：{_src}）。"
        insights = []
        if qm:
            insights.append(f"高质量动量得分 {qm['momentum']}（raw_return {qm['raw_return']}, sigma {qm['sigma']}；数值越大=风险调整后动量越强）")
        if am:
            insights.append(f"振幅切割动量 A={am['a_factor']}（低振幅日ret加总，>0=动量正向）、B={am['b_factor']}（高振幅日，反转效应）")
        else:
            insights.append("场外基金无 OHLC，振幅切割动量不适用")
        if qm and qm.get("momentum") is not None and qm["momentum"] < 0:
            insights.append("高质量动量得分为负，动量或由单日大波动驱动，持续性存疑")
        return DimensionResult.success("动量质量", conclusion=conclusion, data=data, insights=insights)


    # ---------- 维度：风险提示 ----------

    def analyze_risk(self) -> DimensionResult:
        risks = []
        notes = []

        performance = self.results.get("performance")
        if performance and performance.is_ok() and performance.data:
            mdd = performance.data.get("max_drawdown")
            if isinstance(mdd, (int, float)) and mdd < -25:
                risks.append(f"最大回撤 {mdd}% 较深")
            vol = performance.data.get("volatility")
            if isinstance(vol, (int, float)) and vol > 30:
                risks.append(f"年化波动率 {vol}% 偏高")

        portfolio = self.results.get("portfolio")
        if portfolio and portfolio.is_ok() and portfolio.data:
            holdings = portfolio.data.get("holdings", [])
            if holdings:
                top_ratio = holdings[0].get("ratio", 0)
                if isinstance(top_ratio, (int, float)) and top_ratio > 10:
                    risks.append(f"第一大重仓占比 {top_ratio:.2f}%，集中度较高")

        share = self.results.get("share")
        if share and share.is_ok() and share.data:
            changes = share.data.get("quarterly_changes", [])
            if len(changes) >= 2:
                first = changes[0].get("fd_share")
                last = changes[-1].get("fd_share")
                if first and last and first > 0:
                    chg = (last / first - 1) * 100
                    if chg < -20:
                        risks.append(f"近4季份额缩水 {abs(chg):.2f}%，需关注赎回压力")

        if not risks:
            risks.append("未发现显著风险信号（基于已有维度）")

        for dim, res in self.results.items():
            if dim == "risk":
                continue
            if res.status != ResultStatus.SUCCESS:
                notes.append(f"{dim}：{res.note or '数据缺失'}")

        return DimensionResult.success("风险提示", data={"risks": risks, "notes": notes}, risks=risks)

    # ---------- 执行入口 ----------

    def run(self) -> Dict[str, Any]:
        self.results = {}

        if "overview" in self.dimensions:
            self.results["overview"] = self.analyze_overview()

        parallel_dims = [d for d in self.dimensions if d not in ("overview", "risk")]
        dim_methods = {
            "nav": self.analyze_nav,
            "performance": self.analyze_performance,
            "peer": self.analyze_peer,
            "manager": self.analyze_manager,
            "portfolio": self.analyze_portfolio,
            "share": self.analyze_share,
            "div": self.analyze_dividend,
            "momentum_quality": self.analyze_momentum_quality,
        }
        to_run = {d: dim_methods[d] for d in parallel_dims if d in dim_methods}
        if to_run:
            max_workers = min(5, len(to_run))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(method): d for d, method in to_run.items()}
                for future in as_completed(futures):
                    d = futures[future]
                    self.results[d] = future.result()

        if "risk" in self.dimensions:
            self.results["risk"] = self.analyze_risk()

        return {
            "ts_code": self.ts_code,
            "name": self.fund_name,
            "fund_type": self.fund_type,
            "end_date": self.end_date,
            "dimensions": {k: v.to_dict() for k, v in self.results.items()},
        }

    # ---------- 报告渲染 ----------

    def _fmt(self, x, digits=2):
        """数值格式化。"""
        if x is None:
            return "N/A"
        try:
            if isinstance(x, float) and (x != x or x == float("inf") or x == float("-inf")):
                return "N/A"
        except Exception:
            return "N/A"
        if isinstance(x, (int, np.integer)):
            return f"{int(x):,}"
        if isinstance(x, (float, np.floating)):
            return f"{x:,.{digits}f}"
        return str(x)

    def _f(self, x):
        """安全转 float，失败返回 None。"""
        if x is None or x == "N/A":
            return None
        try:
            v = float(x)
            return None if (v != v or v == float("inf") or v == float("-inf")) else v
        except Exception:
            return None

    def _v(self, d, *keys, default="N/A"):
        """安全取嵌套值。"""
        cur = d
        for k in keys:
            if cur is None:
                return default
            cur = cur.get(k) if isinstance(cur, dict) else None
        return cur if cur is not None else default

    def _md(self, df):
        """输出 markdown 表格。"""
        if df is None or df.empty:
            return ""
        df = df.copy()
        df = df.dropna(axis=1, how="all")
        for col in df.columns:
            if df[col].dtype.kind in "iufc":
                df[col] = df[col].apply(lambda x: self._fmt(x))
            else:  # 兜底：pandas 3.x 新 str dtype 不属于 object，也需清洗 NaN
                df[col] = df[col].apply(
                    lambda x: self._fmt(x) if isinstance(x, (int, float, np.integer, np.floating))
                    else ("N/A" if x is None or (isinstance(x, float) and x != x) else str(x))
                )
        return df_to_md_table(df)

    DIM_TITLES = {
        "overview": "概况", "nav": "净值走势", "performance": "业绩指标",
        "peer": "同类对比", "manager": "基金经理", "portfolio": "持仓分析",
        "share": "规模变化", "div": "分红", "momentum_quality": "动量质量", "risk": "风险提示",
    }

    # 各维度含义描述（面向非专业读者，标题后以引用块呈现）
    DIM_DESCRIPTIONS = {
        "overview": "基金基础档案：代码、类型、成立与上市日期，回答“这是什么基金”。",
        "nav": "净值走势：单位净值/复权净值的历史涨跌，反映基金长期收益趋势。",
        "performance": "业绩指标：年化波动率、最大回撤、夏普比率，衡量风险调整后的收益质量。",
        "peer": "同类对比：与同类型/同主题基金的收益排名，判断在同类中处于什么水平。",
        "manager": "基金经理：现任管理人的任职年限，判断管理经验与稳定性。",
        "portfolio": "持仓分析：前十大重仓股及占比，判断集中度与持仓结构。",
        "share": "规模变化：近4季份额变化，反映资金净申购或赎回动向。",
        "div": "分红：历史分红次数与累计金额，反映分红策略与持有体验。",
        "momentum_quality": "动量质量：用风险调整后的动量得分，判断净值涨势是稳健驱动还是少数大波动堆出来的（低质量、持续性存疑）。",
        "risk": "风险提示：汇总各维度风险信号，给出综合风险分级。",
    }

    def _overall_conclusion(self) -> str:
        parts = []
        nav = self.results.get("nav")
        if nav and nav.is_ok() and nav.data:
            ret20 = nav.data.get("returns", {}).get("近20日涨幅%", "N/A")
            latest = nav.data.get("latest_nav", "N/A")
            parts.append(f"最新净值 {latest}，近20日 {ret20}%")
        performance = self.results.get("performance")
        if performance and performance.is_ok() and performance.data:
            sharpe = performance.data.get("sharpe")
            mdd = performance.data.get("max_drawdown")
            parts.append(f"夏普 {sharpe}，最大回撤 {mdd}%")
        parts = [p for p in parts if p]
        return "；".join(parts) + "。" if parts else "数据不足，无法生成综合结论。"

    def report(self) -> str:
        if not self.results:
            self.run()

        name = self.fund_name or self.ts_code
        _dts = self.results.get("nav")
        _ds = _dts.data.get("chart", {}).get("dates") if (_dts and _dts.is_ok() and _dts.data) else None
        lines = [
            f"# {name} 全景研究报告",
            "",
            f"> 数据日期：{_ds[-1] if _ds else self.end_date}（Tushare 数据为 T-1 日，即最新已发布数据）",
            "",
        ]

        dim_order = [d for d in self.dimensions if d in self.results]
        risk_dim = "risk" if "risk" in dim_order else None
        ordered = [d for d in dim_order if d != "risk"]

        idx = 0
        for dim in ordered:
            idx += 1
            res = self.results[dim]
            title = self.DIM_TITLES.get(dim, res.title)
            lines.append(f"## {idx}. {title}")
            desc = self.DIM_DESCRIPTIONS.get(dim)
            if desc:
                lines.append(f"> {desc}")
            if res.conclusion:
                lines.append(res.conclusion)
            if res.is_ok() and res.data:
                lines.extend(self._render_dimension(res))
                if res.insights:
                    lines.append("")
                    lines.append("**因子洞察**：")
                    for _ins in res.insights:
                        lines.append(f"- {_ins}")
            elif res.note:
                lines.append(f"- {res.note}")
            lines.append("")

        if risk_dim:
            idx += 1
            res = self.results[risk_dim]
            lines.append(f"## {idx}. 风险提示")
            risk_desc = self.DIM_DESCRIPTIONS.get("risk")
            if risk_desc:
                lines.append(f"> {risk_desc}")
            if res.is_ok() and res.data:
                for r in res.data.get("risks", []):
                    lines.append(f"- {r}")
                notes = res.data.get("notes", [])
                if notes:
                    lines.append("")
                    lines.append("**数据缺失说明**：")
                    for n in notes:
                        lines.append(f"- {n}")
            else:
                lines.append(f"- {res.note or '未发现显著风险信号'}")
            lines.append("")

        idx += 1
        lines.append(f"## {idx}. 整体分析评价")
        lines.append(self._overall_evaluation())
        lines.append("")
        lines.append("---")
        lines.append("*本报告由AI基于山西证券Tushare平台数据自动生成，基于 T-1 日历史数据，仅供技术交流与学习参考，不构成任何投资建议或财务指导。*")
        return "\n".join(lines)

    def _overall_evaluation(self) -> str:
        """跨维度整体分析评价。"""
        nav = self.results.get("nav")
        perf = self.results.get("performance")
        port = self.results.get("portfolio")
        share = self.results.get("share")
        div = self.results.get("div")
        peer = self.results.get("peer")

        nav_ok = nav and nav.is_ok() and nav.data
        perf_ok = perf and perf.is_ok() and perf.data
        port_ok = port and port.is_ok() and port.data

        lines = []
        name = self.fund_name or self.ts_code

        # 综合判断
        tags = []
        if perf_ok:
            vol = self._f(self._v(perf.data, "volatility"))
            mdd = self._f(self._v(perf.data, "max_drawdown"))
            if vol is not None and vol > 25:
                tags.append("高波动")
            elif vol is not None and vol < 15:
                tags.append("低波动")
            if mdd is not None and mdd < -20:
                tags.append("回撤较深")
        if nav_ok:
            ret250 = self._f(self._v(nav.data, "returns", "近250日涨幅%"))
            if ret250 is not None and ret250 > 15:
                tags.append("强势")
            elif ret250 is not None and ret250 < -10:
                tags.append("弱势")
        if tags:
            lines.append(f"**综合判断**：{name}当前具备{'、'.join(tags[:4])}特征。")
        else:
            lines.append(f"**综合判断**：{name}当前各项指标相对中性。")

        # 分维度要点
        points = []
        if nav_ok:
            ret20 = self._v(nav.data, "returns", "近20日涨幅%")
            ret250 = self._v(nav.data, "returns", "近250日涨幅%")
            latest = self._v(nav.data, "latest_nav")
            points.append(f"- **净值**：最新 {self._fmt(latest)}，近20日 {self._fmt(ret20)}%，近250日 {self._fmt(ret250)}%")
        if perf_ok:
            vol = self._v(perf.data, "volatility")
            mdd = self._v(perf.data, "max_drawdown")
            sharpe = self._v(perf.data, "sharpe")
            points.append(f"- **业绩**：年化波动 {self._fmt(vol)}%，最大回撤 {self._fmt(mdd)}%，夏普 {self._fmt(sharpe)}")
        if port_ok:
            holdings = port.data.get("holdings", [])
            top1 = holdings[0] if holdings else {}
            top1_name = top1.get("name") or top1.get("symbol", "N/A")
            top1_ratio = self._fmt(top1.get("ratio"))
            total = self._fmt(port.data.get("top10_total_ratio"))
            points.append(f"- **持仓**：第一大重仓 {top1_name}（{top1_ratio}%），前十大合计 {total}%")
        if share and share.is_ok() and share.data:
            latest_share = self._f(self._v(share.data, "latest_share"))
            changes = share.data.get("quarterly_changes", [])
            split_note = share.data.get("split_note", "")
            if len(changes) >= 2 and changes[0].get("fd_share") and changes[-1].get("fd_share"):
                chg = round((changes[-1]["fd_share"] / changes[0]["fd_share"] - 1) * 100, 2)
                points.append(f"- **规模**：最新份额 {self._fmt(latest_share)} 万份，近4季变化 {chg:+.2f}%{split_note}")
            else:
                points.append(f"- **规模**：最新份额 {self._fmt(latest_share)} 万份{split_note}")
        if peer and peer.is_ok() and peer.data:
            own = peer.data.get("own") or {}
            rank = own.get("近20日%排名%", "N/A")
            total_peers = peer.data.get("peer_total") or len(peer.data.get("peers", []))
            scope = peer.data.get("scope") or self.fund_type or "同类"
            points.append(f"- **同类**：{scope}共 {total_peers} 只，近20日排名 {self._fmt(rank)}% 分位")
        if div and div.is_ok() and div.data:
            count = div.data.get("count", "N/A")
            total_div = self._fmt(div.data.get("total_div_cash"))
            points.append(f"- **分红**：历史 {count} 次，累计 {total_div} 元/份")

        if points:
            lines.append("")
            lines.extend(points)

        # 风格定位
        style_parts = []
        if perf_ok:
            vol = self._f(self._v(perf.data, "volatility"))
            if vol is not None and vol < 15:
                style_parts.append("低波动/稳健型")
            elif vol is not None and vol > 25:
                style_parts.append("高波动/进取型")
        if self.fund_type:
            if "股票" in str(self.fund_type):
                style_parts.append("股票型")
            elif "债券" in str(self.fund_type):
                style_parts.append("债券型")

        if style_parts:
            lines.append("")
            lines.append(f"**风格定位**：{name}属于{'/'.join(style_parts)}基金。")

        # 结论
        concl_parts = []
        if perf_ok:
            mdd = self._f(self._v(perf.data, "max_drawdown"))
            if mdd is not None and mdd < -20:
                concl_parts.append(f"最大回撤 {mdd:.1f}%较深")
        if share and share.is_ok() and share.data:
            changes = share.data.get("quarterly_changes", [])
            if len(changes) >= 2 and changes[0].get("fd_share") and changes[-1].get("fd_share"):
                chg = round((changes[-1]["fd_share"] / changes[0]["fd_share"] - 1) * 100, 2)
                if chg < -20:
                    concl_parts.append(f"份额缩水 {abs(chg):.1f}%需关注赎回压力")

        if concl_parts:
            lines.append("")
            lines.append(f"**结论**：{name}{'，'.join(concl_parts)}。综合来看，需结合市场环境与自身风险承受度判断。以上为基于 T-1 历史数据的描述性分析，不构成投资建议。")
        else:
            lines.append("")
            lines.append(f"**结论**：{name}当前各项指标平稳，未现显著风险信号。以上为基于 T-1 历史数据的描述性分析，不构成投资建议。")

        # ---- 因子综合评分 + 三维定位 + 风险预算 ----
        try:
            ret250 = self._f(self._v(nav.data, "returns", "近250日涨幅%")) if nav_ok else None
            sharpe = self._f(self._v(perf.data, "sharpe")) if perf_ok else None
            # 基金无 PE/F-Score，估值与质量维跳过
            composite = calc_composite_score(None, None, ret250, sharpe, None)
            if composite.get("composite") is not None:
                lines.append("")
                lines.append(f"**因子综合评分**：{composite['composite']}（{composite['rating']}，等权雏形非IC优化，仅供参考）")
                fac = composite.get("factors", {})
                if fac:
                    lines.append(f"- 各维子分：{' / '.join(f'{k}{v}' for k, v in fac.items())}")

            positioning = calc_factor_positioning(None, None, ret250)
            if positioning.get("positioning"):
                lines.append("")
                lines.append(f"**因子定位**：{positioning['positioning']}")

            if perf_ok:
                mdd = self._f(self._v(perf.data, "max_drawdown"))
                vol = self._f(self._v(perf.data, "volatility"))
                budget = calc_risk_budget(None, mdd, None, None, vol)
                if budget.get("suggested_position_pct") is not None:
                    lines.append("")
                    lines.append(f"**风险预算**：{budget['risk_level']}风险等级（参考风险承受度约 {budget['suggested_position_pct']}%，基于回撤/波动测算，为风险评估参考，非配置建议）")
                    for r in budget.get("reasons", []):
                        lines.append(f"- {r}")
        except Exception:
            pass

        return "\n".join(lines) if lines else "维度数据不完整，暂无法给出跨维度综合判断。"

    def _render_dimension(self, res) -> list:
        """渲染单个维度的数据表 + 分析评价。"""
        lines = []
        data = res.data or {}

        if res.title == "概况":
            label_map = {
                "ts_code": "基金代码", "name": "基金简称", "fund_type": "基金类型",
                "found_date": "成立日期", "list_date": "上市日期", "delist_date": "退市日期", "management": "基金管理人",
            }
            rows = []
            for k, v in data.items():
                if v is None or str(v) == "nan" or k == "manager_company":
                    continue
                if k in ("found_date", "list_date", "delist_date") and str(v).isdigit() and len(str(v)) == 8:
                    v = f"{str(v)[:4]}-{str(v)[4:6]}-{str(v)[6:]}"
                rows.append({"项目": label_map.get(k, k), "内容": str(v)})
            if rows:
                lines.append(self._md(pd.DataFrame(rows)))
            mc = data.get("manager_company")
            if mc:
                _mc_label = {"shortname": "简称", "setup_date": "成立日期", "employees": "员工数", "reg_capital": "注册资本(万元)", "chairman": "董事长"}
                mc_rows = []
                for k, v in mc.items():
                    if v is None or str(v) == "nan":
                        continue
                    if k == "setup_date" and str(v).isdigit() and len(str(v)) == 8:
                        v = f"{str(v)[:4]}-{str(v)[4:6]}-{str(v)[6:]}"
                    mc_rows.append({"项目": _mc_label.get(k, k), "内容": str(v)})
                if mc_rows:
                    lines.append("\n**管理人（基金公司）**：")
                    lines.append(self._md(pd.DataFrame(mc_rows)))

        elif res.title == "净值走势":
            returns = data.get("returns", {})
            all_keys = [k for k in ["近5日涨幅%", "近20日涨幅%", "近60日涨幅%", "近120日涨幅%", "近250日涨幅%"] if k in returns]
            rows = [{"区间": k, "数值": self._fmt(returns.get(k))} for k in all_keys]
            lines.append(self._md(pd.DataFrame(rows)))
            lines.append("")
            ec = data.get("etf_check") or {}
            if ec:
                if ec.get("premium_discount_pct") is not None:
                    _pd_pct = ec["premium_discount_pct"]
                    _pd_txt = "溢价" if _pd_pct > 0 else "折价" if _pd_pct < 0 else "平价"
                    lines.append(f"\n**ETF 折溢价**：{_pd_pct:+.2f}%（{_pd_txt}，收盘价相对单位净值；|折溢价|>1% 时注意交易成本与价格偏离风险）")
                if ec.get("tracking_error_ann_pct") is not None:
                    _te = ec["tracking_error_ann_pct"]
                    lines.append(f"**跟踪误差**：年化 {_te:.2f}%（基准 {ec.get('benchmark_code')}）；宽基 ETF 年化跟踪误差通常 <1%，偏高时检查费用、抽样复制或现金拖累")
            lines.append(f"**分析评价**：最新净值 {self._fmt(data.get('latest_nav'))}，近20日 {self._fmt(returns.get('近20日涨幅%'))}%，近250日 {self._fmt(returns.get('近250日涨幅%'))}%。净值走势反映基金长期趋势，需结合波动率和回撤综合判断。")

        elif res.title == "业绩指标":
            table = {
                "指标": ["年化波动%", "最大回撤%", "夏普比率"],
                "数值": [self._fmt(data.get("volatility")), self._fmt(data.get("max_drawdown")), self._fmt(data.get("sharpe"))],
            }
            lines.append(self._md(pd.DataFrame(table)))
            lines.append("")
            vol = self._f(data.get("volatility"))
            mdd = self._f(data.get("max_drawdown"))
            sharpe = self._f(data.get("sharpe"))
            parts = []
            if vol is not None:
                parts.append(f"年化波动 {vol}%，" + ("偏高" if vol > 25 else "适中" if vol > 15 else "较低"))
            if mdd is not None:
                parts.append(f"最大回撤 {mdd}%，" + ("较深" if mdd < -20 else "可控"))
            if sharpe is not None:
                parts.append(f"夏普 {sharpe}，" + ("风险调整收益较好" if sharpe > 1 else "风险调整收益一般" if sharpe > 0 else "风险调整收益较差"))
            lines.append(f"**分析评价**：{'，'.join(parts)}。")

        elif res.title == "同类对比":
            own = data.get("own") or {}
            if own:
                own_map = {"ts_code": "基金代码"}
                rows = [{"指标": own_map.get(k, k), "数值": str(v)} for k, v in own.items()]
                lines.append(self._md(pd.DataFrame(rows)))
            peers = data.get("peers", [])
            if peers:
                lines.append("")
                lines.append("**同类基金前10**：")
                _clean = [{k: v for k, v in p.items() if not k.startswith("_")} for p in peers]
                lines.append(self._md(pd.DataFrame(_clean).rename(columns={"ts_code": "基金代码"})))
            rank = own.get("近20日%排名%", "N/A")
            lines.append("")
            lines.append(f"**分析评价**：本基金近20日收益排名约 {self._fmt(rank)}% 分位，" + ("处于同类前1/3" if self._f(rank) and self._f(rank) < 33 else "处于同类中游" if self._f(rank) and self._f(rank) < 67 else "处于同类后1/3") + "。")

        elif res.title == "基金经理":
            rows = [
                {"项目": "基金经理", "内容": data.get("name", "N/A")},
                {"项目": "任职起始", "内容": data.get("begin_date", "N/A")},
            ]
            if data.get("tenure_days"):
                years = data["tenure_days"] // 365
                rows.append({"项目": "任职年限", "内容": f"约 {years} 年"})
            lines.append(self._md(pd.DataFrame(rows)))
            lines.append("")
            tenure = self._f(data.get("tenure_days"))
            if tenure is not None:
                years = int(tenure) // 365
                lines.append(f"**分析评价**：基金经理任职约 {years} 年，" + ("管理经验丰富" if years >= 5 else "管理经验尚浅" if years < 2 else "具备一定管理经验") + "。")

        elif res.title == "持仓分析":
            holdings = data.get("holdings", [])
            if holdings:
                h_rows = [{"股票代码": h.get("symbol", "N/A"), "股票名称": h.get("name", ""), "占比%": self._fmt(h.get("ratio"))} for h in holdings]
                lines.append(self._md(pd.DataFrame(h_rows)))
            total = self._fmt(data.get("top10_total_ratio"))
            lines.append("")
            lines.append(f"**分析评价**：前十大重仓占比合计 {total}%，" + ("集中度较高" if self._f(data.get("top10_total_ratio")) and self._f(data.get("top10_total_ratio")) > 50 else "集中度适中") + "。")

        elif res.title == "规模变化":
            changes = data.get("quarterly_changes", [])
            split_note = data.get("split_note", "")
            if changes:
                s_rows = []
                _prev = None
                for c in changes:
                    _row = {"季度": c.get("quarter", "N/A"), "份额(万份)": self._fmt(c.get("fd_share"))}
                    if _prev is not None and _prev and c.get("fd_share"):
                        _row["环比%"] = f"{(c['fd_share'] / _prev - 1) * 100:+.1f}"
                    s_rows.append(_row)
                    _prev = c.get("fd_share")
                lines.append(self._md(pd.DataFrame(s_rows)))
            lines.append("")
            if data.get("latest_scale_yi") is not None:
                lines.append("")
                lines.append(f"最新规模（份额×单位净值）约 {self._fmt(data.get('latest_scale_yi'))} 亿元。")
            if len(changes) >= 2 and changes[0].get("fd_share") and changes[-1].get("fd_share"):
                chg = round((changes[-1]["fd_share"] / changes[0]["fd_share"] - 1) * 100, 2)
                lines.append(f"**分析评价**：近4季份额变化 {chg:+.2f}%{split_note}，" + ("存在赎回压力" if chg < -10 else "规模相对稳定" if abs(chg) < 10 else "资金持续流入") + "。份额为该基金单只口径（同标的场内外其他基金不合并），规模为份额×单位净值的近似值。")

        elif res.title == "分红":
            rows = [
                {"指标": "分红次数", "数值": data.get("count", "N/A")},
                {"指标": "累计分红(元/份)", "数值": self._fmt(data.get("total_div_cash"))},
            ]
            lines.append(self._md(pd.DataFrame(rows)))
            if data.get("latest"):
                lines.append("")
                lines.append(f"最近一次除息日：{data['latest'].get('ex_date', 'N/A')}，每份派息 {self._fmt(data['latest'].get('div_cash'))} 元")
            lines.append("")
            _n = data.get("count", 0)
            lines.append(f"**分析评价**：成立以来累计分红 {_n} 次、合计 {self._fmt(data.get('total_div_cash'))} 元/份" + ("，分红政策稳定（累计口径，非当期收益承诺）" if _n > 10 else "，分红次数较少") + "。")

        elif res.title == "风险提示":
            for r in data.get("risks", []):
                lines.append(f"- {r}")
            notes = data.get("notes", [])
            if notes:
                lines.append("")
                lines.append("**数据缺失说明**：")
                for n in notes:
                    lines.append(f"- {n}")

        elif res.title == "动量质量":
            rows = []
            qm = data.get("quantitative_momentum", {})
            if qm:
                rows.append({"指标": "高质量动量得分(r_60-3000σ²)", "数值": self._fmt(qm.get("momentum"))})
                rows.append({"指标": "原始60日收益", "数值": self._fmt(qm.get("raw_return"))})
                rows.append({"指标": "60日收益标准差", "数值": self._fmt(qm.get("sigma"))})
            am = data.get("amplitude_momentum", {})
            if am:
                rows.append({"指标": "振幅切割A因子(低振幅日)", "数值": self._fmt(am.get("a_factor"))})
                rows.append({"指标": "振幅切割B因子(高振幅日)", "数值": self._fmt(am.get("b_factor"))})
            if data.get("source"):
                rows.append({"指标": "数据源", "数值": data["source"]})
            if rows:
                lines.append(self._md(pd.DataFrame(rows)))

        return [l for l in lines if l is not None]

# ---------- 便捷函数 ----------

def analyze_fund(ts_code: str, end_date: Optional[str] = None, dimensions: Optional[List[str]] = None) -> Dict[str, Any]:
    runner = FundAnalysisRunner(ts_code=ts_code, end_date=end_date, dimensions=dimensions)
    return runner.run()


def fund_report(ts_code: str, end_date: Optional[str] = None, dimensions: Optional[List[str]] = None) -> str:
    runner = FundAnalysisRunner(ts_code=ts_code, end_date=end_date, dimensions=dimensions)
    md = runner.report()
    return render_html_report(md, runner.results)


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    import argparse
    parser = argparse.ArgumentParser(description="基金综合分析 Runner")
    parser.add_argument("ts_code", help="基金代码，如 110011.OF 或 510300.SH")
    parser.add_argument("--end-date", default=None, help="分析截止日期 YYYYMMDD")
    parser.add_argument(
        "--output",
        default=None,
        help="HTML 报告输出路径，默认保存到当前目录 {ts_code}_report.html",
    )
    args = parser.parse_args()
    report = fund_report(args.ts_code, args.end_date)
    output = args.output or f"{args.ts_code}_report.html"
    with open(output, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"HTML 报告已保存: {os.path.abspath(output)}")
