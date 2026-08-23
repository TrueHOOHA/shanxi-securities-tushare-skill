#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
期货综合分析 Runner（以主力连续合约为标的）。

架构：
    data_api.DataAPI 取数（期货接口SDK不传fields）
    -> 各维度 analyze_* 方法返回 DimensionResult
    -> FutAnalysisRunner 汇总并渲染 markdown

用法：
    from fut_analysis_runner import fut_report
    print(fut_report("SR.ZCE"))        # 白糖主力连续
    print(fut_report("RB.SHF"))       # 螺纹钢主力连续
    print(fut_report("JM.DCE"))       # 焦煤主力连续

输入：主力连续合约 ts_code（如 SR.ZCE / RB.SHF / JM.DCE），也接受纯 symbol（SR/RB）自动匹配连续合约。
"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from basic_metrics import calc_ma, calc_max_drawdown, calc_returns, calc_sharpe, calc_volatility
from data_api import DataAPI, shift_date
from result_model import DimensionResult, ResultStatus, safe_result
from technical_indicators import calc_boll, calc_kdj, calc_macd, calc_rsi

# 交易所后缀 -> 中文名（用于持仓排名覆盖判断）
EXCHANGE_MAP = {".ZCE": ("CZCE", "郑州商品交易所"), ".SHF": ("SHFE", "上海期货交易所"),
                ".DCE": ("DCE", "大连商品交易所"), ".CFE": ("CFFEX", "中国金融期货交易所")}
# fut_holding 仅覆盖大商所
HOLDING_COVERED = {".DCE"}


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return None
        return float(value)
    except Exception:
        return None


def _resolve_ts_code(ts_code: str, api: DataAPI) -> str:
    """含后缀直接用；纯 symbol（如 SR/RB）按 fut_basic fut_type=2 匹配连续合约。"""
    if "." in ts_code:
        return ts_code
    try:
        df = api.get_fut_basic(fut_type="2")
        if df is not None and not df.empty and "symbol" in df.columns:
            m = df[df["symbol"] == ts_code.upper()]
            if not m.empty:
                return str(m.iloc[0]["ts_code"])
    except Exception:
        pass
    return ts_code


class FutAnalysisRunner:
    """期货综合分析 Runner（主力连续合约为标的）。"""

    PERIODS = (5, 20, 60, 120, 250)
    DEFAULT_DIMENSIONS = ["overview", "trend", "holding", "mapping", "wsr", "settle", "risk"]

    def __init__(self, ts_code: str, end_date: Optional[str] = None,
                 dimensions: Optional[List[str]] = None, api: Optional[DataAPI] = None):
        self.api = api or DataAPI()
        self.ts_code = _resolve_ts_code(ts_code, self.api)
        self.end_date = end_date or _today()
        self.dimensions = dimensions or self.DEFAULT_DIMENSIONS.copy()
        self.symbol = self.ts_code.split(".")[0]
        self.fut_name: Optional[str] = None
        self.exchange: Optional[str] = None
        self.main_contract: Optional[str] = None
        self.results: Dict[str, DimensionResult] = {}

    # ---------- 维度：概况 ----------
    @safe_result("概况")
    def analyze_overview(self) -> DimensionResult:
        # 交易所(从ts_code后缀推断，用于get_fut_basic筛选)
        suf = "." + self.ts_code.split(".")[-1]
        ex_pair = EXCHANGE_MAP.get(suf)
        self.exchange = ex_pair[0] if ex_pair else None
        # 连续合约信息(传exchange精确匹配，否则fut_type=2全量可能不含本品种)
        fb2 = self.api.get_fut_basic(exchange=self.exchange, fut_type="2") if self.exchange else self.api.get_fut_basic(fut_type="2")
        cont_row = None
        if fb2 is not None and not fb2.empty and "symbol" in fb2.columns:
            m = fb2[fb2["symbol"] == self.symbol]
            if not m.empty:
                cont_row = m.iloc[0]
                self.fut_name = cont_row.get("name") or self.symbol
        # 当前主力合约
        fm = self.api.get_fut_mapping(self.ts_code, shift_date(self.end_date, -60), self.end_date)
        if fm is not None and not fm.empty and "mapping_ts_code" in fm.columns:
            fm = fm.sort_values("trade_date")
            self.main_contract = fm.iloc[-1]["mapping_ts_code"]
        # 主力合约具体参数
        cont_detail = None
        if self.main_contract:
            tmp = self.api.get_fut_basic(ts_code=self.main_contract)
            if tmp is not None and not tmp.empty:
                cont_detail = tmp.iloc[0]

        rows = [
            {"项目": "品种", "内容": self.fut_name or self.symbol},
            {"项目": "产品代码", "内容": self.symbol},
            {"项目": "交易所", "内容": (ex_pair[1] if ex_pair else self.exchange or "未知") + f"（{self.exchange or '未知'}）"},
            {"项目": "主力连续合约", "内容": self.ts_code},
            {"项目": "当前主力合约", "内容": self.main_contract or "N/A"},
        ]
        if cont_detail is not None:
            for k, label in [("multiplier", "合约乘数"), ("trade_unit", "交易单位"),
                             ("min_unit", "最小变动价位"), ("quote_unit", "报价单位"),
                             ("list_date", "上市日期"), ("last_ddate", "最后交易日")]:
                v = cont_detail.get(k)
                if v is not None and str(v) != "nan":
                    rows.append({"项目": label, "内容": v})
        return DimensionResult.success("概况", data={"rows": rows, "main_contract": self.main_contract},
                                      conclusion=f"主力连续合约 {self.ts_code}（{self.fut_name or self.symbol}），当前主力 {self.main_contract or 'N/A'}。")

    # ---------- 维度：行情趋势 ----------
    @safe_result("行情趋势")
    def analyze_trend(self) -> DimensionResult:
        start = shift_date(self.end_date, -max(self.PERIODS) * 2)
        daily = self.api.get_fut_daily(self.ts_code, start, self.end_date)
        if daily is None or daily.empty:
            return DimensionResult.empty("行情趋势", note="无法获取期货日线行情")
        daily = daily.sort_values("trade_date").reset_index(drop=True)
        if "close" not in daily.columns:
            return DimensionResult.empty("行情趋势", note="日线无close字段")
        daily = daily.dropna(subset=["close"])
        s = daily.set_index("trade_date")["close"].sort_index()
        if len(s) < 2:
            return DimensionResult.insufficient_history("行情趋势", note="历史数据不足")
        h = daily.set_index("trade_date")["high"].sort_index() if "high" in daily.columns else s
        low = daily.set_index("trade_date")["low"].sort_index() if "low" in daily.columns else s
        ret = calc_returns(s, periods=self.PERIODS)
        vol = calc_volatility(s); mdd = calc_max_drawdown(s); sh = calc_sharpe(s)
        ma = calc_ma(s); macd = calc_macd(s); rsi = calc_rsi(s)
        kdj = calc_kdj(h, low, s); boll = calc_boll(s)
        latest_close = float(s.iloc[-1])
        latest_settle = _safe_float(daily.iloc[-1].get("settle")) if "settle" in daily.columns else None
        latest_oi = _safe_float(daily.iloc[-1].get("oi")) if "oi" in daily.columns else None
        data = {"returns": ret, "latest_close": latest_close, "latest_settle": latest_settle,
                "latest_oi": latest_oi, "volatility": vol, "max_drawdown": mdd, "sharpe": sh,
                "ma": ma, "macd": macd, "rsi": rsi, "kdj": kdj, "boll": boll, "daily": daily}
        conclusion = (f"最新收盘 {latest_close}，近20日 {ret.get('近20日涨幅%','N/A')}%，"
                      f"近250日 {ret.get('近250日涨幅%','N/A')}%，年化波动 {vol}%，最大回撤 {mdd}%。")
        return DimensionResult.success("行情趋势", conclusion=conclusion, data=data)

    # ---------- 维度：持仓分析 ----------
    @safe_result("持仓分析")
    def analyze_holding(self) -> DimensionResult:
        suf = "." + self.ts_code.split(".")[-1]
        covered = suf in HOLDING_COVERED
        # fut_holding 对连续合约通常无数据，尝试主力合约
        holding_df = None
        if self.main_contract:
            holding_df = self.api.get_fut_holding(self.main_contract, shift_date(self.end_date, -20), self.end_date)
        if (holding_df is None or holding_df.empty):
            note = (f"fut_holding 持仓排名接口仅覆盖大商所(DCE)，{EXCHANGE_MAP.get(suf,(None,'该交易所'))[1]}无持仓排名数据"
                    if not covered else "持仓排名数据暂缺（主力合约较新或接口未更新），已降级为持仓量(oi)序列")
            # 退而求其次：从日线取持仓量(oi)序列
            daily = self.api.get_fut_daily(self.ts_code, shift_date(self.end_date, -60), self.end_date)
            oi_series = None
            if daily is not None and not daily.empty and "oi" in daily.columns:
                d = daily.sort_values("trade_date").dropna(subset=["oi"])
                if not d.empty:
                    oi_series = d.set_index("trade_date")["oi"].tail(30)
            return DimensionResult.success("持仓分析", conclusion=note, data={"holding_records": None, "oi_series": (oi_series.tail(10).to_dict() if oi_series is not None else None)})
        holding_df = holding_df.sort_values("trade_date") if "trade_date" in holding_df.columns else holding_df
        data = {"holding_records": holding_df.tail(20).to_dict("records"), "covered_exchange": True}
        return DimensionResult.success("持仓分析", data=data,
                                       conclusion=f"持仓排名数据 {len(holding_df)} 条（{EXCHANGE_MAP.get(suf,(None,''))[1]}）。")

    # ---------- 维度：主力合约 ----------
    @safe_result("主力合约")
    def analyze_mapping(self) -> DimensionResult:
        fm = self.api.get_fut_mapping(self.ts_code, shift_date(self.end_date, -max(self.PERIODS) * 2), self.end_date)
        if fm is None or fm.empty:
            return DimensionResult.empty("主力合约", note="无法获取主力合约映射数据")
        fm = fm.sort_values("trade_date")
        changes = fm[["trade_date", "mapping_ts_code"]].drop_duplicates("mapping_ts_code", keep="last").sort_values("trade_date")
        data = {"changes": changes.tail(8).to_dict("records"), "current_main": self.main_contract}
        conclusion = f"当前主力合约 {self.main_contract}，近{len(changes)}期换月记录。基差(现货-期货)需现货价，Tushare无现货接口，暂缺。"
        return DimensionResult.success("主力合约", conclusion=conclusion, data=data)

    # ---------- 维度：仓单库存 ----------
    @safe_result("仓单库存")
    def analyze_wsr(self) -> DimensionResult:
        # 从 T-1 起算，避免循环首日 d=今天被裁剪后标签与数据日期不一致
        end_d = datetime.strptime(min(self.end_date, self.api._t_minus_1), "%Y%m%d")
        rows = []
        for i in range(0, 90, 7):
            d = (end_d - timedelta(days=i)).strftime("%Y%m%d")
            w = self.api.get_fut_wsr(d)
            if w is not None and not w.empty and "symbol" in w.columns:
                sr = w[w["symbol"] == self.symbol]
                if not sr.empty and "vol" in sr.columns:
                    rows.append({"trade_date": d, "仓库_count": int(len(sr)), "vol": int(sr["vol"].sum())})
        if not rows:
            return DimensionResult.empty("仓单库存", note="无法获取仓单数据")
        wdf = pd.DataFrame(rows).sort_values("trade_date")
        chg = int(wdf["vol"].iloc[-1]) - int(wdf["vol"].iloc[0])
        trend = "增加(可交割量上升，偏空)" if chg > 0 else "减少(可交割量下降，偏多)" if chg < 0 else "稳定"
        data = {"wsr_series": wdf.to_dict("records"), "chg": chg, "trend": trend}
        latest_vol = wdf["vol"].iloc[-1]
        conclusion = f"最新仓单 {latest_vol} 张，近{len(wdf)}期变化 {chg:+d}，{trend}。"
        return DimensionResult.success("仓单库存", conclusion=conclusion, data=data)

    # ---------- 维度：结算参数 ----------
    @safe_result("结算参数")
    def analyze_settle(self) -> DimensionResult:
        # fut_settle 对连续合约通常返回空，优先用主力合约
        code = self.main_contract or self.ts_code
        fs = self.api.get_fut_settle(code, shift_date(self.end_date, -30), self.end_date)
        if fs is not None and not fs.empty:
            if "settle" in fs.columns:
                fs = fs.sort_values("trade_date").dropna(subset=["settle"])
            data = {"settle_records": fs.tail(5).to_dict("records"), "source": "fut_settle"}
            conclusion = f"结算参数来自 fut_settle（{code}），最新 {len(fs)} 条。"
            return DimensionResult.success("结算参数", conclusion=conclusion, data=data)
        # 降级：从日线取结算价
        trend = self.results.get("trend")
        latest_settle = None
        if trend and trend.is_ok() and trend.data:
            latest_settle = trend.data.get("latest_settle")
        if latest_settle is None:
            daily = self.api.get_fut_daily(self.ts_code, shift_date(self.end_date, -10), self.end_date)
            if daily is not None and not daily.empty and "settle" in daily.columns:
                d = daily.sort_values("trade_date").dropna(subset=["settle"])
                if not d.empty:
                    latest_settle = _safe_float(d.iloc[-1]["settle"])
        data = {"latest_settle": latest_settle, "source": "fut_daily(降级)"}
        return DimensionResult.success("结算参数", data=data,
                                       conclusion=f"fut_settle 对连续/主力合约无数据，结算价取自 fut_daily：最新结算 {latest_settle if latest_settle else 'N/A'}。保证金率等参数暂缺。")

    # ---------- 维度：风险提示 ----------
    def analyze_risk(self) -> DimensionResult:
        risks = []
        trend = self.results.get("trend")
        if trend and trend.is_ok() and trend.data:
            vol = trend.data.get("volatility"); mdd = trend.data.get("max_drawdown")
            rsi = trend.data.get("rsi", {})
            if isinstance(vol, (int, float)) and vol > 30:
                risks.append(f"年化波动率 {vol}% 偏高")
            if isinstance(mdd, (int, float)) and mdd < -20:
                risks.append(f"最大回撤 {mdd}% 较深")
            rsi_v = rsi.get("RSI")
            if isinstance(rsi_v, (int, float)):
                if rsi_v > 70:
                    risks.append(f"RSI {rsi_v} 超买，短期或有回调")
                elif rsi_v < 30:
                    risks.append(f"RSI {rsi_v} 超卖，短期或有反弹")
        risks.append("期货为保证金交易，杠杆放大盈亏")
        risks.append("主力连续合约有换月跳空，技术指标可能失真")
        # 数据缺失
        notes = []
        holding = self.results.get("holding")
        if holding and holding.status != ResultStatus.SUCCESS:
            notes.append(f"持仓分析：{holding.note or '数据缺失'}")
        settle = self.results.get("settle")
        if settle and settle.is_ok() and settle.data and settle.data.get("source", "").startswith("fut_daily"):
            notes.append("结算参数：fut_settle 无连续合约数据，保证金率等暂缺")
        notes.append("主力合约：基差需现货价，Tushare无现货接口")
        if not risks:
            risks.append("未发现显著风险信号（基于已有维度）")
        return DimensionResult.success("风险提示", data={"risks": risks, "notes": notes}, risks=risks)

    # ---------- 执行入口 ----------
    def run(self) -> Dict[str, Any]:
        self.results = {}
        if "overview" in self.dimensions:
            self.results["overview"] = self.analyze_overview()
        if "trend" in self.dimensions:
            self.results["trend"] = self.analyze_trend()  # trend 先跑，供 settle 降级复用
        parallel_dims = [d for d in self.dimensions if d not in ("overview", "trend", "risk")]
        dim_methods = {"holding": self.analyze_holding, "mapping": self.analyze_mapping,
                       "wsr": self.analyze_wsr, "settle": self.analyze_settle}
        to_run = {d: dim_methods[d] for d in parallel_dims if d in dim_methods}
        if to_run:
            max_workers = min(4, len(to_run))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(method): d for d, method in to_run.items()}
                for future in as_completed(futures):
                    d = futures[future]
                    self.results[d] = future.result()
        if "risk" in self.dimensions:
            self.results["risk"] = self.analyze_risk()
        return {"ts_code": self.ts_code, "name": self.fut_name, "symbol": self.symbol,
                "end_date": self.end_date, "dimensions": {k: v.to_dict() for k, v in self.results.items()}}

    # ---------- 报告渲染 ----------
    DIM_TITLES = {"overview": "概况", "trend": "行情趋势", "holding": "持仓分析",
                  "mapping": "主力合约", "wsr": "仓单库存", "settle": "结算参数", "risk": "风险提示"}

    def _fmt(self, x, digits=2):
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
        if x is None or x == "N/A":
            return None
        try:
            v = float(x)
            return None if (v != v or v == float("inf") or v == float("-inf")) else v
        except Exception:
            return None

    def _md(self, df):
        if df is None or df.empty:
            return ""
        df = df.copy().dropna(axis=1, how="all")
        for col in df.columns:
            if df[col].dtype.kind in "iufc":
                df[col] = df[col].apply(lambda x: self._fmt(x))
            elif df[col].dtype == object:
                df[col] = df[col].apply(lambda x: self._fmt(x) if isinstance(x, (int, float, np.integer, np.floating))
                                        else ("N/A" if x is None or (isinstance(x, float) and x != x) else str(x)))
        try:
            return df.to_markdown(index=False, disable_numparse=True)
        except Exception:
            return df.to_string(index=False)

    def report(self) -> str:
        if not self.results:
            self.run()
        name = self.fut_name or self.symbol
        lines = [f"# {name} 全景研究报告", "", f"> 数据日期：{self.end_date}（Tushare 数据为 T-1 日）", ""]
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
            lines.append("")
        idx += 1
        lines.append(f"## {idx}. 整体分析评价")
        lines.append(self._overall_evaluation())
        lines.append("")
        lines.append("---")
        lines.append("*本报告由AI基于山西证券Tushare平台数据自动生成，基于 T-1 日历史数据，仅供技术交流与学习参考，不构成任何投资建议或财务指导。*")
        return "\n".join(lines)

    def _render_dimension(self, res) -> list:
        lines = []
        data = res.data or {}
        if res.title == "概况":
            rows = data.get("rows", [])
            if rows:
                lines.append(self._md(pd.DataFrame(rows)))
        elif res.title == "行情趋势":
            ret = data.get("returns", {})
            ret_rows = [{"区间": k, "涨幅%": v} for k, v in ret.items() if k.startswith("近")]
            if ret_rows:
                lines.append(self._md(pd.DataFrame(ret_rows)))
            lines.append("")
            lines.append(self._md(pd.DataFrame([
                {"指标": "最新收盘", "数值": self._fmt(data.get("latest_close"))},
                {"指标": "最新结算价", "数值": self._fmt(data.get("latest_settle"))},
                {"指标": "最新持仓量(手)", "数值": self._fmt(data.get("latest_oi"))},
                {"指标": "年化波动率%", "数值": self._fmt(data.get("volatility"))},
                {"指标": "最大回撤%", "数值": self._fmt(data.get("max_drawdown"))},
                {"指标": "夏普比率", "数值": self._fmt(data.get("sharpe"))},
            ])))
            ma = data.get("ma", {})
            tech = [{"技术指标": "MACD", "信号": (data.get("macd") or {}).get("signal", "N/A")},
                    {"技术指标": "RSI", "信号": (data.get("rsi") or {}).get("RSI", "N/A")},
                    {"技术指标": "KDJ", "信号": (data.get("kdj") or {}).get("signal", "N/A")},
                    {"技术指标": "布林带", "信号": (data.get("boll") or {}).get("position", "N/A")}]
            if ma:
                tech.append({"技术指标": "均线", "信号": f"MA5={self._fmt(ma.get('MA5'))},MA20={self._fmt(ma.get('MA20'))},MA60={self._fmt(ma.get('MA60'))}"})
            lines.append("")
            lines.append(self._md(pd.DataFrame(tech)))
            lines.append("")
            lines.append(f"**分析评价**：主力连续合约拼接各时期主力，注意换月跳空对技术指标的影响。RSI超买(>70)/超卖(<30)为短期信号，需结合趋势判断。")
        elif res.title == "持仓分析":
            if res.status == ResultStatus.SUCCESS and data.get("holding_records"):
                lines.append(self._md(pd.DataFrame(data["holding_records"]).head(10)))
                lines.append("")
                lines.append("**分析评价**：持仓排名反映多空主力结构，前20会员多空比可判断机构倾向。")
            else:
                oi = data.get("oi_series")
                if oi:
                    lines.append(self._md(pd.DataFrame([{"trade_date": k, "持仓量(手)": int(v)} for k, v in oi.items()])))
                    lines.append("")
                lines.append("**分析评价**：持仓量(oi)变化反映资金进出，与价格同向为趋势健康，背离需警惕。")
        elif res.title == "主力合约":
            ch = data.get("changes", [])
            if ch:
                lines.append(self._md(pd.DataFrame(ch)))
                lines.append("")
            lines.append("**分析评价**：主力合约为当前成交量最大的具体合约，定期换月。基差(现货-期货)反映期现结构，正基差(现货>期货)偏多头，负基差偏空头，本数据源无现货价暂缺。")
        elif res.title == "仓单库存":
            ws = data.get("wsr_series", [])
            if ws:
                lines.append(self._md(pd.DataFrame(ws)))
                lines.append("")
            lines.append(f"**分析评价**：仓单反映可交割量，{data.get('trend','')}。仓单增加压制价格(偏空)，减少支撑价格(偏多)。")
        elif res.title == "结算参数":
            src = data.get("source", "")
            if src.startswith("fut_settle"):
                rec = data.get("settle_records", [])
                if rec:
                    lines.append(self._md(pd.DataFrame(rec)))
                    lines.append("")
            lines.append("**分析评价**：结算价是每日无负债结算基准，影响保证金占用与盈亏。保证金率需交易所公告，连续合约暂以日线结算价近似。")
        return [l for l in lines if l is not None]

    def _overall_evaluation(self) -> str:
        trend = self.results.get("trend")
        wsr = self.results.get("wsr")
        t_ok = trend and trend.is_ok() and trend.data
        w_ok = wsr and wsr.is_ok() and wsr.data
        tags = []
        if t_ok:
            vol = self._f(trend.data.get("volatility")); mdd = self._f(trend.data.get("max_drawdown"))
            r250 = self._f(trend.data.get("returns", {}).get("近250日涨幅%"))
            rsi_v = self._f((trend.data.get("rsi") or {}).get("RSI"))
            if vol and vol > 30:
                tags.append("高波动")
            if mdd and mdd < -25:
                tags.append("回撤较深")
            if r250 and r250 > 20:
                tags.append("中期上行")
            elif r250 and r250 < -10:
                tags.append("中期下行")
            if rsi_v and rsi_v > 70:
                tags.append("短期超买")
            elif rsi_v and rsi_v < 30:
                tags.append("短期超卖")
        if w_ok:
            chg = wsr.data.get("chg")
            if isinstance(chg, (int, float)) and chg < 0:
                tags.append("仓单减少偏多")
            elif isinstance(chg, (int, float)) and chg > 0:
                tags.append("仓单增加偏空")
        name = self.fut_name or self.symbol
        lines = [f"**综合判断**：{name}当前具备{'、'.join(tags) if tags else '中性'}特征。"]
        pts = []
        if t_ok:
            ret = trend.data.get("returns", {})
            pts.append(f"- **趋势**：近5日 {ret.get('近5日涨幅%','N/A')}%，近20日 {ret.get('近20日涨幅%','N/A')}%，近250日 {ret.get('近250日涨幅%','N/A')}%")
            pts.append(f"- **波动**：年化波动 {self._fmt(trend.data.get('volatility'))}%，最大回撤 {self._fmt(trend.data.get('max_drawdown'))}%，夏普 {self._fmt(trend.data.get('sharpe'))}")
            pts.append(f"- **技术**：MACD {(trend.data.get('macd') or {}).get('signal','N/A')}，RSI {self._fmt((trend.data.get('rsi') or {}).get('RSI'))}，KDJ {(trend.data.get('kdj') or {}).get('signal','N/A')}")
        if w_ok:
            _ws = wsr.data.get("wsr_series") or []
            _latest_ws_vol = _ws[-1].get("vol", "N/A") if _ws else "N/A"
            pts.append(f"- **仓单**：最新 {_latest_ws_vol} 张，{wsr.data.get('trend','')}")
        pts.append("- **数据缺失**：持仓排名(非DCE交易所)、基差(无现货)、fut_settle连续合约")
        lines.extend(pts)
        lines.append("")
        lines.append(f"**风格定位**：{name}属期货品种，受供需、季节性、宏观与政策等多因素影响，杠杆交易放大波动。")
        r250 = self._f(trend.data.get("returns", {}).get("近250日涨幅%")) if t_ok else None
        vol = self._f(trend.data.get("volatility")) if t_ok else None
        concl = f"{name}{'波动较高' if vol and vol>30 else '波动适中'}，{'中期走势偏弱' if r250 and r250<0 else '中期走势平稳'}。期货分析需结合现货供需、仓单与持仓结构，本报告仅基于Tushare行情与仓单数据，仅供技术参考，不构成投资建议。"
        lines.append(f"**结论**：{concl}")
        return "\n".join(lines)


# ---------- 便捷函数 ----------
def analyze_futures(ts_code: str, end_date: Optional[str] = None,
                    dimensions: Optional[List[str]] = None) -> Dict[str, Any]:
    runner = FutAnalysisRunner(ts_code=ts_code, end_date=end_date, dimensions=dimensions)
    return runner.run()


def fut_report(ts_code: str, end_date: Optional[str] = None,
               dimensions: Optional[List[str]] = None) -> str:
    runner = FutAnalysisRunner(ts_code=ts_code, end_date=end_date, dimensions=dimensions)
    return runner.report()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    import argparse
    parser = argparse.ArgumentParser(description="期货综合分析 Runner（主力连续合约）")
    parser.add_argument("ts_code", help="主力连续合约代码，如 SR.ZCE / RB.SHF / JM.DCE，或纯symbol如 SR")
    parser.add_argument("--end-date", default=None, help="分析截止日期 YYYYMMDD")
    parser.add_argument(
        "--output",
        default=None,
        help="markdown 报告输出路径，默认保存到当前目录 {ts_code}_report.md",
    )
    args = parser.parse_args()
    report = fut_report(args.ts_code, args.end_date)
    output = args.output or f"{args.ts_code}_report.md"
    with open(output, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"报告已保存: {os.path.abspath(output)}")
    print(report)
