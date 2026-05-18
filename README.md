# 山西证券 Tushare 数据Skill
> 使用山西证券 `sxsc_tushare` 包获取中国金融市场数据的 Agent Skill

## 概述

本 Skill 提供对 **101 个金融数据接口** 的结构化访问，覆盖：
- 📈 **A 股**：行情、基本面、资金流向、龙虎榜、股权质押等
- 🌏 **港股**：列表、行情、港股通持股
- 💰 **基金**：公募列表、净值、持仓、分红
- 📊 **指数**：基础信息、行情、申万行业分类
- 🔄 **融资融券 / 转融通**：交易汇总与明细
- 📅 **宏观**：CPI、PPI、GDP、货币供应量、利率（Shibor/Libor/Hibor/LPR）
- ⚙️ **期货 / 期权**：合约信息、日线行情、持仓排名

## 安装 山西证券 Tushare Skill

### 方法1. 通过 npx skills CLI工具安装（Recommended）

安装 nodejs（如果需要 skills 管理本地包-npx 命令），https://nodejs.cn/download/

在命令行中执行以下命令：

```bash
npx skills add https://github.com/TrueHOOHA/shanxi-securities-tushare-skill.git --skill shanxi-securities-tushare
```

通过上述命令，技能将被自动安装到本地的 skills 目录中，并且方便后续同步升级。

### 方法 2. 直接安装

将本项目git clone到本地，把 /shanxi-securities-tushare 目录复制到本地的 skills 目录：

```
~/.claude/skills/
```

## 环境准备

```bash
# Windows PowerShell (永久设置，仅对当前用户生效)
[Environment]::SetEnvironmentVariable("SXSC_TUSHARE_TOKEN", "你的token", "User")

# Linux / macOS (临时设置，仅当前终端窗口生效)
export SXSC_TUSHARE_TOKEN="你的token"
```

## 触发条件

只要用户提到以下内容，**就应触发本 skill**（即使用户未明确提及）：

| 触发关键词 | 说明 |
|-----------|------|
| `山西证券`、`sxsc_tushare` | 直接提及数据源 |
| `股票行情`、`A 股`、`港股` | 行情查询 |
| `基金净值`、`基金持仓` | 基金数据 |
| `资金流向`、`主力资金` | 资金流向分析 |
| `融资融券`、`转融通` | 信用交易数据 |
| `财务指标`、`资产负债表` | 财务数据 |
| `申万行业`、`行业分类` | 行业分类 |
| `Shibor`、`LPR`、`Libor` | 利率数据 |
| `期货行情`、`期权行情` | 衍生品数据 |

## 文件结构

```
shanxi-securities-tushare/
├── SKILL.md                          # 技能主文件（320 行，精简版）
├── README.md                         # 本文件
├── scripts/
│   ├── stock_data_demo.py            # 股票数据示例脚本
│   └── fund_data_demo.py             # 基金数据示例脚本
└── references/
    ├── API接口对应表.md              # 101 个 API ↔ 文档映射（必查）
    ├── 调取数据.md                   # 环境配置与调用说明
    ├── intent_taxonomy.md            # 意图分类详解（10 大场景）
    ├── workflow_templates.md         # 工作流模板（5 种路径）
    ├── examples.md                   # 完整代码示例集合
    ├── 基础信息.md                   # stock_basic 接口文档
    ├── A股日线行情.md                # daily 接口文档
    ├── ... (共 101 个 API 接口文档)
    └── (其他参考文档)
```

## 核心原则：先查表，再调用

**每次调用 API 前，必须先查阅 `references/API接口对应表.md`**，确认：
1. 接口名称（`pro.api_name`）
2. 对应的参考文档路径
3. 输入参数与输出字段

```python
# ❌ 错误：凭记忆调用
df = pro.daily(ts_code='000001.SZ')

# ✅ 正确：先查表，再调用
# 1. 查阅 references/API接口对应表.md → 确认 daily → A股日线行情.md
# 2. 查阅 references/A股日线行情.md → 确认参数
# 3. 调用
df = pro.daily(ts_code='000001.SZ', start_date='20240101', end_date='20240131')
```

## 示例脚本

### 股票数据查询

```bash
python scripts/stock_data_demo.py
```

该脚本演示：
- 初始化 `sxsc_tushare`
- 查询 A 股日线行情
- 查询股票基础信息
- 查询每日指标

### 基金数据查询

```bash
python scripts/fund_data_demo.py
```

该脚本演示：
- 查询公募基金列表
- 查询基金净值
- 查询基金持仓

## API 接口统计

| 类别 | 接口数量 |
|------|---------|
| A 股 | 30+ |
| 港股 | 6 |
| 基金 | 8 |
| 指数 | 9 |
| 融资融券 / 转融通 | 10 |
| 宏观 | 7 |
| 期货 / 期权 | 12 |
| 其他 | 19 |
| **总计** | **101** |

完整列表见 `references/API接口对应表.md`

## 注意事项

1. **Token 安全**：不要在代码中硬编码 token，使用环境变量 `SXSC_TUSHARE_TOKEN`
2. **查表优先**：即使记得接口名称，也要先查 `API接口对应表.md`
3. **工具无关约束**：核心是先查 `API接口对应表.md`，检索工具可替换。
4. **环境选择**：纯 Python 和仿真端用 `env='prd'`，生产端用 `env='qa'`
5. **日期格式**：统一使用 `YYYYMMDD` 格式（如 `20240101`）
6. **错误处理**：token 无效、无权限、空结果等均有对应处理策略（见 SKILL.md）
