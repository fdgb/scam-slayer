#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
联合辟谣平台 (piyao.org.cn) + 科学辟谣 (piyao.kepuchina.cn) 翻页批量采集器
- piyao.org.cn：列表页（首页 + index_1..N）中文章形如 /2026xxxx/<hash>/c.html
- kepuchina.cn：列表页 rumorlist?type=0&keyword=5188&page=N，详情 rumordetail?id=<id>
- 提取：标题 / 日期 / 来源 / 原文链接
- 去重（按 URL），追加保存到 collected_data/piyao_queue.json
- 纯标准库实现，无第三方依赖；不调 LLM（鉴别由 auto_verdict 规则引擎处理）
- 这些条目都是「已辟谣真相」（🟢），入库到 references/rumor-library.md，
  与诈骗案例库 case-library.md（仅 🔴/🟡）严格分离。
"""

import json
import os
import re
import time
import urllib.request
from datetime import datetime

BASE_DIR = os.path.dirname(__file__)
# 权威数据目录统一指向 skill 的 references/（与 auto_verdict 一致）。
COLLECTED_DIR = os.path.expanduser("~/.workbuddy/skills/scam-slayer/references/collected_data")
OUT_FILE = os.path.join(COLLECTED_DIR, "piyao_queue.json")
RUMOR_LIB = os.path.expanduser("~/.workbuddy/skills/scam-slayer/references/rumor-library.md")
os.makedirs(COLLECTED_DIR, exist_ok=True)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
SLEEP = 1.0
PIYAO_PAGES = 8       # 联合辟谣平台翻几页
KEPU_MAX_PAGES = 50   # 科学辟谣翻页上限（按 10 条/页，最多探 500 条；遇空页即停）

PIYAO = {
    "name": "联合辟谣平台",
    "home": "https://www.piyao.org.cn/",
    "page": "https://www.piyao.org.cn/index_{}.html",
    "base": "https://www.piyao.org.cn",
}
KEPU = {
    "name": "科学辟谣",
    "home": "https://piyao.kepuchina.cn/rumor/rumorlist?type=0&keyword=5188&page=1",
    "page": "https://piyao.kepuchina.cn/rumor/rumorlist?type=0&keyword=5188&page={}",
    "base": "https://piyao.kepuchina.cn",
}

# 联合辟谣平台：文章链接形如 /20260805/<hash>/c.html
A_RE = re.compile(
    r'<a[^>]+href=["\']([^"\']*(?:20\d{6})/[a-f0-9]+/c\.html)["\'][^>]*>(.*?)</a>',
    re.S | re.I,
)
# 科学辟谣：列表项含 rumordetail?id= + 其后的日期/标题
ITEM_RE = re.compile(
    r'href="(https://piyao\.kepuchina\.cn/rumor/rumordetail\?id=[^"]+)"'
    r'.*?rumor-list_item-date">([^<]+)</div>'
    r'.*?rumor-list_item-title[^>]*>([^<]+)</div>',
    re.S,
)


def fetch(url: str, ref: str = "") -> str:
    try:
        headers = {"User-Agent": UA}
        if ref:
            headers["Referer"] = ref
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode("utf-8", "ignore")
    except Exception as e:
        print(f"  ⚠ 抓取失败 {url}: {type(e).__name__} {e}")
        return ""


def extract_piyao(html: str, base: str):
    out = []
    for m in A_RE.finditer(html):
        href = m.group(1)
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if not title:
            continue
        if not href.startswith("http"):
            href = base + (href if href.startswith("/") else "/" + href)
        dm = re.search(r"/(20\d{6})/", href)
        date = dm.group(1) if dm else ""
        out.append((href, title, date))
    return out


def extract_kepu(html: str, base: str):
    out = []
    for m in ITEM_RE.finditer(html):
        href = m.group(1).strip()
        date = m.group(2).strip()
        title = m.group(3).strip()
        if not title:
            continue
        out.append((href, title, date))
    return out


def load_queue() -> list:
    if os.path.exists(OUT_FILE):
        try:
            return json.load(open(OUT_FILE, encoding="utf-8"))
        except Exception:
            return []
    return []


def collect_piyao_site() -> int:
    """联合辟谣平台：首页 + index_1..N"""
    queue = load_queue()
    seen = {it["url"] for it in queue}
    new = 0
    print(f"\n📡 {PIYAO['name']} 开始")
    urls = [PIYAO["home"]] + [PIYAO["page"].format(i) for i in range(1, PIYAO_PAGES + 1)]
    for url in urls:
        html = fetch(url, PIYAO["base"])
        if not html:
            time.sleep(SLEEP)
            continue
        for href, title, date in extract_piyao(html, PIYAO["base"]):
            if href not in seen:
                queue.append(_make_entry(href, title, date, PIYAO["name"]))
                seen.add(href)
                new += 1
        print(f"  {url} → 累计新增 {new}")
        time.sleep(SLEEP)
    json.dump(queue, open(OUT_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"  ✅ {PIYAO['name']} 本次新增 {new}，站点累计 {len(queue)}")
    return new


def collect_kepu_site() -> int:
    """科学辟谣：rumorlist 翻页直到空页"""
    queue = load_queue()
    seen = {it["url"] for it in queue}
    new = 0
    print(f"\n📡 {KEPU['name']} 开始（翻页至空页）")
    page = 1
    while page <= KEPU_MAX_PAGES:
        url = KEPU["page"].format(page)
        html = fetch(url, KEPU["base"])
        if not html:
            break
        entries = extract_kepu(html, KEPU["base"])
        if not entries:
            print(f"  {url} → 空页，停止翻页")
            break
        for href, title, date in entries:
            if href not in seen:
                queue.append(_make_entry(href, title, date, KEPU["name"]))
                seen.add(href)
                new += 1
        print(f"  {url} → 本页 {len(entries)} 条，累计新增 {new}")
        page += 1
        time.sleep(SLEEP)
    json.dump(queue, open(OUT_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"  ✅ {KEPU['name']} 本次新增 {new}，站点累计 {len(queue)}")
    return new


def _make_entry(url: str, title: str, date: str, source: str) -> dict:
    return {
        "url": url,
        "title": title,
        "date": date,
        "source": source,
        "collected_at": datetime.now().isoformat(),
        "verdict": "",
        "risk_level": "",
    }


def archive_to_rumor_library(entries: list) -> int:
    """将辟谣条目追加进 references/rumor-library.md（按 URL 去重，不污染诈骗案例口径）"""
    existing = set()
    if os.path.exists(RUMOR_LIB):
        with open(RUMOR_LIB, encoding="utf-8") as f:
            for line in f:
                m = re.search(r"链接：\((https?://[^)]+)\)", line)
                if m:
                    existing.add(m.group(1))
    added = 0
    header_ok = os.path.exists(RUMOR_LIB) and os.path.getsize(RUMOR_LIB) > 0
    with open(RUMOR_LIB, "a", encoding="utf-8") as f:
        if not header_ok:
            f.write("# 📗 已辟谣库（官方辟谣平台采集 · 与诈骗案例库 case-library.md 分离）\n\n")
        for e in entries:
            if e["url"] in existing:
                continue
            existing.add(e["url"])
            f.write(f"\n### {e.get('date', '')} {e['title']}\n")
            f.write(f"- 来源：{e['source']}\n")
            f.write(f"- 链接：({e['url']})\n")
            f.write(f"- 采集：{e['collected_at']}\n")
            added += 1
    return added


def main():
    print(f"\n{'='*60}\n  🛡 辟谣平台批量采集  {datetime.now():%Y-%m-%d %H:%M}\n{'='*60}")
    total = 0
    total += collect_piyao_site()
    total += collect_kepu_site()
    queue = load_queue()
    archived = archive_to_rumor_library(queue)
    print(f"\n🎉 本轮合计新增 {total} 条辟谣条目 → {OUT_FILE}")
    print(f"   📗 已写入 rumor-library.md：本次新增 {archived} 条（累计去重 {len(queue)}）\n")


if __name__ == "__main__":
    main()
