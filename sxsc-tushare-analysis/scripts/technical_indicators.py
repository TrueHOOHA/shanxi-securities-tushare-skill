#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
技术指标模块：MACD、RSI、KDJ、布林带、OBV、量比。

用于行情趋势维度的技术分析，补充均线之外的趋势/动量/波动区间信号。
"""

import numpy as np


def calc_macd(df_close, fast=12, slow=26, signal=9):
    """MACD 指标。返回 (DIF, DEA, MACD柱) 最新值。"""
    ema_fast = df_close.ewm(span=fast, adjust=False).mean()
    ema_slow = df_close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd_bar = (dif - dea) * 2
    return {
        "DIF": round(dif.iloc[-1], 2),
        "DEA": round(dea.iloc[-1], 2),
        "MACD": round(macd_bar.iloc[-1], 2),
        "signal": "金叉" if dif.iloc[-1] > dea.iloc[-1] else "死叉",
    }


def calc_rsi(df_close, period=14):
    """RSI 相对强弱指标。返回最新 RSI 值与超买/超卖判断。"""
    delta = df_close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    rsi = 100 - 100 / (1 + rs)
    val = round(rsi.iloc[-1], 1)
    flag = "超买" if val > 70 else ("超卖" if val < 30 else "中性")
    return {"RSI": val, "signal": flag}


def calc_kdj(df_high, df_low, df_close, period=9):
    """KDJ 随机指标。返回 K/D/J 最新值。"""
    low_min = df_low.rolling(period).min()
    high_max = df_high.rolling(period).max()
    rsv = (df_close - low_min) / (high_max - low_min) * 100
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    j = 3 * k - 2 * d
    return {
        "K": round(k.iloc[-1], 2),
        "D": round(d.iloc[-1], 2),
        "J": round(j.iloc[-1], 2),
        "signal": "金叉" if k.iloc[-1] > d.iloc[-1] and k.iloc[-2] <= d.iloc[-2] else
                  ("死叉" if k.iloc[-1] < d.iloc[-1] and k.iloc[-2] >= d.iloc[-2] else "无叉"),
    }


def calc_boll(df_close, window=20, num_std=2):
    """布林带。返回上轨/中轨/下轨及当前位置判断。"""
    mid = df_close.rolling(window).mean()
    std = df_close.rolling(window).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    price = df_close.iloc[-1]
    up = round(upper.iloc[-1], 2)
    lo = round(lower.iloc[-1], 2)
    md = round(mid.iloc[-1], 2)
    pos = "触及上轨（超买）" if price >= up * 0.98 else (
          "触及下轨（超卖）" if price <= lo * 1.02 else "中轨附近")
    return {"上轨": up, "中轨": md, "下轨": lo, "position": pos}


def calc_obv(df_close, df_vol):
    """OBV（能量潮）。返回 OBV 趋势方向。"""
    direction = np.where(df_close.diff() > 0, 1, np.where(df_close.diff() < 0, -1, 0))
    obv = (direction * df_vol).cumsum()
    trend = "上升" if obv.iloc[-1] > obv.iloc[-5] else ("下降" if obv.iloc[-1] < obv.iloc[-5] else "持平")
    return {"OBV": round(obv.iloc[-1], 0), "trend": trend}


def calc_volume_ratio(df_vol, window=5):
    """量比 = 当日成交量 / 近 N 日平均成交量。"""
    ma_vol = df_vol.tail(window + 1).head(window).mean()
    if ma_vol == 0:
        return None
    ratio = df_vol.iloc[-1] / ma_vol
    flag = "放量" if ratio > 2 else ("缩量" if ratio < 0.5 else "正常")
    return {"量比": round(ratio, 2), "signal": flag}
