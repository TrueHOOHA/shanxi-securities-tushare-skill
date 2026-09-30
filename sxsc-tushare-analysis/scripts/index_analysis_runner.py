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
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from basic_metrics import calc_ma, calc_max_drawdown, calc_returns, calc_sharpe, calc_volatility
from data_api import DataAPI, shift_date
from factor_signals import calc_amplitude_momentum, calc_quantitative_momentum
from result_model import DimensionResult, ResultStatus, safe_result
from report_html import df_to_md_table, render_html_report
from composite import calc_composite_score, calc_factor_positioning, calc_risk_budget


# index_weight 是月度数据且被"成分权重""行业分布"两个维度并行消费；
# 进程级缓存避免同一份报告里重复取数（审计发现每次多调一次接口）。
_INDEX_WEIGHT_CACHE: Dict[tuple, Optional[pd.DataFrame]] = {}
_INDEX_WEIGHT_LOCK = threading.Lock()


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
    DEFAULT_DIMENSIONS = ["overview", "trend", "valuation", "weight", "sector", "global", "margin", "momentum_quality", "risk"]

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

        conclusion = "近20日涨幅 " + (f"{returns.get('近20日涨幅%')}%" if returns.get('近20日涨幅%') not in (None, "N/A") else "数据不足")
        conclusion += f"，年化波动 {volatility if volatility is not None else '数据不足'}%，最大回撤 {max_dd if max_dd is not None else '数据不足'}%"
        data = {
            "returns": returns,
            "ma": ma,
            "volatility": volatility,
            "max_drawdown": max_dd,
            "sharpe": sharpe,
            "benchmark": bench_data,
            "chart": {
                "title": "指数日线收盘",
                "type": "line",
                "dates": [str(d) for d in series.index.tolist()],
                "series": [{"name": self.index_name or self.ts_code, "data": series.tolist()}],
            },
        }
        return DimensionResult.success("行情趋势", conclusion=conclusion, data=data)

    # ---------- 维度：估值 ----------

    @safe_result("估值分析")
    def analyze_valuation(self) -> DimensionResult:
        # 取近 5 年估值序列。⚠️ 注意：shift_date 是自然日，5 年 = 365*5 自然日；
        # 旧实现写 -250*5 会只取到约 3.4 年（1250 自然日 ≈ 856 交易日），
        # "近5年"分位实际是"近3年多"的分位。不足时按实际返回量降级标注。
        start = shift_date(self.end_date, -365 * 5)
        df = self.api.get_index_dailybasic(self.ts_code, start, self.end_date)
        if df is None or df.empty:
            # ⚠️ SKILL.md:115：index_dailybasic 不覆盖的指数（实测 科创100 000698.SH 为空）
            # 降级为成分股 daily_basic 聚合估算（截面中位数 + 历史分位），并标注数据源；
            # 不再直接判 empty（旧注释"不做聚合估算"与 SKILL 要求相悖）。
            return self._valuation_from_constituents(start)

        df = df.sort_values("trade_date").reset_index(drop=True)
        latest = df.iloc[-1]
        # ⚠️ PE 取 TTM 口径：SKILL 要求 PE(TTM)；服务端返回 pe（静态）与 pe_ttm 两列，
        # 优先 pe_ttm，缺时才回退静态 pe（与股票 runner 的估值口径一致）
        pe_raw = _safe_float(latest.get("pe_ttm")) or _safe_float(latest.get("pe"))
        pe = round(pe_raw, 2) if pe_raw is not None else None
        pb_raw = _safe_float(latest.get("pb"))
        pb = round(pb_raw, 2) if pb_raw is not None else None
        total_mv = _safe_float(latest.get("total_mv"))
        total_mv_yi = round(total_mv / 1e8, 0) if total_mv is not None else None  # index_dailybasic.total_mv 单位为元
        hist_count = len(df)

        # 历史分位需 ≥250 日数据才统计可靠；不足则置 None 并标注
        pe_hist = None
        pb_hist = None
        if hist_count >= 250:
            if pe is not None:
                pe_hist = round((df["pe_ttm"].fillna(df["pe"]) < pe).sum() / len(df) * 100, 1) if "pe_ttm" in df.columns else None
                if pe_hist is None:
                    pe_hist = round((df["pe"] < pe).sum() / len(df) * 100, 1)
            if pb is not None:
                pb_hist = round((df["pb"] < pb).sum() / len(df) * 100, 1)
        hist_note = f"（数据不足，仅 {hist_count} 日）" if hist_count < 250 else ""

        data = {"pe": pe, "pb": pb, "total_mv_yi": total_mv_yi, "pe_hist_percentile": pe_hist, "pb_hist_percentile": pb_hist, "hist_sample_days": hist_count}
        data["chart"] = {
            "title": "近5年 PE/PB 走势",
            "type": "line",
            "dates": df["trade_date"].tolist(),
            "series": [
                {"name": "PE", "yAxisIndex": 0, "data": (df["pe_ttm"].fillna(df["pe"]) if "pe_ttm" in df.columns else df["pe"]).tolist()},
                {"name": "PB", "yAxisIndex": 1, "data": df["pb"].tolist()},
            ],
        }
        # 不直显 "N/A"：无值时分位给出原因（数据不足/无 PE），PE/PB 本身缺失时给出明确口径
        conclusion = f"PE(TTM) {pe if pe is not None else '数据缺失'}"
        conclusion += f"，PB {pb if pb is not None else '数据缺失'}"
        if pe_hist is not None:
            conclusion += f"，PE近5年历史分位 {pe_hist}%"
        elif pe is not None and hist_count < 250:
            conclusion += f"，PE近5年历史分位{hist_note}"
        if pb_hist is not None:
            conclusion += f"，PB近5年历史分位 {pb_hist}%"
        elif pb is not None and hist_count < 250:
            conclusion += f"，PB近5年历史分位{hist_note}"
        return DimensionResult.success("估值分析", conclusion=conclusion, data=data)

    def _valuation_from_constituents(self, start: str) -> DimensionResult:
        """成分股聚合估值（SKILL.md:115 降级路径，仅当 index_dailybasic 未覆盖时触发）。

        index_weight 取最新一期成分（前 20 大权重股控制取数成本）→ 并行拉 daily_basic
        （含 pe_ttm/pb 历史）→ 逐日截面中位数 → 当前中位数的历史分位。
        数据源必须标注"成分股聚合估算"，与官方指数级估值区分。
        """
        import concurrent.futures
        end = self.end_date
        iw = self.api.get_index_weight(self.ts_code, shift_date(end, -40), end)
        if iw is None or iw.empty:
            return DimensionResult.empty("估值分析", note="该指数无指数级估值数据，且无法获取成分股权重（index_weight 未覆盖）")
        iw = iw.sort_values("trade_date")
        latest_wd = iw.iloc[-1]["trade_date"]
        cons = iw[iw["trade_date"] == latest_wd].sort_values("weight", ascending=False).head(20)["con_code"].tolist()
        if not cons:
            return DimensionResult.empty("估值分析", note="该指数无指数级估值数据，且成分股列表为空")

        def _fetch(code: str):
            try:
                d = self.api.get_daily_basic(code, start, end)
                if d is not None and not d.empty and {"trade_date", "pe_ttm", "pb"}.issubset(d.columns):
                    return d[["trade_date", "pe_ttm", "pb"]]
            except Exception:
                return None
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            frames = [f for f in ex.map(_fetch, cons) if f is not None and not f.empty]
        if not frames:
            return DimensionResult.empty("估值分析", note="该指数无指数级估值数据，且成分股 daily_basic 不可用")
        # 逐日截面中位数（pe_ttm/pb 分别取当日成分股中位数）
        med = pd.concat(frames).groupby("trade_date")[["pe_ttm", "pb"]].median().sort_index()
        med = med.replace([float("inf"), float("-inf")], float("nan")).dropna(how="all")
        if med.empty:
            return DimensionResult.empty("估值分析", note="该指数成分股估值聚合结果为空")
        latest = med.iloc[-1]
        pe = round(float(latest["pe_ttm"]), 2) if pd.notna(latest["pe_ttm"]) else None
        pb = round(float(latest["pb"]), 2) if pd.notna(latest["pb"]) else None
        hist_count = len(med)
        hist_note = f"（数据不足，仅 {hist_count} 日）" if hist_count < 250 else ""
        pe_hist = pb_hist = None
        if hist_count >= 250:
            if pe is not None:
                pe_hist = round(float((med["pe_ttm"] < pe).sum() / len(med) * 100), 1)
            if pb is not None:
                pb_hist = round(float((med["pb"] < pb).sum() / len(med) * 100), 1)

        data = {
            "pe": pe, "pb": pb,
            "pe_hist_percentile": pe_hist, "pb_hist_percentile": pb_hist,
            "hist_sample_days": hist_count,
            "source": "成分股聚合估算", "constituent_count": len(frames),
            "chart": {
                "title": "成分股聚合 PE/PB 中位数走势（估算）",
                "type": "line",
                "dates": med.index.tolist(),
                "series": [
                    {"name": "PE(TTM)中位数", "yAxisIndex": 0, "data": [round(float(v), 2) if pd.notna(v) else None for v in med["pe_ttm"]]},
                    {"name": "PB中位数", "yAxisIndex": 1, "data": [round(float(v), 2) if pd.notna(v) else None for v in med["pb"]]},
                ],
            },
        }
        conclusion = f"PE(TTM) {pe if pe is not None else '无数据'}，PB {pb if pb is not None else '无数据'}（成分股聚合估算，{len(frames)} 只成分股，非官方指数估值）"
        if pe_hist is not None:
            conclusion += f"，PE近5年历史分位 {pe_hist}%"
        elif pe is not None:
            conclusion += f"，PE近5年历史分位{hist_note}"
        if pb_hist is not None:
            conclusion += f"，PB近5年历史分位 {pb_hist}%"
        elif pb is not None:
            conclusion += f"，PB近5年历史分位{hist_note}"
        return DimensionResult.success("估值分析", conclusion=conclusion, data=data)

    # ---------- 维度：成分权重 ----------

    def _get_index_weight_cached(self, start: str, end: str) -> Optional[pd.DataFrame]:
        """index_weight 区间取数（进程级缓存）。

        "成分权重"与"行业分布"两个维度并行执行都会取同区间权重，
        不缓存会重复调用接口（审计发现）。键 = (index_code, start, end)。
        """
        key = (self.ts_code, start, end)
        with _INDEX_WEIGHT_LOCK:
            if key in _INDEX_WEIGHT_CACHE:
                return _INDEX_WEIGHT_CACHE[key].copy() if _INDEX_WEIGHT_CACHE[key] is not None else None
        df = self.api.get_index_weight(self.ts_code, start, end)
        with _INDEX_WEIGHT_LOCK:
            _INDEX_WEIGHT_CACHE[key] = df
        return df

    @safe_result("成分权重")
    def analyze_weight(self) -> DimensionResult:
        # index_weight 为月度数据，取最近一个月末
        start = shift_date(self.end_date, -40)
        df = self._get_index_weight_cached(start, self.end_date)
        if df is None or df.empty:
            return DimensionResult.empty("成分权重", note="无法获取成分权重")

        df = df.sort_values("trade_date").reset_index(drop=True)
        latest_date = df.iloc[-1]["trade_date"]
        latest = df[df["trade_date"] == latest_date].sort_values("weight", ascending=False).head(10)

        # 批量补充股票名称（单次API调用，避免逐只查被限流）
        all_codes = ", ".join(latest["con_code"].tolist())
        name_map = {}
        try:
            sb = self.api.get_stock_basic(all_codes, fields="ts_code,name")
            if sb is not None and not sb.empty:
                name_map = dict(zip(sb["ts_code"], sb["name"]))
        except Exception:
            pass
        # 逐只补充缺失的名称（批量查可能因退市等原因遗漏）
        for code in latest["con_code"]:
            if code not in name_map:
                import time; time.sleep(0.3)
                try:
                    sb = self.api.get_stock_basic(code, fields="ts_code,name")
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
        df_w = self._get_index_weight_cached(start, self.end_date)
        if df_w is None or df_w.empty:
            return DimensionResult.empty("行业分布", note="无法获取指数成分股")

        # 取最新一期成分股
        df_w = df_w.sort_values("trade_date")
        latest_date = df_w.iloc[-1]["trade_date"]
        latest = df_w[df_w["trade_date"] == latest_date]
        codes = latest["con_code"].unique().tolist()  # 全量成分股，不截断

        # ⚠️ SKILL.md:117 要求申万 2021 口径。实现：index_classify(SW2021) 取申万一级
        # 行业指数列表 → index_member 逐行业取在册成分 → 本地把指数成分股映射到申万行业。
        # 任一步失败则回退 Tushare 自有分类并如实标注，绝不冒充申万。
        ind_map = {}
        src_label = "Tushare分类（申万映射失败）"
        try:
            clf = self.api.get_index_classify(level="L1", src="SW2021")
            if clf is not None and not clf.empty and {"index_code", "industry_name"}.issubset(clf.columns):
                sw_rows = clf[["index_code", "industry_name"]].drop_duplicates("index_code").values.tolist()

                def _members(code: str):
                    try:
                        m = self.api.get_index_member(code)
                        if m is not None and not m.empty and "con_code" in m.columns:
                            cur = m[m["out_date"].isna()] if "out_date" in m.columns else m
                            return set(cur["con_code"].astype(str))
                    except Exception:
                        return set()
                    return set()

                # ⚠️ index_member 单独限速 20 次/秒（实测 8 路并行会触发
                # "每秒最多访问该接口20次"导致约 1/3 行业取不到 → 匹配率掉到 ~70%）。
                # 必须串行 + 间隔，不能与其它维度并行抢配额。
                import time
                member_sets = []
                for r in sw_rows:
                    member_sets.append((r[1], _members(r[0])))
                    time.sleep(0.06)
                for _name, _members_ in member_sets:
                    for c in _members_:
                        ind_map.setdefault(c, _name)
                if ind_map:
                    src_label = "申万2021一级"
        except Exception:
            ind_map = {}

        if not ind_map:
            # 回退：Tushare 自有分类（一次性查全市场，本地匹配，避免逐只查被限流）
            try:
                all_basic = self.api.get_stock_basic(list_status="L", fields="ts_code,name,industry")
                if all_basic is not None and not all_basic.empty:
                    ind_map = dict(zip(all_basic["ts_code"], all_basic["industry"]))
            except Exception:
                pass
            if not ind_map:
                return DimensionResult.empty("行业分布", note="无法获取成分股行业信息")

        industries = [{"ts_code": c, "industry": ind_map.get(c, "未匹配" if src_label.startswith("申万") else "未知")} for c in codes]
        ind_df = pd.DataFrame(industries)
        sector_dist = ind_df["industry"].value_counts().head(10).reset_index()
        sector_dist.columns = ["行业", "成分股数量"]
        sector_dist["家数占比%"] = round(sector_dist["成分股数量"] / len(ind_df) * 100, 2)
        # 权重口径：最新一期成分权重按行业聚合（资金分布视角，与家数口径并列）
        latest_w = df_w.sort_values("trade_date").groupby("con_code").tail(1)[["con_code", "weight"]]
        ind_w = latest_w.merge(ind_df, left_on="con_code", right_on="ts_code", how="left")
        w_dist = ind_w.groupby("industry")["weight"].sum().sort_values(ascending=False).reset_index()
        w_dist.columns = ["行业", "权重占比%"]
        w_dist["权重占比%"] = w_dist["权重占比%"].round(2)
        sector_dist = sector_dist.merge(w_dist, on="行业", how="left")

        matched_count = int((ind_df["industry"] != ("未匹配" if src_label.startswith("申万") else "未知")).sum())
        data = {"distribution": sector_dist.to_dict("records"), "total_members": len(codes), "matched": matched_count,
                "industry_source": src_label}
        conclusion = (f"成分股覆盖 {len(ind_df)} 只（匹配行业 {matched_count} 只），"
                      f"前三大行业（{src_label}）：{', '.join(sector_dist['行业'].head(3).tolist())}")
        return DimensionResult.success("行业分布", conclusion=conclusion, data=data)

    # ---------- 维度：国际对比 ----------

    @safe_result("国际对比")
    def analyze_global(self) -> DimensionResult:
        # 仅对 A 股主要指数做国际对比
        peers = {
            "000300.SH": [("SPX", "标普500"), ("IXIC", "纳斯达克"), ("DJI", "道琼斯"), ("HSI", "恒生指数")],
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
        # 近一年两融：250 交易日 ≈ 365 自然日（shift_date 是自然日），
        # 用默认 lookback_days=250（自然日 ≈ 166 交易日）会显著短于"近一年"
        sse_df = self.api.get_margin(self.end_date, exchange_id="SSE", lookback_days=365)
        szse_df = self.api.get_margin(self.end_date, exchange_id="SZSE", lookback_days=365)

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
            parts.append(f"近一年融资余额{direction} {abs(avg_chg)}%")
        # 两市融资余额走势（亿元）；两所数据天数可能差一天，按日期对齐避免错位
        _m_dates = []
        _m_raw = {}  # label -> {trade_date: rzye}
        for _label, _df in (("上交所", sse_df), ("深交所", szse_df)):
            if _df is None or _df.empty:
                continue
            _d = _df.sort_values("trade_date")
            _m_raw[_label] = dict(zip(_d["trade_date"], _d["rzye"]))
            if len(_d["trade_date"]) > len(_m_dates):
                _m_dates = _d["trade_date"].tolist()
        _m_series = []
        for _label, _m in _m_raw.items():
            _vals = [_m.get(d) for d in _m_dates]
            _m_series.append({"name": _label,
                             "data": [round(v / 1e8, 2) if v is not None and v == v else None for v in _vals]})
        if _m_series:
            data["chart"] = {
                "title": "两市融资余额走势（亿元）",
                "type": "line",
                "dates": _m_dates,
                "series": _m_series,
            }
        conclusion = "，".join(parts)
        return DimensionResult.success("两融/市场杠杆", conclusion=conclusion, data=data)

    # ---------- 维度：动量质量（因子化） ----------

    @safe_result("动量质量")
    def analyze_momentum_quality(self) -> DimensionResult:
        start = shift_date(self.end_date, -max(self.PERIODS) * 2)
        df = self.api.get_index_daily(self.ts_code, start, self.end_date)
        if df is None or df.empty:
            return DimensionResult.empty("动量质量", note="无法获取指数行情")
        df = df.set_index("trade_date").sort_index()
        price = df["close"]
        has_ohlc = "high" in df.columns and "low" in df.columns
        high = df["high"] if has_ohlc else None
        low = df["low"] if has_ohlc else None
        _src = "指数日线" if has_ohlc else "指数日线（缺 high/low，振幅切割不适用）"
        if len(price) < 121:
            return DimensionResult.insufficient_history("动量质量", note="需至少121日数据")

        qm = calc_quantitative_momentum(price, window=60)
        am = calc_amplitude_momentum(price, high, low, n=120, lam=0.3) if has_ohlc else None
        data = {"quantitative_momentum": qm, "amplitude_momentum": am, "source": _src}
        conclusion = f"动量质量因子分析（数据源：{_src}）。"
        insights = []
        if qm:
            insights.append(f"高质量动量得分 {qm['momentum']}（60日收益 {qm['raw_return'] * 100:.2f}%、日波动 {qm['sigma'] * 100:.2f}%；得分 = 收益 − 3000×方差，越大动量越强且质量越高）")
        if am:
            insights.append(f"振幅切割动量：A={am['a_factor']}（低振幅日收益合计，>0 动量正向）、B={am['b_factor']}（高振幅日收益合计，呈反转效应）")
        elif not has_ohlc:
            insights.append("行情缺 high/low 列，振幅切割动量不适用")
        if qm and qm.get("momentum") is not None and qm["momentum"] < 0:
            insights.append("高质量动量得分为负，动量或由单日大波动驱动，持续性存疑")
        return DimensionResult.success("动量质量", conclusion=conclusion, data=data, insights=insights)


    # ---------- 维度：风险提示 ----------

    @safe_result("风险提示")
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
            if isinstance(pe_hist, (int, float)) and pe_hist > 70:
                risks.append(f"PE 历史分位 {pe_hist}%，估值偏高")
            if isinstance(pb_hist, (int, float)) and pb_hist > 70:
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
            else:  # 兜底：pandas 3.x 新 str dtype 不属于 object，也需清洗 NaN
                df[col] = df[col].apply(
                    lambda x: self._fmt(x) if isinstance(x, (int, float, np.integer, np.floating))
                    else ("N/A" if x is None or (isinstance(x, float) and x != x) else str(x))
                )
        return df_to_md_table(df)

    DIM_TITLES = {
        "overview": "概况",
        "trend": "行情趋势",
        "valuation": "估值分析",
        "weight": "成分权重",
        "sector": "行业分布",
        "global": "国际对比",
        "margin": "两融/市场杠杆",
        "momentum_quality": "动量质量",
        "risk": "风险提示",
    }

    # 各维度含义描述（面向非专业读者，标题后以引用块呈现）
    DIM_DESCRIPTIONS = {
        "overview": "指数基础档案：代码、发布机构、基期与基点，回答“这是什么指数”。",
        "trend": "指数走势：区间涨跌幅、波动率、回撤与均线，并与沪深300对比，判断趋势方向与相对强弱。",
        "valuation": "估值水平：指数整体 PE/PB 及历史分位，判断“贵不贵”。",
        "weight": "成分权重：前十大成分股及占比，判断指数受哪些大票主导。",
        "sector": "行业分布：成分股按申万行业归类，判断指数的行业集中度。",
        "global": "国际对比：与标普500/纳斯达克/恒生等指数的收益对比，判断相对国际市场的强弱。",
        "margin": "两融/市场杠杆：全市场融资融券余额，判断整体市场杠杆情绪与风险偏好。",
        "momentum_quality": "动量质量：用风险调整后的动量得分，判断指数涨势的稳健程度与持续性。",
        "risk": "风险提示：汇总各维度风险信号，给出综合风险分级。",
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
        _dts = self.results.get("trend")
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
        try:
            lines.append(self._overall_evaluation())
        except Exception as e:
            # 整体评价是装饰性汇总，任何异常都不该让整份报告崩溃（SKILL 部分失败原则）
            print(f"[index_runner] 整体分析评价生成失败: {type(e).__name__}: {e}", file=sys.stderr)
            lines.append("**整体分析评价**：部分维度数据不完整，本次未生成综合结论。")
        lines.append("")
        lines.append("---")
        lines.append("*本报告由AI基于山西证券Tushare平台数据自动生成，所有内容均为 T-1 日历史数据的客观统计与描述，不含对证券价格走势的预测、判断或方向性建议，不构成任何投资建议、要约或财务指导。*")
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
            if pe_hist is not None and pe_hist > 70:
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
            points.append(f"- **杠杆**：上交所融资余额 {sse_rzye}亿，深交所融资余额 {szse_rzye}亿")

        if points:
            lines.append("")
            lines.extend(points)

        # 风格定位
        style_parts = []
        if val_ok:
            pe_hist = self._f(self._v(valuation.data, "pe_hist_percentile"))
            if pe_hist is not None and pe_hist > 70:
                style_parts.append("高估值")
            elif pe_hist is not None and pe_hist < 20:
                style_parts.append("低估值")
        if trend_ok:
            vol = self._f(self._v(trend.data, "volatility"))
            beta_approx = self._f(self._v(trend.data, "returns", "近250日涨幅%"))
            bench250 = self._f(self._v(trend.data.get("benchmark") or {}, "returns", "近250日涨幅%"))
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
            _st = "/".join(style_parts)
            _st = _st[:-1] if _st.endswith("型") else _st
            lines.append(f"**风格定位**：{name}属于{_st}型指数。")

        # 结论
        concl_parts = []
        if val_ok:
            pe_hist = self._f(self._v(valuation.data, "pe_hist_percentile"))
            if pe_hist is not None:
                concl_parts.append(f"PE 处于历史分位 {pe_hist}%")
        if trend_ok:
            vol = self._f(self._v(trend.data, "volatility"))
            mdd = self._f(self._v(trend.data, "max_drawdown"))
            if vol is not None and vol > 30:
                concl_parts.append(f"年化波动 {vol:.1f}%偏高")
            if mdd is not None and mdd < -20:
                concl_parts.append(f"最大回撤 {mdd:.1f}%较深")

        if concl_parts:
            lines.append("")
            lines.append(f"**数据汇总**：{name}{'，'.join(concl_parts)}。以上均为 T-1 历史数据的统计描述，不含对指数方向性判断或操作建议。")
        else:
            lines.append("")
            lines.append(f"**数据汇总**：{name}当前各项指标相对中性。以上均为 T-1 历史数据的统计描述，不含对指数方向性判断或操作建议。")

        # ---- 因子综合评分 + 三维定位 + 风险预算 ----
        try:
            pe_hist = self._f(self._v(valuation.data, "pe_hist_percentile")) if val_ok else None
            ret250 = self._f(self._v(trend.data, "returns", "近250日涨幅%")) if trend_ok else None
            sharpe = self._f(self._v(trend.data, "sharpe")) if trend_ok else None

            composite = calc_composite_score(pe_hist, None, ret250, sharpe, None)
            if composite.get("composite") is not None:
                lines.append("")
                lines.append(f"**因子综合评分**：{composite['composite']}（{composite['rating']}，等权雏形非IC优化，仅供参考）")
                fac = composite.get("factors", {})
                if fac:
                    lines.append(f"- 各维子分：{' / '.join(f'{k}{v}' for k, v in fac.items())}")

            positioning = calc_factor_positioning(pe_hist, None, ret250)
            if positioning.get("positioning"):
                lines.append("")
                lines.append(f"**因子定位**：{positioning['positioning']}")

            if trend_ok:
                mdd = self._f(self._v(trend.data, "max_drawdown"))
                vol = self._f(self._v(trend.data, "volatility"))
                budget = calc_risk_budget(None, mdd, None, None, vol)
                if budget.get("risk_level"):
                    lines.append("")
                    # 合规：只列风险因子的客观测算值与定性等级，不给出仓位/配置比例
                    lines.append(f"**风险因子测度**：风险等级 {budget['risk_level']}（由回撤/波动/Beta/流动性/VaR 等因子综合测算，为数据统计结果，不含仓位或配置建议）")
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
                if v is None or str(v) == "nan":
                    continue
                if k in ("base_date", "list_date") and str(v).isdigit() and len(str(v)) == 8:
                    v = f"{str(v)[:4]}-{str(v)[4:6]}-{str(v)[6:]}"
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
                top1_name = dist[0].get("行业", "") if dist else ""
                top1_cnt = dist[0].get("家数占比%", "N/A")
                top1_w = dist[0].get("权重占比%", "N/A")
                _wd_txt = f"（家数占比 {top1_cnt}%，权重占比 {top1_w}%）" if top1_w != "N/A" else f"（家数占比 {top1_cnt}%）"
                _conc_basis = self._f(top1_w) if top1_w != "N/A" else self._f(top1_cnt)
                # 分类口径必须在报告中标注（申万2021 / Tushare回退）
                _src = data.get("industry_source") or "分类口径未标注"
                lines.append(f"**分析评价**：按{_src}分类，前三大行业为{'、'.join(top3)}，第一大行业（{top1_name}）{_wd_txt}。" + ("权重口径集中度较高，指数走势受少数行业影响显著。" if _conc_basis is not None and _conc_basis > 20 else "权重口径下行业分布相对分散。"))
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
                            eval_text = f"{idx_name}近250日涨幅 {idx_entry[1]}%，在对比标的中表现最弱。"
                        else:
                            eval_text = f"{idx_name}近250日涨幅 {idx_entry[1]}%，介于{best[0]}({best[1]}%)与{worst[0]}({worst[1]}%)之间，较最强标的落后 {round(best[1] - idx_entry[1], 1)} 个百分点，处于对比样本第 {len(eval_parts) - [x[0] for x in eval_parts].index(idx_name)}/{len(eval_parts)} 位。"
                        lines.append(f"**分析评价**：{eval_text}")
            else:
                lines.append("- 未配置该指数的国际对比标的")

        elif res.title == "两融/市场杠杆":
            table_rows = []
            for ex in ["sse", "szse"]:
                s = data.get(ex)
                if s:
                    table_rows.append({
                        "交易所": {"SSE": "上交所", "SZSE": "深交所"}.get(ex.upper(), ex.upper()),
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

        elif res.title == "动量质量":
            rows = []
            qm = data.get("quantitative_momentum", {})
            if qm:
                rows.append({"指标": "高质量动量得分(r_60-3000σ²)", "数值": self._fmt(qm.get("momentum"), digits=4)})
                rows.append({"指标": "原始60日收益", "数值": self._fmt(qm.get("raw_return"), digits=4)})
                rows.append({"指标": "60日收益标准差", "数值": self._fmt(qm.get("sigma"), digits=4)})
            am = data.get("amplitude_momentum", {})
            if am:
                rows.append({"指标": "振幅切割A因子(低振幅日)", "数值": self._fmt(am.get("a_factor"), digits=4)})
                rows.append({"指标": "振幅切割B因子(高振幅日)", "数值": self._fmt(am.get("b_factor"), digits=4)})
            if data.get("source"):
                rows.append({"指标": "数据源", "数值": data["source"]})
            if rows:
                lines.append(self._md(pd.DataFrame(rows)))

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
            elif pe_hist > 70:
                parts.append(f"PE历史分位 {pe_hist}%，估值中高（进一步抬升依赖盈利增速）。")
            elif pe_hist < 20:
                parts.append(f"PE历史分位 {pe_hist}%，处于历史低位。")
                if pe is not None and pe > 30:
                    parts.append(f"PE绝对值 {pe} 虽不低，但相对自身历史已处于底部区间，反映成分股盈利改善快于股价上涨。")
            else:
                parts.append(f"PE历史分位 {pe_hist}%，估值中等。")
        if pb_hist is not None:
            if pb_hist > 80:
                parts.append(f"PB历史分位 {pb_hist}%，偏高。")
            elif pb_hist > 70:
                parts.append(f"PB历史分位 {pb_hist}%，中高。")
            elif pb_hist < 20:
                parts.append(f"PB历史分位 {pb_hist}%，偏低。")
            else:
                parts.append(f"PB历史分位 {pb_hist}%。")
        parts.append("指数估值水位与成分股盈利增速、行业景气度相关。")
        return "**分析评价**：" + "".join(parts)

    def _margin_eval(self, data):
        total_rzye = self._f(data.get("total_rzye_billion"))
        avg_chg = self._f(data.get("avg_rzye_chg_pct"))
        parts = []
        if total_rzye is not None:
            parts.append(f"两市融资余额合计 {total_rzye:.0f}亿，")
        if avg_chg is not None:
            if avg_chg > 5:
                parts.append(f"近一年上升 {avg_chg}%，杠杆资金快速入场，市场风险偏好升温。")
            elif avg_chg < -5:
                parts.append(f"近一年下降 {abs(avg_chg)}%，杠杆资金持续撤离，市场风险偏好降温，反映资金面偏谨慎。")
            else:
                parts.append(f"近一年变化 {avg_chg}%，杠杆情绪相对平稳，市场风险偏好未出现明显转向。")
        parts.append("两融余额是市场整体杠杆水平的晴雨表，融资余额上升通常对应风险偏好提升，下降则反映去杠杆压力。")
        return "**分析评价**：" + "".join(parts)

# ---------- 便捷函数 ----------

def analyze_index(ts_code: str, end_date: Optional[str] = None, dimensions: Optional[List[str]] = None) -> Dict[str, Any]:
    runner = IndexAnalysisRunner(ts_code=ts_code, end_date=end_date, dimensions=dimensions)
    return runner.run()


def index_report(ts_code: str, end_date: Optional[str] = None, dimensions: Optional[List[str]] = None) -> str:
    runner = IndexAnalysisRunner(ts_code=ts_code, end_date=end_date, dimensions=dimensions)
    md = runner.report()
    return render_html_report(md, runner.results)


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    import argparse
    parser = argparse.ArgumentParser(description="指数综合分析 Runner")
    parser.add_argument("ts_code", help="指数代码，如 000300.SH")
    parser.add_argument("--end-date", default=None, help="分析截止日期 YYYYMMDD")
    parser.add_argument(
        "--output",
        default=None,
        help="HTML 报告输出路径，默认保存到当前目录 {ts_code}_report.html",
    )
    args = parser.parse_args()
    report = index_report(args.ts_code, args.end_date)
    output = args.output or f"{args.ts_code}_report.html"
    with open(output, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"HTML 报告已保存: {os.path.abspath(output)}")
