#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
指数综合分析 Runner。

维度：
1. 概况
2. 行情趋势
3. 估值分析
4. 成分权重
5. 行业分布
6. 国际对比
7. 风险提示
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from basic_metrics import calc_ma, calc_max_drawdown, calc_returns, calc_sharpe, calc_volatility
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


class IndexAnalysisRunner:
    """指数综合分析 Runner。"""

    PERIODS = (5, 20, 60, 120, 250)
    DEFAULT_DIMENSIONS = ["overview", "trend", "valuation", "weight", "sector", "global", "margin", "risk"]

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

        self.index_name: Optional[str] = None
        self.results: Dict[str, DimensionResult] = {}

    # ---------- 维度：概况 ----------

    @safe_result("概况")
    def analyze_overview(self) -> DimensionResult:
        df = self.api.get_index_basic(self.ts_code)
        if df is None or df.empty:
            return DimensionResult.empty("概况", note="无法获取指数基础信息")

        row = df.iloc[0]
        self.index_name = row.get("name")
        data = {
            "ts_code": self.ts_code,
            "name": self.index_name,
            "publisher": row.get("publisher"),
            "category": row.get("category"),
            "base_date": row.get("base_date"),
            "base_point": row.get("base_point"),
            "list_date": row.get("list_date"),
        }
        return DimensionResult.success("概况", data=data)

    # ---------- 维度：行情趋势 ----------

    @safe_result("行情趋势")
    def analyze_trend(self) -> DimensionResult:
        start = shift_date(self.end_date, -max(self.PERIODS) * 2)
        df = self.api.get_index_daily(self.ts_code, start, self.end_date)
        if df is None or df.empty:
            return DimensionResult.empty("行情趋势", note="无法获取指数行情")

        series = df.set_index("trade_date")["close"].sort_index()
        if len(series) < 2:
            return DimensionResult.insufficient_history("行情趋势", note="历史数据不足")

        returns = calc_returns(series, periods=self.PERIODS)
        ma = calc_ma(series)
        volatility = calc_volatility(series)
        max_dd = calc_max_drawdown(series)
        sharpe = calc_sharpe(series)

        # 基准对比：沪深300
        bench_code = "000300.SH"
        bench_data = None
        if bench_code != self.ts_code:
            df_bench = self.api.get_index_daily(bench_code, start, self.end_date)
            if df_bench is not None and not df_bench.empty:
                bench_series = df_bench.set_index("trade_date")["close"].sort_index()
                bench_returns = calc_returns(bench_series, periods=self.PERIODS)
                bench_vol = calc_volatility(bench_series)
                bench_mdd = calc_max_drawdown(bench_series)
                bench_sharpe = calc_sharpe(bench_series)
                bench_data = {
                    "ts_code": bench_code,
                    "returns": bench_returns,
                    "volatility": bench_vol,
                    "max_drawdown": bench_mdd,
                    "sharpe": bench_sharpe,
                }

        conclusion = f"近20日涨幅 {returns.get('近20日涨幅%', 'N/A')}%，年化波动 {volatility}%，最大回撤 {max_dd}%"
        data = {
            "returns": returns,
            "ma": ma,
            "volatility": volatility,
            "max_drawdown": max_dd,
            "sharpe": sharpe,
            "benchmark": bench_data,
        }
        return DimensionResult.success("行情趋势", conclusion=conclusion, data=data)

    # ---------- 维度：估值 ----------

    @safe_result("估值分析")
    def analyze_valuation(self) -> DimensionResult:
        # index_dailybasic 对部分指数（如科创50）不可用
        start = shift_date(self.end_date, -max(self.PERIODS) * 2)
        df = self.api._call("index_dailybasic", {"ts_code": self.ts_code, "start_date": start, "end_date": self.end_date},
                            "ts_code,trade_date,pe,pb,total_mv")
        if df is None or df.empty:
            # 不做聚合估算：无 index_dailybasic 官方指数级估值数据即判 empty，不编造数据
            return DimensionResult.empty("估值分析", note="该指数暂无指数级估值数据（index_dailybasic 未覆盖）")

        df = df.sort_values("trade_date").reset_index(drop=True)
        latest = df.iloc[-1]
        pe = _safe_float(latest.get("pe"))
        pb = _safe_float(latest.get("pb"))

        pe_hist = None
        pb_hist = None
        if len(df) >= 10:
            if pe is not None:
                pe_hist = round((df["pe"] < pe).sum() / len(df) * 100, 1)
            if pb is not None:
                pb_hist = round((df["pb"] < pb).sum() / len(df) * 100, 1)

        data = {"pe": pe, "pb": pb, "pe_hist_percentile": pe_hist, "pb_hist_percentile": pb_hist}
        conclusion = f"PE {pe if pe is not None else 'N/A'}，PB {pb if pb is not None else 'N/A'}"
        if pe_hist is not None:
            conclusion += f"，PE历史分位 {pe_hist}%"
        return DimensionResult.success("估值分析", conclusion=conclusion, data=data)

    # ---------- 维度：成分权重 ----------

    @safe_result("成分权重")
    def analyze_weight(self) -> DimensionResult:
        # index_weight 为月度数据，取最近一个月末
        start = shift_date(self.end_date, -40)
        df = self.api.get_index_weight(self.ts_code, start, self.end_date)
        if df is None or df.empty:
            return DimensionResult.empty("成分权重", note="无法获取成分权重")

        df = df.sort_values("trade_date").reset_index(drop=True)
        latest_date = df.iloc[-1]["trade_date"]
        latest = df[df["trade_date"] == latest_date].sort_values("weight", ascending=False).head(10)

        # 批量补充股票名称（单次API调用，避免逐只查被限流）
        all_codes = ", ".join(latest["con_code"].tolist())
        name_map = {}
        try:
            sb = self.api._call("stock_basic", {"ts_code": all_codes}, "ts_code,name")
            if sb is not None and not sb.empty:
                name_map = dict(zip(sb["ts_code"], sb["name"]))
        except Exception:
            pass
        # 逐只补充缺失的名称（批量查可能因退市等原因遗漏）
        for code in latest["con_code"]:
            if code not in name_map:
                import time; time.sleep(0.3)
                try:
                    sb = self.api._call("stock_basic", {"ts_code": code}, "ts_code,name")
                    if sb is not None and not sb.empty:
                        name_map[code] = sb.iloc[0].get("name", "")
                except Exception:
                    pass
        weight_records = [{"con_code": r["con_code"], "name": name_map.get(r["con_code"], ""), "weight": _safe_float(r["weight"])} for _, r in latest.iterrows()]

        data = {
            "date": latest_date,
            "top_weights": weight_records,
            "top10_total_weight": round(float(latest["weight"].sum()), 2),
        }
        conclusion = f"最新权重日期 {latest_date}，前十大成分股权重合计 {latest['weight'].sum():.2f}%"
        return DimensionResult.success("成分权重", conclusion=conclusion, data=data)

    # ---------- 维度：行业分布 ----------

    @safe_result("行业分布")
    def analyze_sector(self) -> DimensionResult:
        # 用 index_weight 获取成分股（index_member 仅适用于申万行业指数）
        start = shift_date(self.end_date, -40)
        df_w = self.api.get_index_weight(self.ts_code, start, self.end_date)
        if df_w is None or df_w.empty:
            return DimensionResult.empty("行业分布", note="无法获取指数成分股")

        # 取最新一期成分股
        df_w = df_w.sort_values("trade_date")
        latest_date = df_w.iloc[-1]["trade_date"]
        latest = df_w[df_w["trade_date"] == latest_date]
        codes = latest["con_code"].unique().tolist()  # 全量成分股，不截断

        # 一次性查全市场 stock_basic（含 industry 字段），本地匹配成分股行业，避免逐只查被限流
        ind_map = {}
        try:
            all_basic = self.api._call("stock_basic", {"list_status": "L"}, "ts_code,name,industry")
            if all_basic is not None and not all_basic.empty:
                ind_map = dict(zip(all_basic["ts_code"], all_basic["industry"]))
        except Exception:
            pass

        industries = [{"ts_code": c, "industry": ind_map.get(c, "未知")} for c in codes]
        if not ind_map:
            return DimensionResult.empty("行业分布", note="无法获取成分股行业信息")

        ind_df = pd.DataFrame(industries)
        sector_dist = ind_df["industry"].value_counts().head(10).reset_index()
        sector_dist.columns = ["行业", "成分股数量"]
        sector_dist["占比%"] = round(sector_dist["成分股数量"] / len(ind_df) * 100, 2)

        matched_count = int((ind_df["industry"] != "未知").sum())
        data = {"distribution": sector_dist.to_dict("records"), "total_members": len(codes), "matched": matched_count}
        conclusion = f"成分股覆盖 {len(ind_df)} 只（匹配行业 {matched_count} 只），前三大行业：{', '.join(sector_dist['行业'].head(3).tolist())}"
        return DimensionResult.success("行业分布", conclusion=conclusion, data=data)

    # ---------- 维度：国际对比 ----------

    @safe_result("国际对比")
    def analyze_global(self) -> DimensionResult:
        # 仅对 A 股主要指数做国际对比
        peers = {
            "000300.SH": [("SPX", "标普500"), ("DJI", "道琼斯"), ("HSI", "恒生指数")],
            "000001.SH": [("SPX", "标普500"), ("DJI", "道琼斯"), ("HSI", "恒生指数")],
            "399001.SZ": [("IXIC", "纳斯达克"), ("HSI", "恒生指数")],
            "399006.SZ": [("IXIC", "纳斯达克"), ("SPX", "标普500"), ("HSI", "恒生指数")],
            "399005.SZ": [("IXIC", "纳斯达克"), ("SPX", "标普500")],
            "000016.SH": [("DJI", "道琼斯"), ("HSI", "恒生指数")],
            "000688.SH": [("IXIC", "纳斯达克"), ("SPX", "标普500"), ("HSI", "恒生指数")],
            "000905.SH": [("SPX", "标普500"), ("HSI", "恒生指数")],
        }
        peer_codes = peers.get(self.ts_code)
        if not peer_codes:
            return DimensionResult.empty("国际对比", note="未配置该指数的国际对比标的")

        start = shift_date(self.end_date, -max(self.PERIODS) * 2)
        rows = []
        for code, name in [(self.ts_code, self.index_name or self.ts_code)] + peer_codes:
            if code == self.ts_code:
                df = self.api.get_index_daily(code, start, self.end_date)
            else:
                df = self.api.get_index_global(code, start, self.end_date)
            if df is None or df.empty:
                continue
            series = df.set_index("trade_date")["close"].sort_index()
            ret = calc_returns(series, periods=self.PERIODS)
            rows.append({
                "标的": name,
                "近20日%": ret.get("近20日涨幅%", "N/A"),
                "近60日%": ret.get("近60日涨幅%", "N/A"),
                "近250日%": ret.get("近250日涨幅%", "N/A"),
            })

        if not rows:
            return DimensionResult.empty("国际对比", note="无法获取国际指数数据")

        data = {"comparison": rows}
        conclusion = "近20日国际/跨市场涨跌幅对比见表"
        return DimensionResult.success("国际对比", conclusion=conclusion, data=data)


    # ---------- 维度：两融/市场杠杆 ----------

    @safe_result("两融/市场杠杆")
    def analyze_margin(self) -> DimensionResult:
        # 上交所全市场两融汇总
        sse_df = self.api.get_margin(self.end_date, exchange_id="SSE")
        szse_df = self.api.get_margin(self.end_date, exchange_id="SZSE")

        def _summary(df):
            if df is None or df.empty:
                return None
            df = df.sort_values("trade_date").reset_index(drop=True)
            latest = df.iloc[-1]
            rzye = _safe_float(latest.get("rzye"))
            start_rzye = _safe_float(df.iloc[0].get("rzye")) if len(df) > 1 else None
            chg = None
            if rzye is not None and start_rzye and start_rzye > 0:
                chg = round((rzye / start_rzye - 1) * 100, 2)
            return {
                "latest_rzye_billion": rzye / 1e8 if rzye else None,
                "latest_rzrqye_billion": _safe_float(latest.get("rzrqye")) / 1e8 if latest.get("rzrqye") else None,
                "rzye_chg_pct": chg,
            }

        data = {}
        if sse_df is not None and not sse_df.empty:
            data["sse"] = _summary(sse_df)
        if szse_df is not None and not szse_df.empty:
            data["szse"] = _summary(szse_df)

        if not data:
            return DimensionResult.empty("两融/市场杠杆", note="无法获取全市场两融数据")

        # 计算两市合计
        total_rzye = sum(s["latest_rzye_billion"] for s in data.values() if s and s.get("latest_rzye_billion"))
        total_rzrqye = sum(s["latest_rzrqye_billion"] for s in data.values() if s and s.get("latest_rzrqye_billion"))
        # 取平均变化方向
        chgs = [s["rzye_chg_pct"] for s in data.values() if s and s.get("rzye_chg_pct") is not None]
        avg_chg = round(sum(chgs) / len(chgs), 2) if chgs else None
        data["total_rzye_billion"] = total_rzye
        data["total_rzrqye_billion"] = total_rzrqye
        data["avg_rzye_chg_pct"] = avg_chg

        parts = [f"两市融资余额合计 {total_rzye:.0f}亿"]
        if avg_chg is not None:
            direction = "上升" if avg_chg > 0 else "下降"
            parts.append(f"近60日融资余额{direction} {abs(avg_chg)}%")
        conclusion = "，".join(parts)
        return DimensionResult.success("两融/市场杠杆", conclusion=conclusion, data=data)

    # ---------- 维度：风险提示 ----------

    def analyze_risk(self) -> DimensionResult:
        risks = []
        notes = []

        trend = self.results.get("trend")
        if trend and trend.is_ok() and trend.data:
            vol = trend.data.get("volatility")
            mdd = trend.data.get("max_drawdown")
            if isinstance(vol, (int, float)) and vol > 30:
                risks.append(f"指数年化波动 {vol}% 偏高")
            if isinstance(mdd, (int, float)) and mdd < -25:
                risks.append(f"指数近一年最大回撤 {mdd}% 较深")

        valuation = self.results.get("valuation")
        if valuation and valuation.is_ok() and valuation.data:
            pe_hist = valuation.data.get("pe_hist_percentile")
            pb_hist = valuation.data.get("pb_hist_percentile")
            if isinstance(pe_hist, (int, float)) and pe_hist > 80:
                risks.append(f"PE 历史分位 {pe_hist}%，估值偏高")
            if isinstance(pb_hist, (int, float)) and pb_hist > 80:
                risks.append(f"PB 历史分位 {pb_hist}%，估值偏高")

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
            "trend": self.analyze_trend,
            "valuation": self.analyze_valuation,
            "weight": self.analyze_weight,
            "sector": self.analyze_sector,
            "global": self.analyze_global,
            "margin": self.analyze_margin,
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
            "name": self.index_name,
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
        "overview": "概况",
        "trend": "行情趋势",
        "valuation": "估值分析",
        "weight": "成分权重",
        "sector": "行业分布",
        "global": "国际对比",
        "margin": "两融/市场杠杆",
        "risk": "风险提示",
    }

    def _overall_conclusion(self) -> str:
        parts = []
        trend = self.results.get("trend")
        if trend and trend.is_ok() and trend.data:
            ret20 = trend.data.get("returns", {}).get("近20日涨幅%", "N/A")
            vol = trend.data.get("volatility")
            parts.append(f"近20日涨幅 {ret20}%，年化波动 {vol}%")
        valuation = self.results.get("valuation")
        if valuation and valuation.is_ok() and valuation.data:
            pe = valuation.data.get("pe", "N/A")
            pe_hist = valuation.data.get("pe_hist_percentile")
            parts.append(f"PE {pe}" + (f"（历史分位 {pe_hist}%）" if pe_hist is not None else ""))
        weight = self.results.get("weight")
        if weight and weight.is_ok() and weight.data:
            tw = weight.data.get("top_weights", [])
            if tw:
                top1 = tw[0]
                top1_label = f"{top1.get('name', '')}（{top1.get('con_code', 'N/A')}）" if top1.get('name') else top1.get('con_code', 'N/A')
                parts.append(f"第一大权重股 {top1_label}（{top1.get('weight', 'N/A')}%）")
        parts = [p for p in parts if p]
        return "；".join(parts) + "。" if parts else "数据不足，无法生成综合结论。"

    def report(self) -> str:
        if not self.results:
            self.run()

        name = self.index_name or self.ts_code
        lines = [
            f"# {name} 指数综合分析报告",
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
        trend = self.results.get("trend")
        valuation = self.results.get("valuation")
        weight = self.results.get("weight")
        margin = self.results.get("margin")
        macro = self.results.get("macro")

        trend_ok = trend and trend.is_ok() and trend.data
        val_ok = valuation and valuation.is_ok() and valuation.data
        wt_ok = weight and weight.is_ok() and weight.data

        lines = []
        name = self.index_name or self.ts_code

        # 综合判断
        tags = []
        if val_ok:
            pe_hist = self._f(self._v(valuation.data, "pe_hist_percentile"))
            pb_hist = self._f(self._v(valuation.data, "pb_hist_percentile"))
            if pe_hist is not None and pe_hist > 80:
                tags.append("估值偏高")
            elif pe_hist is not None and pe_hist < 20:
                tags.append("估值偏低")
        if trend_ok:
            vol = self._f(self._v(trend.data, "volatility"))
            if vol is not None and vol > 30:
                tags.append("高波动")
            elif vol is not None and vol < 15:
                tags.append("低波动")
            ret250 = self._f(self._v(trend.data, "returns", "近250日涨幅%"))
            if ret250 is not None and ret250 > 20:
                tags.append("强势")
            elif ret250 is not None and ret250 < -10:
                tags.append("弱势")

        if tags:
            lines.append(f"**综合判断**：{name}当前具备{'、'.join(tags[:4])}特征。")
        else:
            lines.append(f"**综合判断**：{name}当前各项指标相对中性。")

        # 分维度要点
        points = []
        if trend_ok:
            ret20 = self._v(trend.data, "returns", "近20日涨幅%")
            ret250 = self._v(trend.data, "returns", "近250日涨幅%")
            vol = self._v(trend.data, "volatility")
            mdd = self._v(trend.data, "max_drawdown")
            sharpe = self._v(trend.data, "sharpe")
            bench = trend.data.get("benchmark") or {}
            bench20 = self._v(bench, "returns", "近20日涨幅%")
            bench250 = self._v(bench, "returns", "近250日涨幅%")
            points.append(f"- **趋势**：近20日 {self._fmt(ret20)}%，近250日 {self._fmt(ret250)}%，年化波动 {self._fmt(vol)}%，最大回撤 {self._fmt(mdd)}%，夏普 {self._fmt(sharpe)}")
            if bench:
                points.append(f"- **基准(沪深300)**：近20日 {self._fmt(bench20)}%，近250日 {self._fmt(bench250)}%")
        if val_ok:
            pe = self._v(valuation.data, "pe")
            pb = self._v(valuation.data, "pb")
            pe_hist = self._v(valuation.data, "pe_hist_percentile")
            pb_hist = self._v(valuation.data, "pb_hist_percentile")
            points.append(f"- **估值**：PE {self._fmt(pe)}（历史分位 {self._fmt(pe_hist)}%），PB {self._fmt(pb)}（历史分位 {self._fmt(pb_hist)}%）")
        if wt_ok:
            tw = weight.data.get("top_weights", [])
            top1 = tw[0] if tw else {}
            top_name = top1.get("name", "") or top1.get("con_code", "N/A")
            top_code = top1.get("con_code", "N/A")
            top_w = self._fmt(top1.get("weight"))
            total_w = self._fmt(weight.data.get("top10_total_weight"))
            points.append(f"- **权重**：第一大权重 {top_name}（{top_code}，{top_w}%），前十大合计 {total_w}%")
        if margin and margin.is_ok() and margin.data:
            sse = margin.data.get("sse") or {}
            szse = margin.data.get("szse") or {}
            sse_rzye = self._fmt(self._v(sse, "latest_rzye_billion"))
            szse_rzye = self._fmt(self._v(szse, "latest_rzye_billion"))
            points.append(f"- **杠杆**：SSE融资余额 {sse_rzye}亿，SZSE融资余额 {szse_rzye}亿")

        if points:
            lines.append("")
            lines.extend(points)

        # 风格定位
        style_parts = []
        if val_ok:
            pe_hist = self._f(self._v(valuation.data, "pe_hist_percentile"))
            if pe_hist is not None and pe_hist > 80:
                style_parts.append("高估值")
            elif pe_hist is not None and pe_hist < 20:
                style_parts.append("低估值")
        if trend_ok:
            vol = self._f(self._v(trend.data, "volatility"))
            beta_approx = self._f(self._v(trend.data, "returns", "近250日涨幅%"))
            bench250 = self._f(self._v(trend.data.get("benchmark", {}), "returns", "近250日涨幅%"))
            if vol is not None and vol > 30:
                style_parts.append("高波动/成长型")
            elif vol is not None and vol < 15:
                style_parts.append("低波动/稳健型")
            if beta_approx is not None and bench250 is not None and bench250 != 0:
                rs = beta_approx / bench250
                if rs > 1.5:
                    style_parts.append("高弹性")
                elif rs < 0.5:
                    style_parts.append("防御型")

        if style_parts:
            lines.append("")
            lines.append(f"**风格定位**：{name}属于{'/'.join(style_parts)}型指数。")

        # 结论
        concl_parts = []
        if val_ok:
            pe_hist = self._f(self._v(valuation.data, "pe_hist_percentile"))
            if pe_hist is not None and pe_hist > 80:
                concl_parts.append("估值处于历史高位，需警惕回调风险")
            elif pe_hist is not None and pe_hist < 20:
                concl_parts.append("估值处于历史低位，具备一定安全边际")
        if trend_ok:
            vol = self._f(self._v(trend.data, "volatility"))
            mdd = self._f(self._v(trend.data, "max_drawdown"))
            if vol is not None and vol > 30:
                concl_parts.append(f"年化波动 {vol:.1f}%偏高")
            if mdd is not None and mdd < -20:
                concl_parts.append(f"最大回撤 {mdd:.1f}%较深")

        if concl_parts:
            lines.append("")
            lines.append(f"**结论**：{name}{'，'.join(concl_parts)}。综合来看，" + ("适合风险偏好型投资者关注" if tags and ("高波动" in tags or "估值偏高" in tags) else "可作为配置参考") + "，注意分散风险，不构成投资建议。")

        return "\n".join(lines) if lines else "维度数据不完整，暂无法给出跨维度综合判断。"

    def _render_dimension(self, res) -> list:
        """渲染单个维度的数据表 + 分析评价。"""
        lines = []
        data = res.data or {}

        if res.title == "概况":
            # 概况数据直接在顶层，遍历生成表
            label_map = {
                "ts_code": "指数代码",
                "name": "指数简称",
                "publisher": "发布机构",
                "category": "类别",
                "base_date": "基期",
                "base_point": "基点",
                "list_date": "上市日期",
            }
            rows = []
            for k, v in data.items():
                if v is not None and str(v) != "nan":
                    rows.append({"项目": label_map.get(k, k), "内容": str(v)})
            if rows:
                lines.append(self._md(pd.DataFrame(rows)))

        elif res.title == "行情趋势":
            returns = data.get("returns", {})
            bench = data.get("benchmark") or {}
            bench_returns = bench.get("returns") or {}
            all_keys = [k for k in ["近5日涨幅%", "近20日涨幅%", "近60日涨幅%", "近120日涨幅%", "近250日涨幅%"] if k in returns or k in bench_returns]
            rows = []
            for k in all_keys:
                row = {"区间": k}
                row[self.index_name or "指数"] = self._fmt(returns.get(k, "N/A"))
                if bench:
                    row[bench.get("ts_code", "沪深300")] = self._fmt(bench_returns.get(k, "N/A"))
                rows.append(row)
            lines.append(self._md(pd.DataFrame(rows)))


            # 风控指标对比
            lines.append("")
            bench_col = bench.get("ts_code", "沪深300") if bench else None
            risk_rows = [
                {"指标": "年化波动率%", "指数": self._fmt(data.get("volatility"))},
                {"指标": "最大回撤%", "指数": self._fmt(data.get("max_drawdown"))},
                {"指标": "夏普比率", "指数": self._fmt(data.get("sharpe"))},
            ]
            if bench_col:
                for _i, _key in enumerate(["volatility", "max_drawdown", "sharpe"]):
                    risk_rows[_i][bench_col] = self._fmt(bench.get(_key))
            lines.append(self._md(pd.DataFrame(risk_rows)))

            # 均线
            ma = data.get("ma")
            if isinstance(ma, dict):
                lines.append("")
                ma_rows = [{"均线": k, "数值": self._fmt(v)} for k, v in ma.items()]
                lines.append(self._md(pd.DataFrame(ma_rows)))

            # 分析评价
            lines.append("")
            lines.append(self._trend_eval(data))

        elif res.title == "估值分析":
            pe_label = "PE(加权)" if data.get("source") else "PE"
            pb_label = "PB(加权)" if data.get("source") else "PB"
            table = {
                "指标": [pe_label, pb_label, "PE历史分位", "PB历史分位"],
                "数值": [self._fmt(data.get("pe")), self._fmt(data.get("pb")), self._fmt(data.get("pe_hist_percentile")), self._fmt(data.get("pb_hist_percentile"))],
            }
            lines.append(self._md(pd.DataFrame(table)))
            if data.get("source"):
                lines.append("")
                lines.append(f"*数据源：{data['source']}，非官方指数级估值*")
            lines.append("")
            lines.append(self._valuation_eval(data))

        elif res.title == "成分权重":
            weights = data.get("top_weights", [])
            if weights:
                # 列名中文化，含股票名称
                w_rows = [{"成分代码": w.get("con_code", "N/A"), "股票名称": w.get("name", ""), "权重%": self._fmt(w.get("weight"))} for w in weights]
                lines.append(self._md(pd.DataFrame(w_rows)))
            total = self._fmt(data.get("top10_total_weight"))
            lines.append("")
            weights = data.get("top_weights", [])
            top1_name = weights[0].get("name", "") if weights else ""
            top1_code = weights[0].get("con_code", "N/A") if weights else "N/A"
            top1_w = self._fmt(weights[0].get("weight")) if weights else "N/A"
            top1_desc = f"第一大权重{top1_name}（{top1_code}，{top1_w}%）" if top1_name else f"第一大权重{top1_code}（{top1_w}%）"
            lines.append(f"**分析评价**：前十大成分股权重合计 {total}%，" + ("集中度较高" if self._f(data.get("top10_total_weight")) and self._f(data.get("top10_total_weight")) > 50 else "集中度适中") + f"。{top1_desc}对指数走势影响显著。")

        elif res.title == "行业分布":
            dist = data.get("distribution", [])
            if dist:
                lines.append(self._md(pd.DataFrame(dist)))
                lines.append("")
                top3 = [d.get("行业", "") for d in dist[:3]]
                top1_pct = dist[0].get("占比%", "N/A") if dist else "N/A"
                lines.append(f"**分析评价**：前三大行业为{'、'.join(top3)}，第一大行业占比 {top1_pct}%。" + ("行业集中度较高，指数走势受少数行业影响显著。" if self._f(top1_pct) and self._f(top1_pct) > 20 else "行业分布相对分散。"))
            else:
                lines.append("- 无法获取指数成分股")

        elif res.title == "国际对比":
            comp = data.get("comparison", [])
            if comp:
                lines.append(self._md(pd.DataFrame(comp)))
                lines.append("")
                # 找出近250日表现最好和最差的
                eval_parts = []
                for c in comp:
                    name = c.get("标的", "")
                    r250 = self._f(c.get("近250日%"))
                    if r250 is not None:
                        eval_parts.append((name, r250))
                if eval_parts:
                    eval_parts.sort(key=lambda x: x[1], reverse=True)
                    best = eval_parts[0]
                    worst = eval_parts[-1]
                    idx_name = self.index_name or self.ts_code
                    idx_entry = next((x for x in eval_parts if x[0] == idx_name), None)
                    if idx_entry:
                        eval_text = ""
                        if idx_entry[1] == best[1]:
                            eval_text = f"{idx_name}近250日涨幅 {idx_entry[1]}%，在对比标的中表现最强。"
                        elif idx_entry[1] == worst[1]:
                            eval_text = f"{idx_name}近250日涨幅 {idx_entry[1]}%，在对比标中表现最弱。"
                        else:
                            eval_text = f"{idx_name}近250日涨幅 {idx_entry[1]}%，介于{best[0]}({best[1]}%)与{worst[0]}({worst[1]}%)之间。"
                        lines.append(f"**分析评价**：{eval_text}")
            else:
                lines.append("- 未配置该指数的国际对比标的")

        elif res.title == "两融/市场杠杆":
            table_rows = []
            for ex in ["sse", "szse"]:
                s = data.get(ex)
                if s:
                    table_rows.append({
                        "交易所": ex.upper(),
                        "融资余额(亿)": self._fmt(s.get("latest_rzye_billion")),
                        "融资融券余额(亿)": self._fmt(s.get("latest_rzrqye_billion")),
                        "区间变化%": self._fmt(s.get("rzye_chg_pct")),
                    })
            if table_rows:
                lines.append(self._md(pd.DataFrame(table_rows)))
            lines.append("")
            lines.append(self._margin_eval(data))

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

    def _trend_eval(self, data):
        returns = data.get("returns", {})
        ret20 = self._fmt(returns.get("近20日涨幅%"))
        ret250 = self._fmt(returns.get("近250日涨幅%"))
        vol = self._fmt(data.get("volatility"))
        mdd = self._fmt(data.get("max_drawdown"))
        ma = data.get("ma", {})
        ma_sig = ""
        if ma:
            ma5 = self._f(ma.get("MA5"))
            ma20 = self._f(ma.get("MA20"))
            ma60 = self._f(ma.get("MA60"))
            if ma5 and ma20 and ma60:
                if ma5 > ma20 > ma60:
                    ma_sig = "多头排列"
                elif ma5 < ma20 < ma60:
                    ma_sig = "空头排列"
                else:
                    ma_sig = "交织"
        parts = [f"近20日 {ret20}%，近250日 {ret250}%，年化波动 {vol}%，最大回撤 {mdd}%。"]
        if ma_sig:
            parts.append(f"均线{ma_sig}。")
        bench = data.get("benchmark") or {}
        if bench:
            bench250 = self._fmt(self._v(bench, "returns", "近250日涨幅%"))
            ret250_f = self._f(ret250)
            bench250_f = self._f(bench250)
            if ret250_f is not None and bench250_f is not None:
                parts.append(f"近250日相对沪深300 {ret250}% vs {bench250}%，{'跑赢' if ret250_f > bench250_f else '跑输'}基准。")
        return "**分析评价**：" + "".join(parts)

    def _valuation_eval(self, data):
        pe = self._f(data.get("pe"))
        pb = self._f(data.get("pb"))
        pe_hist = self._f(data.get("pe_hist_percentile"))
        pb_hist = self._f(data.get("pb_hist_percentile"))
        is_est = bool(data.get("source"))
        parts = []
        if pe_hist is not None:
            if pe_hist > 80:
                parts.append(f"PE历史分位 {pe_hist}%，估值偏高。")
            elif pe_hist < 20:
                parts.append(f"PE历史分位 {pe_hist}%，处于历史低位。")
                if pe is not None and pe > 30:
                    parts.append(f"PE绝对值 {pe} 虽不低，但相对自身历史已处于底部区间，反映成分股盈利改善快于股价上涨。")
            else:
                parts.append(f"PE历史分位 {pe_hist}%，估值中等。")
        if pb_hist is not None:
            if pb_hist > 80:
                parts.append(f"PB历史分位 {pb_hist}%，偏高。")
            elif pb_hist < 20:
                parts.append(f"PB历史分位 {pb_hist}%，偏低。")
            else:
                parts.append(f"PB历史分位 {pb_hist}%。")
        parts.append("指数估值需结合成分股盈利增速与行业景气度判断。")
        return "**分析评价**：" + "".join(parts)

    def _margin_eval(self, data):
        total_rzye = self._f(data.get("total_rzye_billion"))
        avg_chg = self._f(data.get("avg_rzye_chg_pct"))
        parts = []
        if total_rzye is not None:
            parts.append(f"两市融资余额合计 {total_rzye:.0f}亿，")
        if avg_chg is not None:
            if avg_chg > 5:
                parts.append(f"近60日上升 {avg_chg}%，杠杆资金快速入场，市场风险偏好升温，需警惕追高风险。")
            elif avg_chg < -5:
                parts.append(f"近60日下降 {abs(avg_chg)}%，杠杆资金持续撤离，市场风险偏好降温，反映资金面偏谨慎。")
            else:
                parts.append(f"近60日变化 {avg_chg}%，杠杆情绪相对平稳，市场风险偏好未出现明显转向。")
        parts.append("两融余额是市场整体杠杆水平的晴雨表，融资余额上升通常对应风险偏好提升，下降则反映去杠杆压力。")
        return "**分析评价**：" + "".join(parts)

# ---------- 便捷函数 ----------

def analyze_index(ts_code: str, end_date: Optional[str] = None, dimensions: Optional[List[str]] = None) -> Dict[str, Any]:
    runner = IndexAnalysisRunner(ts_code=ts_code, end_date=end_date, dimensions=dimensions)
    return runner.run()


def index_report(ts_code: str, end_date: Optional[str] = None, dimensions: Optional[List[str]] = None) -> str:
    runner = IndexAnalysisRunner(ts_code=ts_code, end_date=end_date, dimensions=dimensions)
    return runner.report()


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    import argparse
    parser = argparse.ArgumentParser(description="指数综合分析 Runner")
    parser.add_argument("ts_code", help="指数代码，如 000300.SH")
    parser.add_argument("--end-date", default=None, help="分析截止日期 YYYYMMDD")
    args = parser.parse_args()
    print(index_report(args.ts_code, args.end_date))
