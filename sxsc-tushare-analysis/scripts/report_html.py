#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTML 报告渲染器：把 runner 产出的 markdown 报告 + DimensionResult 图表数据渲染为独立 HTML。

图表规格（由各 runner 在 DimensionResult.data["chart"] 中填入）：
    {"title": str, "type": "line" | "bar" | "candlestick",
     "dates": [...],                                          # x 轴日期（str）
     "series": [{"name": str, "data": [...]}, ...],           # line / bar 用
     "ohlc": [[open, close, low, high], ...], "vol": [...]}   # candlestick 用

数组须为 JSON 安全类型（pandas Series 用 .tolist() 转换）。
"""
import html as _html
import json
import re
from datetime import datetime

ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"

_CSS = """
:root{--ink:#1a1a1a;--accent:#2c6fbb;--muted:#5f656d;--faint:#9aa0a8;--border:#e6e8eb}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,'Segoe UI','Microsoft YaHei','PingFang SC',system-ui,sans-serif;max-width:1000px;margin:0 auto;padding:44px 30px 80px;color:var(--ink);line-height:1.75;background:#fff;-webkit-font-smoothing:antialiased}
h1{font-size:24px;font-weight:700;letter-spacing:.005em;margin-bottom:6px}
blockquote{font-size:13px;color:var(--muted);margin:0 0 30px}
h2{font-size:17px;font-weight:600;display:flex;align-items:baseline;gap:10px;margin:38px 0 14px;padding-bottom:10px;border-bottom:1px solid var(--border)}
h2 .num{color:var(--faint);font-weight:600;font-size:12.5px}
h3{font-size:13.5px;font-weight:600;color:#333;margin:20px 0 8px}
p{font-size:13.5px;margin-bottom:10px}
p em{color:var(--muted);font-size:12px}
.table-wrap{overflow-x:auto;margin:14px 0 8px}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
thead th{padding:8px 12px;font-weight:600;color:#333;text-align:left;border-bottom:1.5px solid #333;white-space:nowrap;letter-spacing:.02em}
tbody td{padding:8px 12px;border-bottom:1px solid var(--border);vertical-align:top}
td.r{text-align:right;white-space:nowrap}
th.r{text-align:right}
tbody td:first-child{white-space:nowrap;font-weight:500;padding-right:22px}
tbody tr:hover{background:#f5f7fa}
tbody tr:last-child td{border-bottom:none}
code{font-family:ui-monospace,'SF Mono',Consolas,Menlo,monospace;font-size:12.5px;background:#f4f5f6;padding:1px 6px;border-radius:4px}
ul{margin:8px 0 14px;padding-left:20px}
ul li{font-size:13.5px;margin:3px 0}
hr{border:none;border-top:1px solid var(--border);margin:30px 0 10px}
.chart{width:100%;height:430px;margin:14px 0 4px}
.kpi-bar{display:flex;flex-wrap:wrap;gap:12px;margin:0 0 6px}
.kpi{background:#f7f8f9;border:1px solid var(--border);border-radius:12px;padding:10px 18px;min-width:96px}
.kpi-label{font-size:11.5px;color:var(--muted);margin-bottom:3px}
.kpi-value{font-size:19px;font-weight:700;font-variant-numeric:tabular-nums}
.footer{color:var(--faint);font-size:12px;margin-top:44px;padding-top:14px;border-top:1px solid var(--border);text-align:center}
"""

_INIT_JS = """
for (const [id, option] of Object.entries(CHARTS)) {
    const el = document.getElementById(id);
    if (!el) continue;
    const chart = echarts.init(el);
    chart.setOption(option);
    window.addEventListener("resize", () => chart.resize());
}
"""


def _inline(text):
    """行内 markdown → HTML（code / 加粗 / 斜体），先转义再套标签。"""
    s = _html.escape(text)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
    return s


def _is_block_start(s):
    """该行是否开启新块（段落应在此停止续行）。"""
    return (
        not s
        or s.startswith("#")
        or s.startswith("|")
        or s.startswith("- ")
        or s.startswith("> ")
        or re.fullmatch(r"-{3,}", s)
    )


_NUM_RE = re.compile(r"^[-+]?[0-9][0-9,]*(?:\.[0-9]+)?(?:%|亿|万|万亿|元)?$")


def _md_table(lines, i):
    """解析从 lines[i] 起的管道表格；数值列自动右对齐，返回 (html, 下一行下标)。"""
    raw = lambda s: [c.strip() for c in s.strip().strip("|").split("|")]
    header = raw(lines[i])
    j = i + 1
    if j < len(lines) and re.fullmatch(r"[\s|:\-]+", lines[j].strip()):
        j += 1  # 表头分隔行
    rows = []
    while j < len(lines) and lines[j].strip().startswith("|"):
        rows.append(raw(lines[j]))
        j += 1

    # 数值列判定：该列全部单元格匹配数值模式才算（含 N/A 视为非数值列）
    numeric = []
    for col in range(len(header)):
        vals = [r[col] for r in rows if col < len(r) and r[col]]
        numeric.append(bool(vals) and all(_NUM_RE.match(v) for v in vals))
    cls = lambda col: " class='r'" if col < len(numeric) and numeric[col] else ""

    head = "".join(f"<th{cls(col)}>{_inline(header[col])}</th>" for col in range(len(header)))
    body = ""
    for r in rows:
        cells_html = "".join(
            f"<td{cls(col)}>{_inline(c)}</td>" for col, c in enumerate(r))
        cells_html += "<td></td>" * (len(header) - len(r))  # 补足缺列
        body += f"<tr>{cells_html}</tr>"
    return (f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead>"
            f"<tbody>{body}</tbody></table></div>", j)


def df_to_md_table(df):
    """已格式化的 DataFrame → GFM 管道表格（替代 to_markdown，无 tabulate 依赖）。"""
    if df is None or df.empty:
        return ""
    header = [str(c) for c in df.columns]
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join(["---"] * len(header)) + "|",
    ]
    for row in df.astype(str).values.tolist():
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def md_to_html(md: str) -> str:
    """把 runner 生成的 markdown 子集转换为 HTML 正文。"""
    lines = md.splitlines()
    out = []
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith("# "):
            out.append(f"<h1>{_inline(s[2:])}</h1>")
        elif s.startswith("## "):
            out.append(f"<h2>{_inline(s[3:])}</h2>")
        elif s.startswith("### "):
            out.append(f"<h3>{_inline(s[4:])}</h3>")
        elif re.fullmatch(r"-{3,}", s):
            out.append("<hr>")
        elif s.startswith("|"):
            table_html, i = _md_table(lines, i)
            out.append(table_html)
            continue
        elif s.startswith("- "):
            items = []
            while i < len(lines) and lines[i].strip().startswith("- "):
                items.append(f"<li>{_inline(lines[i].strip()[2:])}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        elif s.startswith("> "):
            quotes = []
            while i < len(lines) and lines[i].strip().startswith("> "):
                quotes.append(_inline(lines[i].strip()[2:]))
                i += 1
            out.append("<blockquote>" + "<br>".join(quotes) + "</blockquote>")
            continue
        else:
            para = [s]
            i += 1
            while i < len(lines) and not _is_block_start(lines[i].strip()):
                para.append(lines[i].strip())
                i += 1
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            continue
        i += 1
    return "\n".join(out)


# ---------- ECharts option ----------


def _candlestick_option(c):
    # 红涨绿跌（收敛色），无上/右边框，统一深色 tooltip，细网格
    return {
        "title": {"text": c.get("title", ""), "left": 0,
                  "textStyle": {"color": "#1a1a1a", "fontSize": 13, "fontWeight": 600}},
        "animationDuration": 500,
        "tooltip": {"trigger": "axis",
                    "axisPointer": {"type": "cross", "crossStyle": {"color": "#bbb"}},
                    "backgroundColor": "rgba(26,26,26,.92)", "borderColor": "transparent",
                    "padding": [8, 12], "textStyle": {"color": "#fff", "fontSize": 12}},
        "axisPointer": {"link": [{"xAxisIndex": "all"}]},
        "grid": [
            {"left": 56, "right": 16, "top": 36, "height": "52%"},
            {"left": 56, "right": 16, "top": "70%", "height": "16%"},
        ],
        "xAxis": [
            {"type": "category", "data": c["dates"], "gridIndex": 0,
             "axisLabel": {"show": False}, "axisLine": {"show": False}, "axisTick": {"show": False}},
            {"type": "category", "data": c["dates"], "gridIndex": 1,
             "axisLabel": {"color": "#9aa0a8", "fontSize": 9, "fontWeight": 500},
             "axisLine": {"lineStyle": {"color": "#e6e8eb"}}, "axisTick": {"show": False}},
        ],
        "yAxis": [
            {"type": "value", "gridIndex": 0, "scale": True,
             "axisLabel": {"color": "#9aa0a8", "fontSize": 9.5, "fontWeight": 500},
             "axisLine": {"show": False}, "axisTick": {"show": False},
             "splitLine": {"lineStyle": {"color": "#eef0f2", "width": 1}}},
            {"type": "value", "gridIndex": 1,
             "axisLabel": {"show": False}, "axisLine": {"show": False}, "axisTick": {"show": False},
             "splitLine": {"show": False}},
        ],
        "dataZoom": [
            {"type": "inside", "xAxisIndex": [0, 1], "start": 40, "end": 100},
            {"type": "slider", "xAxisIndex": [0, 1], "top": "92%", "start": 40, "end": 100,
             "height": 12, "borderColor": "transparent", "backgroundColor": "#f0f1f3",
             "fillerColor": "#d5e2f2", "handleStyle": {"color": "#2c6fbb"},
             "textStyle": {"color": "#9aa0a8", "fontSize": 9}},
        ],
        "series": [
            {"type": "candlestick", "name": "日K", "data": c["ohlc"], "barWidth": "55%",
             "itemStyle": {"color": "#d0342c", "color0": "#16a34a",
                           "borderColor": "#d0342c", "borderColor0": "#16a34a",
                           "borderWidth": 1}},
            {"type": "bar", "name": "成交量", "xAxisIndex": 1, "yAxisIndex": 1,
             "data": c.get("vol", []),
             "itemStyle": {"color": "rgba(44,111,187,.35)"}},
        ],
    }


def _line_bar_option(c):
    # 受控语义色板：蓝=主指标，红=对比/流出，灰=辅助，绿=正向
    _SERIES = ["#2c6fbb", "#d0342c", "#8a8f98", "#16a34a"]
    t = c.get("type", "line")
    # 系列通过 "yAxisIndex": 1 挂右轴（如 PE/PB 量纲悬殊时）；未指定则默认左轴
    dual = any(s.get("yAxisIndex") == 1 for s in c.get("series", []))
    series = []
    for i, s in enumerate(c.get("series", [])):
        color = _SERIES[i % len(_SERIES)]
        base = dict(s, type=t, itemStyle={"color": color})
        if t == "line":
            base.update({"smooth": True, "symbol": "none",
                         "lineStyle": {"width": 1.6, "color": color}})
        else:
            base.update({"barMaxWidth": 16})
        series.append(base)
    axis_style = {"axisLabel": {"color": "#9aa0a8", "fontSize": 9.5, "fontWeight": 500},
                  "axisLine": {"show": False}, "axisTick": {"show": False}}
    y_axes = [dict(axis_style, type="value",
                   splitLine={"lineStyle": {"color": "#eef0f2", "width": 1}})]
    if dual:
        y_axes.append(dict(axis_style, type="value", splitLine={"show": False}))  # 右轴不重复画网格
    return {
        "title": {"text": c.get("title", ""), "left": 0,
                  "textStyle": {"color": "#1a1a1a", "fontSize": 13, "fontWeight": 600}},
        "animationDuration": 600,
        "animationEasing": "quarticOut",
        "tooltip": {"trigger": "axis", "backgroundColor": "rgba(26,26,26,.92)",
                    "borderColor": "transparent", "padding": [8, 12],
                    "textStyle": {"color": "#fff", "fontSize": 12}},
        "legend": {"top": 0, "right": 0, "itemWidth": 10, "itemHeight": 3,
                   "textStyle": {"color": "#5f656d", "fontSize": 11}},
        "grid": {"left": 50, "right": 30, "top": 38, "bottom": 48},
        "xAxis": {"type": "category", "data": c["dates"], "boundaryGap": t == "bar",
                  "axisLabel": {"color": "#9aa0a8", "fontSize": 9.5, "fontWeight": 500},
                  "axisLine": {"lineStyle": {"color": "#e6e8eb"}}, "axisTick": {"show": False}},
        "yAxis": y_axes,
        "dataZoom": [
            {"type": "inside", "start": 0, "end": 100},
            {"type": "slider", "bottom": 4, "start": 0, "end": 100,
             "height": 12, "borderColor": "transparent", "backgroundColor": "#f0f1f3",
             "fillerColor": "#d5e2f2", "handleStyle": {"color": "#2c6fbb"},
             "textStyle": {"color": "#9aa0a8", "fontSize": 9}},
        ],
        "series": series,
    }


def _chart_options(results):
    """按维度顺序收集各维度的图表 option，返回 [(维度标题, option), ...]。"""
    out = []
    for _key, res in results.items():
        if not (res.is_ok() and res.data):
            continue
        c = res.data.get("chart")
        if not c or "dates" not in c:
            continue
        if c.get("type") == "candlestick" and "ohlc" in c:
            out.append((res.title, _candlestick_option(c)))
        elif c.get("type") in ("line", "bar") and c.get("series"):
            out.append((res.title, _line_bar_option(c)))
    return out


def _page_title(md: str) -> str:
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return _html.escape(s[2:])
    return "分析报告"


def _kpi_items(results):
    """从分析结果提取核心指标，供报告顶部 KPI 速览条（四类标的通用）。"""
    items = []
    # 最新价/净值：优先 runner 显式字段（未复权），退化到图表序列
    for key in ("trend", "nav"):
        r = results.get(key)
        if not (r and r.is_ok() and r.data):
            continue
        d = r.data
        v = None
        for fld in ("latest_close_unadj", "latest_close", "latest_nav"):
            if d.get(fld) is not None:
                v = d[fld]
                break
        if v is None:
            c = d.get("chart") or {}
            if c.get("series"):
                dd = c["series"][0].get("data") or []
                if dd and dd[-1] is not None:
                    v = dd[-1]
            elif c.get("ohlc"):  # candlestick 规格
                o = c["ohlc"][-1]
                v = o[1] if isinstance(o, (list, tuple)) and len(o) > 1 else o
        if v is not None:
            items.append(("最新价", f"{float(v):,.2f}"))
        break
    # 区间涨幅 + 波动率
    for r in results.values():
        if not (r.is_ok() and r.data):
            continue
        d = r.data
        if isinstance(d.get("returns"), dict) and "近20日涨幅%" in d["returns"]:
            rets = d["returns"]
            items.append(("近20日", f"{rets.get('近20日涨幅%', 'N/A')}%"))
            items.append(("近250日", f"{rets.get('近250日涨幅%', 'N/A')}%"))
            vol = d.get("volatility")
            if vol is not None:
                items.append(("年化波动", f"{vol}%"))
            break
    # 估值分位
    val = results.get("valuation")
    if val and val.is_ok() and val.data:
        pe = val.data.get("pe_hist_percentile")
        if pe is not None:
            items.append(("PE 5年分位", f"{pe}%"))
    return items


def _kpi_html(items):
    if not items:
        return ""
    cards = "".join(
        f'<div class="kpi"><div class="kpi-label">{_html.escape(k)}</div><div class="kpi-value">{_html.escape(str(v))}</div></div>'
        for k, v in items
    )
    return f'<div class="kpi-bar">{cards}</div>'

def _split_sections(md: str):
    """按 h1/h2 切分 markdown：h1 行与其后的非章节行（如数据日期）归入 head，返回 (head, [(h2标题行, 正文行列表)])。"""
    head = []
    sections = []
    cur_heading = None
    cur = []
    for line in md.splitlines():
        if line.startswith("# "):
            head.append(line)
        elif line.startswith("## "):
            if cur_heading is not None:
                sections.append((cur_heading, cur))
                cur = []
            cur_heading = line
        else:
            if cur_heading is None:
                head.append(line)
            else:
                cur.append(line)
    if cur_heading is not None:
        sections.append((cur_heading, cur))
    return "\n".join(head), sections


def render_html_report(md_text: str, results) -> str:
    """markdown 报告 + 图表数据 → 完整 HTML 页面字符串。

    图表按维度标题分发到对应 `## N. 标题` 章节内（标题后）；匹配不上的回退到附录。
    """
    head_md, sections = _split_sections(md_text)

    # 图表按维度归类，all_opts 保留全局编号（供 id/JS 映射）
    by_title = {}
    all_opts = []
    for title, opt in _chart_options(results):
        all_opts.append(opt)
        by_title.setdefault(title, []).append(len(all_opts) - 1)

    def _chart_divs(section_title):
        key = re.sub(r"^\d+\.\s*", "", section_title).strip()
        return "".join(f'<div id="chart-{i}" class="chart"></div>' for i in by_title.pop(key, []))

    parts = [md_to_html(head_md)] if head_md else []
    kpi = _kpi_html(_kpi_items(results))
    if kpi:
        parts.append(kpi)
    for h2_md, body in sections:
        heading = h2_md[3:]  # 去掉 '## '
        m = re.match(r"^(\d+)\.\s*(.*)$", heading)
        if m:
            head_html = f'<span class="num">{m.group(1)}</span>{_inline(m.group(2))}'
        else:
            head_html = _inline(heading)
        parts.append(f"<h2>{head_html}</h2>")
        parts.append(_chart_divs(heading))
        if any(line.strip() for line in body):
            parts.append(md_to_html("\n".join(body)))

    # 未匹配到章节的图表回退到附录
    leftover = [i for idxs in by_title.values() for i in idxs]
    if leftover:
        parts.append("<h2>附录：可视化图表</h2>" + "".join(
            f'<div id="chart-{i}" class="chart"></div>' for i in leftover))

    chart_js = json.dumps(
        {f"chart-{i}": all_opts[i] for i in range(len(all_opts))}, ensure_ascii=False
    )
    return (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<title>{_page_title(md_text)}</title>"
        f"<script src='{ECHARTS_CDN}'></script>"
        f"<style>{_CSS}</style></head><body>"
        + "\n".join(parts)
        + f"<div class='footer'>报告生成于 {datetime.now():%Y-%m-%d %H:%M} · 数据来自山西证券 Tushare（T-1）</div>"
        + f"<script>const CHARTS = {chart_js};{_INIT_JS}</script>"
        + "</body></html>"
    )


if __name__ == "__main__":
    # 自检：md 转换 + 图表注入的最小可用样例
    md = (
        "# 测试标的 全景研究报告\n\n"
        "> 数据日期：20260904（Tushare 数据为 T-1 日）\n\n"
        "## 1. 行情趋势\n"
        "近20日涨幅 **5.2%**，年化波动 `12.3%`。\n\n"
        "| 指标 | 数值 |\n|---|---|\n| 近20日涨幅 | 5.2% |\n| 年化波动 | 12.3% |\n\n"
        "- 风险信号 1\n- 风险信号 2\n\n"
        "---\n"
        "*本报告由AI自动生成*"
    )
    from result_model import DimensionResult

    fake = DimensionResult.success(
        "行情趋势",
        data={
            "chart": {
                "title": "日线行情（样例）",
                "type": "candlestick",
                "dates": ["20260901", "20260902", "20260903"],
                "ohlc": [[10, 10.5, 9.8, 10.6], [10.5, 10.2, 10.0, 10.8], [10.2, 10.9, 10.1, 11.0]],
                "vol": [100, 120, 130],
            }
        },
    )
    page = render_html_report(md, {"trend": fake})
    assert "<h1>测试标的 全景研究报告</h1>" in page
    assert "<table>" in page and "5.2%" in page
    assert "<ul><li>风险信号 1</li>" in page
    assert "<blockquote>数据日期" in page
    assert "echarts@5/dist/echarts.min.js" in page
    assert '"type": "candlestick"' in page
    assert "<div class='table-wrap'>" in page
    assert "<td class='r'>5.2%</td>" in page  # 数值列右对齐
    assert '<span class="num">1</span>行情趋势' in page
    assert "报告生成于" in page
    assert page.index('<div id="chart-0"') > page.index('<span class="num">1</span>行情趋势')
    with open("_selfcheck_report.html", "w", encoding="utf-8") as f:
        f.write(page)
    print("self-check OK -> _selfcheck_report.html")
