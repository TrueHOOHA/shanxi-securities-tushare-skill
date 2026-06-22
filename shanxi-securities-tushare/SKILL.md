---
name: shanxi-securities-tushare
description: >
  使用山西证券 Tushare (sxsc_tushare) 获取中国 A 股、基金、港股、债券、期货、宏观等金融市场数据。
  触发条件：用户提及股票/基金/指数/期货/港股行情、K线、财务报表、资金流向、板块分析、
  宏观数据(CPI/PPI/PMI/社融/利率)、龙虎榜、北向资金、可转债、可选期货，或说
  "查一下行情""看看资金流向""对比几家公司""拉份数据""导出 CSV"等口语化表达。
  适用场景：实时行情查询、资金流向分析、基金估值查询、板块热点分析、量化数据获取、
  财务分析、投资研究简报、回测数据准备。
  数据时点为 T-1 日（无实时行情），不提供买卖建议或自动下单。
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
3. **查表选接口** — 先查 `references/API接口对应表.md` 拿到 `pro.api_name` 与对应文档路径。🔴 **STOP**：禁止凭记忆写接口，必须查表确认后再调用。
4. **校验参数** — 日期 `YYYYMMDD`、起止顺序、冲突裁决、未来日期裁剪（细节见 `references/execution_contract.md`）。
5. 🔴 **CHECKPOINT · 投资建议拦截** — 如果用户问"值不值得买/该不该卖"，**STOP 并明确拒绝**，只提供数据对比（见"反模式清单"#1）。
6. **取数执行** — 优先复用 `scripts/stock_data_demo.py`、`scripts/fund_data_demo.py`。
7. **结构化交付** — 一句话结论 → 口径 → 关键指标/表 → 风险/限制 → 文件路径。

**关键原则**：先想"这是什么任务、走哪条工作流"，再想"用哪个接口"。**永远不要先翻接口名**。

## 任务分类

| 任务类型 | 典型表达 | 详细映射 |
|---|---|---|
| 行情/趋势 | 最近怎么样、涨了多少、放量没有 | `references/intent_taxonomy.md` §1 + `workflow_templates.md` §1 |
| 基本资料 | 什么公司、什么时候上市、行业 | `intent_taxonomy.md` §2 |
| 财务/公司质量 | 财报、利润趋势、ROE、现金流 | `intent_taxonomy.md` §3 + `workflow_templates.md` §3 |
| 估值/筛选 | 估值高不高、谁便宜、低估值高股息 | `intent_taxonomy.md` §4 + `workflow_templates.md` §4 |
| 资金流/市场行为 | 北向、主力、龙虎榜、板块吸金 | `intent_taxonomy.md` §5 + `workflow_templates.md` §5 |
| 板块/指数/主题 | 哪个板块最强、成分股、概念主题 | `intent_taxonomy.md` §6 + `workflow_templates.md` §6 |
| 涨跌停/情绪 | 涨停梯队、连板、活跃度 | `intent_taxonomy.md` §7 |
| 公告/新闻/研报 | 公告、催化、新闻面 | **受限**：`intent_taxonomy.md` §8，仅告知边界 + 替代路径 |
| 宏观/跨市场 | CPI、PMI、利率、港股、美股 | `intent_taxonomy.md` §9 |
| 数据导出/研究准备 | 拉 CSV、回测数据表、Parquet | `intent_taxonomy.md` §10 + `workflow_templates.md` §8 |
| 综合研究简报 | 快速研究 XX、全景判断 | `workflow_templates.md` §9 |

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
🔴 **数据时点确认**：数据为 T-1 日，"今天"数据不可用时提示用户改查最近交易日。

## 反模式清单（不要做的事）

以下行为在数据获取中**严格禁止**，执行流程时必须逐条检查：

| # | 反模式 | 后果 | 正确替代 |
|---|--------|------|----------|
| 1 | 提供买卖建议或投资推荐 | 违反 skill 边界，误导用户决策 | 🔴 STOP，明确告知"不提供买卖建议"，只给数据对比 |
| 2 | 凭记忆直接写接口名（不查表） | 参数名/字段可能过时，返回错误数据 | 查 `references/API接口对应表.md` 确认后再调用 |
| 3 | 把空结果当失败，不区分原因 | 掩盖非交易日/未上市/权限不足等真实原因 | 先排除非交易日、标的未上市、参数错误、权限不足 |
| 4 | 混用 `trade_date` 与 `start_date/end_date` | API 参数冲突，返回不一致数据 | 先裁决日期参数冲突再调用 |
| 5 | 未提示 T-1 数据时点就回答"今天" | 用户误以为拿到实时数据 | 明确提示"今天数据 T-1 更新后可用" |
| 6 | 无 token / 无权限时伪造数据 | 输出完全不可信 | 报错并告知用户检查 token 和权限 |

### What this skill is NOT for

- 买卖建议、替代投资顾问
- 自动下单或交易执行
- 实时行情（仅 T-1 日数据）
- 回测引擎、组合优化系统的实现
- 无 token / 无权限时伪造数据

## References 快速索引

| 用途 | 路径 |
|---|---|
| 接口名 ↔ 文档映射 | `references/API接口对应表.md` |
| **执行规范（查表/参数/输出/错误）** | `references/execution_contract.md` |
| 任务详细分类与典型问题 | `references/intent_taxonomy.md` |
| 9 类工作流详细步骤 | `references/workflow_templates.md` |
| 典型场景执行要点 | `references/examples.md` |
| 安装与 HTTP 调取 | `references/调取数据.md` |
| 维护者更新检查清单 | `references/maintainer_notes.md` |
| 股票示例脚本 | `scripts/stock_data_demo.py` |
| 基金示例脚本 | `scripts/fund_data_demo.py` |

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

# 日线行情
pro.daily(ts_code='600519.SH', start_date='20250101', end_date='20251231')
```

### 错误处理（三段式 Fallback）

| 触发条件 | 一线修复 | 仍失败兜底 |
|---------|---------|-----------|
| 空结果返回 | 排除非交易日 / 标的未上市 / `fields` 拼写错误 | 检查 token 权限是否覆盖该接口（见 `references/调取数据.md`） |
| `429` 限流 | 节流重试，降低调用频率（间隔 ≥1s） | 改分段拉取（按年/季切片），记录失败段重跑 |
| 参数错误 / 字段不存在 | 查 `references/API接口对应表.md` + 对应接口文档确认 `fields` | 明确告知用户该字段不可用，不给替代值 |
| 网络超时 | 重试 1 次 | 改分段拉取，缩小日期范围到单年 |

> 详细错误处理规范见 `references/execution_contract.md` §4。空结果不一定是失败——先排除真实原因再下结论。
