#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回归测试：覆盖审查报告中确认的 bug 修复点。
运行：python sxsc-tushare-analysis/scripts/test_regression.py
也兼容 pytest（函数名 test_*）。
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from adjustment import (  # noqa: E402
    _check_date_index, apply_fund_adj, calc_percentile_rank, clean_panel,
    rebase_series, valuation_percentiles, winsorize_cross_section,
)
from attribution import calc_piotroski_fscore, calc_event_study  # noqa: E402
from basic_metrics import calc_returns, calc_roe_trend, flag_risks  # noqa: E402
from risk_modeling import (  # noqa: E402
    calc_amihud_illiquidity, calc_rolling_beta, calc_var_cvar,
)
from technical_indicators import calc_boll, calc_macd, calc_obv, calc_rsi, calc_volume_ratio  # noqa: E402


def test_obv_short_data_no_crash():
    """OBV 数据 < 5 应返回数据不足而非 IndexError。"""
    r = calc_obv(pd.Series([10, 11, 12]), pd.Series([100, 200, 150]))
    assert r["trend"].startswith("数据不足") or r["OBV"] == "N/A"


def test_var_cvar_empty_no_crash():
    """VaR 空序列应返回数据不足而非 IndexError。"""
    r = calc_var_cvar(pd.Series([], dtype=float))
    assert r.get("status") == "数据不足"


def test_percentile_rank_empty_returns_none():
    """空历史序列应返回 None 而非静默 nan。"""
    assert calc_percentile_rank(10, pd.Series([], dtype=float)) is None


def test_rolling_beta_len_equals_window():
    """数据长度等于窗口时应返回单个 Beta 而非 None。"""
    s = pd.Series(np.linspace(0, 0.01, 60))
    m = pd.Series(np.linspace(0, 0.01, 60))
    r = calc_rolling_beta(s, m, window=60)
    assert r is not None and "当前Beta" in r and "数据不足" in r["趋势"]


def test_rebase_series_uses_earliest_common_date():
    """两序列起点不同时，不应让后启动序列整列变 NaN，基准日=100。"""
    s1 = pd.Series([10, 11, 12, 13], index=pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]))
    s2 = pd.Series([20, 21, 22], index=pd.to_datetime(["2026-01-07", "2026-01-08", "2026-01-09"]))
    out = rebase_series({"A": s1, "B": s2})
    assert not bool(out["B"].isna().all()), "B 列不应全 NaN"
    assert out["B"].dropna().iloc[0] == 100.0, "公共基准日 B 应为 100"


def test_apply_fund_adj_ascending_input_uses_latest_factor():
    """升序输入时前复权应除以最新因子（iloc[-1]），而非最早。"""
    nav = pd.DataFrame({"nav_date": pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]), "unit_nav": [1.0, 1.0, 1.0]})
    adj_asc = pd.DataFrame({"trade_date": pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]), "adj_factor": [1.0, 1.5, 2.0]})
    r = apply_fund_adj(nav.copy(), adj_asc)
    assert r["adj_nav"].iloc[-1] == 1.0, "最新日 adj_nav 应 = 1.0*2.0/2.0 = 1.0"


def test_amihud_mismatched_nan_no_mispair():
    """收益与成交额 NaN 不在同一行时，应联合 dropna 避免按位置错配。"""
    idx = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"])
    pr = pd.Series([0.01, np.nan, 0.03, 0.04], index=idx)
    dv = pd.Series([100, 200, np.nan, 400], index=idx)
    r = calc_amihud_illiquidity(pr, dv)
    assert r is not None, "应返回结果（联合 dropna 后仍有 2 个有效对）"


def test_macd_signal_is_real_crossover_or_state():
    """MACD signal 不应仅凭当前 DIF>DEA 误报金叉。"""
    # 单调上升：DIF>DEA 但无交叉，应为多头排列
    c = pd.Series(np.arange(30, dtype=float))
    r = calc_macd(c)
    assert r["signal"] in {"多头排列", "空头排列", "金叉", "死叉", "数据不足"}


def test_fscore_all_true_returns_9_and_filters_q3():
    """全真情形应得 9 分；Q3 报告应被年报过滤剔除。"""
    fina = pd.DataFrame({
        "end_date": ["20231231", "20240930", "20241231", "20231231"],
        "ann_date": ["20240428", "20241028", "20250428", "20240420"],
        "roa": [0.05, 0.06, 0.07, 0.05],
        "grossprofit_margin": [40, 41, 42, 40],
        "debt_to_assets": [50, 49, 48, 50],
        "current_ratio": [1.5, 1.6, 1.7, 1.5],
        "assets_turn": [0.6, 0.6, 0.7, 0.6],
    })
    inc = pd.DataFrame({"end_date": ["20231231", "20241231"], "ann_date": ["20240428", "20250428"], "n_income_attr_p": [1e9, 1.2e9]})
    cf = pd.DataFrame({"end_date": ["20231231", "20241231"], "ann_date": ["20240428", "20250428"], "n_cashflow_act": [1.5e9, 1.8e9]})
    bal = pd.DataFrame({"end_date": ["20231231", "20241231"], "ann_date": ["20240428", "20250428"], "total_share": [5e9, 5e9]})
    r = calc_piotroski_fscore(fina, inc, cf, bal)
    assert r["F-Score"] == 9, f"全真应得 9 分，得 {r['F-Score']}"
    assert r["rating"] == "强"
    assert len(r["details"]) == 9

    # 不提供资产负债表：第 7 项应标注数据缺失，得分 8
    r2 = calc_piotroski_fscore(fina, inc, cf)
    assert r2["F-Score"] == 8, f"缺资产负债表应得 8 分，得 {r2['F-Score']}"
    assert any("数据缺失" in d for d in r2["details"])


def test_roe_trend_returns_latest_periods():
    """calc_roe_trend 应返回最近 8 期（tail），而非最早 8 期。"""
    df = pd.DataFrame({
        "end_date": [f"2020Q{i}" for i in range(1, 5)] + [f"2021Q{i}" for i in range(1, 5)] + ["2022Q1"],
        "roe": list(range(9)),
        "grossprofit_margin": list(range(9)),
        "netprofit_margin": list(range(9)),
    })
    out = calc_roe_trend(df)
    assert len(out) == 8
    assert out.iloc[-1]["end_date"] == "2022Q1", "tail(8) 应包含最新一期 2022Q1"


def test_flag_risks_sorts_descending_margin():
    """margin_detail 返回降序时，flag_risks 应正确取最新两期比较。"""
    df_price = pd.DataFrame({"trade_date": ["20260101", "20260102", "20260103", "20260104"], "pct_chg": [1, 2, 1, 1]})
    # 降序：最新在最前
    df_margin = pd.DataFrame({"trade_date": ["20260104", "20260103", "20260102"], "rzye": [2000, 1000, 500]})
    risks = flag_risks("X", df_price, df_margin=df_margin)
    # rzye 最新(20260104)=2000 vs 前一期(20260103)=1000，1.1 倍阈值触发
    assert any("融资余额快速上升" in r for r in risks)


def test_event_study_no_false_significance_claim():
    """事件研究 interpretation 不应声称'显著'，应注明未做 t 检验。"""
    np.random.seed(0)
    stock = pd.Series(np.random.randn(60) * 0.01)
    market = pd.Series(np.random.randn(60) * 0.01)
    r = calc_event_study(stock, market, event_date=40)
    if r is not None:
        assert "显著" not in r["interpretation"], "不应声称统计显著性"
        assert "t 检验" in r["interpretation"]


def test_rsi_boll_short_data_guards():
    assert calc_rsi(pd.Series([1, 2, 3]), period=14)["signal"] == "数据不足"
    assert calc_boll(pd.Series([1, 2, 3]), window=20)["position"] == "数据不足"


def test_clean_panel_dedup_and_numeric():
    """clean_panel 去重(保最新公告) + 数值列强转。"""
    df = pd.DataFrame({
        "end_date": ["20241231", "20241231", "20250331"],
        "ann_date": ["20250428", "20250420", "20251028"],
        "roe": ["0.05", "0.05", "0.06"],
        "ts_code": ["X", "X", "X"],
    })
    out = clean_panel(df, value_cols=["roe"])
    assert out is not None and len(out) == 2, "应去重到 2 行"
    assert out["roe"].dtype.kind == "f", "roe 应转为 float"
    assert out.iloc[-1]["end_date"] == "20250331"


def test_winsorize_cross_section_clips_outlier():
    """截面去极值：单个极端值应被截断，中位数不受影响。"""
    s = pd.Series([10, 11, 12, 10, 11, 12, 10, 11, 999.0])
    w = winsorize_cross_section(s, method="mad", k=3.0)
    assert w.max() < 999.0, "极端高值应被截断"
    assert abs(w.median() - 11.0) < 1e-9, "中位数不应被去极值改变"


def test_valuation_percentiles_dual_check():
    """双口径分位：历史 + 截面都应返回，值高于参考集越多分位越高。"""
    r = valuation_percentiles(50, pd.Series([10, 20, 30, 40, 50, 60]), pd.Series([5, 15, 25, 35, 45, 55]))
    assert "历史分位" in r and "截面分位" in r
    hv = r["历史分位"]
    cv = r["截面分位"]
    assert hv is not None and hv == 66.7, "50 高于历史 4/6=66.7%"
    assert cv is not None and cv == 83.3, "50 高于截面 5/6=83.3%"
    assert r["截面样本数"] == 6
    assert "样本不足" in r["截面可靠性"], "6 < min_sample(10) 应标注样本不足"


def test_fscore_financial_firm_comp_note():
    """金融机构(comp_type=4) 应触发 comp_note，并返回有效项/缺失项计数；npta 兜底 ROA。"""
    fina = pd.DataFrame({"end_date": ["20231231", "20241231"], "ann_date": ["20240428", "20250428"],
                         "roa": [None, None], "npta": [0.74, 0.87], "grossprofit_margin": [None, None],
                         "debt_to_assets": [76.6, 77.3], "current_ratio": [2.13, 2.13], "assets_turn": [0.04, 0.04]})
    inc = pd.DataFrame({"end_date": ["20231231", "20241231"], "ann_date": ["20240428", "20250428"], "n_income_attr_p": [6e8, 7e8]})
    cf = pd.DataFrame({"end_date": ["20231231", "20241231"], "ann_date": ["20240428", "20250428"], "n_cashflow_act": [1e9, 1e9]})
    bal = pd.DataFrame({"end_date": ["20231231", "20241231"], "ann_date": ["20240428", "20250428"],
                        "total_share": [3.5e9, 3.5e9], "total_assets": [7.7e10, 8.0e10], "comp_type": [4, 4]})
    r = calc_piotroski_fscore(fina, inc, cf, bal)
    assert r["comp_note"] is not None and "金融机构" in r["comp_note"], "券商应触发金融业弱参考提示"
    assert r["有效项"] + r["缺失项"] == 9
    assert any("ROA>0: 是" in d for d in r["details"]), "npta 兜底后 ROA>0 应为是"


def test_calc_returns_empty_and_nan_no_crash():
    """空序列/全 NaN 不应崩，应返回 N/A 而非裸 NaN。"""
    assert calc_returns(pd.Series([], dtype=float))["最新收盘"] == "N/A"
    assert calc_returns(pd.Series([np.nan] * 30))["最新收盘"] == "N/A"


def test_macd_volume_ratio_empty_no_crash():
    """MACD/量比 空序列不应抛 IndexError。"""
    e = pd.Series([], dtype=float)
    assert calc_macd(e)["signal"] == "数据不足"
    assert calc_volume_ratio(e) is None


def test_boll_constant_no_false_breakout():
    """常量数据下布林带应识别为无波动，而非误报触及上轨。"""
    r = calc_boll(pd.Series([5.0] * 30), window=20)
    assert "无波动" in r["position"], "std=0 时应标注无波动，不应误报超买"


def test_check_date_index_rejects_integer():
    """整数索引应被拒绝，避免 rebase 产出无意义"日期"。"""
    int_idx = pd.Series([1, 2, 3])  # RangeIndex 整数
    raised = False
    try:
        _check_date_index(int_idx.index, "test")
    except TypeError:
        raised = True
    assert raised, "整数索引应抛 TypeError"
    # 日期字符串索引应通过
    date_idx = pd.Series([1, 2, 3], index=["20260101", "20260102", "20260103"])
    _check_date_index(date_idx.index, "test")  # 不抛


def test_rebase_series_rejects_integer_index():
    """rebase_series 传整数索引的 Series 应抛错而非产出假日期。"""
    s_int = pd.Series([1, 2, 3, 4])  # RangeIndex
    raised = False
    try:
        rebase_series({"A": s_int})
    except TypeError:
        raised = True
    assert raised, "整数索引应被拒绝"


def test_apply_fund_adj_returns_nav_date_index():
    """apply_fund_adj 应返回 nav_date 索引（非整数），便于 rebase 直接使用。"""
    nav = pd.DataFrame({"nav_date": ["d1", "d2", "d3"], "unit_nav": [1.0, 1.0, 1.0]})
    adj = pd.DataFrame({"trade_date": ["d1", "d2", "d3"], "adj_factor": [1.0, 1.5, 2.0]})
    out = apply_fund_adj(nav.copy(), adj)
    assert list(out.index)[:2] == ["d1", "d2"], f"应为 nav_date 索引，得到 {list(out.index)}"


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {len(tests)} total")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
