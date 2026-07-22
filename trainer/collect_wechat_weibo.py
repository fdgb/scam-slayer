#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信公众号 & 微博文章采集器
由于微信公众号无官方开放API，采用以下替代策略：
  1. 搜狗微信搜索（公开可访问）
  2. 微博话题/关键词搜索（公开API）
"""

import requests
import json
import time
import os
import re
from datetime import datetime
from bs4 import BeautifulSoup

# ====== 配置区 ======
KEYWORDS = [
    "养生保健 转发", "老年人保健品", "退休养老金新规",
    "治病偏方", "这种食物不能吃", "防癌食谱",
    "投资理财稳赚", "副业月入过万", "国家补贴领取",
    "AI换脸骗局", "声音克隆诈骗", "深度伪造视频识别",
    "老人防骗", "微信群谣言",
]

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "references", "collected_data")
SOGOU_WEIXIN_API = "https://weixin.sogou.com/weixin"
WEIBO_SEARCH_API = "https://m.weibo.cn/api/container/getIndex"

HEADERS_SOGOU = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xhtml+xml",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

HEADERS_WEIBO = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
    "Referer": "https://m.weibo.cn",
}

SLEEP_BETWEEN = 3
MAX_PER_KEYWORD = 8
# ====== 配置区结束 ======


def clean_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', '', str(text))
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# ===== 搜狗微信搜索 =====
def fetch_sogou_weixin(keyword: str) -> list:
    """通过搜狗微信搜索获取公众号文章（公开内容）"""
    params = {
        "type": "2",       # 2=文章搜索
        "s_from": "input",
        "query": keyword,
        "ie": "utf8",
        "_sug_": "n",
        "_sug_type_": "",
    }
    results = []
    try:
        resp = requests.get(SOGOU_WEIXIN_API, params=params,
                            headers=HEADERS_SOGOU, timeout=12)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        # 解析文章列表
        items = soup.select(".news-box .news-list li")
        for item in items[:MAX_PER_KEYWORD]:
            try:
                title_el = item.select_one("h3 a") or item.select_one(".tit")
                account_el = item.select_one(".account") or item.select_one(".s-p")
                time_el = item.select_one(".s2 span") or item.select_one("date")
                summary_el = item.select_one("p.txt-info") or item.select_one(".s-p")

                title = clean_html(title_el.text if title_el else "")
                url = title_el.get("href", "") if title_el else ""
                account = clean_html(account_el.text if account_el else "")
                pub_time = clean_html(time_el.text if time_el else "")
                summary = clean_html(summary_el.text if summary_el else "")[:200]

                if not title:
                    continue

                results.append({
                    "platform": "wechat",
                    "title": title,
                    "url": url if url.startswith("http") else f"https://weixin.sogou.com{url}",
                    "account": account,
                    "pub_time": pub_time,
                    "summary": summary,
                    "search_keyword": keyword,
                    "collected_at": datetime.now().isoformat(),
                    "risk_level": "🟡待鉴别",
                    "verdict": "",
                    "hit_patterns": [],
                })
            except Exception:
                continue
    except Exception as e:
        print(f"  ⚠️  搜狗微信请求失败: {e}")
    return results


# ===== 微博搜索 =====
def fetch_weibo_search(keyword: str) -> list:
    """通过微博移动版API搜索话题"""
    params = {
        "containerid": f"100103type=1&q={keyword}",
        "page_type": "searchall",
    }
    results = []
    try:
        resp = requests.get(WEIBO_SEARCH_API, params=params,
                            headers=HEADERS_WEIBO, timeout=12)
        data = resp.json()
        cards = data.get("data", {}).get("cards", [])

        for card in cards[:MAX_PER_KEYWORD]:
            mblog = card.get("mblog", {})
            if not mblog:
                # 有些是card_group
                for sub in card.get("card_group", []):
                    mblog = sub.get("mblog", {})
                    if mblog:
                        break

            if not mblog:
                continue

            text = clean_html(mblog.get("text", ""))[:300]
            uid = mblog.get("user", {}).get("id", "")
            screen_name = mblog.get("user", {}).get("screen_name", "")
            mid = mblog.get("mid", "")

            if not text:
                continue

            # 预判风险
            risk_keywords = ["转发", "有奇效", "内部消息", "一定要看", "秘密", "速看",
                              "马上删", "限时", "不转不是中国人", "震惊"]
            risk_level = "🟡待鉴别(预警)" if any(k in text for k in risk_keywords) else "🟡待鉴别"

            results.append({
                "platform": "weibo",
                "mid": mid,
                "url": f"https://weibo.com/{uid}/{mid}" if uid and mid else "",
                "text_preview": text,
                "author": screen_name,
                "uid": str(uid),
                "reposts": mblog.get("reposts_count", 0),
                "comments": mblog.get("comments_count", 0),
                "likes": mblog.get("attitudes_count", 0),
                "created_at": mblog.get("created_at", ""),
                "search_keyword": keyword,
                "collected_at": datetime.now().isoformat(),
                "risk_level": risk_level,
                "verdict": "",
                "hit_patterns": [],
            })
    except Exception as e:
        print(f"  ⚠️  微博请求失败: {e}")
    return results


def load_existing_ids(filepath: str, id_field: str) -> set:
    if not os.path.exists(filepath):
        return set()
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {str(item.get(id_field, "")) for item in data if item.get(id_field)}
    except Exception:
        return set()


def collect_wechat_weibo():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    wechat_file = os.path.join(OUTPUT_DIR, "wechat_queue.json")
    weibo_file = os.path.join(OUTPUT_DIR, "weibo_queue.json")

    existing_wechat = load_existing_ids(wechat_file, "url")
    existing_weibo = load_existing_ids(weibo_file, "mid")

    wechat_results = []
    weibo_results = []

    if os.path.exists(wechat_file):
        with open(wechat_file, "r", encoding="utf-8") as f:
            wechat_results = json.load(f)
    if os.path.exists(weibo_file):
        with open(weibo_file, "r", encoding="utf-8") as f:
            weibo_results = json.load(f)

    wechat_new = 0
    weibo_new = 0

    print(f"\n📱 微信/微博数据采集开始 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"   微信已有: {len(wechat_results)} 条 | 微博已有: {len(weibo_results)} 条\n")

    for kw in KEYWORDS:
        print(f"  🔍 关键词: 「{kw}」")

        # 采集微信公众号
        wx_items = fetch_sogou_weixin(kw)
        for item in wx_items:
            if item["url"] not in existing_wechat:
                wechat_results.append(item)
                existing_wechat.add(item["url"])
                wechat_new += 1
        print(f"     微信新增 {len([i for i in wx_items if i['url'] not in existing_wechat - {i['url']}])} 条（实际 {len(wx_items)} 条原始）")

        time.sleep(SLEEP_BETWEEN)

        # 采集微博
        wb_items = fetch_weibo_search(kw)
        for item in wb_items:
            if item["mid"] not in existing_weibo:
                weibo_results.append(item)
                existing_weibo.add(item["mid"])
                weibo_new += 1
        print(f"     微博新增 {len(wb_items)} 条")

        time.sleep(SLEEP_BETWEEN)

    # 保存
    with open(wechat_file, "w", encoding="utf-8") as f:
        json.dump(wechat_results, f, ensure_ascii=False, indent=2)
    with open(weibo_file, "w", encoding="utf-8") as f:
        json.dump(weibo_results, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 微信/微博采集完成！微信新增 {wechat_new} 条，微博新增 {weibo_new} 条")
    print(f"   微信累计: {len(wechat_results)} | 微博累计: {len(weibo_results)}\n")
    return wechat_new, weibo_new


if __name__ == "__main__":
    collect_wechat_weibo()
