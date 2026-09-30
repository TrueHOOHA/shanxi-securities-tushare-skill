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

from adjustment import apply_adj_factor, calc_percentile_rank, winsorize_cross_section
from attribution import calc_beta_alpha, calc_piotroski_fscore
from basic_metrics import (
    calc_cagr, calc_information_ratio, calc_ma, calc_max_drawdown,
    calc_returns, calc_sharpe, calc_sortino, calc_volatility,
)
from data_api import DataAPI, data_errors, shift_date
from result_model import DimensionResult, ResultStatus, safe_result
from report_html import df_to_md_table, render_html_report
from composite import calc_composite_score, calc_factor_positioning, calc_risk_budget
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
        # 主营业务构成（最新报告期按产品，取收入前三）
        try:
            mainbz = self.api.get_fina_mainbz(self.ts_code, self.end_date)
            if mainbz is not None and not mainbz.empty:
                m = mainbz.sort_values("end_date")
                latest_period = m.iloc[-1]["end_date"]
                m = m[m["end_date"] == latest_period].copy()
                # 去重：同报告期同金额的重复行（合并/调整口径重复记录），避免占比虚高
                m = m.drop_duplicates(subset=["bz_sales", "bz_profit"], keep="first")
                m["bz_sales"] = pd.to_numeric(m["bz_sales"], errors="coerce")
                m = m.dropna(subset=["bz_sales"]).sort_values("bz_sales", ascending=False)
                if not m.empty:
                    total_sales = float(m["bz_sales"].sum())
                    data["mainbz_period"] = str(latest_period)
                    data["mainbz_top3"] = [
                        {"主营项目": r.get("bz_item"),
                         "营收(亿元)": (round(_safe_float(r.get("bz_sales")) / 1e8, 2) if _safe_float(r.get("bz_sales")) is not None else None),
                         "占比%": round(float(r.get("bz_sales")) / total_sales * 100, 1) if total_sales else None}
                        for _, r in m.head(3).iterrows()
                    ]
        except Exception:
            pass
        # 曾用名（历史沿革参考）
        try:
            nc = self.api.get_namechange(self.ts_code)
            if nc is not None and not nc.empty:
                former = [n for n in nc.sort_values("start_date")["name"].tolist() if n and n != self.stock_name]
                if former:
                    data["former_names"] = "、".join(dict.fromkeys(former))
        except Exception:
            pass
        # 次新股标记（近1年IPO，附发行信息）
        try:
            ns = self.api.get_new_share(shift_date(self.end_date, -365), self.end_date)
            if ns is not None and not ns.empty:
                hit = ns[ns["ts_code"] == self.ts_code]
                if not hit.empty:
                    nrow = hit.iloc[0]
                    data["ipo_info"] = f"次新股：{nrow.get('ipo_date')} 上市，发行价 {_safe_float(nrow.get('price'))} 元，发行PE {_safe_float(nrow.get('pe'))}"
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
            "chart": {
                "title": "日线行情（未复权）",
                "type": "candlestick",
                "dates": df_daily.sort_values("trade_date")["trade_date"].tolist(),
                "ohlc": df_daily.sort_values("trade_date")[["open", "close", "low", "high"]].values.tolist(),
                "vol": df_daily.sort_values("trade_date")["vol"].tolist(),
            },
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

        pe_raw = _safe_float(latest.get("pe_ttm")) or _safe_float(latest.get("pe"))
        pe = round(pe_raw, 2) if pe_raw is not None else None
        pb_raw = _safe_float(latest.get("pb"))
        pb = round(pb_raw, 2) if pb_raw is not None else None
        ps_raw = _safe_float(latest.get("ps_ttm")) or _safe_float(latest.get("ps"))
        ps = round(ps_raw, 2) if ps_raw is not None else None
        dv_raw = _safe_float(latest.get("dv_ratio"))
        dv = round(dv_raw, 2) if dv_raw is not None else None
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


        # 分红记录（近3年，实施口径；cash_div_tax 为每股税前分红）
        div_info = None
        try:
            div_df = self.api.get_dividend(self.ts_code)
            if div_df is not None and not div_df.empty:
                dd = div_df[div_df["div_proc"].astype(str).str.contains("实施", na=False)].copy()
                dd = dd.dropna(subset=["ex_date"])
                # 去重：同除权日同金额的重复记录（dividend 接口含多阶段重复披露）
                dd = dd.drop_duplicates(subset=["ex_date", "cash_div_tax"])
                if "cash_div_tax" in dd.columns:
                    dd["cash_div_tax"] = pd.to_numeric(dd["cash_div_tax"], errors="coerce")
                    dd = dd[dd["cash_div_tax"] > 0]  # 剔除纯送转/零现金行（to_numeric 防 dtype 漂移为 str 时比较报错）
                cutoff = shift_date(self.end_date, -365 * 3)
                dd = dd[dd["ex_date"].astype(str) >= cutoff].sort_values("ex_date")
                if not dd.empty:
                    div_info = {
                        "count_3y": len(dd),
                        "records": dd[["ex_date", "cash_div_tax", "stk_div"]].tail(5).to_dict("records"),
                        "total_cash_div_3y": round(pd.to_numeric(dd["cash_div_tax"], errors="coerce").sum(), 3),
                    }
        except Exception:
            pass

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
            "dividends": div_info,
            "chart": {
                "title": "近5年 PE(TTM)/PB 走势",
                "type": "line",
                "dates": df["trade_date"].tolist(),
                "series": [
                    {"name": "PE(TTM)", "yAxisIndex": 0, "data": df["pe_ttm"].tolist()},
                    {"name": "PB", "yAxisIndex": 1, "data": df["pb"].tolist()},
                ],
            },
        }

        # PE 缺失多为报告期亏损（负收益），此时 PE 无意义——给出可解释文案而非裸 N/A
        _pe_missing_note = "（通常为报告期亏损或收益为负，PE 无意义）" if pe is None else ""
        conclusion = f"PE(TTM) {pe if pe is not None else '无数据'}{_pe_missing_note}，PB {pb if pb is not None else '无数据'}，总市值 {_fmt_billions(total_mv)}。"
        if pe_hist is not None:
            conclusion += f"PE 近5年历史分位 {pe_hist}%。"
        elif pe is not None and hist_count < 250:
            conclusion += f"PE 近5年历史分位 无数据{hist_note}。"
        if pb_hist is not None:
            conclusion += f"PB 近5年历史分位 {pb_hist}%。"
        elif pb is not None and hist_count < 250:
            conclusion += f"PB 近5年历史分位 无数据{hist_note}。"
        if industry_val:
            conclusion += (
                f" 同行业({industry_val.get('industry_name', self.industry)})PE均值 {industry_val.get('pe_mean', 'N/A')}"
                f"，中位数 {industry_val.get('pe_median', 'N/A')}"
                f"，本股行业截面分位 {industry_val.get('pe_percentile', 'N/A')}%。"
            )

        if div_info:
            conclusion += f" 近3年实施分红 {div_info['count_3y']} 次，累计每股分红（税前）{self._fmt(div_info['total_cash_div_3y'])} 元。"

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
            # 过滤过期预告：若预告对应报告期已出正式财报（end_date 已在 fina_indicator 已公布报告期内），该预告已被实绩替代，不再引用
            reported_ends = set(df["end_date"].astype(str).tolist()) if "end_date" in df.columns else set()
            pending = forecast_df[~forecast_df["end_date"].astype(str).isin(reported_ends)]
            if not pending.empty:
                fc = pending.sort_values("ann_date").iloc[-1]
                forecast_info = {
                    "end_date": fc.get("end_date"),
                    "type": fc.get("type"),
                    "p_change_min": _safe_float(fc.get("p_change_min")),
                    "p_change_max": _safe_float(fc.get("p_change_max")),
                    "ann_date": fc.get("ann_date"),
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

        # 业绩快报（比定期报告更早披露的快报口径）
        express_info = None
        express_df = self.api.get_express(self.ts_code, self.end_date)
        if express_df is not None and not express_df.empty:
            er = express_df.sort_values(["end_date", "ann_date"]).iloc[-1]
            express_info = {
                "end_date": er.get("end_date"),
                "ann_date": er.get("ann_date"),
                "revenue_yoy": _safe_float(er.get("yoy_sales")),
                "np_yoy": _safe_float(er.get("yoy_dedu_np")),
                "eps": _safe_float(er.get("diluted_eps")),
                "roe": _safe_float(er.get("diluted_roe")),
            }

        # 审计意见（最新一期）
        audit_info = None
        audit_df = self.api.get_fina_audit(self.ts_code, self.end_date)
        if audit_df is not None and not audit_df.empty:
            ar = audit_df.sort_values(["end_date", "ann_date"]).iloc[-1]
            audit_info = {
                "end_date": ar.get("end_date"),
                "ann_date": ar.get("ann_date"),
                "audit_result": ar.get("audit_result"),
                "audit_agency": ar.get("audit_agency"),
            }

        # 下一次财报预定披露日（事件窗口提示）
        next_disclosure = None
        try:
            disc_df = self.api.get_disclosure_date(self.ts_code)
            if disc_df is not None and not disc_df.empty:
                dd = disc_df.dropna(subset=["pre_date"])
                future = dd[dd["pre_date"].astype(str) >= self.end_date].sort_values("pre_date")
                if not future.empty:
                    fr = future.iloc[0]
                    next_disclosure = {"end_date": fr.get("end_date"), "pre_date": fr.get("pre_date"), "actual_date": fr.get("actual_date") or None}
        except Exception:
            pass

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
            "express": express_info,
            "audit": audit_info,
            "next_disclosure": next_disclosure,
            "chart": {
                "title": "近8期 ROE/毛利率/净利率（%）",
                "type": "line",
                "dates": [str(e) for e in trend["end_date"].tolist()],
                "series": [
                    {"name": "ROE", "data": [v if v == v else None for v in trend["roe"].tolist()]},
                    {"name": "毛利率", "data": [v if v == v else None for v in trend["grossprofit_margin"].tolist()]},
                    {"name": "净利率", "data": [v if v == v else None for v in trend["netprofit_margin"].tolist()]},
                ],
            },
        }

        roe = data["latest"]["roe"]
        gm = data["latest"]["grossprofit_margin"]
        debt = data["latest"]["debt_to_assets"]
        _period_m = str(latest.get("end_date", ""))[4:6]
        _roe_note = "" if _period_m == "12" else "（非年化口径）"
        conclusion = (
            f"ROE {self._fmt(roe) if roe is not None else 'N/A'}%{_roe_note}，"
            f"毛利率 {self._fmt(gm) if gm is not None else 'N/A'}%，"
            f"资产负债率 {self._fmt(debt) if debt is not None else 'N/A'}%。"
        )
        if fscore:
            conclusion += f" Piotroski F-Score {fscore.get('F-Score', 'N/A')}（{fscore.get('rating', '')}）。"
        if growth:
            conclusion += (
                f" 营收同比 {growth.get('revenue_yoy', 'N/A')}%，"
                f"净利润同比 {growth.get('profit_yoy', 'N/A')}%。"
            )
        if forecast_info:
            if self._forecast_stale(forecast_info):
                conclusion += (
                    f"最近业绩预告发布于 {forecast_info['ann_date']}（报告期 {forecast_info['end_date']}），"
                    f"距今已超 150 天未更新，该预告已过期，不作为当前业绩参考。"
                )
            else:
                conclusion += (
                    f"{self._forecast_label(forecast_info)}：{forecast_info['type']}，"
                    f"净利润变动 {forecast_info['p_change_min']}%~{forecast_info['p_change_max']}%。"
                )

        if express_info:
            conclusion += (
                f" 业绩快报（{express_info.get('ann_date')} 公告，报告期 {express_info.get('end_date')}）："
                f"营收同比 {express_info.get('revenue_yoy', 'N/A')}%，归母净利同比 {express_info.get('np_yoy', 'N/A')}%。"
            )
        if audit_info and audit_info.get("audit_result"):
            _std = "标准无保留" in str(audit_info["audit_result"])
            conclusion += (
                f" 最新审计意见：{audit_info['audit_result']}（{audit_info.get('end_date')} 年报）"
                + ("" if _std else "——非标准无保留意见（区别于标准无保留意见）") + "。"
            )
        if next_disclosure:
            conclusion += f" 下一期财报预定披露日 {next_disclosure.get('pre_date')}（报告期 {next_disclosure.get('end_date')}）。"

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

        # ⚠️ 口径区分（实测校准，标签必须与口径一致）：
        #   · `net_mf_amount` 是服务端独立计算的**全口径**净流入，不等于"大单+超大单"净额
        #     （实测 300750.SZ 20260929：全口径 3.67 亿 vs 主力 2.53 亿）。
        #   · `buy_*`/`sell_*` 是买卖**双边全额**分解，四类相加恒为 0（实测 Σ买=Σ卖=当日成交额），
        #     所以只有"超大单""大单+超大单"这类子集口径才有意义。
        # 原实现把 net_mf_amount 直接标成"主力净流入（含大单与超大单）"，属于口径错标。
        net_5 = round(df.tail(5)["net_mf_amount"].sum() / 1e4, 2) if len(df) >= 5 else None
        net_20 = round(df.tail(20)["net_mf_amount"].sum() / 1e4, 2) if len(df) >= 20 else None

        df["elg_net"] = df["buy_elg_amount"] - df["sell_elg_amount"]
        elg_5 = round(df.tail(5)["elg_net"].sum() / 1e4, 2) if len(df) >= 5 else None
        elg_20 = round(df.tail(20)["elg_net"].sum() / 1e4, 2) if len(df) >= 20 else None

        # 主力口径 = 大单 + 超大单净额（与"全口径"分开呈现，避免混用）
        df["main_net"] = (df["buy_lg_amount"] + df["buy_elg_amount"]
                          - df["sell_lg_amount"] - df["sell_elg_amount"])
        main_5 = round(df.tail(5)["main_net"].sum() / 1e4, 2) if len(df) >= 5 else None
        main_20 = round(df.tail(20)["main_net"].sum() / 1e4, 2) if len(df) >= 20 else None

        # 大宗交易
        block_df = self.api.get_block_trade(self.ts_code, self.end_date)
        block_summary = None
        if block_df is not None and not block_df.empty:
            block_df = block_df.sort_values("trade_date").reset_index(drop=True)
            block_summary = {
                "count": len(block_df),
                "total_vol": round(block_df["vol"].sum(), 2),
                "total_amount": round(block_df["amount"].sum(), 2),
                "total_amount_billion": round(float(block_df["amount"].sum()) / 1e4, 2),
                "avg_price": round(block_df["price"].mean(), 2),
                "latest": block_df.iloc[-1].to_dict(),
            }

        data = {
            "latest_net_mf": _safe_float(latest.get("net_mf_amount")),
            "net_inflow_5d_billion": net_5,
            "net_inflow_20d_billion": net_20,
            "main_net_5d_billion": main_5,
            "main_net_20d_billion": main_20,
            "elg_net_5d_billion": elg_5,
            "elg_net_20d_billion": elg_20,
            "block_trade": block_summary,
            "chart": {
                "title": "资金净流入（万元）",
                "type": "bar",
                "dates": df["trade_date"].tolist(),
                "series": [
                    {"name": "全口径净流入", "data": df["net_mf_amount"].tolist()},
                    {"name": "主力净流入(大单+超大单)", "data": df["main_net"].tolist()},
                    {"name": "超大单净流入", "data": df["elg_net"].tolist()},
                ],
            },
        }

        # 文案与口径一一对应：全口径 / 主力（大单+超大单）/ 超大单，不再混称"主力"
        conclusion = f"近5日全口径净流入 {net_5:.2f}亿" if net_5 is not None else "近5日全口径净流入 数据不足"
        conclusion += f"，近20日全口径净流入 {net_20:.2f}亿" if net_20 is not None else ""
        conclusion += f"；主力（大单+超大单）近5日净流入 {main_5:.2f}亿" if main_5 is not None else "；主力口径数据不足"
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


        # 沪深股通十大成交股上榜（近60日；amount/net_amount 单位：元）
        hsgt_top = None
        hsgt_top_df = self.api.get_hsgt_top10(self.ts_code, self.end_date)
        if hsgt_top_df is not None and not hsgt_top_df.empty:
            hd = hsgt_top_df.sort_values("trade_date")
            # 取最近有净买入金额的行（最新上榜行金额可能缺省）
            hd_valid = hd.dropna(subset=["net_amount"])
            lh = (hd_valid if not hd_valid.empty else hd).iloc[-1]
            _hna = _safe_float(lh.get("net_amount"))
            hsgt_top = {
                "count_60d": len(hd),
                "latest_trade_date": lh.get("trade_date"),
                "rank": _safe_float(lh.get("rank")),
                "net_amount_billion": round(_hna / 1e8, 2) if _hna is not None else None,
            }
        data["hsgt_top10"] = hsgt_top

        # 回购（近一年）
        # ⚠️ 单位与口径（实测校准，勿改回"对 amount 直接求和"）：
        #   1) repurchase.amount 单位为「元」，不是万元。校验：文档示例 000813.SZ
        #      vol=15,450,767 / amount=1.243e8 → 8.04 元/股，落在当日 high_limit 8.40
        #      / low_limit 7.80 区间内；若按万元解读则为 8 万元/股，明显不成立。
        #   2) 同一回购计划的多条公告中 vol/amount 是**累计快照**，随 ann_date 单调递增。
        #      校验：300750.SZ 20260924→20260929 增量 2.0e8 元 / 697,080 股 = 287 元/股，
        #      落在当日 low_limit 286.53 ~ high_limit 287.27 区间内。
        #      因此**不可对多条记录求和**，否则同一笔回购被重复累加。
        #   3) 「预案」「股东大会通过」属未实施计划，不计入已回购金额。
        rep_info = None
        try:
            rep_df = self.api.get_repurchase(self.ts_code, self.end_date)
            if rep_df is not None and not rep_df.empty:
                rep_df = rep_df.sort_values("ann_date").reset_index(drop=True)
                _proc = (
                    rep_df["proc"].astype(str)
                    if "proc" in rep_df.columns
                    else pd.Series([""] * len(rep_df))
                )
                _done_mask = _proc.str.contains("实施|完成", na=False)
                done, plan = rep_df[_done_mask], rep_df[~_done_mask]
                rep_info = {
                    "count_1y": int(len(done)),
                    "latest_proc": rep_df.iloc[-1].get("proc"),
                    "latest_ann_date": rep_df.iloc[-1].get("ann_date"),
                }
                _amt = (
                    pd.to_numeric(done["amount"], errors="coerce").dropna()
                    if "amount" in done.columns
                    else pd.Series(dtype=float)
                )
                _vol = (
                    pd.to_numeric(done["vol"], errors="coerce").dropna()
                    if "vol" in done.columns
                    else pd.Series(dtype=float)
                )
                if not _amt.empty:
                    # vol 随公告日单调不减 → 累计口径，取最新一条；否则视为逐日值求和
                    _cumulative = len(_vol) > 1 and bool(_vol.is_monotonic_increasing)
                    _total_yuan = float(_amt.iloc[-1]) if _cumulative else float(_amt.sum())
                    rep_info["total_amount_billion"] = round(_total_yuan / 1e8, 2)
                    rep_info["is_cumulative"] = _cumulative
                if not plan.empty and "amount" in plan.columns:
                    _plan_amt = pd.to_numeric(plan["amount"], errors="coerce").dropna()
                    if not _plan_amt.empty:
                        rep_info["plan_amount_billion"] = round(float(_plan_amt.max()) / 1e8, 2)
        except Exception:
            rep_info = None
        data["repurchase"] = rep_info

        # 券商月度金股（近3个月；month 为必选入参，按月查询后本地过滤）
        br_records = []
        try:
            for _i in range(3):
                _m = shift_date(self.end_date[:6] + "01", -30 * _i)[:6]
                br_df = self.api.get_broker_recommend(_m)
                if br_df is not None and not br_df.empty:
                    hit = br_df[br_df["ts_code"] == self.ts_code]
                    for _, r in hit.iterrows():
                        br_records.append({"month": _m, "broker": r.get("broker")})
        except Exception:
            pass
        br_info = {"count_3m": len(br_records), "brokers": sorted({r["broker"] for r in br_records if r.get("broker")})[:5]}
        data["broker_recommend"] = br_info
        if hsgt_top:
            _hna_txt = f"{hsgt_top['net_amount_billion']} 亿" if hsgt_top.get("net_amount_billion") is not None else "金额未披露"
            conclusion += f" 近60日上榜沪深股通十大成交股 {hsgt_top['count_60d']} 次（最新净买入 {_hna_txt}）。"
        if rep_info:
            if rep_info.get("total_amount_billion") is not None:
                _scope = "累计" if rep_info.get("is_cumulative") else "合计"
                conclusion += (
                    f" 近一年已实施回购 {rep_info['count_1y']} 次，"
                    f"{_scope} {rep_info['total_amount_billion']} 亿元。"
                )
            else:
                conclusion += " 近一年回购公告均处于预案/股东大会通过阶段，尚无已实施金额。"
            if rep_info.get("plan_amount_billion") is not None:
                conclusion += (
                    f" 另有回购预案计划 {rep_info['plan_amount_billion']} 亿元"
                    "（未实施，不计入已回购金额）。"
                )
        if br_info["count_3m"]:
            conclusion += f" 近3个月入选券商金股 {br_info['count_3m']} 次。"

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
            signal = "筹码趋于集中（户数下降）"
        elif latest_chg > 3:
            signal = "筹码趋于分散（户数上升）"
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
                "records": latest_top10[["holder_name", "hold_ratio", "hold_change"]].head(10).to_dict("records"),
            }

        # 大股东增减持：优先用 stk_holdertrade，否则用 top10_holders 的 hold_change 降级
        holder_trade = self.api.get_stk_holdertrade(self.ts_code, self.end_date)
        trade_summary = None
        if holder_trade is not None and not holder_trade.empty:
            ht = holder_trade.sort_values("ann_date")
            # ⚠️ 字段名（实测校准）：服务端为 `change_vol`（单位：股），**不是** `change_amount`；
            # 增减方向另有 `in_de` 字段（IN=增持 / DE=减持），比按数值符号判断更可靠。
            # 原实现取 change_amount 会因字段不存在导致整个接口返回空，
            # 使增减持永远只能走 top10_holders 降级路径。
            _vol = pd.to_numeric(ht["change_vol"], errors="coerce") if "change_vol" in ht.columns else pd.Series(dtype=float)
            if "in_de" in ht.columns:
                _flag = ht["in_de"].astype(str).str.upper()
                buy, sell = ht[_flag == "IN"], ht[_flag == "DE"]
                if buy.empty and sell.empty:  # in_de 全为空时回退按变动方向判断
                    buy, sell = ht[_vol > 0], ht[_vol < 0]
            else:
                buy, sell = ht[_vol > 0], ht[_vol < 0]
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
                changed = latest_top10_df[latest_top10_df["hold_change"] != 0]
                records = (changed.sort_values("hold_change", key=lambda s: s.abs(), ascending=False).head(5)[["holder_name", "hold_change", "hold_ratio"]].to_dict("records") if not changed.empty else [])
                trade_summary = {
                    "source": "top10_holders_hold_change",
                    "total_records": len(latest_top10_df),
                    "buy_records": len(buy),
                    "sell_records": len(sell),
                    "latest_records": records,
                }
        # 股权质押（最新期统计；pledge_ratio 为质押比例%）
        pledge_info = None
        try:
            pledge_df = self.api.get_pledge_stat(self.ts_code)
            if pledge_df is not None and not pledge_df.empty:
                pr = pledge_df.sort_values("end_date").iloc[-1]
                pledge_info = {
                    "end_date": pr.get("end_date"),
                    "pledge_count": _safe_float(pr.get("pledge_count")),
                    "pledge_ratio": _safe_float(pr.get("pledge_ratio")),
                }
        except Exception:
            pass

        # 管理层规模与薪酬持股（最新报告期）
        managers_count = None
        rewards_info = None
        try:
            mgr_df = self.api.get_stk_managers(self.ts_code)
            if mgr_df is not None and not mgr_df.empty:
                # 仅统计在任高管（end_date 为空 = 现任记录；stk_managers 含历史离任记录）
                if "end_date" in mgr_df.columns:
                    _active = mgr_df[mgr_df["end_date"].isna() | (mgr_df["end_date"].astype(str).isin(["", "None", "nan"]))]
                    managers_count = len(_active) if not _active.empty else len(mgr_df)
                else:
                    managers_count = len(mgr_df)
            rw_df = self.api.get_stk_rewards(self.ts_code)
            if rw_df is not None and not rw_df.empty:
                lp = rw_df.sort_values("end_date").iloc[-1]["end_date"]
                lr = rw_df[rw_df["end_date"] == lp]
                rewards_info = {
                    "end_date": lp,
                    "count": len(lr),
                    # stk_rewards.reward 单位为元（非万元），换算万元需 /1e4
                    "total_reward_wan": round(pd.to_numeric(lr["reward"], errors="coerce").sum() / 1e4, 1),
                    "total_hold_vol": (round(pd.to_numeric(lr["hold_vol"], errors="coerce").sum(), 0) if pd.to_numeric(lr["hold_vol"], errors="coerce").notna().any() else None),
                }
        except Exception:
            pass

        # 前十大股东持股比例饼图（chart 管线自动注入到股东筹码节）
        _pie_data = [
            {"name": str(r.get("holder_name", "")), "value": r.get("hold_ratio")}
            for r in ((top10_summary or {}).get("records") or [])
            if isinstance(r.get("hold_ratio"), (int, float)) and r.get("hold_ratio") > 0
        ]
        data = {
            "holder_num": latest,
            "latest_qoq": latest_chg,
            "total_chg_pct": total_chg,
            "signal": signal,
            "top10_holders": top10_summary,
            "holder_trade": trade_summary,
            "pledge": pledge_info,
            "managers_count": managers_count,
            "manager_rewards": rewards_info,
            "chart": ({"title": "前十大股东持股比例分布（%）", "type": "pie", "data": _pie_data} if _pie_data else None),
        }
        conclusion = f"最新股东户数 {latest:,}，环比 {latest_chg:+.1f}%，{signal}。"
        if top10_summary:
            conclusion += f" 最新报告期前十大股东持股占比 {top10_summary['total_hold_ratio']}%"
        if trade_summary:
            # 口径必须跟着数据来源走：只有 stk_holdertrade 才是"增减持公告"口径，
            # 降级路径用的是"最新一期前十大股东的持股变动"，两者时间范围完全不同，
            # 不能都写成"近半年…公告口径"。
            if trade_summary.get("source") == "stk_holdertrade":
                conclusion += f"；近半年股东增减持公告：增持 {trade_summary['buy_records']} 次，减持 {trade_summary['sell_records']} 次"
            else:
                conclusion += (f"；最新一期前十大股东持股变动：增持 {trade_summary['buy_records']} 位，"
                               f"减持 {trade_summary['sell_records']} 位（非公告口径）")
        if pledge_info and pledge_info.get("pledge_ratio") is not None:
            _pr = pledge_info["pledge_ratio"]
            conclusion += f"；股权质押比例 {_pr}%（{int(pledge_info.get('pledge_count') or 0)} 笔）" + ("，质押比例偏高" if _pr > 30 else "")
        if rewards_info:
            conclusion += f"；现任高管 {rewards_info['count']} 人（{rewards_info['end_date']} 报告期合计薪酬 {self._fmt(rewards_info['total_reward_wan'])} 万元）"

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
        conclusion = f"未来 3 个月有 {len(df)} 笔解禁，合计 {total_float} 股（占比以总股本为分母）。"
        return DimensionResult.success("解禁压力", conclusion=conclusion, data=data)


    # ---------- 维度：两融（可选） ----------

    @safe_result("两融杠杆")
    def analyze_margin(self) -> DimensionResult:
        df = self.api.get_margin_detail(self.ts_code, self.end_date)
        if df is None or df.empty:
            # 区分"非两融标的"与"两融标的但明细缺失"（margin_secs 为盘前标的名单）
            secs_df = self.api.get_margin_secs(self.ts_code, self.end_date)
            if secs_df is None or secs_df.empty:
                return DimensionResult.empty("两融杠杆", note="无法获取两融数据（标的可能不在两融标的名单内）")
            return DimensionResult.empty("两融杠杆", note="该标的在两融标的名单内（margin_secs 有记录），但融资融券明细数据为空")

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
            "is_margin_target": True,
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

        # 近一年异常波动/严重异常波动记录
        shock_records = []
        high_shock_records = []
        try:
            s_df = self.api.get_stk_shock(self.ts_code, self.end_date)
            if s_df is not None and not s_df.empty:
                shock_records = s_df.sort_values("trade_date")[["trade_date", "reason"]].tail(10).to_dict("records")
        except Exception:
            pass
        try:
            hs_df = self.api.get_stk_high_shock(self.ts_code, self.end_date)
            if hs_df is not None and not hs_df.empty:
                high_shock_records = hs_df.sort_values("trade_date")[["trade_date", "reason"]].tail(10).to_dict("records")
        except Exception:
            pass

        # 交易所重点提示（近一年）
        alert_records = []
        try:
            al_df = self.api.get_stk_alert(self.ts_code, self.end_date)
            if al_df is not None and not al_df.empty:
                alert_records = al_df[["start_date", "end_date", "type"]].to_dict("records")
        except Exception:
            pass

        # 每日涨跌停价格（最近交易日）
        limit_price = None
        try:
            lp_df = self.api.get_stk_limit(self.ts_code, self.end_date)
            if lp_df is not None and not lp_df.empty:
                lrow = lp_df.sort_values("trade_date").iloc[-1]
                limit_price = {
                    "trade_date": lrow.get("trade_date"),
                    "pre_close": _safe_float(lrow.get("pre_close")),
                    "up_limit": _safe_float(lrow.get("up_limit")),
                    "down_limit": _safe_float(lrow.get("down_limit")),
                }
        except Exception:
            pass

        # 游资上榜明细（近120日）
        # ⚠️ 服务端 hm_detail 的真实字段是 rank_date/category/buy_sell_count/ins_name
        # （不是 trade_date/hm_name/net_amount）；净额字段为 buy_sell_count，单位：元。
        hm_records = []
        try:
            hm_df = self.api.get_hm_detail(self.ts_code, self.end_date)
            if hm_df is not None and not hm_df.empty:
                hm_df = hm_df.sort_values("rank_date")
                hm_records = [
                    {"rank_date": r.get("rank_date"), "category": r.get("category"),
                     "net_amount_billion": round(_safe_float(r.get("buy_sell_count")) / 1e8, 3) if _safe_float(r.get("buy_sell_count")) is not None else None,
                     "ins_name": r.get("ins_name")}
                    for _, r in hm_df.tail(10).iterrows()
                ]
        except Exception:
            pass

        # 停复牌（近60日：停牌为流动性事件信号）
        suspend_records = []
        try:
            su_df = self.api.get_suspend_d(self.ts_code, self.end_date)
            if su_df is not None and not su_df.empty:
                suspend_records = su_df.sort_values("trade_date")[["trade_date", "suspend_timing", "suspend_type"]].tail(10).to_dict("records")
        except Exception:
            pass

        # 区分"接口取数失败"与"确实无记录"：取数失败时绝不能把结果呈现为
        # "该股无相关异动记录"（曾因 limit_list_d/top_inst/hm_detail 字段名错误
        # 四个子项永久取不到数据，报告却写成"无记录"）。
        _failed_apis = {e["api"] for e in data_errors()}
        _probe_items = [
            ("limit_records", "limit_list_d", "涨跌停"),
            ("top_list_records", "top_list", "龙虎榜"),
            ("top_inst_records", "top_inst", "机构席位"),
            ("shock_records", "stk_shock", "异常波动"),
            ("high_shock_records", "stk_high_shock", "严重异常波动"),
            ("alert_records", "stk_alert", "交易所重点提示"),
            ("hm_records", "hm_detail", "游资上榜"),
            ("suspend_records", "suspend_d", "停复牌"),
        ]
        _failed_items = [label for _, api_name, label in _probe_items if api_name in _failed_apis]
        _present = any([
            limit_records, top_records, inst_records, shock_records,
            high_shock_records, alert_records, hm_records, suspend_records,
        ])
        if not _present:
            if _failed_items:
                note = (f"最近交易日 {latest_trade_date} 无异动记录；"
                        f"但以下子项取数失败、其结论不可信：{'、'.join(_failed_items)}"
                        "（详见页脚数据通道提示）")
            else:
                note = f"最近交易日 {latest_trade_date} 无涨跌停/龙虎榜/机构/异常波动/游资/停复牌异动记录"
            return DimensionResult.empty("市场异动", note=note)

        data = {
            "trade_date": latest_trade_date,
            "limit_records": limit_records,
            "top_list_records": top_records,
            "top_inst_records": inst_records,
            "shock_records": shock_records,
            "high_shock_records": high_shock_records,
            "alert_records": alert_records,
            "limit_price": limit_price,
            "hm_records": hm_records,
            "suspend_records": suspend_records,
            "failed_items": _failed_items,
        }
        conclusion = f"最近交易日 {latest_trade_date}："
        parts = []
        if limit_records:
            parts.append(f"涨跌停记录 {len(limit_records)} 条")
        if top_records:
            parts.append(f"龙虎榜记录 {len(top_records)} 条")
        if inst_records:
            parts.append(f"机构席位记录 {len(inst_records)} 条")
        if shock_records:
            parts.append(f"异常波动 {len(shock_records)} 条")
        if high_shock_records:
            parts.append(f"严重异常波动 {len(high_shock_records)} 条")
        if alert_records:
            parts.append(f"交易所重点提示 {len(alert_records)} 条")
        if hm_records:
            parts.append(f"游资上榜 {len(hm_records)} 条")
        if suspend_records:
            parts.append(f"停复牌记录 {len(suspend_records)} 条")
        if _failed_items:
            parts.append(f"取数失败子项：{'、'.join(_failed_items)}（结论未包含，详见页脚）")

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

        # 货币供应量（M1/M2 同比与剪刀差）
        m1_yoy = None
        m2_yoy = None
        m1_m2_gap = None
        try:
            m_df = self.api.get_cn_m(start_m, end_m)
            if m_df is not None and not m_df.empty:
                m_df = m_df.sort_values("month")
                m1_yoy = _safe_float(m_df.iloc[-1].get("m1_yoy"))
                m2_yoy = _safe_float(m_df.iloc[-1].get("m2_yoy"))
                if m1_yoy is not None and m2_yoy is not None:
                    m1_m2_gap = round(m1_yoy - m2_yoy, 2)
        except Exception:
            pass

        # Shibor 3M（近60日最新值，单位 %）
        shibor_3m = None
        try:
            shibor_df = self.api.get_shibor(shift_date(self.end_date, -60), self.end_date)
            if shibor_df is not None and not shibor_df.empty:
                shibor_df = shibor_df.sort_values("date")
                shibor_3m = _safe_float(shibor_df.iloc[-1].get("3m"))
        except Exception:
            pass

        # A股市场成交热度（最近交易日：沪市 SH_A + 深市"股票"板块，amount 单位亿元）
        market_turnover = None
        try:
            _mt_start = shift_date(self.end_date, -15)
            di_sh = self.api.get_daily_info(_mt_start, self.end_date, ts_code="SH_A")
            di_sz = self.api.get_sz_daily_info(_mt_start, self.end_date, ts_code="股票")
            sh_amt = _safe_float(di_sh.sort_values("trade_date").iloc[-1].get("amount")) if di_sh is not None and not di_sh.empty else None
            _sz_raw = _safe_float(di_sz.sort_values("trade_date").iloc[-1].get("amount")) if di_sz is not None and not di_sz.empty else None
            sz_amt = round(_sz_raw / 1e8, 2) if _sz_raw is not None else None  # sz_daily_info.amount 单位为元，换算亿元
            _mt_src = di_sh if di_sh is not None and not di_sh.empty else di_sz
            if sh_amt is not None or sz_amt is not None:
                market_turnover = {
                    "trade_date": _mt_src.sort_values("trade_date").iloc[-1].get("trade_date") if _mt_src is not None else None,
                    "sh_amount": sh_amt,
                    "sz_amount": sz_amt,
                    "total_amount": round(sh_amt + sz_amt, 0) if sh_amt is not None and sz_amt is not None else None,
                    "sh_pe": _safe_float(di_sh.sort_values("trade_date").iloc[-1].get("pe")) if di_sh is not None and not di_sh.empty else None,
                }
        except Exception:
            pass

        data = {
            "benchmark_returns": bench_ret,
            "cpi_yoy": cpi_latest,
            "cpi_trend": cpi_trend,
            "ppi_yoy": ppi_latest,
            "ppi_trend": ppi_trend,
            "lpr_1y": lpr_latest,
            "gdp": gdp_latest,
            "gdp_yoy": gdp_yoy,
            "m1_yoy": m1_yoy,
            "m2_yoy": m2_yoy,
            "m1_m2_gap": m1_m2_gap,
            "shibor_3m": shibor_3m,
            "market_turnover": market_turnover,
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

        if m1_yoy is not None and m2_yoy is not None:
            parts.append(f"M1同比 {m1_yoy}% / M2同比 {m2_yoy}%（剪刀差 {m1_m2_gap}%）")
        if shibor_3m is not None:
            parts.append(f"Shibor 3M {shibor_3m}%")
        if market_turnover and market_turnover.get("total_amount"):
            parts.append(f"A股成交额 {market_turnover['total_amount']:.0f} 亿（沪 {market_turnover['sh_amount']} + 深 {market_turnover['sz_amount']}）")

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
                risks.append(f"RSI 为 {rsi['RSI']}，高于超买阈值 70")
            if isinstance(rsi.get("RSI"), (int, float)) and rsi["RSI"] < 30:
                risks.append(f"RSI 为 {rsi['RSI']}，低于超卖阈值 30")

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
            if fc and fc.get("type") in ("预减", "首亏", "续亏", "略减") and (self._forecast_days(fc) or 999) <= 150:
                risks.append(f"业绩预告类型：{fc['type']}（报告期 {fc.get('end_date', '')}）")

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
                    risks.append(f"融资余额近5日上升 {chg:.2f}%，杠杆参与度提升")
                elif chg < -10:
                    risks.append(f"融资余额近5日下降 {chg:.2f}%，杠杆参与度回落")

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

        # 质押/审计意见/披露窗口/严重异常波动/重点提示
        if shareholder and shareholder.is_ok() and shareholder.data:
            pl = shareholder.data.get("pledge") or {}
            if isinstance(pl.get("pledge_ratio"), (int, float)) and pl["pledge_ratio"] > 30:
                risks.append(f"股权质押比例 {pl['pledge_ratio']}% 偏高（大股东资金压力信号）")
        if financial and financial.is_ok() and financial.data:
            au = financial.data.get("audit") or {}
            if au.get("audit_result") and "标准无保留" not in str(au.get("audit_result")):
                risks.append(f"审计意见非标准无保留：{au.get('audit_result')}（{au.get('end_date')} 年报）")
            nd = financial.data.get("next_disclosure")
            if nd and nd.get("pre_date"):
                try:
                    _dd = (datetime.strptime(str(nd["pre_date"]), "%Y%m%d") - datetime.strptime(self.end_date, "%Y%m%d")).days
                    if 0 <= _dd <= 30:
                        risks.append(f"下一期财报预定披露日 {nd['pre_date']}（{_dd} 天后），临近业绩事件窗口")
                except Exception:
                    pass
        if market and market.is_ok() and market.data:
            if market.data.get("high_shock_records"):
                risks.append(f"近一年严重异常波动 {len(market.data['high_shock_records'])} 次")
            if market.data.get("alert_records"):
                risks.append(f"该股被交易所重点提示 {len(market.data['alert_records'])} 次")
            if any(str(r.get("suspend_type")) == "S" for r in market.data.get("suspend_records", [])):
                risks.append(f"近60日有停牌记录（{len(market.data['suspend_records'])} 条停复牌事件），该期间流动性受限")

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
                    risks.append(f"尾部风险：{tr['risk']}，偏度 {tr.get('偏度')}，峰度(超额) {tr.get('峰度(超额)')}")
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

        # 风险分级：命中高风险关键词（非标审计/质押/严重异常波动/停牌/尾部风险）或信号较多 → 高；有信号 → 中；无 → 低
        _placeholder = risks == ["未发现显著风险信号（基于已有维度）"]
        _severe_kw = ("非标准无保留", "质押", "严重异常波动", "停牌", "尾部风险较高", "尾部风险高")
        _has_severe = any(any(k in r for k in _severe_kw) for r in risks)
        if _has_severe or (not _placeholder and len(risks) >= 5):
            risk_level = "高"
        elif not _placeholder:
            risk_level = "中"
        else:
            risk_level = "低"
        return DimensionResult.success("风险提示", data={"risks": risks, "notes": notes, "risk_level": risk_level, "risk_count": 0 if _placeholder else len(risks)}, risks=risks)

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

    # 各维度含义描述（面向非专业读者，标题后以引用块呈现，区别于数据描述句）
    DIM_DESCRIPTIONS = {
        "overview": "标的基础档案：代码、行业、上市地、主业与规模，回答“这是什么公司”。",
        "trend": "价格走势与动量：区间涨跌幅、波动率、回撤、技术指标与基准对比，判断趋势方向与风险水平。",
        "valuation": "估值水平：PE/PB/股息率及其历史分位与行业截面分位，判断“贵不贵”。",
        "financial": "财务质量：ROE、毛利率、负债率、增速与 F-Score，判断“赚不赚钱、稳不稳”。",
        "moneyflow": "资金动向：主力/超大单/北向资金净流入，判断大资金在买还是卖。",
        "shareholder": "股东筹码：户数变化、前十大持股与大股东增减持，判断筹码集中度与机构动向。",
        "float": "解禁压力：未来3个月限售股释放计划，判断潜在的供给冲击。",
        "margin": "两融杠杆：融资融券余额变化，反映市场杠杆情绪与杠杆资金进出方向。",
        "market_activity": "市场异动：涨跌停、龙虎榜与机构席位，捕捉短期资金的极端关注。",
        "macro": "宏观环境：大盘走势、CPI/PPI、LPR 与 GDP，判断整体顺风或逆风。",
        "risk": "风险提示：汇总各维度风险信号，给出综合风险分级。",
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
            else:  # 兜底：pandas 3.x 新 str dtype 不属于 object，也需清洗 NaN
                df[col] = df[col].apply(
                    lambda x: self._fmt(x) if isinstance(x, (int, float, np.integer, np.floating))
                    else ("N/A" if x is None or (isinstance(x, float) and x != x) else str(x))
                )
        return df_to_md_table(df)

    def _forecast_days(self, fc):
        """业绩预告距今天的自然日数；ann_date 缺失或解析失败返回 None。"""
        ann = fc.get("ann_date") if isinstance(fc, dict) else None
        if not ann:
            return None
        try:
            return (datetime.strptime(self.end_date, "%Y%m%d") - datetime.strptime(ann, "%Y%m%d")).days
        except Exception:
            return None

    def _forecast_label(self, fc):
        """业绩预告带报告期/公告日的展示标签，超 150 天未更新标注可能过期。"""
        end = fc.get("end_date")
        label = f"业绩预告（报告期 {end or '?'}"
        days = self._forecast_days(fc)
        if days is not None:
            label += f"，{fc.get('ann_date')} 公告"
            if days > 150:
                label += "，⚠️ 距今已超 150 天，期间或未发布新预告"
        label += "）"
        return label

    def _forecast_stale(self, fc):
        """预告距今超过 150 天视为过期（期间公司未再发新预告）。"""
        return (self._forecast_days(fc) or 999) > 150

    def _annualized_roe(self, financial):
        """按报告期年化 ROE（一季报×4 / 中报×2 / 三季报×4/3 / 年报×1），避免半年口径误判盈利强弱。"""
        roe = self._f(self._v(financial.data, "latest", "roe"))
        if roe is None:
            return None
        m = str((financial.data.get("latest") or {}).get("end_date", ""))[4:6]
        return roe * {"03": 4, "06": 2, "09": 4 / 3}.get(m, 1)

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
        _dts = self.results.get("trend")
        _ds = _dts.data.get("chart", {}).get("dates") if (_dts and _dts.is_ok() and _dts.data) else None
        lines = [
            f"# {name} 全景研究报告",
            "",
            f"> 数据日期：{_ds[-1] if _ds else self.end_date}（Tushare 数据为 T-1 日，即最新已发布数据）",
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
            desc = self.DIM_DESCRIPTIONS.get(dim)
            if desc:
                lines.append(f"> {desc}")
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
                _rl = res.data.get("risk_level")
                if _rl:
                    lines.append(f"**风险等级**：{_rl}（{res.data.get('risk_count', 0)} 项信号）")
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
        lines.append("*本报告由AI基于山西证券Tushare平台数据自动生成，所有内容均为 T-1 日历史数据的客观统计与描述，不含对证券价格走势的预测、判断或方向性建议，不构成任何投资建议、要约或财务指导。*")
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
            beta = self._f(self._v(trend.data.get("beta_alpha") or {}, "Beta"))
            _r2_style = self._f((trend.data.get("beta_alpha") or {}).get("R2"))
            if beta is not None and _r2_style is not None and _r2_style < 0.3:
                beta = None  # 回归解释力弱，Beta 不作为风格判定依据
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
            roe = self._annualized_roe(financial)
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
            _ba_all = trend.data.get("beta_alpha") or {}
            _r2pt = self._v(_ba_all, "R2")
            _beta_note = f"（R²={self._fmt(_r2pt)}，回归解释力弱，仅供参考）" if (_r2pt != "N/A" and self._f(_r2pt) is not None and self._f(_r2pt) < 0.3) else ""
            points.append(f"- **趋势**：近250日 {self._fmt(ret250)}%，近20日 {self._fmt(ret20)}%，年化波动 {self._fmt(vol)}%，最大回撤 {self._fmt(mdd)}%，夏普 {self._fmt(sharpe)}，Beta {self._fmt(self._v(_ba_all, 'Beta'))}{_beta_note}")
        if val_ok:
            pe = self._v(valuation.data, "pe_ttm")
            pb = self._v(valuation.data, "pb")
            pe_hist = self._v(valuation.data, "pe_hist_percentile")
            pb_hist = self._v(valuation.data, "pb_hist_percentile")
            dv = self._v(valuation.data, "dividend_yield")
            ind_name = self._v(valuation.data, "industry", "industry_name")
            pe_pct = self._v(valuation.data, "industry", "pe_percentile")
            # 裸 N/A 替换为可解释文案：PE 缺失多为亏损/负收益（PE 无意义）
            _pe_txt = self._fmt(pe) if pe is not None else "无数据（亏损或负收益时 PE 无意义）"
            _pe_hist_txt = f"{self._fmt(pe_hist)}%" if pe_hist is not None else "无数据"
            _pb_hist_txt = f"{self._fmt(pb_hist)}%" if pb_hist is not None else "无数据"
            _dv_txt = self._fmt(dv) if dv is not None else "无数据"
            points.append(f"- **估值**：PE {_pe_txt}（历史分位 {_pe_hist_txt}），PB {self._fmt(pb)}（历史分位 {_pb_hist_txt}），股息率 {_dv_txt}%" + (f"，同行业({ind_name})截面分位 {self._fmt(pe_pct)}%" if pe_pct != "N/A" else ""))
        if fin_ok:
            roe = self._v(financial.data, "latest", "roe")
            debt = self._v(financial.data, "latest", "debt_to_assets")
            npm = self._v(financial.data, "latest", "netprofit_margin")
            g = financial.data.get("growth") or {}
            rev_yoy = self._v(g, "revenue_yoy")
            prof_yoy = self._v(g, "profit_yoy")
            fc = financial.data.get("forecast") or {}
            if fc and fc.get("type"):
                if self._forecast_stale(fc):
                    fc_text = (f"，业绩预告已过期（{fc.get('ann_date')} 公告，报告期 {fc.get('end_date')}），不作为当前参考")
                else:
                    fc_text = f"，{self._forecast_label(fc)}({fc.get('p_change_min','')}%~{fc.get('p_change_max','')}%)"
            else:
                fc_text = ""
            points.append(f"- **财务**：ROE {self._fmt(roe)}%，净利率 {self._fmt(npm)}%，资产负债率 {self._fmt(debt)}%，营收同比 {self._fmt(rev_yoy)}%，净利润同比 {self._fmt(prof_yoy)}%{fc_text}")
        if mf_ok:
            net5 = self._v(moneyflow.data, "net_inflow_5d_billion")
            net20 = self._v(moneyflow.data, "net_inflow_20d_billion")
            main5 = self._v(moneyflow.data, "main_net_5d_billion")
            # 全口径与主力口径分开列出，避免把 net_mf_amount 当成"主力资金"
            points.append(f"- **资金**：近5日全口径净流入 {self._fmt(net5)}亿，近20日 {self._fmt(net20)}亿；"
                          f"主力口径（大单+超大单）近5日 {self._fmt(main5)}亿")
        if sh_ok:
            signal = self._v(shareholder.data, "signal")
            ht = shareholder.data.get("holder_trade") or {}
            if ht:
                _scope = "公告口径" if ht.get("source") == "stk_holdertrade" else "前十大持股变动口径"
                ht_text = f"，股东增持{ht.get('buy_records',0)}次/减持{ht.get('sell_records',0)}次（{_scope}）"
            else:
                ht_text = ""
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
            beta = self._f(self._v(trend.data.get("beta_alpha") or {}, "Beta"))
            _r2_style = self._f((trend.data.get("beta_alpha") or {}).get("R2"))
            if beta is not None and _r2_style is not None and _r2_style < 0.3:
                beta = None  # 回归解释力弱，Beta 不作为风格判定依据
            vol = self._f(self._v(trend.data, "volatility"))
            if beta is not None and beta < 0.8 and vol is not None and vol < 25:
                style_parts.append("防御型")
            elif beta is not None and beta > 1.2:
                style_parts.append("进攻型/高弹性")
        if fin_ok:
            roe = self._annualized_roe(financial)
            if roe is not None and roe > 15:
                style_parts.append("高盈利质量")
            elif roe is not None and roe < 5:
                style_parts.append("盈利偏弱")

        if style_parts:
            lines.append("")
            _st = "/".join(style_parts)
            _st = _st[:-1] if _st.endswith("型") else _st  # 元素自带"型"后缀，避免"防御型型资产"
            lines.append(f"**风格定位**：{name}属于{_st}型资产。")

        # ---- 结论 ----
        concl_parts = []
        if val_ok and trend_ok:
            pe_hist = self._f(self._v(valuation.data, "pe_hist_percentile"))
            ret250 = self._f(self._v(trend.data, "returns", "近250日涨幅%"))
            if pe_hist is not None and pe_hist < 30:
                concl_parts.append(f"PE 处于近5年历史分位 {pe_hist}%")
            elif pe_hist is not None and pe_hist > 70:
                concl_parts.append(f"PE 处于近5年历史分位 {pe_hist}%")
            if ret250 is not None and ret250 < -10:
                concl_parts.append("近期走势偏弱")
            elif ret250 is not None and ret250 > 20:
                concl_parts.append("近期走势较强")
        if fin_ok:
            debt = self._f(self._v(financial.data, "latest", "debt_to_assets"))
            if debt is not None and debt > 80:
                concl_parts.append("资产负债率偏高")
            fc = financial.data.get("forecast") or {}
            if fc and fc.get("type") in ("预增", "略增") and (self._forecast_days(fc) or 999) <= 150:
                concl_parts.append(f"业绩预告类型：{fc.get('type')}（报告期 {fc.get('end_date', '')}）")
        if mf_ok:
            net5 = self._f(self._v(moneyflow.data, "net_inflow_5d_billion"))
            if net5 is not None and abs(net5) > 2:
                concl_parts.append(f"近5日全口径净流入 {net5}亿")

        # ---- 因子综合评分 + 三维定位 + 风险预算（分析输入，置于结论之前）----
        try:
            pe_hist = self._f(self._v(valuation.data, "pe_hist_percentile")) if val_ok else None
            fscore = self._f(self._v(financial.data.get("fscore") or {}, "F-Score")) if fin_ok else None
            ret250 = self._f(self._v(trend.data, "returns", "近250日涨幅%")) if trend_ok else None
            sharpe = self._f(self._v(trend.data, "sharpe")) if trend_ok else None

            composite = calc_composite_score(pe_hist, fscore, ret250, sharpe, None)
            if composite.get("composite") is not None:
                lines.append("")
                lines.append(f"**因子综合评分**：{composite['composite']}（{composite['rating']}，等权雏形非IC优化，仅供参考）")
                fac = composite.get("factors", {})
                if fac:
                    lines.append(f"- 各维子分：{' / '.join(f'{k}{v}' for k, v in fac.items())}")

            positioning = calc_factor_positioning(pe_hist, fscore, ret250)
            if positioning.get("positioning"):
                lines.append("")
                lines.append(f"**因子定位**：{positioning['positioning']}")

            if trend_ok:
                var_cvar = trend.data.get("var_cvar") or {}
                var95_str = var_cvar.get("VaR(95%)")
                var95 = self._f(str(var95_str).rstrip("%")) if isinstance(var95_str, str) else None
                mdd = self._f(self._v(trend.data, "max_drawdown"))
                beta = self._f(self._v(trend.data.get("beta_alpha") or {}, "Beta"))
                amihud = self._f(trend.data["amihud"].get("Amihud非流动性")) if trend.data.get("amihud") else None
                vol = self._f(self._v(trend.data, "volatility"))
                budget = calc_risk_budget(var95, mdd, beta, amihud, vol)
                if budget.get("risk_level"):
                    lines.append("")
                    # 合规：只列风险因子的客观测算值与定性等级，不给出仓位/配置比例
                    lines.append(f"**风险因子测度**：风险等级 {budget['risk_level']}（由回撤/波动/Beta/流动性/VaR 等因子综合测算，为数据统计结果，不含仓位或配置建议）")
                    for r in budget.get("reasons", []):
                        lines.append(f"- {r}")
        except Exception:
            pass

        if concl_parts:
            lines.append("")
            lines.append(f"**数据汇总**：{name}{'，'.join(concl_parts)}。以上均为 T-1 历史数据的统计描述，不含对标的方向性判断或操作建议。")

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
                "former_names": "曾用名", "ipo_info": "次新股信息",
            }
            rows = []
            for k, v in data.items():
                if v is None or str(v) == "nan" or k in ("mainbz_period", "mainbz_top3"):
                    continue
                if k == "list_date" and str(v).isdigit() and len(str(v)) == 8:
                    v = f"{str(v)[:4]}-{str(v)[4:6]}-{str(v)[6:]}"
                elif k == "exchange":
                    v = {"SSE": "上交所（沪市）", "SZSE": "深交所（深市）", "BSE": "北交所"}.get(str(v), v)
                elif k == "list_status":
                    v = {"L": "上市", "D": "退市", "P": "暂停上市"}.get(str(v), v)
                elif k == "reg_capital":
                    try:
                        v = f"{float(v) / 1e4:.2f}亿"
                    except Exception:
                        pass
                elif k == "employees":
                    try:
                        v = f"{int(v):,}"
                    except Exception:
                        pass
                rows.append({"项目": label_map.get(k, k), "内容": str(v)})
            if rows:
                lines.append(self._md(pd.DataFrame(rows)))
            mainbz_rows = data.get("mainbz_top3") or []
            if mainbz_rows:
                lines.append(f"\n**主营业务构成（{data.get('mainbz_period', 'N/A')} 报告期，按产品，收入前三）**：")
                lines.append(self._md(pd.DataFrame(mainbz_rows)))

        elif res.title == "行情趋势":
            # 涨跌幅对比表（标的 + 基准）
            returns = data.get("returns", {})
            bench = data.get("benchmark") or {}
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
            # 最新价同时给未复权与后复权口径，避免与后复权 MA 混用造成误读
            if data.get("latest_close_unadj") is not None:
                price_note = f"\n最新价：未复权 {self._fmt(data['latest_close_unadj'])}"
                if data.get("latest_close_adj") is not None:
                    price_note += f"，后复权 {self._fmt(data['latest_close_adj'])}"
                price_note += "（下表均线、技术指标均为后复权口径）"
                lines.append(price_note)

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
            vr = data.get("volume_ratio") or {}
            if vr:
                tech_rows.append({"技术指标": "量比", "数值/信号": f"{vr.get('量比','N/A')}({vr.get('signal','')})"})
            if tech_rows:
                lines.append("")
                lines.append(self._md(pd.DataFrame(tech_rows)))

            # 进阶指标
            adv = []
            ba = data.get("beta_alpha") or {}
            if ba:
                _r2 = ba.get("R2")
                _beta_txt = f"Beta {ba.get('Beta','N/A')}"
                if _r2 is not None:
                    _beta_txt += f"（R²={_r2}，回归解释力弱，Beta 估计不可靠，仅供参考）" if _r2 < 0.3 else f"（R²={_r2}）"
                adv.append(_beta_txt + f" | Alpha(年化%) {ba.get('Alpha(年化%)','N/A')}")
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
                try:
                    _rsv = "跑赢" if float(rs.get("RS", 0)) > 1 else "跑输"
                except Exception:
                    _rsv = ""
                adv.append(f"相对强度RS {rs.get('RS','N/A')}（{_rsv}，趋势{rs.get('trend','')}）")
            if data.get("tail_risk"):
                tr = data["tail_risk"]
                adv.append(f"尾部风险: 偏度{tr.get('偏度','N/A')}, 峰度{tr.get('峰度(超额)','N/A')}")
            if data.get("amihud"):
                am = data["amihud"]
                _am_raw = am.get("Amihud非流动性", "N/A")
                try:
                    _am_txt = f"{float(_am_raw):.6f}".rstrip("0").rstrip(".") if isinstance(_am_raw, (int, float)) else str(_am_raw)
                except Exception:
                    _am_txt = str(_am_raw)
                adv.append(f"Amihud非流动性 {_am_txt}（{am.get('interpretation', '')}）")
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
            ind = data.get("industry") or {}
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
            dv3 = data.get("dividends")
            if dv3:
                lines.append(f"\n**分红记录（近3年实施，最近5次）**：共 {dv3.get('count_3y')} 次，累计每股分红（税前）{self._fmt(dv3.get('total_cash_div_3y'))} 元")
                lines.append(self._md(pd.DataFrame(dv3.get("records", [])).rename(columns={"ex_date": "除权除息日", "cash_div_tax": "每股分红(税前,元)", "stk_div": "每股送转"})))
            lines.append("")
            lines.append(self._valuation_eval(data))

        elif res.title == "财务质量":
            import pandas as pd
            trend_list = data.get("trend", [])
            if trend_list:
                lines.append("**近8期财务指标（累计口径，未年化：一/三季报与中报 ROE 与年报不可直接比较）**")
                lines.append(self._md(pd.DataFrame(trend_list)))
            g = data.get("growth") or {}
            if g:
                lines.append(f"\n**营收/净利润增速**：最新 {g.get('latest_end_date','N/A')} vs 上年同期 {g.get('yoy_end_date','N/A')}")
                g_rows = [
                    {"指标": "营收同比%", "数值": self._fmt(g.get("revenue_yoy"))},
                    {"指标": "净利润同比%", "数值": self._fmt(g.get("profit_yoy"))},
                ]
                lines.append(self._md(pd.DataFrame(g_rows)))
            fs = data.get("fscore") or {}
            if fs:
                lines.append(f"\n**Piotroski F-Score**：{fs.get('F-Score','N/A')}（{fs.get('rating','N/A')}），有效项 {fs.get('有效项','N/A')}/{(fs.get('有效项',0) or 0) + (fs.get('缺失项',0) or 0)}")
                if fs.get("comp_note"):
                    lines.append(f"- {fs['comp_note']}")
            fc = data.get("forecast") or {}
            if fc:
                if self._forecast_stale(fc):
                    lines.append(f"- **业绩预告（已过期）**：最近一条发布于 {fc.get('ann_date')}（报告期 {fc.get('end_date')}），距今超 150 天未更新，不作为当前业绩参考")
                else:
                    lines.append("\n**业绩预告**：")
                    fc_rows = [{"指标": k, "数值": str(v)} for k, v in fc.items()]
                    lines.append(self._md(pd.DataFrame(fc_rows)))
            ex = data.get("express")
            if ex:
                lines.append("\n**业绩快报**：")
                ex_rows = [{"指标": k, "数值": str(v)} for k, v in ex.items()]
                lines.append(self._md(pd.DataFrame(ex_rows)))
            au = data.get("audit")
            if au:
                lines.append(f"\n**审计意见**：{au.get('audit_result', 'N/A')}（{au.get('end_date')} 报告期，{au.get('ann_date')} 公告，机构：{au.get('audit_agency', 'N/A')}）")
            nd = data.get("next_disclosure")
            if nd:
                lines.append(f"\n**下一期财报预定披露**：{nd.get('pre_date')}（报告期 {nd.get('end_date')}）")
            lines.append("")
            lines.append(self._financial_eval(data))

        elif res.title == "资金面":
            import pandas as pd
            _north = data.get("north_inflow_20d_million")
            rows = [
                {"口径": "近5日全口径净流入(亿)", "数值": self._fmt(data.get("net_inflow_5d_billion"))},
                {"口径": "近20日全口径净流入(亿)", "数值": self._fmt(data.get("net_inflow_20d_billion"))},
                {"口径": "近5日主力净流入(亿，大单+超大单)", "数值": self._fmt(data.get("main_net_5d_billion"))},
                {"口径": "近20日主力净流入(亿，大单+超大单)", "数值": self._fmt(data.get("main_net_20d_billion"))},
                {"口径": "超大单近5日净流入(亿)", "数值": self._fmt(data.get("elg_net_5d_billion"))},
                {
                    "口径": "北向近20日净流入(百万)",
                    "数值": self._fmt(_north) if _north is not None else "不可用",
                },
            ]
            lines.append(self._md(pd.DataFrame(rows)))
            if _north is None:
                lines.append(
                    "\n> **北向资金口径说明**：沪深港通日度净流入自 2024-08 起停止披露，"
                    "`moneyflow_hsgt` 接口数据止于 2024-08-16，近期无可用口径。"
                    "属数据源停更，非权限不足或参数错误；北向动向可用沪深股通十大成交股上榜情况作替代观察。"
                )
            bt = data.get("block_trade") or {}
            if bt:
                lines.append(f"\n**大宗交易（近60日）**：共 {bt.get('count','N/A')} 笔，合计 {self._fmt(bt.get('total_amount_billion'))} 亿元，均价 {self._fmt(bt.get('avg_price'))} 元")
            ht10 = data.get("hsgt_top10")
            if ht10:
                _hna = ht10.get("net_amount_billion")
                _hna_txt = (
                    f"{self._fmt(_hna)} 亿元"
                    if _hna is not None
                    else "金额未披露（接口近期不再返回 net_amount 字段）"
                )
                lines.append(f"\n**沪深股通十大成交股（近60日）**：上榜 {ht10.get('count_60d','N/A')} 次，最新上榜日 {ht10.get('latest_trade_date','N/A')}，当日排名 {self._fmt(ht10.get('rank'))}，净买入 {_hna_txt}")
            rep = data.get("repurchase")
            if rep:
                _rp_amt = rep.get("total_amount_billion")
                if _rp_amt is not None:
                    _rp_txt = f"{self._fmt(_rp_amt)} 亿元" + ("（累计）" if rep.get("is_cumulative") else "")
                else:
                    _rp_txt = "暂无已实施金额（公告处于预案/股东大会通过阶段）"
                _rp_plan = rep.get("plan_amount_billion")
                _rp_tail = f"最新进度：{rep.get('latest_proc','N/A')}，{rep.get('latest_ann_date','N/A')} 公告"
                if _rp_plan is not None:
                    _rp_tail = f"另有预案计划 {self._fmt(_rp_plan)} 亿元（未实施）；" + _rp_tail
                lines.append(f"\n**股票回购（近一年）**：已实施 {rep.get('count_1y', 0)} 次，{_rp_txt}；{_rp_tail}")
            br = data.get("broker_recommend")
            if br:
                if br.get("count_3m"):
                    lines.append(f"\n**券商月度金股（近3个月）**：入选 {br.get('count_3m')} 次，券商：{'、'.join(br.get('brokers', []))}")
                else:
                    lines.append("\n**券商月度金股（近3个月）**：未入选")
            lines.append("")
            lines.append(self._moneyflow_eval(data))

        elif res.title == "股东筹码":
            import pandas as pd
            # 顺序：前十大股东（饼图紧随其后）→ 股东户数 → 增减持/质押
            t10 = data.get("top10_holders", {})
            if t10 and t10.get("records"):
                lines.append(f"\n**前十大股东（{t10.get('period','N/A')}）**：合计持股 {t10.get('total_hold_ratio','N/A')}%")
                lines.append("<!--chart:股东筹码-->")
                lines.append(self._md(pd.DataFrame(t10["records"]).rename(columns={"holder_name": "股东名称", "hold_ratio": "持股比例%", "hold_change": "持股变动(股)"})))
            rows = [
                {"指标": "最新股东户数", "数值": self._fmt(data.get("holder_num"))},
                {"指标": "环比变化", "数值": f"{self._fmt(data.get('latest_qoq'))}%"},
                {"指标": "中期变化", "数值": f"{self._fmt(data.get('total_chg_pct'))}%"},
                {"指标": "信号", "数值": data.get("signal", "N/A")},
            ]
            lines.append("")
            lines.append("**股东户数（筹码集中度）**：")
            lines.append(self._md(pd.DataFrame(rows)))
            ht = data.get("holder_trade") or {}
            if ht:
                _src_note = "增减持公告口径" if ht.get("source") == "stk_holdertrade" else "最新一期前十大持股变动口径"
                lines.append(f"\n**大股东增减持（{_src_note}）**：总记录 {ht.get('total_records','N/A')}，增持 {ht.get('buy_records','N/A')} 次，减持 {ht.get('sell_records','N/A')} 次")
                if ht.get("latest_records"):
                    lines.append(self._md(pd.DataFrame(ht["latest_records"]).rename(columns={"holder_name": "股东名称", "hold_change": "持股变动(股)", "hold_ratio": "持股比例%"})))
            pl = data.get("pledge")
            if pl:
                _pl_high = isinstance(pl.get("pledge_ratio"), (int, float)) and pl.get("pledge_ratio") > 30
                lines.append(f"\n**股权质押（{pl.get('end_date', 'N/A')}）**：质押 {self._fmt(pl.get('pledge_count'), digits=0)} 笔，质押比例 {self._fmt(pl.get('pledge_ratio'))}%" + ("（偏高）" if _pl_high else ""))
            mr = data.get("manager_rewards")
            if mr:
                _hold = f"，合计持股 {self._fmt(mr.get('total_hold_vol'), digits=0)} 股" if mr.get("total_hold_vol") is not None else "（持股数据未披露）"
                lines.append(f"**管理层（{mr.get('end_date')} 报告期）**：披露 {mr.get('count')} 人，合计薪酬 {self._fmt(mr.get('total_reward_wan'))} 万元{_hold}")
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
                {"指标": "融券余量(股)", "数值": self._fmt(data.get("rqyl"))},
                {"指标": "近5日融资余额变化%", "数值": self._fmt(data.get("rzye_chg_5d_pct"))},
                {"指标": "两融标的", "数值": "是" if data.get("is_margin_target") else "否/未确认"},
            ]
            lines.append(self._md(pd.DataFrame(rows)))
            lines.append("")
            lines.append(self._margin_eval(data))

        elif res.title == "市场异动":
            lines.append(f"**交易日**：{data.get('trade_date', 'N/A')}")
            import pandas as pd
            for key, label in [("limit_records", "涨跌停"), ("top_list_records", "龙虎榜"), ("top_inst_records", "机构席位"), ("shock_records", "异常波动"), ("high_shock_records", "严重异常波动"), ("alert_records", "交易所重点提示"), ("hm_records", "游资上榜"), ("suspend_records", "停复牌")]:
                records = data.get(key, [])
                if records:
                    lines.append(f"\n**{label}**：")
                    lines.append(self._md(pd.DataFrame(records)))
            lp = data.get("limit_price")
            if lp:
                lines.append(f"\n**涨跌停价格（{lp.get('trade_date', 'N/A')}）**：前收 {self._fmt(lp.get('pre_close'))} 元，涨停 {self._fmt(lp.get('up_limit'))} 元，跌停 {self._fmt(lp.get('down_limit'))} 元")
            if data.get("failed_items"):
                lines.append(f"\n> **取数失败子项**：{'、'.join(data['failed_items'])} —— 以下章节未包含这些子项，"
                             "请勿据此判断'该股无相关记录'（详见页脚数据通道提示）。")
            if not any(data.get(k) for k in ["limit_records", "top_list_records", "top_inst_records", "shock_records", "high_shock_records", "alert_records", "hm_records", "suspend_records"]):
                lines.append("- 无异动记录")

        elif res.title == "宏观环境":
            import pandas as pd
            bench_ret = data.get("benchmark_returns", {}) or {}
            rows = [
                {"指标": "沪深300近20日%", "数值": self._fmt(bench_ret.get("近20日涨幅%"))},
                {"指标": "CPI同比%", "数值": self._fmt(data.get("cpi_yoy"))},
                {"指标": "CPI趋势", "数值": data.get("cpi_trend") or "N/A"},
                {"指标": "PPI同比%", "数值": self._fmt(data.get("ppi_yoy"))},
                {"指标": "PPI趋势", "数值": data.get("ppi_trend") or "N/A"},
                {"指标": "1年期LPR%", "数值": self._fmt(data.get("lpr_1y"))},
                {"指标": "GDP当季同比%", "数值": self._fmt(data.get("gdp_yoy"))},
                {"指标": "M1同比%", "数值": self._fmt(data.get("m1_yoy"))},
                {"指标": "M2同比%", "数值": self._fmt(data.get("m2_yoy"))},
                {"指标": "M1-M2剪刀差%", "数值": self._fmt(data.get("m1_m2_gap"))},
                {"指标": "Shibor 3M%", "数值": self._fmt(data.get("shibor_3m"))},
            ]
            lines.append(self._md(pd.DataFrame(rows)))
            mt = data.get("market_turnover")
            if mt:
                lines.append(f"\n**A股市场热度（{mt.get('trade_date', 'N/A')}）**：总成交 {self._fmt(mt.get('total_amount'))} 亿（沪 {self._fmt(mt.get('sh_amount'))} + 深 {self._fmt(mt.get('sz_amount'))}），沪市平均PE {self._fmt(mt.get('sh_pe'))}")
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
        ind_pe = (data.get("industry") or {}).get("pe_median")
        parts = []
        try:
            pe_f = float(pe_hist)
            pb_f = float(pb_hist)
            vert = '偏低' if pe_f < 30 and pb_f < 30 else '偏高' if pe_f > 70 or pb_f > 70 else '中等'
            parts.append(f"纵向历史看，PE分位{pe_f}%、PB分位{pb_f}%，历史估值{vert}；")
        except Exception:
            pass
        try:
            pe_v = float(pe)
            ind_v = float(ind_pe)
            horiz = f"与行业中位数比值 {pe_v / ind_v:.2f}倍" if ind_v else ""
            parts.append(f"横向行业截面看，当前PE {pe_v} {'高于' if pe_v > ind_v else '低于'}行业中位数 {ind_v}" + (f"，{horiz}。" if horiz else "。"))
        except Exception:
            pass
        # 高ROE个股的PB/PE截面背离解读：PB截面分位偏高可能由高ROE支撑，不构成高估证据
        _ind_pb = (data.get("industry") or {}).get("pb_percentile")
        _fin_res = self.results.get("financial")
        _own_roe = self._f(self._v(_fin_res.data, "latest", "roe")) if _fin_res and _fin_res.is_ok() else None
        try:
            if _own_roe is not None and _ind_pb is not None and float(_ind_pb) >= 80 and pe_hist is not None and float(pe_hist) < 30:
                parts.append(f"本股ROE {_own_roe}% 与同业存在差异，PB 行业截面分位 {self._fmt(_ind_pb)}% 与 PE 截面分位口径不一致；PB 与 PE 之比约等于 ROE，高 ROE 标的的 PB 天然高于同业，跨行业比较时 PE 口径可比性更强。")
        except Exception:
            pass

        parts.append("纵向历史位与横向板块位反映不同口径，二者背离通常源于板块整体估值水位变化。")
        return "**分析评价**：" + "".join(parts)

    def _financial_eval(self, data):
        fs = data.get("fscore") or {}
        fscore = fs.get("F-Score")
        g = data.get("growth") or {}
        rev = g.get("revenue_yoy")
        prof = g.get("profit_yoy")
        parts = []
        if fscore is not None:
            parts.append(f"Piotroski F-Score {fscore}分（{'偏强' if fscore >= 7 else '偏弱' if fscore <= 2 else '中等'}）。")
        try:
            rev_f = float(rev)
            prof_f = float(prof)
            parts.append(f"营收同比 {rev_f}%、净利润同比 {prof_f}%（同比增速为报告期口径）。")
        except Exception:
            pass
        comp = data.get("latest", {}).get("grossprofit_margin")
        if comp is None or comp != comp:
            parts.append("金融机构毛利率/资产周转率等指标语义与工商业不同，F-Score仅作弱参考。")
        return "**分析评价**：" + "".join(parts)

    def _moneyflow_eval(self, data):
        net5 = data.get("net_inflow_5d_billion")
        net20 = data.get("net_inflow_20d_billion")
        main5 = data.get("main_net_5d_billion")
        main20 = data.get("main_net_20d_billion")
        parts = []
        try:
            n5 = float(net5)
            # 口径必须写清：net_mf_amount 是服务端独立计算的全口径净流入，
            # 不等于大单+超大单，原实现误标为"主力单净流入"。
            parts.append(f"近5日全口径净流入 {n5}亿。")
        except Exception:
            pass
        try:
            m5 = float(main5)
            _opposite = ""
            try:
                if float(net5) * m5 < 0:
                    _opposite = "（与全口径方向相反）"
            except Exception:
                pass
            parts.append(f"主力口径（大单+超大单）近5日净流入 {m5}亿{_opposite}。")
        except Exception:
            pass
        try:
            n20 = float(net20)
            n5 = float(net5)
            parts.append(f"全口径近20日累计 {n20}亿，与近5日方向{'一致' if n5 * n20 >= 0 else '背离'}。")
        except Exception:
            pass
        try:
            _m5, _m20 = float(main5), float(main20)
            parts.append(f"主力口径近20日累计 {_m20}亿（方向{'一致' if _m5 * _m20 >= 0 else '背离'}）。")
        except Exception:
            pass
        _ht10 = data.get("hsgt_top10")
        if _ht10 and _ht10.get("count_60d"):
            parts.append(f"北向活跃度：近60日上榜沪深股通十大成交股 {_ht10['count_60d']} 次（可作为北向交易活跃度的替代口径）。")

        parts.append("资金流向为短期统计口径，不含对后续资金方向或股价方向的判断。")
        return "**分析评价**：" + "".join(parts)

    def _shareholder_eval(self, data):
        signal = data.get("signal", "")
        trade = data.get("holder_trade") or {}
        parts = []
        # 合规：仅陈述户数与股价的同期数值关系，不推断资金主体意图（吸筹/派发）
        # 户数变化与股价走势为相关性观察，涨跌之间不存在已证实的因果关系
        if "中期筹码趋于集中" in signal:
            qoq = self._f(data.get("latest_qoq")) or 0
            parts.append(f"中期户数累计 {self._fmt(data.get('total_chg_pct'))}%，人均持股相应上升；最新一期环比 {self._fmt(data.get('latest_qoq'))}%，{'继续下降' if qoq < 0 else '有所回升'}。户数变化反映持股分布的集中度，与资金主体意图无已证实关联。")
        elif "中期筹码趋于分散" in signal:
            _trend_res = self.results.get("trend")
            _chg60 = self._f(self._v(_trend_res.data, "returns", "近60日涨幅%")) if _trend_res and _trend_res.is_ok() else None
            _base = f"中期户数累计 {self._fmt(data.get('total_chg_pct'))}%，人均持股相应下降；最新一期环比 {self._fmt(data.get('latest_qoq'))}%"
            parts.append(_base + (f"；同期近60日股价涨幅 {_chg60}%。户数与股价为同期并列观测值，不构成因果推断。" if _chg60 is not None else "。户数与股价为同期并列观测值，不构成因果推断。"))
        elif "集中" in signal:
            parts.append("股东户数减少，人均持股相应上升，持股分布集中度提高。户数与股价为同期并列观测值，不构成因果推断。")
        elif "分散" in signal:
            _trend_res = self.results.get("trend")
            _chg60 = self._f(self._v(_trend_res.data, "returns", "近60日涨幅%")) if _trend_res and _trend_res.is_ok() else None
            _tail = f"同期近60日股价涨幅 {_chg60}%。" if _chg60 is not None else ""
            parts.append("股东户数增加，人均持股相应下降，持股分布集中度降低。" + _tail + "户数与股价为同期并列观测值，不构成因果推断。")
        else:
            parts.append("股东户数变化幅度有限，持股分布集中度相对稳定。")
        if trade:
            buy = trade.get("buy_records", 0) or 0
            sell = trade.get("sell_records", 0) or 0
            _rel = "增持笔数多于减持笔数" if buy > sell else "减持笔数多于增持笔数" if sell > buy else "增减笔数持平"
            # 口径必须随数据来源变化：降级路径不是公告口径，不能写成"公告口径下"
            if trade.get("source") == "stk_holdertrade":
                parts.append(f"近半年股东增减持公告 {buy + sell} 条，增持 {buy} 次、减持 {sell} 次，{_rel}。")
            else:
                parts.append(f"最新一期前十大股东中 {buy + sell} 位持股发生变动（增持 {buy} 位、减持 {sell} 位），"
                             f"{_rel}；此为定期报告前十大股东口径，非增减持公告口径。")
        return "**分析评价**：" + "".join(parts)

    def _margin_eval(self, data):
        chg = data.get("rzye_chg_5d_pct")
        parts = []
        try:
            c = float(chg)
            parts.append(f"融资余额近5日变化 {c}%，杠杆资金参与度{'上升' if c > 5 else '下降' if c < -5 else '基本持平'}。")
        except Exception:
            parts.append("融资余额数据不足。")
        parts.append("两融余额变动为杠杆资金参与度的统计口径。")
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
        try:
            _cpi = data.get("cpi_yoy")
            _ppi = data.get("ppi_yoy")
            if _cpi is not None and _ppi is not None:
                _gap = round(float(_ppi) - float(_cpi), 1)
                if abs(_gap) >= 1:
                    parts.append(f"PPI-CPI 剪刀差 {_gap}%（{'上游价格相对更强' if _gap > 0 else '下游价格相对更强'}，剪刀差为价格传导的结构性统计口径）。")
        except Exception:
            pass

        parts.append("以上为宏观统计口径的客观读数，不含对权益资产方向的判断。")
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
    md = runner.report()
    return render_html_report(md, runner.results)


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
        help='维度列表，逗号分隔，默认 ' + ",".join(StockAnalysisRunner.DEFAULT_DIMENSIONS),
    )
    parser.add_argument(
        "--output",
        default=None,
        help="HTML 报告输出路径，默认保存到当前目录 {ts_code}_report.html",
    )
    args = parser.parse_args()

    dims = args.dimensions.split(",") if args.dimensions else None
    report = stock_report(args.ts_code, args.end_date, dims)
    output = args.output or f"{args.ts_code}_report.html"
    with open(output, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"HTML 报告已保存: {os.path.abspath(output)}")
