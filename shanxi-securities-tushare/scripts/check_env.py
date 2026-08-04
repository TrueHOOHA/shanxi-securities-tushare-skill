#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
校验脚本环境，并把结果缓存到 JSON，避免每次重复校验。

校验项：
1. SXSC_TUSHARE_TOKEN 是否已设置（必选，缺失则无法执行 skill）
2. sxsc_tushare 库是否已安装（可选，决定走 SDK 还是 HTTP 方式）

用法：
    python check_env.py            # 读取缓存（若存在且 token 未变）或执行校验
    python check_env.py --force    # 强制重新校验并覆盖缓存
    python check_env.py --check    # 只校验不写缓存
"""

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

CACHE_FILE = Path(__file__).resolve().parent / "env_check.json"
TOKEN_ENV = "SXSC_TUSHARE_TOKEN"
SDK_NAME = "sxsc_tushare"


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _sdk_version():
    try:
        mod = importlib.import_module(SDK_NAME)
        return getattr(mod, "__version__", "unknown")
    except Exception:
        return None


def run_check():
    """执行一次真实校验，返回结果 dict。"""
    token = os.getenv(TOKEN_ENV)
    sdk_available = importlib.util.find_spec(SDK_NAME) is not None
    sdk_version = _sdk_version() if sdk_available else None

    return {
        "token_set": bool(token),
        "token_hash": _token_hash(token) if token else None,
        "sdk_available": sdk_available,
        "sdk_version": sdk_version,
        "mode": "sdk" if sdk_available else "http",
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

    # 优先读缓存：token 未变则复用，token 变化则重新校验
    if not args.force and not args.check:
        cached = load_cache()
        if cached:
            current_token = os.getenv(TOKEN_ENV)
            if current_token and cached.get("token_hash") == _token_hash(current_token):
                print("使用缓存校验结果：")
                print(json.dumps(cached, ensure_ascii=False, indent=2))
                return 0 if cached["token_set"] else 1

    result = run_check()
    if not args.check:
        save_cache(result)
        print("已重新校验并写入缓存：")
    else:
        print("校验结果（未写缓存）：")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 退出码：token 缺失 -> 1（必选失败），SDK 缺失但 token 有 -> 0（可选警告）
    if not result["token_set"]:
        print("\n错误：未设置环境变量 SXSC_TUSHARE_TOKEN，无法执行 skill。")
        print("请参考 README 配置后重试。")
        return 1
    if not result["sdk_available"]:
        print("\n提示：未安装 sxsc_tushare 库，将使用 HTTP 协议方式调取数据。")
    return 0


if __name__ == "__main__":
    sys.exit(main())