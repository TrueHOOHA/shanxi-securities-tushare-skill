#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票分析入口（兼容层）。

本文件保留原有接口，底层转发到 analysis_runner.StockAnalysisRunner。
新开发请直接使用 analysis_runner。
"""

from typing import Optional

from analysis_runner import StockAnalysisRunner, analyze_stock, stock_report


class StockAnalyzer(StockAnalysisRunner):
    """兼容旧接口的别名类。"""

    def __init__(
        self,
        ts_code: str,
        end_date: Optional[str] = None,
        include_optional: bool = False,
    ):
        # include_optional 语义：False=基础 8 维，True=再追加 3 个可选维度。
        # 原实现把基础列表挂在 True 分支上，导致 include_optional=True 反而少跑维度。
        dimensions = [
            "overview", "trend", "valuation", "financial", "moneyflow",
            "shareholder", "float", "risk",
        ]
        if include_optional:
            dimensions += ["margin", "market_activity", "macro"]
        super().__init__(ts_code=ts_code, end_date=end_date, dimensions=dimensions)


__all__ = ["StockAnalyzer", "analyze_stock", "stock_report"]
