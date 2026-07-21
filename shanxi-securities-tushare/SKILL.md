---
name: shanxi-securities-tushare
description: >
  使用山西证券 Tushare (sxsc_tushare) 获取专业结构化金融市场历史数据。特点：数据为 T-1 日（无实时行情）、数据规范适合批量处理和导出。触发条件：用户提及 tushare、sxsc_tushare、山西证券tushare、历史行情批量拉取、财务报表批量导出、回测数据准备、数据导出 CSV/Parquet、宏观历史数据(CPI/PPI/PMI/社融/利率)、指数成分股、申万/中信行业分类、多标的横向对比、可转债数据、期货数据、债券数据、历史资金流向、历史K线、涨跌停统计、分红送股、限售解禁、基金净值历史、港股数据、国债收益率、Shibor利率、停复牌信息、沪深股通十大成交股时，或说"拉份数据""导出CSV""批量取数""对比几家公司"等。适用场景：历史数据批量获取、财务分析、回测数据表准备、研究简报生成。
---

# 山西证券 Tushare 数据技能

把自然语言财经数据请求转为可执行的 Tushare 工作流：**理解任务 → 选接口 → 校验参数 → 取数 → 整理 → 解释 → 交付**。

## 典型场景

- 看单只股票/指数/ETF 的走势、估值、活跃度
- 多标的横向对比（业绩、估值、涨幅、ROE 等）
- 财务质量快照（利润趋势、ROE、毛利率、现金流）
- 资金流追踪（北向、主力、龙虎榜、板块吸金）
- 板块/题材轮动、指数成分股
- 宏观数据（CPI / PPI / GDP / PMI / 利率 / 货币供应量）
- 数据导出（CSV / Parquet）供回测或后续分析
- 生成可复用研究简报

## 核心工作流

每次执行按这个顺序：

1. **理解任务** — 识别用户要解决什么问题（见下方"任务分类"）。
2. **解析标的** — 名称/代码 → 标准 `ts_code`（如 `600519.SH`）；市场、时间窗、字段按默认值补全。
3. **查表选接口** — 先查 `references/API接口对应表.md` 拿到 `api_name` 与对应文档路径，**禁止凭记忆写接口**。
4. **校验参数** — 日期 `YYYYMMDD`、起止顺序、冲突裁决、未来日期裁剪（细节见下方「关键默认值」）。
5. **取数执行** — 优先复用 `scripts/stock_data_demo.py`、`scripts/fund_data_demo.py`。
6. **结构化交付** — 一句话结论 → 口径 → 关键指标/表 → 风险/限制 → 文件路径。

**关键原则**：先想"这是什么任务、走哪条工作流"，再想"用哪个接口"。**永远不要先翻接口名**。

## 任务分类

| 任务类型 | 典型表达 |
|---|---|
| 行情/趋势 | 最近怎么样、涨了多少、放量没有 |
| 基本资料 | 什么公司、什么时候上市、行业 |
| 财务/公司质量 | 财报、利润趋势、ROE、现金流 |
| 估值/筛选 | 估值高不高、谁便宜、低估值高股息 |
| 资金流/市场行为 | 北向、主力、龙虎榜、板块吸金 |
| 板块/指数/主题 | 哪个板块最强、成分股、概念主题 |
| 涨跌停/情绪 | 涨停梯队、连板、活跃度 |
| 公告/新闻/研报 | 公告、催化、新闻面（**受限**：仅告知边界 + 替代路径） |
| 宏观/跨市场 | CPI、PMI、利率、港股、美股 |
| 数据导出/研究准备 | 拉 CSV、回测数据表、Parquet |
| 综合研究简报 | 快速研究 XX、全景判断 |

## 关键默认值

| 模糊中文 | 默认口径 |
|---|---|
| "最近"/"近期" | 近 20 个交易日 |
| "最近一段时间"/"最近三个月" | 近 60 个交易日 |
| "今年" | 当年 1 月 1 日至今 |
| "财报"/"业绩" | 最近 8 个季度 + 最近年度 |
| "资金流最近如何" | 近 5–20 个交易日 |
| "宏观最近如何" | 最近 6–12 期 |
| 行业分类 | 申万/中信（更稳定） |
| 概念分类 | 同花顺/东方财富（主题更热） |
| 证券代码 | `600519.SH` / `000001.SZ` 标准格式 |

裸代码（如 `000001`）能按规则补全就补全并说明，不能补全就最小澄清。  
日期冲突（`trade_date` 与 `start_date/end_date` 同给）先裁决再调用。  
未来日期自动裁剪到最近可用日期并提示用户。

## What this skill is NOT for

- 买卖建议、替代投资顾问
- 自动下单或交易执行
- 实时行情（仅 T-1 日数据）
- 回测引擎、组合优化系统的实现
- 无 token / 无权限时伪造数据

## References 快速索引

| 用途 | 路径 |
|---|---|
| 接口名 ↔ 文档映射 | `references/API接口对应表.md` |
| 安装与 HTTP 调取 | `references/调取数据.md` |
| 股票示例脚本 | `scripts/stock_data_demo.py` |
| 基金示例脚本 | `scripts/fund_data_demo.py` |

## Code Examples

### HTTP 直连（零外部依赖）
```python
import os, json, urllib.request

API_URL = 'http://221.204.19.233:7172'  # Python / 仿真环境端点
token = os.getenv('SXSC_TUSHARE_TOKEN') or 'YOUR_TOKEN_HERE'

def call_api(api_name, fields='', **params):
    """通用 HTTP 调取：POST JSON Body，返回 list[dict]。code 非 0 抛错。"""
    payload = {'api_name': api_name, 'token': token, 'params': params, 'fields': fields}
    req = urllib.request.Request(
        API_URL, data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.loads(r.read().decode('utf-8'))
    if res.get('code') != 0:
        raise RuntimeError(f"{api_name} 调用失败：code={res.get('code')} msg={res.get('msg')}")
    d = res.get('data') or {}
    return [dict(zip(d.get('fields', []), row)) for row in d.get('items', [])]

# 股票列表
call_api('stock_basic', fields='ts_code,symbol,name,area,industry,list_date',
         exchange='', list_status='L')

# 日线行情（ah_vol/ah_amount 为盘后成交量/额，2026-07-06 起有数据，需显式用 fields 指定）
call_api('daily', ts_code='600519.SH', start_date='20250101', end_date='20251231')
# 取盘后数据示例：
# call_api('daily', fields='ts_code,open,close,ah_vol,ah_amount', trade_date='20260707')
```

> 生产环境（PTrade 客户端）的 HTTP 端点需咨询山西证券；上述端点适用于 Python / 仿真环境。

### 错误处理
```python
try:
    rows = call_api('daily', ts_code='600519.SH', start_date='20250101', end_date='20251231')
except Exception as e:
    print(f"获取数据失败：{e}")
```

空结果不一定是失败：可能是非交易日、标未上市、参数错误、权限不足 —— 区分清楚再下结论。
