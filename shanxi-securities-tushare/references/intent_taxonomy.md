# Intent Taxonomy 详细参考

> 本文件是意图分类清单；执行规则采用“简述 + 统一规范”。
> 详细执行规范（查表优先、参数确认、输出结构、错误处理）统一见 `execution_contract.md`。

## 1. 行情 / 趋势

**典型问题：**
- 最近走势怎么样
- 今年涨了多少
- 最近波动大不大
- 最近有没有放量

**常用接口（查阅 `API接口对应表.md` 确认）：**
- `daily` → `references/A股日线行情.md`
- `adj_factor` + `daily` → 复权行情计算（使用 `references/复权因子.md`）
- `weekly` → `references/周线行情.md`
- `monthly` → `references/月线行情.md`

- `daily_basic` → `references/每日指标.md`

---

## 2. 基本资料 / 标的识别

**典型问题：**
- 这是什么公司 / 什么指数 / 什么基金
- 是创业板吗 / 是 ST 吗 / 什么时候上市

**常用接口：**
- `stock_basic` → `references/基础信息.md`
- `fund_basic` → `references/公募基金列表.md`
- `index_basic` → `references/指数基本信息.md`
- `stock_company` → `references/上市公司基本信息.md`

---

## 3. 财务 / 公司质量

**典型问题：**
- 最近几个季度利润趋势
- 最近几个季度营收和净利润趋势
- 财务质量怎么样
- ROE / 毛利率 / 现金流如何

**常用接口：**
- `income` → `references/利润表.md`（营收 / 净利润趋势优先）
- `fina_indicator` → `references/财务指标数据.md`（ROE / 毛利率 / 净利率等质量指标补充）
- `balancesheet` → `references/资产负债表.md`
- `cashflow` → `references/现金流量表.md`
- `forecast` → `references/业绩预告.md`
- `express` → `references/业绩快报.md`
- `disclosure_date` → `references/财报披露计划.md`

---

## 4. 估值 / 基本面指标

**典型问题：**
- 现在估值高不高
- 谁更便宜
- PE / PB / 股息率如何

**常用接口：**
- `daily_basic` → `references/每日指标.md`
- `fina_indicator` → `references/财务指标数据.md`

---

## 5. 资金流 / 市场行为

**典型问题：**
- 北向最近买什么
- 主力资金流向
- 龙虎榜情况

**常用接口：**
- `moneyflow` → `references/个股资金流向.md`
- `moneyflow_hsgt` → `references/沪深港通资金流向.md`
- `hsgt_top10` → `references/沪深股通十大成交股.md`
- `top_list` → `references/龙虎榜每日明细.md`
- `top_inst` → `references/龙虎榜机构明细.md`

---

## 6. 板块 / 指数 / 主题

**典型问题：**
- 最近哪个板块最强
- 行业轮动如何
- 某板块有哪些成分股

**常用接口：**
- `index_basic` → `references/指数基本信息.md`
- `index_daily` → `references/指数日线行情.md`
- `index_classify` → `references/申万行业分类.md`
- `index_member` → `references/申万行业成分构成.md`

说明：查询申万行业分类及其成分时，通常结合 `index_classify` 与 `index_member` 使用；目前暂无独立行业指数行情接口。


---

## 7. 打板 / 情绪 / 活跃度

**典型问题：**
- 今天涨停梯队
- 连板结构
- 炸板率 / 情绪强弱

**常用接口：**
- `limit_list_d` → `references/涨跌停列表（新）.md`



---

## 8. 公告 / 新闻 / 研报 / 政策

**典型问题：**
- 最近有什么公告或催化
- 最近有什么研究报告
- 最近政策面发生了什么

**常用接口：**
- 暂无对应接口（当前意图暂不支持；如后续新增，请同步更新 `API接口对应表.md`）

**推荐回复模板关键句：**
- 当前 skill 无公告/新闻/研报/政策的直连接口，暂时不能直接拉取该类文本事件数据。
- 可改用“价格异动 + 资金流 + 龙虎榜”作为替代证据链，先验证是否存在交易层面的催化迹象。
- 如果你同意，我可以立刻按近 20 个交易日给你整理：涨跌与成交量异常、主力/北向资金变化、龙虎榜上榜情况。

---

## 9. 宏观 / 跨市场

**典型问题：**
- CPI / PMI / 社融 / M2
- 利率与收益率曲线
- 港股 / 美股 / 美债数据

**常用接口：**
- `cn_cpi` → `references/居民消费价格指数.md`
- `cn_ppi` → `references/工业生产者出厂价格指数.md`
- `cn_gdp` → `references/GDP数据.md`
- `cn_m` → `references/货币供应量.md`
- `shibor` → `references/Shibor利率数据.md`
- `shibor_lpr` → `references/LPR贷款基础利率.md`
- `hk_daily` → `references/港股行情.md`
- `index_global` → `references/国际指数.md`

---

## 10. 导出 / 研究准备

**典型问题：**
- 导出某标的一段时间行情
- 生成回测用数据表
- 输出 CSV / parquet

**常用接口：**
- 取决于上游任务，核心是统一输出规则与命名规范
