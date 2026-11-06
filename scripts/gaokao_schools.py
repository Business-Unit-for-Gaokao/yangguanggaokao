import json
import os
import re
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


BASE = "https://gaokao.chsi.com.cn"
SEARCH_HOME = BASE + "/sch/search.do"
SEARCH_URL_TEMPLATE = BASE + "/sch/search--ss-on,option-qg,searchType-1,start-{start}.dhtml"
WAP_SCHOOL_LIST = BASE + "/wap/sch/schlist"
WAP_SCHOOL_DETAIL_TEMPLATE = BASE + "/wap/sch/schinfomain/{sch_id}"

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "data/chsi-schools"))
SCHOOLS_DIR = OUTPUT_DIR / "schools"

SOURCE_MODE = os.getenv("SOURCE_MODE", "wap").lower()
START_MIN = int(os.getenv("START_MIN", "0"))
START_MAX = int(os.getenv("START_MAX", "2900"))
PAGE_STEP = int(os.getenv("PAGE_STEP", "20"))
OVERWRITE = os.getenv("OVERWRITE", "0") == "1"
SAVE_RAW_HTML = os.getenv("SAVE_RAW_HTML", "0") == "1"
SAVE_DEBUG = os.getenv("SAVE_DEBUG", "0") == "1"
REUSE_INDEX = os.getenv("REUSE_INDEX", "0") == "1"
PAGE_TIMEOUT = int(os.getenv("PAGE_TIMEOUT", "60000"))
WAIT_MS = int(os.getenv("WAIT_MS", "1200"))
MAX_WAP_SCROLLS = int(os.getenv("MAX_WAP_SCROLLS", "500"))

PROVINCES = {
    "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江", "上海", "江苏",
    "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "广西",
    "海南", "重庆", "四川", "贵州", "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆"
}

NAV_BLACKLIST = {
    "首页", "高考资讯", "阳光志愿", "高招咨询", "在线咨询", "招生动态", "试题评析",
    "院校库", "专业库", "招生章程", "名单公示", "院校满意度", "专业满意度", "专业推荐",
    "专业解读", "招办访谈", "帮助中心", "登录", "注册", "学籍查询", "学历查询",
    "学位查询", "在线验证", "出国教育背景服务", "图像校对", "学信档案", "高考", "研招",
    "港澳台招生", "征兵", "就业", "学职平台", "学信网", "中心简介", "联系我们", "版权声明",
    "网站地图", "网站宣传", "查看", "取消", "搜索", "返回", "上一页", "下一页",
    "更多信息", "更多", "展开", "收起"
}

KNOWN_PATTERNS = {
    "schoolInfoMain": re.compile(r"/sch/schoolInfoMain--schId-(\d+)\.dhtml"),
    "schoolInfo": re.compile(r"/sch/schoolInfo--schId-(\d+),categoryId-(\d+),mindex-(\d+)\.dhtml"),
    "listzyjs": re.compile(r"/sch/listzyjs--schId-(\d+),categoryId-(\d+),mindex-(\d+)\.dhtml"),
    "listdksw": re.compile(r"/sch/listdksw--schId-(\d+),categoryId-(\d+),mindex-(\d+)\.dhtml"),
    "listBulletin": re.compile(r"/sch/listBulletin--schId-(\d+),categoryId-(\d+),mindex-(\d+)\.dhtml"),
    "listlqjggs": re.compile(r"/sch/listlqjggs--schId-(\d+),categoryId-(\d+),mindex-(\d+)\.dhtml"),
    "zszc": re.compile(r"/zsgs/zhangcheng/listZszc--schId-(\d+)\.dhtml"),
}

GENERIC_SCH_PATTERN = re.compile(r"/sch/([^/?]+)--schId-(\d+)([^?]*)\.dhtml")
GENERIC_ZSGS_PATTERN = re.compile(r"/zsgs/([^?]+)--schId-(\d+)([^?]*)\.dhtml")

COMMON_PAGE_CANDIDATES = [
    {"name": "学校首页", "path": "/sch/schoolInfoMain--schId-{schId}.dhtml"},
    {"name": "学校简介", "path": "/sch/schoolInfo--schId-{schId},categoryId-26172,mindex-1.dhtml"},
    {"name": "院系设置", "path": "/sch/schoolInfo--schId-{schId},categoryId-26177,mindex-2.dhtml"},
    {"name": "专业介绍", "path": "/sch/listzyjs--schId-{schId},categoryId-417809,mindex-3.dhtml"},
    {"name": "录取规则", "path": "/sch/schoolInfo--schId-{schId},categoryId-26187,mindex-4.dhtml"},
    {"name": "体检要求", "path": "/sch/schoolInfo--schId-{schId},categoryId-26196,mindex-5.dhtml"},
    {"name": "收费项目", "path": "/sch/schoolInfo--schId-{schId},categoryId-26201,mindex-7.dhtml"},
    {"name": "奖学金设置", "path": "/sch/schoolInfo--schId-{schId},categoryId-26204,mindex-8.dhtml"},
    {"name": "食宿条件", "path": "/sch/schoolInfo--schId-{schId},categoryId-26208,mindex-8.dhtml"},
    {"name": "基础设施", "path": "/sch/schoolInfo--schId-{schId},categoryId-26213,mindex-9.dhtml"},
    {"name": "毕业生就业", "path": "/sch/schoolInfo--schId-{schId},categoryId-26216,mindex-10.dhtml"},
    {"name": "联系办法", "path": "/sch/schoolInfo--schId-{schId},categoryId-26221,mindex-11.dhtml"},
    {"name": "公示栏", "path": "/sch/listBulletin--schId-{schId},categoryId-26219,mindex-12.dhtml"},
    {"name": "答考生问", "path": "/sch/listdksw--schId-{schId},categoryId-420549,mindex-16.dhtml"},
    {"name": "录取结果公示", "path": "/sch/listlqjggs--schId-{schId},categoryId-423317,mindex-8.dhtml"},
    {"name": "其他", "path": "/sch/schoolInfo--schId-{schId},categoryId-26224,mindex-17.dhtml"},
    {"name": "招生章程", "path": "/zsgs/zhangcheng/listZszc--schId-{schId}.dhtml"},
]


def ensure_output():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SCHOOLS_DIR.mkdir(parents=True, exist_ok=True)


def iso_now():
    return datetime.now(timezone.utc).astimezone().isoformat()


def clean_text(text):
    if text is None:
        return ""
    return " ".join(str(text).replace("\xa0", " ").split()).strip()


def normalize_lines(text):
    return [clean_text(x) for x in (text or "").splitlines() if clean_text(x)]


def unique_keep_order(items):
    seen = set()
    out = []
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else str(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    path.write_text(content, encoding="utf-8")


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_debug(page, name: str):
    if not SAVE_DEBUG:
        return
    try:
        page.screenshot(path=str(OUTPUT_DIR / f"{name}.png"), full_page=True)
    except Exception:
        pass
    try:
        (OUTPUT_DIR / f"{name}.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass


def normalize_url(url: str):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def is_internal_source_url(url: str):
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return (
        host == "chsi.com.cn"
        or host.endswith(".chsi.com.cn")
        or host == "chei.com.cn"
        or host.endswith(".chei.com.cn")
    )


def normalize_declared_url(value: str, page_url: str = BASE):
    raw = clean_text(value).strip().rstrip(".,;:!?，。；：！？）】》")
    if not raw or raw in {"-", "—", "#", "javascript:;"}:
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    elif raw.lower().startswith("www."):
        raw = "https://" + raw
    try:
        normalized = normalize_url(urljoin(page_url, raw))
    except ValueError:
        return ""
    parts = urlsplit(normalized)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    if parts.hostname in {"-", "—"}:
        return ""
    if is_internal_source_url(normalized):
        return ""
    return normalized


def extract_labeled_url_from_lines(lines, label: str, page_url: str = BASE):
    for index, line in enumerate(lines):
        if label not in line:
            continue
        candidates = [line.split(label, 1)[1]]
        candidates.extend(lines[index + 1:index + 3])
        for candidate in candidates:
            match = re.search(r"https?://[^\s<>\"']+", candidate)
            if not match:
                match = re.search(
                    r"(?:(?:https?:)?//|www\.)?[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}(?::\d+)?(?:[/\?#][^\s<>\"']*)?",
                    candidate,
                )
            if not match:
                continue
            value = match.group(0)
            if value and not value.startswith(("http://", "https://", "//", "www.")):
                value = "https://" + value
            normalized = normalize_declared_url(value, page_url)
            if normalized:
                return normalized
    return ""


def soup_of(html: str):
    return BeautifulSoup(html, "html.parser")


def title_from_soup(soup: BeautifulSoup):
    if soup.title and clean_text(soup.title.get_text()):
        return clean_text(soup.title.get_text())
    for tag in ["h1", "h2", "h3"]:
        node = soup.find(tag)
        if node and clean_text(node.get_text()):
            return clean_text(node.get_text())
    return ""


def detect_page_type(url: str):
    for page_type, pattern in KNOWN_PATTERNS.items():
        m = pattern.search(url)
        if m:
            groups = list(m.groups())
            sch_id = groups[0] if groups else ""
            category_id = groups[1] if len(groups) >= 2 else ""
            mindex = groups[2] if len(groups) >= 3 else ""
            return {
                "page_type": page_type,
                "page_family": "known",
                "schId": sch_id,
                "categoryId": category_id,
                "mindex": mindex,
                "raw_name": page_type,
            }

    m = GENERIC_SCH_PATTERN.search(url)
    if m:
        raw_name = m.group(1)
        sch_id = m.group(2)
        tail = m.group(3) or ""
        category_id = ""
        mindex = ""
        c = re.search(r"categoryId-(\d+)", tail)
        mi = re.search(r"mindex-(\d+)", tail)
        if c:
            category_id = c.group(1)
        if mi:
            mindex = mi.group(1)
        return {
            "page_type": "generic_sch",
            "page_family": "generic",
            "schId": sch_id,
            "categoryId": category_id,
            "mindex": mindex,
            "raw_name": raw_name,
        }

    m = GENERIC_ZSGS_PATTERN.search(url)
    if m:
        raw_name = m.group(1)
        sch_id = m.group(2)
        tail = m.group(3) or ""
        category_id = ""
        mindex = ""
        c = re.search(r"categoryId-(\d+)", tail)
        mi = re.search(r"mindex-(\d+)", tail)
        if c:
            category_id = c.group(1)
        if mi:
            mindex = mi.group(1)
        return {
            "page_type": "generic_zsgs",
            "page_family": "generic",
            "schId": sch_id,
            "categoryId": category_id,
            "mindex": mindex,
            "raw_name": raw_name,
        }

    return {
        "page_type": "unknown",
        "page_family": "unknown",
        "schId": "",
        "categoryId": "",
        "mindex": "",
        "raw_name": "",
    }


def is_target_school_url(url: str, sch_id: str):
    meta = detect_page_type(url)
    return meta["page_type"] != "unknown" and meta["schId"] == str(sch_id)


def is_list_like_page(page_type: str):
    return page_type in {"listzyjs", "listdksw", "listBulletin", "listlqjggs", "zszc"}


def extract_same_school_links(soup: BeautifulSoup, page_url: str, sch_id: str):
    links = []
    for a in soup.find_all("a", href=True):
        href = clean_text(a.get("href"))
        if not href or href.startswith("javascript:"):
            continue
        full = normalize_url(urljoin(page_url, href))
        if is_target_school_url(full, sch_id):
            links.append({
                "名称": clean_text(a.get_text()),
                "链接": full,
                "页面标识": detect_page_type(full),
            })
    return unique_keep_order(links)


def extract_other_links(soup: BeautifulSoup, page_url: str, sch_id: str):
    links = []
    for a in soup.find_all("a", href=True):
        href = clean_text(a.get("href"))
        text = clean_text(a.get_text())
        if not href or href.startswith("javascript:"):
            continue
        full = normalize_url(urljoin(page_url, href))
        if is_target_school_url(full, sch_id):
            continue
        if text in NAV_BLACKLIST:
            continue
        links.append({
            "名称": text,
            "链接": full,
        })
    return unique_keep_order(links)


def extract_images(soup: BeautifulSoup, page_url: str):
    imgs = []
    for img in soup.find_all("img", src=True):
        src = clean_text(img.get("src"))
        if not src:
            continue
        imgs.append({
            "alt": clean_text(img.get("alt")),
            "src": normalize_url(urljoin(page_url, src)),
        })
    return unique_keep_order(imgs)


def extract_tables(soup: BeautifulSoup):
    tables = []
    for idx, table in enumerate(soup.find_all("table"), start=1):
        rows = []
        for tr in table.find_all("tr"):
            cells = [clean_text(td.get_text(" ", strip=True)) for td in tr.find_all(["th", "td"])]
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append({
                "序号": idx,
                "行数": len(rows),
                "内容": rows,
            })
    return tables


def extract_key_values_from_lines(lines):
    kv = {}
    for line in lines:
        m = re.match(r"^([^：:]{1,40})[：:]\s*(.+)$", line)
        if not m:
            continue
        key = clean_text(m.group(1))
        value = clean_text(m.group(2))
        if not key or not value:
            continue
        kv.setdefault(key, [])
        if value not in kv[key]:
            kv[key].append(value)
    return kv


def extract_list_items(soup: BeautifulSoup, page_url: str, sch_id: str):
    items = []
    seen = set()

    for a in soup.find_all("a", href=True):
        title = clean_text(a.get_text())
        href = normalize_url(urljoin(page_url, a.get("href")))
        if not title or title in NAV_BLACKLIST:
            continue
        if len(title) > 150:
            continue

        meta = detect_page_type(href)
        same_school = is_target_school_url(href, sch_id)
        key = (title, href)
        if key in seen:
            continue
        seen.add(key)

        parent_text = ""
        parent_date = ""
        li = a.find_parent(["li", "tr", "dd", "div", "p"])
        if li:
            parent_text = clean_text(li.get_text(" ", strip=True))
            dm = re.search(r"(\d{4}-\d{2}-\d{2})", parent_text)
            if dm:
                parent_date = dm.group(1)

        items.append({
            "标题": title,
            "链接": href,
            "是否同校页面": same_school,
            "页面标识": meta,
            "日期": parent_date,
            "上下文文本": parent_text,
        })

    return unique_keep_order(items)


def guess_location(lines):
    for line in lines:
        if line in PROVINCES:
            return line
    return ""


def guess_field(text, label):
    m = re.search(rf"{re.escape(label)}[:：]\s*([^\n]+)", text)
    return clean_text(m.group(1)) if m else ""


def guess_level(text):
    for item in ["本科", "高职（专科）", "高职(专科)", "专科"]:
        if item in text:
            return item
    return ""


def guess_features(lines):
    wanted = ["“双一流”建设高校", "211工程", "985工程", "民办高校", "独立学院", "中外合作办学", "研究生院"]
    out = []
    joined = "\n".join(lines)
    for x in wanted:
        if x in joined:
            out.append(x)
    return out


def fetch_html(context, url: str, is_list_page: bool = False):
    page = context.new_page()
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        if response is not None and response.status >= 400:
            raise RuntimeError(f"HTTP {response.status}: {url}")

        if is_list_page:
            page.wait_for_function(
                """
                () => {
                    const body = document.body ? document.body.innerText : '';
                    const anchors = Array.from(document.querySelectorAll('a[href]'));
                    const hasSchoolLink = anchors.some(a =>
                        /\\/sch\\/schoolInfoMain--schId-\\d+\\.dhtml/.test(a.href || '')
                    );
                    return hasSchoolLink || body.includes('没有查询到相关院校') || body.includes('院校信息库') || body.includes('院校库');
                }
                """,
                timeout=PAGE_TIMEOUT,
            )
        else:
            page.wait_for_function(
                """
                () => {
                    const body = document.body ? document.body.innerText : '';
                    return body.replace(/\\s+/g, ' ').trim().length > 80;
                }
                """,
                timeout=PAGE_TIMEOUT,
            )

        page.wait_for_timeout(WAIT_MS)
        final_url = normalize_url(page.url)
        html = page.content()
        return final_url, html
    finally:
        page.close()


def bootstrap(context):
    page = context.new_page()
    try:
        page.goto(SEARCH_HOME, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1500)
    finally:
        page.close()


def extract_school_list_from_dom(page, start: int):
    raw_rows = page.locator("a[href]").evaluate_all(
        """
        nodes => {
            const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
            const out = [];

            for (const a of nodes) {
                const href = a.href || '';
                if (!/\\/sch\\/schoolInfoMain--schId-\\d+\\.dhtml/.test(href)) continue;

                const schoolName = clean(a.innerText);
                if (!schoolName) continue;

                let best = a;
                let p = a.parentElement;

                while (p && p !== document.body) {
                    const txt = clean(p.innerText);
                    if (!txt) {
                        p = p.parentElement;
                        continue;
                    }

                    if (txt.includes(schoolName)) {
                        best = p;
                    }

                    if (/主管部门|教育行政主管部门|所在地|院校隶属|学校地址|通讯地址|学历层次|满意度|院校特性/.test(txt)) {
                        best = p;
                        break;
                    }

                    if (txt.length > 1200) {
                        break;
                    }

                    p = p.parentElement;
                }

                out.push({
                    href,
                    school_name: schoolName,
                    raw_text: clean((best && best.innerText) || a.innerText || '')
                });
            }

            return out;
        }
        """
    )

    schools = []
    seen = set()
    page_url = normalize_url(page.url)

    for item in raw_rows:
        href = normalize_url(item.get("href", ""))
        meta = detect_page_type(href)
        if meta["page_type"] != "schoolInfoMain":
            continue

        sch_id = meta["schId"]
        school_name = clean_text(item.get("school_name", ""))
        raw_text = clean_text(item.get("raw_text", ""))

        if not sch_id or not school_name or sch_id in seen:
            continue
        seen.add(sch_id)

        lines = normalize_lines(raw_text)
        text = "\n".join(lines)

        schools.append({
            "schId": sch_id,
            "学校名称": school_name,
            "主页链接": href,
            "搜索页": page_url,
            "start": start,
            "页码": start // PAGE_STEP + 1,
            "所在地": guess_location(lines),
            "教育行政主管部门": guess_field(text, "教育行政主管部门") or guess_field(text, "主管部门"),
            "详细地址": guess_field(text, "详细地址") or guess_field(text, "学校地址") or guess_field(text, "通讯地址"),
            "官方网址": guess_field(text, "官方网址") or guess_field(text, "学校网址"),
            "招生网址": guess_field(text, "招生网址"),
            "官方电话": guess_field(text, "官方电话") or guess_field(text, "联系电话"),
            "办学层次": guess_level(text),
            "院校特性": guess_features(lines),
            "列表页原始文本": text,
        })

    return schools


def extract_wap_school_list_from_dom(page, start: int):
    raw_rows = page.locator(".gk-yxklist-item").evaluate_all(
        """
        nodes => {
            const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
            return nodes.map((node, index) => {
                const image = node.querySelector('img[src*="/xh/"]');
                const imageSrc = image ? image.src : '';
                const codeMatch = imageSrc.match(/\\/(\\d+)\\.jpg(?:\\?.*)?$/);
                const schoolCode = codeMatch ? codeMatch[1] : '';
                const schoolId = schoolCode && Number(schoolCode) >= 10000
                    ? String(Number(schoolCode) - 10000)
                    : '';
                const tags = Array.from(node.querySelectorAll('.yxk-tags'))
                    .map(item => clean(item.innerText))
                    .filter(Boolean);
                return {
                    schId: schoolId,
                    学校代码: schoolCode,
                    学校名称: clean(node.querySelector('.yxk-name')?.innerText),
                    所在地: clean(node.querySelector('.yxk-szd')?.innerText),
                    教育行政主管部门: clean(node.querySelector('.department')?.innerText),
                    办学层次: tags.find(item => item.includes('本科') || item.includes('专科')) || '',
                    院校特性: tags,
                    满意度: clean(node.querySelector('.rate')?.innerText),
                    列表页序号: index + 1
                };
            });
        }
        """
    )

    page_url = normalize_url(page.url)
    rows = []
    for item in raw_rows:
        sch_id = clean_text(item.get("schId"))
        school_name = clean_text(item.get("学校名称"))
        if not sch_id or not school_name:
            continue
        rows.append({
            "schId": sch_id,
            "学校代码": clean_text(item.get("学校代码")),
            "学校名称": school_name,
            "主页链接": normalize_url(WAP_SCHOOL_DETAIL_TEMPLATE.format(sch_id=sch_id)),
            "搜索页": page_url,
            "start": start,
            "页码": (start // PAGE_STEP) + 1,
            "所在地": clean_text(item.get("所在地")),
            "教育行政主管部门": clean_text(item.get("教育行政主管部门")),
            "办学层次": clean_text(item.get("办学层次")),
            "院校特性": item.get("院校特性") or [],
            "满意度": clean_text(item.get("满意度")),
        })
    return rows


def extract_labeled_link(soup: BeautifulSoup, label: str, page_url: str = BASE):
    for node in soup.find_all(string=lambda value: value and label in value):
        parent = node.parent
        if parent is None:
            continue
        candidates = []
        if parent.name == "a" and parent.get("href"):
            candidates.append(parent)
        candidates.extend(parent.find_all("a", href=True))
        for sibling in list(parent.next_siblings)[:3]:
            if hasattr(sibling, "find_all"):
                candidates.extend(sibling.find_all("a", href=True))
        for link in candidates:
            normalized = normalize_declared_url(link.get("href"), page_url)
            if normalized:
                return normalized
    return ""


def parse_wap_school_payload(url: str, html: str, school_stub: dict):
    soup = soup_of(html)
    body = soup.body or soup
    lines = normalize_lines(body.get_text("\n", strip=True))
    text = "\n".join(lines)
    official_url = extract_labeled_url_from_lines(lines, "官方网址", url)
    if not official_url:
        official_url = extract_labeled_link(soup, "官方网址", url)
    admission_url = extract_labeled_url_from_lines(lines, "招生网址", url)
    if not admission_url:
        admission_url = extract_labeled_link(soup, "招生网址", url)
    phone = guess_field(text, "官方电话")

    website_fields = {
        "官方网址": official_url,
        "招生网址": admission_url,
    }
    website_links = []
    if official_url:
        website_links.append({"site_type": "official", "url": official_url})
    if admission_url:
        website_links.append({"site_type": "admissions", "url": admission_url})

    return {
        "链接": normalize_url(url),
        "发现来源": school_stub.get("搜索页", WAP_SCHOOL_LIST),
        "页面标识": {
            "page_type": "wap_school_detail",
            "page_family": "wap",
            "schId": str(school_stub.get("schId", "")),
        },
        "标题": school_stub.get("学校名称", ""),
        "原始文本": text,
        "文本行": lines,
        "结构化字段": extract_key_values_from_lines(lines),
        "表格": extract_tables(body),
        "图片": extract_images(body, url),
        "同校页面链接": [],
        "其他链接": extract_other_links(body, url, str(school_stub.get("schId", ""))),
        "列表项": [],
        "子详情链接": [],
        "网站字段": website_fields,
        "网站链接": website_links,
        "官方电话": phone,
        "抓取时间": iso_now(),
    }


def crawl_wap_school(context, school_stub: dict):
    sch_id = str(school_stub["schId"])
    url = WAP_SCHOOL_DETAIL_TEMPLATE.format(sch_id=sch_id)
    errors = []
    pages = []
    try:
        final_url, html = fetch_html(context, url, is_list_page=False)
        pages.append(parse_wap_school_payload(final_url, html, school_stub))
    except Exception as exc:
        errors.append({
            "链接": url,
            "发现来源": "wap_school_detail",
            "错误": repr(exc),
            "时间": iso_now(),
        })

    website_fields = pages[0].get("网站字段", {}) if pages else {}
    return {
        "schId": sch_id,
        "学校名称": school_stub.get("学校名称", ""),
        "列表信息": school_stub,
        "网站字段": website_fields,
        "网站链接": pages[0].get("网站链接", []) if pages else [],
        "详情页总数": len(pages),
        "页面类型统计": {"wap_school_detail": len(pages)} if pages else {},
        "页面列表": pages,
        "错误": errors,
        "抓取时间": iso_now(),
    }


def build_seed_urls(sch_id: str):
    return [{"名称": item["name"], "链接": normalize_url(BASE + item["path"].format(schId=sch_id))} for item in COMMON_PAGE_CANDIDATES]


def parse_page_payload(url: str, html: str, sch_id: str, discovered_from: str = ""):
    soup = soup_of(html)
    body = soup.body or soup
    meta = detect_page_type(url)
    title = title_from_soup(soup)
    lines = normalize_lines(body.get_text("\n", strip=True))
    same_school_links = extract_same_school_links(body, url, sch_id)
    list_items = extract_list_items(body, url, sch_id) if is_list_like_page(meta["page_type"]) else []

    child_detail_links = []
    for item in list_items:
        if item["是否同校页面"]:
            child_detail_links.append({
                "名称": item["标题"],
                "链接": item["链接"],
                "页面标识": item["页面标识"],
            })
    child_detail_links = unique_keep_order(child_detail_links)

    payload = {
        "链接": normalize_url(url),
        "发现来源": discovered_from,
        "页面标识": meta,
        "标题": title,
        "原始文本": "\n".join(lines),
        "文本行": lines,
        "结构化字段": extract_key_values_from_lines(lines),
        "表格": extract_tables(body),
        "图片": extract_images(body, url),
        "同校页面链接": same_school_links,
        "其他链接": extract_other_links(body, url, sch_id),
        "列表项": list_items,
        "子详情链接": child_detail_links,
        "抓取时间": iso_now(),
    }

    if SAVE_RAW_HTML:
        payload["原始HTML"] = str(soup)

    return payload


def sort_pages(pages):
    order = {
        "schoolInfoMain": 0,
        "schoolInfo": 1,
        "listzyjs": 2,
        "listdksw": 3,
        "listBulletin": 4,
        "listlqjggs": 5,
        "zszc": 6,
        "generic_sch": 7,
        "generic_zsgs": 8,
        "unknown": 99,
    }

    def key(x):
        meta = x.get("页面标识", {})
        page_type = meta.get("page_type", "unknown")
        mindex = meta.get("mindex") or "9999"
        category = meta.get("categoryId") or "999999999"
        raw_name = meta.get("raw_name", "")
        mindex_num = int(mindex) if str(mindex).isdigit() else 9999
        category_num = int(category) if str(category).isdigit() else 999999999
        return (order.get(page_type, 99), mindex_num, category_num, raw_name, x.get("链接", ""))

    return sorted(pages, key=key)


def crawl_school(context, school_stub: dict):
    if SOURCE_MODE == "wap":
        return crawl_wap_school(context, school_stub)

    sch_id = school_stub["schId"]
    queue = deque()
    visited = set()
    pages = []
    errors = []

    queue.append((school_stub["主页链接"], "school_stub"))
    for seed in build_seed_urls(sch_id):
        queue.append((seed["链接"], "seed:" + seed["名称"]))

    while queue:
        current, source = queue.popleft()
        current = normalize_url(current)
        if current in visited:
            continue
        visited.add(current)

        try:
            final_url, html = fetch_html(context, current, is_list_page=False)
            payload = parse_page_payload(final_url, html, sch_id, discovered_from=source)
            pages.append(payload)

            for link in payload.get("同校页面链接", []):
                u = normalize_url(link["链接"])
                if u not in visited:
                    queue.append((u, final_url))

            for link in payload.get("子详情链接", []):
                u = normalize_url(link["链接"])
                if u not in visited:
                    queue.append((u, final_url))

        except Exception as e:
            errors.append({
                "链接": current,
                "发现来源": source,
                "错误": repr(e),
                "时间": iso_now(),
            })

    pages = sort_pages(pages)

    page_count_by_type = {}
    for p in pages:
        pt = p.get("页面标识", {}).get("page_type", "unknown")
        page_count_by_type[pt] = page_count_by_type.get(pt, 0) + 1

    return {
        "schId": sch_id,
        "学校名称": school_stub.get("学校名称", ""),
        "列表信息": school_stub,
        "详情页总数": len(pages),
        "页面类型统计": page_count_by_type,
        "页面列表": pages,
        "错误": errors,
        "抓取时间": iso_now(),
    }


def write_partial(index_rows, school_rows):
    save_json(
        OUTPUT_DIR / "school-index.partial.json",
        {
            "抓取时间": iso_now(),
            "数量": len(index_rows),
            "学校列表": index_rows,
        },
    )
    save_json(
        OUTPUT_DIR / "schools-flat.partial.json",
        {
            "抓取时间": iso_now(),
            "数量": len(school_rows),
            "学校列表": school_rows,
        },
    )


def build_hierarchy_by_province(schools):
    province_map = {}
    for school in schools:
        province = school.get("列表信息", {}).get("所在地") or "未知"
        province_map.setdefault(province, [])
        province_map[province].append(school)

    result = []
    for province, items in province_map.items():
        result.append({
            "所在地": province,
            "学校数量": len(items),
            "学校列表": items,
        })
    result.sort(key=lambda x: x["所在地"])
    return result


def collect_school_index(context):
    all_rows = []
    seen = set()
    page = context.new_page()

    try:
        for start in range(START_MIN, START_MAX + 1, PAGE_STEP):
            url = SEARCH_URL_TEMPLATE.format(start=start)
            print(f"[INFO] 列表页 start={start}")

            page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.wait_for_function(
                """
                () => {
                    const body = document.body ? document.body.innerText : '';
                    const anchors = Array.from(document.querySelectorAll('a[href]'));
                    const hasSchoolLink = anchors.some(a =>
                        /\\/sch\\/schoolInfoMain--schId-\\d+\\.dhtml/.test(a.href || '')
                    );
                    return hasSchoolLink || body.includes('没有查询到相关院校') || body.includes('院校信息库') || body.includes('院校库');
                }
                """,
                timeout=PAGE_TIMEOUT,
            )
            page.wait_for_timeout(WAIT_MS)

            anchor_count = page.locator("a[href*='schoolInfoMain--schId-']").count()
            print(f"[INFO] start={start} schoolInfoMain链接数: {anchor_count}")

            if SAVE_DEBUG and (start == START_MIN or anchor_count == 0):
                save_debug(page, f"school-list-{start}")

            rows = extract_school_list_from_dom(page, start)
            print(f"[INFO] start={start} 学校数: {len(rows)}")

            for row in rows:
                if row["schId"] in seen:
                    continue
                seen.add(row["schId"])
                all_rows.append(row)

            write_partial(all_rows, [])

    finally:
        page.close()

    return all_rows


def collect_wap_school_index(context):
    all_rows = []
    seen = set()
    partial_path = OUTPUT_DIR / "school-index.partial.json"
    partial = load_json(partial_path, {}) or {}
    for row in partial.get("学校列表", []):
        sch_id = clean_text(row.get("schId"))
        if not sch_id or sch_id in seen:
            continue
        if not clean_text(row.get("主页链接")).startswith(BASE + "/wap/sch/schinfomain/"):
            continue
        seen.add(sch_id)
        all_rows.append(row)

    page = context.new_page()
    stable_rounds = 0
    last_card_count = 0
    last_scroll_height = 0

    try:
        response = page.goto(WAP_SCHOOL_LIST, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        if response is not None and response.status >= 400:
            raise RuntimeError(f"移动版院校列表返回 HTTP {response.status}: {WAP_SCHOOL_LIST}")

        page.wait_for_selector(".gk-yxklist-item", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(WAIT_MS)

        for scroll_no in range(MAX_WAP_SCROLLS):
            rows = extract_wap_school_list_from_dom(page, scroll_no)
            new_rows = []
            for row in rows:
                sch_id = row["schId"]
                if sch_id in seen:
                    continue
                seen.add(sch_id)
                all_rows.append(row)
                new_rows.append(row)

            card_count = page.locator(".gk-yxklist-item").count()
            scroll_height = page.evaluate("() => document.documentElement.scrollHeight")
            print(
                f"[INFO] WAP滚动轮次={scroll_no + 1} 当前卡片={card_count} "
                f"新增院校={len(new_rows)} 累计院校={len(all_rows)}"
            )

            if new_rows or scroll_no == 0:
                write_partial(all_rows, [])

            if SAVE_DEBUG and (scroll_no == 0 or not all_rows):
                save_debug(page, f"wap-school-list-{scroll_no}")

            if card_count == last_card_count and scroll_height <= last_scroll_height:
                stable_rounds += 1
            else:
                stable_rounds = 0

            if stable_rounds >= 6:
                print(f"[INFO] WAP院校列表连续 {stable_rounds} 轮无变化，结束滚动")
                break

            last_card_count = max(last_card_count, card_count)
            last_scroll_height = max(last_scroll_height, scroll_height)
            page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
            page.wait_for_timeout(WAIT_MS)

        if not all_rows:
            save_debug(page, "wap-school-list-empty")
            raise RuntimeError(
                "移动版院校列表未解析到院校卡片；可能是页面结构变化、未完成加载或请求被拦截"
            )
    finally:
        page.close()

    return all_rows


def build_college_website_records(school_rows, run_id, discovered_at):
    records = []
    for school in school_rows:
        sch_id = clean_text(school.get("schId"))
        institution_name = clean_text(school.get("学校名称"))
        detail_url = normalize_url(
            school.get("列表信息", {}).get(
                "主页链接", WAP_SCHOOL_DETAIL_TEMPLATE.format(sch_id=sch_id)
            )
        )
        for link_index, link in enumerate(school.get("网站链接", [])):
            url = normalize_declared_url(link.get("url"), detail_url)
            if not sch_id or not institution_name or not url:
                continue
            site_type = clean_text(link.get("site_type")) or "unknown"
            records.append(
                {
                    "institution_id": sch_id,
                    "institution_name": institution_name,
                    "site_type": site_type,
                    "url": url,
                    "source_url": detail_url,
                    "discovered_at": discovered_at,
                    "verification_status": "unverified",
                    "verification_level": "source_declared",
                    "last_checked_at": None,
                    "source_id": "chsi.sunshine_gaokao",
                    "endpoint_id": "chsi.wap.schinfomain",
                    "run_id": run_id,
                    "raw_record_ref": f"schools/{sch_id}.json#/网站链接/{link_index}",
                }
            )

    return sorted(
        records,
        key=lambda item: (
            item["institution_id"],
            item["site_type"],
            item["url"],
        ),
    )


def write_college_website_outputs(school_rows, run_id, discovered_at):
    records = build_college_website_records(school_rows, run_id, discovered_at)
    save_json(
        OUTPUT_DIR / "college-websites.json",
        {
            "schema_version": "1.0",
            "generated_at": iso_now(),
            "source_id": "chsi.sunshine_gaokao",
            "source_url": WAP_SCHOOL_LIST if SOURCE_MODE == "wap" else SEARCH_HOME,
            "verification": {
                "status": "unverified",
                "level": "source_declared",
                "note": "网址来自阳光高考页面展示，尚未独立验证目标站点可达性或官方归属。",
            },
            "record_count": len(records),
            "records": records,
        },
    )
    save_jsonl(OUTPUT_DIR / "college-websites.jsonl", records)

    institution_ids = {item["institution_id"] for item in records}
    official_ids = {item["institution_id"] for item in records if item["site_type"] == "official"}
    admissions_ids = {
        item["institution_id"] for item in records if item["site_type"] == "admissions"
    }
    save_json(
        OUTPUT_DIR / "college-websites-summary.json",
        {
            "generated_at": iso_now(),
            "run_id": run_id,
            "source_id": "chsi.sunshine_gaokao",
            "institution_count": len(school_rows),
            "institution_with_any_website_count": len(institution_ids),
            "institution_with_official_website_count": len(official_ids),
            "institution_with_admissions_website_count": len(admissions_ids),
            "website_record_count": len(records),
            "institution_without_website_record_count": max(
                0, len(school_rows) - len(institution_ids)
            ),
        },
    )

    return records


def sanitize_school_record(data):
    pages = data.get("页面列表", []) if data else []
    for page in pages:
        page_url = normalize_url(page.get("链接", BASE))
        fields = page.get("网站字段", {}) or {}
        lines = page.get("文本行", []) or []
        official_url = extract_labeled_url_from_lines(lines, "官方网址", page_url)
        if not official_url:
            official_url = normalize_declared_url(fields.get("官方网址"), page_url)
        admission_url = extract_labeled_url_from_lines(lines, "招生网址", page_url)
        if not admission_url:
            admission_url = normalize_declared_url(fields.get("招生网址"), page_url)
        page["网站字段"] = {
            "官方网址": official_url,
            "招生网址": admission_url,
        }
        page["网站链接"] = [
            {"site_type": "official", "url": official_url}
            for official_url in [official_url]
            if official_url
        ] + [
            {"site_type": "admissions", "url": admission_url}
            for admission_url in [admission_url]
            if admission_url
        ]

    if pages:
        data["网站字段"] = pages[0].get("网站字段", {})
        data["网站链接"] = pages[0].get("网站链接", [])
    else:
        fields = data.get("网站字段", {}) or {}
        page_url = normalize_url(data.get("链接", BASE))
        official_url = normalize_declared_url(fields.get("官方网址"), page_url)
        admission_url = normalize_declared_url(fields.get("招生网址"), page_url)
        data["网站字段"] = {
            "官方网址": official_url,
            "招生网址": admission_url,
        }
        data["网站链接"] = [
            {"site_type": "official", "url": official_url}
            for official_url in [official_url]
            if official_url
        ] + [
            {"site_type": "admissions", "url": admission_url}
            for admission_url in [admission_url]
            if admission_url
        ]
    return data


def can_reuse_school_record(data):
    if not data or data.get("错误"):
        return False
    if SOURCE_MODE != "wap":
        return True
    return any(
        page.get("页面标识", {}).get("page_type") == "wap_school_detail"
        for page in data.get("页面列表", [])
    )


def run():
    ensure_output()

    if SOURCE_MODE not in {"wap", "desktop"}:
        raise ValueError("SOURCE_MODE must be wap or desktop")

    flat_index_rows = []
    flat_school_rows = []
    run_id = uuid.uuid4().hex
    run_started_at = iso_now()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
        )

        try:
            if SOURCE_MODE == "wap":
                if REUSE_INDEX:
                    cached_index = load_json(OUTPUT_DIR / "school-index.json", {})
                    flat_index_rows = cached_index.get("学校列表", [])
                    if not flat_index_rows:
                        raise RuntimeError("REUSE_INDEX=1 但 school-index.json 没有可用院校列表")
                    print(f"[INFO] 复用已有院校索引：{len(flat_index_rows)} 所")
                else:
                    flat_index_rows = collect_wap_school_index(context)
            else:
                bootstrap(context)
                flat_index_rows = collect_school_index(context)
            write_partial(flat_index_rows, [])

            total = len(flat_index_rows)

            for i, school_stub in enumerate(flat_index_rows, start=1):
                sch_id = school_stub["schId"]
                path = SCHOOLS_DIR / f"{sch_id}.json"

                if path.exists() and not OVERWRITE:
                    data = load_json(path, {})
                    if can_reuse_school_record(data):
                        data = sanitize_school_record(data)
                        save_json(path, data)
                        flat_school_rows.append(data)
                        print(f"[INFO] 跳过已有 {i}/{total} schId={sch_id}")
                        continue

                print(f"[INFO] 抓取学校 {i}/{total} schId={sch_id} {school_stub.get('学校名称', '')}")
                data = sanitize_school_record(crawl_school(context, school_stub))
                save_json(path, data)
                flat_school_rows.append(data)

                if i % 10 == 0 or i == total:
                    write_partial(flat_index_rows, flat_school_rows)

            save_json(
                OUTPUT_DIR / "school-index.json",
                {
                    "抓取时间": iso_now(),
                    "数量": len(flat_index_rows),
                    "学校列表": flat_index_rows,
                },
            )

            save_json(
                OUTPUT_DIR / "schools-flat.json",
                {
                    "抓取时间": iso_now(),
                    "数量": len(flat_school_rows),
                    "学校列表": flat_school_rows,
                },
            )

            save_json(
                OUTPUT_DIR / "all.json",
                {
                    "抓取时间": iso_now(),
                    "数量": len(flat_school_rows),
                    "按所在地聚合": build_hierarchy_by_province(flat_school_rows),
                },
            )

            website_records = write_college_website_outputs(
                flat_school_rows,
                run_id=run_id,
                discovered_at=run_started_at,
            )

            save_json(
                OUTPUT_DIR / "meta.json",
                {
                    "抓取时间": iso_now(),
                    "run_id": run_id,
                    "source_mode": SOURCE_MODE,
                    "来源": {
                        "移动版院校列表": WAP_SCHOOL_LIST,
                        "移动版院校详情模板": WAP_SCHOOL_DETAIL_TEMPLATE,
                        "搜索首页": SEARCH_HOME,
                        "搜索页模板": SEARCH_URL_TEMPLATE,
                        "已知页面模式": list(KNOWN_PATTERNS.keys()),
                        "通用页面模式": ["generic_sch", "generic_zsgs"],
                    },
                    "范围": {
                        "start_min": START_MIN,
                        "start_max": START_MAX,
                        "page_step": PAGE_STEP,
                    },
                    "学校总数": len(flat_school_rows),
                    "网址记录数": len(website_records),
                    "网址验证状态": "未独立验证；仅记录阳光高考页面声明/展示的网址",
                    "是否覆盖已存在文件": OVERWRITE,
                    "是否保存原始HTML": SAVE_RAW_HTML,
                    "是否保存调试文件": SAVE_DEBUG,
                },
            )

            print(f"school_index: {len(flat_index_rows)}")
            print(f"school_saved: {len(flat_school_rows)}")
            print(f"college_websites: {len(website_records)}")
            print(f"output_dir: {OUTPUT_DIR.resolve()}")

        except PlaywrightTimeoutError as e:
            write_partial(flat_index_rows, flat_school_rows)
            raise e
        except Exception as e:
            write_partial(flat_index_rows, flat_school_rows)
            raise e
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    run()
