#!/usr/bin/env python3
"""
Compare server API documentation with local reference files.

For each doc_id:
1. Fetch HTML from http://221.204.19.233:7173/document/2?doc_id=XX
2. Extract the main content area (skip navigation sidebar)
3. Convert HTML to markdown
4. Compare with reference .md file (skip YAML frontmatter)
5. Report: CHANGED, SAME, MISSING, or FETCH_ERROR
"""

import os
import sys
import io
import re
import difflib
import requests
import html2text
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Ensure stdout handles UTF-8 properly
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE_URL = "http://221.204.19.233:7173/document/2"
REF_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "shanxi-securities-tushare",
    "references"
)

# All doc_ids and their corresponding reference filenames
DOC_MAP = {
    # 基础数据
    25: "基础信息.md",
    26: "股票交易日历.md",
    100: "股票曾用名.md",
    112: "上市公司基本信息.md",
    193: "上市公司管理层.md",
    194: "管理层薪酬和持股.md",
    123: "IPO新股列表.md",
    # 行情数据
    27: "A股日线行情.md",
    144: "周线行情.md",
    145: "月线行情.md",
    28: "复权因子.md",
    214: "每日停复牌信息.md",
    32: "每日指标.md",
    170: "个股资金流向.md",
    183: "每日涨跌停价格.md",
    47: "沪深港通资金流向.md",
    48: "沪深股通十大成交股.md",
    49: "港股通十大成交股.md",
    196: "港股通每日成交统计.md",
    197: "港股通每月成交统计.md",
    # 财务数据
    33: "利润表.md",
    36: "资产负债表.md",
    44: "现金流量表.md",
    45: "业绩预告.md",
    46: "业绩快报.md",
    103: "分红送股.md",
    79: "财务指标数据.md",
    80: "财务审计意见.md",
    81: "主营业务构成.md",
    162: "财报披露计划.md",
    # 市场参考数据
    58: "融资融券交易汇总.md",
    59: "融资融券交易明细.md",
    61: "前十大股东.md",
    62: "前十大流通股东.md",
    106: "龙虎榜每日明细.md",
    107: "龙虎榜机构明细.md",
    110: "股权质押统计数据.md",
    111: "股权质押明细.md",
    124: "股票回购.md",
    160: "限售股解禁.md",
    161: "大宗交易.md",
    166: "股东人数.md",
    175: "股东增减持.md",
    318: "个股异常波动.md",
    317: "个股严重异常波动.md",
    316: "交易所重点提示证券.md",
    # 特色数据
    188: "沪深港股通持股明细.md",
    298: "涨跌停列表（新）.md",
    # 两融及转融通
    307: "转融券交易汇总.md",
    308: "转融资交易汇总.md",
    309: "转融券交易明细.md",
    310: "做市借券交易汇总.md",
    314: "融资融券标的（盘前更新）.md",
    # 交易专题
    312: "游资名录.md",
    313: "游资每日明细.md",
    # 指数
    94: "指数基本信息.md",
    95: "指数日线行情.md",
    171: "指数周线行情.md",
    172: "指数月线行情.md",
    96: "指数成分和权重.md",
    128: "大盘指数每日指标.md",
    181: "申万行业分类.md",
    182: "申万行业成分构成.md",
    215: "市场交易统计.md",
    211: "国际指数.md",
    # 公募基金
    19: "公募基金列表.md",
    118: "公募基金管理人.md",
    208: "基金经理.md",
    207: "基金规模数据.md",
    119: "公募基金净值.md",
    120: "公募基金分红.md",
    121: "公募基金持仓数据.md",
    127: "场内基金日线行情.md",
    199: "基金复权因子.md",
    # 期货
    135: "期货合约信息表.md",
    137: "期货交易日历.md",
    138: "期货日线行情.md",
    139: "每日成交持仓排名.md",
    140: "仓单日报.md",
    141: "结算参数.md",
    189: "期货主力与连续合约.md",
    # 期权
    158: "期权合约信息.md",
    159: "期权日线行情.md",
    # 债券
    185: "可转债基本信息.md",
    186: "可转债发行.md",
    187: "可转债行情.md",
    303: "可转债增强.md",
    # 外汇
    178: "外汇基础信息（海外）.md",
    179: "外汇日线行情.md",
    # 港股
    191: "港股列表.md",
    192: "港股行情.md",
    # 宏观经济 - 利率
    149: "Shibor利率数据.md",
    150: "Shibor报价数据.md",
    151: "LPR贷款基础利率.md",
    152: "Libor拆借利率.md",
    153: "Hibor利率.md",
    173: "温州民间借贷利率.md",
    # 宏观经济 - 国民经济
    227: "GDP数据.md",
    # 宏观经济 - 价格指数
    228: "居民消费价格指数.md",
    245: "工业生产者出厂价格指数.md",
    # 宏观经济 - 金融
    242: "货币供应量.md",
}


def create_session():
    """Create a requests session with retry logic."""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def fetch_html(session, doc_id):
    """Fetch the HTML page for a given doc_id."""
    url = f"{BASE_URL}?doc_id={doc_id}"
    resp = session.get(url, timeout=30)
    resp.encoding = "utf-8"
    resp.raise_for_status()
    return resp.text


def extract_content_div(html):
    """
    Extract the main content div from the HTML.
    The structure is:
    <nav class="sidebar ...">...</nav>
    <div class="content col-md-9 col-sm-8 col-xs-12">...</div>
    """
    soup = BeautifulSoup(html, "lxml")
    content_div = soup.select_one("div.content")
    if content_div is None:
        return ""
    return str(content_div)


def convert_html_to_markdown(html_content):
    """Convert HTML content to markdown using html2text."""
    converter = html2text.HTML2Text()
    converter.body_width = 0          # do not wrap lines
    converter.ignore_links = False
    converter.ignore_images = True
    converter.ignore_emphasis = False
    converter.ignore_tables = False
    converter.protect_links = True
    converter.mark_code = True
    converter.unicode_snob = True      # use Unicode instead of ASCII
    converter.skip_internal_links = True
    converter.reference_links = False  # inline links instead of reference style
    return converter.handle(html_content)


def strip_yaml_frontmatter(content):
    """
    Strip YAML frontmatter from a markdown file.
    Frontmatter is delimited by --- lines at the start.
    """
    if not content.startswith("---"):
        return content
    lines = content.split("\n")
    # Find the closing ---
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is not None:
        return "\n".join(lines[end_idx + 1:])
    return content


def normalize_formatting(text):
    """
    Normalize markdown formatting differences between html2text output
    and hand-crafted reference markdown files.

    Normalizations:
    1. Remove [code] / [/code] tags (html2text artifact)
    2. Remove angle brackets around URLs in markdown links: ](<url>) → ](url)
    3. Strip trailing whitespace from each line
    4. Strip ALL blank lines (only content lines matter for comparison)
    5. Normalize each line's leading whitespace for code blocks
    """
    import re

    lines = text.split("\n")

    # 1. Remove [code] and [/code] tags entirely
    lines = [l for l in lines if l.strip() not in ("[code]", "[/code]")]

    # 2. Remove angle brackets around URLs in markdown links
    result_lines = []
    for line in lines:
        # ](<url>) → ](url)
        line = re.sub(r'\]\(<(https?://[^>]+)>\)', r'](\1)', line)
        # ](<url> → ](url (for links at end of string)
        line = re.sub(r'\]\(<(https?://[^>]+)>', r'](\1', line)
        # Also handle any remaining standalone angle-bracketed URLs
        line = re.sub(r'<(https?://[^>]+)>', r'\1', line)
        result_lines.append(line)

    # 3. Strip trailing whitespace
    lines = [l.rstrip() for l in result_lines]

    # 4. Strip ALL blank lines (keep only non-empty lines)
    lines = [l for l in lines if l.strip()]

    return "\n".join(lines)


def compare_lines(server_lines, ref_lines):
    """
    Compare two lists of normalized content lines.
    Returns (is_same, diff_lines).
    """
    if server_lines == ref_lines:
        return True, []
    
    # Generate unified diff
    diff = list(difflib.unified_diff(
        ref_lines, server_lines,
        fromfile="reference", tofile="server",
        lineterm="", n=3
    ))
    return False, diff


def main():
    session = create_session()
    
    results = []
    
    print("=" * 70, file=sys.stderr)
    print(f"Starting comparison of {len(DOC_MAP)} API docs...", file=sys.stderr)
    print(f"Reference directory: {REF_DIR}", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    
    for doc_id in sorted(DOC_MAP.keys()):
        filename = DOC_MAP[doc_id]
        filepath = os.path.join(REF_DIR, filename)
        
        sys.stderr.write(f"\rProcessing doc_id={doc_id} ({filename})...")
        sys.stderr.flush()
        
        # Step 1: Fetch HTML from server
        try:
            html = fetch_html(session, doc_id)
        except Exception as e:
            results.append((doc_id, filename, "FETCH_ERROR", str(e)))
            continue
        
        # Step 2: Extract content div
        content_html = extract_content_div(html)
        if not content_html:
            results.append((doc_id, filename, "PARSE_ERROR", "Content div not found"))
            continue
        
        # Step 3: Convert HTML to markdown and normalize
        server_md = convert_html_to_markdown(content_html)
        server_md = normalize_formatting(server_md)
        
        # Step 4: Check if reference file exists
        if not os.path.exists(filepath):
            results.append((doc_id, filename, "MISSING", ""))
            continue
        
        # Step 5: Read reference file
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                ref_content = f.read()
        except Exception as e:
            results.append((doc_id, filename, "READ_ERROR", str(e)))
            continue
        
        # Step 6: Strip YAML frontmatter and normalize
        ref_md = strip_yaml_frontmatter(ref_content)
        ref_md = normalize_formatting(ref_md)
        
        # Step 7: Compare
        server_lines = server_md.split("\n")
        ref_lines = ref_md.split("\n")
        
        is_same, diff = compare_lines(server_lines, ref_lines)
        
        if is_same:
            results.append((doc_id, filename, "SAME", []))
        else:
            results.append((doc_id, filename, "CHANGED", diff))
    
    sys.stderr.write("\n")
    sys.stderr.flush()
    
    # Print detailed results to stdout
    print("=" * 80)
    print("COMPARISON RESULTS — SERVER vs REFERENCE FILES")
    print("=" * 80)
    print()
    
    stats = {"SAME": 0, "CHANGED": 0, "MISSING": 0,
             "FETCH_ERROR": 0, "PARSE_ERROR": 0, "READ_ERROR": 0}
    
    for doc_id, filename, status, diff in results:
        stats[status] = stats.get(status, 0) + 1
        
        if status == "SAME":
            print(f"  SAME: doc_id={doc_id}, file={filename}")
        elif status == "CHANGED":
            print(f"  CHANGED: doc_id={doc_id}, file={filename}")
            if diff:
                # Show first 10 lines of diff
                print(f"    Difference (first {min(15, len(diff))} lines):")
                for d in diff[:15]:
                    print(f"      {d.rstrip()}")
                print()
        elif status == "MISSING":
            print(f"  MISSING: doc_id={doc_id}, file={filename}")
        else:
            print(f"  {status}: doc_id={doc_id}, file={filename} — {''.join(diff)}")
    
    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"  Total pages compared: {len(results)}")
    print(f"  SAME:                {stats.get('SAME', 0)}")
    print(f"  CHANGED:             {stats.get('CHANGED', 0)}")
    print(f"  MISSING:             {stats.get('MISSING', 0)}")
    print(f"  FETCH_ERROR:         {stats.get('FETCH_ERROR', 0)}")
    print(f"  PARSE_ERROR:         {stats.get('PARSE_ERROR', 0)}")
    print(f"  READ_ERROR:          {stats.get('READ_ERROR', 0)}")


if __name__ == "__main__":
    main()
