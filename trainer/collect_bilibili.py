#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B站营销号鉴别训练数据采集器
采集目标关键词下的热门视频信息，无需登录，使用公开API
"""

import requests
import json
import time
import os
import re
from datetime import datetime
from urllib.parse import quote

# ====== 配置区 ======
KEYWORDS = [
    "副业赚钱", "月入过万", "暴富秘籍", "财富自由",   # 财富诱惑型
    "养生秘方", "保健品推荐", "治疗偏方", "排毒方法",  # 养生伪科学型
    "食物相克", "这样吃致癌", "健康必看", "医生不告诉你", # 健康焦虑型
    "国家补贴", "养老金新规", "退休政策",               # 政策谣言型
    "AI换脸", "克隆声音", "深度伪造",                  # AI伪造型
    "搬运 YouTube", "国外视频翻译", "海外精选", "录屏搬运", # 盗视频/搬运型
]

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "references", "collected_data")
BILIBILI_API = "https://api.bilibili.com/x/web-interface/search/type"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com",
}

MAX_PER_KEYWORD = 10  # 每个关键词最多采集数量
SLEEP_BETWEEN = 2     # 请求间隔（秒），避免频率过高
# ====== 配置区结束 ======


def fetch_bilibili_search(keyword: str, page: int = 1) -> list:
    """调用B站搜索API获取视频列表"""
    params = {
        "search_type": "video",
        "keyword": keyword,
        "page": page,
        "order": "click",  # 按播放量排序，获取热门内容
        "duration": 0,
    }
    try:
        resp = requests.get(BILIBILI_API, params=params, headers=HEADERS, timeout=10)
        data = resp.json()
        if data.get("code") == 0:
            return data.get("data", {}).get("result", [])
    except Exception as e:
        print(f"  ⚠️  请求失败: {e}")
    return []


def extract_video_info(item: dict) -> dict:
    """从搜索结果中提取关键信息"""
    # 清除HTML标签
    def clean_html(text):
        return re.sub(r'<[^>]+>', '', str(text)) if text else ""

    bvid = item.get("bvid", "")
    return {
        "platform": "bilibili",
        "bvid": bvid,
        "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
        "title": clean_html(item.get("title", "")),
        "author": item.get("author", ""),
        "mid": item.get("mid", ""),  # UP主ID
        "play": item.get("play", 0),
        "danmaku": item.get("video_review", 0),
        "favorites": item.get("favorites", 0),
        "like": item.get("like", 0),
        "description": clean_html(item.get("description", ""))[:200],
        "pubdate": item.get("pubdate", ""),
        "duration": item.get("duration", ""),
        "tag": item.get("tag", ""),
        "collected_at": datetime.now().isoformat(),
        "verdict": "",        # 鉴别结论（后续自动填写）
        "risk_level": "",     # 🔴高危 / 🟡存疑 / 🟢可信
        "hit_patterns": [],   # 命中的特征
    }


def load_existing_bvids(filepath: str) -> set:
    """加载已采集的BV号，避免重复"""
    if not os.path.exists(filepath):
        return set()
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {item.get("bvid") for item in data if item.get("bvid")}


def collect_bilibili():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_file = os.path.join(OUTPUT_DIR, "bilibili_queue.json")

    existing_bvids = load_existing_bvids(output_file)
    all_results = []

    if os.path.exists(output_file):
        with open(output_file, "r", encoding="utf-8") as f:
            all_results = json.load(f)

    new_count = 0
    print(f"\n🎬 B站数据采集开始 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"   已有数据: {len(all_results)} 条，已知BV号: {len(existing_bvids)} 个\n")

    for kw in KEYWORDS:
        print(f"  🔍 搜索关键词: 「{kw}」")
        results = fetch_bilibili_search(kw)
        added = 0

        for item in results:
            if added >= MAX_PER_KEYWORD:
                break
            info = extract_video_info(item)
            if not info["bvid"] or info["bvid"] in existing_bvids:
                continue

            # 初步风险预筛（标题关键词命中）
            risky_keywords = ["秘方", "暴富", "月入万", "致癌", "偏方", "不告诉你",
                               "内部消息", "绝密", "你必须知道", "限时", "马上删"]
            title = info["title"].lower()
            if any(k in title for k in risky_keywords):
                info["risk_level"] = "🟡待鉴别(预警)"
            else:
                info["risk_level"] = "🟡待鉴别"

            info["search_keyword"] = kw
            all_results.append(info)
            existing_bvids.add(info["bvid"])
            added += 1
            new_count += 1

        print(f"     新增 {added} 条")
        time.sleep(SLEEP_BETWEEN)

    # 保存
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    print(f"\n✅ B站采集完成！本次新增 {new_count} 条，累计 {len(all_results)} 条")
    print(f"   数据已保存至: {output_file}\n")
    return new_count


if __name__ == "__main__":
    collect_bilibili()
