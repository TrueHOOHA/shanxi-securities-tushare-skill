#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sxsc-tushare-analysis 自带环境校验（独立运行，不依赖任何同级 skill 目录）。

token 来源：**只读 token 文件**，不再使用 `SXSC_TUSHARE_TOKEN` 环境变量。
文件即 `sxsc_tushare.set_token()` 写入的位置（默认 ~/sxsc_tk.csv，文件名随包构建而变，
统一读 `upass.TOKEN_F_P` 得到），内容为 65 字节左右的 csv：首行 `token`，次行 token 值。

校验项：
1. token 文件是否可读且能解析出非空 token（必选，缺失则无法取数）
2. sxsc_tushare 库是否已安装（必需；未安装时自动 pip install sxsc_tushare）
3. SDK 能否完成 get_api(token) 初始化（与 data_api.DataAPI.__init__ 同一路径）

用法：
    python check_env.py             # 读取缓存或执行校验（token 文件始终实时检查）
    python check_env.py --force     # 强制重新校验并覆盖缓存
    python check_env.py --check     # 只校验不写缓存
    python check_env.py --no-install  # 未安装时不自动 pip install，仅报告
    python check_env.py --path      # 只打印缓存文件路径

退出码：0 可用；1 token 文件缺失/无效、SDK 不可用或初始化失败。

写盘说明：本脚本**不会**写 token 文件（不再调 set_token），只在未加 --check 时把校验
结果写入同目录 env_check.json；该 JSON 不含 token 明文与哈希。
"""

import argparse
import importlib
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

CACHE_FILE = Path(__file__).resolve().parent / "env_check.json"
SDK_NAME = "sxsc_tushare"


# ---------- token 解析（与 data_api 共用 token_util 实现，避免两份逻辑漂移） ----------

from token_util import load_token, token_file_path  # noqa: E402


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
    """初始化 SDK 并做一次轻量鉴权探针；返回 None 表示可用，否则返回失败原因。

    token 直接传给 get_api，**不调 set_token**，因此不会重写 token 文件。
    ⚠️ get_api 只构造客户端、不校验 token；必须再调一次真实接口，否则过期/占位
    token 能通过校验，随后所有取数被静默吞掉，产出全 N/A 的报告。
    """
    if not token:
        return "token 未找到"
    try:
        import sxsc_tushare as sx
        api = sx.get_api(token, env="prd")
        _end = datetime.now().strftime("%Y%m%d")
        _start = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
        probe = api.trade_cal(exchange="SSE", start_date=_start, end_date=_end)
        if probe is None:
            return "鉴权探针 trade_cal 返回空（token 可能无效或无权限）"
        return None
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def run_check(install=True):
    """执行一次真实校验，返回结果 dict（不含 token 明文）。"""
    sdk_available = importlib.util.find_spec(SDK_NAME) is not None
    install_note = None
    if not sdk_available and install:
        sdk_available, install_note = _ensure_installed()

    token_file = token_file_path()
    # token 文件解析不依赖 SDK：即使 SDK 缺失也要如实区分"没装库"与"没 token"
    token = load_token()
    # SDK 缺失时仍报告文件是否存在，便于区分"没装库"和"没 token"
    file_exists = os.path.isfile(token_file)
    sdk_version = _sdk_version() if sdk_available else None
    sdk_init_err = _sdk_init_error(token) if sdk_available else None

    return {
        "token_ok": bool(token),
        "token_file": token_file,
        "token_file_exists": file_exists,
        "token_len": len(token) if token else 0,
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
        # 旧版缓存用 token_set（环境变量口径），不兼容，强制刷新
        if isinstance(data, dict) and "token_ok" in data:
            return data
    except Exception:
        return None
    return None


def save_cache(result):
    """写缓存；返回是否成功（main 据此如实提示，不能写失败还报"已写入"）。"""
    try:
        CACHE_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as exc:
        # 只读目录下缓存写失败不影响校验结论
        print(f"[check_env] 缓存写入失败（不影响本次校验）：{type(exc).__name__}: {exc}", file=sys.stderr)
        return False


def _report_missing_token(result):
    print("\n错误：未从 token 文件读到有效 token。")
    print(f"  期望路径：{result['token_file']}")
    print(f"  文件存在：{'是' if result['token_file_exists'] else '否'}")
    print("  修复方式（二选一）：")
    print("    python -c \"import sxsc_tushare as sx; sx.set_token('你的token')\"   # 推荐，SDK 自带")
    print("    或手工创建上述 csv：首行 token，次行 token 值（UTF-8，无 BOM）")


def main():
    parser = argparse.ArgumentParser(description="校验 sxsc-tushare-analysis 运行环境")
    parser.add_argument("--force", action="store_true", help="强制重新校验并覆盖缓存")
    parser.add_argument("--check", action="store_true", help="只校验不写缓存")
    parser.add_argument("--no-install", action="store_true", help="未安装时不自动 pip install，仅报告")
    parser.add_argument("--path", action="store_true", help="仅打印缓存文件路径")
    args = parser.parse_args()

    if args.path:
        print(CACHE_FILE)
        return 0

    # 缓存命中时复用 SDK 信息，但 token 文件始终实时读
    # （用户可能已删除/替换 token 文件，不能仅凭缓存判定可用）
    if not args.force and not args.check:
        cached = load_cache()
        token_now = bool(load_token())
        # 缓存也要复核 SDK 是否仍可导入（卸载/换 venv 后不能继续报绿）
        sdk_now = importlib.util.find_spec(SDK_NAME) is not None
        if cached and cached.get("token_ok") and token_now and sdk_now:
            print("使用缓存校验结果（token 文件已实时确认）：")
            print(json.dumps(cached, ensure_ascii=False, indent=2))
            if not cached.get("sdk_available"):
                print("\n错误：未安装 sxsc_tushare 库。请运行：pip install sxsc_tushare")
                return 1
            if not cached.get("sdk_init_ok"):
                print(f"\n错误：SDK 已安装但初始化失败（{cached.get('sdk_init_error') or '未知原因'}），无法取数。")
                return 1
            return 0

    # 缓存未命中或 token 缺失：实时检测
    result = run_check(install=not args.no_install)
    if not args.check:
        if save_cache(result):
            print("已重新校验并写入缓存：")
        else:
            print("校验结果（缓存写入失败，不影响本次结论）：")
    else:
        print("校验结果（未写缓存）：")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not result["token_ok"]:
        _report_missing_token(result)
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
