#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
归因分析模块：CAPM Beta/Alpha、Piotroski F-Score。

用于收益归因分解与财务健康量化评分。
"""

import numpy as np
import pandas as pd


# ============ 收益归因：Beta/Alpha ============
def calc_beta_alpha(stock_returns, market_returns, risk_free=0.02, periods=250):
    """CAPM Beta/Alpha 分解。
    stock_returns / market_returns: 日收益率序列（对齐索引）。
    返回 Beta（市场敏感度）、Alpha（超额收益年化）。
    """
    aligned = pd.DataFrame({"stock": stock_returns, "market": market_returns}).dropna()
    if len(aligned) < 30:
        return None
    stock = np.asarray(aligned["stock"], dtype=float)
    market = np.asarray(aligned["market"], dtype=float)
    cov = np.cov(stock, market)[0, 1]
    # ddof=1：与 OLS 回归口径一致；np.var 默认 ddof=0（总体方差）会把 Beta
    # 系统性放大 n/(n-1)（n=60 时约 1.7%），与滚动 Beta 也不一致
    var = np.var(market, ddof=1)
    if var == 0:
        return None
    beta = round(float(cov / var), 2)
    alpha_daily = stock.mean() - (risk_free / periods) - beta * (market.mean() - risk_free / periods)
    alpha_annual = round(float(alpha_daily * periods * 100), 2)
    # R² = 回归解释力；低于 0.3 时 Beta/Alpha 估计不稳定，展示与风格判定均应标注不可靠
    r2 = round(float(np.corrcoef(stock, market)[0, 1] ** 2), 3)
    return {
        "Beta": beta,
        "Alpha(年化%)": alpha_annual,
        "R2": r2,
        "interpretation": f"市场敏感度{'高' if beta > 1.2 else ('低' if beta < 0.8 else '适中')}，{'跑赢' if alpha_annual > 0 else '跑输'}市场"
    }


# ============ 财务健康评分：Piotroski F-Score ============
def _norm_end_date(df, date_col="end_date"):
    """把报告期列规范为 'YYYYMMDD' 字符串，供跨表按报告期对齐。
    datetime64 列若直接 astype(str) 会得到 '2023-12-31'（endswith('1231') 判定失败、
    跨表字符串也不相等），必须先按日期格式化成 8 位；已是字符串/整数的保持原样。
    返回新列（不修改入参）。
    """
    col = df[date_col]
    if pd.api.types.is_datetime64_any_dtype(col):
        return col.dt.strftime("%Y%m%d")
    s = col.astype(str)
    # Tushare 有时把日期返回成 20231231.0 这样的浮点字符串，去掉小数尾巴
    return s.where(~s.str.match(r"^\d+\.0$"), s.str.slice(0, -2))


def _filter_annual(df, date_col="end_date"):
    """筛年报并去重：保留 end_date 为 12-31 的年报，同报告期保留最新公告。
    兼容 end_date 为字符串（'20231231'）与 datetime64/datetime 两种类型。
    """
    if df is None or len(df) == 0:
        return df
    df = df.copy()
    df[date_col] = _norm_end_date(df, date_col)
    # 规范后为 'YYYYMMDD'（datetime）或原样字符串，两种类型都只认 1231 结尾
    df = df[df[date_col].str.endswith("1231")].copy()
    if df.empty:
        return df
    if "ann_date" in df.columns:
        df = df.sort_values([date_col, "ann_date"]).drop_duplicates(date_col, keep="last")
    else:
        df = df.drop_duplicates(date_col, keep="last")
    return df.sort_values(date_col)


def calc_piotroski_fscore(df_fina, df_income, df_cashflow, df_balances=None):
    """Piotroski F-Score（0-9 分）。
    需要 2 期年报数据（当期 + 去年同期）。各表先按 end_date 筛年报(1231)、
    去重后取最近 2 期对比，避免拿 Q3 与 Q2 比导致的非同比错误。

    df_fina: fina_indicator 结果（含 npta/roa, grossprofit_margin, debt_to_assets,
             current_ratio, assets_turn；均为接口预计算字段）
    df_income: income 结果（含 n_income_attr_p）
    df_cashflow: cashflow 结果（含 n_cashflow_act）
    df_balances: balancesheet 结果（含 total_share, total_assets, comp_type 可选），
                 用于判断是否新增股本；comp_type 用于金融业识别(2银行/3保险/4证券)；
                 可选，缺失则第 7 项计 0 分并标注数据缺失。

    ROA 取值口径：优先 npta(总资产净利润，Piotroski 净利口径)，次选 roa(EBIT 口径，
    部分金融业为空)，最后用 n_income_attr_p/total_assets 自算——兼容证券/银行等 roa 字段为空的标的。

    ⚠️ 跨表对齐：以 fina_indicator 的当期/上期 end_date 为锚，其余表按 end_date 精确取数。
    各表披露进度常不一致（income/cashflow 慢一期），按"最后一行"取数会让第 4 项
    "经营现金流>净利润"退化成跨年度比较；某表缺该报告期时该项记"数据缺失"，不静默错配年份。

    9 项标准：
      盈利能力   1) ROA>0  2) 经营现金流>0  3) ΔROA>0  4) 经营现金流>净利润(应计质量)
      杠杆/流动/融资 5) Δ资产负债率≤0  6) Δ流动比率>0  7) 未新增股本(Δtotal_share≤0)
      运营效率   8) Δ毛利率>0  9) Δ总资产周转率>0
    返回 F-Score/rating/details，以及 有效项/缺失项 计数；金融业额外给 comp_note 弱参考提示。
    """
    df_fina = _filter_annual(df_fina)
    df_income = _filter_annual(df_income)
    df_cashflow = _filter_annual(df_cashflow)
    df_balances = _filter_annual(df_balances) if df_balances is not None else None

    score = 0
    details = []

    # 报告期锚点：以 fina_indicator 最近两期年报 end_date 为准（_filter_annual 已按升序排列）
    cur_date = df_fina["end_date"].iloc[-1] if df_fina is not None and len(df_fina) >= 2 else None
    prev_date = df_fina["end_date"].iloc[-2] if df_fina is not None and len(df_fina) >= 2 else None

    def _val_at(df, col, end_date):
        """按报告期精确取数；该表没有这一期就返回 None（不要退回"最后一行"）。"""
        if df is None or end_date is None or col not in df.columns or "end_date" not in df.columns:
            return None
        hit = df[df["end_date"] == end_date]
        if hit.empty:
            return None
        v = hit[col].iloc[-1]
        return v if pd.notna(v) else None

    def _get_roa(end_date):
        # 优先 npta(净利口径)，次选 roa(EBIT 口径，金融业常空)，最后用 净利润/总资产 自算
        v = _val_at(df_fina, "npta", end_date)
        if v is None:
            v = _val_at(df_fina, "roa", end_date)
        if v is None:
            ni_ = _val_at(df_income, "n_income_attr_p", end_date)
            ta_ = _val_at(df_balances, "total_assets", end_date)
            if ni_ is not None and ta_ not in (None, 0, 0.0):
                v = ni_ / ta_ * 100
        return v

    # 1. ROA > 0
    roa = _get_roa(cur_date)
    s = roa is not None and roa > 0
    score += s; details.append(f"ROA>0: {'是' if s else ('否' if roa is not None else '数据缺失')}")

    # 2. 经营现金流 > 0
    cfo = _val_at(df_cashflow, "n_cashflow_act", cur_date)
    s = cfo is not None and cfo > 0
    score += s; details.append(f"经营现金流>0: {'是' if s else ('否' if cfo is not None else '数据缺失')}")

    # 3. ΔROA > 0
    roa_prev = _get_roa(prev_date)
    if roa is None or roa_prev is None:
        details.append("ΔROA>0: 数据不足")
    else:
        s = roa > roa_prev; score += s; details.append(f"ΔROA>0: {'是' if s else '否'}")

    # 4. 经营现金流 > 净利润（盈利质量）——两者必须取同一报告期
    ni = _val_at(df_income, "n_income_attr_p", cur_date)
    if cfo is None or ni is None:
        details.append("现金流>净利润: 数据缺失(该报告期数据未披露)")
    else:
        s = cfo > ni; score += s; details.append(f"现金流>净利润: {'是' if s else '否'}")

    # 5. Δ资产负债率 ≤ 0（杠杆下降）
    da = _val_at(df_fina, "debt_to_assets", cur_date); da_prev = _val_at(df_fina, "debt_to_assets", prev_date)
    if da is None or da_prev is None:
        details.append("Δ资产负债率≤0: 数据不足")
    else:
        s = da <= da_prev; score += s; details.append(f"Δ资产负债率≤0: {'是' if s else '否'}")

    # 6. Δ流动比率 > 0
    cr = _val_at(df_fina, "current_ratio", cur_date); cr_prev = _val_at(df_fina, "current_ratio", prev_date)
    if cr is None or cr_prev is None:
        details.append("Δ流动比率>0: 数据不足")
    else:
        s = cr > cr_prev; score += s; details.append(f"Δ流动比率>0: {'是' if s else '否'}")

    # 7. 未新增股本
    sh = _val_at(df_balances, "total_share", cur_date)
    sh_prev = _val_at(df_balances, "total_share", prev_date)
    if sh is None or sh_prev is None:
        details.append("未新增股本: 数据缺失(未提供资产负债表)")
    else:
        s = sh <= sh_prev; score += s; details.append(f"未新增股本: {'是' if s else '否'}")

    # 8. Δ毛利率 > 0
    gm = _val_at(df_fina, "grossprofit_margin", cur_date); gm_prev = _val_at(df_fina, "grossprofit_margin", prev_date)
    if gm is None or gm_prev is None:
        details.append("Δ毛利率>0: 数据不足")
    else:
        s = gm > gm_prev; score += s; details.append(f"Δ毛利率>0: {'是' if s else '否'}")

    # 9. Δ总资产周转率 > 0
    at = _val_at(df_fina, "assets_turn", cur_date); at_prev = _val_at(df_fina, "assets_turn", prev_date)
    if at is None or at_prev is None:
        details.append("Δ总资产周转率>0: 数据不足")
    else:
        s = at > at_prev; score += s; details.append(f"Δ总资产周转率>0: {'是' if s else '否'}")

    rating = "强" if score >= 7 else ("弱" if score <= 2 else "中等")
    # 金融业识别（balancesheet comp_type: 1工商业 2银行 3保险 4证券）
    comp_note = None
    if df_balances is not None and "comp_type" in df_balances.columns and len(df_balances):
        ct = df_balances["comp_type"].iloc[-1]
        if str(ct) in ("2", "3", "4"):
            comp_note = (f"标的为金融机构(comp_type={ct})，F-Score 为工商业设计，"
                         f"毛利率/资产周转率等项对金融业语义失真，结果仅弱参考")
    missing = sum(1 for d in details if "数据缺失" in d or "数据不足" in d)
    out = {"F-Score": score, "rating": rating, "details": details,
           "有效项": 9 - missing, "缺失项": missing}
    if comp_note:
        out["comp_note"] = comp_note
    return out
