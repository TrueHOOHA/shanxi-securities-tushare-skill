#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票分析入口（兼容层）。

本文件保留原有接口，底层转发到 analysis_runner.StockAnalysisRunner。
新开发请直接使用 analysis_runner。
"""

from typing import Any, Dict, Optional

from analysis_runner import StockAnalysisRunner, analyze_stock, stock_report


class StockAnalyzer(StockAnalysisRunner):
    """兼容旧接口的别名类。"""

    def __init__(
        self,
        ts_code: str,
        end_date: Optional[str] = None,
        include_optional: bool = False,
    ):
        dimensions = None
        if include_optional:
            dimensions = [
                "overview", "trend", "valuation", "financial", "moneyflow",
                "shareholder", "float", "risk",
            ]
        super().__init__(ts_code=ts_code, end_date=end_date, dimensions=dimensions)


__all__ = ["StockAnalyzer", "analyze_stock", "stock_report"]
