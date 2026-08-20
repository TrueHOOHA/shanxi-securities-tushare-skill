#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一结果模型：为分析 skill 提供一致的状态、结构和错误处理。

核心设计：
- ResultStatus：四态枚举（success / empty / permission_denied / insufficient_history）
- DimensionResult：每个分析维度的标准输出容器
- safe_result：装饰器，把函数异常自动映射为 DimensionResult
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class ResultStatus(str, Enum):
    """维度执行后的四态之一。"""

    SUCCESS = "success"
    EMPTY = "empty"                     # 接口返回空
    PERMISSION_DENIED = "permission_denied"  # 无权限/配额不足
    INSUFFICIENT_HISTORY = "insufficient_history"  # 历史数据不足
    ERROR = "error"                     # 其他错误


@dataclass
class DimensionResult:
    """单个分析维度的标准结果。"""

    status: ResultStatus
    title: str                          # 维度中文名，如"行情趋势"
    conclusion: str = ""                # 一句话结论
    data: Optional[Dict[str, Any]] = None  # 结构化数据
    tables: List[Dict[str, Any]] = field(default_factory=list)  # 可渲染的表格列表
    risks: List[str] = field(default_factory=list)  # 该维度发现的风险信号
    note: str = ""                      # 对空结果/异常的说明

    def is_ok(self) -> bool:
        return self.status == ResultStatus.SUCCESS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "title": self.title,
            "conclusion": self.conclusion,
            "data": self.data,
            "tables": self.tables,
            "risks": self.risks,
            "note": self.note,
        }

    @classmethod
    def success(
        cls,
        title: str,
        conclusion: str = "",
        data: Optional[Dict[str, Any]] = None,
        tables: Optional[List[Dict[str, Any]]] = None,
        risks: Optional[List[str]] = None,
        note: str = "",
    ) -> "DimensionResult":
        return cls(
            status=ResultStatus.SUCCESS,
            title=title,
            conclusion=conclusion,
            data=data or {},
            tables=tables or [],
            risks=risks or [],
            note=note,
        )

    @classmethod
    def empty(cls, title: str, note: str = "无数据") -> "DimensionResult":
        return cls(status=ResultStatus.EMPTY, title=title, note=note)

    @classmethod
    def permission_denied(cls, title: str, note: str = "权限不足") -> "DimensionResult":
        return cls(status=ResultStatus.PERMISSION_DENIED, title=title, note=note)

    @classmethod
    def insufficient_history(cls, title: str, note: str = "历史数据不足") -> "DimensionResult":
        return cls(status=ResultStatus.INSUFFICIENT_HISTORY, title=title, note=note)

    @classmethod
    def error(cls, title: str, note: str = "") -> "DimensionResult":
        return cls(status=ResultStatus.ERROR, title=title, note=note)


def safe_result(title: str):
    """装饰器：捕获异常并转为 DimensionResult.error。

    用法：
        @safe_result("行情趋势")
        def analyze_trend(...) -> DimensionResult:
            ...
    """
    def decorator(fn: Callable[..., DimensionResult]) -> Callable[..., DimensionResult]:
        def wrapper(*args, **kwargs) -> DimensionResult:
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                return DimensionResult.error(title, note=f"{type(e).__name__}: {e}")
        return wrapper
    return decorator
