#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一分析 Runner（股票版）。

架构：
    data_api.DataAPI 取数
    -> 各维度 analyze_* 方法返回 DimensionResult
    -> StockAnalysisRunner 汇总并渲染 markdown

用法：
    from analysis_runner import StockAnalysisRunner
    runner = StockAnalysisRunner("600519.SH")
    result = runner.run()          # 结构化结果
    print(runner.report())         # markdown 报告
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from adjustment import apply_adj_factor, calc_percentile_rank, valuation_percentiles, winsorize_cross_section
from attribution import calc_beta_alpha, calc_piotroski_fscore
from basic_metrics import (
    calc_cagr, calc_information_ratio, calc_ma, calc_max_drawdown,
    calc_returns, calc_sharpe, calc_sortino, calc_volatility,
)
from data_api import DataAPI, shift_date
from result_model import DimensionResult, ResultStatus, safe_result
from risk_modeling import (
    calc_amihud_illiquidity,
    calc_relative_strength,
    calc_rolling_beta,
    calc_rolling_sharpe,
    calc_tail_risk,
    calc_var_cvar,
)
from technical_indicators import calc_boll, calc_kdj, calc_macd, calc_rsi, calc_volume_ratio


# ---------- 工具函数 ----------

def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return None
        return float(value)
    except Exception:
        return None


def _fmt_billions(value: Optional[float]) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "N/A"
    return f"{value / 1e4:,.2f}亿"


def _resolve_ts_code(ts_code: str) -> str:
    if "." in ts_code:
        return ts_code
    if ts_code.startswith(("600", "601", "603", "605", "688")):
        return f"{ts_code}.SH"
    if ts_code.startswith(("000", "001", "002", "003", "300", "301")):
        return f"{ts_code}.SZ"
    if ts_code.startswith(("8", "92")):
        return f"{ts_code}.BJ"
    return f"{ts_code}.SH"


# ---------- 股票分析 Runner ----------

class StockAnalysisRunner:
    """股票综合分析 Runner。"""

    PERIODS = (5, 20, 60, 120, 250)
    TRADING_DAYS_PER_YEAR = 250
    DEFAULT_DIMENSIONS = ["overview", "trend", "valuation", "financial", "moneyflow", "shareholder", "float", "margin", "market_activity", "macro", "risk"]

    def __init__(
        self,
        ts_code: str,
        end_date: Optional[str] = None,
        dimensions: Optional[List[str]] = None,
        api: Optional[DataAPI] = None,
    ):
        self.ts_code = _resolve_ts_code(ts_code)
        self.end_date = end_date or _today()
        self.dimensions = dimensions or self.DEFAULT_DIMENSIONS.copy()
        self.api = api or DataAPI()

        self.stock_name: Optional[str] = None
        self.industry: Optional[str] = None
        self.results: Dict[str, DimensionResult] = {}

    # ---------- 维度：概况 ----------

    @safe_result("概况")
    def analyze_overview(self) -> DimensionResult:
        df = self.api.get_stock_basic(self.ts_code)
        if df is None or df.empty:
            return DimensionResult.empty("概况", note="无法获取标的基础信息")

        row = df.iloc[0]
        self.stock_name = row.get("name")
        self.industry = row.get("industry")

        data = {
            "ts_code": self.ts_code,
            "name": self.stock_name,
            "industry": self.industry,
            "area": row.get("area"),
            "list_date": row.get("list_date"),
            "exchange": row.get("exchange"),
            "list_status": row.get("list_status"),
        }
        # 补充公司详情：员工数、主营业务、注册资本
        try:
            company = self.api.get_stock_company(self.ts_code)
            if company is not None and not company.empty:
                crow = company.iloc[0]
                data["employees"] = crow.get("employees")
                data["main_business"] = crow.get("main_business")
                data["reg_capital"] = crow.get("reg_capital")
                data["province"] = crow.get("province")
                data["city"] = crow.get("city")
        except Exception:
            pass
        return DimensionResult.success("概况", data=data)

    # ---------- 维度：行情趋势 ----------

    @safe_result("行情趋势")
    def analyze_trend(self) -> DimensionResult:
        start = shift_date(self.end_date, -self.PERIODS[-1] * 2)

        df_daily = self.api.get_daily(self.ts_code, start, self.end_date)
        if df_daily is None:
            return DimensionResult.empty("行情趋势", note="无法获取日线行情")

        df_adj = self.api.get_adj_factor(self.ts_code, start, self.end_date)
        if df_adj is not None:
            df = apply_adj_factor(df_daily, df_adj)
            price_series = df["close_post"].sort_index()
            high_series = df["high_post"].sort_index()
            low_series = df["low_post"].sort_index()
            latest_unadj = _safe_float(df_daily.sort_values("trade_date").iloc[-1]["close"])
        else:
            df = df_daily.sort_values("trade_date").reset_index(drop=True).set_index("trade_date")
            price_series = df["close"].sort_index()
            high_series = df["high"].sort_index()
            low_series = df["low"].sort_index()
            latest_unadj = _safe_float(price_series.iloc[-1])

        if len(price_series) < 2:
            return DimensionResult.insufficient_history("行情趋势", note="历史数据不足")

        returns = calc_returns(price_series, periods=self.PERIODS)
        ma = calc_ma(price_series)
        volatility = calc_volatility(price_series)
        max_dd = calc_max_drawdown(price_series)
        sharpe = calc_sharpe(price_series)
        macd = calc_macd(price_series)
        rsi = calc_rsi(price_series)
        kdj = calc_kdj(high_series, low_series, price_series)
        boll = calc_boll(price_series)

        # 量比（使用原始成交量）
        vol_series = df_daily.sort_values("trade_date").set_index("trade_date")["vol"].sort_index()
        volume_ratio = calc_volume_ratio(vol_series)

        # 阶段高/低
        stage_high = round(float(price_series.tail(250).max()), 2) if len(price_series) >= 20 else None
        stage_low = round(float(price_series.tail(250).min()), 2) if len(price_series) >= 20 else None

        # 基准对比 + Beta/Alpha
        bench_code = "000300.SH"
        bench_returns = None
        beta_alpha = None
        info_ratio = None
        rolling_beta = None
        relative_strength = None

        # 仅依赖标的自身序列的进阶指标：不受基准取数成败影响
        stock_ret = price_series.pct_change().dropna()
        sortino = calc_sortino(price_series)
        cagr = calc_cagr(price_series.iloc[0], price_series.iloc[-1], len(price_series) - 1)
        rolling_sharpe = calc_rolling_sharpe(price_series)
        tail_risk = calc_tail_risk(stock_ret) if len(stock_ret) >= 10 else None
        var_cvar = calc_var_cvar(stock_ret) if len(stock_ret) >= 30 else None

        df_bench = self.api.get_index_daily(bench_code, start, self.end_date)
        if df_bench is not None and not df_bench.empty:
            bench_series = df_bench.set_index("trade_date")["close"].sort_index()
            bench_returns = calc_returns(bench_series, periods=self.PERIODS)
            bench_volatility = calc_volatility(bench_series)
            bench_max_dd = calc_max_drawdown(bench_series)
            bench_sharpe = calc_sharpe(bench_series)
            market_ret = bench_series.pct_change().dropna()
            aligned = pd.DataFrame({"stock": stock_ret, "market": market_ret}).dropna()
            if len(aligned) >= 30:
                beta_alpha = calc_beta_alpha(aligned["stock"], aligned["market"])
                info_ratio = calc_information_ratio(aligned["stock"], aligned["market"])
                rolling_beta = calc_rolling_beta(aligned["stock"], aligned["market"])
                relative_strength = calc_relative_strength(aligned["stock"], aligned["market"])

        ret_20 = returns.get("近20日涨幅%", "N/A")
        bench_20 = bench_returns.get("近20日涨幅%", "N/A") if bench_returns else "N/A"
        bench_text = ""
        if bench_returns:
            bench_text = f"，相对沪深300（{bench_20}%）"
            try:
                bench_text += "跑赢" if float(ret_20) > float(bench_20) else "跑输"
            except Exception:
                bench_text += "——"

        conclusion = (
            f"近20日涨幅 {ret_20}%{bench_text}；"
            f"年化波动 {volatility}%，最大回撤 {max_dd}%；RSI {rsi.get('RSI', 'N/A')}，"
            f"KDJ {kdj.get('signal', 'N/A')}，量比 {volume_ratio.get('量比', 'N/A') if volume_ratio else 'N/A'}。"
        )

        data = {
            "returns": returns,
            "latest_close_unadj": latest_unadj,
            "latest_close_adj": _safe_float(price_series.iloc[-1]),
            "ma": ma,
            "volatility": volatility,
            "max_drawdown": max_dd,
            "sharpe": sharpe,
            "macd": macd,
            "rsi": rsi,
            "kdj": kdj,
            "boll": boll,
            "volume_ratio": volume_ratio,
            "stage_high_250d": stage_high,
            "stage_low_250d": stage_low,
            "benchmark": {"ts_code": bench_code, "returns": bench_returns, "volatility": bench_volatility, "max_drawdown": bench_max_dd, "sharpe": bench_sharpe} if bench_returns else None,
            "beta_alpha": beta_alpha,
            "sortino": sortino,
            "information_ratio": info_ratio,
            "cagr": cagr,
            "rolling_beta": rolling_beta,
            "rolling_sharpe": rolling_sharpe,
            "relative_strength": relative_strength,
            "tail_risk": tail_risk,
            "amihud": self._calc_amihud(price_series, df_daily) if df_daily is not None else None,
            "var_cvar": var_cvar,
        }

        return DimensionResult.success("行情趋势", conclusion=conclusion, data=data)

    # ---------- 维度：估值 ----------

    @safe_result("估值分析")
    def analyze_valuation(self) -> DimensionResult:
        # 取近 5 年估值序列（~1250 自然日），不足时按实际返回量降级标注
        start = shift_date(self.end_date, -250 * 5)
        df = self.api.get_daily_basic(self.ts_code, start, self.end_date)
        if df is None or df.empty:
            return DimensionResult.empty("估值分析", note="无法获取估值数据")

        df = df.sort_values("trade_date").reset_index(drop=True)
        latest = df.iloc[-1]
        hist_count = len(df)

        pe = round(_safe_float(latest.get("pe_ttm")) or _safe_float(latest.get("pe")), 2)
        pb = round(_safe_float(latest.get("pb")), 2)
        ps = round(_safe_float(latest.get("ps_ttm")) or _safe_float(latest.get("ps")), 2)
        dv = round(_safe_float(latest.get("dv_ratio")), 2)
        total_mv = _safe_float(latest.get("total_mv"))

        # 历史分位需 ≥250 日数据才统计可靠；次新股等不足则置 None 并标注
        pe_hist = None
        pb_hist = None
        if hist_count >= 250:
            if pe is not None and "pe_ttm" in df.columns:
                pe_hist = calc_percentile_rank(pe, df["pe_ttm"].dropna())
            if pb is not None:
                pb_hist = calc_percentile_rank(pb, df["pb"].dropna())
        hist_note = f"（数据不足，仅 {hist_count} 日）" if hist_count < 250 else ""

        # 行业截面估值对比
        industry_val = self._calc_industry_valuation(pe, pb)

        data = {
            "pe_ttm": pe,
            "pb": pb,
            "ps_ttm": ps,
            "dividend_yield": dv,
            "total_mv_billion": total_mv / 1e4 if total_mv else None,
            "pe_hist_percentile": pe_hist,
            "pb_hist_percentile": pb_hist,
            "hist_sample_days": hist_count,
            "industry": industry_val,
        }

        conclusion = f"PE(TTM) {pe if pe is not None else 'N/A'}，PB {pb if pb is not None else 'N/A'}，总市值 {_fmt_billions(total_mv)}。"
        if pe_hist is not None:
            conclusion += f"PE 近5年历史分位 {pe_hist}%。"
        elif pe is not None and hist_count < 250:
            conclusion += f"PE 近5年历史分位 N/A{hist_note}。"
        if pb_hist is not None:
            conclusion += f"PB 近5年历史分位 {pb_hist}%。"
        elif pb is not None and hist_count < 250:
            conclusion += f"PB 近5年历史分位 N/A{hist_note}。"
        if industry_val:
            conclusion += (
                f" 同行业({industry_val.get('industry_name', self.industry)})PE均值 {industry_val.get('pe_mean', 'N/A')}"
                f"，中位数 {industry_val.get('pe_median', 'N/A')}"
                f"，本股市值加权行业分位 {industry_val.get('pe_percentile', 'N/A')}%"
            )

        return DimensionResult.success("估值分析", conclusion=conclusion, data=data)


    def _calc_amihud(self, price_series: pd.Series, df_daily: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """计算 Amihud 非流动性指标。"""
        try:
            df = df_daily.sort_values("trade_date").set_index("trade_date")
            df = df.reindex(price_series.index)
            if df.empty or "amount" not in df.columns:
                return None
            returns = price_series.pct_change().dropna().abs()
            # daily.amount 单位为千元，转为元
            dollar_volume = df["amount"] * 1000
            dollar_volume = dollar_volume.reindex(returns.index)
            aligned = pd.DataFrame({"ret": returns, "vol": dollar_volume}).dropna()
            if aligned.empty:
                return None
            return calc_amihud_illiquidity(aligned["ret"], aligned["vol"])
        except Exception:
            return None

    def _calc_industry_valuation(self, own_pe: Optional[float], own_pb: Optional[float]) -> Optional[Dict[str, Any]]:
        """计算同行业（申万三级行业成分股）截面估值统计。"""
        if not self.industry:
            return None
        try:
            # 1. 获取申万三级行业列表并匹配名称
            l3_df = self.api.get_index_classify(level="L3", src="SW2021")
            if l3_df is None or l3_df.empty:
                return None

            l3_df["match_name"] = l3_df["industry_name"].astype(str).str.replace("Ⅲ", "").str.replace("Ⅱ", "").str.strip()
            matched = l3_df[l3_df["match_name"] == self.industry]
            if matched.empty:
                # 降级：包含匹配，可能命中多个子行业
                matched = l3_df[l3_df["match_name"].str.contains(self.industry, na=False)]
            if matched.empty:
                return None
            # 若命中多个，通过成分股包含本标的来精确确定子行业
            if len(matched) > 1:
                for _, mrow in matched.iterrows():
                    try:
                        m_members = self.api.get_index_member(mrow["index_code"])
                        if m_members is not None and not m_members.empty:
                            m_active = m_members[m_members["out_date"].isna() | (m_members["out_date"] == "")]
                            if self.ts_code in m_active["con_code"].tolist():
                                matched = matched[matched["index_code"] == mrow["index_code"]]
                                break
                    except Exception:
                        continue
            matched_code = matched.iloc[0]["index_code"]
            industry_name = matched.iloc[0]["industry_name"]

            # 2. 获取该行业当前成分股
            members = self.api.get_index_member(matched_code)
            if members is None or members.empty:
                return None
            active = members[members["out_date"].isna() | (members["out_date"] == "")]
            peer_codes = active["con_code"].tolist()[:50]
            if not peer_codes:
                return None

            start = shift_date(self.end_date, -10)

            def _get_peer_metrics(code: str) -> Optional[Dict[str, Any]]:
                try:
                    df = self.api.get_daily_basic(code, start, self.end_date)
                    if df is None or df.empty:
                        return None
                    latest = df.sort_values("trade_date").iloc[-1]
                    return {
                        "ts_code": code,
                        "pe_ttm": _safe_float(latest.get("pe_ttm")),
                        "pb": _safe_float(latest.get("pb")),
                    }
                except Exception:
                    return None

            rows = []
            with ThreadPoolExecutor(max_workers=5) as executor:
                rows = [r for r in executor.map(_get_peer_metrics, peer_codes) if r is not None]

            if not rows:
                return None

            peer_df = pd.DataFrame(rows)
            pe_series = peer_df["pe_ttm"].dropna()
            pb_series = peer_df["pb"].dropna()
            if len(pe_series) < 5 or len(pb_series) < 5:
                return None

            pe_clean = winsorize_cross_section(pe_series)
            pb_clean = winsorize_cross_section(pb_series)

            return {
                "industry_name": industry_name,
                "sample_size": int(len(pe_clean)),
                "pe_mean": round(float(pe_clean.mean()), 2),
                "pe_median": round(float(pe_clean.median()), 2),
                "pb_mean": round(float(pb_clean.mean()), 2),
                "pb_median": round(float(pb_clean.median()), 2),
                "pe_percentile": round(float(calc_percentile_rank(own_pe, pe_clean)), 1) if own_pe is not None else None,
                "pb_percentile": round(float(calc_percentile_rank(own_pb, pb_clean)), 1) if own_pb is not None else None,
            }
        except Exception:
            return None

    # ---------- 维度：财务质量 ----------

    @safe_result("财务质量")
    def analyze_financial(self) -> DimensionResult:
        df = self.api.get_fina_indicator(self.ts_code, self.end_date)
        if df is None or df.empty:
            return DimensionResult.empty("财务质量", note="无法获取财务指标")

        df = df.sort_values(["end_date", "ann_date"]).drop_duplicates("end_date", keep="last")
        latest = df.iloc[-1]

        trend = df[["end_date", "roe", "grossprofit_margin", "netprofit_margin", "debt_to_assets"]].tail(8)

        forecast_info = None
        forecast_df = self.api.get_forecast(self.ts_code, self.end_date)
        if forecast_df is not None and not forecast_df.empty:
            fc = forecast_df.sort_values("ann_date").iloc[-1]
            forecast_info = {
                "end_date": fc.get("end_date"),
                "type": fc.get("type"),
                "p_change_min": _safe_float(fc.get("p_change_min")),
                "p_change_max": _safe_float(fc.get("p_change_max")),
            }

        # F-Score 需要三张表
        fscore = None
        df_income = self.api.get_income(self.ts_code, self.end_date)
        df_cashflow = self.api.get_cashflow(self.ts_code, self.end_date)
        df_balance = self.api.get_balancesheet(self.ts_code, self.end_date)
        if df_income is not None and not df_income.empty and df_cashflow is not None and not df_cashflow.empty:
            fscore = calc_piotroski_fscore(df, df_income, df_cashflow, df_balance)

        # 营收/利润增速（最新报告期 vs 上年同期）
        growth = self._calc_revenue_profit_growth(df_income)

        data = {
            "latest": {
                "end_date": latest.get("end_date"),
                "roe": _safe_float(latest.get("roe")),
                "grossprofit_margin": _safe_float(latest.get("grossprofit_margin")),
                "netprofit_margin": _safe_float(latest.get("netprofit_margin")),
                "debt_to_assets": _safe_float(latest.get("debt_to_assets")),
                "current_ratio": _safe_float(latest.get("current_ratio")),
            },
            "trend": trend.to_dict("records"),
            "forecast": forecast_info,
            "fscore": fscore,
            "growth": growth,
        }

        roe = data["latest"]["roe"]
        gm = data["latest"]["grossprofit_margin"]
        debt = data["latest"]["debt_to_assets"]
        conclusion = (
            f"最新报告期 {latest.get('end_date')}："
            f"ROE {roe if roe is not None else 'N/A'}%，"
            f"毛利率 {gm if gm is not None else 'N/A'}%，"
            f"资产负债率 {debt if debt is not None else 'N/A'}%。"
        )
        if fscore:
            conclusion += f" Piotroski F-Score {fscore.get('F-Score', 'N/A')}（{fscore.get('rating', '')}）。"
        if growth:
            conclusion += (
                f" 营收YoY {growth.get('revenue_yoy', 'N/A')}%，"
                f"净利润YoY {growth.get('profit_yoy', 'N/A')}%。"
            )
        if forecast_info:
            conclusion += (
                f"业绩预告：{forecast_info['type']}，"
                f"净利润变动 {forecast_info['p_change_min']}%~{forecast_info['p_change_max']}%。"
            )

        return DimensionResult.success("财务质量", conclusion=conclusion, data=data)

    def _calc_revenue_profit_growth(self, df_income: Optional[pd.DataFrame]) -> Optional[Dict[str, Any]]:
        """基于利润表计算最新报告期营收与净利润同比增速。"""
        if df_income is None or df_income.empty:
            return None
        try:
            df = df_income.sort_values(["end_date", "ann_date"]).drop_duplicates("end_date", keep="last").copy()
            df["total_revenue"] = pd.to_numeric(df.get("total_revenue"), errors="coerce")
            df["n_income_attr_p"] = pd.to_numeric(df.get("n_income_attr_p"), errors="coerce")
            df = df.dropna(subset=["total_revenue", "n_income_attr_p"])
            if len(df) < 2:
                return None

            latest = df.iloc[-1]
            latest_end = str(latest["end_date"])
            # 上年同期：同年份-1
            yoy_end = str(int(latest_end[:4]) - 1) + latest_end[4:]
            yoy_row = df[df["end_date"] == yoy_end]
            if yoy_row.empty:
                return None

            yoy = yoy_row.iloc[0]
            revenue_yoy = round((latest["total_revenue"] / yoy["total_revenue"] - 1) * 100, 2) if yoy["total_revenue"] else None
            profit_yoy = round((latest["n_income_attr_p"] / yoy["n_income_attr_p"] - 1) * 100, 2) if yoy["n_income_attr_p"] else None
            return {
                "latest_end_date": latest_end,
                "yoy_end_date": yoy_end,
                "revenue_yoy": revenue_yoy,
                "profit_yoy": profit_yoy,
            }
        except Exception:
            return None

    # ---------- 维度：资金面 ----------

    @safe_result("资金面")
    def analyze_moneyflow(self) -> DimensionResult:
        df = self.api.get_moneyflow(self.ts_code, self.end_date)
        if df is None or df.empty:
            return DimensionResult.empty("资金面", note="无法获取资金流向")

        df = df.sort_values("trade_date").reset_index(drop=True)
        latest = df.iloc[-1]

        net_5 = round(df.tail(5)["net_mf_amount"].sum() / 1e4, 2) if len(df) >= 5 else None
        net_20 = round(df.tail(20)["net_mf_amount"].sum() / 1e4, 2) if len(df) >= 20 else None

        df["elg_net"] = df["buy_elg_amount"] - df["sell_elg_amount"]
        elg_5 = round(df.tail(5)["elg_net"].sum() / 1e4, 2) if len(df) >= 5 else None
        elg_20 = round(df.tail(20)["elg_net"].sum() / 1e4, 2) if len(df) >= 20 else None

        # 大宗交易
        block_df = self.api.get_block_trade(self.ts_code, self.end_date)
        block_summary = None
        if block_df is not None and not block_df.empty:
            block_df = block_df.sort_values("trade_date").reset_index(drop=True)
            block_summary = {
                "count": len(block_df),
                "total_vol": round(block_df["vol"].sum(), 2),
                "total_amount": round(block_df["amount"].sum(), 2),
                "avg_price": round(block_df["price"].mean(), 2),
                "latest": block_df.iloc[-1].to_dict(),
            }

        data = {
            "latest_net_mf": _safe_float(latest.get("net_mf_amount")),
            "net_inflow_5d_billion": net_5,
            "net_inflow_20d_billion": net_20,
            "elg_net_5d_billion": elg_5,
            "elg_net_20d_billion": elg_20,
            "block_trade": block_summary,
        }

        conclusion = f"近5日主力净流入 {net_5:.2f}亿" if net_5 is not None else "近5日主力净流入 N/A"
        conclusion += f"，近20日主力净流入 {net_20:.2f}亿" if net_20 is not None else "，近20日主力净流入 N/A"
        conclusion += f"；超大单近5日净流入 {elg_5:.2f}亿。" if elg_5 is not None else "。"
        if block_summary:
            conclusion += (
                f" 近60日大宗交易 {block_summary['count']} 笔，"
                f"合计 {block_summary['total_amount']} 万元，"
                f"均价 {block_summary['avg_price']} 元。"
            )

        hsgt_df = self.api.get_hsgt_money(self.end_date)
        if hsgt_df is not None and not hsgt_df.empty:
            hsgt_df = hsgt_df.sort_values("trade_date").reset_index(drop=True)
            data["north_inflow_20d_million"] = round(hsgt_df.tail(20)["north_money"].sum(), 2)

        return DimensionResult.success("资金面", conclusion=conclusion, data=data)

    # ---------- 维度：股东筹码（可选） ----------

    @safe_result("股东筹码")
    def analyze_shareholder(self) -> DimensionResult:
        df = self.api.get_stk_holdernumber(self.ts_code, self.end_date)
        if df is None or df.empty:
            return DimensionResult.empty("股东筹码", note="无法获取股东户数数据")

        df = df.sort_values("end_date").dropna(subset=["holder_num"])
        if len(df) < 2:
            return DimensionResult.insufficient_history("股东筹码", note="股东户数历史数据不足")

        latest = int(df["holder_num"].iloc[-1])
        changes = df["holder_num"].pct_change() * 100
        total_chg = round((df["holder_num"].iloc[-1] / df["holder_num"].iloc[0] - 1) * 100, 1)
        latest_chg = round(changes.iloc[-1], 1) if len(changes) > 0 else 0

        if latest_chg < -3:
            signal = "筹码趋于集中（户数下降，需结合量价验证）"
        elif latest_chg > 3:
            signal = "筹码趋于分散（户数上升，需结合量价验证）"
        elif total_chg < -15:
            signal = "中期筹码趋于集中"
        elif total_chg > 15:
            signal = "中期筹码趋于分散"
        else:
            signal = "筹码稳定"

        # 前十大股东/流通股东集中度
        top10_holders = self.api.get_top10_holders(self.ts_code, self.end_date)
        top10_float_holders = self.api.get_top10_floatholders(self.ts_code, self.end_date)
        top10_summary = None
        if top10_holders is not None and not top10_holders.empty:
            latest_period = top10_holders.sort_values("end_date").iloc[-1]["end_date"]
            latest_top10 = top10_holders[top10_holders["end_date"] == latest_period]
            top10_summary = {
                "period": latest_period,
                "holder_count": len(latest_top10),
                "total_hold_ratio": round(_safe_float(latest_top10["hold_ratio"].sum()), 2),
                "records": latest_top10[["holder_name", "hold_ratio", "hold_change"]].head(5).to_dict("records"),
            }

        # 大股东增减持：优先用 stk_holdertrade，否则用 top10_holders 的 hold_change 降级
        holder_trade = self.api.get_stk_holdertrade(self.ts_code, self.end_date)
        trade_summary = None
        if holder_trade is not None and not holder_trade.empty:
            ht = holder_trade.sort_values("ann_date")
            buy = ht[ht["change_amount"] > 0]
            sell = ht[ht["change_amount"] < 0]
            trade_summary = {
                "source": "stk_holdertrade",
                "total_records": len(ht),
                "buy_records": len(buy),
                "sell_records": len(sell),
                "latest_records": ht.tail(5).to_dict("records"),
            }
        else:
            top10 = self.api.get_top10_holders(self.ts_code, self.end_date)
            if top10 is not None and not top10.empty:
                latest_period = top10.sort_values("end_date").iloc[-1]["end_date"]
                latest_top10_df = top10[top10["end_date"] == latest_period].copy()
                latest_top10_df["hold_change"] = pd.to_numeric(latest_top10_df["hold_change"], errors="coerce").fillna(0)
                buy = latest_top10_df[latest_top10_df["hold_change"] > 0]
                sell = latest_top10_df[latest_top10_df["hold_change"] < 0]
                records = latest_top10_df.sort_values("hold_change", ascending=False).head(5)[["holder_name", "hold_change", "hold_ratio"]].to_dict("records")
                trade_summary = {
                    "source": "top10_holders_hold_change",
                    "total_records": len(latest_top10_df),
                    "buy_records": len(buy),
                    "sell_records": len(sell),
                    "latest_records": records,
                }
        data = {
            "holder_num": latest,
            "latest_qoq": latest_chg,
            "total_chg_pct": total_chg,
            "signal": signal,
            "top10_holders": top10_summary,
            "holder_trade": trade_summary,
        }
        conclusion = f"最新股东户数 {latest:,}，环比 {latest_chg:+.1f}%，{signal}。"
        if top10_summary:
            conclusion += f" 最新报告期前十大股东持股占比 {top10_summary['total_hold_ratio']}%"
        if trade_summary:
            conclusion += f"；近半年大股东增持 {trade_summary['buy_records']} 次，减持 {trade_summary['sell_records']} 次"
        return DimensionResult.success("股东筹码", conclusion=conclusion, data=data)

    # ---------- 维度：解禁压力（可选） ----------

    @safe_result("解禁压力")
    def analyze_float(self) -> DimensionResult:
        df = self.api.get_share_float(self.ts_code, self.end_date)
        if df is None or df.empty:
            return DimensionResult.empty("解禁压力", note="未来 3 个月无解禁数据/无解禁计划")

        df = df.sort_values("float_date").reset_index(drop=True)
        total_float = _safe_float(df["float_share"].sum())
        data = {
            "float_records": df.to_dict("records"),
            "total_float_share": total_float,
        }
        conclusion = f"未来 3 个月有 {len(df)} 笔解禁，合计 {total_float} 股（需结合总股本计算占比）。"
        return DimensionResult.success("解禁压力", conclusion=conclusion, data=data)


    # ---------- 维度：两融（可选） ----------

    @safe_result("两融杠杆")
    def analyze_margin(self) -> DimensionResult:
        df = self.api.get_margin_detail(self.ts_code, self.end_date)
        if df is None or df.empty:
            return DimensionResult.empty("两融杠杆", note="无法获取两融数据")

        df = df.sort_values("trade_date").reset_index(drop=True)
        latest = df.iloc[-1]
        rzye = _safe_float(latest.get("rzye"))
        rqyl = _safe_float(latest.get("rqyl"))

        rzye_5_start = _safe_float(df.tail(5)["rzye"].iloc[0]) if len(df) >= 5 else None
        rzye_latest = _safe_float(df.tail(1)["rzye"].iloc[0])
        rzye_chg_5d = round((rzye_latest / rzye_5_start - 1) * 100, 2) if rzye_5_start and rzye_5_start > 0 else None

        data = {
            "rzye_billion": rzye / 1e8 if rzye else None,
            "rqyl": rqyl,
            "rzye_chg_5d_pct": rzye_chg_5d,
        }
        conclusion = f"融资余额 {data['rzye_billion']:.2f}亿" if data['rzye_billion'] is not None else "融资余额 N/A"
        if rzye_chg_5d is not None:
            conclusion += f"，近5日变化 {rzye_chg_5d:+.2f}%"
        return DimensionResult.success("两融杠杆", conclusion=conclusion, data=data)

    # ---------- 维度：市场异动（可选） ----------

    @safe_result("市场异动")
    def analyze_market_activity(self) -> DimensionResult:
        df_daily = self.api.get_daily(self.ts_code, shift_date(self.end_date, -5), self.end_date)
        if df_daily is None or df_daily.empty:
            return DimensionResult.empty("市场异动", note="无法获取日线行情")
        latest_trade_date = df_daily.sort_values("trade_date").iloc[-1]["trade_date"]

        limit_df = self.api.get_limit_list_d(latest_trade_date)
        top_df = self.api.get_top_list(latest_trade_date)
        top_inst_df = self.api.get_top_inst(latest_trade_date)

        limit_records = []
        if limit_df is not None and not limit_df.empty:
            records = limit_df[limit_df["ts_code"] == self.ts_code]
            if not records.empty:
                limit_records = records.to_dict("records")

        top_records = []
        if top_df is not None and not top_df.empty:
            records = top_df[top_df["ts_code"] == self.ts_code]
            if not records.empty:
                top_records = records.to_dict("records")

        inst_records = []
        if top_inst_df is not None and not top_inst_df.empty:
            records = top_inst_df[top_inst_df["ts_code"] == self.ts_code]
            if not records.empty:
                inst_records = records.to_dict("records")

        if not limit_records and not top_records and not inst_records:
            return DimensionResult.empty("市场异动", note=f"最近交易日 {latest_trade_date} 无涨跌停/龙虎榜/机构异动记录")

        data = {
            "trade_date": latest_trade_date,
            "limit_records": limit_records,
            "top_list_records": top_records,
            "top_inst_records": inst_records,
        }
        conclusion = f"最近交易日 {latest_trade_date}："
        parts = []
        if limit_records:
            parts.append(f"涨跌停记录 {len(limit_records)} 条")
        if top_records:
            parts.append(f"龙虎榜记录 {len(top_records)} 条")
        if inst_records:
            parts.append(f"机构席位记录 {len(inst_records)} 条")
        conclusion += "，".join(parts)
        return DimensionResult.success("市场异动", conclusion=conclusion, data=data)

    # ---------- 维度：宏观环境（可选） ----------

    @safe_result("宏观环境")
    def analyze_macro(self) -> DimensionResult:
        start = shift_date(self.end_date, -self.PERIODS[-1])
        bench_df = self.api.get_index_daily("000300.SH", start, self.end_date)
        bench_ret = None
        if bench_df is not None and not bench_df.empty:
            bench_series = bench_df.set_index("trade_date")["close"].sort_index()
            bench_ret = calc_returns(bench_series, periods=(20, 60, 250))

        end_m = self.end_date[:6]
        start_m = shift_date(self.end_date[:6] + "01", -400)[:6]
        cpi_df = self.api.get_cn_cpi(start_m, end_m)
        ppi_df = self.api.get_cn_ppi(start_m, end_m)

        cpi_latest = None
        cpi_trend = None
        if cpi_df is not None and not cpi_df.empty:
            cpi_df = cpi_df.sort_values("month")
            cpi_latest = _safe_float(cpi_df.iloc[-1].get("nt_yoy"))
            if len(cpi_df) >= 3:
                cpi_trend = "上升" if cpi_df.iloc[-1]["nt_yoy"] > cpi_df.iloc[-3]["nt_yoy"] else "下降"

        ppi_latest = None
        ppi_trend = None
        if ppi_df is not None and not ppi_df.empty:
            ppi_df = ppi_df.sort_values("month")
            ppi_latest = _safe_float(ppi_df.iloc[-1].get("ppi_yoy"))
            if len(ppi_df) >= 3:
                ppi_trend = "上升" if ppi_df.iloc[-1]["ppi_yoy"] > ppi_df.iloc[-3]["ppi_yoy"] else "下降"

        lpr_start = shift_date(self.end_date, -180)
        lpr_df = self.api.get_shibor_lpr(lpr_start, self.end_date)
        lpr_latest = None
        if lpr_df is not None and not lpr_df.empty:
            lpr_df = lpr_df.sort_values("date")
            lpr_latest = _safe_float(lpr_df.iloc[-1].get("1y"))

        # GDP：取最近 8 个季度
        gdp_latest = None
        gdp_yoy = None
        gdp_df = self.api.get_cn_gdp("2018Q1", f"{self.end_date[:4]}Q4")
        if gdp_df is not None and not gdp_df.empty:
            gdp_df = gdp_df.sort_values("quarter")
            latest_gdp = gdp_df.iloc[-1]
            gdp_latest = _safe_float(latest_gdp.get("gdp"))
            gdp_yoy = _safe_float(latest_gdp.get("gdp_yoy"))

        data = {
            "benchmark_returns": bench_ret,
            "cpi_yoy": cpi_latest,
            "cpi_trend": cpi_trend,
            "ppi_yoy": ppi_latest,
            "ppi_trend": ppi_trend,
            "lpr_1y": lpr_latest,
            "gdp": gdp_latest,
            "gdp_yoy": gdp_yoy,
        }

        parts = []
        if bench_ret:
            parts.append(f"沪深300近20日 {bench_ret.get('近20日涨幅%', 'N/A')}%")
        if cpi_latest is not None:
            parts.append(f"CPI同比 {cpi_latest}%（趋势{cpi_trend or '不明'}）")
        if ppi_latest is not None:
            parts.append(f"PPI同比 {ppi_latest}%（趋势{ppi_trend or '不明'}）")
        if lpr_latest is not None:
            parts.append(f"1年期LPR {lpr_latest}%")
        if gdp_yoy is not None:
            parts.append(f"GDP当季同比 {gdp_yoy}%")

        conclusion = "；".join(parts) if parts else "宏观数据获取不完整"
        return DimensionResult.success("宏观环境", conclusion=conclusion, data=data)

    # ---------- 维度：风险提示（汇总） ----------

    def analyze_risk(self) -> DimensionResult:
        risks = []
        notes = []

        trend = self.results.get("trend")
        if trend and trend.is_ok() and trend.data:
            vol = trend.data.get("volatility")
            mdd = trend.data.get("max_drawdown")
            rsi = trend.data.get("rsi", {})
            if isinstance(vol, (int, float)) and vol > 40:
                risks.append(f"年化波动率 {vol}% 偏高")
            if isinstance(mdd, (int, float)) and mdd < -30:
                risks.append(f"近一年最大回撤 {mdd}% 较深")
            if isinstance(rsi.get("RSI"), (int, float)) and rsi["RSI"] > 70:
                risks.append("RSI 超买，短期或有回调压力")
            if isinstance(rsi.get("RSI"), (int, float)) and rsi["RSI"] < 30:
                risks.append("RSI 超卖，短期或有反弹机会")

        valuation = self.results.get("valuation")
        if valuation and valuation.is_ok() and valuation.data:
            pe_hist = valuation.data.get("pe_hist_percentile")
            pb_hist = valuation.data.get("pb_hist_percentile")
            if isinstance(pe_hist, (int, float)) and pe_hist > 80:
                risks.append(f"PE 历史分位 {pe_hist}%，估值偏高")
            if isinstance(pb_hist, (int, float)) and pb_hist > 80:
                risks.append(f"PB 历史分位 {pb_hist}%，估值偏高")

        financial = self.results.get("financial")
        if financial and financial.is_ok() and financial.data:
            debt = financial.data.get("latest", {}).get("debt_to_assets")
            if isinstance(debt, (int, float)) and debt > 80:
                risks.append(f"资产负债率 {debt}% 较高")
            fc = financial.data.get("forecast")
            if fc and fc.get("type") in ("预减", "首亏", "续亏", "略减"):
                risks.append(f"业绩预告类型：{fc['type']}")

        moneyflow = self.results.get("moneyflow")
        if moneyflow and moneyflow.is_ok() and moneyflow.data:
            net5 = moneyflow.data.get("net_inflow_5d_billion")
            if isinstance(net5, (int, float)) and net5 < -5:
                risks.append(f"近5日主力净流出 {-net5:.2f}亿")

        margin = self.results.get("margin")
        if margin and margin.is_ok() and margin.data:
            chg = margin.data.get("rzye_chg_5d_pct")
            if isinstance(chg, (int, float)):
                if chg > 10:
                    risks.append(f"融资余额近5日快速上升 {chg:.2f}%，杠杆情绪偏热")
                elif chg < -10:
                    risks.append(f"融资余额近5日快速下降 {chg:.2f}%，杠杆资金撤离")

        market = self.results.get("market_activity")
        if market and market.is_ok() and market.data:
            if market.data.get("limit_records"):
                risks.append(f"最近交易日 {market.data['trade_date']} 出现涨跌停异动")
            if market.data.get("top_inst_records"):
                risks.append(f"最近交易日 {market.data['trade_date']} 出现机构席位异动")

        shareholder = self.results.get("shareholder")
        if shareholder and shareholder.is_ok() and shareholder.data:
            signal = shareholder.data.get("signal", "")
            if "分散" in signal:
                risks.append(f"股东筹码：{signal}")

        # VaR/CVaR
        trend = self.results.get("trend")
        if trend and trend.is_ok() and trend.data:
            if trend.data.get("var_cvar"):
                vc = trend.data["var_cvar"]
                var95 = vc.get("VaR(95%)")
                if var95 and float(var95.rstrip("%")) < -3:
                    risks.append(f"日收益率 VaR(95%) 为 {var95}，尾部风险较高")
            if trend.data.get("tail_risk"):
                tr = trend.data["tail_risk"]
                if tr.get("risk"):
                    risks.append(f"尾部风险：{tr['risk']}（偏度 {tr.get('偏度')}, 峰度 {tr.get('峰度(超额)')}）")
            if trend.data.get("amihud"):
                am = trend.data["amihud"]
                interp = am.get("interpretation", "")
                if "流动性差" in interp or "较差" in interp:
                    risks.append(f"Amihud 非流动性：{interp}")

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

        # overview 先跑：提供 stock_name / industry 等元信息
        if "overview" in self.dimensions:
            self.results["overview"] = self.analyze_overview()

        # 其余维度并行执行（I/O 为主）
        parallel_dims = [d for d in self.dimensions if d not in ("overview", "risk")]
        dim_methods = {
            "trend": self.analyze_trend,
            "valuation": self.analyze_valuation,
            "financial": self.analyze_financial,
            "moneyflow": self.analyze_moneyflow,
            "shareholder": self.analyze_shareholder,
            "float": self.analyze_float,
            "margin": self.analyze_margin,
            "market_activity": self.analyze_market_activity,
            "macro": self.analyze_macro,
        }
        to_run = {d: dim_methods[d] for d in parallel_dims if d in dim_methods}
        if to_run:
            max_workers = min(5, len(to_run))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(method): d for d, method in to_run.items()}
                for future in as_completed(futures):
                    d = futures[future]
                    self.results[d] = future.result()
        # risk 汇总最后跑
        if "risk" in self.dimensions:
            self.results["risk"] = self.analyze_risk()

        return {
            "ts_code": self.ts_code,
            "name": self.stock_name,
            "industry": self.industry,
            "end_date": self.end_date,
            "dimensions": {k: v.to_dict() for k, v in self.results.items()},
        }

    # ---------- 报告渲染 ----------

    # 维度标题中文映射 + 编号
    DIM_TITLES = {
        "overview": "概况",
        "trend": "行情趋势",
        "valuation": "估值分析",
        "financial": "财务质量",
        "moneyflow": "资金面",
        "shareholder": "股东筹码",
        "float": "解禁压力",
        "margin": "两融杠杆",
        "market_activity": "市场异动",
        "macro": "宏观环境",
        "risk": "风险提示",
    }

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

    def _v(self, d, *keys, default="N/A"):
        """安全取嵌套值。"""
        cur = d
        for k in keys:
            if cur is None:
                return default
            cur = cur.get(k) if isinstance(cur, dict) else None
        return cur if cur is not None else default

    def report(self) -> str:
        if not self.results:
            self.run()

        name = self.stock_name or self.ts_code
        lines = [
            f"# {name} 全景研究报告",
            "",
            f"> 数据日期：{self.end_date}（Tushare 数据为 T-1 日）",
            "",
        ]

        # 按顺序输出维度，带编号
        dim_order = [d for d in self.dimensions if d in self.results]
        # risk 放到最后（整体分析评价之前）
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

        # 风险提示
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

        # 整体分析评价
        idx += 1
        lines.append(f"## {idx}. 整体分析评价")
        lines.append(self._overall_evaluation())
        lines.append("")
        lines.append("---")
        lines.append("*本报告由AI基于山西证券Tushare平台数据自动生成，基于 T-1 日历史数据，仅供技术交流与学习参考，不构成任何投资建议或财务指导。*")
        return "\n".join(lines)

    def _overall_evaluation(self) -> str:
        """跨维度整体分析评价：综合判断 → 分维度要点 → 风格定位 → 结论。"""
        trend = self.results.get("trend")
        valuation = self.results.get("valuation")
        financial = self.results.get("financial")
        moneyflow = self.results.get("moneyflow")
        shareholder = self.results.get("shareholder")
        margin = self.results.get("margin")
        macro = self.results.get("macro")

        trend_ok = trend and trend.is_ok() and trend.data
        val_ok = valuation and valuation.is_ok() and valuation.data
        fin_ok = financial and financial.is_ok() and financial.data
        mf_ok = moneyflow and moneyflow.is_ok() and moneyflow.data
        sh_ok = shareholder and shareholder.is_ok() and shareholder.data

        lines = []

        # ---- 综合判断（一句话定位）----
        tags = []
        if val_ok:
            pe_hist = self._f(self._v(valuation.data, "pe_hist_percentile"))
            pb_hist = self._f(self._v(valuation.data, "pb_hist_percentile"))
            if pe_hist is not None and pb_hist is not None:
                if pe_hist < 30 and pb_hist < 30:
                    tags.append("低估值")
                elif pe_hist > 70 or pb_hist > 70:
                    tags.append("估值偏高")
        if trend_ok:
            beta = self._f(self._v(trend.data.get("beta_alpha", {}), "Beta"))
            vol = self._f(self._v(trend.data, "volatility"))
            if beta is not None and beta < 0.8:
                tags.append("防御型")
            elif beta is not None and beta > 1.2:
                tags.append("高弹性")
            if vol is not None and vol < 20:
                tags.append("低波动")
            elif vol is not None and vol > 35:
                tags.append("高波动")
        if fin_ok:
            roe = self._f(self._v(financial.data, "latest", "roe"))
            if roe is not None and roe > 15:
                tags.append("高盈利")
            elif roe is not None and roe < 5:
                tags.append("盈利偏弱")
        if val_ok:
            dv = self._f(self._v(valuation.data, "dividend_yield"))
            if dv is not None and dv > 4:
                tags.append("高股息")

        name = self.stock_name or self.ts_code
        if tags:
            lines.append(f"**综合判断**：{name}当前具备{'、'.join(tags[:4])}特征。")
        else:
            lines.append(f"**综合判断**：{name}当前各项指标相对中性，无明显极端特征。")

        # ---- 分维度要点 ----
        points = []
        if trend_ok:
            ret250 = self._v(trend.data, "returns", "近250日涨幅%")
            ret20 = self._v(trend.data, "returns", "近20日涨幅%")
            vol = self._v(trend.data, "volatility")
            mdd = self._v(trend.data, "max_drawdown")
            sharpe = self._v(trend.data, "sharpe")
            beta = self._v(trend.data.get("beta_alpha", {}), "Beta")
            points.append(f"- **趋势**：近250日 {self._fmt(ret250)}%，近20日 {self._fmt(ret20)}%，年化波动 {self._fmt(vol)}%，最大回撤 {self._fmt(mdd)}%，夏普 {self._fmt(sharpe)}，Beta {self._fmt(beta)}")
        if val_ok:
            pe = self._v(valuation.data, "pe_ttm")
            pb = self._v(valuation.data, "pb")
            pe_hist = self._v(valuation.data, "pe_hist_percentile")
            pb_hist = self._v(valuation.data, "pb_hist_percentile")
            dv = self._v(valuation.data, "dividend_yield")
            ind_name = self._v(valuation.data, "industry", "industry_name")
            pe_pct = self._v(valuation.data, "industry", "pe_percentile")
            points.append(f"- **估值**：PE {self._fmt(pe)}（历史分位 {self._fmt(pe_hist)}%），PB {self._fmt(pb)}（历史分位 {self._fmt(pb_hist)}%），股息率 {self._fmt(dv)}%" + (f"，同行业({ind_name})截面分位 {self._fmt(pe_pct)}%" if pe_pct != "N/A" else ""))
        if fin_ok:
            roe = self._v(financial.data, "latest", "roe")
            debt = self._v(financial.data, "latest", "debt_to_assets")
            npm = self._v(financial.data, "latest", "netprofit_margin")
            g = financial.data.get("growth", {})
            rev_yoy = self._v(g, "revenue_yoy")
            prof_yoy = self._v(g, "profit_yoy")
            fc = financial.data.get("forecast", {})
            fc_text = f"，业绩预告{fc.get('type','N/A')}({fc.get('p_change_min','')}%~{fc.get('p_change_max','')}%)" if fc and fc.get("type") else ""
            points.append(f"- **财务**：ROE {self._fmt(roe)}%，净利率 {self._fmt(npm)}%，资产负债率 {self._fmt(debt)}%，营收YoY {self._fmt(rev_yoy)}%，净利润YoY {self._fmt(prof_yoy)}%{fc_text}")
        if mf_ok:
            net5 = self._v(moneyflow.data, "net_inflow_5d_billion")
            net20 = self._v(moneyflow.data, "net_inflow_20d_billion")
            points.append(f"- **资金**：近5日主力净流入 {self._fmt(net5)}亿，近20日 {self._fmt(net20)}亿")
        if sh_ok:
            signal = self._v(shareholder.data, "signal")
            ht = shareholder.data.get("holder_trade", {})
            ht_text = f"，大股东增持{ht.get('buy_records',0)}次/减持{ht.get('sell_records',0)}次" if ht else ""
            points.append(f"- **筹码**：{signal}{ht_text}")
        if margin and margin.is_ok() and margin.data:
            rzye = self._v(margin.data, "rzye_billion")
            rzye_chg = self._v(margin.data, "rzye_chg_5d_pct")
            points.append(f"- **杠杆**：融资余额 {self._fmt(rzye)}亿，近5日变化 {self._fmt(rzye_chg)}%")
        if macro and macro.is_ok() and macro.data:
            gdp = self._v(macro.data, "gdp_yoy")
            bench = macro.data.get("benchmark_returns", {}) or {}
            bench20 = self._v(bench, "近20日涨幅%")
            points.append(f"- **宏观**：沪深300近20日 {bench20}%，GDP同比 {gdp}%")

        if points:
            lines.append("")
            lines.extend(points)

        # ---- 风格定位 ----
        style_parts = []
        if val_ok:
            pe_hist = self._f(self._v(valuation.data, "pe_hist_percentile"))
            pb_hist = self._f(self._v(valuation.data, "pb_hist_percentile"))
            dv = self._f(self._v(valuation.data, "dividend_yield"))
            if pe_hist is not None and pe_hist < 30 and dv is not None and dv > 3:
                style_parts.append("低估值高股息")
            elif pe_hist is not None and pe_hist > 70:
                style_parts.append("估值偏高")
        if trend_ok:
            beta = self._f(self._v(trend.data.get("beta_alpha", {}), "Beta"))
            vol = self._f(self._v(trend.data, "volatility"))
            if beta is not None and beta < 0.8 and vol is not None and vol < 25:
                style_parts.append("防御型")
            elif beta is not None and beta > 1.2:
                style_parts.append("进攻型/高弹性")
        if fin_ok:
            roe = self._f(self._v(financial.data, "latest", "roe"))
            if roe is not None and roe > 15:
                style_parts.append("高盈利质量")
            elif roe is not None and roe < 5:
                style_parts.append("盈利偏弱")

        if style_parts:
            lines.append("")
            lines.append(f"**风格定位**：{name}属于{'/'.join(style_parts)}型资产。")

        # ---- 结论 ----
        concl_parts = []
        if val_ok and trend_ok:
            pe_hist = self._f(self._v(valuation.data, "pe_hist_percentile"))
            ret250 = self._f(self._v(trend.data, "returns", "近250日涨幅%"))
            if pe_hist is not None and pe_hist < 30:
                concl_parts.append("估值处于历史低位，具备一定安全边际")
            elif pe_hist is not None and pe_hist > 70:
                concl_parts.append("估值处于历史高位，需警惕回调风险")
            if ret250 is not None and ret250 < -10:
                concl_parts.append("近期走势偏弱")
            elif ret250 is not None and ret250 > 20:
                concl_parts.append("近期走势较强")
        if fin_ok:
            debt = self._f(self._v(financial.data, "latest", "debt_to_assets"))
            if debt is not None and debt > 80:
                concl_parts.append("资产负债率偏高需关注")
            fc = financial.data.get("forecast", {})
            if fc and fc.get("type") in ("预增", "略增"):
                concl_parts.append("业绩预告正向")
        if mf_ok:
            net5 = self._f(self._v(moneyflow.data, "net_inflow_5d_billion"))
            if net5 is not None and net5 < -2:
                concl_parts.append("短期资金流出")
            elif net5 is not None and net5 > 2:
                concl_parts.append("短期资金流入")

        if concl_parts:
            lines.append("")
            lines.append(f"**结论**：{name}{'，'.join(concl_parts)}。综合来看，" + ("适合作为价值型配置关注" if tags and ("低估值" in tags or "高股息" in tags) else "需结合行业景气度与业绩趋势进一步判断") + "，注意分散风险，不构成投资建议。")

        return "\n".join(lines) if lines else "维度数据不完整，暂无法给出跨维度综合判断。"

    def _render_dimension(self, res) -> list:
        """渲染单个维度的数据表 + 分析评价。"""
        lines = []
        data = res.data or {}
        import pandas as pd

        if res.title == "概况":
            label_map = {
                "ts_code": "股票代码", "name": "股票简称", "industry": "行业(申万)",
                "area": "地区", "list_date": "上市日期", "exchange": "交易所",
                "list_status": "上市状态", "employees": "员工数",
                "main_business": "主营业务", "reg_capital": "注册资本",
                "province": "省份", "city": "城市",
            }
            rows = []
            for k, v in data.items():
                if v is not None and str(v) != "nan":
                    rows.append({"项目": label_map.get(k, k), "内容": str(v)})
            if rows:
                lines.append(self._md(pd.DataFrame(rows)))

        elif res.title == "行情趋势":
            # 涨跌幅对比表（标的 + 基准）
            returns = data.get("returns", {})
            bench = data.get("benchmark", {})
            bench_returns = bench.get("returns") or {} if bench else {}
            import pandas as pd
            all_keys = ["近5日涨幅%", "近20日涨幅%", "近60日涨幅%", "近120日涨幅%", "近250日涨幅%"]
            rows = []
            for k in all_keys:
                row = {"区间": k}
                row[self.stock_name or "标的"] = self._fmt(returns.get(k, "N/A"))
                row[bench.get("ts_code", "沪深300")] = self._fmt(bench_returns.get(k, "N/A"))
                rows.append(row)
            lines.append(self._md(pd.DataFrame(rows)))
            if data.get("latest_close_unadj") is not None:
                lines.append(f"\n未复权最新价：{data['latest_close_unadj']}")

            # 风控指标对比表
            bench_code = bench.get("ts_code", "沪深300")
            risk_rows = [
                {"指标": "年化波动率%", "标的": self._fmt(data.get("volatility")), bench_code: self._fmt(bench.get("volatility"))},
                {"指标": "最大回撤%", "标的": self._fmt(data.get("max_drawdown")), bench_code: self._fmt(bench.get("max_drawdown"))},
                {"指标": "夏普比率", "标的": self._fmt(data.get("sharpe")), bench_code: self._fmt(bench.get("sharpe"))},
            ]
            lines.append(self._md(pd.DataFrame(risk_rows)))

            # 技术指标合并表
            tech_rows = []
            for label, d in [("MACD", data.get("macd")), ("RSI", data.get("rsi")), ("KDJ", data.get("kdj")), ("布林带", data.get("boll"))]:
                if isinstance(d, dict):
                    tech_rows.append({"技术指标": label, "数值/信号": str(d.get("signal", d.get("RSI", d.get("position", "N/A"))))})
            ma = data.get("ma", {})
            if ma:
                tech_rows.append({"技术指标": "均线(后复权)", "数值/信号": f"MA5={self._fmt(ma.get('MA5'))}, MA20={self._fmt(ma.get('MA20'))}, MA60={self._fmt(ma.get('MA60'))}"})
            vr = data.get("volume_ratio", {})
            if vr:
                tech_rows.append({"技术指标": "量比", "数值/信号": f"{vr.get('量比','N/A')}({vr.get('signal','')})"})
            if tech_rows:
                lines.append("")
                lines.append(self._md(pd.DataFrame(tech_rows)))

            # 进阶指标
            adv = []
            ba = data.get("beta_alpha", {})
            if ba:
                adv.append(f"Beta {ba.get('Beta','N/A')} | Alpha(年化%) {ba.get('Alpha(年化%)','N/A')}")
            if data.get("sortino") is not None:
                adv.append(f"Sortino {data['sortino']}")
            if data.get("information_ratio") is not None:
                adv.append(f"信息比率 {data['information_ratio']}")
            if data.get("cagr") is not None:
                adv.append(f"CAGR {data['cagr']}%")
            if data.get("rolling_beta"):
                rb = data["rolling_beta"]
                adv.append(f"滚动Beta(60日) {rb.get('当前Beta','N/A')}({rb.get('趋势','')})")
            if data.get("rolling_sharpe"):
                rs = data["rolling_sharpe"]
                adv.append(f"滚动夏普(60日) {rs.get('当前滚动夏普','N/A')}({rs.get('趋势','')})")
            if data.get("relative_strength"):
                rs = data["relative_strength"]
                adv.append(f"相对强度RS {rs.get('RS','N/A')}({rs.get('trend','')})")
            if data.get("tail_risk"):
                tr = data["tail_risk"]
                adv.append(f"尾部风险: 偏度{tr.get('偏度','N/A')}, 峰度{tr.get('峰度(超额)','N/A')}")
            if data.get("amihud"):
                am = data["amihud"]
                adv.append(f"Amihud非流动性 {am.get('Amihud非流动性','N/A')}")
            if data.get("var_cvar"):
                vc = data["var_cvar"]
                adv.append(f"VaR(95%) {vc.get('VaR(95%)','N/A')} | CVaR(95%) {vc.get('CVaR(95%)','N/A')}")
            if adv:
                lines.append("\n**进阶量化指标**：")
                for a in adv:
                    lines.append(f"- {a}")

            # 分析评价
            lines.append("")
            lines.append(self._trend_eval(data))

        elif res.title == "估值分析":
            import pandas as pd
            table = {
                "指标": ["PE(TTM)", "PB", "PS(TTM)", "股息率%", "总市值(亿)"],
                "数值": [
                    self._fmt(data.get("pe_ttm")),
                    self._fmt(data.get("pb")),
                    self._fmt(data.get("ps_ttm")),
                    self._fmt(data.get("dividend_yield")),
                    self._fmt(data.get("total_mv_billion")),
                ],
            }
            lines.append(self._md(pd.DataFrame(table)))
            pe_hist = data.get("pe_hist_percentile")
            pb_hist = data.get("pb_hist_percentile")
            lines.append(f"\n**历史分位**：PE {self._fmt(pe_hist)}%，PB {self._fmt(pb_hist)}%")
            ind = data.get("industry", {})
            if ind:
                lines.append(f"\n**同行业（{ind.get('industry_name','')}）截面估值**（样本 {ind.get('sample_size','N/A')}）：")
                ind_rows = [
                    {"指标": "PE均值", "数值": self._fmt(ind.get("pe_mean"))},
                    {"指标": "PE中位数", "数值": self._fmt(ind.get("pe_median"))},
                    {"指标": "PB均值", "数值": self._fmt(ind.get("pb_mean"))},
                    {"指标": "PB中位数", "数值": self._fmt(ind.get("pb_median"))},
                    {"指标": "本股PE截面分位%", "数值": self._fmt(ind.get("pe_percentile"))},
                    {"指标": "本股PB截面分位%", "数值": self._fmt(ind.get("pb_percentile"))},
                ]
                lines.append(self._md(pd.DataFrame(ind_rows)))
            lines.append("")
            lines.append(self._valuation_eval(data))

        elif res.title == "财务质量":
            import pandas as pd
            trend_list = data.get("trend", [])
            if trend_list:
                lines.append(self._md(pd.DataFrame(trend_list)))
            g = data.get("growth", {})
            if g:
                lines.append(f"\n**营收/净利润增速**：最新 {g.get('latest_end_date','N/A')} vs 上年同期 {g.get('yoy_end_date','N/A')}")
                g_rows = [
                    {"指标": "营收YoY%", "数值": self._fmt(g.get("revenue_yoy"))},
                    {"指标": "净利润YoY%", "数值": self._fmt(g.get("profit_yoy"))},
                ]
                lines.append(self._md(pd.DataFrame(g_rows)))
            fs = data.get("fscore", {})
            if fs:
                lines.append(f"\n**Piotroski F-Score**：{fs.get('F-Score','N/A')}（{fs.get('rating','N/A')}），有效项 {fs.get('有效项','N/A')}/{(fs.get('有效项',0) or 0) + (fs.get('缺失项',0) or 0)}")
                if fs.get("comp_note"):
                    lines.append(f"- {fs['comp_note']}")
            fc = data.get("forecast", {})
            if fc:
                lines.append("\n**业绩预告**：")
                fc_rows = [{"指标": k, "数值": str(v)} for k, v in fc.items()]
                lines.append(self._md(pd.DataFrame(fc_rows)))
            lines.append("")
            lines.append(self._financial_eval(data))

        elif res.title == "资金面":
            import pandas as pd
            rows = [
                {"口径": "近5日主力净流入(亿)", "数值": self._fmt(data.get("net_inflow_5d_billion"))},
                {"口径": "近20日主力净流入(亿)", "数值": self._fmt(data.get("net_inflow_20d_billion"))},
                {"口径": "超大单近5日净流入(亿)", "数值": self._fmt(data.get("elg_net_5d_billion"))},
                {"口径": "北向近20日净流入(百万)", "数值": self._fmt(data.get("north_inflow_20d_million"))},
            ]
            lines.append(self._md(pd.DataFrame(rows)))
            bt = data.get("block_trade", {})
            if bt:
                lines.append(f"\n**大宗交易（近60日）**：共 {bt.get('count','N/A')} 笔，合计 {bt.get('total_amount','N/A')} 万元，均价 {bt.get('avg_price','N/A')} 元")
            lines.append("")
            lines.append(self._moneyflow_eval(data))

        elif res.title == "股东筹码":
            import pandas as pd
            rows = [
                {"指标": "最新股东户数", "数值": self._fmt(data.get("holder_num"))},
                {"指标": "环比变化", "数值": f"{self._fmt(data.get('latest_qoq'))}%"},
                {"指标": "中期变化", "数值": f"{self._fmt(data.get('total_chg_pct'))}%"},
                {"指标": "信号", "数值": data.get("signal", "N/A")},
            ]
            lines.append(self._md(pd.DataFrame(rows)))
            t10 = data.get("top10_holders", {})
            if t10 and t10.get("records"):
                lines.append(f"\n**前十大股东（{t10.get('period','N/A')}）**：合计持股 {t10.get('total_hold_ratio','N/A')}%")
                lines.append(self._md(pd.DataFrame(t10["records"])))
            ht = data.get("holder_trade", {})
            if ht:
                lines.append(f"\n**大股东增减持（近半年）**：总记录 {ht.get('total_records','N/A')}，增持 {ht.get('buy_records','N/A')} 次，减持 {ht.get('sell_records','N/A')} 次（数据源：{ht.get('source','N/A')}）")
                if ht.get("latest_records"):
                    lines.append(self._md(pd.DataFrame(ht["latest_records"])))
            lines.append("")
            lines.append(self._shareholder_eval(data))

        elif res.title == "解禁压力":
            records = data.get("float_records", [])
            if records:
                import pandas as pd
                lines.append(self._md(pd.DataFrame(records)))
            else:
                lines.append("- 未来3个月无解禁数据/无解禁计划")

        elif res.title == "两融杠杆":
            import pandas as pd
            rows = [
                {"指标": "融资余额(亿)", "数值": self._fmt(data.get("rzye_billion"))},
                {"指标": "融券余量", "数值": self._fmt(data.get("rqyl"))},
                {"指标": "近5日融资余额变化%", "数值": self._fmt(data.get("rzye_chg_5d_pct"))},
            ]
            lines.append(self._md(pd.DataFrame(rows)))
            lines.append("")
            lines.append(self._margin_eval(data))

        elif res.title == "市场异动":
            lines.append(f"**交易日**：{data.get('trade_date', 'N/A')}")
            import pandas as pd
            for key, label in [("limit_records", "涨跌停"), ("top_list_records", "龙虎榜"), ("top_inst_records", "机构席位")]:
                records = data.get(key, [])
                if records:
                    lines.append(f"\n**{label}**：")
                    lines.append(self._md(pd.DataFrame(records)))
            if not any(data.get(k) for k in ["limit_records", "top_list_records", "top_inst_records"]):
                lines.append("- 无涨跌停/龙虎榜/机构异动记录")

        elif res.title == "宏观环境":
            import pandas as pd
            bench_ret = data.get("benchmark_returns", {}) or {}
            rows = [
                {"指标": "沪深300近20日%", "数值": self._fmt(bench_ret.get("近20日涨幅%"))},
                {"指标": "CPI同比%", "数值": self._fmt(data.get("cpi_yoy"))},
                {"指标": "CPI趋势", "数值": data.get("cpi_trend", "N/A")},
                {"指标": "PPI同比%", "数值": self._fmt(data.get("ppi_yoy"))},
                {"指标": "PPI趋势", "数值": data.get("ppi_trend", "N/A")},
                {"指标": "1年期LPR%", "数值": self._fmt(data.get("lpr_1y"))},
                {"指标": "GDP当季同比%", "数值": self._fmt(data.get("gdp_yoy"))},
            ]
            lines.append(self._md(pd.DataFrame(rows)))
            lines.append("")
            lines.append(self._macro_eval(data))

        return [l for l in lines if l is not None]

    # ---------- 各维度分析评价 ----------

    def _trend_eval(self, data):
        parts = []
        returns = data.get("returns", {})
        ret20 = self._fmt(returns.get("近20日涨幅%"))
        ret250 = self._fmt(returns.get("近250日涨幅%"))
        mdd = self._fmt(data.get("max_drawdown"))
        rsi = data.get("rsi", {}).get("RSI", "N/A")
        macd_sig = data.get("macd", {}).get("signal", "")
        boll_pos = data.get("boll", {}).get("position", "")
        parts.append(f"近20日涨跌幅 {ret20}%，近250日 {ret250}%，最大回撤 {mdd}%。")
        try:
            rsi_f = float(rsi)
            parts.append(f"RSI {rsi} 处于{'超卖(<30)' if rsi_f < 30 else '超买(>70)' if rsi_f > 70 else '中性区间'}。")
        except Exception:
            pass
        if macd_sig:
            parts.append(f"MACD {macd_sig}。")
        if boll_pos:
            parts.append(f"布林带{boll_pos}。")
        rb = data.get("rolling_beta", {})
        if rb:
            parts.append(f"滚动Beta {rb.get('当前Beta','N/A')}，趋势{rb.get('趋势','N/A')}。")
        rs = data.get("relative_strength", {})
        if rs:
            parts.append(f"相对沪深300 RS {rs.get('RS','N/A')}，{'相对走强' if rs.get('RS',1) and float(rs.get('RS',1)) > 1 else '相对走弱'}。")
        return "**分析评价**：" + "".join(parts)

    def _valuation_eval(self, data):
        pe_hist = data.get("pe_hist_percentile")
        pb_hist = data.get("pb_hist_percentile")
        pe = data.get("pe_ttm")
        ind_pe = data.get("industry", {}).get("pe_median")
        parts = []
        try:
            pe_f = float(pe_hist)
            pb_f = float(pb_hist)
            parts.append(f"PE历史分位 {pe_f}%、PB历史分位 {pb_f}%，整体估值{'偏低' if pe_f < 30 and pb_f < 30 else '偏高' if pe_f > 70 or pb_f > 70 else '中等'}。")
        except Exception:
            pass
        try:
            pe_v = float(pe)
            ind_v = float(ind_pe)
            parts.append(f"当前PE {pe_v} {'高于' if pe_v > ind_v else '低于'}行业中位数 {ind_v}，{'相对偏贵' if pe_v > ind_v * 1.2 else '相对便宜' if pe_v < ind_v * 0.8 else '差异不大'}。")
        except Exception:
            pass
        parts.append("估值需结合业绩增速与行业景气度判断，单一分位不能作为买卖依据。")
        return "**分析评价**：" + "".join(parts)

    def _financial_eval(self, data):
        fs = data.get("fscore", {})
        fscore = fs.get("F-Score")
        g = data.get("growth", {})
        rev = g.get("revenue_yoy")
        prof = g.get("profit_yoy")
        parts = []
        if fscore is not None:
            parts.append(f"Piotroski F-Score {fscore}分（{'偏强' if fscore >= 7 else '偏弱' if fscore <= 2 else '中等'}）。")
        try:
            rev_f = float(rev)
            prof_f = float(prof)
            parts.append(f"营收YoY {rev_f}%、净利润YoY {prof_f}%，业绩{'向好' if rev_f > 10 and prof_f > 10 else '承压' if rev_f < 0 or prof_f < 0 else '平稳'}。")
        except Exception:
            pass
        comp = data.get("latest", {}).get("grossprofit_margin")
        if comp is None or comp != comp:
            parts.append("金融机构毛利率/资产周转率等指标语义与工商业不同，F-Score仅作弱参考。")
        return "**分析评价**：" + "".join(parts)

    def _moneyflow_eval(self, data):
        net5 = data.get("net_inflow_5d_billion")
        net20 = data.get("net_inflow_20d_billion")
        parts = []
        try:
            n5 = float(net5)
            parts.append(f"近5日主力净流入 {n5}亿，短期资金{'流入' if n5 > 0 else '流出'}。")
        except Exception:
            pass
        try:
            n20 = float(net20)
            n5 = float(net5)
            parts.append(f"近20日累计 {n20}亿，与近5日方向{'一致' if n5 * n20 >= 0 else '背离'}。")
        except Exception:
            pass
        parts.append("资金流向为短期情绪指标，需与基本面、估值配合观察。")
        return "**分析评价**：" + "".join(parts)

    def _shareholder_eval(self, data):
        signal = data.get("signal", "")
        trade = data.get("holder_trade", {})
        parts = []
        if "集中" in signal:
            parts.append("股东户数减少，筹码趋于集中，通常与人均持股上升相关，但需结合股价位置与成交量判断是否为有效吸筹。")
        elif "分散" in signal:
            parts.append("股东户数增加，筹码趋于分散，通常与人均持股下降相关，需警惕高位派发风险。")
        else:
            parts.append("股东户数变化温和，筹码结构相对稳定。")
        if trade:
            buy = trade.get("buy_records", 0) or 0
            sell = trade.get("sell_records", 0) or 0
            parts.append(f"大股东近半年增持 {buy} 次、减持 {sell} 次，方向{'偏积极' if buy > sell else '偏谨慎' if sell > buy else '中性'}。")
        return "**分析评价**：" + "".join(parts)

    def _margin_eval(self, data):
        chg = data.get("rzye_chg_5d_pct")
        parts = []
        try:
            c = float(chg)
            parts.append(f"融资余额近5日变化 {c}%，{'杠杆情绪升温' if c > 5 else '杠杆资金撤离' if c < -5 else '杠杆情绪平稳'}。")
        except Exception:
            parts.append("融资余额数据不足。")
        parts.append("两融余额变化反映风险偏好，快速上升需警惕追高风险。")
        return "**分析评价**：" + "".join(parts)

    def _macro_eval(self, data):
        gdp = data.get("gdp_yoy")
        bench = data.get("benchmark_returns", {}) or {}
        bench20 = bench.get("近20日涨幅%")
        parts = []
        try:
            b = float(bench20)
            parts.append(f"沪深300近20日 {b}%，大盘短期{'偏强' if b > 2 else '偏弱' if b < -2 else '震荡'}。")
        except Exception:
            pass
        try:
            g = float(gdp)
            parts.append(f"GDP当季同比 {g}%，宏观经济{'景气度较好' if g > 5 else '景气度偏弱' if g < 4 else '景气度平稳'}。")
        except Exception:
            pass
        parts.append("宏观环境对权益资产形成顺风/逆风，但个股仍取决于自身基本面。")
        return "**分析评价**：" + "".join(parts)


# ---------- 便捷函数 ----------

def analyze_stock(
    ts_code: str,
    end_date: Optional[str] = None,
    dimensions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    runner = StockAnalysisRunner(ts_code=ts_code, end_date=end_date, dimensions=dimensions)
    return runner.run()


def stock_report(
    ts_code: str,
    end_date: Optional[str] = None,
    dimensions: Optional[List[str]] = None,
) -> str:
    runner = StockAnalysisRunner(ts_code=ts_code, end_date=end_date, dimensions=dimensions)
    return runner.report()


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    import argparse
    parser = argparse.ArgumentParser(description="股票综合分析 Runner")
    parser.add_argument("ts_code", help="股票代码，如 600519.SH 或 600519")
    parser.add_argument("--end-date", default=None, help="分析截止日期 YYYYMMDD")
    parser.add_argument(
        "--dimensions",
        default=None,
        help='维度列表，逗号分隔，默认 overview,trend,valuation,financial,moneyflow,margin,market_activity,macro,risk',
    )
    args = parser.parse_args()

    dims = args.dimensions.split(",") if args.dimensions else None
    print(stock_report(args.ts_code, args.end_date, dims))
