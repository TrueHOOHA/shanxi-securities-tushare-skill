#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""token 文件读取工具：data_api 与 check_env 的唯一实现，避免两份逻辑漂移。

token 文件即 `sxsc_tushare.set_token()` 写入的位置（当前构建为 ~/sxsc_tk.csv，旧构建可能
为 ~/jsdata_tk.csv），内容为首行表头 `token`、次行 token 值的 csv。
不解析 SDK 内部常量：文件名走候选列表，候选都解析不到时再由 SDK 自己读。
"""
import os
from typing import Optional


def token_file_candidates():
    """token 文件候选路径（由近到旧）。

    `sx.set_token()` 写入的文件名随包构建而变，这里列已知名而不去读 SDK 内部常量；
    候选都解析不到时，`data_api` / `check_env` 会退回让 `sx.get_api(env=...)` 自己读，
    所以换构建也不会误报。
    """
    home = os.path.expanduser("~")
    return [os.path.join(home, n) for n in ("sxsc_tk.csv", "jsdata_tk.csv")]


def token_file_path() -> str:
    """返回实际使用的 token 文件路径：优先已存在的候选，否则主默认名（用于提示）。"""
    for path in token_file_candidates():
        if os.path.isfile(path):
            return path
    return token_file_candidates()[0]


def _read_token_text(path: str) -> Optional[str]:
    """按文本解析 token 文件（容忍表头/注释/UTF-16/GBK）。

    只接受两种可明确判定的格式，避免把表头或注释当凭证：
      - 表头 `token`（或单行 `token,值`）+ 下一行值
      - 仅一行有效内容（无表头手写文件）
    多行且无 token 表头（如误写成 `tk\\n值`）返回 None，由调用方报"未找到 token"。
    """
    text = None
    for enc in ("utf-8-sig", "utf-16", "gbk"):
        try:
            with open(path, "r", encoding=enc) as f:
                text = f.read()
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError:
            return None
    if text is None:
        return None

    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and not ln.lstrip().startswith("#")]
    for i, ln in enumerate(lines):
        _low = ln.lower()
        if _low == "token":
            if i + 1 < len(lines):
                return lines[i + 1].split(",")[0].strip() or None
            return None
        if _low.startswith("token,"):
            return ln.split(",", 1)[1].strip() or None
    if len(lines) == 1:
        return lines[0].split(",")[0].strip() or None
    return None


def load_token() -> Optional[str]:
    """从 token 文件读取明文 token（不读环境变量）。

    该文件即 `sxsc_tushare.set_token()` 写入的位置：65 字节左右的 csv，
    首行表头 `token`，次行 token 值。手动创建即可，不需再经过环境变量。

    不直接调 `sx.get_token()` 的原因：它在失败时会向 stdout 打 `get token error.`，
    会把日志/报告输出弄脏；这里自己读并保持同上解析口径，另容忍“只有一行 token”的
    手写文件（无表头）与 UTF-16/GBK 编码（Windows PowerShell 常见）。

    Returns:
        token 字符串；文件不存在 / 为空 / 解析失败或不可读时返回 None。
    """
    path = token_file_path()
    if not os.path.isfile(path):
        return None

    try:
        import pandas as pd

        # dtype=str：纯数字 token 不会因类型推断丢掉前导零
        df = pd.read_csv(path, dtype=str)
        if not df.empty and "token" in df.columns:
            val = df.loc[0, "token"]
            if not pd.isna(val) and str(val).strip():
                return str(val).strip()
    except Exception:
        pass
    return _read_token_text(path)


def token_help() -> str:
    """token 缺失时的可操作提示。"""
    path = token_file_path()
    return (
        f"未找到可用 token。请把 token 写入：{path}\n"
        f"  方式一（推荐，SDK 自带）：python -c \"import sxsc_tushare as sx; sx.set_token('你的token')\"\n"
        f"  方式二（手写文件）：首行写 token，次行写 token 值，UTF-8 编码无 BOM\n"
        f"  若文件已存在仍报错：请检查文件读取权限与编码（支持 UTF-8/UTF-16/GBK）"
    )
