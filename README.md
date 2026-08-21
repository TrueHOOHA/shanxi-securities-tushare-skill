# 山西证券 Tushare Skill 套件

本项目包含两个配套的金融分析 Agent Skill，基于山西证券 `sxsc_tushare` 包：

| Skill | 职责 | 适用场景 |
|-------|------|---------|
| `shanxi-securities-tushare` | **数据获取**：调用 100+ 金融数据接口，批量取数并结构化交付 | 取数、导出、回测数据准备 |
| `sxsc-tushare-analysis` | **综合分析**：基于数据 skill 的接口，对标的进行交叉分析并输出报告 | 分析、研究、评估、对比 |

---

## 一、如果你是人类

直接把下面这句话发给 AI 助手：

```
帮我安装这个 skill ：
https://gitee.com/TrueHOOHA/shanxi-securities-tushare-skill/
```

> 剩下的安装、配置工作交给 AI 完成即可，你无需手动操作。

---

## 二、如果你是 Agent

### 安装

1. 将本项目下的 `shanxi-securities-tushare/`（数据 skill）与 `sxsc-tushare-analysis/`（分析 skill）两个文件夹复制到所使用的 Agent 工具的 `skills/` 目录下。

   最终路径结构示例：

   ```
   <工具目录>/skills/
   ├── shanxi-securities-tushare/
   │   ├── SKILL.md
   │   ├── scripts/
   │   └── references/
   └── sxsc-tushare-analysis/
       ├── SKILL.md
       └── scripts/
   ```

   > 两个 skill 需放在同一 `skills/` 目录下。分析 skill 依赖数据 skill 的接口表、demo 模板与 `check_env.py`。

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

### 环境校验

配置完成后，可运行校验脚本确认环境就绪，结果缓存到 `shanxi-securities-tushare/scripts/env_check.json`（token 未变则复用，避免每次重复校验）：

```bash
python shanxi-securities-tushare/scripts/check_env.py          # 用缓存，否则重新校验
python shanxi-securities-tushare/scripts/check_env.py --force  # 强制重新校验
python shanxi-securities-tushare/scripts/check_env.py --check  # 只校验不写缓存
```

校验项：

| 项 | 必需 | 缺失后果 |
|---|---|---|
| `SXSC_TUSHARE_TOKEN` 已设置 | 是 | 无法执行 skill，退出码非 0 |
| `sxsc_tushare` 库已安装 | 否 | 有 → SDK 方式；无 → HTTP 协议方式 |

---

## 三、Skill 1：数据获取（shanxi-securities-tushare）

### 触发条件

只要用户提到以下内容，**就应触发本 skill**（即使用户未明确提及"山西证券"或"tushare"）：

- 股票/基金/指数/期货/港股行情、涨跌、走势、K线、日线/周线/月线的**获取/导出**
- 财务报表、营收/利润/ROE/毛利率/现金流的**批量拉取与导出**
- 资金流向、北向资金、主力资金、龙虎榜、涨停板数据的**取数**
- 行业分类、申万行业、指数成分股的**查询**
- CPI/PPI/PMI/社融/利率/宏观数据、港股/美股/外汇的**获取**
- 可转债行情/发行、停复牌、限售解禁、分红送股、股东增减持的**数据查询**
- 导出数据、生成 CSV/parquet、回测数据准备
- 用户说"拉份数据""导出CSV""批量取数""查一下行情""看看资金流向"等

> **边界**：本 skill 只负责取数与数据交付，不负责标的分析。若用户要求对标的进行分析/评估/对比/深度研究，应使用 `sxsc-tushare-analysis` skill。

> 补充说明：公告/新闻/研报/政策催化目前不属于本 skill 的直连数据能力；如用户提出该类需求，应告知限制，并建议改查价格异动、龙虎榜、资金流等可量化替代证据。

### 数据覆盖（100 个接口）

| 类别 | 接口数量 |
|------|---------|
| A 股（含财务报表） | 45 |
| 港股 | 6 |
| 基金 | 9 |
| 指数 | 9 |
| 融资融券 / 转融通 | 7 |
| 宏观 | 10 |
| 期货 / 期权 | 9 |
| 其他（可转债 / 外汇） | 5 |
| **总计** | **100** |

> 按接口文档映射计；`trade_cal` 同一接口分别对应股票/期货交易日历两份文档。完整列表见 `references/API接口对应表.md`。

### 核心原则

- 调用任何接口前，必须先查 `shanxi-securities-tushare/references/API接口对应表.md` 获取 `pro.api_name` 与对应文档路径，**禁止凭记忆写接口**。
- 取数前先运行 `check_env.py` 确认环境就绪，token 缺失时任何取数都无意义。
- 日期格式统一 `YYYYMMDD`，未来日期自动裁剪到最近可用日期。

### 工作流

每次执行按此顺序：**环境校验 → 理解任务 → 解析标的 → 选接口 → 校验参数 → 取数 → 整理 → 解释 → 交付**。

### 参考模板

`scripts/` 下的 `*_demo.py` 是 **Agent 调取数据时的参考模板**（非可执行脚本），每个接口同时提供 **SDK 与 HTTP 两种调用方式**，由 Agent 按环境校验结果（`mode` 字段）选择其一复制调用。

| 文件 | 覆盖接口 |
|---|---|
| `stock_data_demo.py` | `stock_basic`、`daily`、`fina_indicator` |
| `fund_data_demo.py` | `fund_basic`、`fund_nav`、`fund_manager` |
| `index_sector_demo.py` | `index_daily`、`index_classify`、`index_member`、`index_global` |
| `fin_report_demo.py` | `income`、`balancesheet`、`cashflow` |
| `moneyflow_demo.py` | `moneyflow`、`moneyflow_hsgt`、`top_list` |

---

## 四、Skill 2：综合分析（sxsc-tushare-analysis）

### 触发条件

只要用户对标的提出分析类请求，**就应触发本 skill**：

- 用户说"分析一下XX""帮我看看XX""深度研究XX""XX怎么样""XX基本面""XX技术面""XX估值""XX财务"
- 用户说"评估XX""对比XX""研究XX""XX的业绩表现""XX的风险"
- 用户要求对股票/指数/基金/期货进行综合分析或出具研究简报

> 本 skill 自动调用 `sxsc_tushare` 取数并输出完整报告，接管数据获取与分析全流程。当本 skill 被触发时，数据获取类 skill 不应同时激活——分析已包含数据。

### 分析维度

| 标的类型 | 维度数 | 覆盖内容 |
|---------|--------|---------|
| 沪深股票 | 11 维 | 概况、行情趋势(含基准对比)、估值、财务质量(含业绩预告)、资金面(含大宗交易)、股东/筹码(含增减持)、两融/杠杆情绪、市场异动、解禁压力、风险提示、宏观/市场环境 |
| 指数 | 7 维 | 概况、行情趋势、估值、成分权重、行业分布、两融/市场杠杆、对比(含国际指数) |
| 公募基金 | 8 维 | 概况、净值走势、业绩指标(夏普/回撤)、基金经理、持仓、规模变化、分红、同类对比 |
| 期货 | 6 维 | 概况、行情趋势、持仓分析、主力合约、仓单库存、结算参数 |

### 报告结构

每份报告输出为结构化 markdown，按此模板：

```
# 标的名称 综合分析报告

> 数据日期：YYYY-MM-DD（Tushare 数据为 T-1 日）
> 免责：本报告基于历史数据，不构成投资建议

## 1. 概况
## 2. 行情趋势
## 3. 估值分析
...（逐维度）
## 风险提示（综合信号汇总）
```

每维度输出：**结论句 → 关键数据表 → 维度解读**。结论先行，不替用户决策。

### 工作流

每次执行按此顺序：**环境校验(引用数据 skill 的 check_env.py) → 标的识别(类型+ts_code) → 维度加载(按类型全套) → 逐维取数分析 → 综合报告**。

取数前必须查数据 skill 的 `API接口对应表.md`，引用其 demo 模板，禁止凭记忆写接口。

### 量化分析方法

**纵向对比（单标的）**：
- 股票用 `adj_factor` 复权因子消除除权除息跳变，区间收益率/MA/回撤均基于复权价
- 基金用 `fund_adj` 复权因子构建复权净值序列，夏普/最大回撤基于复权净值
- 期货用 `fut_mapping` 识别主力合约区间，跨合约不简单拼接

**横向对比（多标的）**：
- 序列归一化（rebase 到基准日 = 100），消除价格量纲差异
- 收益率横排对比表（近 1/3/6/12 月/YTD）
- 估值不比绝对值，比各自近 5 年历史分位数
- 财务比率类指标（ROE/毛利率）直接横排，绝对值指标转增速后对比

### 分析原则

1. **结论先行**：每维度先写结论句，再给数据
2. **交叉验证**：单一维度信号不充分时，多维度交叉判断
3. **数据时效**：Tushare 数据为 T-1 日，报告顶部注明
4. **风险前置**：风险提示在报告末尾单独一节
5. **不替用户决策**：用描述性语言，不用"建议买入/卖出"
6. **空结果处理**：区分非交易日/标未上市/参数错误/权限不足

### 边缘情况处理

- **ST/*ST 股票**：2026-07-06 起涨跌停调整为 10%，此前为 5%，分析时注意日期界限
- **次新股/上市不足 1 年**：历史数据不足 250 日时 MA250/年化波动率不可用，降级为可用区间并标注
- **停复牌**：停牌期间价格序列断裂，用 `suspend_d` 查记录，计算时跳过停牌日
- **数据质量异常**：如 `fund_portfolio` 返回 ratio 全 0，标注"数据异常"而非当正常持仓分析
- **标的名称多义性**：返回多个匹配时按市值排序取最大，并在报告中注明

### 计算参考模板

`scripts/` 下按分析方法分模块，Agent 按需引用对应模块的函数：

| 模块 | 覆盖函数 |
|------|---------|
| `data_api.py` | `DataAPI`/`shift_date`/`ensure_sorted`/`drop_t0_placeholder`/`safe_call`（统一数据访问层：SDK/HTTP 双模式取数，内置 T-0 占位行过滤，覆盖股票/指数/基金/期货/财务/资金流等接口） |
| `basic_metrics.py` | `calc_returns`/`calc_cagr`/`calc_ma`/`calc_volatility`/`calc_max_drawdown`/`calc_sharpe`/`calc_sortino`/`calc_information_ratio`/`calc_roe_trend`/`calc_revenue_growth`/`flag_risks` |
| `adjustment.py` | `apply_adj_factor`/`apply_fund_adj`/`apply_etf_adj`/`rebase_series`/`compare_returns`/`calc_percentile_rank`/`calc_zscore` |
| `technical_indicators.py` | `calc_macd`/`calc_rsi`/`calc_kdj`/`calc_boll`/`calc_obv`/`calc_volume_ratio` |
| `risk_modeling.py` | `calc_var_cvar`/`calc_tail_risk`/`calc_drawdown_detail`/`calc_amihud_illiquidity`/`calc_rolling_beta`/`calc_rolling_sharpe`/`calc_relative_strength` |
| `attribution.py` | `calc_beta_alpha`/`calc_piotroski_fscore`/`calc_event_study` |
| `composite.py` | `calc_technical_confluence`/`calc_price_volume_pattern`/`calc_composite_score`/`calc_factor_positioning`/`calc_earnings_inflection`/`calc_pair_relative_value`/`calc_risk_budget`/`calc_chip_price_cross` |

---

## 文件结构

```
.
├── README.md                                  # 根文档（总入口）
├── LICENSE
├── shanxi-securities-tushare/                 # 数据 skill（只负责取数）
│   ├── SKILL.md                               # 执行规范入口
│   ├── scripts/
│   │   ├── check_env.py                       # 环境校验脚本
│   │   ├── stock_data_demo.py                 # 股票数据示例脚本
│   │   ├── fund_data_demo.py                  # 基金数据示例脚本
│   │   ├── index_sector_demo.py               # 指数/行业示例脚本
│   │   ├── fin_report_demo.py                 # 财务三表示例脚本
│   │   └── moneyflow_demo.py                  # 资金流向/龙虎榜示例脚本
│   └── references/
│       ├── API接口对应表.md                   # 100 个 API ↔ 文档映射（必查）
│       ├── 调取数据.md                        # 环境配置与调用说明
│       ├── 基础信息.md                        # stock_basic 接口文档
│       ├── A股日线行情.md                     # daily 接口文档
│       └── ...（其余接口文档见 references/，每个接口一文档）
└── sxsc-tushare-analysis/                     # 分析 skill（负责综合分析）
    ├── SKILL.md                               # 综合分析执行规范入口
    └── scripts/
        ├── data_api.py                        # 统一数据访问层（DataAPI：SDK/HTTP 双模式取数）
        ├── result_model.py                    # 分析结果数据模型
        ├── analysis_runner.py                 # 股票综合分析 Runner（stock_report）
        ├── index_analysis_runner.py           # 指数综合分析 Runner（index_report）
        ├── fund_analysis_runner.py            # 基金综合分析 Runner（fund_report）
        ├── fut_analysis_runner.py             # 期货综合分析 Runner（fut_report）
        ├── stock_analyzer.py                  # 旧入口兼容包装
        ├── basic_metrics.py                   # 基础指标（收益率/MA/波动率/夏普等）
        ├── adjustment.py                      # 复权与归一化（adj_factor/rebase/Z-Score）
        ├── technical_indicators.py            # 技术指标（MACD/RSI/KDJ/布林带/OBV）
        ├── risk_modeling.py                   # 风险建模（VaR/尾部风险/滚动分析/RS）
        ├── attribution.py                     # 归因分析（Beta-Alpha/Piotroski/事件研究）
        └── composite.py                       # 跨维度组合分析（技术共振/量价/多因子评分等）
```

## 注意事项

1. **Token 安全**：不要在代码中硬编码 token，使用环境变量 `SXSC_TUSHARE_TOKEN`
2. **查表优先**：即使记得接口名称，也要先查 `API接口对应表.md`
3. **工具无关约束**：核心是先查 `API接口对应表.md`，检索工具可替换。
4. **环境选择**：纯 Python 和仿真端用 `env='prd'`，生产端用 `env='qa'`
5. **日期格式**：统一使用 `YYYYMMDD` 格式（如 `20240101`）
6. **错误处理**：token 无效、无权限、空结果等均有对应处理策略（见各 SKILL.md）
7. **分析 skill 不替代数据 skill**：纯取数/导出类请求应由数据 skill 处理，分析类请求由分析 skill 处理。两者 description 已做边界划分，各自触发互不干扰。