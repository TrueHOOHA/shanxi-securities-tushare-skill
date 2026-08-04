---
name: shanxi-securities-tushare
description: >
  使用山西证券 Tushare (sxsc_tushare) 获取专业结构化金融市场历史数据。特点：数据为 T-1 日（无实时行情）、数据规范适合批量处理和导出。触发条件：用户提及 tushare、sxsc_tushare、山西证券tushare、历史行情批量拉取、财务报表批量导出、回测数据准备、数据导出 CSV/Parquet、宏观历史数据(CPI/PPI/PMI/社融/利率)、指数成分股、申万/中信行业分类、多标的横向对比、可转债数据、期货数据、债券数据、历史资金流向、历史K线、涨跌停统计、分红送股、限售解禁、基金净值历史、港股数据、国债收益率、Shibor利率、停复牌信息、沪深股通十大成交股时，或说"拉份数据""导出CSV""批量取数""对比几家公司"等。适用场景：历史数据批量获取、财务分析、回测数据表准备、研究简报生成。
---

# 山西证券 Tushare 数据技能

把自然语言财经数据请求转为可执行的 Tushare 工作流：**环境校验 → 理解任务 → 解析标的 → 选接口 → 校验参数 → 取数 → 整理 → 解释 → 交付**。

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

1. **环境校验** — 检查 `scripts/env_check.json` 缓存是否存在：
   - 有 → 直接读取，取 `mode` 字段（`sdk`/`http`）决定取数方式。
   - 无 → 先运行 `python scripts/check_env.py` 生成缓存，再读取。
   - 退出码非 0（token 缺失）→ 停下，先提示用户配置 `SXSC_TUSHARE_TOKEN`，**不得进入取数**。
2. **理解任务** — 识别用户要解决什么问题（见下方"任务分类"）。
3. **解析标的** — 名称/代码 → 标准 `ts_code`（如 `600519.SH`）；市场、时间窗、字段按默认值补全。
4. **查表选接口** — 先查 `references/API接口对应表.md` 拿到 `pro.api_name` 与对应文档路径，**禁止凭记忆写接口**。
5. **校验参数** — 日期 `YYYYMMDD`、起止顺序、冲突裁决、未来日期裁剪（规则见下方"关键默认值"第 71–73 行）。
6. **取数执行** — 按第 1 步的 `mode` 选择方式：
   - `sdk`：参考 `scripts/` 下模板（`stock_data_demo.py`、`fund_data_demo.py`、`index_sector_demo.py`、`fin_report_demo.py`、`moneyflow_demo.py`）的 SDK 函数，走 `references/调取数据.md` 的 Python SDK 节。
   - `http`：参考模板中的 HTTP 函数（`http_call`），走 `references/调取数据.md` 的 HTTP 协议节（无 SDK 依赖）。
7. **结构化交付** — 一句话结论 → 口径 → 关键指标/表 → 风险/限制 → 文件路径。

**关键原则**：先想"这是什么任务、走哪条工作流"，再想"用哪个接口"。**永远不要先翻接口名**。**取数前先确认环境就绪**（第 1 步），token 缺失时任何取数都无意义。

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

## 环境校验

执行任取数任务前，先检查环境是否可用（结果缓存到 `scripts/env_check.json`，避免每次重复校验）：

```bash
python scripts/check_env.py          # 用缓存（token 未变则复用），否则重新校验
python scripts/check_env.py --force  # 强制重新校验并覆盖缓存
python scripts/check_env.py --check  # 只校验不写缓存
```

校验项：

| 项 | 必需 | 缺失后果 |
|---|---|---|
| `SXSC_TUSHARE_TOKEN` 已设置 | 是 | 无法执行 skill，返回非零退出码，需先配置环境变量 |
| `sxsc_tushare` 库已安装 | 否 | 有 → 走 SDK 方式（`sxsc_tushare`）；无 → 走 HTTP 协议方式 |

- 退出码非 0 表示 token 缺失，**先解决环境再取数**。
- 缓存按 token 内容哈希失效：token 变化会触发重新校验，SDK 安装状态每次校验会刷新。
- 取数方式按校验结果 `mode` 字段选择：`sdk` 用 `references/调取数据.md` 的 Python SDK 节，`http` 用 HTTP 协议节。

## References 快速索引

| 用途 | 路径 |
|---|---|
| 接口名 ↔ 文档映射 | `references/API接口对应表.md` |
| 安装与 HTTP 调取 | `references/调取数据.md` |
| 股票调用模板 | `scripts/stock_data_demo.py` |
| 基金调用模板 | `scripts/fund_data_demo.py` |
| 指数/行业调用模板 | `scripts/index_sector_demo.py` |
| 财务三表调用模板 | `scripts/fin_report_demo.py` |
| 资金流/龙虎榜调用模板 | `scripts/moneyflow_demo.py` |

## Code Examples

### 初始化与日线
```python
import sxsc_tushare as sx
import os

token = os.getenv('SXSC_TUSHARE_TOKEN') or 'YOUR_TOKEN_HERE'
sx.set_token(token)
pro = sx.get_api(env='prd')  # 'prd' 仿真, 'qa' 生产

# 股票列表
pro.stock_basic(exchange='', list_status='L',
                fields='ts_code,symbol,name,area,industry,list_date')

# 日线行情（ah_vol/ah_amount 为盘后成交量/额，2026-07-06 起有数据，需显式用 fields 指定）
pro.daily(ts_code='600519.SH', start_date='20250101', end_date='20251231')
# 取盘后数据示例：
# pro.daily(trade_date='20260707', fields='ts_code,open,close,ah_vol,ah_amount')
```

### 错误处理
```python
try:
    df = pro.daily(ts_code='600519.SH', start_date='20250101', end_date='20251231')
except Exception as e:
    print(f"获取数据失败：{e}")
```

空结果不一定是失败：可能是非交易日、标未上市、参数错误、权限不足 —— 区分清楚再下结论。
