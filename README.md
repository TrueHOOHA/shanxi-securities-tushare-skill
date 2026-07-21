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

## 一、如果你是人类

直接把下面这句话发给 AI 助手：

```
帮我安装这个 skill ：
https://gitee.com/TrueHOOHA/shanxi-securities-tushare-skill/
```

> 剩下的安装、配置工作交给 AI 完成即可，你无需手动操作。

---

## 二、如果你是 Agent

1. 将本项目下的 `/shanxi-securities-tushare/` 文件夹复制到所使用的 Agent 工具的 `skills/` 目录下。

   最终路径结构示例：

   ```
   <工具目录>/skills/
   └── shanxi-securities-tushare/
       ├── SKILL.md
       ├── scripts/
       └── references/
   ```

2. 引导用户配置环境变量 `SXSC_TUSHARE_TOKEN`（详见下方「环境变量配置」）。

### 环境变量配置

向用户询问 Token 后，根据其操作系统配置：

**Windows PowerShell（永久生效，仅当前用户）：**

```powershell
[Environment]::SetEnvironmentVariable("SXSC_TUSHARE_TOKEN", "你的token", "User")
```

**Linux / macOS（永久生效，写入 shell 配置文件）：**

```bash
# bash 用户
echo 'export SXSC_TUSHARE_TOKEN="你的token"' >> ~/.bashrc && source ~/.bashrc

# zsh 用户
echo 'export SXSC_TUSHARE_TOKEN="你的token"' >> ~/.zshrc && source ~/.zshrc
```

**验证：**

```bash
# Linux / macOS
echo $SXSC_TUSHARE_TOKEN

# Windows PowerShell
echo $env:SXSC_TUSHARE_TOKEN
```

---

## 触发条件

只要用户提到以下内容，**就应触发本 skill**（即使用户未明确提及“山西证券”或“tushare”）：

- 股票/基金/指数/期货/港股行情、涨跌、走势、K线、日线/周线/月线
- 财务报表、营收/利润/ROE/毛利率/现金流、估值/PE/PB/股息率
- 资金流向、北向资金、主力资金、龙虎榜、涨停板、市场情绪
- 板块轮动、行业分析、概念题材、申万行业、成分股
- CPI/PPI/PMI/社融/利率/宏观数据、港股/美股/外汇
- 导出数据、生成 CSV/parquet、回测数据准备
- 用户说“查一下行情”“看看资金流向”“查基金”“帮我研究一下”“对比几家公司”“最近哪个板块强”等口语化表达

> 补充说明：公告/新闻/研报/政策催化目前不属于本 skill 的直连数据能力；如用户提出该类需求，应告知限制，并建议改查价格异动、龙虎榜、资金流等可量化替代证据。

## 文件结构

```
.
├── README.md                                  # 根文档（总入口）
├── LICENSE
└── shanxi-securities-tushare/
    ├── SKILL.md                               # 执行规范入口
    ├── scripts/
    │   ├── stock_data_demo.py                 # 股票数据示例脚本
    │   └── fund_data_demo.py                  # 基金数据示例脚本
    └── references/
        ├── API接口对应表.md                   # 101 个 API ↔ 文档映射（必查）
        ├── 调取数据.md                        # 环境配置与调用说明
        ├── intent_taxonomy.md                 # 意图分类详解（10 大场景）
        ├── workflow_templates.md              # 工作流模板（5 种路径）
        ├── examples.md                        # 完整代码示例集合
        ├── 基础信息.md                        # stock_basic 接口文档
        ├── A股日线行情.md                     # daily 接口文档
        ├── ...（其余接口与专题文档见 references/）
```

## 核心原则：查表优先

## 文档入口说明

- 根目录 `README.md`：项目总入口（安装、触发条件、整体结构说明）
- `shanxi-securities-tushare/SKILL.md`：执行规范入口（调用原则、流程与约束）

执行规范采用“简述 + 统一规范”方式：
- 调用前必须先查 `references/API接口对应表.md` 与对应接口文档。
- 参数、输出、错误处理的详细规则统一见 `shanxi-securities-tushare/references/execution_contract.md`。
- 本 README 不再重复展开长规则，避免多版本漂移。

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
4. **HTTP 端点**：示例脚本通过 `http://221.204.19.233:7172` 直连（Python / 仿真环境），零外部依赖（仅 Python 标准库）；生产环境端点需咨询山西证券
5. **日期格式**：统一使用 `YYYYMMDD` 格式（如 `20240101`）
6. **错误处理**：token 无效、无权限、空结果等均有对应处理策略（见 SKILL.md）
