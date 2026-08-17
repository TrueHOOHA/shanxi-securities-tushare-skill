#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
组合分析模块：跨维度因子组合、技术共振、量价模式、业绩拐点、配对相对价值、风险预算。

将 basic_metrics / technical_indicators / risk_modeling / attribution / adjustment 五大模块的
单维度计算函数组合成跨维度的深度分析，从"列指标"升级到"交叉判断"。
"""

import numpy as np
import pandas as pd

# 引用同目录下的兄弟模块
from basic_metrics import (calc_returns, calc_ma, calc_volatility, calc_max_drawdown,
                           calc_sharpe, calc_sortino, calc_cagr, calc_roe_trend,
                           calc_revenue_growth, calc_holder_concentration)
from technical_indicators import calc_macd, calc_rsi, calc_kdj, calc_boll, calc_obv, calc_volume_ratio
from risk_modeling import (calc_var_cvar, calc_tail_risk, calc_drawdown_detail,
                           calc_amihud_illiquidity, calc_relative_strength)
from attribution import calc_beta_alpha
from adjustment import rebase_series, compare_returns, calc_percentile_rank


# ============ 1. 技术共振信号 ============
def calc_technical_confluence(close, high=None, low=None, vol=None):
    """技术共振信号：汇总 MACD/RSI/KDJ/布林带/OBV/量比 6 指标方向 → 共振强度。

    每个指标给 -1(偏空) / 0(中性) / +1(偏多) 的方向分，汇总后：
      ≥4 强多 / 2~3 偏多 / -1~1 中性 / -3~-2 偏空 / ≤-4 强空

    close: 后复权收盘价 Series（日期升序）
    high/low: 后复权高/低价（可选，无则用 close 近似）
    vol: 成交量 Series（可选，无则跳过量比/OBV）
    返回 dict：各指标信号 + 共振总分 + 共振判断。
    """
    if close is None or len(close) == 0:
        return {"score": 0, "signal": "数据不足", "details": []}

    h = high if high is not None else close
    l = low if low is not None else close
    details = []
    score = 0

    # MACD
    macd = calc_macd(close)
    s = 1 if macd["signal"] == "金叉" else (-1 if macd["signal"] == "死叉" else (0.5 if "多头" in macd["signal"] else (-0.5 if "空头" in macd["signal"] else 0)))
    score += s; details.append(f"MACD: {macd['signal']} ({'+' if s>=0 else ''}{s})")

    # RSI
    rsi = calc_rsi(close)
    if rsi["RSI"] != "N/A":
        rv = float(rsi["RSI"])
        s = 1 if rv < 30 else (-1 if rv > 70 else 0)
    else:
        s = 0
    score += s; details.append(f"RSI: {rsi['RSI']}({rsi['signal']}) ({'+' if s>=0 else ''}{s})")

    # KDJ
    kdj = calc_kdj(h, l, close)
    s = 1 if kdj["signal"] == "金叉" else (-1 if kdj["signal"] == "死叉" else 0)
    score += s; details.append(f"KDJ: K{kdj['K']}/D{kdj['D']}({kdj['signal']}) ({'+' if s>=0 else ''}{s})")

    # 布林带
    boll = calc_boll(close)
    pos = boll.get("position", "")
    s = 1 if "下轨" in pos else (-1 if "上轨" in pos else 0)
    score += s; details.append(f"布林带: {pos} ({'+' if s>=0 else ''}{s})")

    # OBV（需 vol）
    if vol is not None and len(vol) >= 5:
        obv = calc_obv(close, vol)
        s = 1 if obv["trend"] == "上升" else (-1 if obv["trend"] == "下降" else 0)
        score += s; details.append(f"OBV: {obv['trend']} ({'+' if s>=0 else ''}{s})")

    # 量比（需 vol）
    if vol is not None and len(vol) > 0:
        vr = calc_volume_ratio(vol)
        if vr is not None:
            s = 0.5 if vr["signal"] == "放量" else (-0.5 if vr["signal"] == "缩量" else 0)
            score += s; details.append(f"量比: {vr['量比']}({vr['signal']}) ({'+' if s>=0 else ''}{s})")

    if score >= 4:
        signal = "强多（多指标共振看多）"
    elif score >= 2:
        signal = "偏多"
    elif score <= -4:
        signal = "强空（多指标共振看空）"
    elif score <= -2:
        signal = "偏空"
    else:
        signal = "中性（信号分歧）"

    return {"score": round(score, 1), "signal": signal, "details": details}


# ============ 2. 量价关系矩阵 ============
def calc_price_volume_pattern(close, vol):
    """量价关系矩阵：价格变化 × 成交量变化 → 四象限定位。

    价(近5日涨跌) × 量(量比放缩)：
      价升+量增 = 健康上涨（主力参与，趋势可信）
      价升+量缩 = 量价背离（上涨乏力，警惕见顶）
      价跌+量增 = 恐慌抛售（加速赶底或利空发酵）
      价跌+量缩 = 缩量回调（抛压衰竭，可能见底）

    close: 复权收盘价 Series；vol: 成交量 Series。
    返回 dict：价格方向 + 量方向 + 象限 + 含义。
    """
    if close is None or len(close) == 0 or vol is None or len(vol) == 0:
        return {"pattern": "数据不足"}

    ret = calc_returns(close)
    price_chg = ret.get("近5日涨幅%", 0)
    if price_chg == "N/A":
        price_chg = 0
    price_dir = "升" if float(price_chg) > 0 else ("跌" if float(price_chg) < 0 else "平")

    vr = calc_volume_ratio(vol)
    vol_dir = "增" if (vr and vr["signal"] == "放量") else ("缩" if (vr and vr["signal"] == "缩量") else "平")

    if price_dir == "升" and vol_dir == "增":
        pattern, meaning = "价升量增", "健康上涨（主力参与，趋势可信）"
    elif price_dir == "升" and vol_dir == "缩":
        pattern, meaning = "价升量缩", "量价背离（上涨乏力，警惕见顶）"
    elif price_dir == "跌" and vol_dir == "增":
        pattern, meaning = "价跌量增", "恐慌抛售（加速赶底或利空发酵）"
    elif price_dir == "跌" and vol_dir == "缩":
        pattern, meaning = "价跌量缩", "缩量回调（抛压衰竭，可能见底）"
    else:
        pattern, meaning = f"价{price_dir}量{vol_dir}", "信号不明确"

    return {
        "pattern": pattern,
        "meaning": meaning,
        "price_5d_pct": float(price_chg),
        "volume_ratio": vr["量比"] if vr else "N/A",
    }


# ============ 3. 多因子综合评分 ============
def calc_composite_score(pe_hist_pct=None, fscore=None, return_250d=None,
                         sharpe=None, tech_score=None):
    """多因子综合评分：估值/质量/动量/风险/技术 5 维标准化后等权 → 综合分(0-1)。

    各维标准化到 0-1（越高越好）：
      估值因子 = (100 - pe_hist_pct) / 100  （PE 分位越低=越便宜=分越高）
      质量因子 = fscore / 9
      动量因子 = clip(return_250d / 50, -1, 1) / 2 + 0.5  （涨50%→1，跌50%→0）
      风险因子 = clip(sharpe / 2, 0, 1)  （夏普2→1，0→0）
      技术因子 = (tech_score + 6) / 12  （共振-6→0，+6→1）

    任一因子输入 None 则跳过该维度，剩余维度重新等权。
    返回 dict：各维子分 + 综合分 + 评级。
    """
    factors = {}

    if pe_hist_pct is not None:
        factors["估值"] = max(0, min(1, (100 - pe_hist_pct) / 100))
    if fscore is not None:
        factors["质量"] = max(0, min(1, fscore / 9))
    if return_250d is not None:
        factors["动量"] = max(0, min(1, float(return_250d) / 50 / 2 + 0.5))
    if sharpe is not None:
        factors["风险调整"] = max(0, min(1, float(sharpe) / 2))
    if tech_score is not None:
        factors["技术"] = max(0, min(1, (float(tech_score) + 6) / 12))

    if not factors:
        return {"composite": None, "rating": "数据不足"}

    composite = round(sum(factors.values()) / len(factors), 3)
    rating = "优" if composite >= 0.7 else ("弱" if composite < 0.35 else "中")

    return {"composite": composite, "rating": rating, "factors": {k: round(v, 3) for k, v in factors.items()}}


# ============ 4. 估值-质量-动量三维定位 ============
def calc_factor_positioning(pe_hist_pct=None, fscore=None, return_250d=None):
    """估值-质量-动量三维定位：每维分 3 档，交叉判断投资风格。

    估值(PE历史分位): <30=便宜 / 30-70=合理 / >70=偏贵
    质量(F-Score): ≥7=优 / 4-6=中 / ≤3=弱
    动量(近250日): >10%=强 / -10~10%=平 / <-10%=弱

    返回 dict：三维各档 + 风格标签。
    """
    dims = {}

    if pe_hist_pct is not None:
        p = float(pe_hist_pct)
        dims["估值"] = "便宜" if p < 30 else ("偏贵" if p > 70 else "合理")
    if fscore is not None:
        f = int(fscore)
        dims["质量"] = "优" if f >= 7 else ("弱" if f <= 3 else "中")
    if return_250d is not None:
        r = float(return_250d)
        dims["动量"] = "强" if r > 10 else ("弱" if r < -10 else "平")

    # 组合判断
    cheap = dims.get("估值") == "便宜"
    quality = dims.get("质量") == "优"
    strong_mom = dims.get("动量") == "强"
    weak_mom = dims.get("动量") == "弱"

    if cheap and quality and strong_mom:
        label = "价值修复+动量启动（最佳象限）"
    elif cheap and quality and not strong_mom:
        label = "低估值高质量但动量未启动（左侧布局）"
    elif cheap and not quality:
        label = "低估值但质量偏弱（价值陷阱风险）"
    elif not cheap and quality and strong_mom:
        label = "高质量+强动量但估值不便宜（趋势跟随）"
    elif not cheap and not quality and weak_mom:
        label = "高估值+低质量+弱动量（最差象限，回避）"
    else:
        label = "因子信号混合，需逐维细看"

    return {"dimensions": dims, "positioning": label}


# ============ 5. 业绩拐点检测 ============
def calc_earnings_inflection(fina_df, income_df):
    """业绩拐点检测：ROE 趋势(近4期方向) × 营收增速变化(加速/减速/转负) → 拐点判断。

    fina_df: fina_indicator 结果（含 roe, end_date）
    income_df: income 结果（含 total_revenue, n_income_attr_p, end_date）
    返回 dict：ROE方向 + 增速方向 + 拐点判断 + 数据明细。
    """
    if fina_df is None or income_df is None:
        return {"inflection": "数据不足"}

    roe_trend = calc_roe_trend(fina_df)
    rev_growth = calc_revenue_growth(income_df)

    if len(roe_trend) < 2 or len(rev_growth) < 2:
        return {"inflection": "数据不足(需至少2期)"}

    # ROE 方向（取最近4期，对比首末）
    roe_recent = roe_trend.tail(4)
    roe_vals = roe_recent["roe"].dropna()
    if len(roe_vals) >= 2:
        roe_dir = "上升" if roe_vals.iloc[-1] > roe_vals.iloc[0] else ("下降" if roe_vals.iloc[-1] < roe_vals.iloc[0] else "走平")
    else:
        roe_dir = "数据不足"

    # 营收增速变化（最近2期对比）
    rev_recent = rev_growth.tail(2)
    rev_yoy = rev_recent["rev_yoy"].dropna()
    if len(rev_yoy) >= 2:
        if rev_yoy.iloc[-1] < 0 and rev_yoy.iloc[-2] >= 0:
            growth_dir = "转负（增速由正转负）"
        elif rev_yoy.iloc[-1] > rev_yoy.iloc[-2]:
            growth_dir = "加速"
        elif rev_yoy.iloc[-1] < rev_yoy.iloc[-2]:
            growth_dir = "减速"
        else:
            growth_dir = "走平"
    else:
        growth_dir = "数据不足"

    # 拐点判断
    if roe_dir == "上升" and "加速" in growth_dir:
        inflection = "业绩拐点向上（ROE 上升+增速加速）"
    elif roe_dir == "下降" and ("转负" in growth_dir or "减速" in growth_dir):
        inflection = "业绩拐点向下（ROE 下降+增速放缓/转负）"
    elif roe_dir == "上升" and "减速" in growth_dir:
        inflection = "ROE 改善但增速放缓（复苏初期？）"
    elif roe_dir == "下降" and "加速" in growth_dir:
        inflection = "ROE 下降但增速加速（扩张牺牲利润率？）"
    else:
        inflection = f"趋势延续（ROE {roe_dir}，增速 {growth_dir}）"

    return {
        "roe_direction": roe_dir,
        "growth_direction": growth_dir,
        "inflection": inflection,
        "roe_latest": round(float(roe_vals.iloc[-1]), 2) if len(roe_vals) > 0 else None,
        "rev_yoy_latest": round(float(rev_yoy.iloc[-1]), 2) if len(rev_yoy) > 0 else None,
    }


# ============ 6. 配对相对价值 ============
def calc_pair_relative_value(series_a, series_b, label_a="A", label_b="B"):
    """配对相对价值：两标的多维对比（收益/波动/回撤/夏普/RS）。

    series_a/series_b: 后复权价/复权净值 Series（日期索引）。
    返回 dict：各维对比 + "A 相对 B [优/劣] [强/弱]"。
    """
    if series_a is None or series_b is None or len(series_a) == 0 or len(series_b) == 0:
        return {"comparison": "数据不足"}

    # 归一化 + 收益对比
    out = rebase_series({label_a: series_a, label_b: series_b})
    cr = compare_returns({label_a: series_a, label_b: series_b})

    # 各维指标
    vol_a = calc_volatility(series_a); vol_b = calc_volatility(series_b)
    mdd_a = calc_max_drawdown(series_a); mdd_b = calc_max_drawdown(series_b)
    sharpe_a = calc_sharpe(series_a); sharpe_b = calc_sharpe(series_b)

    # 相对强度
    ret_a = series_a.pct_change().dropna(); ret_b = series_b.pct_change().dropna()
    rs = calc_relative_strength(ret_a, ret_b)

    # 综合判断
    better_return = label_a if (cr.iloc[0].get("近250日涨幅%", 0) or 0) >= (cr.iloc[1].get("近250日涨幅%", 0) or 0) else label_b
    better_risk = label_a if (sharpe_a or 0) >= (sharpe_b or 0) else label_b

    return {
        "rebased": out,
        "compare_returns": cr,
        "volatility": {label_a: vol_a, label_b: vol_b},
        "max_drawdown": {label_a: mdd_a, label_b: mdd_b},
        "sharpe": {label_a: sharpe_a, label_b: sharpe_b},
        "relative_strength": rs,
        "better_return": better_return,
        "better_risk_adjusted": better_risk,
        "summary": f"{better_return} 收益更强，{better_risk} 风险调整后更优",
    }


# ============ 7. 风险预算建议 ============
def calc_risk_budget(var95=None, max_drawdown=None, beta=None, amihud=None, volatility=None):
    """风险预算建议：基于 VaR/回撤/Beta/流动性 综合推算建议仓位上限。

    逻辑：
      - 回撤越深 → 仓位越低（回撤<-40%→≤20%，<-25%→≤40%，其他→≤60%）
      - 波动越高 → 仓位越低（>40%→额外降一档）
      - 流动性差 → 仓位越低（Amihud>0.1→额外降一档）
      - Beta 高 → 仓位越低（>1.2→额外降一档）
    返回 dict：建议仓位 + 风险等级 + 各维风险信号 + 理由。
    """
    base = 60  # 默认仓位上限
    reasons = []

    if max_drawdown is not None:
        mdd = float(max_drawdown)
        if mdd < -40:
            base = min(base, 20); reasons.append(f"最大回撤 {mdd}%（极深，仓位≤20%）")
        elif mdd < -25:
            base = min(base, 40); reasons.append(f"最大回撤 {mdd}%（较深，仓位≤40%）")
        else:
            reasons.append(f"最大回撤 {mdd}%（可控）")

    if volatility is not None:
        vol = float(volatility)
        if vol > 40:
            base = min(base, base - 10); reasons.append(f"年化波动 {vol}%（偏高，额外降仓）")
        else:
            reasons.append(f"年化波动 {vol}%（适中）")

    if amihud is not None:
        ai = float(amihud)
        if ai > 0.1:
            base = min(base, base - 10); reasons.append(f"Amihud {ai}（流动性差，进出成本高）")
        else:
            reasons.append(f"Amihud {ai}（流动性好）")

    if beta is not None:
        b = float(beta)
        if b > 1.2:
            base = min(base, base - 10); reasons.append(f"Beta {b}（高弹性，放大波动）")
        elif b < 0.8:
            reasons.append(f"Beta {b}（防御型，波动小于大盘）")
        else:
            reasons.append(f"Beta {b}（接近大盘）")

    if var95 is not None:
        reasons.append(f"VaR95 {var95}（单日最大亏损预期）")

    base = max(base, 10)  # 最低 10%
    level = "高" if base <= 20 else ("中" if base <= 40 else "低")

    return {
        "suggested_position_pct": base,
        "risk_level": level,
        "reasons": reasons,
    }


# ============ 8. 筹码-股价交叉验证 ============
def calc_chip_price_cross(df_holders, df_price):
    """筹码-股价交叉验证：股东户数变化方向 × 股价变化方向 → 4 种组合 + 健康度。

    户数下降+股价上涨 = 筹码锁定上涨（健康上涨，主力锁仓）
    户数下降+股价下跌 = 主力被套（可能阶段见底）
    户数上升+股价上涨 = 散户接盘（警惕见顶）
    户数上升+股价下跌 = 筹码分散下跌（抛压加重）

    df_holders: stk_holdernumber 结果（含 end_date, holder_num）
    df_price: daily 结果（含 trade_date, close），需先排序升序
    返回 dict：户数方向 + 股价方向 + 组合 + 健康度判断。
    """
    if df_holders is None or df_price is None:
        return {"cross": "数据不足"}

    hc = calc_holder_concentration(df_holders)
    ret = calc_returns(df_price.sort_values("trade_date")["close"])

    holder_chg = hc.get("latest_qoq", 0)
    if holder_chg == "N/A":
        holder_chg = 0
    holder_dir = "降" if float(holder_chg) < -1 else ("升" if float(holder_chg) > 1 else "平")

    price_20d = ret.get("近20日涨幅%", 0)
    if price_20d == "N/A":
        price_20d = 0
    price_dir = "涨" if float(price_20d) > 0 else ("跌" if float(price_20d) < 0 else "平")

    if holder_dir == "降" and price_dir == "涨":
        cross, health = "户数降+股价涨", "筹码锁定上涨（健康，主力锁仓）"
    elif holder_dir == "降" and price_dir == "跌":
        cross, health = "户数降+股价跌", "主力被套（可能阶段见底）"
    elif holder_dir == "升" and price_dir == "涨":
        cross, health = "户数升+股价涨", "散户接盘（警惕见顶）"
    elif holder_dir == "升" and price_dir == "跌":
        cross, health = "户数升+股价跌", "筹码分散下跌（抛压加重）"
    else:
        cross, health = f"户数{holder_dir}+股价{price_dir}", "信号不明确"

    return {
        "cross": cross,
        "health": health,
        "holder_signal": hc.get("signal", "N/A"),
        "holder_qoq": holder_chg,
        "price_20d_pct": float(price_20d),
    }
