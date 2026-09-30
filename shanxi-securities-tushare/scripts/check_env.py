#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
校验脚本环境，并把结果缓存到 JSON，避免每次重复校验。

校验项：
1. SXSC_TUSHARE_TOKEN 是否已设置（必选，缺失则无法执行 skill）
2. sxsc_tushare 库是否已安装（可选，决定走 SDK 还是 HTTP 方式）

用法：
    python check_env.py            # 读取缓存（SDK 信息）或执行校验（token 始终实时检查）
    python check_env.py --force    # 强制重新校验并覆盖缓存
    python check_env.py --check    # 只校验不写缓存
"""

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

CACHE_FILE = Path(__file__).resolve().parent / "env_check.json"
TOKEN_ENV = "SXSC_TUSHARE_TOKEN"
SDK_NAME = "sxsc_tushare"


def _sdk_version():
    try:
        mod = importlib.import_module(SDK_NAME)
        return getattr(mod, "__version__", "unknown")
    except Exception:
        return None


def _sdk_init_error(token):
    """尝试用与 DataAPI 相同的方式初始化 SDK，返回 None 表示可用，否则返回失败原因。

    ⚠️ 只判 import 成功不代表 SDK 可用：sxsc_tushare.set_token() 会把 token 缓存写
    到用户主目录（如 jsdata_tk.csv），在沙箱/只读主目录下会 PermissionError，
    导致 DataAPI 静默降级为 HTTP——而 check_env 此前仍报 mode=sdk，与实际运行不符。
    """
    if not token:
        return "token 未设置"
    try:
        import sxsc_tushare as sx
        sx.set_token(token)
        _api = sx.get_api(env="prd")
        return None
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def run_check():
    """执行一次真实校验，返回结果 dict。"""
    token = os.getenv(TOKEN_ENV)
    sdk_available = importlib.util.find_spec(SDK_NAME) is not None
    sdk_version = _sdk_version() if sdk_available else None
    sdk_init_err = _sdk_init_error(token) if sdk_available else None

    return {
        "token_set": bool(token),
        "sdk_available": sdk_available,
        "sdk_version": sdk_version,
        "sdk_init_ok": sdk_init_err is None if sdk_available else False,
        "sdk_init_error": sdk_init_err,
        # mode 以"能否真正初始化"为准，而不是"能否 import"：
        # import 成功但 set_token 写缓存失败（沙箱/只读主目录）时 DataAPI 会走 HTTP
        "mode": "sdk" if (sdk_available and sdk_init_err is None) else "http",
        "checked_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }


def load_cache():
    """读取缓存，若不存在或结构异常返回 None。"""
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "token_set" in data:
            return data
    except Exception:
        return None
    return None


def save_cache(result):
    CACHE_FILE.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser(description="校验山西证券 Tushare skill 环境")
    parser.add_argument("--force", action="store_true", help="强制重新校验并覆盖缓存")
    parser.add_argument("--check", action="store_true", help="只校验不写缓存")
    parser.add_argument("--path", action="store_true", help="仅打印缓存文件路径")
    args = parser.parse_args()

    if args.path:
        print(CACHE_FILE)
        return 0

    # 缓存命中时复用 SDK 信息，但 token 始终实时校验
    # （用户可能已删除/更换环境变量中的 token，不能仅凭缓存判定可用）
    if not args.force and not args.check:
        cached = load_cache()
        token_now = bool(os.getenv(TOKEN_ENV))
        if cached and cached.get("token_set") and token_now:
            # 旧版缓存没有 sdk_init_ok 字段（mode 只按 import 判定，与实际运行不符），
            # 必须刷新而不是复用，否则会继续给出误导性的 mode=sdk
            if "sdk_init_ok" not in cached:
                cached = None
            else:
                print("使用缓存校验结果（token 已实时确认）：")
                print(json.dumps(cached, ensure_ascii=False, indent=2))
                if not cached.get("sdk_available"):
                    print("\n提示：未安装 sxsc_tushare 库，将使用 HTTP 协议方式调取数据。")
                elif not cached.get("sdk_init_ok"):
                    print(f"\n提示：SDK 已安装但初始化失败（{cached.get('sdk_init_error') or '未知原因'}），"
                          "将使用 HTTP 协议方式调取数据。")
                return 0

    # 缓存未命中或 token 未设置：实时检测
    result = run_check()
    if not args.check:
        save_cache(result)
        print("已重新校验并写入缓存：")
    else:
        print("校验结果（未写缓存）：")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not result["token_set"]:
        print("\n错误：未设置环境变量 SXSC_TUSHARE_TOKEN，无法执行 skill。")
        print("请参考 README 配置后重试。")
        return 1
    if not result["sdk_available"]:
        print("\n提示：未安装 sxsc_tushare 库，将使用 HTTP 协议方式调取数据。")
    elif not result["sdk_init_ok"]:
        print(f"\n提示：SDK 已安装但初始化失败（{result.get('sdk_init_error') or '未知原因'}），"
              "将使用 HTTP 协议方式调取数据。")
    return 0


if __name__ == "__main__":
    sys.exit(main())