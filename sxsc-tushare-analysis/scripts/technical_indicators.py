#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
技术指标模块：MACD、RSI、KDJ、布林带、OBV、量比。

用于行情趋势维度的技术分析，补充均线之外的趋势/动量/波动区间信号。
"""

import numpy as np


def calc_macd(df_close, fast=12, slow=26, signal=9):
    """MACD 指标。返回 (DIF, DEA, MACD柱) 最新值。
    signal 判定真实金叉/死叉：比较前一日与当日 DIF/DEA 的相对位置变化。
    """
    ema_fast = df_close.ewm(span=fast, adjust=False).mean()
    ema_slow = df_close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd_bar = (dif - dea) * 2
    if len(dif) == 0:
        return {"DIF": "N/A", "DEA": "N/A", "MACD": "N/A", "signal": "数据不足"}
    if len(dif) < 2:
        cross = "数据不足"
    elif dif.iloc[-1] > dea.iloc[-1] and dif.iloc[-2] <= dea.iloc[-2]:
        cross = "金叉"
    elif dif.iloc[-1] < dea.iloc[-1] and dif.iloc[-2] >= dea.iloc[-2]:
        cross = "死叉"
    else:
        cross = "多头排列" if dif.iloc[-1] > dea.iloc[-1] else "空头排列"
    return {
        "DIF": round(float(dif.iloc[-1]), 2),
        "DEA": round(float(dea.iloc[-1]), 2),
        "MACD": round(float(macd_bar.iloc[-1]), 2),
        "signal": cross,
    }


def calc_rsi(df_close, period=14):
    """RSI 相对强弱指标。返回最新 RSI 值与超买/超卖判断。"""
    if len(df_close) < period + 1:
        return {"RSI": "N/A", "signal": "数据不足"}
    delta = df_close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    rsi = 100 - 100 / (1 + rs)
    val = rsi.iloc[-1]
    if np.isnan(val):
        return {"RSI": "N/A", "signal": "数据不足"}
    val = round(float(val), 1)
    flag = "超买" if val > 70 else ("超卖" if val < 30 else "中性")
    return {"RSI": val, "signal": flag}


def calc_kdj(df_high, df_low, df_close, period=9):
    """KDJ 随机指标。返回 K/D/J 最新值。"""
    if len(df_close) < period:
        return {"K": "N/A", "D": "N/A", "J": "N/A", "signal": "数据不足"}
    low_min = df_low.rolling(period).min()
    high_max = df_high.rolling(period).max()
    denom = high_max - low_min
    rsv = (df_close - low_min) / denom * 100
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    j = 3 * k - 2 * d
    if np.isnan(k.iloc[-1]) or np.isnan(d.iloc[-1]):
        return {"K": "N/A", "D": "N/A", "J": "N/A", "signal": "数据不足"}
    return {
        "K": round(float(k.iloc[-1]), 2),
        "D": round(float(d.iloc[-1]), 2),
        "J": round(float(j.iloc[-1]), 2),
        "signal": "金叉" if k.iloc[-1] > d.iloc[-1] and k.iloc[-2] <= d.iloc[-2] else
                  ("死叉" if k.iloc[-1] < d.iloc[-1] and k.iloc[-2] >= d.iloc[-2] else "无叉"),
    }


def calc_boll(df_close, window=20, num_std=2):
    """布林带。返回上轨/中轨/下轨及当前位置判断。"""
    if len(df_close) < window:
        return {"上轨": "N/A", "中轨": "N/A", "下轨": "N/A", "position": "数据不足"}
    mid = df_close.rolling(window).mean()
    std = df_close.rolling(window).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    price = df_close.iloc[-1]
    up = upper.iloc[-1]
    lo = lower.iloc[-1]
    md = mid.iloc[-1]
    if np.isnan(up) or np.isnan(lo):
        return {"上轨": "N/A", "中轨": "N/A", "下轨": "N/A", "position": "数据不足"}
    up = round(float(up), 2); lo = round(float(lo), 2); md = round(float(md), 2)
    std_last = std.iloc[-1]
    if not np.isnan(std_last) and std_last == 0:
        return {"上轨": up, "中轨": md, "下轨": lo, "position": "无波动(区间恒定)"}
    pos = "触及上轨（超买）" if price >= up * 0.98 else (
          "触及下轨（超卖）" if price <= lo * 1.02 else "中轨附近")
    return {"上轨": up, "中轨": md, "下轨": lo, "position": pos}


def calc_obv(df_close, df_vol):
    """OBV（能量潮）。返回 OBV 趋势方向。需至少 5 个数据点判断趋势。"""
    if len(df_close) < 5:
        return {"OBV": "N/A", "trend": "数据不足(<5日)"}
    direction = np.where(df_close.diff() > 0, 1, np.where(df_close.diff() < 0, -1, 0))
    obv = (direction * df_vol).cumsum()
    trend = "上升" if obv.iloc[-1] > obv.iloc[-5] else ("下降" if obv.iloc[-1] < obv.iloc[-5] else "持平")
    return {"OBV": round(float(obv.iloc[-1]), 0), "trend": trend}


def calc_volume_ratio(df_vol, window=5):
    """量比 = 当日成交量 / 近 N 日平均成交量。"""
    if len(df_vol) == 0:
        return None
    ma_vol = df_vol.tail(window + 1).head(window).mean()
    if ma_vol == 0 or (isinstance(ma_vol, float) and np.isnan(ma_vol)):
        return None
    ratio = df_vol.iloc[-1] / ma_vol
    flag = "放量" if ratio > 2 else ("缩量" if ratio < 0.5 else "正常")
    return {"量比": round(float(ratio), 2), "signal": flag}
