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

from adjustment import apply_fund_adj, apply_etf_adj
from basic_metrics import calc_max_drawdown, calc_returns, calc_sharpe, calc_volatility
from data_api import DataAPI, shift_date
from result_model import DimensionResult, ResultStatus, safe_result


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
    DEFAULT_DIMENSIONS = ["overview", "nav", "performance", "peer", "manager", "portfolio", "share", "div", "risk"]

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

        data = {
            "ts_code": self.ts_code,
            "name": self.fund_name,
            "fund_type": self.fund_type,
            "found_date": row.get("found_date"),
            "issue_date": row.get("issue_date"),
            "delist_date": row.get("delist_date"),
        }
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

        returns = calc_returns(nav_series, periods=self.PERIODS)
        conclusion = f"最新净值 {latest_nav}，近20日 {returns.get('近20日涨幅%', 'N/A')}%"
        return DimensionResult.success("净值走势", conclusion=conclusion, data={
            "latest_nav": latest_nav,
            "returns": returns,
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
        }
        conclusion = f"最新份额 {latest_share:,.2f} 万份"
        if len(share_changes) >= 2:
            first = share_changes[0]["fd_share"]
            last = share_changes[-1]["fd_share"]
            if first and last:
                chg = round((last / first - 1) * 100, 2)
                conclusion += f"，近4季变化 {chg:+.2f}%"
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
        """从基金名称提取行业/主题关键词（剔除管理人名与通用词），缩窄同类对比口径，避免混入其他行业ETF。"""
        import re
        if not name:
            return []
        manager_stop = {"国泰","华宝","易方达","华夏","南方","嘉实","富国","广发","招商","博时","汇添富","景顺","东财","浦银","华泰柏瑞","银华","华安","大成","鹏华","工银","交银","建信","兴全","中欧","万家","国联安","长信","银河","中银","上投","摩根","泰康","平安","前海","开源","国寿","人保","西藏","诺安","信达","华商","中邮","安信","长城","中海","中加"}
        generic_stop = {"基金","指数","策略","增强","红利","低波","联接","股票型","债券型","混合型","货币","商品","REITs","LOF","ETF","型"}
        cn = re.findall(r"[\u4e00-\u9fa5]+", str(name))
        return [s for s in cn if s not in manager_stop and s not in generic_stop and len(s) >= 2]

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
                narrow = peers_df[peers_df["name"].apply(lambda n: any(k in str(n) for k in keywords))]
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
                    return {
                        "ts_code": code,
                        "近20日%": returns.get("近20日涨幅%", "N/A"),
                        "近60日%": returns.get("近60日涨幅%", "N/A"),
                        "近120日%": returns.get("近120日涨幅%", "N/A"),
                        "近250日%": returns.get("近250日涨幅%", "N/A"),
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
        rank20 = own_row.get("近20日%排名%", "N/A")
        conclusion = f"同类基金({peer_scope})共 {len(peer_df)} 只，本基金近20日收益排名约 {rank20}% 分位"
        return DimensionResult.success("同类对比", conclusion=conclusion, data=data)

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
            elif df[col].dtype == object:
                df[col] = df[col].apply(
                    lambda x: self._fmt(x) if isinstance(x, (int, float, np.integer, np.floating))
                    else ("N/A" if x is None or (isinstance(x, float) and x != x) else str(x))
                )
        return df.to_markdown(index=False, disable_numparse=True)

    DIM_TITLES = {
        "overview": "概况", "nav": "净值走势", "performance": "业绩指标",
        "peer": "同类对比", "manager": "基金经理", "portfolio": "持仓分析",
        "share": "规模变化", "div": "分红", "risk": "风险提示",
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
        lines = [
            f"# {name} 全景研究报告",
            "",
            f"> 数据日期：{self.end_date}（Tushare 数据为 T-1 日）",
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
            if res.conclusion:
                lines.append(res.conclusion)
            if res.is_ok() and res.data:
                lines.extend(self._render_dimension(res))
            elif res.note:
                lines.append(f"- {res.note}")
            lines.append("")

        if risk_dim:
            idx += 1
            res = self.results[risk_dim]
            lines.append(f"## {idx}. 风险提示")
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
            if len(changes) >= 2 and changes[0].get("fd_share") and changes[-1].get("fd_share"):
                chg = round((changes[-1]["fd_share"] / changes[0]["fd_share"] - 1) * 100, 2)
                points.append(f"- **规模**：最新份额 {self._fmt(latest_share)} 万份，近4季变化 {chg:+.2f}%")
            else:
                points.append(f"- **规模**：最新份额 {self._fmt(latest_share)} 万份")
        if peer and peer.is_ok() and peer.data:
            own = peer.data.get("own", {})
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
            lines.append(f"**结论**：{name}{'，'.join(concl_parts)}。综合来看，" + ("适合作为配置参考" if not concl_parts else "需结合市场环境判断配置时机") + "，注意分散风险，不构成投资建议。")
        else:
            lines.append("")
            lines.append(f"**结论**：{name}当前表现平稳，可作为配置参考，注意分散风险，不构成投资建议。")

        return "\n".join(lines) if lines else "维度数据不完整，暂无法给出跨维度综合判断。"

    def _render_dimension(self, res) -> list:
        """渲染单个维度的数据表 + 分析评价。"""
        lines = []
        data = res.data or {}

        if res.title == "概况":
            label_map = {
                "ts_code": "基金代码", "name": "基金简称", "fund_type": "基金类型",
                "found_date": "成立日期", "issue_date": "上市日期", "delist_date": "退市日期",
            }
            rows = []
            for k, v in data.items():
                if v is not None and str(v) != "nan":
                    rows.append({"项目": label_map.get(k, k), "内容": str(v)})
            if rows:
                lines.append(self._md(pd.DataFrame(rows)))

        elif res.title == "净值走势":
            returns = data.get("returns", {})
            all_keys = [k for k in ["近5日涨幅%", "近20日涨幅%", "近60日涨幅%", "近120日涨幅%", "近250日涨幅%"] if k in returns]
            rows = [{"区间": k, "数值": self._fmt(returns.get(k))} for k in all_keys]
            lines.append(self._md(pd.DataFrame(rows)))
            lines.append("")
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
            own = data.get("own", {})
            if own:
                rows = [{"指标": k, "数值": str(v)} for k, v in own.items()]
                lines.append(self._md(pd.DataFrame(rows)))
            peers = data.get("peers", [])
            if peers:
                lines.append("")
                lines.append("**同类基金前10**：")
                lines.append(self._md(pd.DataFrame(peers)))
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
            if changes:
                s_rows = [{"季度": c.get("quarter", "N/A"), "份额(万份)": self._fmt(c.get("fd_share"))} for c in changes]
                lines.append(self._md(pd.DataFrame(s_rows)))
            lines.append("")
            if len(changes) >= 2 and changes[0].get("fd_share") and changes[-1].get("fd_share"):
                chg = round((changes[-1]["fd_share"] / changes[0]["fd_share"] - 1) * 100, 2)
                lines.append(f"**分析评价**：近4季份额变化 {chg:+.2f}%，" + ("存在赎回压力" if chg < -10 else "规模相对稳定" if abs(chg) < 10 else "资金持续流入") + "。")

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
            lines.append(f"**分析评价**：历史分红 {data.get('count', 0)} 次，累计 {self._fmt(data.get('total_div_cash'))} 元/份，" + ("分红频率较高，适合长期持有" if data.get("count", 0) > 10 else "分红较少") + "。")

        elif res.title == "风险提示":
            for r in data.get("risks", []):
                lines.append(f"- {r}")
            notes = data.get("notes", [])
            if notes:
                lines.append("")
                lines.append("**数据缺失说明**：")
                for n in notes:
                    lines.append(f"- {n}")

        return [l for l in lines if l is not None]

# ---------- 便捷函数 ----------

def analyze_fund(ts_code: str, end_date: Optional[str] = None, dimensions: Optional[List[str]] = None) -> Dict[str, Any]:
    runner = FundAnalysisRunner(ts_code=ts_code, end_date=end_date, dimensions=dimensions)
    return runner.run()


def fund_report(ts_code: str, end_date: Optional[str] = None, dimensions: Optional[List[str]] = None) -> str:
    runner = FundAnalysisRunner(ts_code=ts_code, end_date=end_date, dimensions=dimensions)
    return runner.report()


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    import argparse
    parser = argparse.ArgumentParser(description="基金综合分析 Runner")
    parser.add_argument("ts_code", help="基金代码，如 110011.OF 或 510300.SH")
    parser.add_argument("--end-date", default=None, help="分析截止日期 YYYYMMDD")
    args = parser.parse_args()
    print(fund_report(args.ts_code, args.end_date))
