#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
组合分析模块：跨维度因子组合、三维定位、风险预算。

将各单维度指标（估值分位 / F-Score / 区间收益 / 夏普 / 回撤 / 波动 / Beta / VaR / Amihud）
组合成跨维度的客观判断，从"列指标"升级到"交叉判断"。
本模块只消费**已计算好的标量指标**，不直接取数；不依赖兄弟模块的导入。

（历史遗留：calc_technical_confluence / calc_price_volume_pattern / calc_earnings_inflection /
calc_pair_relative_value / calc_chip_price_cross 因全库无调用方，已删除，仅保留方法论文档）
"""


# ============ 1. 多因子综合评分 ============
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


# ============ 2. 估值-质量-动量三维定位 ============
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

    # 合规：仅陈述因子组合的客观状态，不含优劣评判、机会判断或操作指向
    if cheap and quality and strong_mom:
        label = "低估值+高质量+动量为强"
    elif cheap and quality and not strong_mom:
        label = "低估值+高质量，动量非强"
    elif cheap and not quality:
        label = "低估值，质量非优"
    elif not cheap and quality and strong_mom:
        label = "高质量+动量为强，估值非低"
    elif not cheap and not quality and weak_mom:
        label = "估值非低+质量非优+动量为弱"
    else:
        label = "因子信号混合，需逐维细看"

    return {"dimensions": dims, "positioning": label}


# ============ 3. 风险预算（风险评估参考） ============
def calc_risk_budget(var95=None, max_drawdown=None, beta=None, amihud=None, volatility=None):
    """风险预算参考：基于 VaR/回撤/Beta/流动性 综合推算风险承受度参考值。

    逻辑（各维风险信号综合，仅作风险评估参考，非配置建议）：
      - 回撤越深 → 风险越高（回撤<-40%→参考≤20%，<-25%→参考≤40%，其他→参考≤60%）
      - 波动越高 → 风险越高（>40%→参考值降一档）
      - 流动性差 → 风险越高（Amihud>0.1→参考值降一档）
      - Beta 高 → 风险越高（>1.2→参考值降一档）
    返回 dict：风险承受度参考值 + 风险等级 + 各维风险信号 + 理由。
    ⚠️ 全部输入均为 None 时不编造默认仓位：返回 None 风险等级（调用方以
    `if budget.get("risk_level")` 判定是否展示，None 即不展示）。
    reasons 始终保留"某维无数据"的说明，便于区分"数据不足"与"风险中性"。
    """
    base = None  # 无任何有效输入时为 None，不预置 60 这类无依据的默认值
    reasons = []

    if max_drawdown is not None:
        mdd = float(max_drawdown)
        if mdd < -40:
            base = 20; reasons.append(f"最大回撤 {mdd}%（极深）")
        elif mdd < -25:
            base = 40; reasons.append(f"最大回撤 {mdd}%（较深）")
        else:
            base = 60; reasons.append(f"最大回撤 {mdd}%（可控）")
    else:
        reasons.append("最大回撤 无数据")

    if volatility is not None:
        vol = float(volatility)
        if vol > 40:
            base = (base if base is not None else 60) - 10; reasons.append(f"年化波动 {vol}%（偏高）")
        else:
            reasons.append(f"年化波动 {vol}%（适中）")
    else:
        reasons.append("年化波动 无数据")

    if amihud is not None:
        ai = float(amihud)
        _ai_txt = f"{ai:.6f}".rstrip("0").rstrip(".")
        if ai > 0.1:
            base = (base if base is not None else 60) - 10; reasons.append(f"Amihud {_ai_txt}（流动性差，进出成本高）")
        else:
            reasons.append(f"Amihud {_ai_txt}（流动性好）")
    else:
        reasons.append("Amihud 无数据")

    if beta is not None:
        b = float(beta)
        if b > 1.2:
            base = (base if base is not None else 60) - 10; reasons.append(f"Beta {b}（高弹性，放大波动）")
        elif b < 0.8:
            reasons.append(f"Beta {b}（防御型，波动小于大盘）")
        else:
            reasons.append(f"Beta {b}（接近大盘）")
    else:
        reasons.append("Beta 无数据")

    if var95 is not None:
        reasons.append(f"VaR95 {var95}（单日最大亏损预期）")
    else:
        reasons.append("VaR95 无数据")

    if base is None:
        # 无任何有效风险输入：不给具体仓位、不给风险等级（"低"是没有依据的结论）
        return {
            "suggested_position_pct": None,
            "risk_level": None,
            "reasons": reasons + ["各维风险因子均无数据，不给出风险承受度参考值"],
        }

    base = max(base, 10)  # 最低 10%
    level = "高" if base <= 20 else ("中" if base <= 40 else "低")

    return {
        "suggested_position_pct": base,
        "risk_level": level,
        "reasons": reasons,
    }
