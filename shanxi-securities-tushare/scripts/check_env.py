#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
校验脚本环境，并把结果缓存到 JSON，避免每次重复校验。

校验项：
1. SXSC_TUSHARE_TOKEN 是否已设置（必选，缺失则无法执行 skill）
2. sxsc_tushare 库是否已安装（必需；未安装时自动 pip install sxsc_tushare）

用法：
    python check_env.py             # 读取缓存或执行校验（token 始终实时检查）
    python check_env.py --force     # 强制重新校验并覆盖缓存
    python check_env.py --check     # 只校验不写缓存
    python check_env.py --no-install  # 未安装时不自动 pip install，仅报告
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime
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


def _ensure_installed():
    """未安装 sxsc_tushare 时尝试 pip install。返回 (是否可用, 说明)。"""
    if importlib.util.find_spec(SDK_NAME) is not None:
        return True, None
    print(f"未检测到 {SDK_NAME} 库，尝试安装：pip install {SDK_NAME} ...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", SDK_NAME])
    except Exception as exc:
        return False, f"pip install 失败：{type(exc).__name__}: {exc}"
    if importlib.util.find_spec(SDK_NAME) is not None:
        return True, "已自动安装 sxsc_tushare"
    return False, "pip install 未报错但仍无法导入 sxsc_tushare"


def _sdk_init_error(token):
    """尝试用与 DataAPI 相同的方式初始化 SDK，返回 None 表示可用，否则返回失败原因。

    ⚠️ 只判 import 成功不代表 SDK 可用：sxsc_tushare.set_token() 会把 token 缓存写
    到用户主目录（如 jsdata_tk.csv），在沙箱/只读主目录下会 PermissionError。
    现在已无 HTTP 回退通道，初始化失败必须显式暴露并终止取数。
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


def run_check(install=True):
    """执行一次真实校验，返回结果 dict。"""
    token = os.getenv(TOKEN_ENV)
    sdk_available = importlib.util.find_spec(SDK_NAME) is not None
    install_note = None
    if not sdk_available and install:
        sdk_available, install_note = _ensure_installed()
    sdk_version = _sdk_version() if sdk_available else None
    sdk_init_err = _sdk_init_error(token) if sdk_available else None

    return {
        "token_set": bool(token),
        "sdk_available": sdk_available,
        "sdk_version": sdk_version,
        "sdk_init_ok": (sdk_init_err is None) if sdk_available else False,
        "sdk_init_error": sdk_init_err,
        "install_note": install_note,
        "mode": "sdk",
        "checked_at": datetime.now().isoformat(timespec="seconds"),
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
    parser.add_argument("--no-install", action="store_true", help="未安装时不自动 pip install，仅报告")
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
            # 旧版缓存可能没有 sdk_init_ok 字段（早期按 import 判定，无 HTTP 回退前不可靠），
            # 必须刷新而不是复用
            if "sdk_init_ok" not in cached:
                cached = None
            else:
                print("使用缓存校验结果（token 已实时确认）：")
                print(json.dumps(cached, ensure_ascii=False, indent=2))
                if not cached.get("sdk_available"):
                    print("\n错误：未安装 sxsc_tushare 库。请运行：pip install sxsc_tushare")
                    return 1
                if not cached.get("sdk_init_ok"):
                    print(f"\n错误：SDK 已安装但初始化失败（{cached.get('sdk_init_error') or '未知原因'}），无法取数。")
                    return 1
                return 0

    # 缓存未命中或 token 未设置：实时检测
    result = run_check(install=not args.no_install)
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
        print("\n错误：sxsc_tushare 库不可用，无法取数。")
        if result.get("install_note"):
            print(f"  {result['install_note']}")
        print("  请手动执行：pip install sxsc_tushare")
        return 1
    if not result["sdk_init_ok"]:
        print(f"\n错误：SDK 已安装但初始化失败（{result.get('sdk_init_error') or '未知原因'}），无法取数。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
